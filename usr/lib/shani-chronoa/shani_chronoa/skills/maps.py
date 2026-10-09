"""Skill: maps - find a place, route to it, list what is nearby, open it on a map.

Everything here is OpenStreetMap and needs no API key or account:

* **Nominatim** (nominatim.openstreetmap.org) turns "Shaniwar Wada" into
  coordinates. Its usage policy asks for an identifying User-Agent (netjson's
  `ShaniChronoa/1.0 (...)`), at most one request per second, and attribution.
  `_polite()` holds the one-per-second rule *across* processes - each skill call
  is its own sandboxed child, so an in-process timer alone would not - with a
  best-effort lock file under `$XDG_STATE_HOME/shani-chronoa/`.
* **OSRM on routing.openstreetmap.de** (the FOSSGIS servers openstreetmap.org's
  own directions use) gives road distance, duration and turn-by-turn steps for
  car, bike and foot. Not router.project-osrm.org: measured 2026-10-08, the
  demo server ignores the profile in the URL and returned the identical car
  route (146,805.5 m, 7,019.2 s, Pune to Mumbai) for `/driving/` and `/foot/`,
  so a "walking time" from it would be a driving time wearing a different
  label. FOSSGIS's policy is the same shape as Nominatim's (valid User-Agent,
  one request per second, no heavy use, attribution).
* **Overpass** (overpass-api.de) answers "nearest petrol pump" for the common
  kinds of place listed in `_KINDS`; anything else is a Nominatim search
  bounded to a box around the point.

**No live traffic.** OSRM's durations are typical travel times computed from
road types and speed limits. Nothing in this stack knows about today's traffic,
and every route answer says so rather than letting a model present it as
Google-Maps-style live ETA.

**Opening the map** uses the desktop's default browser through `xdg-open`, as
`open_file` does, not Chronoa's in-app browser. The in-app browser is a
`tools._LOCAL_TOOLS` path that exists only while the window is open and only
for the `browse` skill (this skill runs in the sandboxed child and cannot reach
it), and it carries the person's sign-ins, which a map does not need. A map is
something the person keeps looking at and panning after the conversation, which
is what their own browser is for. The coordinates are in the URL, so opening it
counts as a request leaving the machine: it is gated on the web sense and
recorded in the egress log like every fetch.

Consent: every action is a web request, so it needs the web sense
(`web-sense-enabled`) and privacy mode off - the same gate `get_weather` has.
"Here" (no origin, "nearest ...") additionally needs the location sense
(`location-sense-enabled`), read through `senses.location.locate()` exactly as
`get_weather` does; without it the skill asks for a place instead of guessing.

Distances in `nearby` are straight-line (great-circle) from the point, and are
labelled as such - they are not road distances.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time

import httpx

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

COMPONENT = "skill:maps"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
ROUTER = "https://routing.openstreetmap.de/routed-{mode}/route/v1/driving/{coords}"
OVERPASS = "https://overpass-api.de/api/interpreter"
OSM_PLACE = "https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map={zoom}/{lat:.5f}/{lon:.5f}"
OSM_ROUTE = "https://www.openstreetmap.org/directions?engine=fossgis_osrm_{mode}&route={a};{b}"
ATTRIBUTION = "Map data © OpenStreetMap contributors."
NO_TRAFFIC = ("This is the typical travel time from OpenStreetMap road data - it does not "
              "include live traffic, which no OpenStreetMap service provides.")

_ACTIONS = ("find", "directions", "travel_time", "nearby", "open")
_MODES = {
    "car": "car", "drive": "car", "driving": "car", "auto": "car", "taxi": "car", "cab": "car",
    "motorbike": "car", "bike ride": "car",
    "foot": "foot", "walk": "foot", "walking": "foot", "on foot": "foot",
    "bike": "bike", "bicycle": "bike", "cycle": "bike", "cycling": "bike",
}
_NO_TRANSIT = ("bus", "train", "metro", "transit", "public transport", "rail", "subway", "flight", "fly")

#: Spoken kinds of place -> OpenStreetMap tag. Matched on the singular, so
#: "hotels", "petrol pumps" and "ATMs" all land.
_KINDS = {
    "petrol pump": ("amenity", "fuel"), "petrol station": ("amenity", "fuel"),
    "gas station": ("amenity", "fuel"), "fuel": ("amenity", "fuel"), "fuel station": ("amenity", "fuel"),
    "ev charger": ("amenity", "charging_station"), "charging station": ("amenity", "charging_station"),
    "hotel": ("tourism", "hotel"), "guest house": ("tourism", "guest_house"), "hostel": ("tourism", "hostel"),
    "restaurant": ("amenity", "restaurant"), "cafe": ("amenity", "cafe"), "coffee shop": ("amenity", "cafe"),
    "fast food": ("amenity", "fast_food"), "bar": ("amenity", "bar"), "pub": ("amenity", "pub"),
    "atm": ("amenity", "atm"), "bank": ("amenity", "bank"),
    "hospital": ("amenity", "hospital"), "clinic": ("amenity", "clinic"), "doctor": ("amenity", "doctors"),
    "pharmacy": ("amenity", "pharmacy"), "chemist": ("amenity", "pharmacy"), "medical store": ("amenity", "pharmacy"),
    "police station": ("amenity", "police"), "police": ("amenity", "police"),
    "parking": ("amenity", "parking"), "toilet": ("amenity", "toilets"), "restroom": ("amenity", "toilets"),
    "post office": ("amenity", "post_office"), "school": ("amenity", "school"), "college": ("amenity", "college"),
    "library": ("amenity", "library"), "cinema": ("amenity", "cinema"), "theatre": ("amenity", "theatre"),
    "temple": ("amenity", "place_of_worship"), "place of worship": ("amenity", "place_of_worship"),
    "bus stop": ("highway", "bus_stop"), "bus station": ("amenity", "bus_station"),
    "railway station": ("railway", "station"), "train station": ("railway", "station"),
    "metro station": ("station", "subway"),
    "supermarket": ("shop", "supermarket"), "grocery": ("shop", "supermarket"),
    "bakery": ("shop", "bakery"), "mall": ("shop", "mall"),
    "park": ("leisure", "park"), "gym": ("leisure", "fitness_centre"),
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "maps",
        "description": (
            "OpenStreetMap maps: find where a place is ('find'), driving/walking/cycling "
            "directions ('directions') or just distance and time ('travel_time') between "
            "places, the nearest kind of place such as petrol pump, hotel, ATM or hospital "
            "('nearby'), or open a place or route on the map in the browser ('open'). "
            "Times are typical, without live traffic. With no origin, 'here' is this "
            "computer's location. Requires the web sense and privacy mode off; 'here' also "
            "needs the location sense."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS),
                           "description": "What to do. Defaults from the other arguments."},
                "place": {"type": "string",
                          "description": "The place to find or open, e.g. 'Pune railway station'."},
                "destination": {"type": "string",
                                "description": "Where to go, for directions/travel_time/open, e.g. 'Shaniwar Wada'."},
                "origin": {"type": "string",
                           "description": "Where from, e.g. 'Mumbai'. Omit to start from here."},
                "mode": {"type": "string", "enum": ["car", "foot", "bike"],
                         "description": "How: car (default), foot or bike."},
                "what": {"type": "string",
                         "description": "For nearby: the kind of place, e.g. 'petrol pump', 'hotels', 'ATM'."},
                "near": {"type": "string",
                         "description": "For nearby: around which place, e.g. 'FC Road, Pune'. Omit for here."},
                "limit": {"type": "integer", "description": "How many nearby results (1-10, default 5)."},
            },
        },
    },
}


# -- the network ---------------------------------------------------------------

def _get(url: str, params: dict | None = None):
    from shani_chronoa.netjson import get_json
    return get_json(COMPONENT, url, params)


_last_call: dict = {}


def _polite(host: str, gap: float = 1.1) -> None:
    """Wait until `gap` seconds have passed since the last request to `host`,
    from this process or any other Chronoa process. Never raises: if the lock
    file cannot be used, the in-process timer still holds within this call."""
    now = time.monotonic()
    wait = _last_call.get(host, -1e9) + gap - now
    if wait > 0:
        time.sleep(wait)
    try:
        import fcntl

        from shani_chronoa import files
        directory = files.state_home() / "shani-chronoa"
        files.ensure_private_dir(directory)
        path = directory / f"osm-{re.sub(r'[^a-z0-9.]', '_', host)}.last"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = os.read(fd, 64).decode("ascii", "ignore").strip()
            last = float(raw) if raw else 0.0
            wait = last + gap - time.time()
            if 0 < wait <= gap:
                time.sleep(wait)
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, f"{time.time():.3f}".encode())
        finally:
            os.close(fd)  # releases the flock
    except (OSError, ValueError, ImportError):
        pass
    _last_call[host] = time.monotonic()


def geocode(name: str, near: tuple | None = None) -> "tuple[float, float, str] | str":
    """(lat, lon, display name) for `name`, or a sentence saying it was not found.
    `near` (lat, lon) biases the search towards that point without restricting it."""
    params = {"q": name, "format": "jsonv2", "limit": 1}
    if near:
        lat, lon = near
        params.update(viewbox=f"{lon - 0.5:.4f},{lat + 0.5:.4f},{lon + 0.5:.4f},{lat - 0.5:.4f}", bounded=0)
    _polite("nominatim.openstreetmap.org")
    hits = _get(NOMINATIM, params)
    if not isinstance(hits, list) or not hits:
        return f"OpenStreetMap does not know a place called '{name}'."
    h = hits[0]
    return float(h["lat"]), float(h["lon"]), str(h.get("display_name") or name)


def _here() -> "tuple[float, float] | str":
    from shani_chronoa.senses import location
    config = ChronoaConfig()
    if not config.sense_allowed("location"):
        return f"I can't use this computer's location: {config.sense_allowed_reason('location')}."
    pos, why = location.locate()
    if pos is None:
        return f"This computer's location is not available: {why}."
    return pos[0], pos[1]


# -- formatting ----------------------------------------------------------------

def km(metres: float) -> str:
    if metres < 1000:
        return f"{int(round(metres, -1)) or int(metres)} m"
    return f"{metres / 1000:.1f} km"


def duration(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def haversine(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _short(display: str, parts: int = 3) -> str:
    return ", ".join(display.split(", ")[:parts])


def _step(s: dict) -> str:
    m = s.get("maneuver") or {}
    kind, mod = m.get("type", ""), m.get("modifier", "")
    road = s.get("name") or s.get("ref") or ""
    onto = f" onto {road}" if road else ""
    if kind == "depart":
        text = f"Start{' on ' + road if road else ''}"
    elif kind == "arrive":
        return "Arrive at the destination"
    elif kind in ("roundabout", "rotary", "roundabout turn"):
        exit_no = m.get("exit")
        text = f"At the roundabout take the {_ordinal(exit_no)} exit{onto}" if exit_no else f"Go through the roundabout{onto}"
    elif kind in ("new name", "continue"):
        text = f"Continue{(' ' + mod) if mod and mod != 'straight' else ''}{onto}"
    elif kind == "turn":
        text = f"Turn {mod}{onto}" if mod and mod != "straight" else f"Go straight{onto}"
    elif kind in ("merge", "on ramp", "off ramp", "fork", "end of road"):
        verb = {"on ramp": "Take the ramp", "off ramp": "Take the exit", "fork": "Keep",
                "merge": "Merge", "end of road": "At the end of the road turn"}[kind]
        text = f"{verb}{(' ' + mod) if mod else ''}{onto}"
    else:
        text = f"{kind.capitalize() or 'Continue'}{(' ' + mod) if mod else ''}{onto}"
    dist = s.get("distance")
    return text + (f", then go {km(dist)}" if dist else "")


def _ordinal(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# -- actions -------------------------------------------------------------------

def _text(arguments: dict, key: str) -> str:
    v = arguments.get(key)
    return v.strip() if isinstance(v, str) else ""


def _mode(arguments: dict) -> "str | tuple[str]":
    raw = _text(arguments, "mode").lower()
    if not raw:
        return "car"
    if raw in _MODES:
        return _MODES[raw]
    if any(t in raw for t in _NO_TRANSIT):
        return (f"OpenStreetMap routing here covers car, walking and cycling only - there is no "
                f"public transport or '{raw}' routing.",)
    return (f"I don't know the travel mode '{raw}'; say car, foot or bike.",)


def _endpoints(arguments: dict) -> "tuple | str":
    """((lat, lon, label) origin, (lat, lon, label) destination) or a sentence."""
    dest_name = _text(arguments, "destination") or _text(arguments, "place")
    if not dest_name:
        return "Where to? No destination was given."
    origin_name = _text(arguments, "origin")
    here = None
    if not origin_name or origin_name.lower() in ("here", "my location", "current location", "me"):
        here = _here()
        if isinstance(here, str):
            return f"Where from? {here} Say the starting place too, e.g. 'from Mumbai'."
        origin = (here[0], here[1], "your location")
    else:
        found = geocode(origin_name)
        if isinstance(found, str):
            return found
        origin = (found[0], found[1], _short(found[2]))
    found = geocode(dest_name, near=(origin[0], origin[1]))
    if isinstance(found, str):
        return found
    return origin, (found[0], found[1], _short(found[2]))


def _route(arguments: dict, steps: bool) -> str:
    mode = _mode(arguments)
    if isinstance(mode, tuple):
        return mode[0]
    ends = _endpoints(arguments)
    if isinstance(ends, str):
        return ends
    (alat, alon, alabel), (blat, blon, blabel) = ends
    _polite("routing.openstreetmap.de")
    data = _get(ROUTER.format(mode=mode, coords=f"{alon:.6f},{alat:.6f};{blon:.6f},{blat:.6f}"),
                {"overview": "false", "steps": "true" if steps else "false"})
    if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
        why = (data or {}).get("message") or (data or {}).get("code") or "no route"
        return f"OpenStreetMap could not find a {mode} route from {alabel} to {blabel}: {why}."
    r = data["routes"][0]
    verb = {"car": "Driving", "foot": "Walking", "bike": "Cycling"}[mode]
    out = [f"{verb} from {alabel} to {blabel}: {km(r['distance'])}, about {duration(r['duration'])}.",
           NO_TRAFFIC]
    if steps:
        legs = r.get("legs") or [{}]
        all_steps = [s for leg in legs for s in (leg.get("steps") or [])]
        shown = [_step(s) for s in all_steps[:12]]
        if shown:
            out.append("Directions:\n" + "\n".join(f"{i}. {t}." for i, t in enumerate(shown, 1)))
            if len(all_steps) > len(shown):
                out.append(f"({len(all_steps) - len(shown)} more steps; say 'open it on the map' for the full route.)")
    out.append(ATTRIBUTION)
    return "\n".join(out)


def _kind(what: str) -> "tuple[str, str] | None":
    w = re.sub(r"[^a-z ]", " ", what.lower())
    w = " ".join(t for t in w.split() if t not in ("nearest", "nearby", "closest", "a", "an", "the", "some", "near", "me", "good", "best"))
    for cand in (w, re.sub(r"(es|s)$", "", w), re.sub(r"s$", "", w), re.sub(r"ies$", "y", w)):
        if cand in _KINDS:
            return _KINDS[cand]
    return None


def _nearby(arguments: dict) -> str:
    what = _text(arguments, "what") or _text(arguments, "place")
    if not what:
        return "Nearby what? Say a kind of place, e.g. 'petrol pump' or 'hotels'."
    try:
        limit = max(1, min(10, int(arguments.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5
    near_name = _text(arguments, "near")
    if near_name and near_name.lower() not in ("here", "me", "my location"):
        found = geocode(near_name)
        if isinstance(found, str):
            return found
        centre, label = (found[0], found[1]), _short(found[2])
    else:
        here = _here()
        if isinstance(here, str):
            return f"Near where? {here} Name a place, e.g. 'hotels near FC Road, Pune'."
        centre, label = here, "you"

    tag = _kind(what)
    rows = []
    if tag:
        k, v = tag
        for radius in (3000, 10000):
            query = (f'[out:json][timeout:20];nwr["{k}"="{v}"](around:{radius},{centre[0]:.6f},{centre[1]:.6f});'
                     f"out center tags 60;")
            data = _get(OVERPASS, {"data": query})
            for el in (data or {}).get("elements", []):
                lat = el.get("lat", (el.get("center") or {}).get("lat"))
                lon = el.get("lon", (el.get("center") or {}).get("lon"))
                if lat is None or lon is None:
                    continue
                tags = el.get("tags") or {}
                name = tags.get("name:en") or tags.get("name") or tags.get("brand") or f"unnamed {what.rstrip('s')}"
                street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
                rows.append((haversine(centre, (lat, lon)), name, street))
            if rows:
                break
    else:
        lat, lon = centre
        _polite("nominatim.openstreetmap.org")
        hits = _get(NOMINATIM, {"q": what, "format": "jsonv2", "limit": 20, "bounded": 1,
                                "viewbox": f"{lon - 0.08:.4f},{lat + 0.08:.4f},{lon + 0.08:.4f},{lat - 0.08:.4f}"})
        for h in hits if isinstance(hits, list) else []:
            name = h.get("name") or _short(h.get("display_name", ""), 1)
            rows.append((haversine(centre, (float(h["lat"]), float(h["lon"]))), name,
                         _short(h.get("display_name", ""), 3)))
    if not rows:
        return f"OpenStreetMap has no {what} mapped within 10 km of {label}. {ATTRIBUTION}"
    rows.sort(key=lambda r: r[0])
    lines = [f"{i}. {name}{' - ' + street if street and street != name else ''}: {km(d)}"
             for i, (d, name, street) in enumerate(rows[:limit], 1)]
    return (f"Nearest {what} to {label} (straight-line distance, not by road):\n" + "\n".join(lines)
            + f"\n{ATTRIBUTION}")


def _find(arguments: dict) -> str:
    name = _text(arguments, "place") or _text(arguments, "destination")
    if not name:
        return "Which place? No place was given."
    found = geocode(name)
    if isinstance(found, str):
        return found
    lat, lon, display = found
    return f"{display} - at {lat:.5f}, {lon:.5f}. {ATTRIBUTION}"


def _open(arguments: dict) -> str:
    launcher = "xdg-open"
    if shutil.which(launcher) is None:
        return "Could not open the map: xdg-open is not installed, so nothing was launched. On Arch it comes from xdg-utils."
    if _text(arguments, "destination") and (_text(arguments, "origin") or not _text(arguments, "place")):
        mode = _mode(arguments)
        if isinstance(mode, tuple):
            return mode[0]
        ends = _endpoints(arguments)
        if isinstance(ends, str):
            return ends
        (alat, alon, alabel), (blat, blon, blabel) = ends
        url = OSM_ROUTE.format(mode=mode, a=f"{alat:.5f},{alon:.5f}", b=f"{blat:.5f},{blon:.5f}")
        what = f"the {mode} route from {alabel} to {blabel}"
    else:
        name = _text(arguments, "place") or _text(arguments, "destination")
        if not name:
            return "Which place should I open on the map? None was given."
        found = geocode(name)
        if isinstance(found, str):
            return found
        lat, lon, display = found
        url = OSM_PLACE.format(lat=lat, lon=lon, zoom=17)
        what = _short(display)
    from shani_chronoa import egress
    try:
        proc = subprocess.run([launcher, url], capture_output=True, text=True, timeout=20, check=False)
    except subprocess.TimeoutExpired:
        egress.record(COMPONENT, url, privacy_mode=egress.privacy_mode_enabled(), purpose="open-in-browser")
        return f"Asked the desktop to open {what} on OpenStreetMap, but it did not answer in 20s."
    except OSError as exc:
        return f"Could not open the map: {exc}"
    # The browser, not this process, makes the request - but the coordinates in
    # the URL leave the machine because of this call, so the ledger says so.
    egress.record(COMPONENT, url, privacy_mode=egress.privacy_mode_enabled(), purpose="open-in-browser")
    status = proc.returncode
    if status != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return f"The desktop refused to open the map (exit {status})" + (f": {detail[-1]}." if detail else ".")
    return f"Opened {what} on OpenStreetMap in your browser ({url})."


def _action(arguments: dict) -> str:
    action = _text(arguments, "action").lower().replace(" ", "_").replace("-", "_")
    if action in ("route", "navigate", "directions_to"):
        action = "directions"
    if action in ("distance", "eta", "how_long", "duration", "traffic"):
        action = "travel_time"
    if action in ("search", "where", "locate", "lookup"):
        action = "find"
    if action in ("show", "open_map", "map"):
        action = "open"
    if action in ("nearest", "near", "around"):
        action = "nearby"
    if action:
        return action
    if _text(arguments, "what"):
        return "nearby"
    if _text(arguments, "destination"):
        return "directions"
    return "find"


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "The maps request was not understood: its arguments were not a set of named values."
    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"Maps are not available: {config.sense_allowed_reason('web')}."
    action = _action(arguments)
    handlers = {"find": _find, "nearby": _nearby, "open": _open,
                "directions": lambda a: _route(a, steps=True),
                "travel_time": lambda a: _route(a, steps=False)}
    handler = handlers.get(action)
    if handler is None:
        return f"Unknown maps action '{action}'; use one of: {', '.join(_ACTIONS)}."
    try:
        return handler(arguments)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        return f"Could not reach the map service: {e.__class__.__name__}: {e}"


SKILLS = [Skill(name="maps", schema=SCHEMA, run=_run)]
