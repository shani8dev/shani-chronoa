"""Sense: which system services are unhealthy.

Nothing in the project looked at systemd's own view of the machine. That is a
real gap rather than a nicety: a unit in `failed` state is the single most
common cause of "this worked yesterday" - a stopped service, a failed mount, a
timer that has not run since an upgrade - and it is invisible unless asked for.

Deliberately *not* a health verdict. A unit being `inactive` is usually correct
(most units on a machine are not supposed to be running), so "inactive" is never
reported as a problem. Only `failed` is, because systemd has already decided
that state is wrong.

Honesty rules:

- **No systemd is UNKNOWN, not "no services are failing."** This runs on
  machines that are not systemd-managed, and "0 failed" from a machine where
  the question could not be asked is a lie.
- A unit list that could not be read is reported as unreadable, never as an
  empty list of failures.
- Failed units are named with their last few log lines, because a unit name on
  its own does not say why.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import List, Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0
_TIMEOUT = 15
_MAX_FAILED = 15

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "services",
        "description": (
            "Report system services that systemd has marked as failed, with the "
            "last few log lines from each, plus counts of what is running. "
            "Reports UNKNOWN when the machine is not systemd-managed rather "
            "than claiming nothing is wrong."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _systemctl(*args: str) -> "Optional[subprocess.CompletedProcess[str]]":
    if shutil.which("systemctl") is None:
        return None
    try:
        return subprocess.run(["systemctl", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("systemctl %s failed: %s", args, exc)
        return None


def read_failed() -> "Optional[List[str]]":
    """Failed unit names, or None when systemd could not be asked at all."""
    proc = _systemctl("list-units", "--state=failed", "--no-legend",
                      "--no-pager", "--plain")
    if proc is None:
        return None
    if proc.returncode != 0:
        # 1 here means "no units matched" in some systemd versions, but a
        # non-zero with stderr text is a real failure to ask.
        if not (proc.stderr or "").strip():
            return []
        return None
    units = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        units.append(line.split()[0])
    return units


def read_counts() -> "Optional[dict]":
    proc = _systemctl("list-units", "--type=service", "--no-legend",
                      "--no-pager", "--plain", "--all")
    if proc is None or proc.returncode != 0:
        return None
    counts: dict = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        state = parts[2]
        counts[state] = counts.get(state, 0) + 1
    return counts


def last_log_lines(unit: str, lines: int = 4) -> "List[str]":
    proc = _systemctl("status", unit, "--no-pager", "--lines", str(lines))
    if proc is None:
        return []
    text = (proc.stdout or proc.stderr or "")
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("●") and len(line) < 4:
            continue
        if "loaded; " in line or "Active:" in line or "Main PID" in line:
            continue
        out.append(line[:140])
    return out[-lines:]


def _run(arguments: dict) -> Union[str, Percept]:
    if shutil.which("systemctl") is None:
        return (
            "Service health is UNKNOWN: systemctl is not installed, so this "
            "machine is not systemd-managed. That is not the same as 'no "
            "services are failing' - the question could not be asked."
        )

    failed = read_failed()
    if failed is None:
        return (
            "Service health is UNKNOWN: systemd did not answer. A machine with "
            "nothing wrong and a machine that would not report are reported "
            "identically here on purpose, because they are not "
            "distinguishable from outside."
        )

    counts = read_counts() or {}
    if not failed:
        running = counts.get("running", 0)
        return _SENSE.to_percept(
            "No service is in a failed state."
            + (f" {running} service unit(s) are running." if running else ""),
            source="systemd",
            metadata={"failed": [], "counts": counts, "known": True},
        )

    lines = [f"{len(failed)} service(s) have failed:"]
    shown = failed[:_MAX_FAILED]
    for unit in shown:
        lines.append(f"  {unit}")
        entries = last_log_lines(unit)   # once: this shells out to systemctl
        for entry in entries:
            lines.append(f"      {entry}")
        if not entries:
            lines.append("      (no log lines could be read for this unit)")
    if len(failed) > len(shown):
        lines.append(f"  ... {len(failed) - len(shown)} more not shown.")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="systemd",
        metadata={"failed": failed, "counts": counts, "known": True},
    )


_SENSE = Sense(
    name="services",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
