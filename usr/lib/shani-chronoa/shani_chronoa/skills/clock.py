"""Skill: the current date and time, here or in another place.

"How time is it?" and "what time is it in Tokyo?" are one question with two
backends: the local clock, and the IANA zone database (with a web geocode only
for a place the database does not name). `get_world_time` used to be a second
skill; its handler lives here now so a caller asks one tool.

Local first: a place that names a time zone ("Tokyo" is Asia/Tokyo) or one of
the common cities the tz database files under another name ("Mumbai" is
Asia/Kolkata) is answered with no network at all. Anything else is looked up
with Open-Meteo's geocoding, which returns the place's time zone - a web
request, so only with the web sense on and privacy mode off.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime

from shani_chronoa.skills import Skill

#: Cities the tz database files under a different name.
ALIASES = {
    "mumbai": "Asia/Kolkata",
    "bombay": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata",
    "pune": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata",
    "chennai": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "beijing": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "washington": "America/New_York",
    "boston": "America/New_York",
    "miami": "America/New_York",
    "london": "Europe/London",
    "uk": "Europe/London",
    "dubai": "Asia/Dubai",
    "sydney": "Australia/Sydney",
    "japan": "Asia/Tokyo",
    "germany": "Europe/Berlin",
    "san jose": "America/Los_Angeles",
    "texas": "America/Chicago",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "california": "America/Los_Angeles",
}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_datetime",
        "description": "Current date and time here or in a named place.",
        "parameters": {
            "type": "object",
            "properties": {
                "place": {
                    "type": "string",
                    "description": "City, country or IANA zone; omit for here.",
                },
            },
        },
    },
}


def local_zone(place: str):
    p = " ".join(place.lower().replace("_", " ").split())
    if p in ALIASES:
        return ALIASES[p]
    for z in sorted(zoneinfo.available_timezones()):
        city = z.rsplit("/", 1)[-1].replace("_", " ").lower()
        if p == z.lower() or p == city:
            return z
    return None


def _geocoded_zone(place: str):
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.netjson import get_json
    from shani_chronoa.skills.weather import GEOCODE

    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return None, (
            f"'{place}' is not a time zone I know offline, and looking "
            f"it up needs the web: {config.sense_allowed_reason('web')}"
        )
    data = get_json(
        "skill:get_datetime",
        GEOCODE,
        {"name": place, "count": 1, "language": "en", "format": "json"},
    )
    hits = data.get("results") or []
    if not hits or not hits[0].get("timezone"):
        return None, f"I could not find a place called '{place}'"
    h = hits[0]
    return h["timezone"], ", ".join(x for x in (h.get("name"), h.get("country")) if x)


def _run(arguments: dict) -> str:
    place = str(arguments.get("place") or "").strip()
    if not place:
        return datetime.now().strftime("%A, %B %d, %Y %H:%M")
    zone, label = local_zone(place), place
    if zone is None:
        try:
            zone, label = _geocoded_zone(place)
        except Exception as exc:  # noqa: BLE001 - say what failed, never a wrong time
            return f"Could not look up '{place}': {exc.__class__.__name__}"
        if zone is None:
            return label + "."
    now = datetime.now(zoneinfo.ZoneInfo(zone))
    return (
        f"In {label} it is {now.strftime('%H:%M on %A, %B %d')} "
        f"({zone}, UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]})."
    )


SKILLS = [Skill(name="get_datetime", schema=_SCHEMA, run=_run)]
