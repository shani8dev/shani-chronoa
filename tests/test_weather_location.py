"""get_weather (Open-Meteo) and get_location (gpsd, then GeoClue).

The network and the two location tools are substituted with their real
output shapes; the live API was exercised by hand (Mumbai: 'clear sky,
29.8 C ...'). The gates are not substituted: they are the point.
"""

import json
import subprocess

import pytest

from shani_chronoa.senses import location as loc
from shani_chronoa.skills import weather as w


class _Cfg:
    def __init__(self, allowed):
        self.allowed = set(allowed)

    def sense_allowed(self, s):
        return s in self.allowed

    def sense_allowed_reason(self, s):
        return "it is switched off" if s not in self.allowed else ""


GEO = {"results": [{"name": "Mumbai", "admin1": "Maharashtra", "country": "India",
                    "latitude": 19.07, "longitude": 72.88}]}
FC = {"current": {"temperature_2m": 29.8, "apparent_temperature": 36.4, "relative_humidity_2m": 82,
                  "precipitation": 0, "weather_code": 0, "wind_speed_10m": 7.5},
      "daily": {"temperature_2m_max": [34.9], "temperature_2m_min": [25.4],
                "precipitation_probability_max": [25], "weather_code": [51]}}


@pytest.fixture
def api(monkeypatch):
    calls = []

    def get(url, params):
        calls.append((url, params))
        return GEO if url == w.GEOCODE else FC
    monkeypatch.setattr(w, "_get", get)
    return calls


def test_a_named_place(monkeypatch, api):
    monkeypatch.setattr(w, "ChronoaConfig", lambda: _Cfg({"web"}))
    out = w._run({"place": "Mumbai"})
    assert "Mumbai, Maharashtra, India" in out and "29.8°C" in out and "light drizzle" in out
    assert api[0][1]["name"] == "Mumbai" and api[1][1]["latitude"] == "19.0700"


def test_no_web_consent_means_no_request(monkeypatch, api):
    monkeypatch.setattr(w, "ChronoaConfig", lambda: _Cfg(set()))
    assert "not available" in w._run({"place": "Mumbai"}) and api == []


def test_here_without_location_consent_asks_for_a_place(monkeypatch, api):
    monkeypatch.setattr(w, "ChronoaConfig", lambda: _Cfg({"web"}))
    out = w._run({})
    assert out.startswith("Which place?") and api == []


def test_here_with_a_fix_uses_it(monkeypatch, api):
    monkeypatch.setattr(w, "ChronoaConfig", lambda: _Cfg({"web", "location"}))
    monkeypatch.setattr(loc, "locate", lambda: ((18.52, 73.86, 30.0, "GPS receiver (gpsd)"), ""))
    out = w._run({})
    assert out.startswith("Weather now:") and api[0][0] == w.FORECAST and api[0][1]["latitude"] == "18.5200"


