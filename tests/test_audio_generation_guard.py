"""Every callback the audio pipeline hands back must belong to the turn that asked for it.

Chronoa's capture and playback all run on threads the app does not own. `pw-record` blocks
in `read()` for a whole frame, a capture clears its own "I am busy" state before it delivers
its result, and `stop()` gives up on a thread after a two-second join whether or not the
thread actually died. So a callback can be produced by a turn that has already been replaced,
and nothing about the callback itself says so.

That is the class of bug qwen-code's `sessionIdRef` generation counter exists to kill
(`packages/cli/src/ui/hooks/use-voice-input.ts:268`), and the one its comment at `:452` names
outright: a tap-stop and a native silence auto-stop can both fire, so one session gets
finalised twice. The same counter shape is in Codex's `set_disabled()` epoch
(`codex-rs/voice-host/src/device_buffers.rs:162-175`) and in its post-unmute backlog
rejection (`:178-211`), which refuses the first callback after unmute because its timestamp
may predate the unmute.

Each test here drives the real loop with synthetic PCM, in the way `AGENTS.md` records for
`BargeInMonitor` - "only the detection logic itself was verified, via synthetic PCM fed
directly into the same code path". **No acoustic claim is made anywhere in this file**: there
is no microphone, speaker or PipeWire graph on this machine, so nothing here says whether a
threshold, a ring size or a gate is right for a real room. What each test says is that a
callback belonging to a superseded turn does not reach the caller.

Two things are substituted, and only these two: the capture/playback subprocess (there is no
microphone to spawn) and, for the wake-word loop, `numpy`/`openwakeword` (neither is
installed here). The loops, the calibration, the detector, the frame accounting, the
generation arithmetic and the callback dispatch are all shipped code. Where a barrier holds a
loop at a chosen point it sits at a filesystem call the loop makes *after* the behaviour
under test, and it is called out where it appears.
"""

from __future__ import annotations

import inspect
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import audio as audio_mod  # noqa: E402
from shani_chronoa import wakeword as wakeword_mod  # noqa: E402
from shani_chronoa.audio import (  # noqa: E402
    _CALIBRATION_FRAMES,
    _FRAME_BYTES,
    AudioPlayer,
    AudioRecorder,
    BargeInMonitor,
    _stream_capture_cmd,
)

_PW_RECORD = shutil.which("pw-record")

# Amplitudes chosen so each is unmistakable in the captured bytes. `vad.rms` of a
# constant-amplitude s16le frame is exactly that amplitude, so a threshold can be reasoned
# about arithmetically instead of guessed.
_SPEECH = 5000
_LOUD = 9000
_SHOUT = 20000
_SILENT = 0

# Two silent frames end a turn at `silence_seconds=0.16` and a 0.08s frame size.
_SILENCE_SECONDS = 0.16


