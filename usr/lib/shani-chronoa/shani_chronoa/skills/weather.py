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

Other days: an optional `when` ("tomorrow", "friday", "this week", "weekend",
"day after tomorrow", "in 3 days", or an ISO date) returns Open-Meteo's daily
forecast for that day or range instead of the current conditions. Its days are
counted in the *place's* own time zone (`timezone=auto`, read back from the
first `daily.time`), not this computer's, so "tomorrow in Tokyo" is Tokyo's
tomorrow. An unparseable `when` is refused before any request is sent; a day
beyond Open-Meteo's 16-day horizon is said to be out of range rather than
silently answered with the last day it has. Without `when` (or with "today")
the request and the answer are exactly what they were before it existed.
"""

import datetime as _dt
import json
import re

import httpx

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

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
            "given; with `when`, the forecast for tomorrow, a weekday, this week or the "
            "weekend instead. Use for weather, temperature outside, rain, forecast. Requires "
            "the web sense to be enabled and privacy mode to be off."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "place": {"type": "string",
                          "description": "A city or place, e.g. 'Mumbai' or 'Pune, India'. Omit for here."},
                "when": {"type": "string",
                         "description": ("Which day: 'tomorrow', a weekday like 'friday', 'this week', "
                                         "'weekend', 'day after tomorrow', 'in 3 days' or a date "
                                         "YYYY-MM-DD (up to 16 days ahead). Omit for now and today.")},
            },
        },
    },
}


def _get(url: str, params: dict) -> dict:
    from shani_chronoa.netjson import get_json
    return get_json("skill:get_weather", url, params)


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
        home = str(config.get("home-place", "") or "").strip()
        if home:
            # No GPS or GeoClue here; the person named a home place instead.
            return _place(home)
        return (f"Which place? This computer's location is not available: {why}. "
                "Set a home place in Settings > Privacy to use it instead.")
    return pos[0], pos[1], "here"


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_HORIZON = 16  # days Open-Meteo's forecast API serves
_WHEN_HELP = ("today, tomorrow, the day after tomorrow, a weekday, this week, the weekend, "
              "'in N days' or a date like 2026-10-12")


def parse_when(raw) -> "tuple | str":
    """What `when` asks for, before any date is known: a tagged tuple, or a
    sentence saying it could not be understood. 'today' is ("today",)."""
    if raw is None:
        return ("today",)
    if not isinstance(raw, str):
        return f"I couldn't read the day {raw!r}; say {_WHEN_HELP}."
    w = re.sub(r"[^a-z0-9\- ]", " ", raw.lower())
    w = re.sub(r"\b(the|on|for|of|forecast|weather)\b", " ", w)
    w = " ".join(w.split())
    if w in ("", "today", "now", "tonight", "this evening", "this morning", "this afternoon"):
        return ("today",)
    if w in ("tomorrow", "tmrw", "tomorrow morning", "tomorrow evening", "tomorrow night"):
        return ("offset", 1)
    if w in ("day after tomorrow", "overmorrow"):
        return ("offset", 2)
    m = re.fullmatch(r"in (\d{1,2}) days?", w)
    if m:
        return ("offset", int(m.group(1)))
    if w in ("this week", "week", "next 7 days", "next seven days", "coming week", "rest of week",
             "rest of this week", "next few days", "week ahead"):
        return ("range", 0, 6)
    if w == "next week":
        return ("range", 7, 13)
    if w in ("weekend", "this weekend", "coming weekend"):
        return ("weekend", 0)
    if w == "next weekend":
        return ("weekend", 7)
    m = re.fullmatch(r"(?:(this|next|coming) )?([a-z]+)", w)
    if m:
        name = m.group(2)
        for i, day in enumerate(_WEEKDAYS):
            if name == day or (len(name) >= 3 and day.startswith(name)):
                return ("weekday", i, m.group(1) == "next")
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})", w)
    if m:
        try:
            return ("date", _dt.date.fromisoformat(m.group(1)))
        except ValueError:
            pass
    return f"I couldn't tell which day '{raw}' means; say {_WHEN_HELP}."


def resolve_days(spec: tuple, today: _dt.date) -> "list[int] | str":
    """Day offsets from `today` (the place's own today) that `spec` covers."""
    kind = spec[0]
    if kind == "today":
        return [0]
    if kind == "offset":
        days = [spec[1]]
    elif kind == "range":
        days = list(range(spec[1], spec[2] + 1))
    elif kind == "weekend":
        to_sat = (5 - today.weekday()) % 7
        days = [0] if today.weekday() == 6 else [to_sat, to_sat + 1]
        days = [d + spec[1] for d in days]
    elif kind == "weekday":
        off = (spec[1] - today.weekday()) % 7
        if spec[2] and off == 0:
            off = 7
        days = [off]
    elif kind == "date":
        days = [(spec[1] - today).days]
    else:
        return "That day could not be worked out."
    if min(days) < 0:
        return f"{today + _dt.timedelta(days=min(days)):%A %d %B %Y} has already passed there; I only have forecasts."
    if max(days) >= _HORIZON:
        last = today + _dt.timedelta(days=_HORIZON - 1)
        return (f"Open-Meteo forecasts only {_HORIZON} days ahead (to {last:%A %d %B}); "
                f"that day is too far out to forecast.")
    return days


def _day_name(off: int, day: _dt.date) -> str:
    return {0: "Today", 1: "Tomorrow"}.get(off, f"{day:%A}") + f" ({day:%a} {day.day} {day:%b})"


def _forecast(lat: float, lon: float, label: str, spec: tuple) -> str:
    """The daily forecast for the days `spec` names. Network errors propagate."""
    # Ask for no more days than needed: a weekday is at most 7 out, a date or
    # 'in N days' is checked against the horizon after today is known.
    want = {"offset": lambda: spec[1] + 1, "range": lambda: spec[2] + 1,
            "weekend": lambda: 8 + spec[1], "weekday": lambda: 8}.get(spec[0], lambda: _HORIZON)()
    data = _get(FORECAST, {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto",
        "forecast_days": max(1, min(_HORIZON, want)),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,weather_code,wind_speed_10m_max",
    })
    day = data.get("daily") or {}
    times = day.get("time") or []
    if not times:
        return "Open-Meteo returned no daily forecast for that place."
    today = _dt.date.fromisoformat(times[0])
    offsets = resolve_days(spec, today)
    if isinstance(offsets, str):
        return offsets

    def at(key, i):
        v = day.get(key) or []
        return v[i] if i < len(v) else None

    where = "" if label == "here" else f" in {label}"
    lines = []
    for off in offsets:
        if off >= len(times) or at("temperature_2m_max", off) is None:
            lines.append(f"{_day_name(off, today + _dt.timedelta(days=off))}: no forecast was returned for that day.")
            continue
        d = _dt.date.fromisoformat(times[off])
        line = (f"{_day_name(off, d)}: {_WMO.get(at('weather_code', off), 'mixed')}, "
                f"{at('temperature_2m_min', off)} to {at('temperature_2m_max', off)}°C")
        if at("precipitation_probability_max", off) is not None:
            line += f", {at('precipitation_probability_max', off)}% chance of rain"
        if at("precipitation_sum", off):
            line += f" ({at('precipitation_sum', off)} mm)"
        if at("wind_speed_10m_max", off) is not None:
            line += f", wind up to {at('wind_speed_10m_max', off)} km/h"
        lines.append(line + ".")
    head = f"Forecast{where}:"
    return head + (" " if len(lines) == 1 else "\n") + "\n".join(lines) + "\nSource: Open-Meteo."


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"Weather is not available: {config.sense_allowed_reason('web')}."
    place = arguments.get("place")
    place = place.strip() if isinstance(place, str) else ""
    spec = parse_when(arguments.get("when"))
    if isinstance(spec, str):
        return spec
    try:
        where = _place(place) if place else _here()
        if isinstance(where, str):
            return where
        lat, lon, label = where
        if spec[0] != "today":
            return _forecast(lat, lon, label, spec)
        data = _get(FORECAST, {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto", "forecast_days": 1,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunrise,sunset",
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
    if first("sunrise") and first("sunset"):
        parts.append(f"Sunrise {str(first('sunrise'))[-5:]}, sunset {str(first('sunset'))[-5:]}.")
    parts.append("Source: Open-Meteo.")
    return " ".join(parts)


SKILLS = [Skill(name="get_weather", schema=_SCHEMA, run=_run)]
