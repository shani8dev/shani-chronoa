"""Sense: what has recently gone wrong, at the level the system complains about.

"This worked yesterday" is the most common question anyone asks a machine, and
nothing in this project could answer it. `services` reports units systemd has
already declared `failed`; that is systemd's opinion about units it manages, and
it says nothing about a kernel warning, a failed mount, an OOM kill, a
segfaulting application or a failing disk write.

This is the log's own severity filter rather than a judgement. `journalctl`
already decides what counts as an error, and re-deciding that here would mean
inventing a threshold. What this sense adds over reading the log by hand is the
part that is easy to get wrong: **an empty error list from a machine whose
journal could not be read is not a healthy machine.** A container without
`journalctl`, a user without read access to the system journal, and a machine
that was up for four minutes all produce "no errors", and two of those three are
mislabelling a machine that cannot answer.

Honesty rules:

- **No journal is UNKNOWN, never "nothing has gone wrong."** The distinction is
  the whole point of this sense.
- An empty result states how far back the journal actually reaches, because
  "no errors in the last 24 hours" on a machine whose journal starts two minutes
  ago is a much smaller claim than it sounds.
- The count is of journal *lines*, deduplicated by unit, and is not presented as
  a count of problems - one service retrying in a loop is one unit and can be
  thousands of lines.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import List, Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0
_TIMEOUT = 20

_WINDOW_HOURS = 24
_MAX_UNITS = 12
_MAX_LINES = 3

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "faults",
        "description": (
            "Report errors and warnings the system has logged recently, "
            "deduplicated by the service that emitted them, and state how far "
            "back the log actually reaches."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _journal(*args: str) -> Optional[subprocess.CompletedProcess]:
    if shutil.which("journalctl") is None:
        return None
    try:
        return subprocess.run(["journalctl", "--no-pager", *args],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def read_errors(since: str) -> Optional[List[dict]]:
    """Lines at error priority or worse since `since`, grouped by unit.

    None means the journal could not be read at all, which is not the same as
    an empty list.
    """
    proc = _journal("--since", since, "-p", "err", "--output", "short-iso")
    if proc is None:
        return None
    # journalctl exits non-zero when it cannot open the journal at all (no
    # persistent storage, no permission). An empty successful read is exit 0
    # with no output, and that is the only thing that means "no errors".
    if proc.returncode != 0 and not proc.stdout.strip():
        return None

    by_unit: dict = {}
    for line in proc.stdout.splitlines():
        if not line.strip() or line.startswith("--"):
            continue
        by_unit.setdefault(_attributable(line), []).append(line)
    return by_unit


#: A systemd unit name, as it appears in the message systemd logs about it.
_UNIT_RE = re.compile(
    r"\b([A-Za-z0-9_.@-]+\.(?:service|socket|target|device|mount|timer|path|scope|slice))\b")


def _attributable(line: str) -> str:
    """What to group this line under.

    Grouping by the emitting pid is the obvious move and it is wrong: every unit
    systemd itself starts is logged as `systemd[1]`, so a service failing every
    few minutes and a kernel warning both land in one bucket labelled "1", and
    the reported unit count is a count of pids rather than of things that broke.
    On the dev machine that turned 29 error lines into a claimed "16 unit(s)".

    So the unit named in the message wins, because that is the thing that
    actually failed, and the pid is only the fallback.
    """
    named = _UNIT_RE.search(line)
    if named:
        return named.group(1)
    if "[" in line and "]" in line:
        return line.split("[", 1)[1].split("]", 1)[0]
    parts = line.split()
    if len(parts) >= 3:
        return parts[2]
    return "unattributed"


def journal_span() -> Optional[str]:
    """When the journal's earliest entry is, or None if it cannot be read."""
    proc = _journal("--no-pager", "-n", "1", "--reverse", "--output", "short-iso")
    if proc is None or proc.returncode != 0:
        return None
    first = proc.stdout.strip().splitlines()
    if not first:
        return None
    return first[0].split(" ", 1)[0] if " " in first[0] else first[0]


def _run(arguments: dict) -> Union[str, Percept]:
    if shutil.which("journalctl") is None:
        return (
            "Recent faults are UNKNOWN: journalctl is not installed, so nothing "
            "could be asked. That is not the same as a machine with a clean log."
        )

    since = f"-{_WINDOW_HOURS}h"
    errors = read_errors(since)
    if errors is None:
        return (
            f"Recent faults are UNKNOWN: the system journal could not be read in "
            f"the last {_WINDOW_HOURS} hours, so no count is given. A container "
            f"without a persistent journal, a user without access to the system "
            f"journal, and a machine that was only just booted all look exactly "
            f"like this, and none of them mean a clean log."
        )

    span = journal_span()
    lines: List[str] = []
    if errors:
        total = sum(len(v) for v in errors.values())
        units = sorted(errors.items(), key=lambda kv: -len(kv[1]))
        lines.append(
            f"{total} error-or-worse line(s) in the last {_WINDOW_HOURS}h, "
            f"attributable to {len(errors)} unit(s). The line count is not a "
            f"problem count: one service retrying in a loop is one unit and can "
            f"be most of them."
        )
        for unit, msgs in units[:_MAX_UNITS]:
            lines.append(f"  {unit} ({len(msgs)} line(s))")
            for msg in msgs[-_MAX_LINES:]:
                lines.append(f"      {msg[:160]}")
    else:
        lines.append(f"No errors at or above priority 'err' in the last {_WINDOW_HOURS}h.")

    if span:
        lines.append(f"Journal reaches back to {span}.")
    else:
        lines.append(
            "The journal's own start could not be read, so how far back this "
            "actually reaches is UNKNOWN - on a machine that booted recently, "
            "'no errors' is a much smaller claim than it sounds."
        )
    lines.append("Use the 'services' sense for units systemd has marked failed.")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="journal",
        metadata={
            "window_hours": _WINDOW_HOURS,
            "error_units": sorted(errors.keys())[:_MAX_UNITS],
            "error_unit_count": len(errors),
            "error_line_count": sum(len(v) for v in errors.values()),
            "journal_start": span,
        },
    )


_SENSE = Sense(
    name="faults",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
