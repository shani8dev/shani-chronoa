"""Hardware-monitoring sense: fan speed, SSD and chip temperature, voltage, current.

The kernel's `hwmon` class is where a machine reports what its own hardware is
doing: `/sys/class/hwmon/hwmon*/` carries `fan*_input` in RPM, `temp*_input`
in millidegrees, and `in*_input`, `curr*_input`, `power*_input` for the rest,
with each chip identified by its `name` (which is the driver).

This is a **complement** to `senses/thermal.py`, not a repeat of it. The
thermal zones are the CPU and board; hwmon is where a fan's RPM, the SSD's
die temperature, the WiFi chip's temperature and the battery's voltage and
current live. On this machine the two together surface a genuinely different
picture: the zones reported `SEN2: 90.0C`, and hwmon adds `nvme 37.9C` and
`iwlwifi_1` alongside two fans at 3300 RPM.

**Unpopulated slots are the trap, and they are the reason this module exists
rather than a five-line read of the directory.** Drivers routinely declare more
channels than the board physically wires up. On this machine `thinkpad`
exposes `temp1` through `temp8`, and `temp4` through `temp8` all read back
exactly `0` - which is 0.0 degrees Celsius, physically impossible on a machine
that is running. Reporting those as readings would put five fictitious
zero-degree sensors in front of a user, and a plausible-looking zero is worse
than an absent value: it is indistinguishable from a real reading until
something acts on it. So a channel that reads zero is reported as
**unpopulated**, and counted separately.

The opposite sentinel exists too, and the kernel documentation is explicit
about it for the drivers that declare these channels: a temperature above
127 degrees is a BIOS or driver read error, not a temperature. Both ends are
therefore range-checked, and a value outside a plausible band is reported as a
bad reading rather than passed through.
"""

import glob as _glob
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 60.0
_POLL_INTERVAL = 60.0

_GLOB = "/sys/class/hwmon/hwmon*"

# Attribute suffix -> (kind, scale, unit). `scale` converts the kernel's
# integer into the unit named here.
_KINDS = {
    "fan": ("fan", 1, "RPM"),
    "temp": ("temp", 0.001, "C"),
    "in": ("voltage", 0.001, "V"),
    "curr": ("current", 0.001, "A"),
    "power": ("power", 0.000001, "W"),
    "energy": ("energy", 0.000001, "Wh"),
    "humidity": ("humidity", 0.001, "%"),
}

# A temperature channel reading exactly 0 is an unwired slot on every driver
# that declares one; a real 0C reading on a powered machine is not a thing.
_UNPOPULATED_TEMP = 0
# Above this the kernel's own hwmon docs describe a read error, not a
# temperature (dell-smm-hwmon says so explicitly).
_MAX_PLAUSIBLE_C = 127
_MIN_PLAUSIBLE_C = -50

_ATTRIBUTE = re.compile(r"^(fan|temp|in|curr|power|energy|humidity)(\d+)_(input|label)$")

_SCHEMA = {
    "type": "function",
    "function": {
        # Named for the consent surface: `sense_allowed()` builds the key as
        # "<name>-sense-enabled", so a rename here leaves the sense
        # permanently ungrantable.
        "name": "hwmon",
        "description": (
            "Read the kernel's hardware-monitoring sensors: fan speed in RPM, "
            "and the temperature, voltage, current and power readings each "
            "driver exposes, identified by driver name. Complements the "
            "thermal sense, which reports the CPU and board. Channels a "
            "driver declares but the board does not populate are reported as "
            "unpopulated rather than as a reading."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _chips() -> List[Path]:
    """Every hwmon chip, following the symlinked hwmonX directories.

    `Path.glob` does not descend through a symlinked directory, and every
    attribute lives inside one, so a plain glob over `/sys/class/hwmon/*/`
    finds the directories and nothing inside them. Sorted by name so a chip
    does not change identity between polls.
    """
    chips = []
    for entry in sorted(_glob.glob(_GLOB)):
        path = Path(entry)
        # Resolve the symlink so attribute access works on the real path.
        try:
            chips.append(path.resolve())
        except OSError:
            continue
    return chips


def _channels(chip: Path) -> Dict[str, List[dict]]:
    """grouped by kind -> [{channel, label, value, state}]."""
    grouped: Dict[str, List[dict]] = {}
    try:
        entries = sorted(os.listdir(chip))
    except OSError as exc:
        logger.debug("cannot list %s: %s", chip, exc)
        return grouped

    for name in entries:
        match = _ATTRIBUTE.match(name)
        if not match:
            continue
        kind, index, item = match.group(1), match.group(2), match.group(3)
        if kind not in _KINDS:
            continue
        if item == "label":
            continue

        label = _read_text(chip / f"{kind}{index}_label")
        raw = _read_text(chip / name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue

        resolved, scale, unit = _KINDS[kind]
        record = {
            "channel": f"{kind}{index}",
            "label": label or chip.name,
            "state": "ok",
        }
        if resolved == "temp":
            if value == _UNPOPULATED_TEMP:
                # A declared-but-unwired slot. Not 0C, and not a reading.
                record["state"] = "unpopulated"
            elif not _MIN_PLAUSIBLE_C <= value / 1000.0 <= _MAX_PLAUSIBLE_C:
                # A read error, per the kernel's own documentation for these
                # drivers. Reported as bad rather than passed through.
                record["state"] = "bad-reading"
            else:
                record["celsius"] = round(value * scale, 1)
        else:
            record["value"] = round(value * scale, 3)
            record["unit"] = unit
            if value < 0:
                record["state"] = "bad-reading"

        grouped.setdefault(resolved, []).append(record)
    return grouped


def read_chips() -> List[dict]:
    """Every chip, with its readings and any unpopulated channels."""
    out = []
    for chip in _chips():
        name = _read_text(chip / "name") or chip.name
        channels = _channels(chip)
        if not channels:
            continue
        out.append({"chip": name, "path": str(chip), "channels": channels})
    return out


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("hwmon"):
        return f"Not reading hardware sensors: {config.sense_allowed_reason('hwmon')}."

    chips = read_chips()
    if not chips:
        return (
            "No hwmon chip exposed a readable channel. This is normal on a "
            "machine whose firmware reports temperatures only through the "
            "thermal zones - the thermal sense covers that case."
        )

    lines = []
    total_unpopulated = 0
    total_readings = 0
    for entry in chips:
        lines.append(f"{entry['chip']}:")
        for kind in sorted(entry["channels"]):
            for record in entry["channels"][kind]:
                if record["state"] == "unpopulated":
                    total_unpopulated += 1
                    lines.append(f"  {record['channel']} ({record['label']}): not populated")
                    continue
                if record["state"] == "bad-reading":
                    lines.append(f"  {record['channel']} ({record['label']}): reading out of range")
                    continue
                total_readings += 1
                if kind == "temp":
                    lines.append(
                        f"  {record['channel']} ({record['label']}): {record['celsius']}C"
                    )
                else:
                    lines.append(
                        f"  {record['channel']} ({record['label']}): "
                        f"{record['value']} {record['unit']}"
                    )

    lines.append(
        f"{total_readings} reading(s) across {len(chips)} chip(s); "
        f"{total_unpopulated} channel(s) declared but not populated"
    )
    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-hwmon",
        metadata={
            "chips": len(chips),
            "readings": total_readings,
            "unpopulated": total_unpopulated,
        },
    )


_SENSE = Sense(
    name="hwmon",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
