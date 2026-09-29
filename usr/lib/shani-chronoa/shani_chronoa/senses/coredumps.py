"""Sense: programs that crashed, as opposed to programs that complained.

`faults` reads what the journal logged. A segfaulting application is not
necessarily in there at all: a process killed by SIGSEGV becomes a *core dump*,
and whether one is recorded at all depends on a setting most distributions ship
switched off. On Debian-family systems `apport` handles crashes and systemd never
sees them; on Arch, `systemd-coredump` has to be enabled before anything is kept.

That is the whole reason this is a separate sense. The question "did any of my
apps crash today" has three genuinely different answers - yes, no, and *crashes
are not being recorded here* - and the third is the one a naive implementation
reports as the second. A machine that has never once recorded a core dump is
indistinguishable from a machine where nothing has ever crashed, unless the sense
is willing to say which.

Honesty rules:

- **Crash recording being off is reported as UNKNOWN, never as "no crashes."**
  This is the single most important line in the module.
- Counts are of *recorded* crashes, always labelled that way.
- A dump too large to read is still a crash and is still counted; its size is
  reported rather than used to decide whether the crash happened.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import List, Optional, Union

from shani_chronoa import files
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0
_TIMEOUT = 25
_WINDOW_HOURS = 168
_MAX_LISTED = 12

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "coredumps",
        "description": (
            "Report programs that crashed recently, and state whether crash "
            "recording is even enabled - a machine that records nothing is not "
            "a machine where nothing crashed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _corectl(*args: str):
    if shutil.which("coredumpctl") is None:
        return None
    try:
        return subprocess.run(["coredumpctl", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def read_crashes(since: str) -> Optional[List[dict]]:
    """Recorded crashes since `since`, or None when they could not be read."""
    proc = _corectl("list", "--no-pager", "--since", since)
    if proc is None or proc.returncode != 0:
        return None
    if "No coredumps found" in proc.stdout or "No core dumps found" in proc.stdout:
        return []
    rows: List[dict] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        # "Mon 2026-09-29 14:31:02 IST  host  gnome-shell[1234]: ..."
        if not parts or "[" not in line:
            continue
        head = line.split("[", 1)[0].split()
        when = " ".join(head[1:4]) if len(head) >= 4 else line[:24]
        name = line.split("[", 1)[0].strip().rstrip(":").split()[-1]
        rows.append({"process": name, "when": when})
    return rows


def coredump_service() -> Optional[bool]:
    """Whether systemd-coredump is actually storing dumps, or None if unknown.

    `coredumpctl` exits 0 with a diagnostic rather than an error when the
    service is masked, which is the case that matters here and the one a bare
    return-code check would score as a successful empty result.
    """
    proc = _corectl("list")
    if proc is None:
        return None
    blob = (proc.stdout + proc.stderr).lower()
    if "no coredumps found" in blob or "no core dumps found" in blob:
        return True  # the tool works, there simply are none
    if "disabled" in blob or "masked" in blob or "not stored" in blob:
        return False
    if "stored" in blob or "coredump" in blob:
        return True
    return None


def _run(arguments: dict) -> Union[str, Percept]:
    if shutil.which("coredumpctl") is None:
        return (
            "Crashes are UNKNOWN: coredumpctl is not installed, so no crash "
            "records could be read. That is not the same as a machine where "
            "nothing has crashed - on Debian-family systems apport handles "
            "crashes instead and systemd never sees them."
        )

    recording = coredump_service()
    crashes = read_crashes(f"-{_WINDOW_HOURS}h")

    if recording is False:
        return (
            "Crashes are UNKNOWN: systemd-coredump is present but not storing "
            "dumps, so a crash could have happened and left no record. Treat "
            "this as 'cannot tell', not as 'nothing crashed'. On Arch it is "
            "enabled with `systemctl enable systemd-coredump`."
        )

    if crashes is None:
        return (
            f"Crashes are UNKNOWN: coredumpctl is installed but the crash "
            f"records could not be read for the last {_WINDOW_HOURS}h."
        )

    if not crashes:
        verdict = (f"No crashes were recorded in the last {_WINDOW_HOURS} "
                   f"(7 days). That means no crash produced a dump, not that "
                   f"nothing went wrong - a process that hangs, is killed, or "
                   f"exits badly without a signal leaves no core at all.")
    else:
        shown = crashes[:_MAX_LISTED]
        verdict = (f"{len(crashes)} recorded crash(es) in the last "
                   f"{_WINDOW_HOURS}h (7 days):")
        verdict += "\n" + "\n".join(f"  {c['process']}  ({c['when']})" for c in shown)
        if len(crashes) > len(shown):
            verdict += f"\n  and {len(crashes) - len(shown)} more"
        total_bytes = sum(c.get("bytes", 0) for c in crashes)
        if total_bytes:
            verdict += f"\nDumps total {files.human_size(total_bytes)} on disk."

    return _SENSE.to_percept(
        verdict,
        source="coredumpctl",
        metadata={
            "window_hours": _WINDOW_HOURS,
            "recording_enabled": recording,
            "crash_count": len(crashes),
            "processes": sorted({c["process"] for c in crashes})[:_MAX_LISTED],
        },
    )


_SENSE = Sense(
    name="coredumps",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
