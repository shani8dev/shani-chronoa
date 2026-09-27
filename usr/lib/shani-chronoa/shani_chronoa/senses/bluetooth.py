"""Bluetooth sense: which adapters exist and what the controller can see.

Two real sources: `/sys/class/bluetooth/` for the adapters this machine has,
and `bluetoothctl --timeout` for the device addresses and names the local
controller reports.

**This cannot tell you whether anything is in the room, and must not be
presented as if it could.** Bluetooth is a short-range radio with no ranging
data in its normal operation: the controller reports that a device is
*reachable*, which for a class-2 radio is a coarse ballpark and for a
class-1 device is still tens of metres. There is no distance, no direction,
no presence history and no occupancy. WiFi and Bluetooth "room sensing" in
the marketing sense comes from CSI/radiometric analysis, which is a different
and considerably more invasive technique that this does not perform.

So the honest, useful version is inventory: which adapters are present, which
are powered and blocked by rfkill, and which devices the controller currently
lists. That is genuinely useful for debugging a headset and is not a
surveillance capability.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
# Device names and MAC addresses identify hardware the user owns and chose to
# pair, so this is more than public metadata.
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_ADAPTERS = "/sys/class/bluetooth"
_TIMEOUT = 8.0

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bluetooth",
        "description": (
            "List this machine's Bluetooth adapters - present, powered, and "
            "whether rfkill has them blocked - and the device addresses and "
            "names the local controller reports. Reachability only: this "
            "cannot measure distance, direction, or whether anything is in the "
            "room, because ordinary Bluetooth carries no ranging data."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def read_adapters() -> list:
    out = []
    try:
        entries = sorted(Path(_ADAPTERS).iterdir())
    except OSError:
        return out
    for entry in entries:
        try:
            blocked = "1" in (entry / "rfkill" / "soft").read_text()
        except OSError:
            blocked = None
        out.append({"adapter": entry.name, "rfkill_blocked": blocked})
    return out


def read_devices() -> list:
    """Device addresses the controller lists, via bluetoothctl.

    Returns [] rather than raising when bluetoothctl is absent or the daemon
    is down: a machine with the radio disabled is a normal state, and
    reporting "no Bluetooth" is the right answer for it.
    """
    if shutil.which("bluetoothctl") is None:
        return []
    try:
        proc = subprocess.run(
            ["bluetoothctl", "--timeout", str(int(_TIMEOUT)), "devices"],
            capture_output=True, text=True, timeout=_TIMEOUT + 5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("bluetoothctl failed: %s", exc)
        return []
    devices = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("Device") or line == "No devices found":
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and _is_mac(parts[0]):
            devices.append({"address": parts[0], "name": parts[1]})
    return devices


def _is_mac(token: str) -> bool:
    return len(token) == 17 and token.count(":") == 5 and all(
        c in "0123456789ABCDEFabcdef" for c in token
    )


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("bluetooth"):
        return f"Not reading Bluetooth state: {config.sense_allowed_reason('bluetooth')}."

    adapters = read_adapters()
    if not adapters:
        return "This machine has no Bluetooth adapter, or Bluetooth is compiled out."

    devices = read_devices()
    lines = [f"adapters: {len(adapters)}"]
    for a in adapters:
        state = "rfkill-blocked" if a["rfkill_blocked"] else "unblocked"
        lines.append(f"  {a['adapter']} ({state})")
    lines.append(f"devices seen by the controller: {len(devices)}")
    for d in devices:
        lines.append(f"  {d['address']} {d['name']}")
    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-bluetooth",
        metadata={"adapters": len(adapters), "devices": len(devices)},
    )


_SENSE = Sense(
    name="bluetooth",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
