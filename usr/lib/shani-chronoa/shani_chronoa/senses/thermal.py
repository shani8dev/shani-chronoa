"""Thermal sense: the temperature of this machine's own silicon.

The kernel exports a thermal zone per sensor the firmware declares, and each
zone has a name and a reading in millidegrees. On this machine that is
`INT3400 Thermal` (the board's own sensor) plus `SEN1`-`SEN4`, which the
firmware leaves as unnamed generic sensors.

**This is a machine thermometer, not a room thermometer.** It reads what the
CPU and the board are doing, and it is the useful, honest version of the
question: a machine sitting at 90 degrees is about to throttle, and that is
worth knowing before a long job starts rather than after it slows down.

It is emphatically not thermal imaging. There is no camera involved, no image
is produced, and nothing here can detect a person or read the temperature of
the room. A FLIR-style thermal array is a separate piece of hardware that
reports a coarse grid; this reads integer zone temperatures the kernel already
exposes for power management. Claiming otherwise from these same files would
be straightforwardly false.

Every reading is bounds-checked: a zone can report nonsense while the machine
is resuming, and a "112000" that means "read failed" must not be reported as
112 degrees.
"""

import glob as _glob
import logging
from pathlib import Path
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

# A zone reading outside this range is the kernel reporting a failure, not a
# temperature. Sub-zero is a real possibility for a board sensor, so the floor
# is generous but the ceiling is not.
_MIN_MILLIC = -40000
_MAX_MILLIC = 125000

_GLOB = "/sys/class/thermal/thermal_zone*"

_SCHEMA = {
    "type": "function",
    "function": {
        # Named for the consent surface: `sense_allowed()` builds the key as
        # "<name>-sense-enabled", so a rename here leaves the sense
        # permanently ungrantable.
        "name": "thermal",
        "description": (
            "Read the machine's thermal zones - CPU, board and any sensor the "
            "firmware exposes - and report their names and temperatures. This "
            "is the hardware's own temperature, not the temperature of the "
            "room or of anything in it; no camera is involved."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read_zone(zone: Path) -> Optional[dict]:
    try:
        raw = (zone / "temp").read_text().strip()
        milli = int(raw)
    except (OSError, ValueError):
        return None
    if not _MIN_MILLIC <= milli <= _MAX_MILLIC:
        return None
    try:
        name = (zone / "type").read_text().strip()
    except OSError:
        name = zone.name
    return {"zone": zone.name, "name": name or zone.name, "celsius": round(milli / 1000.0, 1)}


def read_zones() -> list:
    zones = []
    # `Path().glob` rejects an absolute pattern outright, which is a
    # NotImplementedError rather than an empty result, so the stdlib glob is
    # what actually supports an absolute sysfs pattern.
    for zone in sorted(Path(p) for p in _glob.glob(_GLOB)):
        reading = _read_zone(zone)
        if reading is not None:
            zones.append(reading)
    return zones


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("thermal"):
        return f"Not reading temperatures: {config.sense_allowed_reason('thermal')}."

    zones = read_zones()
    if not zones:
        return "No thermal zone on this machine reported a usable temperature."

    hottest = max(zones, key=lambda z: z["celsius"])
    lines = [f"{z['name']}: {z['celsius']}C" for z in zones]
    lines.append(f"hottest: {hottest['name']} at {hottest['celsius']}C")
    return _SENSE.to_percept("\n".join(lines), source="sysfs-thermal", metadata={"zones": len(zones)})


_SENSE = Sense(
    name="thermal",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
