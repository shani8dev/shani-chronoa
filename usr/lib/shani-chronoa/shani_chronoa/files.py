"""Shared filesystem helpers for the file skills.

Nine skills need the same four things: turn a user-supplied path into something
resolvable, refuse a path that is obviously catastrophic to act on, format a
size a human can read, and turn an exception into a sentence that says what
actually went wrong. Implemented once here rather than nine times, because the
earlier `wpctl` triplication in this repo showed exactly what happens when the
same logic is copied per skill: three copies, three timeouts, and a divergence
nobody notices until one of them is wrong.

**The honesty rule these exist to enforce.** Every one of the failure paths
below reports *why* something could not be done. A directory that cannot be
listed is "permission denied", not an empty directory. A glob that matches
nothing is "no files matched", which is a different statement from "this folder
is empty". A truncated listing says it was truncated. Nothing here converts an
error into a clean, empty, successful-looking result, because that is the one
failure mode a person cannot detect and therefore cannot recover from.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional

#: Never act on these, whatever the caller says. A typo that resolves to `/`
#: must not delete a home directory, and a skill reachable by an LLM needs this
#: more than a human does.
PROTECTED_ROOTS = (Path("/"), Path.home())


class PathProblem(Exception):
    """A path that must not be acted on, with the reason to tell the user."""


def resolve(raw: str) -> Path:
    """Expand and absolutise a user-supplied path.

    Raises `PathProblem` with a message meant for the user, not a traceback.
    """
    if raw is None or not str(raw).strip():
        raise PathProblem("No path was given.")
    text = os.path.expandvars(os.path.expanduser(str(raw).strip()))
    try:
        return Path(text).resolve()
    except (OSError, RuntimeError) as exc:
        raise PathProblem(f"Could not resolve {raw!r}: {exc}") from exc


def refuse_catalogue(path: Path, verb: str) -> None:
    """Refuse to `verb` a filesystem root or the user's entire home directory.

    A whole-filesystem or whole-home delete is almost never what was meant, and
    `shutil.rmtree` would do it without complaint.
    """
    for root in PROTECTED_ROOTS:
        try:
            if path == root:
                raise PathProblem(
                    f"Refusing to {verb} {path}: that is a whole filesystem or "
                    f"home directory, not a file. Delete the specific files "
                    f"inside it instead."
                )
        except PermissionError:  # pragma: no cover - root comparison is best-effort
            continue


def human_size(num_bytes: float) -> str:
    """A size a person can read, without pretending to more precision."""
    step = 1024.0
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < step or unit == "TiB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TiB"  # pragma: no cover - loop always returns above


def describe(exc: BaseException, path: Path, action: str) -> str:
    """Turn an OSError into a sentence naming the cause and the path.

    Deliberately does not collapse `NotADirectoryError` and `FileNotFoundError`
    into one message, and never into a success. An LLM reading "Permission
    denied: /root" can ask the user for a different path; an LLM reading "" or
    "done" will report a deletion that never happened.
    """
    name = type(exc).__name__
    if isinstance(exc, FileNotFoundError):
        return f"Could not {action} {path}: it does not exist."
    if isinstance(exc, IsADirectoryError):
        return f"Could not {action} {path}: it is a directory."
    if isinstance(exc, NotADirectoryError):
        return f"Could not {action} {path}: a part of that path is not a directory."
    if isinstance(exc, PermissionError):
        return (
            f"Could not {action} {path}: permission denied. The file exists but "
            f"this user may not touch it."
        )
    if isinstance(exc, FileExistsError):
        return f"Could not {action} {path}: something is already there."
    detail = getattr(exc, "strerror", None) or str(exc)
    return f"Could not {action} {path}: {name}: {detail}"


def entry_line(path: Path, root: Path) -> str:
    """One listing row: name, kind, size, and whether it is a link."""
    try:
        is_link = path.is_symlink()
        stat = path.stat()  # follows links, deliberately
    except OSError:
        # A dangling symlink still deserves a row: "it is there and it points
        # nowhere" is a fact, and dropping it makes a broken link invisible.
        return f"  {path.name}  (link, target could not be read)"
    kind = "dir" if path.is_dir() else "file"
    size = "" if kind == "dir" else human_size(stat.st_size)
    suffix = " -> link" if is_link else ""
    try:
        shown = path.relative_to(root)
    except ValueError:  # pragma: no cover - root is the parent by construction
        shown = Path(path.name)
    name = str(shown)
    return f"  {name}  [{kind}]{(' ' + size) if size else ''}{suffix}"


def walk_limited(
    root: Path,
    *,
    max_entries: int,
    max_depth: int,
    skip_hidden: bool = False,
) -> tuple[list, bool, Optional[str]]:
    """Walk `root` collecting up to `max_entries`, reporting if it stopped early.

    Returns `(entries, truncated, stop_reason)`. A caller that silently returns
    a partial list has turned "there is more" into "this is everything", which
    is the same class of error as an exception swallowed into a clean result.
    """
    entries: list = []
    truncated = False
    reason: Optional[str] = None
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=None):
        here = Path(dirpath)
        if len(here.parts) - base_depth >= max_depth:
            dirnames[:] = []
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for name in list(dirnames) + filenames:
            entries.append(here / name)
            if len(entries) >= max_entries:
                truncated = True
                reason = (
                    f"stopped after {max_entries} entries (limit reached); this "
                    f"is not the whole tree"
                )
                return entries, truncated, reason
    return entries, truncated, reason


def tool_missing(binary: str, purpose: str) -> str:
    """An honest sentence for an absent helper binary.

    `purpose` is the user's question, not the binary's job description, so the
    reply says what could not be answered rather than what went wrong
    internally. A skill that gets `None` back from `shutil.which` and carries on
    anyway is the bug shape this repo has shipped before.
    """
    hint = _PACKAGE_HINTS.get(binary, "the package that provides it")
    return (
        f"Could not {purpose}: {binary} is not installed on this machine, so "
        f"nothing was done. On Arch it comes from {hint}."
    )


_PACKAGE_HINTS = {
    "nmcli": "networkmanager",
    "lp": "cups",
    "lpr": "cups",
    "xdotool": "xdotool",
}


def iter_lines(text: str) -> Iterable[str]:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            yield line
