"""Hands-free wake-phrase ("hey chronoa") activation, built on whisper.cpp.

No wake-word engine of its own. openWakeWord, the previous one, could not run
on Shanios at all: neither it nor either of its inference runtimes
(onnxruntime, tflite-runtime) is in Arch's official repositories, so the
feature silently fell back to push-to-talk on every install. Instead this
listens the way a person does - wait for someone to speak, then hear what
they said - with the two pieces Chronoa already has:

* `vad.py`'s calibrated energy detector cuts the microphone stream into
  utterances, so nothing is transcribed while the room is quiet;
* `whisper-cli` (whisper.cpp, the same binary and model files `stt.py` uses)
  transcribes each short utterance, biased toward the phrase with
  `--prompt`, and the phrase is matched at the START of the text.

Any phrase works without training a model ("hey chronoa" is the default; the
`wake-phrase` gsetting changes it). Measured with whisper.cpp 1.9.4 and
ggml-tiny-q5_1 on synthetic speech (espeak-ng, two voices): every "hey
chronoa" / "okay chronoa" was transcribed as such with the prompt, and
almost never without it ("Take it all in."), which is why the prompt is not
optional; "hey corona" also became "Hey Chronoa." - the one near-miss found.
Each check took ~0.7 s on four threads and the tiny model is 32 MB.
Real voices, rooms and microphones are the open question, and they are what
shani-testbed's voice actions exist to exercise.

The utterance is transcribed and discarded: nothing is stored, logged or
sent anywhere, and only whether it began with the phrase is kept.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata
import wave
from typing import Callable, List, Optional

# The argv builder, not just its latency constant: this listener and the turn
# recorder read the same microphone, and a second copy of the argv drifted from
# this one silently (its rate was a literal). Asserted by
# tests/test_capture_argv_agreement.py.
from shani_chronoa.audio import _stream_capture_cmd
from shani_chronoa.vad import calibrate_noise_floor, rms

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_FRAME_SAMPLES = 1280  # 80ms @ 16kHz mono s16le - the framing audio.py uses
_FRAME_BYTES = _FRAME_SAMPLES * 2
_CALIBRATION_FRAMES = 4  # ~320ms of ambient audio, as the recorder samples
_PRE_ROLL_FRAMES = 2  # 160ms before the first loud frame: the "h" of "hey" is quiet
_END_SILENCE_FRAMES = 5  # 400ms of quiet ends an utterance
_MIN_SPEECH_FRAMES = 3  # under 240ms is a click or a cough, not a phrase
_MAX_UTTERANCE_FRAMES = 38  # ~3s: a wake phrase is short; longer is conversation
_TRANSCRIBE_TIMEOUT = 20

DEFAULT_PHRASE = "hey chronoa"

#: Greetings that stand in for each other: whisper writes "Okay, Chronoa."
#: for "ok chronoa", and a person says "hi" as often as "hey".
_GREETINGS = {"hey", "hi", "hello", "ok", "okay"}

#: Models tried in order. The tiny one is fastest and was enough in testing;
#: whatever model speech input already uses comes after it, so a machine that
#: has one model works without a second download.
_PREFERRED_MODELS = ("tiny-q5_1", "tiny.en", "tiny", "base-q5_1", "base.en", "base")


def _words(text: str) -> List[str]:
    """Lower-case words without accents or punctuation."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9']+", text.lower())


def matches_phrase(transcript: str, phrase: str = DEFAULT_PHRASE) -> bool:
    """True when `transcript` BEGINS with the wake phrase.

    At the start only: "the Chronoa lot" (whisper's take on "the crow knows a
    lot", biased by the prompt) contains the name and must not wake anything.
    A leading greeting may be any of `_GREETINGS`, since they are said and
    transcribed interchangeably; every other word must match exactly.
    """
    want, got = _words(phrase), _words(transcript)
    if not want or len(got) < len(want):
        return False
    for w, g in zip(want, got):
        if w == g or (w in _GREETINGS and g in _GREETINGS):
            continue
        return False
    return True


