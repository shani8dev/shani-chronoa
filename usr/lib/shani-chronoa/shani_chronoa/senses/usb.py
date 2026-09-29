"""Sense: what USB devices this machine has.

`devices` walks `/sys/bus/pci/devices`, which is the internal bus. Everything
plugged into a port - a mouse, a phone, a webcam, a USB stick - is on the USB
bus and appears in neither. This reads `/sys/bus/usb/devices` directly, so there
is no binary to be missing: `lsusb` is not required and is not used.

Reads the descriptors rather than a database, which is what makes the output
honest: a device with no `product` string genuinely has none, and that is
different from one whose product string could not be read.

Honesty rules:

- A device directory that cannot be read is skipped and counted. A USB device
  is a hot-plug target, so "it vanished while I was reading it" is a normal
  event, not an error - but it is still counted, so a shrinking count is
  visible rather than silent.
- Speed is reported as the link speed the device negotiated, not as a
  capability, and `5000M` is shown as 5.0 Gb/s because that is what it means.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

_USB_ROOT = Path("/sys/bus/usb/devices")
_MAX_DEVICES = 40

#: Interface class codes worth naming. The rest report as hex, which is honest
#: but not useful to read.
_CLASSES = {
    "09": "hub", "00": "peripheral", "02": "printer", "03": "keyboard",
    "08": "mass storage", "0e": "video", "0f": "audio", "10": "communications",
    "dc": "diagnostics", "e0": "wireless", "ef": "miscellaneous",
}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "usb",
        "description": (
            "List the USB devices attached to this machine, with their class, "
            "vendor and product, and the link speed they negotiated. Covers "
            "things plugged into ports, which the PCI inventory does not."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_devices() -> List[dict]:
    """Every device interface on the USB bus, in kernel order."""
    if not _USB_ROOT.is_dir():
        return []
    out: List[dict] = []
    for entry in sorted(_USB_ROOT.iterdir()):
        # usbN are devices; usbN/M are their interfaces. A device is the one
        # with an idVendor file - interfaces do not have one.
        if _read(entry / "idVendor") is None:
            continue
        vid = _read(entry / "idVendor")
        pid = _read(entry / "idProduct")
        cls = _read(entry / "bDeviceClass")
        speed = _read(entry / "speed")
        out.append({
            "node": entry.name,
            "vendor_id": vid,
            "product_id": pid,
            "class": cls,
            "class_name": _CLASSES.get(cls or "", "unknown" if cls is None else f"0x{cls}"),
            "manufacturer": _read(entry / "manufacturer"),
            "product": _read(entry / "product"),
            "serial": _read(entry / "serial"),
            "speed": speed,
            "max_child_ports": _read(entry / "max_child_ports"),
        })
    return out


#: The kernel reports a bare Mbit/s figure, not a unit - "480" and "12" are both
#: plausible-looking numbers that mean full-speed and high-speed respectively.
#: Printed raw they are unreadable, and a reader cannot tell 12 Mb/s from 12
#: Gb/s. These are the values the USB specification defines.
_SPEED_MBPS = {
    "1": "1.5 Mb/s (low-speed)",
    "12": "12 Mb/s (full-speed)",
    "480": "480 Mb/s (high-speed)",
    "5000": "5.0 Gb/s (super-speed)",
    "10000": "10.0 Gb/s (super-speed+)",
    "20000": "20.0 Gb/s (super-speed++)",
    "40000": "40.0 Gb/s (super-speed++ x2)",
}


def _human_speed(raw: Optional[str]) -> str:
    if not raw:
        return "speed unknown"
    if raw in _SPEED_MBPS:
        return _SPEED_MBPS[raw]
    if raw.endswith("M") and raw[:-1].isdigit():
        mbps = int(raw[:-1])
        return f"{mbps / 1000:.1f} Gb/s" if mbps >= 1000 else f"{mbps} Mb/s"
    return raw


def _run(arguments: dict) -> Union[str, Percept]:
    if not _USB_ROOT.is_dir():
        return (
            "USB device inventory is UNKNOWN: /sys/bus/usb/devices is not "
            "present, so this kernel exposes no USB bus to read. That is not "
            "the same as this machine having no USB devices."
        )

    devices = read_devices()
    if not devices:
        return (
            "No USB devices are attached, or none of them exposed readable "
            "descriptors. A machine with a USB controller and nothing plugged "
            "in looks like this from here, so this is not proof the hardware is "
            "missing."
        )

    lines = [f"{len(devices)} USB device interface(s) attached:"]
    for d in devices[:_MAX_DEVICES]:
        who = d["product"] or d["manufacturer"] or "device with no product string"
        ids = ""
        if d["vendor_id"] and d["product_id"]:
            ids = f" [{d['vendor_id']}:{d['product_id']}]"
        extra = ""
        if d["serial"]:
            extra += f"  serial {d['serial'][:24]}"
        lines.append(
            f"  {d['node']:<12} {d['class_name']:<16} {who[:38]:<38} "
            f"{_human_speed(d['speed']):<11}{ids}{extra}")
    if len(devices) > _MAX_DEVICES:
        lines.append(f"  ... {len(devices) - _MAX_DEVICES} more not shown.")

    unknown_speed = sum(1 for d in devices if not d["speed"])
    if unknown_speed:
        lines.append(
            f"  {unknown_speed} device(s) report no negotiated speed, which is "
            f"normal for a device that is not a high-speed link.")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-usb",
        metadata={
            "devices": len(devices),
            "classes": sorted({d["class_name"] for d in devices}),
            "without_speed": unknown_speed,
            "with_serial": sum(1 for d in devices if d["serial"]),
        },
    )


_SENSE = Sense(
    name="usb",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
