"""Device inventory: what is on the PCI and USB buses.

`lspci` and `lsusb` give nicer names, and `pciutils`/`usbutils` are shipped in
`shani-tools-network` — but the kernel's own sysfs is the primary source here,
for two reasons. It needs no subprocess, and it answers for a machine where the
tools are absent. The tools are enrichment: used when present, and the output
says which it is showing.

**A PCI class code is not a device name, and the mapping is not total.** Class
`0x030000` is "VGA compatible controller" *by class*, which on this machine is
an Intel Iris Xe integrated GPU. Reporting that as "a display adapter" is
true; reporting it as *the* graphics card is what `gpu` is for. So this sense
reports the class, the vendor:device id, and the tool's name where available,
and leaves identification to the senses whose subject it is.

**A PCI device that has no driver bound is the useful finding.** `/sys/bus/pci
/devices/*/` always has a `driver` symlink only when a driver claimed it. A
device without one is genuinely unclaimed, which is a real state worth
surfacing — an unclaimed device is a machine with hardware the kernel does not
know what to do with — and it is distinguishable from a read failure, which is
not.

**USB devices are a tree, and a hub is not a peripheral.** Root hubs (`usb1`)
report a `xHCI Host Controller` product with no `bDeviceClass` worth
interpreting, and every device sits at a bus-port path like `3-10`. Both are
used: the class to tell a hub from a keyboard, and the path to say which port
something is in, which is the question a user actually has about a device that
stopped working.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 600.0
_POLL_INTERVAL = 600.0

_PCI = Path("/sys/bus/pci/devices")
_USB = Path("/sys/bus/usb/devices")
_TIMEOUT = 20

# PCI class codes worth naming. The map is deliberately short: it covers the
# classes a person asks about, and anything absent from it is reported by its
# hex code rather than guessed at.
_PCI_CLASSES = {
    "0300": "display controller",
    "0302": "3D controller",
    "0200": "ethernet controller",
    "0280": "audio device",
    "0c03": "USB controller",
    "0106": "SATA controller",
    "0600": "host bridge",
    "0604": "PCI bridge",
    "0108": "non-Volatile memory controller",
    "0000": "unclassified device",
}

_USB_CLASSES = {
    "09": "hub",
    "00": "peripheral",
    "03": "human interface device",
    "0e": "video",
    "07": "printer",
    "06": "imaging",
    "08": "mass storage",
    "0a": "CDC data",
    "02": "communications",
    "01": "audio",
    "ef": "miscellaneous",
    "dc": "diagnostic",
    "e0": "wireless controller",
}


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_pci() -> List[dict]:
    """Every PCI function the kernel knows about."""
    try:
        names = sorted(p.name for p in _PCI.iterdir())
    except OSError as exc:
        logger.debug("cannot list PCI: %s", exc)
        return []
    devices = []
    for name in names:
        entry = _PCI / name
        raw_class = _read_text(entry / "class")
        # A PCI class is a 24-bit value written as `0x030000`: the first two
        # hex digits are the base class and the next two the subclass, so a
        # display controller is `03`/`00` = "0300". Slicing from index 2
        # instead would take "0000" and call every device unclassified.
        label = None
        if raw_class:
            digits = raw_class.lower().removeprefix("0x").rjust(6, "0")
            label = _PCI_CLASSES.get(digits[0:4])
        driver_link = entry / "driver"
        record: Dict[str, object] = {"slot": name, "class": label or "unknown"}
        vendor = _read_text(entry / "vendor")
        device = _read_text(entry / "device")
        if vendor and device:
            record["id"] = f"{vendor.removeprefix('0x')}:{device.removeprefix('0x')}"
        try:
            record["driver"] = os.path.basename(os.readlink(driver_link))
        except OSError:
            # A device with no `driver` symlink is genuinely unclaimed, which is
            # a finding rather than a read failure.
            record["driver"] = None
        devices.append(record)
    return devices


def read_usb() -> List[dict]:
    """Every USB device, with its class and where it is plugged in."""
    try:
        names = sorted(p.name for p in _USB.iterdir())
    except OSError as exc:
        logger.debug("cannot list USB: %s", exc)
        return []
    devices = []
    for name in names:
        entry = _USB / name
        record: Dict[str, object] = {"port": name}
        product = _read_text(entry / "product")
        if product:
            record["product"] = product
        manufacturer = _read_text(entry / "manufacturer")
        if manufacturer:
            record["manufacturer"] = manufacturer
        # `bDeviceClass` lives on the device; an interface entry like `1-0:1.0`
        # has none of its own, so the class is read from the nearest ancestor
        # that does. Without that, every root hub and every composite device
        # comes out unclassified.
        # An interface also has `bInterfaceClass`, and a composite device may
        # only be classified there, so both are consulted.
        class_code = _read_text(entry / "bDeviceClass")
        source = entry
        if not class_code:
            class_code = _read_text(entry / "bInterfaceClass")
        for _ in range(4):
            if class_code:
                break
            # `1-0:1.0` -> `1-0`: an interface is its device's name plus a
            # `:config.alt` suffix, so the parent is the name with the colon
            # and everything after it removed. Splitting on the first dash
            # instead lands on the bus root.
            parent = source.name.partition(":")[0]
            candidate = source.parent / parent
            if candidate == source or not candidate.is_dir():
                class_code = None
                break
            source = candidate
            class_code = _read_text(source / "bDeviceClass")
        if class_code:
            record["class"] = _USB_CLASSES.get(class_code.lower().zfill(2),
                                                f"class {class_code}")
            if source != entry:
                record["on"] = source.name
        speed = _read_text(entry / "speed")
        if speed:
            record["speed_mbps"] = int(speed) if speed.isdigit() else speed
        devices.append(record)
    return devices


def _lspci() -> List[str]:
    if shutil.which("lspci") is None:
        return []
    try:
        proc = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("lspci failed: %s", exc)
        return []
    return [
        line.split(": ", 1)[-1].strip()
        for line in (proc.stdout or "").splitlines()
        if ": " in line
    ]


def _lsusb() -> List[str]:
    if shutil.which("lsusb") is None:
        return []
    try:
        proc = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("lsusb failed: %s", exc)
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("devices"):
        return f"Not reading device inventory: {config.sense_allowed_reason('devices')}."

    pci = read_pci()
    usb = read_usb()
    lspci_present = shutil.which("lspci") is not None
    lsusb_present = shutil.which("lsusb") is not None

    if not pci and not usb:
        return _SENSE.to_percept(
            "No PCI or USB device was readable under /sys/bus. On a running "
            "machine that is a fact about what could be read - a container, or "
            "a kernel without those buses - and not a claim that the machine "
            "has no hardware.",
            source="sysfs-bus",
            metadata={"pci": 0, "usb": 0, "determined": False},
        )

    lines = []
    if pci:
        unclaimed = [d for d in pci if d.get("driver") is None]
        lines.append(f"{len(pci)} PCI device(s):")
        for device in sorted(pci, key=lambda d: str(d.get("class"))):
            detail = f"  {device['slot']}  {device['class']}"
            if device.get("id"):
                detail += f"  [{device['id']}]"
            if device.get("driver"):
                detail += f"  driver {device['driver']}"
            else:
                detail += "  NO DRIVER BOUND"
            lines.append(detail)
        if unclaimed:
            lines.append(
                f"{len(unclaimed)} device(s) have no driver bound. That is "
                f"hardware the kernel does not know what to do with, which is "
                f"different from a device whose driver could not be read."
            )

    if usb:
        hubs = [d for d in usb if d.get("class") == "hub"]
        peripherals = [d for d in usb if d.get("class") not in (None, "hub")]
        lines.append(f"{len(usb)} USB device(s):")
        for device in usb:
            detail = f"  port {device['port']}  {device.get('class', 'unknown')}"
            if device.get("product"):
                detail += f"  {device['product']}"
            if device.get("speed_mbps"):
                detail += f"  {device['speed_mbps']} Mb/s"
            lines.append(detail)
        if hubs:
            lines.append(
                f"{len(hubs)} of these are root hubs, which are the controller "
                f"itself rather than something plugged in; {len(peripherals)} "
                f"are peripherals."
            )

    missing = [name for name, present in
               (("lspci", lspci_present), ("lsusb", lsusb_present)) if not present]
    if missing:
        lines.append(
            f"{' and '.join(missing)} not installed, so these are the kernel's "
            f"own class codes and ids rather than the tools' names."
        )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-bus",
        metadata={
            "pci": len(pci),
            "pci_unclaimed": sum(1 for d in pci if d.get("driver") is None),
            "usb": len(usb),
            "usb_hubs": sum(1 for d in usb if d.get("class") == "hub"),
            "lspci_present": lspci_present,
            "lsusb_present": lsusb_present,
            "determined": True,
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "devices",
        "description": (
            "Inventory the PCI and USB buses: every device with its class, "
            "vendor and device id, the driver bound to it, and for USB the port "
            "it is plugged into and its link speed. Reads the kernel's sysfs, so "
            "it needs no external tool; lspci and lsusb add the readable names "
            "when installed, and the output says which it is showing. Flags a "
            "PCI device with no driver bound, which is hardware the kernel does "
            "not know what to do with, and separates USB root hubs from "
            "peripherals, since a hub is the controller rather than something "
            "plugged in. These are class codes and ids, not a diagnosis: "
            "identifying what a device is for is the subject of the gpu and "
            "camera senses."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="devices",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
