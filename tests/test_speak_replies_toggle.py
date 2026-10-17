"""The speaker toggle in the main window silences replies, and only replies.

Before 2026-10-15 the one way to stop Chronoa reading replies aloud was
Settings → Voice, on `notification-enabled` - a switch that also silenced
timers. `speak-replies` is its own key, behind a toggle beside the microphone
(`app.speak-replies`). What must hold, driven through the real `VoiceMixin`
methods:

- off: no speech queue is made for the next turn, and a question is not read
  aloud either;
- turning it off mid-reply stops the audio **without** cancelling the turn -
  that is Esc's job, and muting must not throw the answer away;
- the window's toggle is bound to the action, so it shows the setting.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from shani_chronoa.app.voice import VoiceMixin  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402


class _Available:
    def is_available(self):
        return True

    def synthesize_to_bytes(self, _text):
        return b""


class _Player(_Available):
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1


class _Queue:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _Future:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True


class _App(VoiceMixin):
    def __init__(self):
        self.config = ChronoaConfig()
        self.tts = _Available()
        self.player = _Player()
        self.barge_in_monitor = _Available()
        self.window = None
        self._turn_speech = _Queue()
        self._turn_future = _Future()

    def _mark(self, _name):
        pass


def _action(app):
    action = Gio.SimpleAction.new_stateful(
        "speak-replies", None, GLib.Variant("b", app.config.speak_replies))
    action.connect("change-state", app._on_speak_replies)
    return action


def test_off_means_no_speech_for_the_next_turn():
    app = _App()
    app.config.set("speak-replies", "true")
    assert app._new_speech_queue() is not None, "on, and nothing would be spoken"
    _action(app).change_state(GLib.Variant("b", False))
    assert app.config.speak_replies is False
    assert app._new_speech_queue() is None, "muted, and the next reply was still queued to speak"


def test_muting_mid_reply_stops_the_voice_not_the_answer():
    app = _App()
    app.config.set("speak-replies", "true")
    queue, future = app._turn_speech, app._turn_future
    _action(app).change_state(GLib.Variant("b", False))
    assert app.player.stopped == 1 and queue.stopped, "the reply kept being said"
    assert not future.cancelled, "muting cancelled the turn - that is Esc, not mute"


def test_it_is_its_own_switch_not_the_timers_one():
    app = _App()
    app.config.set("notification-enabled", "true")
    _action(app).change_state(GLib.Variant("b", False))
    assert app.config.notification_enabled is True, "muting replies silenced timers too"


def test_the_window_toggle_is_bound_to_the_action():
    from shani_chronoa.gui import window as window_mod
    source = pathlib.Path(window_mod.__file__).read_text()
    assert 'Gtk.ToggleButton(action_name="app.speak-replies")' in source
    assert "row.append(self._speak_toggle())" in source
