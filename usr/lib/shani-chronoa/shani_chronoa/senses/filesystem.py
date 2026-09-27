"""Sense: read one text file the user names, confined to their home directory.

Reading a file is the cheapest capability Chronoa can add and one of the
highest value - "what's in this file" is a question the assistant is asked
constantly, and before this it could only answer it by asking a human to
paste the contents.

**Consent.** `run()` refuses unless `ChronoaConfig.sense_allowed("filesystem")`
passes, which means `filesystem-sense-enabled` (default false) must be on.
Reading the user's own files is exactly the request that has to be answered
one sense at a time, so the switch is off until someone deliberately flips it.

It also has no ambient privacy surface at all: `poll_interval` is None, so
this never runs unless the user asked for it, and the only path it ever acts
on is a path they named.

The bounds below are the design, so they are argued here rather than left
as bare code:

- **Home confinement, checked *after* `Path.resolve()`.** Containment tested
  before resolving is defeated by a symlink: `~/link -> /etc/shadow` passes a
  `startswith(home)` test on the *named* path and then reads `/etc/shadow`.
  Resolving first collapses `..`, absolute paths and every symlink hop into
  one real path, leaving exactly one thing to test.
- **Regular files only.** A directory read is a directory listing wearing a
  disguise; a fifo blocks forever and can be unbounded; a device is not text.
  Refusing by `stat` type is cheaper and clearer than handling each case.
- **A 1 MiB cap, and truncation is always announced.** A *silent* truncation
  is worse than no read at all: the model would go on to answer about a file
  it never saw in full with no signal that anything was missing.
- **Binary content is refused, not decoded with `errors="replace"`.** Mojibake
  costs real tokens in a 4k context window and teaches the model nothing.
- **A real timeout.** A regular file is not guaranteed to be a *fast* one -
  an unresponsive network or FUSE mount blocks in the kernel, where a
  non-blocking flag does nothing, and a sense that can hang stalls the turn
  with no way to report anything.

Deliberately *not* here, and why:

- **Not routed through `SandboxExecutor`.** The default `LEVEL_3_HOST_USER`
  runs the command on the host as the user, and the bwrap levels are about
  *commands* - neither confines a path handed to this sense directly.
  `resolve()` plus these bounds *is* the containment; adding a shell would
  widen the attack surface and buy no restriction.
- **No shell.** `AGENTS.md`'s permanent boundary is that the skill whitelist
  never becomes generic shell-exec. Reading a file needs no command.
- **No writes, and no directory listing.** Read-only is the whole capability
  surface here. A listing is a filesystem traversal that needs its own bounds
  designed (depth, entry count, name filtering, total bytes) rather than
  something to smuggle in beside a file read.
"""

from __future__ import annotations

import os
import stat as _stat
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Percept, Sense

# 1 MiB. Every realistic target of "read me this file" - a config, a source
# file, a note, a log tail - is far under it, while a minified bundle, a
# disk image or a database is not something the local 4k-context model this
# feeds could use anyway. Larger files get the truncation notice, which tells
# the caller to narrow the read instead of guessing where the interesting
# part is.
_MAX_BYTES = 1024 * 1024

# Five seconds is generous for a local read and still bounded. The failure it
# exists for is a hung mount, not a slow disk.
_READ_TIMEOUT_SECONDS = 5.0

# A file read is only true while the user is still talking about it: long
# enough to survive several turns of "and what about line 40?", short enough
# that a later turn never re-reads this content and presents it as current.
_TTL_SECONDS = 300.0

