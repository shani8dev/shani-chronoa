"""Sense: resources running out, rather than resources being used.

`cpu` reports load and memory. `list_processes` (a skill) lists processes on
demand. Neither notices the failure modes that do not show up as "high usage":

- **Zombie processes** - terminated, never reaped, holding a pid slot and a
  table entry forever. A machine accumulates them one crashed daemon at a time
  and nothing reports it until `fork` starts failing.
- **Swap in use** - the memory figure looks fine while the machine is actually
  paging, which is a different problem with a different fix.
- **File-descriptor pressure** - a service leaks descriptors and the limit is
  invisible until it starts refusing connections.

None of these are "high" in any sense the existing senses measure, which is
exactly why they are here.

Honesty rules:

- **No `/proc` is UNKNOWN, not "nothing is wrong."** Same rule as every other
  sense in this package.
- A zombie count is a count of *entries*, not of CPU or memory cost. They are
  nearly free individually and a real problem in aggregate, and the output says
  which it is rather than alarming about one.
- Swap is reported as in-use or not, with the size, and never as a percentage
  of "recommended swap", which is not a real figure.
"""

from __future__ import annotations

import logging
import os
import resource
from pathlib import Path
from typing import List, Optional, Tuple, Union

from shani_chronoa import files
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

_PROC = Path("/proc")
_SWAPS = Path("/proc/swaps")
#: Zombie counts below this are noise from ordinary daemon churn.
_ZOMBIE_WARN = 10
_MAX_ZOMBIES = 10

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "resources",
        "description": (
            "Report the ways a machine runs out of something without looking "
            "busy: zombie processes that were never reaped, whether swap is in "
            "use, and how close the open file-descriptor limit is."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _stat_fields(pid: str) -> Optional[list]:
    """Fields after the comm, which may itself contain spaces and parentheses."""
    try:
        raw = (_PROC / pid / "stat").read_text()
    except OSError:
        return None
    tail = raw.rsplit(")", 1)
    if len(tail) != 2:
        return None
    return tail[1].split()


def read_zombies() -> List[Tuple[str, str]]:
    """(pid, comm) for every process in state Z."""
    out: List[Tuple[str, str]] = []
    try:
        entries = list(_PROC.iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.name.isdigit():
            continue
        fields = _stat_fields(entry.name)
        if not fields or fields[0] != "Z":
            continue
        try:
            comm = (_PROC / entry.name / "comm").read_text().strip()
        except OSError:
            comm = "?"
        out.append((entry.name, comm or "?"))
    return out


def read_swap() -> Optional[dict]:
    """Total and used swap, or None when swap cannot be read."""
    try:
        lines = _SWAPS.read_text().splitlines()
    except OSError:
        return None
    if not lines:
        return {"total": 0, "used": 0, "devices": []}
    devices = []
    total_kb = used_kb = 0
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            total_kb += int(parts[2])
            used_kb += int(parts[3])
        except ValueError:
            continue
        devices.append(parts[0])
    return {"total": total_kb * 1024, "used": used_kb * 1024, "devices": devices}


def fd_usage() -> Optional[dict]:
    """Open descriptors for this process, against the limit it is given."""
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError):
        return None
    try:
        used = len(os.listdir("/proc/self/fd"))
    except OSError:
        return None
    return {"used": used, "soft": soft, "hard": hard}


def _run(arguments: dict) -> Union[str, Percept]:
    if not _PROC.is_dir():
        return (
            "Resource pressure is UNKNOWN: /proc is not mounted, so none of "
            "this could be measured. That is not the same as a machine with no "
            "zombies, no swap and plenty of file descriptors."
        )

    zombies = read_zombies()
    swap = read_swap()
    fds = fd_usage()

    lines = []
    concerns = 0

    if zombies:
        if len(zombies) >= _ZOMBIE_WARN:
            concerns += 1
            lines.append(
                f"  {len(zombies)} zombie process(es) - terminated but never "
                f"reaped by their parent. Each costs a pid slot and a process "
                f"table entry and almost nothing else, so a few are ordinary "
                f"churn; this many means something is not reaping its children.")
        shown = zombies[:_MAX_ZOMBIES]
        lines.append(f"  {len(zombies)} zombie(s): " + ", ".join(
            f"{pid} {comm}" for pid, comm in shown)
            + (f", and {len(zombies) - len(shown)} more" if len(zombies) > len(shown) else ""))
    else:
        lines.append("  no zombie processes")

    if swap is None:
        lines.append("  swap: UNKNOWN - /proc/swaps could not be read")
    elif swap["total"] == 0:
        lines.append("  swap: none configured")
    else:
        pct = round(100.0 * swap["used"] / swap["total"]) if swap["total"] else 0
        if swap["used"]:
            concerns += 1
        lines.append(
            f"  swap: {files.human_size(swap['used'])} of "
            f"{files.human_size(swap['total'])} in use ({pct}%)"
            + (f" on {', '.join(swap['devices'])}" if swap["devices"] else "")
            + (" - the memory figure looks normal while the machine pages"
               if swap["used"] else ""))

    if fds is None:
        lines.append("  file descriptors: UNKNOWN - the limit could not be read")
    else:
        pct = round(100.0 * fds["used"] / fds["soft"]) if fds["soft"] else 0
        if pct >= 50:
            concerns += 1
        lines.append(
            f"  file descriptors: {fds['used']} open of a {fds['soft']:,} limit"
            + (f" ({pct}%)" if fds["soft"] else ""))

    verdict = (f"{concerns} thing(s) worth a look." if concerns
               else "Nothing here is running out.")
    return _SENSE.to_percept(
        f"{verdict}\n" + "\n".join(lines),
        source="proc",
        metadata={
            "zombies": len(zombies),
            "zombie_sample": [f"{p} {c}" for p, c in zombies[:_MAX_ZOMBIES]],
            "swap_used_bytes": None if swap is None else swap["used"],
            "swap_total_bytes": None if swap is None else swap["total"],
            "fds_open": None if fds is None else fds["used"],
            "fd_limit": None if fds is None else fds["soft"],
            "concerns": concerns,
        },
    )


_SENSE = Sense(
    name="resources",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
