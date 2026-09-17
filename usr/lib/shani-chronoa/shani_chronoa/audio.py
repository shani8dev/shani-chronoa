"""Microphone capture and audio playback for voice input/output.

Shells out to PipeWire's pw-record/pw-play (falling back to ALSA's
arecord/aplay) rather than adding a GStreamer or PortAudio dependency -
these tools are already present on any PipeWire desktop and handle device
selection and format conversion for us.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from typing import Callable, Optional

from shani_chronoa.vad import SilenceDetector, calibrate_noise_floor, rms

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_WIDTH = 2
_FRAME_SECONDS = 0.08
_FRAME_BYTES = int(_SAMPLE_RATE * _FRAME_SECONDS) * _SAMPLE_WIDTH
_CALIBRATION_FRAMES = 4  # ~320ms of ambient audio sampled before listening for speech


def _stream_capture_cmd(backend: str) -> list:
    """Raw s16le PCM to stdout - confirmed live (see wakeword.py's docstring)
    that both backends stream from byte zero with no WAV header when piped."""
    if backend == "pw-record":
        return ["pw-record", "--rate", str(_SAMPLE_RATE), "--channels", str(_CHANNELS), "--format", "s16", "-"]
    return ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(_SAMPLE_RATE), "-c", str(_CHANNELS), "-"]


class AudioRecorder:
    """Microphone recorder producing 16kHz mono WAV files.

    Auto-stops on silence (`start_auto_stop`) rather than requiring an
    explicit "stop recording" call - see that method's docstring.
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._backend = self._detect_backend()
        self._auto_stop_thread: Optional[threading.Thread] = None
        self._auto_stop_cancel = threading.Event()

    def _detect_backend(self) -> Optional[str]:
        if shutil.which("pw-record"):
            return "pw-record"
        if shutil.which("arecord"):
            return "arecord"
        return None

    def is_available(self) -> bool:
        """Check if a recording backend is installed."""
        return self._backend is not None

    def start_auto_stop(
        self,
        on_done: Callable[[Optional[str]], None],
        max_seconds: float = 20.0,
        silence_seconds: float = 1.2,
    ) -> bool:
        """Start recording; auto-stops once the user stops talking (or times out).

        Borrowed from how `aside` and `loquivox` avoid a second explicit
        "stop listening" action - the whole point of wake-word activation
        is hands-free, so requiring a manual click to end the turn defeats
        that. Runs on a background thread; `on_done(path_or_None)` fires
        from that thread, not the GTK main thread - callers must marshal
        back via `GLib.idle_add`, same convention as `WakeWordListener`.
        """
        if self._proc is not None or self._auto_stop_thread is not None or not self._backend:
            return False
        try:
            proc = subprocess.Popen(
                _stream_capture_cmd(self._backend), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error(f"Failed to start auto-stop recording ({self._backend}): {e}")
            return False

        self._proc = proc
        self._auto_stop_cancel.clear()
        self._auto_stop_thread = threading.Thread(
            target=self._auto_stop_loop, args=(proc, on_done, max_seconds, silence_seconds), daemon=True
        )
        self._auto_stop_thread.start()
        return True

    def cancel_auto_stop(self) -> None:
        """Signal an in-progress auto-stop capture to end early (e.g. the user pressed the orb)."""
        self._auto_stop_cancel.set()

    def _auto_stop_loop(
        self, proc: subprocess.Popen, on_done: Callable[[Optional[str]], None], max_seconds: float, silence_seconds: float
    ) -> None:
        stdout = proc.stdout
        chunks: list = []
        detector: Optional[SilenceDetector] = None
        try:
            calib_frames = []
            for _ in range(_CALIBRATION_FRAMES):
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if not chunk:
                    break
                calib_frames.append(chunk)
                chunks.append(chunk)
            threshold = calibrate_noise_floor(calib_frames)
            detector = SilenceDetector(threshold=threshold, silence_seconds=silence_seconds, frame_seconds=_FRAME_SECONDS)

            deadline = time.monotonic() + max_seconds
            while time.monotonic() < deadline and not self._auto_stop_cancel.is_set():
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if not chunk:
                    break
                chunks.append(chunk)
                detector.feed(chunk)
                if detector.is_done():
                    break
        except Exception as e:
            logger.error(f"Auto-stop recording loop crashed: {e}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            self._proc = None
            self._auto_stop_thread = None

        raw = b"".join(chunks)
        if not raw or detector is None or not detector.heard_speech:
            on_done(None)
            return

        fd, path = tempfile.mkstemp(suffix=".wav", prefix="chronoa-rec-")
        os.close(fd)
        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(_CHANNELS)
                wf.setsampwidth(_SAMPLE_WIDTH)
                wf.setframerate(_SAMPLE_RATE)
                wf.writeframes(raw)
        except Exception as e:
            logger.error(f"Failed to write captured audio: {e}")
            os.remove(path) if os.path.exists(path) else None
            on_done(None)
            return
        on_done(path)


class AudioPlayer:
    """Plays WAV audio through the default output device.

    Playback runs as a subprocess tracked on `self._proc` so a concurrent
    call to `stop()` (from the GTK main thread, e.g. the user pressing the
    orb again or a wake word firing while the assistant is still talking)
    can interrupt it early - this is Chronoa's barge-in mechanism. It's a
    "the user started talking again, stop" interrupt, not full-duplex
    simultaneous listen+speak (that would need a live VAD loop during
    playback, a bigger undertaking not attempted here).
    """

    def __init__(self) -> None:
        self._backend = self._detect_backend()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def _detect_backend(self) -> Optional[str]:
        if shutil.which("pw-play"):
            return "pw-play"
        if shutil.which("aplay"):
            return "aplay"
        return None

    def is_available(self) -> bool:
        """Check if a playback backend is installed."""
        return self._backend is not None

    def play_file(self, path: str) -> bool:
        """Play a WAV file, blocking until playback finishes or is stopped."""
        if not self._backend:
            return False
        try:
            with self._lock:
                proc = subprocess.Popen(
                    [self._backend, path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._proc = proc
        except Exception as e:
            logger.error(f"Playback failed ({self._backend}): {e}")
            return False

        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None
        return True

    def stop(self) -> None:
        """Interrupt any in-progress playback (barge-in). No-op if idle."""
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    def play_bytes(self, data: bytes) -> bool:
        """Play in-memory WAV bytes, blocking until playback finishes."""
        if not data:
            return False
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            return self.play_file(path)
        finally:
            os.unlink(path)


class BargeInMonitor:
    """Continuously listens for real speech while TTS is playing and fires a
    callback the instant it's detected - "start talking to interrupt",
    the Pipecat/continuous-VAD pattern, rather than only interrupting when
    the user presses the orb or says the wake word again (that's
    `AudioPlayer.stop()`, used unconditionally by `app.py`'s
    `_begin_listening`; this is the additional, opt-in "just start talking"
    path).

    Deliberately opt-in and off by default (see the `barge-in-vad-enabled`
    gsetting): there is no acoustic echo cancellation here, so on
    loudspeakers - as opposed to headphones - the mic also picks up
    Chronoa's own TTS output bleeding back in, which a plain energy
    threshold cannot tell apart from the user actually talking. The
    threshold uses an extra margin over the calibrated ambient floor to
    reduce (not eliminate) false self-interruption; it will still misfire
    on loud enough speaker output on some hardware. Works best with
    headphones or a room/mic with weak speaker-to-mic coupling.
    """

    # Extra margin on top of the ambient-noise calibration, since playback
    # itself raises the effective noise floor further than plain silence.
    _PLAYBACK_MARGIN = 1.5

    def __init__(self) -> None:
        self._backend = self._detect_backend()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def _detect_backend(self) -> Optional[str]:
        if shutil.which("pw-record"):
            return "pw-record"
        if shutil.which("arecord"):
            return "arecord"
        return None

    def is_available(self) -> bool:
        """Check if a mic capture backend is installed."""
        return self._backend is not None

    def start(self, on_interrupt: Callable[[], None]) -> bool:
        """Start monitoring on a background thread; fires `on_interrupt()` at most once."""
        if not self.is_available() or self._thread is not None:
            return False
        try:
            proc = subprocess.Popen(
                _stream_capture_cmd(self._backend), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error(f"Failed to start barge-in monitor ({self._backend}): {e}")
            return False

        self._proc = proc
        self._stop.clear()
        self._thread = threading.Thread(target=self._monitor_loop, args=(proc, on_interrupt), daemon=True)
        self._thread.start()
        return True

    def _monitor_loop(self, proc: subprocess.Popen, on_interrupt: Callable[[], None]) -> None:
        stdout = proc.stdout
        try:
            calib_frames = []
            for _ in range(_CALIBRATION_FRAMES):
                if self._stop.is_set():
                    return
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if not chunk:
                    return
                calib_frames.append(chunk)
            threshold = calibrate_noise_floor(calib_frames) * self._PLAYBACK_MARGIN

            while not self._stop.is_set():
                chunk = stdout.read(_FRAME_BYTES) if stdout else b""
                if not chunk:
                    break
                if rms(chunk) >= threshold:
                    logger.info("Barge-in: speech detected during playback")
                    on_interrupt()
                    return
        except Exception as e:
            logger.error(f"Barge-in monitor loop crashed: {e}")

    def stop(self) -> None:
        """Stop monitoring. Safe to call whether or not it's running."""
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
