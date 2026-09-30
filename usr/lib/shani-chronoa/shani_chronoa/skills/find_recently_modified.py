"""Skill: what changed on this machine today.

The question after an interrupted session, a suspicious reboot, or a colleague
asking what you touched. Nothing in the package could answer it:
`find_files` searches by *name*, `search_file_contents` opens files and looks
for text, and neither has ever asked the filesystem a question about *time*.

**`st_mtime` is a lower bound, and this skill says so.** It records the last
write the filesystem observed, and a tool that sets timestamps deliberately -
`tar -x`, `rsync --times`, `cp -p`, a restore from backup, a `touch` - moves
it without the content changing. So a file at the top of this list is
*probably* recently written, not certainly, and a file missing from it is not
proof it was not touched. Saying "modified" where the truth is "the
filesystem's recorded mtime" would overstate it.

**Directories are excluded, and that is stated.** A directory's mtime changes
when anything inside it is created or removed, so including them would rank
`~/Downloads` above the file the user actually wrote. Their count is reported
separately so the exclusion is visible rather than silent.

The walk is bounded through `files.walk_limited` and the withheld count is
carried out with the result, because "newest 50" from a tree with 400 recent
files read as "these 50 are what changed" is the exact error `files.py` names.

**An unreadable directory is counted, not dropped.** `os.walk`'s default
`onerror` swallows the error, which would make a permission-denied subtree
contribute zero results and look like a subtree with no recent changes.
"""

from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_ENTRIES = 4000
_MAX_DEPTH = 8
_DEFAULT_DAYS = 1
_MAX_SHOWN = 25

SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_recently_modified",
        "description": (
            "List files under a directory whose recorded modification time is "
            "within the last N days, newest first. Use this to answer 'what did "
            "I change today'. This is a timestamp, so a tool that preserves or "
            "sets times (tar, rsync, cp -p) can move an entry without the "
            "content changing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Where to look. Defaults to the home directory.",
                },
                "within_days": {
                    "type": "integer",
                    "description": f"How far back to look. Defaults to {_DEFAULT_DAYS}.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Only files whose name matches this wildcard, e.g. '*.py'.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many to list. Defaults to {_MAX_SHOWN}.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    try:
        root = files.resolve_in_home((arguments.get("path") or "").strip() or "~")
    except files.PathProblem as exc:
        return str(exc)
    if not root.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              root, "search")
    if root.is_file():
        root, single = root.parent, root
    else:
        single = None

    try:
        days = int(arguments.get("within_days") or _DEFAULT_DAYS)
    except (TypeError, ValueError):
        return f"within_days must be a whole number of days, not {arguments.get('within_days')!r}."
    days = max(0, min(days, 3650))
    try:
        limit = int(arguments.get("limit") or _MAX_SHOWN)
    except (TypeError, ValueError):
        limit = _MAX_SHOWN
    limit = max(1, min(limit, 200))
    pattern = (arguments.get("pattern") or "").strip()

    cutoff = time.time() - days * 86400
    unreadable: list[str] = []
    entries, truncated, stop_reason = files.walk_limited(
        root, max_entries=_MAX_ENTRIES, max_depth=_MAX_DEPTH, skip_hidden=False,
    )

    hits: list[tuple[float, int, Path]] = []
    directories = 0
    for entry in entries:
        if single is not None and entry != single:
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            directories += 1
            continue
        if pattern and not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
            continue
        try:
            mtime = entry.lstat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            size = entry.lstat().st_size
        except OSError:
            size = 0
        hits.append((mtime, size, entry))

    for dirpath, _dirnames, _files in os.walk(root, onerror=None):
        try:
            os.listdir(dirpath)
        except PermissionError:
            unreadable.append(dirpath)
        except OSError:
            unreadable.append(dirpath)

    hits.sort(key=lambda h: (h[0], str(h[2])), reverse=True)

    if not hits:
        note = f" within the last {days} day(s)" if days else " (cutoff is now)"
        return (
            f"No file under {root} has a recorded modification time newer than"
            f"{note}. That is a statement about timestamps: a file restored with "
            f"its times preserved, or one whose times were set deliberately, "
            f"would not appear here even if it changed."
        )

    shown, withheld = files.cap_list(hits, limit)
    lines = [
        f"{len(hits)} file(s) under {root} modified in the last {days} day(s), "
        f"newest first (showing {len(shown)}):"
    ]
    for mtime, size, entry in shown:
        delta = max(0, time.time() - mtime)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        lines.append(f"  {when} ({int(delta // 60)} min ago)  {files.human_size(size):>9}  {entry}")
    note = files.withheld_note("file", withheld, "Raise limit, or narrow the path or pattern.")
    if note:
        lines.append(f"  {note}")
    if directories:
        lines.append(
            f"  {directories} director{'y' if directories == 1 else 'ies'} were "
            f"not listed: a directory's timestamp changes when anything inside "
            f"it does, which would rank a folder above the file written in it."
        )
    if truncated and stop_reason:
        lines.append(f"  note: the walk {stop_reason}, so older files may exist that are not shown.")
    if unreadable:
        lines.append(
            f"  {len(unreadable)} director{'y' if len(unreadable) == 1 else 'ies'} "
            f"could not be read and contributed no results - they are not empty."
        )
    if pattern:
        lines.append(f"  Filtered to names matching {pattern!r}.")
    return "\n".join(lines)


SKILLS = [Skill(name="find_recently_modified", schema=SCHEMA, run=_run)]
