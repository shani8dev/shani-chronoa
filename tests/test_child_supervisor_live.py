"""`child_supervisor.py` (once the supervision half of `gateway_supervisor.py`), driven by real subprocesses.

`gateway_supervisor.py` was, for its whole life, a module that built a heartbeat
mechanism and had nothing to supervise. The half that is now live watches the two
processes `audio.py` genuinely keeps alive for minutes at a time, and this file is the
evidence that it watches them for real: every process below is a real `Popen`, with a
real pid, real pipes, real exit statuses and real signals. None of them is a fake
object with `terminated = True` on it.

**What is NOT verified here, and cannot be.** There is no microphone and no speaker on
this machine, so nothing here says whether `pw-record` and `pw-play` behave correctly
on real acoustic hardware, whether `_CAPTURE_STALL_SECONDS` is the right silence budget
against a real PipeWire graph, or whether a live capture ever legitimately stops
writing. That is the same limit `AGENTS.md` records for barge-in, where "only the
detection logic itself was verified, via synthetic PCM fed directly into the same code
path", and it is why `_CAPTURE_STALL_SECONDS` carries its own HONEST LIMITATION note.
What is verified is the half that does not need ears: that a real child which dies, a
real child which stops writing, and a real child which has not spoken yet are three
different, separately reportable conditions - and that a boolean cannot tell them
apart, which is the reason `check_heartbeats` returns states.

**What is substituted, and only this.** The command `pw-record` is replaced by a real
Python child that writes a chosen number of chosen PCM frames and then does one of
three things: keep running silently (a wedge), exit with a chosen status (a crash), or
exit cleanly (an orderly end). Substituting the *program* keeps everything the
supervision actually depends on genuine - process lifetime, pipe delivery, EOF,
exit status, SIGTERM - and only removes the microphone. `pw-play` is not substituted
at all: the real binary is run against a real unreadable file, because it turns out to
fail with a positive exit status, which is precisely the case its caller cannot see.
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import audio as audio_mod  # noqa: E402
from shani_chronoa.audio import AudioPlayer, AudioRecorder  # noqa: E402
from shani_chronoa.child_supervisor import (  # noqa: E402
    ChildState,
    ChildSupervisor,
)

_PW_PLAY = shutil.which("pw-play")
_PW_RECORD = shutil.which("pw-record")

# A stand-in capture child. Takes a comma-separated list of per-frame amplitudes and
# then one of the three endings, because the three endings are the three things being
# distinguished. Amplitude 0 is silence and 9000 is comfortably past `vad`'s 300 floor,
# so a "speech then silence" list auto-stops the real detector rather than running out
# a timeout.
_CHILD = r"""
import struct, sys, time

FRAME_BYTES = %(frame_bytes)d
amplitudes = [int(a) for a in sys.argv[1].split(",")]
interval = float(sys.argv[2])
ending = sys.argv[3]
status = int(sys.argv[4])
first_delay = float(sys.argv[5])

def frame(amplitude):
    half = FRAME_BYTES // 2
    return struct.pack("<%%dh" %% half, *([amplitude] * half))

time.sleep(first_delay)
for amplitude in amplitudes:
    sys.stdout.buffer.write(frame(amplitude))
    sys.stdout.buffer.flush()
    time.sleep(interval)
if ending == "hang":
    # Alive, holding the write end of the pipe open, and never writing again. This is
    # a wedged capture, and it is indistinguishable from a healthy one to anything
    # that only knows the process exists.
    time.sleep(600)
