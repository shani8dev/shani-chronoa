"""encode_text, convert_color, find_emoji, calendar_month, stopwatch,
bluetooth_devices, vpn_control, install_app. The system tools are substituted;
nothing here pairs, connects or installs anything for real."""

import subprocess
from datetime import date

import pytest

from shani_chronoa.skills import bluetooth_devices as bt
from shani_chronoa.skills import calendar_month as cm
from shani_chronoa.skills import convert_color as cc
from shani_chronoa.skills import encode_text as et
from shani_chronoa.skills import find_emoji as fe
from shani_chronoa.skills import install_app as ia
from shani_chronoa.skills import stopwatch as sw
from shani_chronoa.skills import vpn_control as vpn


def _cp(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class _Cfg:
    def __init__(self, on):
        self.on = on

    def get_bool(self, key, default=False):
        return self.on


@pytest.mark.parametrize("scheme", ["base64", "url", "hex", "binary", "rot13"])
def test_encodings_round_trip(scheme):
    text = "Shani OS & friends ✓"
    enc = et._run({"text": text, "scheme": scheme})
    assert et._run({"text": enc, "scheme": scheme, "direction": "decode"}) == text


def test_morse():
    assert et._run({"text": "SOS", "scheme": "morse"}) == "... --- ..."
    assert et._run({"text": "... --- ... / .... ..", "scheme": "morse", "direction": "decode"}) == "SOS HI"


def test_bad_base64_is_said():
    assert "not valid base64" in et._run({"text": "!!!", "scheme": "base64", "direction": "decode"})


@pytest.mark.parametrize("given, want", [("#ff8800", "rgb(255, 136, 0)"), ("teal", "#008080"),
                                         ("hsl(120, 100%, 25%)", "'green'"), ("rgb(0,0,255)", "'blue'")])
def test_colours(given, want):
    assert want in cc._run({"color": given})


@pytest.mark.parametrize("name, want", [("red heart", "❤️"), ("thumbs up", "\U0001F44D"),
                                        ("indian rupee", "₹"), ("blue heart", "\U0001F499")])
def test_emoji(name, want):
    assert fe.search(name)[0][0] == want


def test_the_calendar_marks_today():
    out = cm._run({}, today=date(2026, 10, 1))
    assert "October 2026" in out and "[ 1]" in out and "Today: Thursday 01 October" in out


def test_stopwatch(tmp_path, monkeypatch):
    monkeypatch.setattr("shani_chronoa.files.data_home", lambda: tmp_path)
    assert sw._run({"action": "read"}, now=0).startswith("The stopwatch is not running")
    sw._run({"action": "start"}, now=100.0)
    assert sw._run({"action": "lap"}, now=165.4) == "Lap 1: 1 min 5.4 s."
    assert sw._run({"action": "stop"}, now=200.0).startswith("Stopped at 1 min 40.0 s.")


def test_bluetooth_connects_the_named_device(monkeypatch):
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if argv[1:3] == ["devices", "Paired"]:
            return _cp("Device AA:BB:CC:DD:EE:01 Sony WH-1000XM5\nDevice AA:BB:CC:DD:EE:02 Keyboard K380\n")
        if argv[1] == "info":
            return _cp("Connected: no\n")
        return _cp("Attempting to connect to AA:BB:CC:DD:EE:01\nConnection successful\n")
    monkeypatch.setattr(bt.shutil, "which", lambda b: "/usr/bin/bluetoothctl")
    monkeypatch.setattr(bt.subprocess, "run", run)
    assert bt._run({"action": "connect", "name": "sony"}) == "Connected Sony WH-1000XM5."
    assert calls[-1] == ["bluetoothctl", "connect", "AA:BB:CC:DD:EE:01"]


def test_vpn_and_installs_refuse_without_their_switch(monkeypatch):
    ran = []
    monkeypatch.setattr(vpn.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(vpn.subprocess, "run", lambda argv, **k: ran.append(argv) or _cp("Work:vpn:no\n"))
    monkeypatch.setattr(vpn, "ChronoaConfig", lambda: _Cfg(False))
    assert vpn._run({"action": "up", "name": "work"}).startswith("Not done")
    assert all("up" not in a for a in ran)
    monkeypatch.setattr(ia.shutil, "which", lambda b: "/usr/bin/flatpak")
    monkeypatch.setattr(ia.subprocess, "run", lambda argv, **k: ran.append(argv) or _cp())
    monkeypatch.setattr(ia, "ChronoaConfig", lambda: _Cfg(False))
    assert ia._run({"action": "install", "app_id": "org.videolan.VLC"}).startswith("Not done")
    assert not any("install" in a for a in ran)


def test_install_needs_an_exact_app_id(monkeypatch):
    monkeypatch.setattr(ia.shutil, "which", lambda b: "/usr/bin/flatpak")
    monkeypatch.setattr(ia, "ChronoaConfig", lambda: _Cfg(True))
    assert "exact ID" in ia._run({"action": "install", "app_id": "vlc"})
    assert "exact ID" in ia._run({"action": "install", "app_id": "org.x.Y; rm -rf ~"})
