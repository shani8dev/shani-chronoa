"""Skill: move a file or folder to the desktop trash, recoverably.

`delete_file` exists and is deliberately permanent - its own module docstring
says so, and says why: "a file manager's trash would be recoverable and is the
right thing for most deletions". That sentence has been sitting there describing
a capability the project did not have.

This is that capability, and it is the one that should be reached for by default.
Permanent deletion is right for a build artefact and wrong for a document someone
is about to realise they still needed; the difference is recoverable versus not,
and offering only the irreversible option means every routine "clear this out"
request has to either use the dangerous one or be refused.

Uses `gio trash`, the same freedesktop interface the file manager's own "Move to
Trash" uses, so a non-standard trash location is handled the way the desktop
handles it. There is deliberately no `mv ~/.local/share/Trash/files/...` fallback:
that path is wrong on any system that relocates its trash, and getting it wrong
copies a file somewhere arbitrary rather than deleting anything.

Gated by `file-delete-enabled`, the same key as permanent deletion, because it
does remove the file from where it was. The difference is that it is recoverable,
and the refusal says so rather than implying this is a lesser permission.

Honesty rules:

- **The move is verified both ways**: the path must be gone *and* the item must
  appear in the trash listing. A `gio` exit code alone is not a move.
- A refusal to trash something is a refusal, not a fallback to deleting it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.files import PathProblem
from shani_chronoa.skills import Skill

_CONSENT_KEY = "file-delete-enabled"
_TIMEOUT = 60

SCHEMA = {
    "type": "function",
    "function": {
        "name": "trash_file",
        "description": (
            "Move a file or folder to the desktop trash, where it stays "
            "recoverable, rather than deleting it permanently. This is the right "
            "tool for clearing something out; use delete_file only when the "
            "removal really must be permanent. Requires the "
            "'file-delete-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file or folder to move to the trash.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            "moving things to the trash is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). It shares that key with permanent "
            "deletion rather than having a lighter one of its own, because it "
            "does remove the file from where it was - the difference is that "
            "this one is recoverable, which is why it is the better default."
        )
    return True, ""


def _gio(*args: str):
    if shutil.which("gio") is None:
        return None
    try:
        return subprocess.run(["gio", "trash", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _trash_dirs() -> "tuple[Path | None, Path | None]":
    """(files, info) for the trash this environment actually uses.

    `gio trash` writes to `$XDG_DATA_HOME/Trash` but `gio trash --list` reads the
    home trash regardless - two GIO code paths that do not agree. Verifying a
    move by asking the lister therefore reports "not in the trash" for a file
    that is sitting in the trash, and did so here on the first real run. The
    filesystem is the authority, so the check looks at the directories directly.
    """
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    trash = base / "Trash"
    if not trash.is_dir():
        return (None, None)
    return (trash / "files", trash / "info")


def _in_trash(path: Path) -> bool:
    """Whether a `.trashinfo` record exists for this name.

    The `info` record is the one that matters: a file in `files/` with no record
    cannot be restored by any file manager, so its presence alone would
    overstate recoverability.
    """
    files, info = _trash_dirs()
    if info is None or files is None:
        return False
    return (info / f"{path.name}.trashinfo").is_file() and (files / path.name).exists()


def _run(arguments: dict) -> str:
    raw = arguments.get("path")
    try:
        path = files.resolve(raw)
    except PathProblem as exc:
        return str(exc)

    if not path.exists():
        return f"{path} does not exist, so there is nothing to move to the trash."
    if path.is_symlink():
        return (f"{path} is a symlink. Moving it to the trash would move the "
                f"link, not what it points at, which is rarely what is meant - "
                f"pass the target path if that is what you want gone.")

    try:
        files.refuse_catalogue(path, "move to the trash")
    except PathProblem as exc:
        return str(exc)

    if shutil.which("gio") is None:
        return ("gio is not installed, so the freedesktop trash cannot be used. "
                "There is deliberately no fallback to copying the file into "
                "~/.local/share/Trash by hand: that path is wrong on any system "
                "that relocates its trash, and getting it wrong copies a file "
                "somewhere arbitrary rather than removing it anywhere.")

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to move {path} to the trash: {reason}"

    proc = _gio(str(path))
    if proc is None:
        return f"The trash did not answer within {_TIMEOUT}s, so whether {path} " \
               f"was moved is UNKNOWN. Check before assuming it is gone."
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "gio gave no reason"
        return (f"Could not move {path} to the trash: {detail}. Nothing was "
                f"deleted, and this did not fall back to a permanent delete.")

    if path.exists():
        return (f"gio reported success but {path} is still there, so this is not "
                f"verified. Nothing was permanently deleted.")

    if not _in_trash(path):
        return (f"{path} is gone from where it was, but it did not appear in the "
                f"trash listing, so where it went is UNKNOWN. Check the trash "
                f"yourself before assuming it is recoverable.")

    return (f"Moved {path.name} to the trash. Verified both ways: it is gone from "
            f"the original path and appears in the trash listing, so it is "
            f"recoverable.")



def _post_condition(arguments: dict):
    """The filesystem after the call, read with a fresh stat - not the skill's own report."""
    try:
        # expand, not resolve: resolving follows a symlink to its target, and a
        # dangling link left behind would then read as removed.
        target = files.expand(arguments.get("path") or "")
    except files.PathProblem:
        return None
    gone = not os.path.lexists(target)
    return gone, f"{target} {'is no longer in place' if gone else 'is still there'}"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="trash_file", schema=SCHEMA, run=_run)]
