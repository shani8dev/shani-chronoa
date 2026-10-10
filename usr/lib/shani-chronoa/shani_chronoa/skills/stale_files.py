"""Skill: what here is big *and* untouched - i.e. what could I actually delete?

`disk_usage` says what is big. `find_recently_modified` says what is recent.
Neither answers the question a person has after either of them, which is
*which of these could I delete* - and the answer is not "the biggest ones". The
biggest files on a machine are usually the ones that matter most.

**A file is only a deletion candidate if two things are true at once**: it is
large, and nothing has touched it in a long time. Either alone is misleading -
a big file touched yesterday is in use, and a small untouched file is not worth
the decision. A large untouched file is the one where the two facts line up,
and that is the only thing this reports.

**Age is measured from mtime, and mtime is not "last opened".** The kernel does
not record reads at all by default (`relatime` records one, at best, per day,
and only when the previous read is older than the mtime). So a file read every
day can carry an old mtime. **This says so, in the answer**, because the
alternative is listing something a person reads daily as untouched - which is
the most damaging wrong answer available here, being both confident and
actionable.

Nothing is deleted, and no "safe to delete" is claimed for any file. A large
untouched file is a *candidate*: it might be the archive nobody has needed
since 2023, or it might be the backup of the thing that just broke.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: A file smaller than this is never a candidate regardless of age - the
#: decision costs more than the space.
_FLOOR = 16 * 1024 * 1024

#: Default age floor. Deliberately long: a file untouched for less than a year
#: is usually just not the current thing.
_DEFAULT_DAYS = 365

#: Bounded, so a walk over an enormous tree returns a partial answer that says
#: so rather than a short list that looks complete.
_CEILING = 20000


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _age(when: float) -> str:
    days = int((time.time() - when) / 86400)
    if days >= 730:
        return f"{days // 365} year(s) ago"
    if days >= 60:
        return f"{days // 30} month(s) ago"
    return f"{days} day(s) ago"


def _walk(top: Path, floor: int, cutoff: float, ceiling: int):
    """(candidates, files seen, paths unreadable, truncated, unentered dirs).

    **`os.walk`'s error callback is where an unentered directory shows up**, and
    swallowing it means the answer reports "0 files checked" for a tree it never
    looked inside. Measured: a directory with mode 000 vanished silently, and the
    answer said 0 files. The directory is named instead.
    """
    candidates: list = []
    seen = 0
    skipped = 0
    truncated = False
    unreadable: list = []

    def _onerror(exc):
        nonlocal skipped
        skipped += 1
        target = getattr(exc, "filename", None)
        if target:
            unreadable.append(str(target))

    for _root, _dirs, names in os.walk(top, onerror=_onerror):
        for name in names:
            path = Path(_root) / name
            try:
                info = path.stat()
            except OSError:
                skipped += 1
                continue
            if not os.path.isfile(path):
                continue
            seen += 1
            if info.st_size < floor:
                continue
            if info.st_mtime > cutoff:
                continue
            candidates.append((path, info.st_size, info.st_mtime))
            if seen >= ceiling:
                truncated = True
                break
        if truncated:
            break
    candidates.sort(key=lambda item: -item[1])
    return candidates, seen, skipped, truncated, unreadable


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    try:
        top = files.expand(raw or "~")
        if not top.is_dir():
            return f"{top} is not a directory, so there is nothing to look at."
    except files.PathProblem as exc:
        return str(exc)

    # **`or _DEFAULT_DAYS` is not enough, and the way it fails is silent.**
    # `0 or 365` is 365, so asking for "older than 0 days" - every file on the
    # machine - came back with a one-year window applied and nothing said so.
    # The default is for an absent or None argument, and for nothing else.
    if "older_than_days" not in arguments or arguments["older_than_days"] in (None, ""):
        days = _DEFAULT_DAYS
    else:
        try:
            days = int(arguments["older_than_days"])
        except (TypeError, ValueError):
            return (f"{arguments['older_than_days']!r} is not a number of "
                    "days.")
    if days < 1:
        return f"{days} days is shorter than this will report on."

    try:
        limit = max(1, min(int(arguments.get("limit") or 12), 200))
    except (TypeError, ValueError):
        return f"{arguments.get('limit')!r} is not a count."

    cutoff = time.time() - days * 86400
    candidates, seen, skipped, truncated, unreadable = _walk(
        top, _FLOOR, cutoff, _CEILING)

    if not candidates:
        lines = [f"No file under {top} is both over {_human(_FLOOR)} and "
                 f"untouched for {days} days. That is a statement about the "
                 f"{seen} file(s) checked, not a promise that nothing could "
                 f"be deleted."]
        if unreadable:
            lines.append("")
            lines.append(f"**{len(unreadable)} directory(ies) could not be "
                         "entered**, so their contents are not in that count: "
                         + ", ".join(unreadable[:10]))
        return "\n".join(lines)

    reclaimable = sum(size for _p, size, _m in candidates)
    lines = [f"{len(candidates)} file(s) under {top} are over "
             f"{_human(_FLOOR)} and untouched for at least {days} days, "
             f"{_human(reclaimable)} together."]
    lines.append("")
    lines.append("**None of these is safe to delete, and none has been "
                 "deleted.** They are the ones where both facts line up - big, "
                 "and not touched - which is where to start looking.")
    lines.append("")
    for path, size, mtime in candidates[:limit]:
        lines.append(f"- {_human(size):>10}  {path}  (untouched {_age(mtime)})")
    if len(candidates) > limit:
        lines.append(f"  ... {len(candidates) - limit} more, another "
                     f"{_human(sum(s for _p, s, _m in candidates[limit:]))}.")

    lines.append("")
    lines.append("**Big is not the same as safe to delete**, and untouched is "
                 "not the same as unused: the kernel does not record reads by "
                 "default, so a file opened every day can still carry an old "
                 "timestamp. Treat this as where to look, not as a list to act "
                 "on.")
    if truncated:
        lines.append(f"Stopped after {seen} file(s); this is not a complete "
                     "list. Narrow the path and ask again.")
    if unreadable:
        lines.append("")
        lines.append(f"**{len(unreadable)} directory(ies) could not be "
                     "entered**, so their contents are not in these figures: "
                     + ", ".join(unreadable[:10])
                     + (f" (+{len(unreadable) - 10} more)"
                        if len(unreadable) > 10 else ""))
    if skipped:
        lines.append(f"({skipped} path(s) could not be read, so they are not "
                     "in these figures - a file this cannot read is not a file "
                     "that does not exist.)")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "stale_files",
        "description": (
            "Find files that are both large and untouched for a long time - "
            "the deletion candidates a disk-usage answer only gestures at. Use "
            "for 'what can I delete', 'what is safe to clean up', 'what in "
            "here is old and big'. Reports candidates only: it claims nothing "
            "is safe to delete, removes nothing, and says that an untouched "
            "timestamp is not proof a file is unused. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Directory to search. Defaults to "
                                         "your home directory.")},
                "older_than_days": {"type": "integer",
                                    "description": ("Age floor in days. "
                                                    "Default 365.")},
                "limit": {"type": "integer",
                          "description": ("How many to show, 1-200. Default "
                                          "12.")},
            },
        },
    },
}

SKILLS = [Skill(name="stale_files", schema=SCHEMA, run=_run_skill)]
