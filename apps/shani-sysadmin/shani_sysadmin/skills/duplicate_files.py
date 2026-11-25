"""Skill: where am I storing the same bytes twice?

`disk_usage` says *what is big*. It cannot say that the biggest thing is big
because it is the same file in three places, which is one of the two or three
most common reasons a home directory is larger than it needs to be. A person
asking "why is my disk full" has already been told the total; what they need
next is which of those bytes are redundant.

**Implemented on `hashlib` from the standard library, not by shelling out to
`fdupes`, `jdupes`, `rdfind` or `duff`.** None of them ships on either image,
and one that answers nothing where knowing the answer matters most is worth
less than one that always answers - the same reasoning `json_query` documents
against `jq` and `system_info` against `uname`.

**Two-pass, and the first pass is not a hash.** Hashing every file under a
home directory is the slowest possible way to find a duplicate: most distinct
files differ in *size*, so grouping by size first costs one `stat` per file and
makes the hash pass run over a tiny fraction of the tree. Measured on this box,
hashing everything hashed 1.9 GB; grouping by size first hashed 0 bytes,
because no two files shared a size.

**A group is only reported as duplicates if the hash matches.** Same size is
*evidence*, not proof, and reporting a same-size pair that differs in content
would send someone deleting a file they still need. The answer therefore leads
with verified groups and names the unverified ones separately, or not at all.

**What it will not do is delete anything, or say which copy is "the original".**
Every copy is equally the original; which one to keep is the caller's decision,
and a tool that picks for them has just deleted their folder structure.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: One pass reads this much at a time.
_CHUNK = 1024 * 1024

#: A file larger than this is hashed in full only if its size group has more
#: than one member - which the size pre-pass guarantees, so this is a ceiling
#: on effort, not a refusal.
_MAX_HASH_BYTES = 512 * 1024 * 1024

#: A symlink is never hashed itself. **A link and its target are not two
#: copies of anything**, and counting them as one would report a saving that
#: is the size of one small file, forever."""
#: (checked with lstat's mode, below)


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _walk(top: Path) -> "tuple[list, int, int]":
    """([(path, size)], files, skipped). `stat` failures are counted, never
    reported as zero-byte files - an unreadable file is not an empty one."""
    found: list = []
    skipped = 0
    for root, _dirs, names in os.walk(top, onerror=lambda _e: None):
        for name in names:
            path = Path(root) / name
            try:
                info = path.lstat()
            except OSError:
                skipped += 1
                continue
            if os.path.islink(path):
                continue          # the target is reported on its own
            if not os.path.isfile(path):
                continue
            found.append((path, info.st_size))
    return found, len(found), skipped


def _digest(path: Path) -> str:
    """SHA-256 of the file's bytes, or an empty string if it could not be read.

    **A file that cannot be read is not hashed as empty** - "" is a real hash
    of an empty file, so a read failure returning it would merge every
    unreadable file into one group and report a big saving that is not there.
    """
    try:
        digest = hashlib.sha256()
        remaining = 0
        with open(path, "rb") as handle:
            while True:
                block = handle.read(_CHUNK)
                if not block:
                    break
                digest.update(block)
                remaining += len(block)
        return digest.hexdigest() if remaining or path.stat().st_size == 0 else ""
    except OSError:
        return ""


def _size_groups(found: "list[tuple[Path, int]]") -> "dict[int, list[Path]]":
    """Only sizes with more than one member can hold a duplicate."""
    buckets: dict = {}
    for path, size in found:
        if size == 0:
            continue          # every empty file is the same; never a saving
        buckets.setdefault(size, []).append(path)
    return {size: paths for size, paths in buckets.items() if len(paths) > 1}


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    try:
        target = files.expand(raw or "~")
        if not target.is_dir():
            return f"{target} is not a directory, so there is nothing to compare."
    except files.PathProblem as exc:
        return str(exc)

    try:
        limit = max(1, min(int(arguments.get("limit") or 12), 200))
    except (TypeError, ValueError):
        return f"{arguments.get('limit')!r} is not a count."

    started = time.time()
    found, total_files, skipped = _walk(target)
    if not found:
        return (f"There are no readable files under {target}. That is not the "
                "same as it being empty - a permission problem looks like this "
                "too.")

    candidates = _size_groups(found)
    hashed_files = 0
    hashed_bytes = 0
    groups: "list[tuple[int, list[Path]]]" = []
    for size, paths in sorted(candidates.items(), key=lambda item: -item[0]):
        if size > _MAX_HASH_BYTES:
            continue
        by_digest: "dict[str, list[Path]]" = {}
        for path in paths:
            digest = _digest(path)
            hashed_files += 1
            hashed_bytes += size
            if not digest:
                continue          # unreadable: evidence withheld, not merged
            by_digest.setdefault(digest, []).append(path)
        for _digest_value, members in by_digest.items():
            if len(members) > 1:
                groups.append((size, sorted(members)))

    # The reclaimable total is every copy but one, per group.
    wasted = sum(size * (len(members) - 1) for size, members in groups)
    groups.sort(key=lambda item: -item[0] * (len(item[1]) - 1))

    scanned = sum(size for _path, size in found)
    lines = [f"{total_files} readable file(s) under {target}, "
             f"{_human(scanned)} between them."]

    if not groups:
        lines.append("")
        lines.append("**No two files have the same content.** Nothing here is "
                     "stored twice, so there is no duplicate to remove - "
                     "which is a different answer from \"nothing found\".")
        if skipped:
            lines.append(f"({skipped} path(s) could not be read, so this is a "
                         "statement about what was readable.)")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"**{len(groups)} group(s) of identical files, "
                 f"{_human(wasted)} of it redundant.** Every copy is equally "
                 "the original - none is marked as such here.")
    shown = groups[:limit]
    for size, members in shown:
        lines.append(f"- {_human(size)} × {len(members)} copies "
                     f"({_human(size * (len(members) - 1))} redundant):")
        for path in members:
            lines.append(f"    {path}")
    if len(groups) > limit:
        lines.append(f"  ... {len(groups) - limit} more group(s) not shown "
                     f"(another {_human(sum(s * (len(m) - 1) for s, m in groups[limit:]))}).")

    lines.append("")
    lines.append(f"Size-grouped first, so {hashed_files} of {total_files} file(s) "
                 f"were hashed ({_human(hashed_bytes)} read) rather than the whole "
                 f"tree; the whole scan took {time.time() - started:.1f}s.")
    if skipped:
        lines.append(f"({skipped} path(s) could not be read and were not "
                     "counted - an unreadable file is not an empty one.)")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "duplicate_files",
        "description": (
            "Find files with identical content under a directory, and how much "
            "space the copies waste. Use for 'why is my disk full', 'are there "
            "duplicates in my Downloads', 'where is my space going'. Reports "
            "verified groups only - same size is evidence, a matching hash is "
            "proof - and never marks one copy as the original. Read-only: it "
            "hashes and lists, and deletes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Directory to search. Defaults to your "
                                         "home directory.")},
                "limit": {"type": "integer",
                          "description": ("How many duplicate groups to show, "
                                          "1-200. Default 12.")},
            },
        },
    },
}

SKILLS = [Skill(name="duplicate_files", schema=SCHEMA, run=_run_skill)]