def _pcm(amplitude: int, nbytes: int = _FRAME_BYTES) -> bytes:
    """A fixed-size frame of constant-amplitude s16le PCM."""
    return struct.pack(f"<{nbytes // 2}h", *([amplitude] * (nbytes // 2)))


def _speech_turn() -> list:
    """Frames for a capture that hears speech and then trailing silence.

    Four quiet frames calibrate the floor, two of speech set `heard_speech`, and two silent
    frames finish the turn.
    """
    return (
        [_pcm(_SILENT) for _ in range(_CALIBRATION_FRAMES)]
        + [_pcm(_SPEECH) for _ in range(2)]
        + [_pcm(_SILENT) for _ in range(2)]
    )


def _silence_turn() -> list:
    """A capture that never hears speech, so it finalises as `on_done(None)`."""
    return [_pcm(_SILENT) for _ in range(_CALIBRATION_FRAMES + 4)]


class _Capture:
    """Stands in for `pw-record`'s stdout: a fixed list of frames, then end of stream."""

    def __init__(self, frames: list) -> None:
        self._frames = list(frames)
        self.reads = 0

    def read(self, size: int) -> bytes:
        self.reads += 1
        return self._frames.pop(0) if self._frames else b""

    def close(self) -> None:  # pragma: no cover - parity with a real pipe
        pass


class _HookedCapture(_Capture):
    """Runs a hook while a chosen frame's `read()` is in flight.

    That is the only place a stop or a cancel can be landed deterministically: the loop has
    snapshotted the state it later compares against, and has not yet judged the frame.
    """

    def __init__(self, frames: list, on_read) -> None:
        super().__init__(frames)
        self._on_read = on_read

    def read(self, size: int) -> bytes:
        chunk = super().read(size)
        self._on_read(chunk)
        return chunk


class _Proc:
    """A capture/playback subprocess that never really exists."""

    def __init__(self, stdout=None) -> None:
        self.stdout = stdout
        self.returncode = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _WaitingProc(_Proc):
    """Blocks in `wait()` until released, so a playback can be held mid-flight."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = threading.Event()
        self._release = threading.Event()

    def wait(self, timeout=None):
        self.waiting.set()
        self._release.wait(30)
        return self.returncode

    def terminate(self) -> None:
        # Releasing here as well as on an explicit release() keeps `_terminate`'s
        # post-terminate `wait()` from spending its own two-second budget.
        super().terminate()
        self._release.set()

    def release(self) -> None:
        self.returncode = 0
        self._release.set()


def _shim_popen(monkeypatch, module, factory):
    """Give `module.subprocess` a `Popen` that hands out fakes, leaving the rest real.

    Scoped to the one module under test, so nothing else in the process - `wave`,
    `tempfile`, the espeak-based tests in `test_speech_paths.py` - sees a substituted
    `subprocess`.
    """

    class _Shim:
        def __init__(self, real, popen):
            self._real = real
            self._popen = popen

        def __getattr__(self, name):
            return getattr(self._real, name)

        def Popen(self, *args, **kwargs):
            return self._popen(*args, **kwargs)

    monkeypatch.setattr(module, "subprocess", _Shim(module.subprocess, factory))


def _run_until(predicate, limit: float = 20.0) -> bool:
    """Wait for a background thread to reach a point, without asserting inside a spin."""
    deadline = time.time() + limit
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def recorder(monkeypatch):
    """A real `AudioRecorder` that captures synthetic PCM instead of spawning a microphone."""
    monkeypatch.setattr(AudioRecorder, "_detect_backend", lambda self: "pw-record")
    return AudioRecorder()


# --------------------------------------------------------------------------------------
# Task 1 - the recorder must not finalise a turn that has been replaced
# --------------------------------------------------------------------------------------


class TestTheSupersessionWindowIsReal:
    """The premise every guard below rests on, asserted so it cannot quietly stop holding.

    A capture clears `_proc` and `_auto_stop_thread` in its own `finally` and delivers
    `on_done` afterwards, so while its callback is running the recorder already looks idle
    and a new capture is accepted. If this ever stops being true the window has been closed by
    construction and the guards are unreachable - worth knowing, rather than something to
    discover from a test that has quietly become vacuous.
    """

    def test_a_capture_releases_itself_before_delivering_its_result(self, recorder, monkeypatch):
        seen: dict = {}

        def _on_done(path):
            seen["restarted"] = recorder.start_auto_stop(
                lambda _p: None, max_seconds=5.0, silence_seconds=_SILENCE_SECONDS
            )
            seen["delivered"] = path

        queue = [_Capture(_speech_turn()), _Capture(_silence_turn())]
        _shim_popen(monkeypatch, audio_mod, lambda *a, **k: _Proc(queue.pop(0)))

        assert recorder.start_auto_stop(
            _on_done, max_seconds=5.0, silence_seconds=_SILENCE_SECONDS
        )
        assert _run_until(lambda: "delivered" in seen), "the capture never finished"

        assert seen["restarted"] is True and seen["delivered"], (
            "the recorder no longer looks idle while its own callback runs, so the "
            "supersession window is gone and the guards below are unreachable"
        )
        # The captured WAV is the caller's to delete (`app._transcribe` unlinks it in a
        # `finally`); this callback is the caller here.
        os.unlink(seen["delivered"])


class TestASupersededCaptureIsNotFinalised:
    """The one session finalised twice, in the form this code can actually reach.

    With the window above open, anything that starts a new capture while the old result is in
    flight - a wake word, a barge-in interrupt, the orb - replaces a turn that has not been
    handed over yet. The replaced turn's result then arrives after the new turn's, and the
    caller finalises over the top of a turn that is still recording: the orb goes back to
    idle with the microphone open, and the old audio is submitted as a fresh turn.
    """

    def test_a_capture_replaced_before_its_callback_is_delivered_is_dropped(
        self, recorder, monkeypatch
    ):
        delivered: list = []
        second_started: list = []

        # A barrier, not a stub: it holds turn 1 inside the real WAV write, at the point where
        # the recorder has already released itself and has not yet called back.
        arrived = threading.Event()
        release = threading.Event()
        written: list = []
        real_mkstemp = tempfile.mkstemp

        def _record_path(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            written.append(path)
            return fd, path

        def _gated_mkstemp(*args, **kwargs):
            arrived.set()
            release.wait(20)
            return _record_path(*args, **kwargs)

        monkeypatch.setattr(tempfile, "mkstemp", _gated_mkstemp)

        # Turn 1 hears speech and produces a WAV; turn 2 hears nothing and finalises as None.
        # So a surviving callback can only be turn 2's - the two are told apart by the value
        # itself, not by the order they happen to arrive in.
        queue = [_Capture(_speech_turn()), _Capture(_silence_turn())]
        _shim_popen(monkeypatch, audio_mod, lambda *a, **k: _Proc(queue.pop(0)))

        assert recorder.start_auto_stop(
            delivered.append, max_seconds=5.0, silence_seconds=_SILENCE_SECONDS
        )
        assert arrived.wait(20), "the first capture never reached its WAV write"

        second_started.append(
            recorder.start_auto_stop(
                delivered.append, max_seconds=5.0, silence_seconds=_SILENCE_SECONDS
            )
        )
        release.set()

        assert _run_until(lambda: delivered), "neither turn ever finalised"
        time.sleep(0.3)  # let a late, dropped callback arrive if one is going to

        assert second_started == [True], (
            "the second capture was refused, so this test is not exercising the window it "
            "is named for"
        )
        assert delivered == [None], (
            f"expected only the second turn's result (None - it heard no speech), got "
            f"{delivered}: a superseded capture was finalised over a live turn"
        )
        assert written and not os.path.exists(written[0]), (
            "the superseded turn's WAV was left on disk; dropping the callback is not enough "
            "if the file it named survives"
        )

    def test_a_capture_that_is_not_superseded_still_finalises(self, recorder, monkeypatch):
        """The control the test above needs: the guard is not simply refusing everything."""
        delivered: list = []
        queue = [_Capture(_speech_turn())]
        _shim_popen(monkeypatch, audio_mod, lambda *a, **k: _Proc(queue.pop(0)))

        assert recorder.start_auto_stop(
            delivered.append, max_seconds=5.0, silence_seconds=_SILENCE_SECONDS
        )
        assert _run_until(lambda: delivered), "an ordinary capture never finalised"
        assert delivered[0], f"expected a WAV path, got {delivered[0]!r}"
        os.unlink(delivered[0])


class TestBothRecorderGuardsExist:
    """The generation is checked twice, and only the later one is reachable here.

    One check sits before the audio is joined and written, guarding the `on_done(None)` path;
    one sits after the write, guarding the `on_done(path)` path. The barrier test above
    exercises the second. The first lives in a window containing nothing but `b"".join`, so no
    fixture that declines to instrument a builtin can land inside it deterministically. It is
    pinned structurally here and that limit is stated rather than papered over.
    """

    def test_the_generation_is_checked_before_and_after_the_wav_is_written(self):
        source = inspect.getsource(AudioRecorder._auto_stop_loop)
        checks = [ln for ln in source.splitlines() if "self._generation != generation" in ln]
        assert len(checks) >= 2, (
            "the generation is only checked once; a supersession during the WAV write would "
            "deliver a stale result"
        )


class TestBargeInAndWakeWordGenerations:
    """The same guard on the two callbacks that can outlive a playback.

    Both loops are called directly with an explicit generation, so both directions are
    pinned. Asserting only the stale case would pass just as happily against a loop that never
    fires at all - which is why each stale test is paired with a current-generation one on the
    same fixture.
    """

    @staticmethod
    def _monitor(monkeypatch) -> BargeInMonitor:
        monkeypatch.setattr(BargeInMonitor, "_detect_backend", lambda self: "pw-record")
        monitor = BargeInMonitor()
        monitor._generation = 7
        # Open the gate so the loop does not sit out its pre-playback guard on every test.
        monitor.begin_playback()
        return monitor

    @staticmethod
    def _monitor_stream() -> _Capture:
        # A quiet room, then genuinely loud audio. Four silent frames calibrate a floor of
        # 500 * _PLAYBACK_MARGIN, which the loud frame clears by a wide margin.
        # three loud frames: one peak no longer interrupts (audio._BARGE_IN_FRAMES)
        return _Capture([_pcm(_SILENT) for _ in range(_CALIBRATION_FRAMES)] + [_pcm(_LOUD)] * 3)

    @staticmethod
    def _wakeword(monkeypatch, heard: str = "Hey Chronoa."):
        # whisper.cpp is the only thing substituted: it is not installed here, and what it
        # would transcribe is the input under test, not the code. Calibration, utterance
        # segmentation, the phrase match, the generation check and the dispatch are all
        # shipped code.
        listener = wakeword_mod.WakeWordListener()
        listener._backend = "pw-record"
        listener._generation = 3
        monkeypatch.setattr(listener, "_transcribe", lambda pcm: heard)
        return listener

    @staticmethod
    def _wakeword_stream() -> _Capture:
        # a quiet room to calibrate on, a short utterance, then the silence that ends it
        return _Capture([_pcm(_SILENT) for _ in range(wakeword_mod._CALIBRATION_FRAMES)]
                        + [_pcm(_LOUD) for _ in range(6)]
                        + [_pcm(_SILENT) for _ in range(wakeword_mod._END_SILENCE_FRAMES)])

    @staticmethod
    def _drive(target, proc, capture, fired, generation):
        thread = threading.Thread(
            target=target,
            args=(proc, lambda: fired.append(True), generation),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=20)
        return fired

    def test_a_current_monitor_still_interrupts(self, monkeypatch):
        monitor = self._monitor(monkeypatch)
        fired = self._drive(
            monitor._monitor_loop,
            type("P", (), {"stdout": self._monitor_stream()}),
            None,
            [],
            monitor._generation,
        )
        assert fired, "the loop no longer detects real speech; the guard is not the reason"

    def test_a_monitor_from_an_older_playback_cannot_interrupt(self, monkeypatch):
        monitor = self._monitor(monkeypatch)
        fired: list = []
        monitor._monitor_loop(
            type("P", (), {"stdout": self._monitor_stream()})(),
            lambda: fired.append(True),
            monitor._generation - 1,
        )
        assert not fired, (
            "a barge-in belonging to a reply that has already ended still opened the "
            "microphone; barge-in must only ever act on the current turn"
        )

    def test_a_frame_in_flight_when_the_monitor_was_stopped_is_never_judged(self, monkeypatch):
        """The post-boundary backlog rejection, with a real `stop()` landing mid-read.

        Codex's `CaptureBoundary` (`device_buffers.rs:178-211`) refuses the first callback
        after unmute because its timestamp may predate the unmute. Same shape here: the
        monitor's `read()` is parked when `stop()` runs, and the frame it eventually returns
        was captured before the stop. `_monitor_loop` is driven directly here, so `stop()` has
        no thread of its own to join and returns at once.

        Measured limitation, recorded rather than hidden: `stop()` bumps *both* the stop epoch
        and the generation, and the generation check sits downstream of the epoch check, so
        deleting the epoch check alone leaves this test **green** (mutation measured: 0 red).
        This test therefore pins the pair, not the epoch specifically. The epoch check is not
        dead - it runs on every frame - it is simply not separable from the generation check
        through the public `stop()` path, because no public operation bumps one without the
        other. The one place the two *are* separable is the recorder above, where
        `cancel_auto_stop()` bumps the epoch alone on purpose, and there deleting the epoch
        check does go red.
        """
        monitor = self._monitor(monkeypatch)
        parked = threading.Event()
        release = threading.Event()
        loud = _pcm(_LOUD)

        def _park(chunk):
            if chunk is loud:
                parked.set()
                # Two events, not one: `Event.set()` followed by `wait()` on the same event
                # returns immediately, which would park nothing at all.
                release.wait(20)

        capture = _HookedCapture(
            [_pcm(_SILENT) for _ in range(_CALIBRATION_FRAMES)] + [loud], _park
        )
        fired: list = []
        thread = threading.Thread(
            target=monitor._monitor_loop,
            args=(type("P", (), {"stdout": capture})(), lambda: fired.append(True), monitor._generation),
            daemon=True,
        )
        thread.start()
        assert parked.wait(20), "the monitor never reached its main loop"
        monitor.stop()
        release.set()
        thread.join(timeout=20)

        assert not fired, (
            "a frame captured before the monitor was stopped was still judged, so a stopped "
            "monitor can interrupt a reply that had already finished"
        )

    def test_a_current_wake_word_session_still_fires(self, monkeypatch):
        listener = self._wakeword(monkeypatch)
        fired: list = []
        listener._listen_loop(
            type("P", (), {"stdout": self._wakeword_stream()})(),
            lambda: fired.append(True),
            listener._generation,
        )
        assert fired, "the loop no longer scores a detection; the guard is not the reason"

    def test_a_wake_word_from_an_older_session_cannot_open_the_microphone(self, monkeypatch):
        listener = self._wakeword(monkeypatch)
        fired: list = []
        listener._listen_loop(
            type("P", (), {"stdout": self._wakeword_stream()})(),
            lambda: fired.append(True),
            listener._generation - 1,
        )
        assert not fired, (
            "a stopped or replaced wake-word session still opened the microphone; two live "
            "listen loops are possible because stop() clears _thread after a bounded join"
        )

    def test_a_wake_word_frame_in_flight_at_stop_is_dropped(self, monkeypatch):
        """Isolates the stop-epoch check by advancing it on its own.

        `stop()` would advance the epoch and the generation together, which - as the monitor
        test above records - makes the two checks indistinguishable. Advancing only the epoch
        here is not a production-reachable state; it is the narrowest way to show the check
        works at all, and it is why this mutation goes red where the monitor's does not
        (measured: 1 red).
        """
        listener = self._wakeword(monkeypatch)
        parked = threading.Event()
        release = threading.Event()
        # Parked on the silent frame that ENDS a real utterance: without the stop-epoch
        # check, that frame is what sends the utterance to be judged and fires.
        last = _pcm(_SILENT)

        def _park(chunk):
            if chunk is last:
                parked.set()
                release.wait(20)

        frames = ([_pcm(_SILENT) for _ in range(wakeword_mod._CALIBRATION_FRAMES)]
                  + [_pcm(_LOUD) for _ in range(6)]
                  + [_pcm(_SILENT) for _ in range(wakeword_mod._END_SILENCE_FRAMES - 1)] + [last])
        capture = _HookedCapture(frames, _park)
        fired: list = []
        thread = threading.Thread(
            target=listener._listen_loop,
            args=(type("P", (), {"stdout": capture})(), lambda: fired.append(True), listener._generation),
            daemon=True,
        )
        thread.start()
        assert parked.wait(20), "the listener never read a frame"
        listener._stop_epoch += 1
        release.set()
        thread.join(timeout=20)

        assert not fired, "a frame captured before the stop was still scored"

    def test_stopping_the_listener_invalidates_its_generation(self, monkeypatch):
        """`stop()` has to bump it, or a loop still blocked in `read()` stays current."""
        listener = self._wakeword(monkeypatch)
        before = listener._generation
        listener.stop()
        assert listener._generation != before, (
            "stop() left the generation alone, so a loop that outlives the bounded join can "
            "still report a detection for a session that is over"
        )


class TestAFrameFromAcrossTheStopBoundaryIsDropped:
    """`cancel_auto_stop()` is a boundary, and a blocking read straddles it.

    The loop checks the cancel flag at the top of each iteration and then blocks in `read()`
    for up to a frame, so a frame captured across the press used to be appended and fed to
    the detector. That is not cosmetic: feeding it can flip `heard_speech`, which is what
    decides between `on_done(None)` and a transcript, so a tap-to-stop could turn "didn't
    catch that" into a turn built mostly from audio the user said *after* asking to stop.
    """

    def test_a_frame_read_across_the_cancel_is_not_recorded(self, recorder, monkeypatch):
        spoken = _pcm(_SPEECH)
        shouted = _pcm(_SHOUT)
        # two spoken frames: speech onset needs two in a row (vad.SilenceDetector.ONSET_FRAMES)
        frames = [_pcm(_SILENT) for _ in range(_CALIBRATION_FRAMES)] + [spoken, spoken]

        def _cancel_during_shout(chunk):
            if chunk is shouted:
                recorder.cancel_auto_stop()

        capture = _HookedCapture(frames + [shouted, _pcm(_SILENT)], _cancel_during_shout)
        delivered: list = []
        _shim_popen(monkeypatch, audio_mod, lambda *a, **k: _Proc(capture))
        assert recorder.start_auto_stop(
            delivered.append, max_seconds=5.0, silence_seconds=_SILENCE_SECONDS
        )

        assert _run_until(lambda: delivered), "the capture never finished; the fixture is wrong"
        path = delivered[0]
        assert path, f"expected a transcript, got {path!r}"
        with wave.open(path, "rb") as wf:
            pcm = wf.readframes(wf.getnframes())
        os.unlink(path)

        assert spoken in pcm, (
            "the speech said before the press was discarded too; the guard is too broad"
        )
        assert shouted not in pcm, (
            "a frame read across the cancel was still recorded: the user asked to stop, and "
            "audio captured after that press is not part of the turn they asked for"
        )


class TestTheRecorderLevelsAreGenerationKeyed:
    """`on_level` drives the orb's halo, so a superseded capture's level is equally wrong.

    Held to the same standard as the callbacks: a stale generation must not emit and a current
    one must. Which of the two a real superseded capture would take is not something this
    environment can produce - the recorder serialises captures on `_auto_stop_thread`, so no
    level can be emitted after a replacement - so this pins the guard rather than claiming a
    reproduction.
    """

    def _run(self, recorder, monkeypatch, generation):
        levels: list = []
        recorder._generation = 5
        thread = threading.Thread(
            target=recorder._auto_stop_loop,
            # A silent turn, so the loop reports levels for every frame it reads but
            # finalises as `on_done(None)` and never writes a WAV for this test to clean up.
            args=(
                _Proc(_Capture(_silence_turn())),
                lambda _path: None,
                5.0,
                _SILENCE_SECONDS,
                levels.append,
                generation,
            ),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=20)
        return levels

    def test_a_stale_capture_does_not_report_a_level(self, recorder, monkeypatch):
        assert self._run(recorder, monkeypatch, 4) == [], (
            "a superseded capture still reported input levels"
        )

    def test_a_current_capture_still_reports_a_level(self, recorder, monkeypatch):
        assert self._run(recorder, monkeypatch, 5), "the capture no longer reports a level"


# --------------------------------------------------------------------------------------
# Tasks 1 and 4 - an older turn's output must not keep talking
# --------------------------------------------------------------------------------------


class TestAnOlderReplyDoesNotKeepTalking:
    """`play_file` runs in a worker thread and the bridge does not serialise those calls.

    `self._proc` names one subprocess, so before the fix the previous playback was simply
    lost: `stop()` found the newer process, the older voice ran to completion over the new
    turn, and nothing reported it. Starting a playback now ends the one it replaces, and the
    return value says which of the two things happened.
    """

    def _player(self, monkeypatch) -> AudioPlayer:
        monkeypatch.setattr(AudioPlayer, "_detect_backend", lambda self: "pw-play")
        return AudioPlayer()

    def test_a_newer_reply_stops_the_one_it_replaces(self, monkeypatch):
        player = self._player(monkeypatch)
        first, second = _WaitingProc(), _WaitingProc()
        queue = [first, second]
        _shim_popen(monkeypatch, audio_mod, lambda *a, **k: queue.pop(0))

        results: list = []
        older = threading.Thread(
            target=lambda: results.append(player.play_file("/tmp/older.wav")), daemon=True
        )
        older.start()
        assert first.waiting.wait(20), "the first playback never started"

        newer = threading.Thread(
            target=lambda: results.append(player.play_file("/tmp/newer.wav")), daemon=True
        )
        newer.start()
        assert second.waiting.wait(20), "the second playback never started"

        assert first.terminated, (
            "the earlier reply's subprocess is still running with nothing able to reach it; "
            "it will keep talking over the turn that replaced it"
        )
        second.release()
        newer.join(timeout=20)
        first.release()
        older.join(timeout=20)

        assert sorted(results) == [False, True], (
            f"expected the replaced playback to report False and the live one True, got "
            f"{results}; a caller cannot otherwise tell a finished reply from an abandoned one"
        )

    def test_barge_in_still_stops_playback_and_now_says_so(self, monkeypatch):
        player = self._player(monkeypatch)
        proc = _WaitingProc()
        _shim_popen(monkeypatch, audio_mod, lambda *a, **k: proc)

        results: list = []
        thread = threading.Thread(
            target=lambda: results.append(player.play_file("/tmp/reply.wav")), daemon=True
        )
        thread.start()
        assert proc.waiting.wait(20), "the playback never started"
        player.stop()
        thread.join(timeout=20)

        assert results == [False], (
            "an interrupted playback reported success; stop() is barge-in, not completion"
        )

    def test_an_uninterrupted_reply_still_reports_success(self, monkeypatch):
        player = self._player(monkeypatch)
        proc = _WaitingProc()
        _shim_popen(monkeypatch, audio_mod, lambda *a, **k: proc)

        results: list = []
        thread = threading.Thread(
            target=lambda: results.append(player.play_file("/tmp/reply.wav")), daemon=True
        )
        thread.start()
        assert proc.waiting.wait(20), "the playback never started"
        proc.release()
        thread.join(timeout=20)

        assert results == [True], "a playback that ran to completion no longer reports success"


# --------------------------------------------------------------------------------------
# Task 2 - the capture ring is pinned, and the pin is one pw-record accepts
# --------------------------------------------------------------------------------------


class TestTheCaptureRingIsPinned:
    def test_capture_passes_an_explicit_latency(self):
        cmd = _stream_capture_cmd("pw-record")
        assert "--latency" in cmd, (
            "pw-record's ring size is left to a default that moves between PipeWire releases"
        )
        assert cmd[cmd.index("--latency") + 1] == audio_mod._CAPTURE_LATENCY == "100ms"
        assert cmd[-1] == "-", "the stdout argument must stay last"

    def test_the_wake_word_listener_pins_the_same_ring(self, monkeypatch):
        """Two paths record from one microphone; a different ring on each is a real defect."""
        monkeypatch.setattr(
            wakeword_mod.WakeWordListener, "_detect_backend", lambda self: "pw-record"
        )
        cmd = wakeword_mod.WakeWordListener()._record_cmd()
        assert cmd[cmd.index("--latency") + 1] == audio_mod._CAPTURE_LATENCY
        assert cmd[-1] == "-"

    def test_target_still_lands_where_it_belongs(self):
        cmd = _stream_capture_cmd("pw-record", "alsa_input.foo.__source")
        assert cmd[cmd.index("--target") + 1] == "alsa_input.foo.__source"
        assert cmd[-1] == "-"

    def test_alsa_capture_is_left_alone(self):
        """`arecord` has no `--latency`; passing one would stop it recording at all."""
        assert "--latency" not in _stream_capture_cmd("arecord")


@pytest.mark.skipif(not _PW_RECORD, reason="pw-record is not installed here")
class TestTheInstalledPwRecordAcceptsThePin:
    """Checked against the real binary, with its own control.

    A negative control that cannot fail is not a control, so the same test also feeds a
    deliberately malformed value and requires the binary to reject *that* one. Without it,
    "no error on stderr" would pass just as happily against a `pw-record` that rejected every
    value there was.
    """

    @staticmethod
    def _stderr_for(latency: str) -> str:
        proc = subprocess.Popen(
            ["pw-record", "--latency", latency, "--rate", "16000", "--channels", "1",
             "--format", "s16", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            _, err = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            # No capture device here, so the real binary blocks trying to open one. Reaching
            # that point is itself the evidence the flag was accepted.
            proc.kill()
            _, err = proc.communicate()
        return err.decode("utf-8", "replace")

    def test_the_pinned_value_parses_and_a_broken_one_does_not(self):
        good = self._stderr_for(audio_mod._CAPTURE_LATENCY)
        assert "unrecognized option" not in good, good
        assert "bad latency value" not in good, good

        bad = self._stderr_for("100zzz")
        assert "bad latency value" in bad, (
            f"the control did not fail, so the assertion above cannot fail either; stderr "
            f"was {bad!r}"
        )


# --------------------------------------------------------------------------------------
# Task 4 - what the barge-in gate already gets right, and what it does not have
# --------------------------------------------------------------------------------------


class TestWhatBargeInAlreadyGotRight:
    """Codex's `suppress_active_realtime_speaker()`
    (`tui/src/chatwidget/realtime/recording_controls.rs:68-99`) keys a *speaker suppression*
    flag to a turn generation, because muting at every turn start is the defect its comment at
    `:69-70` names: "Quiet turns must not wait for captions before accepting their first audio
    packets" - the assistant goes silent for the first second.

    Chronoa has no speaker suppression at all, so that exact gate has nothing to key. The
    reason not to add one is worth pinning rather than leaving to prose: `BargeInMonitor` only
    listens and calls back, and the single place output is stopped is `app._begin_listening`,
    reached only from an explicit orb press, a wake word or a barge-in interrupt - never from
    a turn merely starting. The gate that does exist opens at playback rather than at turn
    start, which is the same intent expressed as "closed until the speaker is known live"
    instead of "keyed to a turn generation"; `tests/test_barge_in_gate.py` pins that in five
    tests, which are not duplicated here.
    """

    def test_the_monitor_has_no_speaker_suppression_to_mis_key(self):
        source = inspect.getsource(BargeInMonitor)
        for forbidden in ("def suppress", "self._suppressed", "def mute", "self._muted"):
            assert forbidden not in source, (
                f"{forbidden!r} appeared on BargeInMonitor; if the monitor can now silence "
                f"output it needs the turn generation Codex keys its gate on, and this is "
                f"the reminder"
            )

    def test_a_replaced_reply_cannot_interrupt_the_turn_that_replaced_it(self, monkeypatch):
        """The half of Codex's gate that does apply, and the half that could have happened.

        A stale turn *signal* acting on the current turn is the barge-in analogue of a stale
        turn *output* playing over it, and both are now keyed to a generation. The mechanism
        that made it reachable is in the monitor tests above: `stop()` clears `_thread` after
        a bounded join, so an older loop can outlive it.
        """
        monkeypatch.setattr(BargeInMonitor, "_detect_backend", lambda self: "pw-record")
        monitor = BargeInMonitor()
        monitor._generation = 2
        monitor.begin_playback()
        capture = _Capture([_pcm(_SILENT) for _ in range(_CALIBRATION_FRAMES)] + [_pcm(_LOUD)])
        fired: list = []
        monitor._monitor_loop(
            type("P", (), {"stdout": capture})(), lambda: fired.append(True), 1
        )
        assert not fired, "barge-in from the previous turn still opened the microphone"