sys.exit(status)
""" % {"frame_bytes": audio_mod._FRAME_BYTES}

#: Four silent frames calibrate the floor, two loud ones set `heard_speech`, two more
#: silent ones end the turn (0.16s of silence at a 0.08s frame).
_SPEECH_THEN_SILENCE = "0,0,0,0,9000,9000,0,0"

#: Long enough to outlast a stall check. Built with `join`, not `"0," * n`: that leaves
#: a trailing comma, and the child then dies of `int("")` - which is how the first draft
#: of this file reported EXITED for a capture that was supposed to be healthy.
_SILENT_30 = ",".join(["0"] * 30)
_SILENT_40 = ",".join(["0"] * 40)

_SPAWNED: list = []


def _track(proc: subprocess.Popen) -> subprocess.Popen:
    """Register a real child for reaping, so a failed test cannot leave one running."""
    _SPAWNED.append(proc)
    return proc


def _child(
    amplitudes: str = _SPEECH_THEN_SILENCE,
    interval: float = 0.02,
    ending: str = "exit",
    status: int = 0,
    first_delay: float = 0.0,
) -> list:
    return [
        sys.executable, "-c", _CHILD,
        amplitudes, str(interval), ending, str(status), str(first_delay),
    ]


def _sleep(seconds: int = 60) -> subprocess.Popen:
    return _track(subprocess.Popen(["sleep", str(seconds)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL))


def _await(predicate, limit: float = 20.0) -> bool:
    """Wait for a background thread to reach a point, without asserting inside a spin."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _state(probe, name: str):
    """The current supervised state of one child, or None if it is not tracked."""
    for status in probe():
        if status.name == name:
            return status
    return None


def _await_state(probe, expected: ChildState, limit: float = 20.0):
    """Wait for a supervised child to reach `expected`; return whatever it reached.

    Returning the last status seen rather than a bool is deliberate: a timed-out wait
    has to be able to say what the state *was*, or the failure reads as "timed out"
    instead of "it stayed healthy the whole time", which are opposite problems.
    """
    deadline = time.monotonic() + limit
    seen = None
    while time.monotonic() < deadline:
        seen = probe()
        if seen is not None and seen.state is expected:
            return seen
        time.sleep(0.02)
    return seen


@pytest.fixture(autouse=True)
def _clean_registry():
    """No test may inherit a tracked child from the one before it, or leak one.

    The reaping half is not bookkeeping. This file exists because a subprocess nobody
    tracked outlived the thing that started it, and a test suite that leaves six
    `sleep 60` processes behind after every run is the same defect at smaller scale.
    """
    _SPAWNED.clear()
    for name in (audio_mod._CAPTURE_CHILD, audio_mod._PLAYBACK_CHILD):
        audio_mod._SUPERVISOR.forget_child(name)
    yield
    for name in (audio_mod._CAPTURE_CHILD, audio_mod._PLAYBACK_CHILD):
        audio_mod._SUPERVISOR.forget_child(name)
    while _SPAWNED:
        proc = _SPAWNED.pop()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def supervisor() -> ChildSupervisor:
    """A private registry, for the cases that are about the supervisor itself."""
    return ChildSupervisor()


@pytest.fixture
def recorder(monkeypatch) -> AudioRecorder:
    """A real `AudioRecorder` whose capture backend is a real child we control."""
    monkeypatch.setattr(AudioRecorder, "_detect_backend", lambda self: "pw-record")
    return AudioRecorder()


@pytest.fixture
def streaming(monkeypatch, request):
    """Make the recorder spawn real children, from a queue the test fills in.

    Only the *program* behind `pw-record` is substituted. `Popen`, `stdout=PIPE`,
    `_auto_stop_loop`, the VAD, the frame accounting and the teardown are all shipped
    code, and the children are real processes with real exit statuses.
    """
    queue: list = []

    def _next_argv(backend, target=None):
        argv = queue.pop(0)
        request.node._child_argv_used = True  # noqa: SLF001
        return argv

    monkeypatch.setattr(audio_mod, "_stream_capture_cmd", _next_argv)
    recorder = AudioRecorder()
    yield recorder, queue
    stop = recorder._watchdog_stop
    if stop is not None:
        stop.set()
    proc = recorder._proc
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


# ======================================================================================
# The three states are three states, and a boolean is not three of anything
# ======================================================================================


