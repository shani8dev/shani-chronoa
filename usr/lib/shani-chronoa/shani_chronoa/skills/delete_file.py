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
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "file-delete-enabled"

#: How recently the *parent directory* must have changed for a missing path to
#: be attributed to this call rather than to a path that was never there.
#: Generous, because a recursive delete of tens of thousands of files takes
#: tens of seconds and the mtime moves as the last one lands, not the first.
_FRESH_SECONDS = 300.0


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
        files.refuse_sensitive(target, "delete")
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
    """The filesystem after the call, read with a fresh stat - not the skill's own report.

    **Why the parent directory is checked, and why a plain "is it gone" is not
    enough.** This skill refuses three ways without deleting anything: the
    consent key is off, the path does not exist, or the directory is not empty
    and `recursive` was not passed. Two of those leave the filesystem exactly
    as a successful delete leaves it - in particular the refusal

        "Could not delete /home/x: it does not exist, so nothing was removed."

    satisfies `not lexists(path)` and nothing else. So the original condition
    reported **VERIFIED for a delete that never happened**, in the most
    destructive skill in this repository. That is not theoretical: a benchmark
    that drove this skill against real paths produced exactly that verdict on a
    refused call, and a false VERIFIED is what stops anybody reading further.

    The signal that separates the two is the **parent directory's mtime**.
    Removing a name from a directory updates that directory; a refusal that
    inspected the filesystem and changed nothing does not. So a delete is only
    certified when the parent moved recently enough to be this call.

    **The window is a window, not a proof.** `_FRESH_SECONDS` is generous - a
    recursive delete of tens of thousands of files takes tens of seconds - and it
    has the honest failure mode: a directory modified by something else within
    the same window can make a never-existed path look deleted. The alternative
    available here is worse, because the skill runs in a subprocess and there is
    no channel by which it could tell this module what it found beforehand.
    """
    try:
        # expand, not resolve: resolving follows a symlink to its target, and a
        # dangling link left behind would then read as removed.
        target = files.expand(arguments.get("path") or "")
    except files.PathProblem:
        return None

    if os.path.lexists(target):
        return False, f"{target} still exists"

    try:
        moved = time.time() - target.parent.stat().st_mtime
    except OSError as exc:
        return None, f"could not read the parent directory ({exc})"

    if moved > _FRESH_SECONDS:
        # The name is gone and the parent has not been touched for longer than
        # any single delete of this shape takes. Almost certainly this path was
        # never there - which is a refusal the caller already has a sentence for,
        # and reporting it as verified would contradict it.
        return None, (
            f"{target} is gone but its directory has not changed for "
            f"{int(moved / 60)} minutes, so this is more likely a path that was "
            f"never there than a delete that just happened"
        )
    return True, f"{target} is gone and its directory changed {moved:.1f}s ago"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="delete_file", schema=SCHEMA, run=_run)]
