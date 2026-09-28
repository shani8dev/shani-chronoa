"""Connected monitors: identity from EDID, and the modes each connector offers.

`display` reports backlight and whether a connector is plugged in. This reports
*which* monitor is plugged in — make, model, serial, native resolution and
physical size — which is the question someone actually has when a display
behaves oddly or when two identical-looking monitors turn out not to be.

**An empty EDID blob is not the same as no monitor, and this machine has both
cases.** Measured here: the connected `card1-eDP-1` carries a full 128-byte
EDID — it decodes to a BOE panel, 31 x 17 cm, made week 32 of 2019 — while the
*disconnected* `card1-DP-1` through `-DP-4` and `card1-HDMI-A-1` each have a
**0-byte** `edid` file, which is what a port with nothing plugged in looks like.

So a connector with no EDID is reported with everything the kernel does know —
status, enabled, DPMS, offered modes — and with its identity explicitly
undetermined. It is never reported as having no monitor, and the mode in use is
never presented as the panel's native resolution, which only EDID carries.

**The EDID byte layout here comes from the kernel's own `include/drm/drm_edid.h`
and is fixed by the VESA spec**, not inferred:

- bytes 0-7: the 8-byte `00 ff ff ff ff ff ff 00` header
- bytes 8-9: manufacturer id, **big-endian**, as three 5-bit letters
- bytes 10-11: product code, little-endian
- bytes 12-15: serial number, little-endian
- bytes 16-17: week and year of manufacture (year is year − 1990)
- bytes 18-19: EDID version and revision
- bytes 20-21: video input parameters
- bytes 21 and 22: physical width and height in centimetres
- bytes 54, 72, 90, 108: four 18-byte descriptors, whose first two bytes are a
  tag — `0xfc` is the monitor's name and `0xff` its serial string

The manufacturer id is the part that goes wrong. It is 16 bits read
**big-endian**, and each 5-bit field holds a letter offset from `'A' - 1`; read
little-endian, or as three 8-bit values, every monitor comes out as a different
company. A `00 00` id is the one the spec defines as unset, and is reported as
unknown rather than as three letters.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_DRM = Path("/sys/class/drm")
_HEADER = bytes((0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00))
_DESCRIPTOR_OFFSETS = (54, 72, 90, 108)
_TAG_MONITOR_NAME = 0xFC
_TAG_MONITOR_SERIAL = 0xFF


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def manufacturer(value: int) -> str:
    """Decode a 16-bit EDID manufacturer id into three letters.

    Big-endian, three 5-bit fields, each an offset from 'A'-1 — the encoding is
    easy to get backwards and a wrong decode produces a plausible wrong company
    for every monitor. `00 00` is the spec's "unset" and stays unset.
    """
    if value == 0:
        return ""
    letters = []
    for shift in (10, 5, 0):
        code = (value >> shift) & 0x1F
        if code == 0:
            return ""
        letters.append(chr(ord("A") - 1 + code))
    return "".join(letters)


def _descriptor_strings(data: bytes) -> Dict[str, str]:
    """The `0xfc` name and `0xff` serial, if the descriptors carry them."""
    found: Dict[str, str] = {}
    for offset in _DESCRIPTOR_OFFSETS:
        block = data[offset:offset + 18]
        if len(block) < 18 or block[:2] == b"\x00\x00":
            continue
        tag = block[0]
        payload = block[3:18].split(b"\n", 1)[0].split(b"\r", 1)[0]
        text = payload.decode("latin-1", "replace").strip()
        if not text:
            continue
        if tag == _TAG_MONITOR_NAME:
            found.setdefault("name", text)
        elif tag == _TAG_MONITOR_SERIAL:
            # Deliberately not `serial`: bytes 12-15 are the 32-bit numeric
            # serial, and a descriptor string is a different fact about the
            # same monitor. Sharing the key let the string overwrite the
            # number, so a panel with both reported only the string.
            found.setdefault("serial_text", text)
    return found


def parse_edid(data: bytes) -> Optional[dict]:
    """Decode a 128-byte EDID blob, or None if it is not one.

    A blob shorter than 128 bytes, or without the fixed header, is refused: the
    offsets below are read by position, and a truncated blob would yield a
    manufacturer and a resolution assembled from the wrong bytes.
    """
    if len(data) < 128 or data[:8] != _HEADER:
        return None
    record: Dict[str, object] = {}
    mfg = int.from_bytes(data[8:10], "big")
    if mfg:
        record["manufacturer"] = manufacturer(mfg)
    record["product_code"] = int.from_bytes(data[10:12], "little")
    serial = int.from_bytes(data[12:16], "little")
    if serial:
        record["serial"] = serial
    year = data[17]
    if year:
        record["manufactured"] = f"{1990 + year}-W{data[16]:02d}"
    record["edid_version"] = f"{data[18]}.{data[19]}"
    width, height = data[21], data[22]
    if width and height:
        record["size_cm"] = f"{width} x {height}"
    record.update(_descriptor_strings(data))
    return record


def read_edid(connector: Path) -> Optional[dict]:
    try:
        blob = (connector / "edid").read_bytes()
    except OSError:
        return None
    if not blob:
        # A 0-byte `edid` is normal for an eDP panel, and says only that the
    # kernel could not read it over DDC — not that there is no monitor.
        return None
    return parse_edid(blob)


def read_connectors() -> List[dict]:
    """Every DRM connector, with its state and whatever identity is available."""
    try:
        names = sorted(p.name for p in _DRM.iterdir())
    except OSError as exc:
        logger.debug("cannot list DRM: %s", exc)
        return []
    connectors = []
    for name in names:
        # `card1` is the adapter, not a port, and `renderD128` is not either.
        if "-" not in name or not name.split("-")[-1][:1].isdigit():
            continue
        entry = _DRM / name
        record: Dict[str, object] = {"connector": name}
        status = _read_text(entry / "status")
        record["status"] = status or "unknown"
        record["enabled"] = _read_text(entry / "enabled") or "unknown"
        dpms = _read_text(entry / "dpms")
        if dpms:
            record["dpms"] = dpms
        modes = _read_text(entry / "modes")
        if modes:
            listed = [m for m in modes.split() if m]
            record["modes"] = listed
            if listed:
                # The first mode is the preferred one the kernel picked, which
                # is a good proxy for what is actually being displayed - and is
                # NOT the panel's native resolution, which only EDID gives.
                record["active"] = listed[0]
        identity = read_edid(entry)
        record["edid"] = identity
        connectors.append(record)
    return connectors


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("monitors"):
        return f"Not reading monitors: {config.sense_allowed_reason('monitors')}."

    connectors = read_connectors()
    if not connectors:
        return _SENSE.to_percept(
            "No DRM connector was readable under /sys/class/drm. On a running "
            "machine that is a fact about what could be read - a headless "
            "server, or a driver not using DRM - and not a claim that the "
            "machine has no display hardware.",
            source="sysfs-drm",
            metadata={"connectors": 0, "connected": 0, "determined": False},
        )

    lines = []
    connected = 0
    identified = 0
    for record in connectors:
        if record["status"] != "connected":
            continue
        connected += 1
        identity = record.get("edid")
        if identity:
            identified += 1
            who = " ".join(x for x in (
                identity.get("manufacturer"), identity.get("name"),
            ) if x) or "unidentified panel"
            lines.append(f"{record['connector']}: {who}")
            if identity.get("serial"):
                lines.append(f"  serial {identity['serial']}")
            if identity.get("size_cm"):
                lines.append(f"  physical size {identity['size_cm']} cm")
            if identity.get("manufactured"):
                lines.append(f"  made {identity['manufactured']}, EDID {identity['edid_version']}")
        else:
            lines.append(
                f"{record['connector']}: connected, identity undetermined"
            )
            lines.append(
                "  this connector exposes no EDID, which is normal for a "
                "laptop panel over eDP - the kernel has no DDC/CI link to read "
                "it from. It is not evidence that no monitor is attached."
            )
        if record.get("active"):
            lines.append(
                f"  showing {record['active']}"
                + (f" of {len(record['modes'])} mode(s) offered"
                   if record.get("modes") else "")
            )
            if not identity:
                lines.append(
                    "  this is the mode in use, not the panel's native "
                    "resolution; only EDID reports that"
                )

    if not lines:
        return _SENSE.to_percept(
            f"{len(connectors)} connector(s) exposed, none currently connected. "
            f"An unplugged monitor is the normal state of a desktop with "
            f"external displays.",
            source="sysfs-drm",
            metadata={
                "connectors": len(connectors), "connected": 0,
                "identified": 0, "determined": True,
            },
        )

    lines.append(
        f"{connected} of {len(connectors)} connector(s) connected; "
        f"{identified} identified from EDID. "
        + ("" if identified < connected else
           "Every connected panel reported its identity.")
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-drm",
        metadata={
            "connectors": len(connectors),
            "connected": connected,
            "identified": identified,
            "determined": True,
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "monitors",
        "description": (
            "Read the connected displays: which DRM connector each is on, "
            "whether it is connected and enabled, its DPMS state, the mode "
            "currently in use and the modes on offer, and where EDID is "
            "readable the manufacturer, model name, serial number, physical "
            "size and manufacture week. A connected monitor whose connector "
            "exposes no EDID is reported as connected with its identity "
            "undetermined, which is normal for a laptop panel over eDP and is "
            "not evidence that nothing is attached. The mode in use is not the "
            "panel's native resolution - only EDID reports that - and the two "
            "are never conflated."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="monitors",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
