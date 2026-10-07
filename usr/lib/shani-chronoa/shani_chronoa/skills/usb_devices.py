"""Skill: what is plugged in, and is my Thunderbolt dock authorised?

The `usb` sense reads `/sys/bus/usb/devices` (no `lsusb` needed). Thunderbolt
and USB4 docks are the case it cannot explain: the kernel holds them
*unauthorised* until `boltd` says otherwise, so a dock can be physically
connected, listed on the bus, and do nothing at all. `boltctl list` is what
says which state each one is in, and nothing in Chronoa called it.

The USB half follows the `usb` sense's switch (on by default) - see
`sense_reading.py`; the Thunderbolt half needs none.

Honesty rules: no `boltctl` is reported as not installed rather than as "no
Thunderbolt devices"; `boltctl` with no daemon running fails, and that is
reported as unreadable.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 15

SCHEMA = {
    "type": "function",
    "function": {
        "name": "usb_devices",
        "description": (
            "What is plugged into this machine's USB ports (mice, phones, "
            "webcams, sticks, with their speed), and every Thunderbolt/USB4 "
            "device with whether it is authorised to work. Use for 'what's "
            "plugged in', 'why doesn't my dock work', 'is my webcam connected'. "
            "The USB list uses the 'usb-sense-enabled' switch. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the usb sense's switch (see sense_reading.py)."""
    if config.sense_allowed("usb"):
        return True, ""
    return False, sense_reading.refusal(config, "usb")


def parse_boltctl(stdout: str) -> "list[dict]":
    """Device blocks of `boltctl list`: a '●' title line, then '├─ key: value'."""
    devices, current = [], None
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith(("●", "○")):
            current = {"title": line[1:].strip()}
            devices.append(current)
            continue
        if current is None:
            continue
        line = line.lstrip("├└│─ ")
        if ":" in line:
            key, _, value = line.partition(":")
            current.setdefault(key.strip(), value.strip())
    return devices


def thunderbolt_lines() -> "list[str]":
    if shutil.which("boltctl") is None:
        return ["boltctl (the 'bolt' package) is not installed, so Thunderbolt "
                "authorisation is UNKNOWN."]
    try:
        proc = subprocess.run(["boltctl", "list"], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"boltctl could not be run ({exc}), so Thunderbolt state is UNKNOWN."]
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return ["boltctl failed" + (f": {detail[-1]}" if detail else "")
                + " (is boltd running?), so Thunderbolt state is UNKNOWN."]
    devices = parse_boltctl(proc.stdout)
    if not devices:
        return ["No Thunderbolt or USB4 devices are connected or remembered."]
    out = [f"{len(devices)} Thunderbolt/USB4 device(s):"]
    for d in devices:
        status = d.get("status", "unknown")
        note = " - connected but NOT authorised, so it will not work yet" if status == "connected" else ""
        out.append(f"  {d['title']}: {status}{note}")
    return out


def _run(_arguments: dict) -> str:
    lines = ["USB:"]
    config = ChronoaConfig()
    allowed, why = _consent(config)
    lines.append(sense_reading.reading("usb") if allowed else why)
    lines.append("")
    lines.append("Thunderbolt:")
    lines.extend(thunderbolt_lines())
    return "\n".join(lines)


SKILLS = [Skill(name="usb_devices", schema=SCHEMA, run=_run)]