class TestTheThreeStatesAreDistinguishable:
    """One real `sleep` left running, one killed, one never started.

    This is the whole point of the change, so it is asserted as the defect of the shape
    it replaced rather than as three separate facts. A test that only asserted "a dead
    child is not healthy" would also pass against `return proc.poll() is None`, and that
    expression cannot tell a child that died from a child that has not started yet - the
    two are opposite mistakes and one boolean reports them identically.
    """

    def test_a_running_silent_child_a_killed_child_and_an_unstarted_one_differ(self, supervisor):
        leaving = _sleep()
        killed = _sleep()
        supervisor.track_child("leaving", leaving.poll, pid=leaving.pid, stall_after=0.3)
        supervisor.track_child("killed", killed.poll, pid=killed.pid, stall_after=0.3)

        fresh = _state(supervisor.check_heartbeats, "leaving")
        assert fresh.state is ChildState.STARTING, (
            f"a running child that has never reported is {fresh.state}, not STARTING: "
            "being new is not a fault and must not look like one"
        )
        killed.terminate()
        killed.wait(timeout=5)

        hung = _await_state(lambda: _state(supervisor.check_heartbeats, "leaving"),
                            ChildState.DISCONNECTED)
        assert hung is not None and hung.name == "leaving", (
            "a child that is running and has said nothing for its whole budget was not "
            f"reported as hung (it stayed {fresh.state} -> {_state(supervisor.check_heartbeats, 'leaving')})"
        )
        assert hung.age is None, (
            "the hung child had reported something, so this is not the never-reported "
            "case and the test is not covering the registration budget"
        )

        dead = _state(supervisor.check_heartbeats, "killed")
        unstarted = _state(supervisor.check_heartbeats, "never-started")

        assert dead.state is ChildState.EXITED and dead.exit_status is not None, (
            f"a child that was terminated is {dead.state}; an exit status is the only "
            "thing that distinguishes a crash from a kill, and it was dropped"
        )
        assert unstarted is None, "a name that was never tracked produced a status"

        states = {fresh.state, hung.state, dead.state}
        collapsed = {s is ChildState.IDLE for s in (fresh, hung, dead)}
        assert len(states) == 3, f"the three states are not distinct: {states}"
        assert len(collapsed) == 1, (
            "this test no longer demonstrates anything: if the states had collapsed to "
            f"a boolean, this file would pass - {collapsed}"
        )

    def test_one_child_is_seen_as_starting_then_healthy(self, supervisor):
        """The healthy case, with the child's own lifetime as the clock.

        A supervisor that only ever reports failures cannot tell "nothing is wrong"
        from "nothing is being watched", so `check_heartbeats` includes the healthy
        state and this asserts it is reachable.
        """
        child = _sleep()
        supervisor.track_child("capture", child.poll, pid=child.pid, stall_after=30)
        assert _state(supervisor.check_heartbeats, "capture").state is ChildState.STARTING

        supervisor.update_heartbeat("capture")
        status = _state(supervisor.check_heartbeats, "capture")
        assert status.state is ChildState.IDLE and status.age is not None
        assert status.age < 30, f"a heartbeat just taken reads as {status.age}s old"

    def test_a_child_with_no_silence_budget_is_never_called_hung(self, supervisor):
        """`stall_after=None` means "liveness is the exit status", and it must say so.

        The honest reading for a child this codebase waits on with one bounded
        `wait()`: it produces no bytes at all, so any silence budget would eventually
        invent a fault that cannot happen.
        """
        child = _sleep()
        supervisor.track_child("playback", child.poll, pid=child.pid, stall_after=None)
        time.sleep(0.6)
        status = _state(supervisor.check_heartbeats, "playback")
        assert status.state is ChildState.STARTING, (
            f"a child with no silence budget reported {status.state}"
        )
        child.terminate()
        child.wait(timeout=5)
        assert _state(supervisor.check_heartbeats, "playback").state is ChildState.EXITED


# ======================================================================================
# The real capture path: registration, heartbeat, and an honest teardown
# ======================================================================================


