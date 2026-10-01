r"""File-based argument transport for skill dispatch (`tools.py`).

`tools.py:execute_tool()` runs a skill in a child `python3 -c` process so the
`SandboxExecutor` can wrap the call. That child used to receive its arguments
by JSON-interpolating them into the *source string* of the program:

    python3 -c 'from shani_chronoa.skills.timer import _run_set_timer; ...(_run_set_timer({"label": "..."}))'

That transport has a hard ceiling that is not a style preference, it is a
failure, and all three of the following were reproduced against a real
subprocess rather than reasoned about:

- **A single oversized value kills the call outright.** Linux caps one `argv`
  entry at `MAX_ARG_STRLEN` (128 KiB). A 200 KiB argument makes `execve` fail
  with `E2BIG`, which `_run_host()` reports as
  `Execution error on the host: [Errno 7] Argument list too long: '/bin/sh'`.
  There is no partial success - the skill never runs.
- **A binary value cannot be expressed at all.** `json.dumps()` has no
  representation for `bytes`; `tools.py`'s `default=str` turns an image into
  the *repr* string `b'\\x89PNG\\r\\n...'`. A vision sense that hands image
  bytes to a model cannot use this path, which is the reason this module
  exists.
- **A `NaN` in the arguments is a crash, not a value.** `json.dumps` emits the
  bare token `NaN`, which is not a Python name, so the child dies with
  `NameError: name 'NaN' is not defined`. (LLM tool arguments are parsed with
  `json.loads`, which accepts a bare `NaN`, so this is reachable from the
  wire rather than being a Python-only curiosity.)

So an argument that cannot be carried inline - binary, or large enough that
the caller explicitly asked for a file - is passed **by reference** instead:
it is written to a private temp file, and the child receives the *path*. The
program it runs is then a fixed constant with no interpolation of anything at
all - module name, function name and every argument travel as data inside a
JSON envelope file named on `argv[1]`. That is what makes the new path
injection-proof rather than merely quoted: there is no untrusted string in the
program source to escape. The inline JSON path is *not* known to be escapable
(`json.dumps` escapes quotes and backslashes and emits `\uXXXX` for
non-ASCII, and `tools.py` then `shlex.quote`s the whole program; a live probe
with `a"; import os; os.system("touch pwned") #` as a label executed nothing)
- but it is not something to keep extending.

**When this transport is used.** A `bytes` argument always takes it, because
JSON has no other way to carry one. A merely *large* argument takes it only
when the caller passes `by_reference=True`, and the reasoning for not deciding
that automatically is in `reference_command()`. Nothing under the caller's
explicit request changes transport, so no existing skill's behaviour moves.

**What a skill receives.** A spilled argument arrives as a `FileRef`, a `str`
subclass whose value *is* the path, so `open(ref)`, `os.fspath(ref)` and
`str(ref)` all work unchanged, plus `ref.kind` (`"bytes"`, `"text"` or
`"json"`), `ref.size` and `ref.read_bytes()` / `ref.read_text()`. A skill
written for small string arguments keeps receiving small string arguments.

**Boundaries, deliberately.**

- **Not a way to widen the skill whitelist.** This adds an argument
  *transport*, not an action. The closed set of skills and
  `tools.py:_get_sandbox_config()`'s levels are untouched, per the standing
  `AGENTS.md` rule that the whitelist never becomes generic shell-exec.
- **No `bytes` value ever reaches a skill unless its caller put one there.**
  LLM tool-call arguments are JSON, so they contain no `bytes`; the binary path
  exists for in-process callers (a future vision sense reading image bytes
  off disk) and is inert otherwise.- **The temp files are private and short-lived.** The per-call directory is
  created by `tempfile.mkdtemp` (mode 0700, never a shared `/tmp` name) and
  every payload file is created `O_EXCL` with mode 0600, so another user on the
  machine cannot read a payload between creation and the child's read. The
  caller owns the directory and must call `ArgumentPayload.cleanup()`; the
  child does not delete it, because the parent is what learns whether the call
  succeeded.
- **The envelope is not a trust boundary the child trusts blindly.** An
  argument that itself looks like a file reference - `{"$chronoa_file": ...}`
  straight off the model - is wrapped in a `"$chronoa_literal"` escape on the
  way in and unwrapped on the way out, so a model cannot make the child open a
  path of its choosing by forging a marker.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from shani_chronoa import files

logger = logging.getLogger(__name__)

# Total serialized size of one call's arguments above which the inline JSON
# transport is known to fail. 32 KiB is roughly a quarter of Linux's 128 KiB
# per-argv cap. Measured live, not assumed: a 100 KiB argument went through
# the inline path fine and a 200 KiB one killed the whole call with
# `E2BIG`. This constant is the *documentation* of that ceiling, not a
# threshold that silently changes which transport a skill gets - see
# `reference_command()`.
MAX_INLINE_ARGUMENTS_BYTES = 32 * 1024

# The cap applied to one value *inside* the envelope. Once the envelope is a
# file there is no `execve` pressure left, so this is purely about the
# contract: a value past this size is always passed by reference, so a skill
# can rely on "a big value is a file" rather than the transport's behaviour
# depending on what else happened to be in the same call. Uniform across
# scalars and containers for exactly that reason.
MAX_INLINE_VALUE_BYTES = 32 * 1024

# Per-user state, alongside the sandbox executor's `sandboxes/` and
# `ToolTracker`'s `logs/`. Deliberately not the system temp dir: the bwrap
# levels `--ro-bind / /` and then `--tmpfs /tmp`, so an envelope under `/tmp`
# would not exist at all for a sandboxed skill, while a path under the user's
# own data dir is visible in every level.
_ARGFILE_ROOT = files.data_home() / "shani-chronoa" / "argfiles"

# The envelope's own key names. Both are reserved: a value carrying either is
# escaped, so a model cannot forge either one.
_FILE_MARKER = "$chronoa_file"
_LITERAL_ESCAPE = "$chronoa_literal"

_ENVELOPE_NAME = "envelope.json"

# The parent package directory, i.e. the entry `shani_chronoa` is importable
# from. The child is a fresh interpreter started by the shell: it inherits
# neither this process's `sys.path` nor its `sys.path.insert` from
# `usr/bin/shani-chronoa`, so without this line every real skill call dies
# with `ModuleNotFoundError: No module named 'shani_chronoa'` unless the app
# happened to be started from this directory.
_MODULE_ROOT = str(Path(__file__).resolve().parent.parent)

# Fixed program text for the by-reference transport. Note what is *not* in it:
# no module name, no function name, no argument, no path. Those arrive as data
# through `sys.argv[1]`, so there is nothing here for untrusted input to
# escape into. The one interpolated value is `_MODULE_ROOT`, this module's own
# location on disk, and it is `repr()`-quoted because this is Python source.
# `shlex.quote()` here would produce a *shell* literal and the child would die
# with `SyntaxError: invalid syntax`, which is exactly what it did before this
# line was corrected (found by running the real child, not by reading it).
# That reasoning predates the move to argv, where the program is its own argv
# element and no shell reads it at all - `repr()` is now simply the correct
# quoting for Python source, and the shell-quoting hazard it once guarded
# against is gone because the shell is gone.
_REFERENCE_PROGRAM = (
    f"import sys; sys.path.insert(0, {_MODULE_ROOT!r}); "
    f"from {__name__} import run_from_file; "
    f"sys.exit(run_from_file(sys.argv[1]))"
)


class FileRef(str):
    """A skill-visible reference to an argument that was passed by reference.

    A `str` subclass on purpose. The value *is* the path, so a skill that
    simply opens it (`open(arguments["image"], "rb")`) is correct and needs to
    know nothing about this class, while a skill that wants to be explicit can
    use `read_bytes()` or check `kind`. Making it a `str` rather than a wrapper
    object is also what keeps the by-reference path a strict superset of the
    inline one for any skill that just opens what it is given.
    """

    __slots__ = ("kind", "size")

    def __new__(cls, path: str, kind: str, size: int) -> "FileRef":
        ref = super().__new__(cls, path)
        ref.kind = kind
        ref.size = size
        return ref

    @property
    def path(self) -> Path:
        return Path(str(self))

    def read_bytes(self) -> bytes:
        """The payload exactly as the caller handed it over."""
        with open(str(self), "rb") as handle:
            return handle.read()

    def read_text(self, encoding: str = "utf-8") -> str:
        """The payload decoded as text.

        Deliberately strict: a `UnicodeDecodeError` is the honest answer for a
        binary payload, matching `senses/filesystem.py`'s refusal to decode
        arbitrary bytes with `errors="replace"`.
        """
        return self.read_bytes().decode(encoding)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"FileRef({str(self)!r}, kind={self.kind!r}, size={self.size})"


class ArgumentPayload(NamedTuple):
    """A prepared by-reference call: the command, and what to delete after it.

    `cleanup()` is idempotent - `rmtree(..., ignore_errors=True)` on an
    already-removed directory is a no-op - so a caller's `finally` can call it
    unconditionally without tracking whether it already ran.
    """

    #: An argv list, not a command string. The sandbox executor's policy guards
    #: read the program out of argv, so a pre-quoted string here would hand them
    #: back the thing they were changed to stop reading.
    argv: "list[str]"
    envelope_path: str
    directory: str

    def cleanup(self) -> None:
        """Remove the envelope and every payload file, whatever happened."""
        shutil.rmtree(self.directory, ignore_errors=True)


def _contains_bytes(value: Any) -> bool:
    """True if `value` is, or contains, a value JSON cannot represent.

    Checked before any serialization, because `json.dumps(..., default=str)`
    would happily turn image bytes into a repr string and hide the problem.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if isinstance(value, dict):
        return any(_contains_bytes(k) or _contains_bytes(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_bytes(item) for item in value)
    return False


def _encoded_size(value: Any) -> int:
    """Serialized size of `value`, or `inf` if it cannot be serialized at all."""
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return float("inf")


def _make_argfile_dir() -> str:
    """A private 0700 directory for one call's envelope and payloads."""
    try:
        _ARGFILE_ROOT.mkdir(parents=True, exist_ok=True)
        return tempfile.mkdtemp(prefix="call-", dir=str(_ARGFILE_ROOT))
    except OSError as e:
        # A read-only or missing HOME must not make tool calls fail outright;
        # the system temp dir is equally private because mkdtemp is 0700.
        logger.warning(f"Falling back to the system temp dir for argument files: {e}")
        return tempfile.mkdtemp(prefix="chronoa-argfile-")


def _write_payload(directory: str, index: int, data: bytes) -> str:
    """Write one payload file, 0600 and exclusive, and return its path.

    `O_EXCL` because the name is predictable (`arg-0.bin`); 0600 because the
    child runs as the same user and no other user has any business reading a
    skill argument off this machine.
    """
    path = os.path.join(directory, f"arg-{index}.bin")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return path


def _spill_value(directory: str, index: int, value: Any) -> "tuple[dict, int]":
    """Return a marker for `value` and the next free payload index.

    The kind records how the file is to be read back, because a `bytes` value
    is raw bytes on disk while a `str` is UTF-8 and a structured value is JSON -
    reading the last two as raw bytes would hand the skill the JSON's own
    quoting instead of the value.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        kind = "bytes"
    elif isinstance(value, str):
        data = value.encode("utf-8")
        kind = "text"
    else:
        data = json.dumps(value, default=str).encode("utf-8")
        kind = "json"
    path = _write_payload(directory, index, data)
    return ({_FILE_MARKER: {"path": path, "kind": kind, "size": len(data)}}, index + 1)


def _prepare_value(directory: str, index: int, value: Any, escaped: bool = False) -> "tuple[Any, int]":
    """Return `value` with anything unspillable, oversized or forged escaped.

    Recurses before measuring, and that order is load-bearing twice over. A
    300 KiB string nested one level down is just as fatal to `execve` as one at
    the top level; and a container holding `bytes` must have those bytes
    spilled individually first, because `_spill_value` on the container would
    otherwise `json.dumps(..., default=str)` them into reprs and the child
    would get text where the caller put binary.

    `escaped` is set once a value has been wrapped, and is what stops the
    escape recursing forever: the wrapper's own key is the reserved one, so
    re-checking it would rebuild the same wrapper on every pass.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _spill_value(directory, index, value)
    if isinstance(value, dict):
        # Escape first: a dict carrying a reserved key must survive as inert
        # data, not be turned into a path the child opens.
        if not escaped and (_FILE_MARKER in value or _LITERAL_ESCAPE in value):
            return _prepare_value(directory, index, {_LITERAL_ESCAPE: value}, escaped=True)
        prepared: dict = {}
        for key, item in value.items():
            prepared[key], index = _prepare_value(directory, index, item, escaped)
        value = prepared
    elif isinstance(value, (list, tuple)):
        prepared_list: list = []
        for item in value:
            converted, index = _prepare_value(directory, index, item, escaped)
            prepared_list.append(converted)
        value = prepared_list
    if _encoded_size(value) > MAX_INLINE_VALUE_BYTES:
        return _spill_value(directory, index, value)
    return value, index


def _materialize(value: Any, escaped: bool = False) -> Any:
    """Rebuild a skill's arguments from an envelope value.

    `escaped` marks "inside something the caller forged", where a marker key is
    just a string like any other and must not be read as a path. Everything
    else is rebuilt recursively, including inside an escape, so a real spilled
    payload nested in a dict that also carried a forged marker still arrives as
    a `FileRef` rather than as an unreadable marker dict.
    """
    if isinstance(value, dict):
        if not escaped:
            if _LITERAL_ESCAPE in value:
                return _materialize(value[_LITERAL_ESCAPE], escaped=True)
            if _FILE_MARKER in value:
                ref = value[_FILE_MARKER]
                return FileRef(ref["path"], ref["kind"], int(ref["size"]))
        return {key: _materialize(item, escaped) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item, escaped) for item in value]
    return value


