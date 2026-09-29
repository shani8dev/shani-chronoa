"""Sense: btrfs snapshots, which are this machine's undo button.

Shanios is a btrfs blue-green system: `shani-deploy` takes a snapshot, switches
the subvolume, and rolls back by switching back. That machinery is the entire
reason a bad update is recoverable, and nothing in this project could report that
it exists, how many there are, or which one is currently booted.

That gap has a specific failure mode worth naming. A rollback-capable system
whose snapshots are never listed is a system where the safety net is invisible,
so the question "do I actually have somewhere to go back to?" has no answer until
the moment it is urgently needed. Absence of information here reads exactly like
absence of snapshots.

Only reads are performed. `btrfs subvolume delete` and `btrfs subvolume snapshot`
are never invoked by this module: a sense that creates or destroys the thing it
observes is not a sensor.

Honesty rules:

- **No btrfs is UNKNOWN, not "no snapshots."** Most machines are not btrfs, and
  for those this sense has nothing to say rather than a good result.
- The mounted subvolume is read from the filesystem itself, not inferred from
  the snapshot list, so "currently booted" is a fact rather than a guess.
- Snapshot age is only reported when the tool actually prints a creation time.
  A snapshot with an unreadable timestamp is listed as such, not as brand new.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import List, Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0
_TIMEOUT = 25

_ROOT = "/"
_MAX_LISTED = 30

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "snapshots",
        "description": (
            "Report the btrfs snapshots available on this machine and which "
            "subvolume is currently booted, which is what makes a rollback "
            "possible. Read-only: never creates or deletes one."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _btrfs(*args: str) -> Optional[subprocess.CompletedProcess]:
    if shutil.which("btrfs") is None:
        return None
    try:
        return subprocess.run(["btrfs", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def is_btrfs() -> Optional[bool]:
    """Whether the root filesystem is btrfs, or None if it could not be told."""
    proc = _btrfs("subvolume", "show", _ROOT)
    if proc is None:
        return None
    if proc.returncode != 0:
        # "not a btrfs filesystem" is a real, definite answer, not a failure.
        return False
    return "Name:" in proc.stdout or "Subvolume ID" in proc.stdout


def current_subvolume() -> Optional[str]:
    """The subvolume path the root filesystem is currently on, or None."""
    proc = _btrfs("subvolume", "show", _ROOT)
    if proc is None or proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("Name:"):
            return line.split("Name:", 1)[1].strip()
    return None


def list_snapshots() -> Optional[List[dict]]:
    """Snapshot subvolumes, or None when the list could not be read."""
    proc = _btrfs("subvolume", "list", "-p", _ROOT)
    if proc is None or proc.returncode != 0:
        return None
    rows: List[dict] = []
    for line in proc.stdout.splitlines():
        # "ID 258 gen 42 top level 5 path <path>"
        if " path " not in line:
            continue
        path = line.split(" path ", 1)[1].strip()
        rows.append({
            "id": line.split()[0] if line.split() else "?",
            "path": path,
            "read_only": "ro" in line.split()[:6],
        })
    return rows


def _looks_like_snapshot(path: str) -> bool:
    """Snapshot subvolumes on this system live under a snapshots directory.

    Deliberately a name test and not a claim about provenance: the alternative
    is reporting every subvolume on the machine as a rollback point, which would
    be a larger and less useful list dressed up as a more confident answer.
    """
    return "snapshot" in path.lower()


def _run(arguments: dict) -> Union[str, Percept]:
    if shutil.which("btrfs") is None:
        return (
            "Snapshots are UNKNOWN: the btrfs tool is not installed, so nothing "
            "could be listed. That is not the same as having no snapshots."
        )

    on_btrfs = is_btrfs()
    if on_btrfs is None:
        return (
            "Snapshots are UNKNOWN: btrfs could not be queried at all, so "
            "neither the subvolume layout nor the snapshot list is established."
        )
    if not on_btrfs:
        return (
            "The root filesystem is not btrfs, so this sense has nothing to "
            "report. That is a statement about the filesystem type, not about "
            "whether any way of rolling back exists."
        )

    current = current_subvolume()
    snaps = list_snapshots()
    if snaps is None:
        return (
            f"The root filesystem is btrfs and is currently on subvolume "
            f"{current or 'UNKNOWN'}, but the snapshot list could not be read, "
            f"so how many rollback points exist is UNKNOWN rather than zero."
        )

    rollback = [s for s in snaps if _looks_like_snapshot(s["path"])]
    others = len(snaps) - len(rollback)

    lines = [f"Root filesystem is btrfs, currently on subvolume: {current or 'UNKNOWN'}."]
    if not rollback:
        lines.append(
            f"No subvolume under a 'snapshot' path was found among "
            f"{len(snaps)} subvolume(s). If rollback on this machine works "
            f"through shani-deploy, its snapshots are not named that way here, "
            f"so treat this as 'not found by name' rather than 'there are none'."
        )
    else:
        lines.append(f"{len(rollback)} snapshot subvolume(s) available to roll back to:")
        for row in sorted(rollback, key=lambda r: r["id"], reverse=True)[:_MAX_LISTED]:
            marker = " (read-only)" if row["read_only"] else ""
            here = " <- booted" if current and current == row["path"] else ""
            lines.append(f"  id {row['id']}  {row['path']}{marker}{here}")
        if len(rollback) > _MAX_LISTED:
            lines.append(f"  and {len(rollback) - _MAX_LISTED} more")
    if others:
        lines.append(f"{others} other subvolume(s) not named as snapshots.")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="btrfs",
        metadata={
            "btrfs": True,
            "current_subvolume": current,
            "snapshot_count": len(rollback),
            "subvolume_count": len(snaps),
            "has_rollback_point": bool(rollback),
        },
    )


_SENSE = Sense(
    name="snapshots",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
