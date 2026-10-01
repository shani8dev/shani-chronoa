"""Skill: the weather where you are, or anywhere you name, from Open-Meteo.

Open-Meteo (https://open-meteo.com) is free, needs no API key or account,
and serves both halves: its geocoding API turns "Mumbai" into coordinates,
and its forecast API answers for them. Two requests, both recorded in the
egress log with what they carried - a place name, or coordinates.

Where: a place in the request is used as given. Without one, the location
sense is asked (its own consent and privacy-mode gate apply), and if that is
off or has no fix the skill says so and asks for a place rather than guess.

Consent: a weather lookup is a web request, so it needs the web sense
(`web-sense-enabled`) and privacy mode off - the same gate web_search has.
"""

import json
import urllib.parse

import httpx

from shani_chronoa import egress
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = httpx.Timeout(8.0, connect=5.0)
MAX_BYTES = 64 * 1024

#: WMO weather interpretation codes, as Open-Meteo documents them.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "Current weather and today's forecast - temperature, feels-like, rain, "
            "wind - for a named place, or for where this computer is if no place is "
            "given. Use for weather, temperature outside, rain, forecast. Requires "
            "the web sense to be enabled and privacy mode to be off."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "place": {"type": "string",
                          "description": "A city or place, e.g. 'Mumbai' or 'Pune, India'. Omit for here."},
            },
        },
    },
}


def _get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    status = None
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "ShaniChronoa/1.0"}) as c:
            r = c.get(full)
            status = r.status_code
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                raise ValueError("response too large")
            return r.json()
    finally:
        egress.record("skill:get_weather", full, status=status,
                      privacy_mode=egress.privacy_mode_enabled())


def _place(name: str) -> "tuple[float, float, str] | str":
    data = _get(GEOCODE, {"name": name, "count": 1, "language": "en", "format": "json"})
    hits = data.get("results") or []
    if not hits:
        return f"Open-Meteo does not know a place called '{name}'."
    h = hits[0]
    label = ", ".join(x for x in (h.get("name"), h.get("admin1"), h.get("country")) if x)
    return float(h["latitude"]), float(h["longitude"]), label


def _here() -> "tuple[float, float, str] | str":
    from shani_chronoa.senses import location
    config = ChronoaConfig()
    if not config.sense_allowed("location"):
        return ("Which place? I can't use this computer's location: "
                f"{config.sense_allowed_reason('location')}.")
    pos, why = location.locate()
    if pos is None:
        return f"Which place? This computer's location is not available: {why}."
    return pos[0], pos[1], "here"


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"Weather is not available: {config.sense_allowed_reason('web')}."
    place = (arguments.get("place") or "").strip()
    try:
        where = _place(place) if place else _here()
        if isinstance(where, str):
            return where
        lat, lon, label = where
        data = _get(FORECAST, {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto", "forecast_days": 1,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        })
    except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError) as e:
        return f"Could not get the weather: {e.__class__.__name__}: {e}"
    cur, day = data.get("current", {}), data.get("daily", {})

    def first(key):
        v = day.get(key) or [None]
        return v[0]

    now = _WMO.get(cur.get("weather_code"), "unknown conditions")
    parts = [f"Weather {'' if label == 'here' else 'in ' + label + ' '}now: {now}, "
             f"{cur.get('temperature_2m')}°C (feels like {cur.get('apparent_temperature')}°C), "
             f"humidity {cur.get('relative_humidity_2m')}%, wind {cur.get('wind_speed_10m')} km/h."]
    if first("temperature_2m_max") is not None:
        parts.append(f"Today: {_WMO.get(first('weather_code'), 'mixed')}, "
                     f"{first('temperature_2m_min')} to {first('temperature_2m_max')}°C, "
                     f"{first('precipitation_probability_max')}% chance of rain.")
    parts.append("Source: Open-Meteo.")
    return " ".join(parts)


SKILLS = [Skill(name="get_weather", schema=_SCHEMA, run=_run)]