def reference_command(
    module: str, function: str, arguments: dict, by_reference: bool = False
) -> "ArgumentPayload | None":
    """Prepare a by-reference call, or return `None` to use the inline path.

    Returning `None` - rather than deciding inside `tools.py` - keeps the
    "when does this transport apply" rule in one place next to the caps that
    define it, and keeps `tools.py`'s existing inline command construction
    untouched on the branch that is already proven.

    **A binary argument switches transports on its own; an oversized text
    argument does not.** A `bytes` value has no JSON representation at all, so
    there is nothing to fall back to and the choice is forced. A *large string*
    is a different matter: the inline path can express it, it simply dies at
    `E2BIG` past Linux's 128 KiB per-argv cap, and silently handing the skill a
    path where it expected text is strictly worse than that loud failure -
    `set_timer` would cheerfully report
    `Timer set for 3 seconds: '/tmp/.../arg-0.bin'` and the model would see a
    success. So a caller that means it passes `by_reference=True` and takes
    responsibility for the skill being able to read a `FileRef`. With the
    default, no existing skill's behaviour changes in any circumstance.
    """
    if not isinstance(arguments, dict):
        return None
    if not by_reference and not _contains_bytes(arguments):
        return None

    directory = _make_argfile_dir()
    try:
        prepared: dict = {}
        index = 0
        for key, value in arguments.items():
            prepared[key], index = _prepare_value(directory, index, value)

        envelope_path = os.path.join(directory, _ENVELOPE_NAME)
        envelope = {
            "version": 1,
            "module": module,
            "function": function,
            "arguments": prepared,
        }
        # The envelope names a module and a function, so it is written 0600 for
        # the same reason the payloads are: it is this call's private input.
        fd = os.open(envelope_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle)

        # Each element is its own argv entry, so nothing here needs quoting and
        # no shell exists between this and the exec.
        argv = ["python3", "-c", _REFERENCE_PROGRAM, envelope_path]
        return ArgumentPayload(argv, envelope_path, directory)
    except BaseException:
        # A half-built directory is a directory nobody will ever clean up.
        shutil.rmtree(directory, ignore_errors=True)
        raise


def run_from_file(envelope_path: str) -> int:
    """Child side: rebuild the arguments and run the skill named in the envelope.

    Runs under a fixed program with no interpolated input, so the only thing
    this has to get right is the data. It writes `str(result)` to stdout and
    returns 0, which is exactly what the inline transport's program does, so
    `_SANDBOX.execute()` and `tools.py:execute_tool()` see the same shape of
    outcome from either path. An exception propagates, so the child exits
    non-zero with a traceback on stderr just as it does today.
    """
    with open(envelope_path, "rb") as handle:
        envelope = json.loads(handle.read().decode("utf-8"))

    arguments = _materialize(envelope.get("arguments") or {})
    handler = getattr(importlib.import_module(envelope["module"]), envelope["function"])
    result = handler(arguments)
    sys.stdout.write(str(result))
    return 0