class TestACaptureChildIsSupervised:
    # The capture is asked for 20 seconds and this test asserts on its first
    # moments, so it is still live - and its ears light still on - when the test
    # ends, except on a slow machine where the 20 seconds have already elapsed.
    # That is why this is a marker and not a cleanup: either behaviour is
    # correct, and the test is not about stopping a capture.
    @pytest.mark.holds_organs
    def test_a_real_capture_child_goes_from_starting_to_healthy(self, streaming):
        recorder, queue = streaming
        queue.append(_child(amplitudes=_SILENT_30, interval=0.1, first_delay=1.5))
        assert recorder.start_auto_stop(lambda _p: None, max_seconds=20.0, silence_seconds=0.16)

        started = recorder.capture_status()
        assert started is not None and started.state is ChildState.STARTING, (
            f"a capture that has not delivered a frame yet is {started}, so a caller "
            "cannot tell it apart from one that is already broken"
        )
        assert started.pid == recorder._proc.pid, (
            "the supervised child is not the one that was spawned, so the status "
            "describes a process nobody is watching"
        )

        healthy = _await_state(recorder.capture_status, ChildState.IDLE, limit=20.0)
        assert healthy is not None and healthy.state is ChildState.IDLE, (
            f"a capture streaming real frames never reported healthy: {healthy}"
        )
        assert healthy.age is not None and healthy.age < audio_mod._CAPTURE_STALL_SECONDS

    def test_a_capture_child_that_dies_is_reported_with_its_real_status(self, streaming):
        recorder, queue = streaming
        delivered: list = []
        queue.append(_child(amplitudes="0,0,0", interval=0.01, ending="exit", status=7))
        assert recorder.start_auto_stop(delivered.append, max_seconds=20.0, silence_seconds=0.16)
        assert _await(lambda: delivered), "the capture never finished"

        status = recorder.capture_status()
        assert status is not None, (
            "a capture whose child died was forgotten, so the exit status is gone - the "
            "one moment it is the only evidence there is"
        )
        assert status.state is ChildState.EXITED, f"a dead child is {status}"
        assert status.exit_status == 7, f"the real exit status was lost: {status}"
        assert "7" in status.detail, "the report does not name the status it is reporting"
        assert delivered == [None], (
            "a capture that heard nothing must finalise as None; anything else would be "
            "a transcript of a dead pipe"
        )

    def test_a_capture_that_ends_the_way_it_should_is_not_reported_as_a_fault(self, streaming):
        recorder, queue = streaming
        delivered: list = []
        queue.append(_child())
        assert recorder.start_auto_stop(delivered.append, max_seconds=20.0, silence_seconds=0.16)
        assert _await(lambda: delivered)
        assert delivered[0], f"expected a WAV from a completed turn, got {delivered[0]!r}"
        os.unlink(delivered[0])

        assert recorder.capture_status() is None, (
            "a capture that heard a sentence, auto-stopped and shut its own child down is "
            "being reported as a fault; every normal turn would raise an alarm"
        )
        assert recorder.capture_failure() is None


@pytest.mark.skipif(not _PW_RECORD, reason="pw-record is not installed here")
class TestAgainstTheRealRecorderBinary:
    """The genuine `pw-record`, on this machine's genuine (virtual) audio source.

    Measured while writing this: the real binary streams 98304 bytes in 3s here, which
    is exactly 16kHz mono s16 - so a real capture really does reach the real read loop
    on this box even without a microphone. What that proves is the *pipe* path: bytes
    arriving, heartbeats taken, the child judged healthy while it runs.

    **What is deliberately not asserted here: what the audio contains.** The first
    draft asserted that the turn finalised as `None`, on the reasoning that a machine
    with no microphone must deliver silence. It failed roughly one run in three: the
    source on this box is a virtual device that sometimes carries enough energy to clear
    `vad`'s 300 floor. That assertion was a claim about this machine's audio dressed up
    as a claim about the supervision, so it is gone - and `silence_seconds` is set past
    the capture length so the turn ends on its deadline whatever the audio does.
    """

    def test_the_real_capture_child_streams_and_stays_alive(self, monkeypatch):
        monkeypatch.setattr(AudioRecorder, "_detect_backend", lambda self: "pw-record")
        recorder = AudioRecorder()
        delivered: list = []
        assert recorder.start_auto_stop(
            delivered.append, max_seconds=3.0, silence_seconds=5.0
        )
        proc = recorder._proc

        healthy = _await_state(recorder.capture_status, ChildState.IDLE, limit=15.0)
        assert healthy is not None and healthy.state is ChildState.IDLE, (
            f"the real capture child was never seen as healthy: {healthy}"
        )
        assert healthy.pid == proc.pid, (
            f"the supervised pid {healthy.pid} is not the spawned one ({proc.pid}), so "
            "the healthy reading is about some other process"
        )

        assert _await(lambda: delivered, limit=30.0), "the real capture never finished"
        if delivered[0]:
            os.unlink(delivered[0])
        recorder.cancel_auto_stop()


# ======================================================================================
# A hang is only observable from outside the loop that is blocked on it
# ======================================================================================


