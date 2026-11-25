"""Skill: who joined or changed on this network in the last few seconds?

`scan_network` answers "what is on my network" by scanning, which is
perception of a neighbourhood and takes a while. `arpwatch` answers the
other question - "did anything *change*" - by listening: it reports new
IP/MAC pairs, hostname changes, flips and duplicates as they happen on one
interface. "Did a new device just join my network?" had no answer before.

**Unprivileged, and honest about it.** arpwatch opens the interface and
wants promiscuous access; Chronoa runs as the desktop user and will not ask
for a password to escalate. When it cannot open the interface, the skill
says that (and names the command to run by hand) rather than reporting an
empty network. When it can, it reports what arpwatch reported.

**Bounded.** arpwatch runs as a daemon by default; here it is a timed
capture (`-n` seconds) whose output is summarised like every other capture
in this package: count, then the rows, capped.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30
_DEFAULT_SECONDS = 10
_MAX_SECONDS = 60
_MAX_ROWS = 20


def _run(arguments: dict) -> str:
    iface = str(arguments.get("interface") or "").strip()
    if not iface:
        return ("I need the interface to watch (interface_counters lists "
                "them, e.g. 'wlp0s20f3').")
    seconds = arguments.get("seconds")
    if seconds is None:
        seconds = _DEFAULT_SECONDS
    if isinstance(seconds, bool) or not isinstance(seconds, int) \
            or not 1 <= seconds <= _MAX_SECONDS:
        return f"'seconds' must be a whole number between 1 and {_MAX_SECONDS}."

    if shutil.which("arpwatch") is None:
        return files.tool_missing("arpwatch", "watch a network for new devices")

    # `-n <seconds>`: run for that long and exit, rather than daemonising.
    # `-i <iface>`: one interface. arpwatch prints one line per change.
    try:
        proc = subprocess.run(
            ["arpwatch", "-i", iface, "-n", str(seconds)],
            capture_output=True, text=True, timeout=seconds + 10, check=False)
    except subprocess.TimeoutExpired:
        return (f"Watching {iface} did not finish within {seconds}s, so "
                f"nothing is reported.")
    except OSError as exc:
        return f"Not done: arpwatch could not be run ({exc})."

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        blob = " ".join(detail)
        if "permission" in blob.lower() or "Operation not permitted" in blob:
            return (
                f"Not done: arpwatch could not open {iface}. It said: {blob}. "
                f"Watching the link for changes needs permission this runs "
                f"without - run `arpwatch -i {iface}` yourself (with sudo) "
                f"if you want it."
            )
        return f"Not done: arpwatch said: " + (blob or f"exit {proc.returncode}")

    lines = [ln.strip() for ln in (proc.stdout or "").splitlines()
             if ln.strip() and not ln.startswith("Hostname")]
    if not lines:
        return (f"No new devices, flips or duplicates seen on {iface} within "
                f"{seconds}s. That is what arpwatch reported for that "
                f"window - a device that joined before the window, or one "
                f"that never arp-replies, would not appear here.")
    shown = lines[:_MAX_ROWS]
    out = [f"{len(lines)} change(s) on {iface} within {seconds}s:", ""]
    out.extend(shown)
    if len(lines) > len(shown):
        out.append(f"... {len(lines) - len(shown)} more change(s) not shown.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "network_watch",
        "description": (
            "Listen on one network interface for a few seconds and report "
            "what changed: new devices (IP/MAC pairs), hostname changes, "
            "flips and duplicates. Use for 'did a new device join my "
            "network', 'something changed on my network'. Read-only; it "
            "listens and reports, and changes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": ("The interface to watch, e.g. 'wlp0s20f3'. "
                                    "interface_counters lists them."),
                },
                "seconds": {
                    "type": "integer",
                    "description": (f"Watch for this many seconds (default "
                                    f"{_DEFAULT_SECONDS}, ceiling {_MAX_SECONDS})."),
                },
            },
            "required": ["interface"],
        },
    },
}

SKILLS = [Skill(name="network_watch", schema=SCHEMA, run=_run)]
