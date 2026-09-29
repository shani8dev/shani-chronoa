"""Skill: list the WiFi networks this machine can see.

Read-only. The `network` *sense* reports the interfaces this machine has; this
answers a different question - what is in range right now - which is what
someone means by "is the cafe wifi in here".

Deliberately not duplicated inside the sense. A sense is polled and deposited on
a timer whether or not anyone asked; scanning is a radio operation with a real
cost and a real privacy dimension, so it belongs behind an explicit request and
behind the same `network` consent the scan-network skill already uses.

Honesty rules: an absent radio and a failed scan are different sentences. A
scan that found nothing says so, and does not claim the machine has no WiFi
hardware.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 25
_MAX = 40

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_wifi_networks",
        "description": (
            "Scan for and list the WiFi networks in range, with signal strength "
            "and whether this machine is joined to one. Scans the radio, so it "
            "requires the 'network-sense-enabled' consent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "rescan": {
                    "type": "boolean",
                    "description": (
                        "Ask for a fresh scan rather than the cached list. "
                        "Slower, and needed to notice a network that has just "
                        "appeared. Defaults to false."
                    ),
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("network"):
        return (
            f"Refusing to scan for WiFi networks: {config.sense_allowed_reason('network')}. "
            f"Scanning puts this machine on the radio."
        )
    if shutil.which("nmcli") is None:
        return files.tool_missing("nmcli", "list WiFi networks")

    args = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
    if arguments.get("rescan"):
        args.insert(2, "--rescan", "yes")
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (
            f"nmcli did not answer within {_TIMEOUT}s. On a machine with no "
            f"wireless radio a scan can block rather than fail, so this is "
            f"reported as a timeout and not as 'no networks'."
        )
    except OSError as exc:
        return f"Could not list WiFi networks: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return f"nmcli could not list WiFi networks (exit {proc.returncode})" + (
            f": {detail[-1]}" if detail else ".")

    rows = []
    joined = None
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], ":".join(parts[3:])
        if not ssid:
            continue
        if in_use == "*":
            joined = ssid
        rows.append((ssid, signal, security))

    if not rows:
        return (
            "The scan completed and found no named networks. Hidden networks "
            "are not listed by name, and a machine with no working radio looks "
            "the same from here - so this is not proof the hardware is absent."
        )

    rows.sort(key=lambda r: int(r[1]) if r[1].isdigit() else -1, reverse=True)
    lines = [
        f"{len(rows)} network(s) in range"
        + (f"; joined to {joined!r}." if joined else "; not joined to any.")
    ]
    for ssid, signal, security in rows[:_MAX]:
        mark = "*" if ssid == joined else " "
        sec = security or "open"
        lines.append(f" {mark} {int(signal) if signal.isdigit() else 0:>3}%  {ssid[:40]:<40} {sec}")
    if len(rows) > _MAX:
        lines.append(f"   ... {len(rows) - _MAX} more not shown (limit {_MAX}).")
    lines.append("  * = joined.  Use connect_wifi to join one.")
    return "\n".join(lines)


SKILLS = [Skill(name="list_wifi_networks", schema=SCHEMA, run=_run)]
