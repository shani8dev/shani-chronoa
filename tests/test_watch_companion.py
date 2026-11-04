"""The watch's own buttons, handled by the companion (no watch needed)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import watch_companion as wc  # noqa: E402


class Cfg:
    def __init__(self, **keys):
        self.keys = keys

    def get_bool(self, key, default=False):
        return self.keys.get(key, default)


def _companion(monkeypatch):
    calls = []
    c = wc.WatchCompanion(Cfg(), actions=lambda name, args: calls.append((name, args)) or "ok")
    monkeypatch.setattr(c, "_notify", lambda *a: calls.append(("notify", a)))
    return c, calls


def _settle(calls, n, seconds=2.0):
    end = time.monotonic() + seconds
    while len(calls) < n and time.monotonic() < end:
        time.sleep(0.02)




















class _FakeWatch:
    """A `moyoung.Watch` stand-in that records what would go on the wire."""

    proc = None

    def __init__(self):
        self.inbox = []
        self.sent = []

    def request(self, command, payload=b"", seconds=8.0, **kw):
        self.sent.append(command)

    def _pump(self, seconds):
        pass






def test_the_switches_default_to_off_in_the_schema():
    """A switch whose schema default was on would push before anyone looked."""
    from xml.etree import ElementTree

    schema = (Path(__file__).resolve().parents[1] / "usr/share/glib-2.0/schemas"
              / "org.shani.chronoa.gschema.xml")
    keys = {k.get("name"): k for k in ElementTree.parse(schema).getroot().iter("key") if k.get("name")}
    for name, why in (("watch-nowplaying-enabled", "two writes every five seconds"),
                      ("watch-weather-enabled", "a location read and a weather request")):
        assert name in keys, f"{name} is not in the schema at all"
        assert keys[name].findtext("default") == "false", f"{name} defaults to on ({why})"