def _proc(stdout, rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


def test_gpsd_fix_is_used_first(monkeypatch):
    lines = [json.dumps({"class": "DEVICES", "devices": [{"path": "/dev/ttyUSB0"}]}),
             json.dumps({"class": "TPV", "mode": 3, "lat": 18.5204, "lon": 73.8567, "eph": 12.0})]
    monkeypatch.setattr(loc.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(loc.subprocess, "run", lambda *a, **k: _proc("\n".join(lines)))
    pos, why = loc.locate()
    assert pos[:2] == (18.5204, 73.8567) and pos[3] == "GPS receiver (gpsd)"


def test_geoclue_when_there_is_no_gps(monkeypatch):
    def run(argv, **k):
        if argv[0] == "gpspipe":
            return _proc("", rc=1)
        return _proc("Latitude:    19.076000°\nLongitude:   72.877700°\nAccuracy:    1500.000000 meters\n"
                     "Description: WiFi\n")
    monkeypatch.setattr(loc.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(loc.os, "access", lambda p, m: True)
    monkeypatch.setattr(loc.subprocess, "run", run)
    pos, why = loc.locate()
    assert pos[:3] == (19.076, 72.8777, 1500.0) and "GeoClue" in pos[3]


def test_neither_source_is_said_not_guessed(monkeypatch):
    monkeypatch.setattr(loc.shutil, "which", lambda b: None)
    monkeypatch.setattr(loc.os, "access", lambda p, m: False)
    pos, why = loc.locate()
    assert pos is None and "gpspipe" in why and "GeoClue" in why


def test_the_sense_refuses_without_consent(monkeypatch):
    monkeypatch.setattr(loc, "ChronoaConfig", lambda: _Cfg(set()))
    assert loc._run({}).startswith("Location is not permitted")


# --- the everyday skills added beside them ----------------------------------

from shani_chronoa.skills import convert_currency as cc  # noqa: E402
from shani_chronoa.skills import convert_units as cu  # noqa: E402
from shani_chronoa.skills import lookup as lk  # noqa: E402
from shani_chronoa.skills import media_control as mc  # noqa: E402
from shani_chronoa.skills import world_clock as wc  # noqa: E402


@pytest.mark.parametrize("place, zone", [("Tokyo", "Asia/Tokyo"), ("mumbai", "Asia/Kolkata"),
                                         ("New York", "America/New_York"), ("Europe/Paris", "Europe/Paris")])
def test_world_time_is_answered_offline(place, zone):
    assert wc.local_zone(place) == zone


def test_an_unknown_place_needs_web_consent(monkeypatch):
    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: _Cfg(set()))
    assert "needs the web" in wc._run({"place": "Nagpur"})


@pytest.mark.parametrize("v, a, b, want", [(100, "km", "miles", 62.1371), (98.6, "fahrenheit", "celsius", 37.0),
                                           (1, "gib", "mb", 1073.74), (2, "hours", "minutes", 120)])
def test_units_convert(v, a, b, want):
    out, err = cu.convert(v, a, b)
    assert err is None and abs(out - want) < 0.01


def test_units_of_different_kinds_do_not(monkeypatch):
    assert "don't convert" in cu._run({"value": 5, "from_unit": "kg", "to_unit": "litres"})


def test_currency_uses_iso_codes_and_names_the_rate(monkeypatch):
    seen = {}
    monkeypatch.setattr(cc, "ChronoaConfig", lambda: _Cfg({"web"}))
    monkeypatch.setattr("shani_chronoa.netjson.get_json",
                        lambda comp, url, params=None: seen.update(params=params) or {"rates": {"INR": 9633.0}, "date": "2026-10-01"})
    out = cc._run({"amount": 100, "from_currency": "dollars", "to_currency": "rupees"})
    assert seen["params"] == {"amount": 100.0, "from": "USD", "to": "INR"}
    assert "9,633.00 INR" in out and "2026-10-01" in out


def test_currency_without_web_consent(monkeypatch):
    monkeypatch.setattr(cc, "ChronoaConfig", lambda: _Cfg(set()))
    assert "not available" in cc._run({"amount": 1, "from_currency": "USD", "to_currency": "INR"})


def test_lookups_without_web_consent(monkeypatch):
    monkeypatch.setattr(lk, "ChronoaConfig", lambda: _Cfg(set()))
    assert "not available" in lk._wiki({"topic": "Pune"}) and "not available" in lk._define({"word": "x"})


def test_media_with_no_player(monkeypatch):
    monkeypatch.setattr(mc.shutil, "which", lambda b: "/usr/bin/gdbus")
    monkeypatch.setattr(mc, "players", lambda: [])
    assert mc._run({"action": "pause"}) == "No media player is running."


def test_media_pauses_the_playing_one_by_its_own_name(monkeypatch):
    calls = []

    def gdbus(*args):
        calls.append(args)
        if "PlaybackStatus" in args:
            return subprocess.CompletedProcess([], 0, "(<'Playing'>,)" if "org.mpris.MediaPlayer2.spotify" in args else "(<'Paused'>,)", "")
        if "Identity" in args:
            return subprocess.CompletedProcess([], 0, "(<'Spotify'>,)", "")
        return subprocess.CompletedProcess([], 0, "()", "")
    monkeypatch.setattr(mc.shutil, "which", lambda b: "/usr/bin/gdbus")
    monkeypatch.setattr(mc, "players", lambda: ["org.mpris.MediaPlayer2.firefox", "org.mpris.MediaPlayer2.spotify"])
    monkeypatch.setattr(mc, "_gdbus", gdbus)
    assert mc._run({"action": "pause"}) == "Done: pause in Spotify."
    assert any("org.mpris.MediaPlayer2.Player.Pause" in a for a in calls[-1])
