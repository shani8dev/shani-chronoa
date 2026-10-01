"""Skill: Bluetooth devices - list the paired ones and whether each is
connected, and connect or disconnect one by name ('connect my headphones').
bluetoothctl (bluez-utils, in every Shanios image); turning the adapter on
and off stays toggle_bluetooth's job."""

import re
import shutil
import subprocess

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bluetooth_devices",
        "description": "List paired Bluetooth devices (and which are connected), or connect / disconnect "
                       "one by its name, e.g. 'connect my headphones'.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "connect", "disconnect"]},
            "name": {"type": "string", "description": "Part of the device's name, for connect/disconnect."},
        }, "required": ["action"]},
    },
}


def _btctl(*args, timeout=20) -> str:
    r = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
    return r.stdout


def paired() -> list:
    out = _btctl("devices", "Paired") or _btctl("paired-devices")
    devs = re.findall(r"Device ([0-9A-F:]{17}) (.+)", out)
    return [(mac, name.strip(), "Connected: yes" in _btctl("info", mac, timeout=10)) for mac, name in devs]


def _run(arguments: dict) -> str:
    if not shutil.which("bluetoothctl"):
        return "bluetoothctl (bluez-utils) is not installed."
    try:
        devs = paired()
    except (OSError, subprocess.TimeoutExpired):
        return "Bluetooth did not answer - is the adapter on?"
    action = arguments.get("action")
    if action == "list":
        if not devs:
            return "No Bluetooth devices are paired. Pair one in Settings first."
        return "Paired: " + "; ".join(f"{n} ({'connected' if c else 'not connected'})" for _m, n, c in devs) + "."
    want = (arguments.get("name") or "").strip().lower()
    match = [d for d in devs if want and want in d[1].lower()] or (devs if len(devs) == 1 and not want else [])
    if not match:
        return f"No paired device matches '{want}'. Paired: {', '.join(n for _m, n, _c in devs) or 'none'}."
    if len(match) > 1:
        return f"Which one: {', '.join(n for _m, n, _c in match)}?"
    mac, name, connected = match[0]
    if action == "connect" and connected:
        return f"{name} is already connected."
    if action == "disconnect" and not connected:
        return f"{name} is not connected."
    out = _btctl(action, mac, timeout=30)
    ok = "successful" in out.lower()
    return f"{'Connected' if action == 'connect' else 'Disconnected'} {name}." if ok else \
        f"Could not {action} {name}: {(out.strip().splitlines() or ['no answer'])[-1][:150]}"


SKILLS = [Skill(name="bluetooth_devices", schema=_SCHEMA, run=_run)]