class TestTheWatchdogSeesARealWedge:
    def _wedge(self, streaming, caplog):
        recorder, queue = streaming
        caplog.set_level(logging.ERROR, logger="shani_chronoa.audio")
        delivered: list = []
        # Four silent frames get past calibration, then the child stops writing forever.
        queue.append(_child(amplitudes="0,0,0,0,0,0", interval=0.01, ending="hang"))
        assert recorder.start_auto_stop(delivered.append, max_seconds=30.0, silence_seconds=0.16)
        return recorder, delivered

    def test_a_capture_that_stops_writing_is_reported_as_hung(self, streaming, caplog):
        recorder, delivered = self._wedge(streaming, caplog)

        wedged = _await_state(recorder.capture_status, ChildState.DISCONNECTED)
        assert wedged is not None and wedged.state is ChildState.DISCONNECTED, (
            f"a wedged capture was never reported: {wedged}"
        )

        # The retention is the watchdog's, not the query's, so it is waited for rather
        # than read on the strength of a live query having just noticed.
        assert _await(lambda: recorder.capture_failure() is not None), (
            "the fault was not retained; it is discovered at a moment when nothing else "
            "is looking, and the watchdog is the only thing that will ever look again"
        )
        assert recorder.capture_failure().state is ChildState.DISCONNECTED
        assert delivered == [], "the wedge produced a finalised turn"

    # The wedged capture is still running at the end, which is the point: the
    # test is about what the watchdog does while it runs. Its ears activity
    # stays lit, so it opts out of the session's no-organ-left-lit check.
    @pytest.mark.holds_organs
    def test_a_wedge_is_reported_once_per_transition_not_once_per_tick(self, streaming, caplog):
        """A fault that logs twice a second for as long as the app is open gets ignored."""
        recorder, _delivered = self._wedge(streaming, caplog)
        assert _await(lambda: recorder.capture_failure() is not None)
        time.sleep(audio_mod._WATCHDOG_INTERVAL_SECONDS * 6)

        reports = [r for r in caplog.records if "not delivering audio" in r.getMessage()]
        assert len(reports) == 1, (
            f"a single wedge produced {len(reports)} reports; one per transition is the "
            "difference between a fault that gets read and one that gets scrolled past"
        )

    @pytest.mark.holds_organs
    def test_a_healthy_capture_is_never_reported(self, streaming, caplog):
        """The control: the watchdog is not simply reporting whatever it finds.

        Marked because this capture is still live when the test ends - its ears
        activity is still lit, which is correct while it is recording.
        """
        recorder, queue = streaming
        caplog.set_level(logging.ERROR, logger="shani_chronoa.audio")
        queue.append(_child(amplitudes=_SILENT_30, interval=0.1))
        assert recorder.start_auto_stop(lambda _p: None, max_seconds=30.0, silence_seconds=0.16)
        healthy = _await_state(recorder.capture_status, ChildState.IDLE)
        assert healthy is not None and healthy.state is ChildState.IDLE, healthy
        time.sleep(audio_mod._WATCHDOG_INTERVAL_SECONDS * 4)

        assert recorder.capture_failure() is None, (
            f"a capture delivering frames on time was reported: {recorder.capture_failure()}"
        )
        assert not [r for r in caplog.records if "not delivering audio" in r.getMessage()]


# ======================================================================================
# Recovery: report first, and never in the middle of what the user is saying
# ======================================================================================


