"""Do not run whisper.cpp while the model is generating.

From `assistd`'s `QueuedTranscriber`, which asks "is the GPU busy" before every
transcription and logs which way it went. Worth having here for a specific
reason: whisper.cpp and llama.cpp share this machine, so a transcript starting
mid-generation competes with the reply for the same accelerator - and the
transcript is the half whose lateness the person notices, having just said the
words.

The clock is injected so these tests prove the *timeout*, rather than measuring
how long four seconds is.
"""

import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import speech_gate  # noqa: E402


class _Clock:
    """A clock the test drives, so no test ever really waits."""

    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def test_an_idle_model_does_not_make_it_wait():
    clock = _Clock()
    waited, reason = speech_gate.wait(lambda: False, 4.0,
                                      now=clock.now, sleep=clock.sleep)
    assert waited is False
    assert clock.slept == [], "it slept while the model was idle"
    assert "idle" in reason


def test_no_probe_at_all_is_not_a_wait():
    """The common case in tests and on a machine with no window: the absence of a
    probe must not become a delay."""
    clock = _Clock()
    waited, reason = speech_gate.wait(None, 4.0, now=clock.now, sleep=clock.sleep)
    assert waited is False
    assert clock.slept == []
    assert "no busy probe" in reason


def test_a_busy_model_is_waited_for():
    clock = _Clock()
    busy = {"value": True}

    def probe():
        return busy["value"]

    # Let go on the third look.
    original_sleep = clock.sleep

    def sleep_and_release(seconds):
        original_sleep(seconds)
        if len(clock.slept) >= 3:
            busy["value"] = False

    clock.sleep = sleep_and_release
    waited, reason = speech_gate.wait(probe, 4.0, now=clock.now, sleep=clock.sleep)
    assert waited is True
    assert "waited for the model" in reason
    assert clock.t <= 4.0


def test_the_wait_is_bounded_and_transcribes_anyway():
    """Loud machine, model that never finishes: the transcript must still happen.

    Dropping what somebody said is worse than being slow, so the timeout
    transcribes anyway - and says so, because "your speech was slow" with no
    reason attached cannot be acted on.
    """
    clock = _Clock()
    waited, reason = speech_gate.wait(lambda: True, 4.0,
                                      now=clock.now, sleep=clock.sleep)
    assert waited is True
    assert clock.t >= 4.0, "it waited past its own timeout"
    assert "anyway" in reason, reason


def test_a_zero_timeout_asks_once_and_does_not_sleep():
    """`gpu_busy_timeout_ms = 0` in assistd: ask, and do not queue behind a
    stream that may never end."""
    clock = _Clock()
    waited, _reason = speech_gate.wait(lambda: True, 0.0,
                                       now=clock.now, sleep=clock.sleep)
    assert waited is False, "a zero timeout cannot have waited"
    assert clock.slept == []


def test_a_probe_that_raises_does_not_lose_the_transcript():
    """An exception in the audio path would drop what somebody said."""
    gate = speech_gate.Gate(lambda: (_ for _ in ()).throw(RuntimeError("no state")))

    def boom():
        raise AssertionError("the probe raised through")

    gate.run()
    assert gate.last_reason, "the failure was not recorded"
    assert gate.deferred == 0


def test_the_gate_counts_what_it_deferred():
    """The number a person needs when speech feels slow.

    A real timeout, not zero: with `timeout=0.0` the gate asks once and does not
    sleep, and by the corrected `waited` rule that is correctly *not* a deferral -
    so counting it as one would put a number on the log that never happened.
    """
    busy = {"value": True}
    gate = speech_gate.Gate(lambda: busy["value"], timeout=0.05)
    gate.run()
    gate.run()
    assert gate.deferred == 2, gate.last_reason
    assert "anyway" in gate.last_reason


def test_the_window_probe_reads_the_state_rather_than_a_flag():
    """The window owns the state and already broadcasts it; a second flag is one
    more thing that can disagree with the orb."""
    from shani_chronoa.gui.widgets import AssistantState

    class _Window:
        def __init__(self, state):
            self._state = state

        def get_state(self):
            return self._state

    assert speech_gate.window_busy(None) is None
    busy = speech_gate.window_busy(_Window(AssistantState.THINKING))
    assert busy() is True
    busy = speech_gate.window_busy(_Window(AssistantState.SPEAKING))
    assert busy() is True
    busy = speech_gate.window_busy(_Window(AssistantState.IDLE))
    assert busy() is False


def test_a_window_that_cannot_answer_is_treated_as_idle():
    class _Broken:
        def get_state(self):
            raise RuntimeError("the window is gone")

    busy = speech_gate.window_busy(_Broken())
    assert busy() is False, (
        "a probe that cannot answer must not become a reason to lose a transcript")

class TestTheVoicePathAsksAboutTheModelNotTheOrb:
    """Measured 2026-10-08: every spoken request waited 4 s at the gate.

    The voice path sets the orb to THINKING right before transcribing, and the
    gate's probe read the orb - so it waited behind the state it had just set,
    with no model running. The probe now asks whether a model turn is in flight.
    """

    def test_no_turn_running_is_not_busy(self):
        from concurrent.futures import Future
        from shani_chronoa.app import voice
        mixin = voice.VoiceMixin
        import types
        done = Future(); done.set_result("answer")
        for future in (None, done):
            ns = types.SimpleNamespace(_turn_future=future)
            assert mixin._model_turn_in_flight(ns) is False

    def test_a_turn_in_flight_is_busy_and_the_gate_waits_for_it(self):
        from concurrent.futures import Future
        import types
        from shani_chronoa.app import voice
        mixin = voice.VoiceMixin
        running = Future()
        ns = types.SimpleNamespace(_turn_future=running)
        assert mixin._model_turn_in_flight(ns) is True
        gate = speech_gate.Gate(lambda: mixin._model_turn_in_flight(ns), timeout=0.3)
        start = time.monotonic()
        assert gate.run() is True and time.monotonic() - start >= 0.25
        running.set_result("done")
        start = time.monotonic()
        assert gate.run() is False and time.monotonic() - start < 0.2
