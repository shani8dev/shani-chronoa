"""Sense: is the clock trustworthy.

Every reminder, every timer, every log timestamp and every certificate check in
this project is downstream of the system clock. If the clock is wrong or
unsynchronised, `add_reminder`'s "tomorrow 9am" lands on the wrong day and
`set_timer` fires early or late, with nothing in either reply saying so. Nothing
in the project checked.

`get_datetime` (a skill) reports *what time it is*. This reports *whether that
time can be trusted*, which is a different question and the one that matters when
something has already gone wrong.

Honesty rules:

- **No `timedatectl` is UNKNOWN, not "synchronised".** Absence of the tool is
  not evidence of a correct clock.
- A sync state that cannot be read is reported as unknown rather than assumed
  good, because an assumed-good clock is exactly the failure this sense exists
  to catch.
- The drift figure is only shown when the tool reports one; it is never
  estimated from a single reading, which would be meaningless.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 900.0
_POLL_INTERVAL = 900.0
_TIMEOUT = 10

#: Above this, NTP is nominally synchronised but the offset is large enough that
#: a reminder could land in the wrong minute.
_DRIFT_WARN = 1.0

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "timebase",
        "description": (
            "Report whether the system clock is trustworthy: time-zone, "
            "whether it is synchronised to a network time source, and the "
            "current drift if one is reported. Reports UNKNOWN when that cannot "
            "be determined rather than assuming the clock is right."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _timedatectl(*fields: str) -> "Optional[dict]":
    if shutil.which("timedatectl") is None:
        return None
    try:
        proc = subprocess.run(
            ["timedatectl", "show", *fields],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("timedatectl failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    out: dict = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out or None


def _run(arguments: dict) -> Union[str, Percept]:
    data = _timedatectl("--property=Timezone", "--property=NTPSynchronized",
                        "--property=NTP", "--property=LocalRTC")
    if data is None:
        return (
            "Clock trustworthiness is UNKNOWN: timedatectl is not available "
            "here, so synchronisation was not checked. This is not the same as "
            "the clock being correct - reminders and timers depend on it."
        )

    lines = []
    timezone = data.get("Timezone") or "unknown"
    lines.append(f"time zone: {timezone}")

    raw_sync = data.get("NTPSynchronized")
    if raw_sync is None:
        lines.append(
            "time synchronisation: UNKNOWN - timedatectl did not report it, "
            "so the clock is neither confirmed good nor suspected bad")
        synced = None
    elif raw_sync.lower() in ("yes", "true"):
        lines.append("time synchronisation: yes, synchronised to a network time source")
        synced = True
    else:
        ntp = data.get("NTP") or "no service named"
        lines.append(
            f"time synchronisation: NO - not synchronised ({ntp}). Reminders "
            f"and timers can drift, and a 'tomorrow 9am' reminder is only as "
            f"reliable as this line.")
        synced = False

    drift = None
    if synced:
        # A single reading cannot measure drift, so this is not estimated. The
        # sync state above is the real signal; a fabricated figure here would be
        # worse than none.
        lines.append(
            "drift: not measured - drift needs two readings over time, and a "
            "figure from one would be a guess")

    if data.get("LocalRTC", "").lower() == "yes":
        lines.append(
            "note: the hardware clock is being treated as local time, which "
            "disagrees with UTC-based systems and is a common cause of "
            "timezone bugs after travel")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="timedatectl",
        metadata={
            "timezone": timezone,
            "synchronised": synced,
            "drift_seconds": drift,
            "ntp": data.get("NTP"),
        },
    )


_SENSE = Sense(
    name="timebase",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
