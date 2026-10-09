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


def test_music_buttons_drive_the_player(monkeypatch):
    c, calls = _companion(monkeypatch)
    for arg, action in ((0, "toggle"), (1, "previous"), (2, "next"), (6, "play"), (7, "pause")):
        c.on_event(wc.CMD_PHONE_OPERATION, bytes([arg]))
        _settle(calls, len([x for x in calls if x[0] == "media_control"]) + 1)
        assert ("media_control", {"action": action}) in calls, (arg, calls)


def test_reject_on_the_watch_hangs_up_the_phone(monkeypatch):
    c, calls = _companion(monkeypatch)
    c.on_event(wc.CMD_PHONE_OPERATION, bytes([3]))
    _settle(calls, 1)
    assert ("bluetooth_call", {"action": "hangup"}) in calls


def test_camera_button_takes_a_photo(monkeypatch):
    c, calls = _companion(monkeypatch)
    c.on_event(wc.CMD_CAMERA, b"")
    _settle(calls, 2)
    assert ("take_photo", {}) in calls


def test_find_my_phone_rings_until_the_watch_says_stop(monkeypatch):
    c, calls = _companion(monkeypatch)
    started = []
    monkeypatch.setattr(c.ringer, "start", lambda: started.append("start"))
    monkeypatch.setattr(c.ringer, "stop", lambda: started.append("stop"))
    c.on_event(wc.CMD_FIND_MY_PHONE, b"\x00")
    c.on_event(wc.CMD_FIND_MY_PHONE, b"\x01")
    assert started == ["start", "stop"]
    assert any(x[0] == "notify" for x in calls)


def test_the_companion_stays_off_unless_both_switches_are_on():
    assert not wc.WatchCompanion(Cfg(**{wc.GATT_KEY: True}))._allowed()
    assert not wc.WatchCompanion(Cfg(**{wc.KEY: True}))._allowed()
    assert wc.WatchCompanion(Cfg(**{wc.KEY: True, wc.GATT_KEY: True}))._allowed()


def test_unsolicited_events_are_taken_out_of_the_inbox_and_replies_kept(monkeypatch):
    c, calls = _companion(monkeypatch)
    monkeypatch.setattr(c.ringer, "start", lambda: None)

    class W:
        inbox = [(wc.CMD_FIND_MY_PHONE, b"\x00"), (38, b"\x10\x27\x00\x00")]
    w = W()
    c.handle_events(w)
    assert w.inbox == [(38, b"\x10\x27\x00\x00")]


def test_ai_voice_on_the_watch_starts_and_stops_listening_once(monkeypatch):
    """Measured: 0xF9 [01 01 00] and [01 01 01] arrive together on a press, [02 00] at the end."""
    calls = []
    c = wc.WatchCompanion(Cfg(), actions=lambda n, a: "ok", on_voice=calls.append)
    for body in ("010100", "010101"):
        c.on_event(wc.CMD_AI_VOICE, bytes.fromhex(body))
    c._voice_at -= 5                                   # the end comes seconds later
    c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))
    assert calls == [True, False]


def test_with_no_session_each_lone_press_toggles_listening(monkeypatch):
    """Measured with the watch showing "not connected": every press is only [02 00]."""
    calls = []
    c = wc.WatchCompanion(Cfg(), actions=lambda n, a: "ok", on_voice=calls.append)
    c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))
    c._voice_at -= 5
    c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))
    assert calls == [True, False]


def test_repeat_give_up_packets_do_not_re_open_the_microphone():
    """The watch repeats [02 00] every 5-10s with nobody touching it (measured 2026-10-08).

    Read as a plain toggle that looped the microphone for ten minutes, one
    "No audio captured" per cycle. The guard is therefore on elapsed time, not on
    the packet - and a press after a real pause must still work.
    """
    calls = []
    c = wc.WatchCompanion(Cfg(), actions=lambda n, a: "ok", on_voice=calls.append)
    now = 1000.0
    real = time.monotonic
    time.monotonic = lambda: now
    try:
        c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))       # the press
        assert calls == [True]
        now += 5
        c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))       # it ends the turn
        assert calls == [True, False]
        for gap in (6.0, 7.0, 5.0):                              # then repeats
            now += gap
            c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))
        assert calls == [True, False], f"re-armed per packet: {calls}"
        now += 20                                                 # a real second press
        c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))
        assert calls == [True, False, True]
    finally:
        time.monotonic = real


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


def test_the_watch_is_not_pushed_anything_unless_asked():
    """Now-playing and weather are off by default (the measured traffic was 68+123 every 5s).

    Both used to be unconditional, and they are the only writes the companion
    makes that the watch did not ask for. Driven through `handle_events` - the
    path an inbound packet actually takes - because calling the private helpers
    directly would not prove the gate is reachable.
    """
    fetched = []
    c = wc.WatchCompanion(Cfg(), actions=lambda n, a: "ok")
    assert not c._switch(wc.NOWPLAYING_KEY), "now-playing defaults on"
    assert not c._switch(wc.WEATHER_KEY), "weather defaults on"
    c.fetch_weather = lambda: fetched.append(1)

    w = _FakeWatch()
    w.inbox = [(wc.CMD_WANTS_WEATHER, b"")]        # the watch asks, as it does on connect
    c.handle_events(w)
    time.sleep(0.2)                                # the fetch runs on its own thread
    # Asserted on the fetch, not on the packets: the fetch runs on a thread and
    # only queues packets, so a packet assertion passes whether or not it ran -
    # which is how this test was green against the switch being ignored.
    assert not fetched, "a weather fetch was started with the switch off"
    assert w.sent == [], f"answered the watch with {w.sent} while the switch is off"


def test_now_playing_reaches_the_watch_when_it_is_asked_for():
    """The gate must open, not merely refuse: the switch has to actually work both ways."""
    c = wc.WatchCompanion(Cfg(**{wc.NOWPLAYING_KEY: True}), actions=lambda n, a: "ok")
    c.now_playing = lambda: 'Test Track is playing'
    c.last_track = None
    w = _FakeWatch()
    c.push_music(w)
    assert w.sent, "the switch is on and nothing was sent"


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


def test_the_watch_button_is_documented_as_using_this_computers_microphone():
    """The finding is in the code, not only in a commit message.

    Measured 2026-10-08: the watch advertises Handsfree/Audio Sink/A2DP in SDP
    but bluez exposes only MediaControl1 for it and never creates an audio
    card, so no watch audio can reach this machine. Da Fit's manual describes
    the button as waking the assistant *on the phone*. Without this, the next
    reader assumes a missing feature rather than the watch's design.
    """
    text = Path(wc.__file__).read_text()
    # The claim itself first: softening it ("may work") is what a later edit
    # would do, and it left the rest of this test green when tried.
    assert "microphone cannot reach this computer" in text, \
        "the finding has been softened or removed - re-measure before changing it"
    assert "wake up the AI voice" in text, "the manual's own wording is not recorded"
    assert "MediaControl1" in text, "what bluez actually exposes is not recorded"
    assert "this computer's** microphone" in text, "the code does not say whose microphone a press uses"


def test_the_watchs_own_packet_right_after_connecting_is_not_a_press(monkeypatch):
    """Measured: the real app opened the microphone 9 s after the companion
    connected, with nobody touching the watch - the guard's clock began at 0."""
    import time
    calls = []
    c = wc.WatchCompanion(Cfg(), actions=lambda n, a: "ok", on_voice=calls.append)
    c._voice_packet_at = time.monotonic()          # what hold() now does on connect
    c.on_event(wc.CMD_AI_VOICE, bytes.fromhex("0200"))
    assert calls == []
