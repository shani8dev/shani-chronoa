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
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

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
_DRM = "/sys/class/drm"

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


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def read_backlights() -> list:
    out = []
    try:
        entries = sorted(Path(_BACKLIGHT).iterdir())
    except OSError:
        return out
    for entry in entries:
        now = _read_int(entry / "brightness")
        if now is None:
            continue
        out.append({
            "backlight": entry.name,
            "brightness": now,
            "max_brightness": _read_int(entry / "max_brightness"),
        })
    return out


def read_outputs() -> list:
    out = []
    try:
        entries = sorted(Path(_DRM).iterdir())
    except OSError:
        return out
    for entry in entries:
        try:
            status = (entry / "status").read_text().strip()
        except OSError:
            continue
        out.append({"output": entry.name, "status": status})
    return out


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("display"):
        return f"Not reading display state: {config.sense_allowed_reason('display')}."

    backlights = read_backlights()
    outputs = read_outputs()
    if not backlights and not outputs:
        return "This machine exposes no backlight or DRM output nodes to read."

    lines = []
    for b in backlights:
        pct = (
            round(100.0 * b["brightness"] / b["max_brightness"])
            if b["max_brightness"] else None
        )
        pct_text = f" ({pct}%)" if pct is not None else ""
        lines.append(f"{b['backlight']}: {b['brightness']}/{b['max_brightness']}{pct_text}")
    connected = [o["output"] for o in outputs if o["status"] == "connected"]
    lines.append(f"outputs connected: {', '.join(connected) if connected else 'none'}")
    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-display",
        metadata={"backlights": len(backlights), "connected": len(connected)},
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
