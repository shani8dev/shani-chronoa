"""Lightweight, dependency-free speech/silence detection over raw PCM audio.

Used for two real gaps found by comparing Chronoa against other Linux voice
assistants: (1) auto-stopping a listening session once the user stops
talking, instead of requiring a second button press (borrowed from
`aside`'s VAD-based auto-stop and `loquivox`'s "Smart Turn" end-of-speech
detector), and (2) detecting the user has started talking again during TTS
playback, for barge-in that doesn't need an explicit re-trigger (the
Pipecat/`voice-agent`-style continuous-VAD pattern).

Deliberately NOT a real ML VAD model (Silero/webrtcvad) - a simple
RMS-energy-over-threshold check in pure Python (the `array` module, no
numpy), since openWakeWord/numpy are already an optional dependency and
this shouldn't require them just to work. This is a real trade-off: no
energy-based detector distinguishes speech from a loud non-speech noise
(a dog bark, a door slam), and - critically for barge-in specifically -
there is no acoustic echo cancellation here, so the mic will also pick up
Chronoa's own TTS output bleeding from the speakers. See `audio.py`'s
`BargeInMonitor` docstring for how that's handled (a deliberately high
threshold and opt-in-by-default-off, not a full AEC implementation).
"""

import array
import math

_SAMPLE_WIDTH = 2  # bytes per sample, s16le


def rms(chunk: bytes) -> float:
    """Root-mean-square energy of a chunk of signed 16-bit little-endian PCM."""
    if not chunk:
        return 0.0
    usable = len(chunk) - (len(chunk) % _SAMPLE_WIDTH)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[:usable])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def calibrate_noise_floor(frames: list) -> float:
    """Derive a speech-detection threshold from a short sample of real ambient audio.

    A fixed absolute RMS threshold does not work across machines/rooms -
    confirmed live on this dev machine, where ambient noise (fan/room
    noise, no one speaking) peaked at ~2046 RMS with a ~592 mean over 2 real
    seconds of silence captured from the actual mic - an order of magnitude
    above what a "quiet room" default would assume. Sample a short window
    of real ambient audio at the start of each session instead and
    threshold relative to its peak, not a literal.
    """
    if not frames:
        return 1500.0  # conservative fallback if calibration capture failed
    peak = max(rms(f) for f in frames)
    return max(peak * 2.0, peak + 500.0, 300.0)


class SilenceDetector:
    """Tracks whether recent audio looks like trailing silence after speech.

    Feed it consecutive fixed-size frames; `is_done()` becomes true once
    real speech was heard and then enough consecutive silent frames follow.
    `threshold` should come from `calibrate_noise_floor()` against a real
    sample of the current room/mic, not a hardcoded literal.
    """

    def __init__(self, threshold: float, silence_seconds: float = 1.2, frame_seconds: float = 0.08) -> None:
        self.threshold = threshold
        self._silence_frames_needed = max(1, round(silence_seconds / frame_seconds))
        self._consecutive_silence = 0
        self._heard_speech = False

    def feed(self, chunk: bytes) -> None:
        if rms(chunk) >= self.threshold:
            self._consecutive_silence = 0
            self._heard_speech = True
        else:
            self._consecutive_silence += 1

    @property
    def heard_speech(self) -> bool:
        return self._heard_speech

    def is_done(self) -> bool:
        """True once speech was heard and then enough trailing silence followed."""
        return self._heard_speech and self._consecutive_silence >= self._silence_frames_needed
