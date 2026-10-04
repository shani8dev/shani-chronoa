"""Display sense: the panel's own backlight and which outputs are connected.

`/sys/class/backlight` exposes the panel's brightness and its maximum, and
`/sys/class/drm/*/status` says whether an output is connected. Both are plain
integer reads with no capture of any kind.

**Brightness here is the screen's setting, not the room's light.** A panel
reporting 9% tells you what the backlight is set to, which is not how bright
it is where you are sitting and not how bright the room is. A real ambient
light sensor is different hardware - usually a separate ALS part - and
reporting one as the other would be a straight lie about what the machine can
do. If no ALS node exists, this says so rather than substituting a guess.

The reason this is worth a sense at all: screen brightness is the most
frequent involuntary signal a user sends, and it is trivially available to
any program. Being able to ask "is my screen at 5% in a bright room" is
usually more useful than guessing.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
# The TTL must be >= the poll interval or the percept expires before the
# scheduler refreshes it, leaving the display state invisible for the gap. This
# pair shipped as ttl=120 against poll=300, so the fact was missing for 180s of
# every 300s cycle. Enforced registry-wide by
# tests/test_sense_manifest.py::TestPollIntervalNeverExceedsTtl.
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_BACKLIGHT = "/sys/class/backlight"
_DRM = Path("/sys/class/drm")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "display",
        "description": (
            "Read display state: each backlight's current and maximum "
            "brightness, and whether each DRM output is connected. This is "
            "the screen's own backlight setting - it is not an ambient light "
            "sensor and cannot measure the room."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def read_backlights() -> list:
    out = []
    try:
        entries = sorted(Path(_BACKLIGHT).iterdir())
    except OSError:
        return out
    for entry in entries:
        now = sysfs.read_int(entry / "brightness")
        if now is None:
            continue
        out.append({
            "backlight": entry.name,
            "brightness": now,
            "max_brightness": sysfs.read_int(entry / "max_brightness"),
        })
    return out


_HEADER = bytes((0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00))
_DESCRIPTOR_OFFSETS = (54, 72, 90, 108)
_TAG_MONITOR_NAME = 0xFC
_TAG_MONITOR_SERIAL = 0xFF


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
        status = sysfs.read_text(entry / "status")
        record["status"] = status or "unknown"
        record["enabled"] = sysfs.read_text(entry / "enabled") or "unknown"
        dpms = sysfs.read_text(entry / "dpms")
        if dpms:
            record["dpms"] = dpms
        modes = sysfs.read_text(entry / "modes")
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
    if not config.sense_allowed("display"):
        return f"Not reading display state: {config.sense_allowed_reason('display')}."

    backlights = read_backlights()
    connectors = read_connectors()
    if not backlights and not connectors:
        return "This machine exposes no backlight or DRM connector nodes to read."

    lines = []
    for b in backlights:
        pct = (
            round(100.0 * b["brightness"] / b["max_brightness"])
            if b["max_brightness"] else None
        )
        pct_text = f" ({pct}%)" if pct is not None else ""
        lines.append(
            f"backlight {b['backlight']}: "
            f"{b['brightness']}/{b['max_brightness']}{pct_text}")

    if not connectors:
        return _SENSE.to_percept(
            "\n".join(lines)
            + "\nNo DRM connector was readable under /sys/class/drm. On a "
              "running machine that is a fact about what could be read - a "
              "headless server, or a driver not using DRM - and not a claim "
              "that the machine has no display hardware.",
            source="sysfs-drm",
            metadata={"backlights": len(backlights), "connectors": 0,
                      "connected": 0, "identified": 0, "determined": False},
        )

    detail: List[str] = []
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
            detail.append(f"{record['connector']}: {who}")
            if identity.get("serial"):
                detail.append(f"  serial {identity['serial']}")
            if identity.get("size_cm"):
                detail.append(f"  physical size {identity['size_cm']} cm")
            if identity.get("manufactured"):
                detail.append(
                    f"  made {identity['manufactured']}, "
                    f"EDID {identity['edid_version']}")
        else:
            detail.append(
                f"{record['connector']}: connected, identity undetermined"
            )
            detail.append(
                "  this connector exposes no EDID, which is normal for a "
                "laptop panel over eDP - the kernel has no DDC/CI link to read "
                "it from. It is not evidence that no monitor is attached."
            )
        if record.get("active"):
            detail.append(
                f"  showing {record['active']}"
                + (f" of {len(record['modes'])} mode(s) offered"
                   if record.get("modes") else "")
            )
            if not identity:
                detail.append(
                    "  this is the mode in use, not the panel's native "
                    "resolution; only EDID reports that"
                )

    if not detail:
        return _SENSE.to_percept(
            "\n".join(lines)
            + f"\n{len(connectors)} connector(s) exposed, none currently "
              f"connected. An unplugged monitor is the normal state of a "
              f"desktop with external displays.",
            source="sysfs-drm",
            metadata={"backlights": len(backlights),
                      "connectors": len(connectors), "connected": 0,
                      "identified": 0, "determined": True},
        )

    lines.extend(detail)
    lines.append(
        f"{connected} of {len(connectors)} connector(s) connected; "
        f"{identified} identified from EDID. "
        + ("" if identified < connected else
           "Every connected panel reported its identity.")
    )
    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-drm",
        metadata={"backlights": len(backlights),
                  "connectors": len(connectors), "connected": connected,
                  "identified": identified, "determined": True},
    )


_SENSE = Sense(
    name="display",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
