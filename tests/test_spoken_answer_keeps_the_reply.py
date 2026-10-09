"""Answering a spoken question must not cost the turn its spoken reply.

Measured 2026-10-08 in the real app, voice turn "remind me tomorrow at nine":
Chronoa asked "what time?" aloud, heard the answer, set the reminder - and then
sat on SPEAKING for six minutes in silence. Two defects:

1. Listening for the answer went through the barge-in path, which stops the
   turn's speech queue ("silence the rest of the reply"). The reply had not been
   said yet; it belonged to the same turn that asked.
2. The end of the turn set SPEAKING whenever a queue existed. A stopped queue
   had already reported done, so nothing ever cleared SPEAKING.
"""

import sys
import time

import pytest

pytest.importorskip("gi")
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import speech  # noqa: E402
from shani_chronoa.gui import AssistantState, ChronoaWindow  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    import shani_chronoa.senses.store as store_mod
    monkeypatch.setattr(store_mod, "DURABLE_FILE", tmp_path / "percepts" / "memory.jsonl")
    from shani_chronoa.app import ChronoaApplication
    a = ChronoaApplication()
    a._init_components()
    a.window = ChronoaWindow(a)
    monkeypatch.setattr(a, "_start_listening", lambda: None)  # no real microphone
    return a


def _queue():
    return speech.SpeechQueue(lambda text: b"", lambda wav: True)


def _pump(seconds=0.5):
    ctx, end = GLib.MainContext.default(), time.time() + seconds
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.01)


def test_listening_for_an_answer_leaves_the_reply_to_be_said(app):
    app._turn_speech = _queue()
    app.window._pending_question = lambda answer: None
    app._listen_for_answer()
    assert not app._turn_speech.stopped, "the turn's reply was silenced by listening for its answer"


def test_a_barge_in_still_silences_the_reply(app):
    """The control: talking over Chronoa must still stop what it is saying."""
    app._turn_speech = _queue()
    app._begin_listening()
    assert app._turn_speech.stopped


def test_a_stopped_queue_does_not_leave_the_window_speaking(app):
    app._turn_speech = _queue()
    app._turn_speech.stop()
    app._turn_marks = {"start": time.monotonic()}
    app._on_response_ready("Reminder set for tomorrow at 9 AM.")
    _pump()
    assert app.window.get_state() is not AssistantState.SPEAKING
