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
