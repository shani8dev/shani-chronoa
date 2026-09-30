"""Sense: is this machine still running a copy of a program it replaced?

The bug this answers is real, quiet, and permanent. Upgrade a package, and the
services started from the old binary keep running from an inode that no longer
has a name: `/proc/<pid>/exe` resolves to `/usr/bin/foo (deleted)`. The
behaviour is then whatever the old code did, forever, until something restarts
it - and a machine in that state is lying to you about its own configuration,
because the file on disk and the process in memory are different programs.

The detection has three traps, and each one produces a confident wrong answer
rather than an error.

**1. The ` (deleted)` suffix is a hint, not a verdict.** The kernel appends
exactly that string when the inode has been unlinked, and nothing stops a file
from being *named* `something (deleted)` - distributions ship such names, and a
backup restore can produce one. So the suffix alone is not evidence. The real
test is inode identity: `stat("/proc/<pid>/exe")` resolves the link to the
inode the process is actually executing, which still exists precisely because
the process holds it open, and `stat(base_path)` resolves whatever now sits at
that path. If the two agree, the suffix was just a name. If they differ, or the
path is gone, the process really is running unlinked code.

That distinction is not academic. The ordinary case - `mv new /usr/bin/foo` -
*replaces the path*, so `base` exists and the naive "does the path exist?" test
answers "no, it is not stale" and misses precisely the case people hit. Comparing
inodes gets it right.

**2. A process this account cannot inspect is not a clean bill of health.**
`/proc/<pid>/fd` returns `EACCES` for another user's process, and a `hidepid=2`
mount does it for the user's own. An implementation that treated an unreadable
scan as "no stale binaries" would report a healthy machine on every multi-user
box and on every hardened `/proc` mount - so the count of what *was* read and
the count of what could not be are both reported, and the answer is never a bare
zero.

**3. Only the user's own processes are inspected at all.** Another user's
command line and executable path are not this user's business, and a sense that
enumerated them would be a process-ownership census wearing a disk-space question
as its excuse. Other users' processes are counted as *not inspectable*, which is
a different thing from being clean and is labelled as such.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
# Executable paths name software the user chose to run, in the same way
# `privilege` reports it. Personal, and not public.
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

_PROC = Path("/proc")
_DELETED_SUFFIX = " (deleted)"
_MAX_LISTED = 20


#: Outcomes of asking who owns a PID. Three, not two: a process that has exited
#: between the scan and the stat is not evidence of anything, and a `hidepid`
#: mount denying the stat is not the same thing.
OWNER_OK = "ok"
OWNER_GONE = "gone"
OWNER_DENIED = "denied"


def _stat_owner(pid: str) -> tuple:
    """`(_OWNER_*, uid)` for `/proc/<pid>`, never raising.

    A `hidepid` mount makes this fail for processes the user can otherwise see,
    and reporting that as *denied* is what keeps such a process in the
    unreadable bucket instead of being silently misfiled as another user's - or,
    worse, as this user's and then counted as clean.
    """
    try:
        return (OWNER_OK, os.stat(_PROC / pid).st_uid)
    except FileNotFoundError:
        return (OWNER_GONE, None)
    except OSError:
        return (OWNER_DENIED, None)


def _own_pids() -> Optional[Tuple[List[str], int, int]]:
    """The numeric PIDs this account owns, plus counts of the rest.

    Returns `(pids, other_users, unreadable)`. `other_users` is a positive
    statement about what was deliberately not looked at, which is the honest
    counterpart to a zero.
    """
    own: List[str] = []
    other = 0
    unreadable = 0
    uid = os.getuid()
    try:
        entries = [e.name for e in os.scandir(_PROC) if e.name.isdigit()]
    except OSError:
        return None
    for pid in entries:
        state, owner = _stat_owner(pid)
        if state == OWNER_DENIED:
            unreadable += 1
        elif state == OWNER_OK and owner == uid:
            own.append(pid)
        elif state == OWNER_OK:
            other += 1
    own.sort(key=int)
    return own, other, unreadable


def _exe_link(pid: str) -> Optional[str]:
    try:
        return os.readlink(_PROC / pid / "exe")
    except OSError:
        return None


def check_exe(pid: str) -> Optional[dict]:
    """Is this one process running a binary that no longer matches its path?

    Returns `None` when the process is gone or its link is unreadable, and a
    dict with `stale` True/False/None otherwise:

    - `stale` True - the path is gone, or holds a *different* inode than the
      one this process is executing. Confirmed.
    - `stale` False - the inode at the path is the same one being executed. The
      ` (deleted)` suffix, if any, was part of the filename.
    - `stale` None - the suffix is present but the inodes could not be compared
      (the running inode could not be stat'd). Unconfirmed, and reported as
      such rather than folded into either answer.
    """
    target = _exe_link(pid)
    if target is None:
        return None
    if not target.endswith(_DELETED_SUFFIX):
        return {"target": target, "stale": False, "why": ""}

    base = target[: -len(_DELETED_SUFFIX)]
    try:
        running = os.stat(_PROC / pid / "exe")
    except OSError:
        # The suffix is there but nothing can be compared. Reported, not guessed.
        return {"target": base, "stale": None,
                "why": "its running inode could not be stat'd, so the "
                       "' (deleted)' in the name could not be checked against "
                       "the path"}
    try:
        on_disk = os.stat(base)
    except OSError:
        return {"target": base, "stale": True,
                "why": "nothing exists at that path any more"}
    if (running.st_dev, running.st_ino) == (on_disk.st_dev, on_disk.st_ino):
        # Same inode: the file is genuinely called this, and the kernel adds the
        # suffix to a *deleted* inode - so this is a name, not a deletion.
        return {"target": target, "stale": False,
                "why": "the path holds the same inode this process is running, "
                       "so the name simply ends in ' (deleted)'"}
    return {"target": base, "stale": True,
            "why": "the path now holds a different file from the one this "
                   "process is executing"}


def scan() -> Optional[dict]:
    """Every stale binary among this user's processes, or None if /proc is gone."""
    found = _own_pids()
    if found is None:
        return None
    pids, other_users, not_inspectable = found

    stale: List[dict] = []
    unconfirmed: List[dict] = []
    readable = 0
    unreadable_fds = 0
    for pid in pids:
        result = check_exe(pid)
        if result is None:
            # Our own pid, but the kernel would not resolve its link - a process
            # that exited mid-scan, or a restrictive setting. Not evidence of
            # anything either way, so it is counted rather than dropped.
            unreadable_fds += 1
            continue
        readable += 1
        entry = {"pid": pid, "path": result["target"]}
        if result["stale"] is True:
            stale.append(entry)
        elif result["stale"] is None:
            unconfirmed.append({**entry, "why": result["why"]})

    return {
        "stale": stale,
        "unconfirmed": unconfirmed,
        "processes_readable": readable,
        "processes_unreadable": unreadable_fds,
        "processes_not_inspectable": not_inspectable,
        "other_users_processes": other_users,
    }


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("stale"):
        return f"Not scanning running binaries: {config.sense_allowed_reason('stale')}."

    result = scan()
    if result is None:
        return (
            "Stale binaries: UNKNOWN - /proc could not be walked, so no process "
            "was inspected. That is not a report of zero stale binaries: a zero "
            "here would mean every running program was checked and none was "
            "unlinked, which is a completely different claim."
        )

    stale = result["stale"]
    unconfirmed = result["unconfirmed"]
    scope = (
        f"Scope: this account's {result['processes_readable']} readable "
        f"process(es) only"
    )
    if result["other_users_processes"]:
        scope += (
            f"; {result['other_users_processes']} process(es) belonging to "
            f"other users were NOT inspected and are not accounted for"
        )
    if result["processes_not_inspectable"]:
        scope += (
            f"; {result['processes_not_inspectable']} process(es) could not be "
            f"inspected at all, because this account was not permitted to stat "
            f"them (a hidepid /proc mount) - so their owner is unknown and they "
            f"are counted as neither ours nor another user's"
        )
    if result["processes_unreadable"]:
        scope += (
            f"; {result['processes_unreadable']} of this account's own "
            f"processes had an unreadable exe link (a kernel thread, or a "
            f"process that exited mid-scan)"
        )

    if not stale and not unconfirmed:
        return (
            f"No stale binaries among this account's processes. {scope}. A "
            f"process running a binary that has since been replaced on disk "
            f"would show as '/path (deleted)' and be confirmed by its inode "
            f"differing from the file now at that path."
        )

    lines = [f"{len(stale)} process(es) running a binary that no longer matches "
             f"the file at its path:"]
    for entry in stale[:_MAX_LISTED]:
        lines.append(f"  pid {entry['pid']}: {entry['path']}")
    if len(stale) > _MAX_LISTED:
        lines.append(f"  and {len(stale) - _MAX_LISTED} more")
    if unconfirmed:
        lines.append(
            f"  {len(unconfirmed)} further process(es) end in ' (deleted)' but "
            f"could not be confirmed, because their running inode could not be "
            f"stat'd. They are counted separately rather than as either answer."
        )
    lines.append(f"  {scope}.")
    lines.append(
        "  A process in this state keeps running the code it started with, so "
        "its behaviour no longer matches the file on disk until it restarts."
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc-exe",
        metadata={
            "scanned": True,
            "stale_count": len(stale),
            "unconfirmed_count": len(unconfirmed),
            "processes_readable": result["processes_readable"],
            "processes_unreadable": result["processes_unreadable"],
            "processes_not_inspectable": result["processes_not_inspectable"],
            "other_users_processes": result["other_users_processes"],
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "stale",
        "description": (
            "Report processes still executing a binary that has since been "
            "replaced or removed - '/path (deleted)' in /proc/<pid>/exe. "
            "Confirms it by comparing the inode the process is running against "
            "the file now at that path, so a file whose name simply ends in "
            "' (deleted)' is not a false positive. Inspects only this account's "
            "processes and reports how many of them and how many other users' "
            "processes could not be looked at, rather than reporting a clean "
            "scan it did not achieve."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="stale",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
