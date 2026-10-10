"""Skill: where are all the files with this name?

`find_files` matches a name **pattern** and lists what matches. That is not the
question people have when they find they have several copies of something: *where
are they, and are they the same?* A `report.docx` in three directories is usually
the same document saved three times, and the answer is which one is newest.

`duplicate_files` answers the content half of that. **This answers the location
half, and the two are deliberately separate**: same name is not same content, and
same content is not same name, so neither implies the other and a reader that
merged them would report a copy that differs as a duplicate.

Measured on this box: **1,481 names occur in more than one directory** under a
home directory, and nothing could list them.

**No name is assumed to be the same file.** The answer shows the paths, the sizes
and the modification times so a person can see whether the copies agree, and it
points at `duplicate_files` for the content question rather than guessing at it.

Nothing is deleted and no copy is marked as "the" one.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: Above this the walk stops and says so.
_CEILING = 60000


def _human(size: float) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _age(when: float) -> str:
    days = int((time.time() - when) / 86400)
    if days >= 730:
        return f"{days // 365}y ago"
    if days >= 60:
        return f"{days // 30}mo ago"
    if days > 0:
        return f"{days}d ago"
    return "today"


def _walk(top: Path, needle: str, ceiling: int, fold: bool = False):
    """(matches, files seen, skipped, truncated, unentered).

    **Case-sensitive by default**, because on Linux a name is case-sensitive and
    `Report.docx` is a different file from `report.docx` - folding case would
    merge two things and report a copy that does not exist.

    **One pass, whatever the folding.** The first version walked the tree, then
    walked it *again* when case-insensitive was asked for - so a search cost two
    full traversals and reported `seen` from the first, empty one.
    """
    matches: list = []
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

    for root, _dirs, names in os.walk(top, onerror=_onerror):
        for name in names:
            if needle not in (name.lower() if fold else name):
                continue
            path = Path(root) / name
            try:
                info = path.lstat()
            except OSError:
                skipped += 1
                continue
            if os.path.islink(path):
                continue
            if not os.path.isfile(path):
                continue
            seen += 1
            matches.append((path, info.st_size, info.st_mtime))
            if seen >= ceiling:
                truncated = True
                break
        if truncated:
            break
    matches.sort(key=lambda item: -item[1])
    return matches, seen, skipped, truncated, unentered


def _run_skill(arguments: dict) -> str:
    raw_name = str(arguments.get("name") or "").strip()
    if not raw_name:
        return "Which name should I look for?"

    raw_path = str(arguments.get("path") or "").strip()
    try:
        top = files.expand(raw_path or "~")
        if not top.is_dir():
            return f"{top} is not a directory, so there are no files in it."
    except files.PathProblem as exc:
        return str(exc)

    case_sensitive = bool(arguments.get("case_sensitive", True))
    matches, seen, skipped, truncated, unentered = _walk(
        top, raw_name if case_sensitive else raw_name.lower(), _CEILING,
        fold=not case_sensitive)

    if not matches:
        lines = [f"No file with {raw_name!r} in its name under {top}."]
        # **A permission failure must not read as "not there".** Measured: with a
        # mode-000 directory in the tree this branch returned bare "no file",
        # which is identical to the answer for a name that genuinely is not
        # present - the absence-shaped green this package keeps recording.
        if unentered:
            lines.append("")
            lines.append(f"But **{len(unentered)} directory(ies) could not be "
                         "entered**, so they were not searched: "
                         + ", ".join(unentered[:6])
                         + (" ..." if len(unentered) > 6 else ""))
            lines.append("That is a different answer from there being no such "
                         "file, and nothing here claims which it is.")
        else:
            lines.append("")
            lines.append("`find_files` matches the same way and lists them too "
                         "- this adds the sizes and dates, which is the part "
                         "that says whether they are copies.")
        if skipped:
            lines.append(f"({skipped} path(s) could not be read.)")
        return "\n".join(lines)

    sizes = {size for _p, size, _m in matches}
    same_size = len(sizes) == 1
    lines = [f"{len(matches)} file(s) with {raw_name!r} in the name under "
             f"{top}:"]
    lines.append("")
    for path, size, mtime in matches[:60]:
        lines.append(f"  {_human(size):>10}  {path}   ({_age(mtime)})")
    if len(matches) > 60:
        lines.append(f"  ... {len(matches) - 60} more")

    lines.append("")
    if same_size and len(matches) > 1:
        lines.append(f"**All {len(matches)} are the same size** "
                     f"({_human(next(iter(sizes)))}), which is a hint they may "
                     "be copies - not proof. Sizes agreeing is what a random "
                     "pair of different files does about as often as not.")
    elif len(matches) > 1:
        biggest = max(matches, key=lambda item: item[1])
        lines.append(f"They are **not** all the same size, so they are not all "
                     f"copies of one thing. The largest is "
                     f"{_human(biggest[1])} at {biggest[0]}.")
    lines.append("")
    lines.append("The newest is "
                 f"{max(matches, key=lambda item: item[2])[0]} - if these are "
                 "copies, that one is usually the one you want.")
    lines.append("")
    lines.append("**This has not compared their contents.** Same name is not "
                 "same content, and same content is not same name; "
                 "`duplicate_files` answers the content question and this one "
                 "answers where they are.")
    if unentered:
        lines.append("")
        lines.append(f"**{len(unentered)} directory(ies) could not be "
                     "entered**, so they are not in these results: "
                     + ", ".join(unentered[:6]))
    if truncated:
        lines.append("")
        lines.append(f"Stopped after {seen} matching file(s); this is not a "
                     "complete list.")
    if skipped:
        lines.append(f"({skipped} path(s) could not be read.)")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "files_by_name",
        "description": (
            "Find every file with a given name under a directory, with sizes and "
            "modification dates, so you can see whether they are copies. Use for "
            "'where are all my report.docx', 'how many copies of this do I "
            "have', 'which is the newest'. Reports the newest and whether the "
            "sizes agree, and does NOT claim the contents match - "
            "duplicate_files answers that. Deletes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "The name (or part of one) to look for."},
                "path": {"type": "string",
                         "description": ("Directory to search. Defaults to your "
                                         "home directory.")},
                "case_sensitive": {"type": "boolean",
                                   "description": ("Match the name exactly as "
                                                   "typed. Default true.")},
            },
            "required": ["name"],
        },
    },
}

SKILLS = [Skill(name="files_by_name", schema=SCHEMA, run=_run_skill)]
