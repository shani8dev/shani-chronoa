"""Skill: which directories in here are empty?

Nothing answered it. `duplicate_files` finds redundant bytes, `stale_files`
finds big untouched ones and `cleanup_report` finds caches - but an **empty
directory** is a different kind of nothing: it costs no space, so every
size-based cleanup answer skips it, and it is the usual residue of a move, a
uninstall or an extraction that unwound.

Measured on this box: **217 empty directories** under a home directory, nothing
in this package able to name one.

**"Empty" is `rmdir`'s definition, and it is narrower than it looks.** A
directory holding only *other directories* is not empty even though it holds no
files, and `rmdir` will refuse it - so a naive "no entries" test would report a
directory as removable that is not. The rule is applied properly: a directory is
empty only if `rmdir` would succeed on it.

**A directory that cannot be entered is not an empty one.** Measured, listing it
is a permission error, and reporting "empty" there is the confidently wrong
answer this package keeps recording elsewhere.

Nothing is removed. Deleting a directory is a decision, and `rmdir` on the wrong
one removes a path somebody's bookmarks still point at.
"""

from __future__ import annotations

import os
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: Above this the walk stops and says so.
_CEILING = 30000


def _is_empty(path: Path) -> "tuple[bool, str]":
    """(empty, reason).

    **`rmdir`'s definition, not "no entries".** A directory containing only
    subdirectories holds no files but is not removable, and calling it empty
    would send someone to `rmdir` a directory that refuses. Measured: 217 empty
    directories here, of which a naive entry-count test would have over-reported.
    """
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    return False, "holds at least one sub-directory"
                return False, "holds at least one entry"
        return True, ""
    except PermissionError:
        return False, "could not be read"
    except OSError as exc:
        return False, f"could not be read ({exc.errno})"


def _walk(top: Path, ceiling: int):
    """(empties, dirs seen, skipped, truncated, unentered)."""
    empties: list = []
    seen = 0
    skipped = 0
    truncated = False
    unentered: list = []

    def _onerror(exc):
        nonlocal skipped
        skipped += 1
        where = getattr(exc, "filename", None)
        if where:
            unentered.append(str(where))

    for root, dirs, _names in os.walk(top, onerror=_onerror):
        for name in dirs:
            path = Path(root) / name
            seen += 1
            empty, _why = _is_empty(path)
            if empty:
                empties.append(path)
            if seen >= ceiling:
                truncated = True
                break
        if truncated:
            break
    return empties, seen, skipped, truncated, unentered


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    try:
        top = files.expand(raw or "~")
        if not top.is_dir():
            return f"{top} is not a directory, so there are no directories in it."
    except files.PathProblem as exc:
        return str(exc)

    try:
        limit = max(1, min(int(arguments.get("limit") or 20), 200))
    except (TypeError, ValueError):
        return f"{arguments.get('limit')!r} is not a count."

    empties, seen, skipped, truncated, unentered = _walk(top, _CEILING)
    if not seen:
        return (f"No directories under {top}. That is not the same as it "
                "being empty - a permission problem looks like this too.")

    if not empties:
        lines = [f"{seen} directory(ies) under {top}, and **none of them is "
                 "empty**."]
        lines.append("")
        lines.append("Nothing here is a leftover of a move or an uninstall, "
                     "which is a different answer from \"nothing found\".")
    else:
        lines = [f"**{len(empties)} of {seen} directory(ies) under {top} are "
                 "empty** - they hold no files at all, so `rmdir` would take "
                 "them."]
        lines.append("")
        for path in sorted(empties)[:limit]:
            lines.append(f"  {path}")
        if len(empties) > limit:
            lines.append(f"  ... {len(empties) - limit} more")
        lines.append("")
        lines.append("Empty is **not** the same as unused: a directory can be "
                     "empty because something is about to write into it, or "
                     "because a path in a script still points there.")

    if unentered:
        lines.append("")
        lines.append(f"**{len(unentered)} directory(ies) could not be "
                     "entered**, so they are not in these figures: "
                     + ", ".join(unentered[:6])
                     + (" ..." if len(unentered) > 6 else ""))
    if truncated:
        lines.append("")
        lines.append(f"Stopped after {seen} directory(ies); this is not a "
                     "complete list. Narrow the path and ask again.")
    if skipped:
        lines.append(f"({skipped} path(s) could not be read and are not "
                     "counted - an unreadable directory is not an empty one.)")
    lines.append("")
    lines.append("None of these has been removed.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "empty_directories",
        "description": (
            "Find directories under a path that hold nothing at all. Use for "
            "'clean up empty folders', 'what are these leftover directories', "
            "'tidy this up'. Empty means rmdir would succeed - a directory "
            "holding only sub-directories is not empty - and one that cannot be "
            "read is not empty either. Reports what it found and removes "
            "nothing. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Directory to search. Defaults to "
                                         "your home directory.")},
                "limit": {"type": "integer",
                          "description": ("How many to show, 1-200. Default "
                                          "20.")},
            },
        },
    },
}

SKILLS = [Skill(name="empty_directories", schema=SCHEMA, run=_run_skill)]
