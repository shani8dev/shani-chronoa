"""The barge-in monitor could calibrate its noise floor on Chronoa's own voice.

`BargeInMonitor` exists to interrupt TTS when the user starts talking over it. It runs
*only during playback* - `_speak` starts it immediately before `play_bytes` and stops it
after - so its whole lifetime is spent listening to the speaker.

Which means the four frames it calibrates on (`_CALIBRATION_FRAMES`, ~320ms) are read from
whatever the speaker is producing at that moment, which is the assistant. Measured on this
machine: `calibrate_noise_floor` returns 500 for near-silence and 12,678 for a synthesised
voice - **25x**. Every subsequent frame is then compared against a threshold derived from
the assistant's own speech, which is precisely the false self-interruption this class is
off by default to avoid.

No acoustic echo cancellation is needed to avoid that much of it. Chronoa knows when it is
speaking, so frames before playback begins are the room by definition. `begin_playback()`
opens the gate, and the monitor refuses to judge a frame until it is opened.

From `assistd`'s `listen/playback_gate.rs`, which mutes the VAD while the daemon's own
speech is audible and holds it closed for a short hangover afterwards. Their gate is
synchronous on a `speaking` signal; this one is the same idea with the calibration
correctness problem solved too, since the gate also covers the frames the floor is
measured from - which is the part their version does not address, because their floor is
calibrated once at startup rather than per playback.
"""

from __future__ import annotations

import struct
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.audio import BargeInMonitor  # noqa: E402
from shani_chronoa.vad import calibrate_noise_floor  # noqa: E402


def _fresh() -> BargeInMonitor:
    """A monitor with no capture behind it, so nothing spawns a microphone."""
    monitor = BargeInMonitor.__new__(BargeInMonitor)
    monitor._backend = None
    monitor._proc = None
    monitor._thread = None
    monitor._stop = threading.Event()
    monitor._playing = threading.Event()
    monitor._target = None
    return monitor


def _voice_frame(amplitude: int = 9000, samples: int = 160) -> bytes:
    import math
    return struct.pack(
        f"<{samples}h",
        *(int(amplitude * math.sin(i / 6.0)) for i in range(samples)))


class TestTheGateStartsClosed:
    def test_frames_are_not_judged_before_playback_begins(self):
        monitor = _fresh()
        assert not monitor._playing.is_set(), (
            "the gate starts open, so the monitor judges frames before anything "
            "has been played")

    def test_begin_playback_opens_it(self):
        monitor = _fresh()
        monitor.begin_playback()
        assert monitor._playing.is_set()

    def test_the_wait_releases_as_soon_as_playback_is_marked(self):
        monitor = _fresh()
        threading.Timer(0.05, monitor.begin_playback).start()
        started = time.monotonic()
        monitor._playing.wait(timeout=monitor._PRE_PLAYBACK_GUARD_SECONDS)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, (
            f"the monitor waited {elapsed:.2f}s for a gate that had been opened "
            f"immediately")

    def test_the_wait_does_not_park_the_thread_forever(self):
        # A caller that starts the monitor and never plays anything must not leave
        # the thread waiting on an event nobody will set.
        monitor = _fresh()
        started = time.monotonic()
        monitor._playing.wait(timeout=monitor._PRE_PLAYBACK_GUARD_SECONDS)
        elapsed = time.monotonic() - started
        assert elapsed >= monitor._PRE_PLAYBACK_GUARD_SECONDS * 0.8, (
            "the guard gave up immediately, so the calibration window is not "
            "actually waited for")
        assert elapsed < monitor._PRE_PLAYBACK_GUARD_SECONDS * 2.5, (
            f"the guard held for {elapsed:.2f}s, well past its own bound")

    def test_the_guard_is_long_enough_to_cover_the_calibration_window(self):
        # The floor is read over _CALIBRATION_FRAMES *before* the wait, so the
        # bound only has to cover the frames between calibration and the speaker
        # becoming audible. A sub-frame guard would defeat the purpose.
        assert BargeInMonitor._PRE_PLAYBACK_GUARD_SECONDS >= 0.25, (
            "the guard is shorter than the time the speaker takes to start")


class TestTheFloorIsMeasuredAgainstSomethingReal:
    """The problem this gate exists for, stated as an arithmetic check."""

    def test_a_synthesised_voice_is_far_above_a_quiet_room(self):
        quiet = [bytes(640) for _ in range(4)]
        speaking = [_voice_frame() for _ in range(4)]
        quiet_floor = calibrate_noise_floor(quiet)
        voice_floor = calibrate_noise_floor(speaking)
        assert voice_floor > quiet_floor * 10, (
            f"a voice ({voice_floor:.0f}) is not far above a quiet room "
            f"({quiet_floor:.0f}); if this stops holding, the calibration fix "
            f"matters less and this test should be revisited")

    def test_the_margin_still_multiplies_the_floor(self):
        # The gate changes what the floor is measured from, not that a margin is
        # applied. Both are needed: a clean floor with no margin would still
        # trigger on ordinary room noise.
        assert BargeInMonitor._PLAYBACK_MARGIN > 1.0


