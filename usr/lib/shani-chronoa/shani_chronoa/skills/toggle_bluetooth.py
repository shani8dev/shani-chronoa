"""Skill: turn the Bluetooth adapter on or off, and report paired devices.

The `bluetooth` sense reads the adapter and reports paired devices. Nothing could
*change* it. That is the largest gap in the actuator set: the machine can see
Bluetooth and cannot do the one thing a person does with Bluetooth, which is turn
it off when it is draining the battery or when the room is full of other people's
devices.

Gated, and separately from the sense's own consent key. Reading which radios are
present and switching one off are different permissions: a user happy for the
assistant to notice a headset has not agreed to have that headset disconnected
mid-call. Splitting them means each can be granted on its own terms.

Refuses to fall back to writing sysfs or calling rfkill directly. Those are
exactly the paths that need privilege, and doing it that way would take the
decision away from the desktop's own authorisation rules.

Honesty rules:

- **Adapter state is read back after the change**, because a command that exits 0
  and a radio that is actually off are different claims.
- "Off" is verified from the adapter's own reported state, not from the absence
  of a device list, which is also what an empty room looks like.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "bluetooth-control-enabled"
_TIMEOUT = 20

SCHEMA = {
    "type": "function",
    "function": {
        "name": "toggle_bluetooth",
        "description": (
            "Turn the Bluetooth adapter on or off, or report its state and the "
            "paired devices. Requires the 'bluetooth-control-enabled' consent "
            "key; reporting needs no such permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'on', 'off' or 'status'. Defaults to status.",
                }
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing the Bluetooth adapter is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting the adapter state and "
            f"paired devices needs no such permission - only switching it does."
        )
    return True, ""


def _bt(*args: str):
    if shutil.which("bluetoothctl") is None:
        return None
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _adapter() -> str:
    proc = _bt("show")
    return "" if proc is None else (proc.stdout or "")


def _powered(block: str) -> str:
    """'yes', 'no', or 'unknown' from a bluetoothctl show block."""
    for line in block.splitlines():
        if line.strip().startswith("Powered:"):
            return line.split(":", 1)[1].strip().lower()
    return "unknown"


def _paired() -> str:
    proc = _bt("devices", "Paired")
    return "" if proc is None else (proc.stdout or "")


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("on", "off", "status"):
        return f"Action must be on, off or status, not {action!r}."

    if _bt is None or shutil.which("bluetoothctl") is None:
        return ("bluetoothctl is not installed, so the adapter cannot be "
                "queried or changed. On Arch it comes from bluez. No fallback "
                "to sysfs or rfkill is attempted: those are the paths that need "
                "privilege, and using them would bypass the desktop's own "
                "authorisation rules.")

    before = _adapter()
    powered = _powered(before)
    paired = [ln.strip() for ln in _paired().splitlines() if ln.strip()]

    if action == "status":
        lines = [f"Bluetooth adapter power: {powered}"]
        if powered == "unknown":
            lines.append("The adapter's own state could not be read from "
                         "bluetoothctl, so this is UNKNOWN rather than off.")
        lines.append(f"{len(paired)} paired device(s):")
        shown, withheld = files.cap_list(paired, 20)
        lines += [f"  {d}" for d in shown] or ["  none reported"]
        note = files.withheld_note("device", withheld)
        if note:
            lines.append(note)
        lines.append("Use the 'bluetooth' sense for adapter and device detail.")
        return "\n".join(lines)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the Bluetooth adapter: {reason}"

    target = "on" if action == "on" else "off"
    if powered == target:
        return (f"The adapter is already {target}, so nothing was changed."
                + (f" {len(paired)} device(s) remain paired." if target == "off"
                   else ""))

    proc = _bt("power", target)
    if proc is None:
        return f"The adapter did not answer within {_TIMEOUT}s, so whether it is now {target} is UNKNOWN."

    # Read the state back rather than trusting the exit code.
    after = _powered(_adapter())
    if after == target:
        return f"Bluetooth adapter is now {target}." + (
            f" {len(paired)} device(s) are still paired and will reconnect when it comes back on."
            if target == "on" else "")
    if after == "unknown":
        return (f"bluetoothctl accepted the request to turn the adapter {target} "
                f"(exit {proc.returncode}), but reading the state back did not "
                f"work, so this is not verified.")
    return (f"Asked bluetoothctl to turn the adapter {target}, but it still "
            f"reports {after}. Nothing was silently assumed.")


SKILLS = [Skill(name="toggle_bluetooth", schema=SCHEMA, run=_run)]