_SCHEMA = {
    "type": "function",
    "function": {
        # Named for the consent surface, not for how it reads:
        # `sense_allowed()` builds the key as "<name>-sense-enabled", so a
        # rename here silently leaves the sense permanently ungrantable.
        "name": "filesystem",
        "description": (
            "Read the text of ONE named file from the user's home directory. "
            "Read-only: it cannot list directories, and cannot write, move or "
            "delete anything. Refuses paths that resolve outside the home "
            "directory. A file larger than the cap is truncated to its first "
            f"{_MAX_BYTES // 1024} KiB with a notice saying so."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Absolute or home-relative path of the file to read, "
                        "e.g. 'notes/todo.md' or '~/notes/todo.md'."
                    ),
                },
                "max_bytes": {
                    "type": "integer",
                    "description": (
                        f"Read at most this many bytes (default and hard cap "
                        f"{_MAX_BYTES}). Use it to take a smaller slice of a "
                        f"large file; values above the cap are clamped, not honoured."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}

# Ordered so the first match names the most specific kind of thing found.
_FILE_KINDS = (
    (_stat.S_ISDIR, "directory"),
    (_stat.S_ISFIFO, "FIFO"),
    (_stat.S_ISSOCK, "socket"),
    (_stat.S_ISBLK, "block device"),
    (_stat.S_ISCHR, "character device"),
)


class _ReadTimeout(Exception):
    """The read did not finish inside `_READ_TIMEOUT_SECONDS`."""


# A BaseException in a success union because this crosses a thread boundary:
# the worker hands back either its result or what it raised, so the caller
# sees the same exception type a direct read would have raised.
_ReadResult = Union["tuple[bytes, os.stat_result]", BaseException]


def _describe_type(mode: int) -> str:
    for is_kind, label in _FILE_KINDS:
        if is_kind(mode):
            return label
    return "special file"


def _clamped_limit(requested: object) -> int:
    """The caller's byte budget, clamped into `(0, _MAX_BYTES]`.

    Exposed as a *reduction* only. Honouring a caller-supplied cap larger
    than `_MAX_BYTES` would let the model lift the one bound that keeps file
    content from swamping the context window, and an LLM-chosen cap is not a
    user consent.
    """
    if isinstance(requested, bool) or not isinstance(requested, int):
        return _MAX_BYTES
    return max(1, min(requested, _MAX_BYTES))


def _read_bounded(path: Path, limit: int) -> "tuple[bytes, os.stat_result]":
    """Read up to `limit + 1` bytes of `path`, or raise.

    The extra byte is how truncation is detected without trusting `st_size`:
    a file of exactly `limit` bytes is complete, and `st_size` is 0 for
    everything under `/proc`, so it cannot answer that question.

    `O_NONBLOCK` and `O_NOFOLLOW` close the window between the `stat` that
    vetted this path and the `open` that acts on it - a fifo appearing in
    that window would otherwise block the open forever, and a symlink
    appearing there would be followed despite the resolve-time check.

    The timeout bounds how long the *caller* waits; it cannot kill the worker
    thread, because a thread blocked inside a kernel read is not killable
    from Python. `daemon=True` is what keeps that from leaking into process
    lifetime: `concurrent.futures` registers an `atexit` hook that joins every
    worker it ever started, which was measured here turning a 5-second
    refusal into a 60-second process. A daemon thread is not joined, so the
    hung read dies with the interpreter.
    """
    slot: Queue[_ReadResult] = Queue(maxsize=1)

    def _work() -> None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                slot.put((handle.read(limit + 1), os.fstat(handle.fileno())))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            slot.put(exc)

    threading.Thread(target=_work, daemon=True).start()
    try:
        result = slot.get(timeout=_READ_TIMEOUT_SECONDS)
    except Empty:
        raise _ReadTimeout from None
    if isinstance(result, BaseException):
        raise result
    data, info = result
    return data, info


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("filesystem"):
        return f"Not reading the file: {config.sense_allowed_reason('filesystem')}."

    raw = str(arguments.get("path") or "").strip()
    if not raw:
        return "No file path was given."

    home = Path.home().resolve()
    candidate = Path(raw).expanduser()
    # Anchored at home, not at the process CWD (which a service manager usually
    # sets to `/`): CWD-relative would refuse the path the user plainly meant and
    # would make the path acted on differ from the path they named.
    if not candidate.is_absolute():
        candidate = home / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as e:
        return f"Not reading '{raw}': could not resolve that path ({type(e).__name__})."

    if not resolved.is_relative_to(home):
        return (
            f"Not reading '{raw}': it resolves to {resolved}, which is outside "
            f"your home directory ({home}). Chronoa only reads files inside your home."
        )

    try:
        vetted = os.stat(resolved)
    except FileNotFoundError:
        return f"Not reading '{raw}': no such file."
    except OSError as e:
        return f"Not reading '{raw}': {e.strerror or e}."

    if not _stat.S_ISREG(vetted.st_mode):
        return f"Not reading '{raw}': it is a {_describe_type(vetted.st_mode)}, not a regular file."

    limit = _clamped_limit(arguments.get("max_bytes"))
    try:
        data, opened = _read_bounded(resolved, limit)
    except _ReadTimeout:
        return (
            f"Not reading '{raw}': the read did not finish within "
            f"{_READ_TIMEOUT_SECONDS:.0f}s - the file is probably on an unresponsive mount."
        )
    except FileNotFoundError:
        return f"Not reading '{raw}': no such file."
    except OSError as e:
        return f"Not reading '{raw}': {e.strerror or e}."

    if (opened.st_dev, opened.st_ino) != (vetted.st_dev, vetted.st_ino):
        return (
            f"Not reading '{raw}': the file was replaced between the check and the "
            f"read, so the bounds checked no longer describe what would be read."
        )

    read_len = len(data)
    omitted = 0
    if read_len > limit:
        omitted = max(vetted.st_size - limit, read_len - limit)
        data = data[:limit]

    if b"\x00" in data:
        return f"Not reading '{raw}': it is binary data (null bytes), not text."
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"Not reading '{raw}': it is not valid UTF-8 text, so reading it would only add noise."

    if omitted:
        # Floored at what was actually read: some mounts report st_size 0 for a
        # real file, and "the first 1048576 of 0 bytes" misleads the model.
        text = (
            f"[truncated: showing the first {len(data)} of "
            f"{max(vetted.st_size, read_len)} bytes ({omitted} omitted)]\n\n{text}"
        )

    return _SENSE.to_percept(
        text,
        source=str(resolved),
        metadata={
            "bytes": len(data),
            "size_on_disk": vetted.st_size,
            "truncated": bool(omitted),
        },
    )


_SENSE = Sense(
    name="filesystem",
    kind="text-file",
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY_PRIVATE,
    schema=_SCHEMA,
    run=_run,
    poll_interval=None,
)

SENSES = [_SENSE]