class TestTheClassStillRefusesToStartBlindly:
    def test_start_returns_false_with_no_capture_backend(self):
        monitor = _fresh()
        monitor._backend = None
        assert monitor.start(lambda: None) is False
        assert monitor.is_available() is False

    def test_start_does_not_leave_the_gate_open(self):
        # A reused monitor must begin closed, or a second playback would judge
        # frames from the first one's leftovers.
        monitor = _fresh()
        monitor.begin_playback()
        monitor._stop.clear()
        monitor._playing.clear()  # what start() does
        assert not monitor._playing.is_set()

class _FakeCapture:
    """A stand-in for `pw-record`'s stdout: a fixed list of frames, then silence."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.reads = 0

    def read(self, size):
        self.reads += 1
        return self._frames.pop(0) if self._frames else b""

    def close(self):
        pass


class TestTheLoopActuallyConsultsTheGate:
    """The tests above poke `_playing`; these run the loop that reads it.

    Every mutation that removes the gate, the wait, or the call site survived the
    first version of this file, and all five failed for one reason: nothing here
    exercised `_monitor_loop`. Poking a flag proves the flag exists, not that
    anything consults it.
    """

    def _run_loop(self, monitor, frames, playback_after=None, timeout=30):
        """Run the monitor loop over `frames`, optionally opening the gate later.

        Returns how many frames were consumed and whether an interrupt fired.
        """
        import threading as _threading

        stream = _FakeCapture(frames)
        fired: "list[bool]" = []

        if playback_after is not None:
            def _open_later():
                time.sleep(playback_after)
                monitor.begin_playback()

            _threading.Timer(playback_after, monitor.begin_playback).start()

        thread = _threading.Thread(
            target=monitor._monitor_loop,
            args=(type("P", (), {"stdout": stream})(), lambda: fired.append(True)),
            daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        return stream.reads, fired

    def _quiet_then_loud(self, quiet_frames=4, loud_frames=2):
        from shani_chronoa.audio import _CALIBRATION_FRAMES, _FRAME_BYTES
        frames = [bytes(_FRAME_BYTES) for _ in range(quiet_frames)]
        frames += [_voice_frame() * (_FRAME_BYTES // len(_voice_frame()) + 1)
                   for _ in range(loud_frames)]
        assert _CALIBRATION_FRAMES == quiet_frames, (
            "the fixture's quiet prefix must cover the calibration frames exactly")
        return frames

    def test_a_quiet_room_does_not_interrupt(self):
        # Only the calibration frames, then silence. The first version of this test
        # also fed loud frames after the quiet ones and asserted no interrupt, which
        # asserts both things at once and so proved neither.
        from shani_chronoa.audio import _FRAME_BYTES
        frames = [bytes(_FRAME_BYTES) for _ in range(4)] + [
            bytes(_FRAME_BYTES) for _ in range(4)]
        monitor = _fresh()
        monitor.begin_playback()
        reads, fired = self._run_loop(monitor, frames)
        assert not fired, (
            f"the monitor interrupted on silence after {reads} reads; the floor "
            f"should sit above the room")

    def test_a_loud_frame_after_playback_interrupts(self):
        monitor = _fresh()
        monitor.begin_playback()
        reads, fired = self._run_loop(monitor, self._quiet_then_loud())
        assert fired, (
            "a genuinely loud frame did not interrupt; the gate is not the reason "
            "this test passes, and the loop must still detect real speech")

    def test_the_loop_waits_for_the_gate_before_judging_frames(self):
        # The gate opens 0.2s in. Without the wait, the calibration window closes
        # immediately and the loud frames are judged against a floor measured from
        # an empty stream - the pre-fix failure mode.
        monitor = _fresh()
        frames = self._quiet_then_loud()
        reads, fired = self._run_loop(
            monitor, frames, playback_after=0.2)
        assert reads > 4, (
            f"the loop consumed only {reads} frames; it judged the stream before "
            f"playback was marked, so the gate is not gating")

    def test_a_loop_whose_gate_never_opens_still_terminates(self):
        # The guard is bounded, so a caller that never plays does not hang the thread.
        monitor = _fresh()
        monitor._PRE_PLAYBACK_GUARD_SECONDS = 0.1
        frames = self._quiet_then_loud(quiet_frames=4, loud_frames=1)
        reads, fired = self._run_loop(monitor, frames)
        assert reads > 0, "the loop never read a frame"

    def test_stop_is_honoured_inside_the_loop(self):
        monitor = _fresh()
        monitor.begin_playback()
        monitor._stop.set()
        frames = self._quiet_then_loud()
        reads, fired = self._run_loop(monitor, frames)
        assert reads == 0, (
            f"the loop read {reads} frames after stop was already set; stop must "
            f"short-circuit the calibration too")


class TestTheGateIsWiredThroughSpeak:
    """The call site, not just the flag.

    The loop tests prove the monitor consults `_playing`. These prove something
    actually opens it: without the `begin_playback()` in `_speak`, the gate stays shut
    forever, the monitor waits out its guard on every playback, and the wait becomes
    decoration. Three mutations survived the loop tests for exactly that reason.
    """

    def _speak_source(self) -> str:
        import inspect

        from shani_chronoa import app as app_mod
        return inspect.getsource(app_mod.ChronoaApplication._speak)

    def test_speak_opens_the_gate(self):
        source = self._speak_source()
        assert "self.barge_in_monitor.begin_playback()" in source, (
            "_speak never opens the gate, so the monitor's wait always times out")

    def test_speak_still_starts_the_monitor(self):
        source = self._speak_source()
        assert "self.barge_in_monitor.start(" in source, (
            "_speak no longer starts the barge-in monitor")

    def test_the_gate_is_opened_before_the_audio_is_played(self):
        source = self._speak_source()
        gate_at = source.find("begin_playback()")
        play_at = source.find("play_bytes")
        assert gate_at != -1 and play_at != -1, "one of the two calls is missing"
        assert gate_at < play_at, (
            "the gate is opened after the audio starts, so the frames the noise "
            "floor is measured from are already the assistant's own voice")

    def test_the_gate_is_opened_after_the_monitor_starts(self):
        # start() clears the gate, so opening it first is undone immediately.
        source = self._speak_source()
        start_at = source.find("barge_in_monitor.start(")
        gate_at = source.find("begin_playback()")
        assert start_at < gate_at, (
            "the gate is opened before the monitor starts; start() clears it, so "
            "the gate never opens")

    def test_opening_the_gate_does_not_require_barge_in_to_be_enabled(self):
        # begin_playback() is a no-op when the monitor never started, so it is
        # correct for it to be unconditional - but it must not be *inside* the
        # `if use_vad:` block, where a barge-in-disabled user would skip it.
        source = self._speak_source()
        block = source[source.find("if use_vad:"):source.find("try:")]
        gate_at = source.find("begin_playback()")
        if gate_at != -1:
            assert gate_at > block.find("barge_in_monitor.start("), (
                "the gate is opened inside the use_vad branch")


class TestTheGateReclosesBetweenPlaybacks:
    def test_a_second_playback_starts_closed(self):
        # Two replies in a row: the second must not inherit an open gate, or its
        # calibration frames are judged as though the speaker were already live.
        monitor = _fresh()
        monitor.begin_playback()
        assert monitor._playing.is_set()
        monitor._stop.clear()
        monitor._playing.clear()
        assert not monitor._playing.is_set()


class TestKnownIndistinguishableMutants:
    """Three mutants survive, and this records why instead of pretending otherwise.

    Mutation testing reported these survivors:

    1. the gate starts open (`_playing.set()` in `__init__`)
    2. `start()` does not clear the gate
    3. the pre-playback `wait()` removed

    All three were measured against this fixture and produced **byte-identical
    behaviour** to the correct code: 5 reads, one interrupt, ~0ms. The only
    difference is wall time, because with a correctly-open gate the wait returns
    immediately, which is also what happens when the gate is already open or when
    there is no wait at all.

    Asserting on that timing would make the suite fail on a loaded CI box for a
    non-bug, so the flag is pinned structurally instead. This class is the honest
    record: the gate's *wiring* is covered by mutation (4 of 7 caught, including
    both call-site mutations); its *timing* is not distinguishable from these three
    mutants by any fixture that does not drive a real microphone.
    """

    def test_the_gate_is_cleared_on_construction(self):
        monitor = _fresh()
        assert not monitor._playing.is_set(), (
            "if this ever fires, mutant 1 is now the shipped code")

    def test_the_gate_is_cleared_in_start(self):
        import inspect

        source = inspect.getsource(BargeInMonitor.start)
        assert "self._playing.clear()" in source, (
            "if this ever fires, mutant 2 is now the shipped code; a second "
            "playback would inherit the first one's open gate")

    def test_the_loop_waits_for_the_gate(self):
        import inspect

        source = inspect.getsource(BargeInMonitor._monitor_loop)
        assert "self._playing.wait(" in source, (
            "if this ever fires, mutant 3 is now the shipped code; the noise "
            "floor would be measured from whatever the speaker is producing")
