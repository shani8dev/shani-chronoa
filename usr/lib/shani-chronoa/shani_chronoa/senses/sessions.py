"""Sense: who is using this machine right now, and what is running as root.

A local assistant that runs as an ordinary user is in an odd position: it is
trusted with the machine, and the one thing it structurally cannot see is other
people's sessions. `privilege` reports which processes *hold* a dangerous
capability, which is about capability rather than about presence. Nothing
reported who is logged in, or what is running as root outside the service tree.

That second question is the one worth asking. On a systemd machine, a great many
root processes are entirely normal and uninteresting - the init system, journald,
udevd, getty. What is interesting is a root process that is *not* in that tree,
because it was started by something and is doing something now, and its parent
is usually a user's session.

Honesty rules:

- **An unreadable session list is UNKNOWN, not "only you are logged in."** Those
  are very different claims and the second one is the dangerous one.
- A root process is not called suspicious. Plenty are legitimate - a package
  update, a backup, a `pkexec` prompt the user is looking at right now. It is
  listed, with its parent, so a person can judge it.
- Kernel threads are excluded and the exclusion is stated, because they are
  parented to pid 2 and are not processes anyone started.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0
_TIMEOUT = 15

_PROC = Path("/proc")
_MAX_ROOT = 20
#: The init system and its own children. Anything parented to pid 1 that is one
#: of these is the machine's own plumbing, not something a person ran.
_INIT_NAMES = frozenset({
    "systemd", "init", "kthreadd", "ksoftirqd", "rcu_sched", "rcu_bh",
    "watchdog", "kswapd0", "kworker", "migration", "jbd2", "kcompactd",
    "khugepaged", "oom_reaper", "cpuhp", "kdevtmpfs", "kauditd",
})

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sessions",
        "description": (
            "Report who is logged in to this machine, and which processes are "
            "running as root outside the init system's own service tree."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except OSError:
        return None


def read_logged_in() -> Optional[List[dict]]:
    """Interactive sessions, or None when the list could not be read.

    `loginctl` is the systemd-native answer. `who` reads utmp and works
    regardless, so it is the fallback - but a `who` that fails is also None,
    not an empty list.
    """
    if shutil.which("loginctl") is not None:
        try:
            proc = subprocess.run(
                ["loginctl", "list-sessions", "--no-legend", "--no-pager"],
                capture_output=True, text=True, timeout=_TIMEOUT, check=False)
            if proc.returncode == 0:
                rows = []
                for line in proc.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].isdigit():
                        rows.append({
                            "session": parts[0],
                            "user": parts[1],
                            "seat": parts[2] if len(parts) > 2 else "",
                        })
                return rows
        except (subprocess.TimeoutExpired, OSError):
            return None

    if shutil.which("who") is not None:
        try:
            proc = subprocess.run(["who"], capture_output=True, text=True,
                                  timeout=_TIMEOUT, check=False)
            if proc.returncode == 0:
                rows = []
                for line in proc.stdout.splitlines():
                    parts = line.split()
                    if parts:
                        rows.append({"session": "-", "user": parts[0],
                                     "seat": parts[-1] if len(parts) > 1 else ""})
                return rows
        except (subprocess.TimeoutExpired, OSError):
            return None
    return None


def root_processes() -> Optional[List[dict]]:
    """Root-owned processes outside the init tree, with their parent.

    A root process whose parent is the init system, or whose name is part of the
    init system's own plumbing, is the machine running itself. What is left is
    something that was started.
    """
    try:
        pids = [e.name for e in _PROC.iterdir() if e.name.isdigit()]
    except OSError:
        return None

    out: List[dict] = []
    for pid in pids:
        stat = _read(_PROC / pid / "stat")
        if not stat:
            continue
        tail = stat.rsplit(")", 1)
        if len(tail) != 2:
            continue
        fields = tail[1].split()
        if len(fields) < 3:
            continue
        # After the comm, the fields are state, ppid, pgrp, ...
        if fields[0] != "S" and fields[0] != "R" and fields[0] != "D":
            continue
        ppid = fields[1]
        if not ppid.isdigit():
            continue

        status = _read(_PROC / pid / "status")
        if not status or "\nUid:\t0\t" not in status.replace("  ", "\t"):
            if not status or "Uid:" not in status:
                continue
            uid_line = [l for l in status.splitlines() if l.startswith("Uid:")]
            if not uid_line or uid_line[0].split()[1] != "0":
                continue

        comm = (_read(_PROC / pid / "comm") or "?").strip()
        if comm in _INIT_NAMES:
            continue
        if ppid == "1" and comm.startswith(("systemd", "dbus", "polkitd", "udisksd")):
            continue
        out.append({
            "pid": pid,
            "ppid": ppid,
            "user": "root",
            "command": (_read(_PROC / pid / "cmdline") or "").replace("\0", " ").strip()
                        or comm,
        })
    out.sort(key=lambda r: int(r["pid"]))
    return out


def _run(arguments: dict) -> Union[str, Percept]:
    if not _PROC.is_dir():
        return (
            "Sessions are UNKNOWN: /proc is not mounted, so neither who is "
            "logged in nor what is running as root could be determined. That is "
            "not the same as being the only user on the machine."
        )

    sessions = read_logged_in()
    roots = root_processes()

    if sessions is None:
        lines = ["Logged-in sessions: UNKNOWN - neither loginctl nor who could "
                 "be read, so who else is on this machine is not established."]
    elif not sessions:
        lines = ["No interactive session other than this one is listed."]
    else:
        lines = [f"{len(sessions)} session(s) listed:"]
        for row in sessions[:10]:
            seat = f" on {row['seat']}" if row["seat"] else ""
            lines.append(f"  {row['user']}{seat} (session {row['session']})")

    if roots is None:
        lines.append("Root processes: UNKNOWN - /proc could not be walked.")
    elif not roots:
        lines.append("Nothing is running as root outside the init system's own "
                     "service tree, with kernel threads excluded.")
    else:
        shown = roots[:_MAX_ROOT]
        lines.append(
            f"{len(roots)} process(es) running as root outside the init tree, "
            f"with kernel threads excluded. This is not a verdict - a package "
            f"update, a backup, or a polkit prompt someone is looking at right "
            f"now all look like this:"
        )
        for row in shown:
            cmd = row["command"][:110]
            lines.append(f"  pid {row['pid']} (parent {row['ppid']}): {cmd}")
        if len(roots) > len(shown):
            lines.append(f"  and {len(roots) - len(shown)} more")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc",
        metadata={
            "sessions_known": sessions is not None,
            "session_count": None if sessions is None else len(sessions),
            "session_users": None if sessions is None
                             else sorted({r["user"] for r in sessions}),
            "root_process_count": None if roots is None else len(roots),
        },
    )


_SENSE = Sense(
    name="sessions",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
