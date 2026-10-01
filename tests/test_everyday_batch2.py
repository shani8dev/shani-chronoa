"""date_math, random_pick, generate_password, my_ip_address, do_not_disturb,
read_document, convert_media, power_action.

power_action and do_not_disturb never reach the real system here: every
subprocess they could start is substituted.
"""

import json
import subprocess
from datetime import date

import pytest

from shani_chronoa.skills import convert_media as cm
from shani_chronoa.skills import date_math as dm
from shani_chronoa.skills import do_not_disturb as dnd
from shani_chronoa.skills import generate_password as gp
from shani_chronoa.skills import my_ip as ip
from shani_chronoa.skills import power_action as pa
from shani_chronoa.skills import random_pick as rp

TODAY = date(2026, 10, 1)


def _cp(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.mark.parametrize("args, want", [
    ({"operation": "days_until", "date": "25 December"}, "in 85 days"),
    ({"operation": "days_until", "date": "1 October"}, "today"),
    ({"operation": "add", "date": "today", "amount": 45, "unit": "days"}, "Sunday, 15 November 2026"),
    ({"operation": "add", "date": "2026-01-31", "amount": 1, "unit": "months"}, "Saturday, 28 February 2026"),
    ({"operation": "between", "date": "2026-01-01", "other_date": "2026-12-31"}, "364 days"),
    ({"operation": "weekday", "date": "15 August 1947"}, "a Friday"),
    ({"operation": "days_until", "date": "1 Jan"}, "2027"),   # no year: the next one
])
def test_dates(args, want):
    assert want in dm._run(args, today=TODAY)


def test_dice_and_numbers_stay_in_range():
    for _ in range(200):
        n = int(rp._run({"kind": "number", "low": 3, "high": 5}).rstrip("."))
        assert 3 <= n <= 5
    rolls = rp._run({"kind": "dice", "dice": "3d6"})
    assert rolls.startswith("Rolled ") and "total" in rolls


def test_the_password_is_never_in_the_reply(monkeypatch):
    got = {}
    monkeypatch.setattr("shani_chronoa.skills.clipboard._run_set_clipboard",
                        lambda a: got.update(a) or "Copied to the clipboard (wayland).")
    out = gp._run({"kind": "password", "length": 24})
    assert got["text"] and got["text"] not in out and "on your clipboard" in out
    assert len(got["text"]) == 24


def test_a_clipboard_failure_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr("shani_chronoa.skills.clipboard._run_set_clipboard",
                        lambda a: "Could not write the clipboard: wl-copy disappeared.")
    assert "could not put it on the clipboard" in gp._run({})


def test_local_addresses_from_ip_json(monkeypatch):
    data = json.dumps([{"ifname": "lo", "flags": ["UP"], "addr_info": [{"local": "127.0.0.1", "scope": "host"}]},
                       {"ifname": "wlan0", "flags": ["UP"], "addr_info": [{"local": "192.168.1.7", "scope": "global"}]}])
    monkeypatch.setattr(ip.subprocess, "run", lambda *a, **k: _cp(data))
    assert ip.local_addresses() == ["wlan0: 192.168.1.7"]


def test_dnd_flips_gnomes_banner_switch(monkeypatch):
    calls = []
    monkeypatch.setattr(dnd.subprocess, "run", lambda argv, **k: calls.append(argv) or _cp("true"))
    assert dnd._run({"enabled": True}) == "Do Not Disturb is now on."
    assert calls[-1][-3:] == ["org.gnome.desktop.notifications", "show-banners", "false"]


def test_power_is_refused_while_its_switch_is_off(monkeypatch):
    ran = []
    monkeypatch.setattr(pa.subprocess, "run", lambda *a, **k: ran.append(a) or _cp())
    monkeypatch.setattr(pa, "ChronoaConfig", lambda: type("C", (), {"get_bool": lambda s, k, d=False: False})())
    assert pa._run({"action": "shutdown"}).startswith("Not done") and not ran


def test_shutdown_is_scheduled_a_minute_ahead_and_cancellable(monkeypatch):
    ran = []
    monkeypatch.setattr(pa.subprocess, "run", lambda argv, **k: ran.append(argv) or _cp())
    monkeypatch.setattr(pa.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(pa, "ChronoaConfig", lambda: type("C", (), {"get_bool": lambda s, k, d=False: True})())
    out = pa._run({"action": "shutdown"})
    assert ran[-1][:3] == ["shutdown", "-P", "+1"] and "cancel" in out
    pa._run({"action": "cancel"})
    assert ran[-1] == ["shutdown", "-c"]


@pytest.mark.skipif(not cm.shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_convert_media_really_converts_and_never_overwrites(tmp_path, monkeypatch):
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(src)], check=True)
    monkeypatch.setattr("shani_chronoa.files.resolve", lambda raw: __import__("pathlib").Path(raw))
    first = cm._run({"path": str(src), "to": "ogg"})
    second = cm._run({"path": str(src), "to": "ogg"})
    assert (tmp_path / "tone.ogg").stat().st_size > 0 and (tmp_path / "tone-1.ogg").exists()
    assert "original is unchanged" in first and "tone-1.ogg" in second
