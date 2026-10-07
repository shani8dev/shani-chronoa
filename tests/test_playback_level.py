"""Orb follows playback level: the AudioPlayer streams RMS of the WAV being played.

sayri's `tts.py:113` reads the same WAV it hands to the player, in 50 ms slices, and
reports the level to the orb. Chronoa's player only spawned the subprocess and waited,
so the orb's halo froze during playback. These tests drive the shipped level stream with
a synthetic WAV and a fake playback subprocess - no acoustic claim is made: the
subprocess is substituted (there is no speaker here), the WAV parsing, frame accounting,
RMS, normalisation and callback are shipped code.
"""

from __future__ import annotations

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
from shani_chronoa.audio import AudioPlayer  # noqa: E402

_FRAMERATE = 16000
_SECONDS = 1.0
_AMPLITUDE = 5000  # constant → rms() is exactly this, normalize maps it above the floor


def _wav(path: Path, seconds: float = _SECONDS, amplitude: int = _AMPLITUDE) -> Path:
    frames = int(_FRAMERATE * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_FRAMERATE)
        wf.writeframes(struct.pack(f"<{frames}h", *([amplitude] * frames)))
    return path


class _Proc:
    """A playback subprocess that never really exists."""

    def __init__(self) -> None:
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
    """Blocks in `wait()` until released, so a playback is held mid-flight."""

    def __init__(self) -> None:
        super().__init__()
        self._release = threading.Event()

    def wait(self, timeout=None):
        self._release.wait(30)
        return self.returncode

    def terminate(self) -> None:
        super().terminate()
        self._release.set()

    def release(self) -> None:
        self.returncode = 0
        self._release.set()


def _shim_popen(monkeypatch, procs) -> None:
    class _Shim:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def Popen(self, *args, **kwargs):
            return procs.pop(0)

    monkeypatch.setattr(audio_mod, "subprocess", _Shim(audio_mod.subprocess))


@pytest.fixture
def player(monkeypatch, tmp_path):
    monkeypatch.setattr(AudioPlayer, "_detect_backend", lambda self: "pw-play")
    return AudioPlayer()


def _run_until(predicate, limit: float = 10.0) -> bool:
    deadline = time.time() + limit
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestPlaybackLevels:
    def test_levels_stream_during_playback_and_settle_at_zero(self, player, monkeypatch, tmp_path):
        path = _wav(tmp_path / "reply.wav")
        proc = _WaitingProc()
        _shim_popen(monkeypatch, [proc])
        levels: list[float] = []

        t = threading.Thread(
            target=lambda: player.play_file(str(path), on_level=levels.append), daemon=True
        )
        t.start()
        try:
            # The level thread reads the whole file in ~50 ms slices and posts each.
            assert _run_until(lambda: any(l > 0.0 for l in levels)), "no playback level arrived"
            assert any(l > 0.0 for l in levels)
        finally:
            proc.release()
            t.join(timeout=5)
        assert levels[-1] == 0.0, "the halo must be told to settle when playback ends"

    def test_a_superseded_playback_stops_streaming_levels(self, player, monkeypatch, tmp_path):
        path = _wav(tmp_path / "a.wav")
        first, second = _WaitingProc(), _WaitingProc()
        _shim_popen(monkeypatch, [first, second])
        first_levels: list[float] = []
        second_levels: list[float] = []

        t1 = threading.Thread(
            target=lambda: player.play_file(str(path), on_level=first_levels.append), daemon=True
        )
        t1.start()
        assert _run_until(lambda: any(l > 0.0 for l in first_levels))

        t2 = threading.Thread(
            target=lambda: player.play_file(str(path), on_level=second_levels.append), daemon=True
        )
        t2.start()
        try:
            assert _run_until(lambda: any(l > 0.0 for l in second_levels))
            # The first stream saw a newer generation and went quiet, ending at 0.0.
            assert _run_until(lambda: first_levels and first_levels[-1] == 0.0)
            assert first.terminated
        finally:
            second.release()
            t1.join(timeout=5)
            t2.join(timeout=5)
        assert second_levels[-1] == 0.0

    def test_play_bytes_forwards_the_level_callback(self, player, monkeypatch, tmp_path):
        import io

        path = _wav(tmp_path / "b.wav")
        data = path.read_bytes()
        proc = _WaitingProc()
        _shim_popen(monkeypatch, [proc])
        levels: list[float] = []

        t = threading.Thread(
            target=lambda: player.play_bytes(data, on_level=levels.append), daemon=True
        )
        t.start()
        try:
            assert _run_until(lambda: any(l > 0.0 for l in levels))
        finally:
            proc.release()
            t.join(timeout=5)
        assert levels[-1] == 0.0
