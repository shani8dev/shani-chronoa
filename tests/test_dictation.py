"""Dictation: a long turn, with nothing held down and no wake word.

An ordinary spoken turn is capped at twenty seconds, which is right for "what
time is it" and wrong for talking *at* the machine - a paragraph of notes, a
message to be written down, a meeting. That cap is why Chronoa could not
transcribe a conversation even though it already had `pw-record`, whisper.cpp and
a turn pipeline: it had all the parts and none of the ceiling.

So this is a second ceiling and a second silence, not a longer version of the
same one:

- **five minutes**, because a paragraph is not twenty seconds;
- **2.5 seconds** of silence to finish, because 1.2 cuts at every pause *inside*
  a paragraph rather than at its end;
- and it reports **when it was cut**, because a transcript that stops mid-sentence
  and says nothing reads as "that is everything I said".
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import audio  # noqa: E402
from shani_chronoa.app import voice  # noqa: E402


def test_an_ordinary_turn_stays_short_and_a_dictation_does_not():
    """Two ceilings, chosen when the turn starts.

    A single compromise number would be wrong in both directions: long enough
    for dictation means a rambling question never gets cut off, and short enough
    for a question means dictation stops inside its first sentence.
    """
    assert voice.TURN_SECONDS == 20.0, (
        "a spoken question should still be cut off when it stops making sense")
    assert voice.DICTATION_SECONDS >= 120.0, (
        "a paragraph is not twenty seconds")
    assert voice.DICTATION_SECONDS > voice.TURN_SECONDS * 5


def test_dictation_waits_longer_for_the_end_of_a_paragraph():
    """1.2 seconds of silence ends a question at a comma. It also ends a
    paragraph at every full stop inside it."""
    assert voice.DICTATION_SILENCE_SECONDS > voice.TURN_SILENCE_SECONDS
    assert voice.DICTATION_SILENCE_SECONDS >= 2.0


class _Recorder:
    """A recorder that records what it was asked for and why it stopped."""

    def __init__(self, reason="silence"):
        self.reason = reason
        self.calls = []

    def auto_stop_reason(self):
        return self.reason

    def start_auto_stop(self, on_done, max_seconds, silence_seconds, on_level=None):
        self.calls.append({"max": max_seconds, "silence": silence_seconds})
        return True

    def stop(self):
        pass

    def cancel_auto_stop(self):
        pass


class _Window:
    def __init__(self):
        self.states = []
        self.statuses = []

    def set_state(self, state):
        self.states.append(state)

    def set_orb_state(self, state):
        self.states.append(state)

    def get_state(self):
        return None

    def set_status(self, text):
        self.statuses.append(text)


class _Host:
    """Enough of the voice mixin for the dictation path, and nothing else."""

    def __init__(self, reason="silence"):
        self.recorder = _Recorder(reason)
        self.window = _Window()
        self.player = type("P", (), {"stop": lambda *a: None,
                                     "is_available": lambda: True})()
        # A usable STT, because `_start_listening` refuses without one - which is
        # the right behaviour and means the capture path is never reached by a
        # host with `stt = None`.
        self.stt = type("S", (), {"is_available": lambda self: True})()
        from shani_chronoa.config import ChronoaConfig
        self.config = ChronoaConfig()
        self.assistant = None
        self._listening = False
        self._dictating = False
        self._follow_up_of = ""
        self._listen_max_seconds = voice.TURN_SECONDS
        self._listen_silence_seconds = None

    # the two real methods, so the test drives the product's code
    _begin_listening = voice.VoiceMixin._begin_listening
    _start_listening = voice.VoiceMixin._start_listening
    _toggle_listening = voice.VoiceMixin._toggle_listening
    dictate = voice.VoiceMixin.dictate
    _dictation_note = voice.VoiceMixin._dictation_note
    _on_recording_done = voice.VoiceMixin._on_recording_done
    _on_input_level = voice.VoiceMixin._on_input_level
    _on_recording_done_main = voice.VoiceMixin._on_recording_done_main

    def _silence_reply(self):
        pass

    def cues_play(self, *a, **k):
        pass


def _cues(monkeypatch):
    played = []
    monkeypatch.setattr(voice.cues, "play", lambda *a, **k: played.append(a))
    return played


def test_dictating_asks_for_five_minutes_not_twenty_seconds(monkeypatch):
    _cues(monkeypatch)
    host = _Host()
    host.window.set_status = lambda text: None
    host.dictate()
    assert host.recorder.calls, "nothing was recorded"
    assert host.recorder.calls[0]["max"] == voice.DICTATION_SECONDS
    assert host.recorder.calls[0]["silence"] == voice.DICTATION_SILENCE_SECONDS


def test_the_ceiling_is_restored_afterwards(monkeypatch):
    """Otherwise a long ceiling leaks into the next question.

    This is the shape of bug the follow-up path already had to handle: it sets
    `_listen_max_seconds` and restores it in a `finally`, and the value has to
    come back or every later turn inherits a five-minute recording.
    """
    _cues(monkeypatch)
    host = _Host()
    host.window.set_status = lambda text: None
    host.dictate()
    assert host._listen_max_seconds == voice.TURN_SECONDS
    assert host._listen_silence_seconds is None, (
        "the silence must fall back to the person's own end-of-speech-pause "
        "setting, not to a constant that would override it for every later turn")


def test_dictation_presses_twice_and_the_second_press_stops_it(monkeypatch):
    _cues(monkeypatch)
    host = _Host()
    host.window.set_status = lambda text: None
    host.dictate()
    host._listening = True
    stopped = []
    # The real stop, so this is the path a person takes: `dictate()` on an
    # in-progress dictation must go through `_toggle_listening`, the microphone
    # button's own handler, because the first version called a `_stop_listening`
    # that does not exist and would have raised on the second press.
    real_toggle = host._toggle_listening

    def watched(action, param):
        stopped.append(True)
        host._listening = False
        real_toggle(action, param)

    host._toggle_listening = watched
    host.dictate()
    assert stopped, "a second press has to stop it, or dictation cannot be ended"
    assert host._listen_max_seconds == voice.TURN_SECONDS, (
        "and the long ceiling must not survive the dictation")


def test_a_clean_stop_says_so_and_a_cut_says_that(monkeypatch):
    """The claim that matters: a transcript must never imply it is complete when
    the clock ended it."""
    # The flag comes from the recorder, not from dictation: `_on_recording_done`
    # reads `auto_stop_reason()` and sets this, so a dictation with no flag is a
    # clean one by default and a test that assumed otherwise was asserting that
    # every dictation is truncated.
    host = _Host()
    host._dictating = True
    host._record_truncated = True
    assert "dictation limit" in host._dictation_note(), (
        "a recording that hit the cap must be reported as incomplete")

    host = _Host()
    host._dictating = True
    host._record_truncated = False
    assert "dictation ended" in host._dictation_note()
    assert "limit" not in host._dictation_note()

    host = _Host()
    host._dictating = False
    assert host._dictation_note() == "", "an ordinary turn is not a dictation"


def test_the_recorder_says_why_it_stopped():
    """Without this the caller cannot tell a finished sentence from a cut one:
    both arrive as an audio file."""
    assert hasattr(audio.AudioRecorder, "auto_stop_reason")
    source = Path(audio.__file__).read_text(encoding="utf-8")
    for reason in ('"silence"', '"limit"', '"cancelled"'):
        assert reason in source, f"the recorder never reports {reason}"


def test_the_application_registers_it_with_a_key():
    """An action nothing can reach is the dead-code shape this repository keeps
    paying for, so its existence is asserted from the source."""
    from shani_chronoa.app import application
    source = Path(application.__file__).read_text(encoding="utf-8")
    assert '"dictate"' in source
    assert "self.dictate" in source
    assert "app.dictate" in source, "and no key to reach it"


def test_it_is_not_the_quick_ask_key():
    """Two actions on one key, where the second silently never fires."""
    from shani_chronoa.app import application
    source = Path(application.__file__).read_text(encoding="utf-8")
    assert 'app.quick-ask", ["<Ctrl><Shift>a"]' in source
    assert 'app.dictate", ["<Ctrl><Shift>d"]' in source