def _find_model(preferred: Optional[str] = None) -> Optional[str]:
    """Path of a whisper.cpp model on this machine, or None."""
    from shani_chronoa.stt import WhisperSTT

    for name in ((preferred,) if preferred else ()) + _PREFERRED_MODELS:
        path = WhisperSTT._get_model_path(WhisperSTT.__new__(WhisperSTT), name)
        if os.path.exists(path):
            return path
    return None


class WakeWordListener:
    """Continuously listens on the mic for a wake phrase and fires a callback.

    Runs its own capture subprocess + detection loop on a background thread
    so it never touches GTK objects directly - callers must marshal
    `on_detected` back to the main thread themselves (e.g. via
    `GLib.idle_add`), same convention as `AsyncBridge`.
    """

    def __init__(
        self,
        phrase: str = DEFAULT_PHRASE,
        target: Optional[str] = None,
        model: Optional[str] = None,
        whisper_path: Optional[str] = None,
    ) -> None:
        self._phrase = (phrase or DEFAULT_PHRASE).strip()
        self._preferred_model = model
        self._whisper = whisper_path or shutil.which("whisper-cli")
        self._backend = self._detect_backend()
        # Same device the recorder uses. A wake word that triggers on one
        # microphone while the turn records from another is a bug that would
        # look like intermittent recognition failure.
        self._target = target or None
        self._model_path: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._generation = 0
        self._stop_epoch = 0

    def _detect_backend(self) -> Optional[str]:
        if shutil.which("pw-record"):
            return "pw-record"
        if shutil.which("arecord"):
            return "arecord"
        return None

    @property
    def phrase(self) -> str:
        return self._phrase

    def set_phrase(self, phrase: str) -> None:
        self._phrase = (phrase or DEFAULT_PHRASE).strip()

    def unavailable_reason(self) -> Optional[str]:
        """Why listening cannot start, in words a person can act on; None if it can."""
        if self._backend is None:
            return "no microphone capture tool (pw-record or arecord)"
        if not self._whisper:
            return "whisper.cpp is not installed (the whisper-cpp package provides whisper-cli)"
        if _find_model(self._preferred_model) is None:
            return "no whisper.cpp model is installed (Settings > Voice can download one)"
        return None

    def is_available(self) -> bool:
        """True if a capture tool, whisper-cli and a whisper.cpp model are all present."""
        return self.unavailable_reason() is None

    def set_target(self, target: Optional[str]) -> None:
        self._target = target or None

    def _record_cmd(self) -> list:
        return _stream_capture_cmd(self._backend, self._target)

    def start(self, on_detected: Callable[[], None]) -> bool:
        """Start the continuous listen loop on a background thread.

        Bumps the generation, so a `on_detected` that belongs to an earlier
        session cannot reach the caller. `stop()`'s join is bounded at two
        seconds and it clears `self._thread` regardless, so a listener still
        blocked in `read()` when the join expires leaves the object looking
        idle; the next `start()` is then accepted and two listen loops run at
        once. Only the current generation may report a detection, so the older
        loop cannot open a microphone for a turn the user has moved on from.
        """
        if self._thread is not None:
            return False
        why = self.unavailable_reason()
        if why:
            logger.info(f"Wake phrase unavailable: {why}")
            return False
        self._model_path = _find_model(self._preferred_model)

        try:
            proc = subprocess.Popen(
                self._record_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error(f"Failed to start wake-phrase listening ({self._backend}): {e}")
            return False

        self._proc = proc
        self._stop.clear()
        self._generation += 1
        self._thread = threading.Thread(
            target=self._listen_loop, args=(proc, on_detected, self._generation), daemon=True
        )
        self._thread.start()
        logger.info(
            f"Wake-phrase listening started (phrase={self._phrase!r}, "
            f"model={os.path.basename(self._model_path or '?')}, backend={self._backend})"
        )
        return True

    def _transcribe(self, pcm: bytes) -> str:
        """whisper-cli on one utterance, biased toward the phrase. "" on any failure."""
        if not (self._whisper and self._model_path):
            return ""
        fd, path = tempfile.mkstemp(prefix="chronoa-wake-", suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(_SAMPLE_RATE)
                w.writeframes(pcm)
            # The prompt is what makes the name recognisable at all: without
            # it whisper heard "hey chronoa" as "Take it all in." and "Okay,
            # criminal." (measured). It is a hint, not a filter - matching is
            # done on the text, below.
            prompt = self._phrase[:1].upper() + self._phrase[1:] + "."
            result = subprocess.run(
                [self._whisper, "-m", self._model_path, "-f", path, "-l", "en",
                 "-nt", "-np", "-t", str(min(4, os.cpu_count() or 1)), "--prompt", prompt],
                capture_output=True, text=True, timeout=_TRANSCRIBE_TIMEOUT,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception as e:
            logger.error(f"Wake-phrase transcription failed: {e}")
            return ""
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _listen_loop(
        self, proc: subprocess.Popen, on_detected: Callable[[], None], generation: int = 0
    ) -> None:
        stdout = proc.stdout
        if stdout is None:
            return

        # Both backends stream raw s16le PCM from byte zero when writing to
        # stdout ("-") - confirmed by capturing real audio from this
        # machine's mic and hex-dumping the first 200 bytes: no RIFF header
        # appears.
        calib: List[bytes] = []
        threshold: Optional[float] = None
        recent: List[bytes] = []  # pre-roll ring
        utterance: List[bytes] = []
        loud = quiet = 0
        boundary = self._stop_epoch

        def judge(frames: List[bytes]) -> bool:
            """Transcribe one utterance; True when the caller should return."""
            text = self._transcribe(b"".join(frames))
            if self._generation != generation:
                logger.info("Wake phrase: dropping a detection from a superseded session")
                return True
            hit = bool(text) and matches_phrase(text, self._phrase)
            # the length and the verdict, never the words: what was said is
            # discarded, and a log is the last place it may end up
            logger.debug(f"Wake phrase: judged {len(frames) * _FRAME_SAMPLES / _SAMPLE_RATE:.1f}s "
                         f"of speech: {'match' if hit else 'no match'}"
                         f"{'' if text else ' (whisper returned nothing)'}")
            if hit:
                logger.info("Wake phrase detected")
                on_detected()
            return False

        try:
            while not self._stop.is_set():
                # A frame already in flight when `stop()` arrived predates the
                # stop, so it is dropped rather than judged: a stopped listener
                # must not be able to open a microphone.
                boundary = self._stop_epoch
                chunk = stdout.read(_FRAME_BYTES)
                if self._stop_epoch != boundary:
                    return
                if not chunk or len(chunk) < _FRAME_BYTES:
                    break
                if threshold is None:
                    calib.append(chunk)
                    if len(calib) >= _CALIBRATION_FRAMES:
                        threshold = calibrate_noise_floor(calib)
                    continue
                is_loud = rms(chunk) >= threshold
                if not utterance:
                    if is_loud:
                        utterance = recent[-_PRE_ROLL_FRAMES:] + [chunk]
                        loud, quiet = 1, 0
                    else:
                        recent = (recent + [chunk])[-_PRE_ROLL_FRAMES:]
                    continue
                utterance.append(chunk)
                if is_loud:
                    loud, quiet = loud + 1, 0
                else:
                    quiet += 1
                ended = quiet >= _END_SILENCE_FRAMES or len(utterance) >= _MAX_UTTERANCE_FRAMES
                if ended:
                    frames, utterance, recent = utterance, [], []
                    if loud >= _MIN_SPEECH_FRAMES and judge(frames):
                        return
            # end of stream mid-utterance (capture stopped): judge what was said
            if utterance and loud >= _MIN_SPEECH_FRAMES and self._stop_epoch == boundary \
                    and not self._stop.is_set():
                judge(utterance)
        except Exception as e:
            logger.error(f"Wake-phrase listen loop crashed: {e}")

    def stop(self) -> None:
        """Stop listening and release the mic."""
        self._stop.set()
        self._stop_epoch += 1
        self._generation += 1
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
