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

#: The Earth, in the units the world uses. **A latitude outside ±90 or a
#: longitude outside ±180 is not on the planet**, and every source below hands
#: its numbers over as JSON or as a regex capture, so neither one can be
#: trusted to have checked. This is open-intelligent-ui's `coordinate()`
#: (`map-utils.ts`) in the other direction: it validates coordinates a *model*
#: supplied for a pin, this validates ones a *device* supplied for the machine.
#: The check is the same, because the failure is the same - a plausible number
#: that is not a place.
MAX_LATITUDE = 90.0
MAX_LONGITUDE = 180.0


def _coordinate(value: object) -> "Optional[float]":
    """`value` as a real, finite, on-the-planet coordinate, or None.

    Four refusals, each because a bad one is worse than an absent one:

    - **not a number at all.** `float("18.5")` happily converts a JSON string
      and `float(True)` converts a boolean to `1.0`; both are a device saying
      something this is not. Measured, gpsd's TPV with `"lat": null` (which its
      own man page allows - *"lat, lon: numeric, may be null"*) raised
      `TypeError: float() argument must be a string or a real number, not
      'NoneType'` out of the sense entirely, so a partially-known TPV crashed
      `get_location`, `weather` and `maps` instead of moving on to GeoClue.
    - **NaN or an infinity.** `float("NaN")` succeeds, and a NaN latitude
      renders as `nan` in every sentence this sense writes and as
      `around:5000,nan,nan` in the Overpass query `maps` builds - which then
      reads as "no petrol pump near you" rather than as a broken position.
    - **out of range.** `lat: 999.0` is accepted by nothing here today and is
      the confident wrong answer this sense exists not to give: it is a
      *position*, so nothing downstream can tell it was never a place.

    The finite/type half is `sysfs.finite`, the one implementation shared with
    `media_control._seconds` - the second copy of that test was written here
    while the first already existed, which is the `wpctl` triplication
    `files.py` records.

    Returning None rather than raising is the point: `locate()` tries its next
    source on a None, so one malformed answer moves on instead of ending the
    question.
    """
    from shani_chronoa import sysfs

    return float(value) if sysfs.finite(value) else None


def _on_the_planet(lat: object, lon: object) -> "Optional[Tuple[float, float]]":
    """(lat, lon) when both are real coordinates, else None."""
    flat, flon = _coordinate(lat), _coordinate(lon)
    if flat is None or flon is None:
        return None
    if abs(flat) > MAX_LATITUDE or abs(flon) > MAX_LONGITUDE:
        return None
    return flat, flon


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
            # `mode >= 2` is gpsd's own claim that it has a fix, and a fix is
            # not the same thing as a position - a device can report mode 3
            # with a lat that is null, a string or off the planet. Taking the
            # service's word for it is the shape this repo records for
            # `privilege` trusting dpkg-query and `bluetooth` answering
            # "0 devices" when bluetoothctl was simply missing.
            where = _on_the_planet(msg.get("lat"), msg.get("lon"))
            if where is None:
                return None, (f"gpsd sent a fix that is not a position "
                              f"(lat={msg.get('lat')!r}, lon={msg.get('lon')!r})")
            acc = _coordinate(msg.get("eph")) if msg.get("eph") is not None else None
            acc = _coordinate(msg.get("epx")) if acc is None else acc
            return (where[0], where[1], acc, "GPS receiver (gpsd)"), ""
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
    where = _on_the_planet(lat, lon)
    if where is None:
        # GeoClue's `where-am-i` prints whatever its source produced, and a
        # source can be wrong in a way the regex still captures happily.
        return None, f"GeoClue gave a position that is not on the planet ({lat}, {lon})"
    m = re.search(r"^\s*Description:\s*(.+)$", text, re.M)
    how = "GNOME location service (GeoClue)" + (f", {m.group(1).strip()}" if m else "")
    return (where[0], where[1], _coordinate(num("Accuracy")), how), ""


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