class TestRecoveryIsSafeAtTurnBoundaries:
    def test_a_wedged_capture_is_released_so_the_next_turn_can_start(self, streaming):
        """The bug this exists for, as a before and after.

        Before: a wedged capture leaves `_proc` set, so every later `start_auto_stop`
        returns False and the user is told "Failed to start audio recording" forever,
        with nothing anywhere saying the microphone is dead.
        """
        recorder, queue = streaming
        delivered: list = []
        queue.append(_child(amplitudes="0,0,0,0,0,0", interval=0.01, ending="hang"))
        assert recorder.start_auto_stop(delivered.append, max_seconds=30.0, silence_seconds=0.16)
        wedged = _await_state(recorder.capture_status, ChildState.DISCONNECTED)
        assert wedged is not None and wedged.state is ChildState.DISCONNECTED, wedged

        second: list = []
        queue.append(_child())
        assert recorder.start_auto_stop(second.append, max_seconds=20.0, silence_seconds=0.16), (
            "the wedged capture was not released, so the user's next turn is refused - "
            "the microphone is dead and the app cannot say so"
        )
        reason = recorder.capture_failure()
        assert reason is not None and reason.state is ChildState.DISCONNECTED, (
            f"the turn was released without a reportable reason: {reason}"
        )
        assert _await(lambda: second), "the released recorder did not then work"
        if second[0]:
            os.unlink(second[0])

    # The capture is deliberately still running when this ends - that is what
    # the test asserts - so its ears activity is still lit. Marked, because the
    # body register is process-global and an unclosed light outlives the test.
    @pytest.mark.holds_organs
    def test_a_live_capture_is_never_released(self, streaming):
        """The control. Recovery that fires on a healthy capture truncates an utterance.

        This is the boundary the whole design rests on: the user is mid-sentence, bytes
        are still arriving, and the only thing that must happen is nothing at all.
        """
        recorder, queue = streaming
        first: list = []
        queue.append(_child(amplitudes=_SILENT_40, interval=0.1))
        assert recorder.start_auto_stop(first.append, max_seconds=30.0, silence_seconds=0.16)
        healthy = _await_state(recorder.capture_status, ChildState.IDLE)
        assert healthy is not None and healthy.state is ChildState.IDLE, healthy

        queue.append(_child())
        assert not recorder.start_auto_stop(lambda _p: None, max_seconds=20.0,
                                            silence_seconds=0.16), (
            "a capture that is still delivering audio was taken over by a new one; the "
            "user's sentence was cut off mid-word"
        )
        assert recorder._proc is not None and recorder._proc.poll() is None, (
            "the live capture's process was terminated while it was still speaking"
        )
        assert recorder.capture_failure() is None

    def test_an_abandoned_turn_does_not_finalise_over_the_turn_that_replaced_it(
        self, streaming
    ):
        """`on_done` is the caller's cue that a turn is over; it must not fire for a
        turn that was abandoned, or it races the new turn's own finalisation."""
        recorder, queue = streaming
        abandoned: list = []
        queue.append(_child(amplitudes="0,0,0,0,0,0", interval=0.01, ending="hang"))
        assert recorder.start_auto_stop(abandoned.append, max_seconds=30.0,
                                        silence_seconds=0.16)
        wedged = _await_state(recorder.capture_status, ChildState.DISCONNECTED)
        assert wedged is not None and wedged.state is ChildState.DISCONNECTED, wedged

        queue.append(_child())
        assert recorder.start_auto_stop(lambda _p: None, max_seconds=20.0,
                                        silence_seconds=0.16)
        time.sleep(0.5)
        assert abandoned == [], (
            f"the abandoned turn finalised as {abandoned!r} on top of the turn that "
            "replaced it; the generation is what stops that"
        )


# ======================================================================================
# Playback: a child that fails on its own is the one failure `play_file` cannot report
# ======================================================================================


@pytest.mark.skipif(not _PW_PLAY, reason="pw-play is not installed here")
class TestPlaybackFailuresAreSurfaced:
    @pytest.fixture
    def player(self, monkeypatch) -> AudioPlayer:
        monkeypatch.setattr(AudioPlayer, "_detect_backend", lambda self: "pw-play")
        return AudioPlayer()

    def test_the_real_pw_play_failing_is_reported_and_still_returns_true(self, player, tmp_path):
        """Measured, not simulated: on this machine the real binary exits 1 on a file
        it cannot read, and `play_file` reports that as a completed reply."""
        unreadable = tmp_path / "not-audio.wav"
        unreadable.write_bytes(b"this is not a RIFF file")

        assert player.play_file(str(unreadable)) is True, (
            "pw-play is expected to fail on this file; if it does not, the binary "
            "behaves differently here and this test is asserting nothing"
        )
        status = player.playback_status()
        assert status is not None, (
            "a playback that failed on its own was forgotten, so the only evidence - "
            "its exit status - is discarded"
        )
        assert status.state is ChildState.EXITED, f"a failed playback is {status}"
        assert status.exit_status == 1, f"expected the real pw-play status, got {status}"

    def test_a_playback_interrupted_by_this_class_is_not_reported_as_a_fault(
        self, player, tmp_path
    ):
        """The control, and the reason `_terminate` records that it signalled.

        A barge-in, a superseded reply and the 120s timeout all end in a signal this
        class sent itself, and the return value already reports them as interruptions.
        Treating those as faults would make `playback_status()` cry wolf on every
        interrupted reply.

        This is measured, not assumed: the real `pw-play` exits **1** when it is sent
        SIGTERM, which is byte-for-byte the status it returns for a file it cannot
        read. An earlier rule - "a positive status means the child failed" - was
        written here, and this test is what proved it wrong.
        """
        spoken = tmp_path / "spoken.wav"
        with wave.open(str(spoken), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("<16000h", *([0] * 16000)))

        results: list = []
        thread = threading.Thread(target=lambda: results.append(player.play_file(str(spoken))),
                                  daemon=True)
        thread.start()
        time.sleep(0.4)
        player.stop()
        thread.join(timeout=20)

        assert results == [False], (
            f"an interrupted playback reported {results!r}; stop() is barge-in, not completion"
        )
        assert player.playback_status() is None, (
            f"a deliberate interruption was reported as a fault: {player.playback_status()}"
        )


