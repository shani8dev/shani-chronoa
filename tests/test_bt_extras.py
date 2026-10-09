"""AVRCP target, battery provider, MAP watcher: the parts testable without radios."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))
pytest.importorskip("gi")

from shani_chronoa import avrcp_target as at  # noqa: E402
from shani_chronoa import bluez_battery as bb  # noqa: E402
from shani_chronoa import map_notify as mn  # noqa: E402


def test_media_control_status_sentences_become_avrcp_state():
    assert at.parse_status("Firefox is playing: Song by Band.") == ("Playing", "Song by Band")
    assert at.parse_status("acer ZX is paused.") == ("Paused", "")
    assert at.parse_status("No media player is running.") == ("Stopped", "")
    assert at.parse_status("x is playing: A. (unverified - ...)") == ("Playing", "A")


def test_a_remote_press_runs_the_matching_media_action():
    ran = []
    t = at.AvrcpTarget(run_action=lambda a: ran.append(a) or "x is playing: A by B.")
    for method in ("PlayPause", "Next", "Previous", "Stop", "Bogus"):
        t.on_method(method)
    end = time.monotonic() + 2
    while len([a for a in ran if a != "status"]) < 4 and time.monotonic() < end:
        time.sleep(0.02)
    assert sorted(a for a in ran if a != "status") == ["next", "previous", "stop", "toggle"]
    assert t.pressed == ["PlayPause", "Next", "Previous", "Stop"]


def test_the_player_metadata_splits_title_and_artist():
    t = at.AvrcpTarget(run_action=lambda a: "")
    t.state, t.title = "Playing", "Song by Band"
    p = t._props()
    assert p["PlaybackStatus"].unpack() == "Playing"
    meta = p["Metadata"].unpack()
    assert meta["xesam:title"] == "Song" and meta["xesam:artist"] == ["Band"]


def test_battery_provider_points_at_the_bluez_device_and_clamps():
    p = bb.BatteryPublisher()
    p.levels["B3:69:73:62:B2:B6"] = 74
    props = p._props("B3:69:73:62:B2:B6")
    assert props["Device"].unpack() == "/org/bluez/hci0/dev_B3_69_73_62_B2_B6"
    assert props["Percentage"].unpack() == 74 and props["Source"].unpack() == "Shani Chronoa"
    assert p._child("B3:69:73:62:B2:B6") == bb.ROOT + "/dev_B3_69_73_62_B2_B6"


def test_new_message_alerts_need_both_switches_and_screen_the_text():
    class Cfg:
        def __init__(self, keys):
            self.keys = keys

        def get_bool(self, k, d=False):
            return k in self.keys
    assert not mn.MessageWatcher(Cfg({"phone-control-enabled"}))._allowed()
    shown = []
    w = mn.MessageWatcher(Cfg(set(mn.KEYS)), notify=lambda t, b: shown.append((t, b)))

    class Bus:
        pass
    w._inbox = lambda bus, session: {"/s/m1": {"Sender": "Asha‮", "Subject": "hi⁦there"}}
    w._announce(Bus(), "/s")
    w._announce(Bus(), "/s")                       # the same message is never shown twice
    assert shown == [("Message from Asha", "hithere")]
