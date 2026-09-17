"""Hands-free wake-word ("hey chronoa") activation, built on openWakeWord.

openWakeWord (https://github.com/dscripka/openWakeWord) is a fully local,
offline, Apache-2.0-licensed wake-word engine (mel-spectrogram + ONNX
embedding + a small per-wakeword classifier) - no cloud calls and no API key,
unlike Porcupine. It is NOT a hard dependency: there's no Arch or Debian
package for it yet (see PKGBUILD/DEBIAN/control's optional-dependency
notes), so if it - or numpy - isn't installed, `is_available()` reports
False and the app just falls back to push-to-talk-only, the same
degrade-gracefully pattern used by stt.py/llm.py/tts.py.

No dedicated "hey chronoa" model has been trained. This ships with
openWakeWord's bundled pretrained "hey_jarvis" model as the default phrase
(see `ChronoaConfig.wake_word_model` / the `wake-word-model` gsetting) until
a custom one exists - training one is a separate effort using openWakeWord's
own training pipeline, not something improvised here.

Caveat: this has NOT been verified against a real microphone/openWakeWord
install in this dev environment (no audio hardware, package not installed
here). The listen loop's framing (1280 samples = 80ms @ 16kHz mono s16le,
openWakeWord's expected chunk size) and the pw-record/arecord invocations
follow the same conventions already proven-by-execution in audio.py, but the
loop itself - and the assumption that piped `pw-record` output starts with
exactly one 44-byte WAV header before raw PCM - needs confirming on real
hardware.
"""

import glob
import logging
import os
import shutil
import subprocess
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_FRAME_SAMPLES = 1280  # 80ms @ 16kHz mono s16le - openWakeWord's expected chunk size
_FRAME_BYTES = _FRAME_SAMPLES * 2

try:
    import numpy as np
    import openwakeword
    from openwakeword.model import Model as _OWWModel
except Exception as e:  # pragma: no cover - only exercised when the optional deps are missing
    np = None  # type: ignore
    openwakeword = None  # type: ignore
    _OWWModel = None  # type: ignore
    logger.debug(f"openWakeWord/numpy not available: {e}")


def _resolve_model_path(name: str) -> Optional[str]:
    """Find a bundled openWakeWord model file matching a short name.

    `Model()` takes file paths, not bare names (e.g. "hey_jarvis" ->
    ".../resources/models/hey_jarvis_v0.1.onnx") - confirmed by actually
    loading the installed package; passing the bare name raises a TypeError.
    """
    if openwakeword is None:
        return None
    models_dir = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
    matches = sorted(glob.glob(os.path.join(models_dir, f"{name}*.onnx")))
    return matches[0] if matches else None


class WakeWordListener:
    """Continuously listens on the mic for a wake phrase and fires a callback.

    Runs its own capture subprocess + inference loop on a background thread
    so it never touches GTK objects directly - callers must marshal
    `on_detected` back to the main thread themselves (e.g. via
    `GLib.idle_add`), same convention as `AsyncBridge`.
    """

    def __init__(self, wakeword_model: str = "hey_jarvis", threshold: float = 0.5) -> None:
        self._wakeword_model = wakeword_model
        self._threshold = threshold
        self._backend = self._detect_backend()
        self._model = None
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
        """True if openWakeWord/numpy and a mic capture backend are all present."""
        return _OWWModel is not None and self._backend is not None

    def _record_cmd(self) -> list:
        if self._backend == "pw-record":
            return ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
        return ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1", "-"]

    def start(self, on_detected: Callable[[], None]) -> bool:
        """Start the continuous listen loop on a background thread."""
        if not self.is_available() or self._thread is not None:
            return False

        model_path = _resolve_model_path(self._wakeword_model)
        if model_path is None:
            logger.error(f"No bundled openWakeWord model found matching '{self._wakeword_model}'")
            return False

        try:
            self._model = _OWWModel(wakeword_model_paths=[model_path])
        except Exception as e:
            logger.error(f"Failed to load wake-word model '{model_path}': {e}")
            return False

        try:
            proc = subprocess.Popen(
                self._record_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error(f"Failed to start wake-word listening ({self._backend}): {e}")
            self._model = None
            return False

        self._proc = proc
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._listen_loop, args=(proc, on_detected), daemon=True
        )
        self._thread.start()
        logger.info(f"Wake-word listening started (model={self._wakeword_model}, backend={self._backend})")
        return True

    def _listen_loop(self, proc: subprocess.Popen, on_detected: Callable[[], None]) -> None:
        stdout = proc.stdout
        if stdout is None:
            return

        # Both backends stream raw s16le PCM from byte zero when writing to
        # stdout ("-") - confirmed by capturing real audio from this
        # machine's mic and hex-dumping the first 200 bytes: no RIFF header
        # appears. (An earlier version of this code assumed pw-record wrote
        # a 44-byte WAV header first and skipped it, which would have
        # silently discarded the first 80ms of every wake-word check.)

        try:
            while not self._stop.is_set():
                chunk = stdout.read(_FRAME_BYTES)
                if not chunk or len(chunk) < _FRAME_BYTES:
                    break
                audio = np.frombuffer(chunk, dtype=np.int16)
                try:
                    scores = self._model.predict(audio)
                except Exception as e:
                    logger.error(f"Wake-word inference failed: {e}")
                    continue
                # Exactly one model is loaded (see start()), but its score-dict
                # key is the resolved filename stem (e.g. "hey_jarvis_v0.1"),
                # not the short config name - confirmed by actually loading
                # the bundled model, so match on the single score present
                # rather than re-deriving that key.
                if scores and max(scores.values()) >= self._threshold:
                    logger.info("Wake word detected")
                    on_detected()
        except Exception as e:
            logger.error(f"Wake-word listen loop crashed: {e}")

    def stop(self) -> None:
        """Stop listening and release the mic and model."""
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        self._model = None