# ======================================================================================
# The supervisor's own bookkeeping
# ======================================================================================


class TestTheSupervisorBookkeeping:
    def test_a_heartbeat_for_something_untracked_is_refused_loudly(self, supervisor, caplog):
        """The old `update_heartbeat` returned quietly for a name it did not know.

        That silent no-op is what makes an unwatched child indistinguishable from a
        watched one - the same shape as `_run_host` reporting success over a process
        nobody was tracking.
        """
        caplog.set_level(logging.WARNING, logger="shani_chronoa.child_supervisor")
        assert supervisor.update_heartbeat("nobody") is False
        assert caplog.records, "a heartbeat for an unknown name failed silently"

    def test_retracking_a_name_warns_about_the_child_it_displaced(self, supervisor, caplog):
        """Starting a second child under a forgotten name is how one becomes unreachable."""
        caplog.set_level(logging.WARNING, logger="shani_chronoa.child_supervisor")
        first = _sleep()
        second = _sleep()
        supervisor.track_child("capture", first.poll, pid=first.pid, stall_after=30)
        supervisor.track_child("capture", second.poll, pid=second.pid, stall_after=30)

        assert any(str(first.pid) in r.getMessage() for r in caplog.records), (
            f"the displaced pid {first.pid} was not named in {[r.getMessage() for r in caplog.records]}"
        )
        assert _state(supervisor.check_heartbeats, "capture").pid == second.pid

    def test_every_capture_goes_through_the_watchdog_that_stops_the_previous_one(
        self, monkeypatch
    ):
        """Why the watchdog needs no handle on its child: this is what scopes it.

        `BargeInMonitor` has the same shape of problem in reverse - two live monitor
        threads, and the older one holding a process nobody can reach - so the invariant
        that prevents it is pinned here rather than left as a comment.
        """
        monkeypatch.setattr(AudioRecorder, "_detect_backend", lambda self: "pw-record")
        recorder = AudioRecorder()
        stops: list = []
        real_thread = threading.Thread

        class _Shim:
            """Hands out the real threads, remembering each watchdog's stop flag."""

            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def Thread(self, target=None, args=(), **kwargs):
                if target == recorder._watchdog_loop:
                    stops.append(args[0])
                return real_thread(target=target, args=args, **kwargs)

        monkeypatch.setattr(audio_mod, "threading", _Shim(threading))
        first, second = _sleep(), _sleep()
        try:
            recorder._watch_capture(first)
            recorder._watch_capture(second)
            # Read the flag BEFORE the teardown below sets it. Setting it first and
            # asserting afterwards makes this test pass no matter what `_watch_capture`
            # does - which is exactly what the mutation run caught, and exactly the
            # negative control that cannot fail that AGENTS.md warns about.
            first_was_stopped = len(stops) == 2 and stops[0].is_set()
        finally:
            for stop in stops:
                stop.set()
            first.terminate()
            second.terminate()
            first.wait(timeout=5)
            second.wait(timeout=5)

        assert first_was_stopped, (
            "a second capture did not stop the first capture's watchdog, so two threads "
            "are watching one name and one of them is watching a child that is gone"
        )
