"""Location sense: where this machine is, from the system's own location services.

Two sources, both already in every Shanios image, tried in order:

1. **gpsd** - a GPS receiver's fix, when one is attached and gpsd has a fix.
   Precise, and it never touches the network. `gpspipe -w` streams gpsd's
   JSON; the first TPV report with a 2D/3D fix (mode >= 2) is the answer.
2. **GeoClue** - GNOME's location service: Wi-Fi networks looked up against
   a location service, a 3G/modem fix, NMEA from the network, or the static
   source in /etc/geolocation. Read through GeoClue's own `where-am-i`
   client, which GeoClue's configuration already authorises.

Neither answering is reported as such, with each source's reason - never a
guess and never an IP lookup made on the user's behalf.

Consent: `location-sense-enabled`, off by default - where someone is is
private on any reading. It is also listed with the networked senses, so
privacy mode blocks it: GeoClue's Wi-Fi source sends nearby network IDs to a
location service, and a user who switched the network off has not agreed
to that. The percept is PRIVATE and lives five minutes.
"""

import json
import os
import re
import shutil
import subprocess
import time
from typing import Optional, Tuple

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Percept, Sense

POSITION_TTL_SECONDS = 300.0
WHERE_AM_I = "/usr/lib/geoclue-2.0/demos/where-am-i"
GPS_TIMEOUT_SECONDS = 4
GEOCLUE_TIMEOUT_SECONDS = 12

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "location",
        "description": (
            "Find where this computer is - latitude, longitude and how accurate "
            "that is - from a GPS receiver (gpsd) or GNOME's location service "
            "(GeoClue). Only works when the location sense is enabled and "
            "privacy mode is off."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

Position = Tuple[float, float, Optional[float], str]  # lat, lon, accuracy m, how


def _from_gpsd() -> "tuple[Optional[Position], str]":
    if not shutil.which("gpspipe"):
        return None, "gpsd's gpspipe is not installed"
    try:
        out = subprocess.run(["gpspipe", "-w", "-n", "12", "-x", str(GPS_TIMEOUT_SECONDS)],
                             capture_output=True, text=True, timeout=GPS_TIMEOUT_SECONDS + 2)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"gpsd did not answer ({e.__class__.__name__})"
    devices = False
    for line in out.stdout.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("class") == "DEVICES" and msg.get("devices"):
            devices = True
        if msg.get("class") == "TPV" and (msg.get("mode") or 0) >= 2 and "lat" in msg and "lon" in msg:
            acc = msg.get("eph") or msg.get("epx")
            return (float(msg["lat"]), float(msg["lon"]), float(acc) if acc else None, "GPS receiver (gpsd)"), ""
    if out.returncode != 0 and not out.stdout:
        return None, "gpsd is not running"
    return None, "gpsd has a receiver but no fix yet" if devices else "no GPS receiver is attached"


def _from_geoclue() -> "tuple[Optional[Position], str]":
    if not os.access(WHERE_AM_I, os.X_OK):
        return None, "GeoClue is not installed"
    try:
        out = subprocess.run([WHERE_AM_I, "-t", str(GEOCLUE_TIMEOUT_SECONDS)],
                             capture_output=True, text=True, timeout=GEOCLUE_TIMEOUT_SECONDS + 3)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"GeoClue did not answer ({e.__class__.__name__})"
    text = out.stdout

    def num(label: str) -> Optional[float]:
        m = re.search(rf"^\s*{label}:\s*(-?[0-9.]+)", text, re.M)
        return float(m.group(1)) if m else None

    lat, lon = num("Latitude"), num("Longitude")
    if lat is None or lon is None:
        err = (out.stderr.strip().splitlines() or ["no location within the timeout"])[-1]
        return None, f"GeoClue gave no position: {err[:160]}"
    m = re.search(r"^\s*Description:\s*(.+)$", text, re.M)
    how = "GNOME location service (GeoClue)" + (f", {m.group(1).strip()}" if m else "")
    return (lat, lon, num("Accuracy"), how), ""


def locate() -> "tuple[Optional[Position], str]":
    """The best position available, or None and why each source failed."""
    why = []
    for source in (_from_gpsd, _from_geoclue):
        pos, reason = source()
        if pos:
            return pos, ""
        why.append(reason)
    return None, "; ".join(why)


def _run(_arguments: dict):
    config = ChronoaConfig()
    if not config.sense_allowed("location"):
        return f"Location is not permitted: {config.sense_allowed_reason('location')}."
    pos, why = locate()
    if pos is None:
        return f"This computer's location is not available: {why}."
    lat, lon, acc, how = pos
    accuracy = f" (within about {acc:.0f} m)" if acc else ""
    return Percept(
        sense="location",
        kind="machine-state",
        content=f"Latitude {lat:.4f}, longitude {lon:.4f}{accuracy}, from {how}.",
        created_at=time.time(),
        ttl_seconds=POSITION_TTL_SECONDS,
        source=how,
        sensitivity=SENSITIVITY_PRIVATE,
        metadata={"latitude": lat, "longitude": lon, "accuracy_m": acc},
    )


SENSE = Sense(
    name="location",
    kind="machine-state",
    ttl_seconds=POSITION_TTL_SECONDS,
    sensitivity=SENSITIVITY_PRIVATE,
    schema=_SCHEMA,
    run=_run,
    poll_interval=None,
)

SENSES = [SENSE]
