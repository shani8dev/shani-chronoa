"""Skill: permanently delete a file or directory.

The most destructive thing this project can do, and the only reason it exists
behind a consent key rather than being folded into `write_text_file`.

**This does not use the trash.** `shutil.rmtree` and `unlink` are permanent.
A file manager's trash would be recoverable and is the right thing for a
human-facing delete; a skill an LLM can call is not that, so it says plainly
that the delete is permanent rather than borrowing a safety the user did not
get.

Consent is checked *before* anything is inspected, so a refused delete does not
even reveal whether the path exists - otherwise a model could probe the
filesystem through refusals.

Honesty rules: the count and total size of what was removed is reported from
before the removal, and a partial failure says which parts went.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "file-delete-enabled"


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"file deletion is turned off (enable '{_CONSENT_KEY}' in Settings). "
            f"Reading and searching files needs no such permission, and neither "
            f"does moving or renaming them - only this does."
        )
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_file",
        "description": (
            "Permanently delete a file or a directory and everything inside it. "
            "This cannot be undone and does not use the trash. Requires the "
            "'file-delete-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file or directory to delete."},
                "recursive": {
                    "type": "boolean",
                    "description": (
                        "Required to delete a non-empty directory. Defaults to "
                        "false, so a directory is never emptied by accident."
                    ),
                },
            },
        },
    },
}


def _count_and_size(path):
    if path.is_file() or path.is_symlink():
        try:
            return 1, path.lstat().st_size
        except OSError:
            return 1, 0
    count = 0
    total = 0
    for dirpath, _dirs, filenames in os.walk(path):
        count += len(filenames)
        for name in filenames:
            try:
                total += (Path(dirpath) / name).lstat().st_size
            except OSError:
                continue
    return count, total


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to delete: {reason}"

    try:
        target = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    try:
        files.refuse_catalogue(target, "delete")
    except files.PathProblem as exc:
        return str(exc)
    if not target.exists() and not target.is_symlink():
        return f"Could not delete {target}: it does not exist, so nothing was removed."

    if target.is_dir() and not target.is_symlink():
        entries = list(target.iterdir())
        if entries and not arguments.get("recursive"):
            return (
                f"Refusing to delete {target}: it is a directory with "
                f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} in "
                f"it. Pass recursive to delete it and everything inside - this "
                f"is permanent and does not use the trash."
            )
        count, size = _count_and_size(target)
        what = f"the directory and {count} file(s) totalling {files.human_size(size)}"
    else:
        count, size = _count_and_size(target)
        what = f"the file ({files.human_size(size)})"

    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        return files.describe(exc, target, "delete")

    if target.exists():
        return f"Reported a delete but {target} is still there."
    return f"Deleted {what}: {target}. This is permanent."



def _post_condition(arguments: dict):
    """The filesystem after the call, read with a fresh stat - not the skill's own report."""
    try:
        # expand, not resolve: resolving follows a symlink to its target, and a
        # dangling link left behind would then read as removed.
        target = files.expand(arguments.get("path") or "")
    except files.PathProblem:
        return None
    gone = not os.path.lexists(target)
    return gone, f"{target} {'is gone' if gone else 'still exists'}"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="delete_file", schema=SCHEMA, run=_run)]
