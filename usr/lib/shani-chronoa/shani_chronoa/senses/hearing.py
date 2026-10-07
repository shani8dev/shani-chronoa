"""Hearing sense: one spoken utterance, captured and transcribed into a percept.

`stt.py` already transcribes, and `app.py` already calls it - but the
transcript goes straight into `Assistant._history` as if the user had typed
it, which bypasses the percept store entirely. Nothing about that path has a
lifetime, a sensitivity, a provenance, or a consent check: an utterance
captured at 09:00 is indistinguishable, five turns later, from something the
user just said, and the existing voice path has no answer to "may this
machine listen at all?". This module is the answer. It makes a real
utterance a first-class transient percept, going through the same
`Percept` -> `PerceptStore` -> `ContextBuilder` path every other sense uses,
so it expires, it is ranked, it is budgeted, and it is auditable.

**This is a sense. The wake word and TTS are not.** `wakeword.py` is a
trigger (it fires an action; it perceives nothing worth recording - the user
did not say anything) and `tts.py` is an actuator (it produces sound, it does
not observe it). Converting either into a sense would put "the assistant
started talking" in the same channel as "the user said something", which is
precisely the confusion this layer exists to prevent.

**There is no amplitude threshold anywhere in this file, and adding one would
be a bug.** `vad.py`'s first draft used a hardcoded RMS silence threshold of
400; a real 2-second capture of *ambient noise* on a normal machine measured
a peak of ~2046 and a mean of ~592, so that threshold registered continuous
"speech" and never auto-stopped on any real room. Every speech/silence
decision is therefore delegated to `AudioRecorder.start_auto_stop`, which
calibrates `vad.calibrate_noise_floor()` against a real sample of the
current room at the start of every capture. The only numbers this module
introduces are *durations* (how long to listen, how much trailing silence
ends the turn) - the same two `app.py` already passes to
`start_auto_stop` - and a transcript length cap. None of them are a decision
about what counts as sound.

**Consent is checked before the microphone is touched, not after.**
`ChronoaConfig.sense_allowed("hearing")` is the very first thing `run()` does,
before `AudioRecorder` is even constructed. This is the same ordering
`senses/scheduler.py` documents as load-bearing: a post-hoc filter is
worthless, because by the time it could discard the result the audio has
already been on disk in a temp WAV. A denied sense is never invoked at all.

As shipped, `hearing-sense-enabled` does **not** exist in the gschema and is
not in `config._SENSE_CONSENT_KEYS`, so `sense_allowed("hearing")` returns
False with the reason "there is no 'hearing' sense" and this sense is
fail-closed: it refuses, and it refuses *before* opening the microphone.
That is the correct behaviour for a voice-input path whose consent key has
not landed, and it is why the acceptance tests in
`tests/test_hearing_sense.py` override the consent verdict rather than
depending on a key existing. This module deliberately does not create the
key itself - `config.py` and the gschema are owned elsewhere, and a sense
granting itself consent is the failure mode the whole layer is built to
prevent.

**Sensitivity is PRIVATE, and it is the most private thing this project
perceives.** Not the microphone, the words: a transcript is the user's own
speech, which can contain anything they can say, including a credential, a
medical detail or someone else's name. `ContextBuilder` ranks by inclusion
least-private-first, so a `private` percept is considered last and shed first
when the character budget is tight - correct here, and the reason the
transcript is not `public` merely because the capture is local.

**TTL is 5 minutes, not `None`.** `PerceptStore.add()` routes on exactly one
field: `ttl_seconds is None` means the on-disk tier, and only
`shani_chronoa.senses.memory` may use it (a repo-level data-retention
boundary, asserted by `tests/test_sense_manifest.py`). A spoken utterance is
perception of the present, not something to be re-read next week. Five
minutes covers the realistic use - "what did I just say?" and follow-up
questions about the same turn - and after that the transcript is gone from
memory, never written to disk.

**Poll interval is `None`, and never anything else.** Reactive only. A
microphone polled on a timer is an always-on microphone, which no
"enabled the hearing sense" toggle can honestly be read as consenting to.
"""

import logging
import os
import threading
import time
from typing import Optional

from shani_chronoa.audio import AudioRecorder
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.hardware_profile import HardwareProfile
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Percept, Sense
from shani_chronoa import stt, stt_provision
from shani_chronoa.stt import STT
from shani_chronoa.stt_parakeet import ParakeetSTT

logger = logging.getLogger(__name__)

# Percept lifetime. See the module docstring: bounded, never durable.
TTL_SECONDS = 300.0

# Capture bounds, matching what `app.py:_start_listening` already uses for
# the orb button. These are *durations*; the speech/silence decision inside
# the capture is calibrated per room against a real ambient sample, never
# against a constant written here.
DEFAULT_MAX_SECONDS = 20.0
DEFAULT_SILENCE_SECONDS = 1.2

# The recorder enforces its own `max_seconds` deadline and always calls back,
# so this slack only covers a capture thread that died without doing so.
_CALLBACK_SLACK_SECONDS = 5.0

# Upper bound on a single capture. A sense whose `run()` is reachable from the
# headless CLI and (later) an MCP tool must not be able to hold a microphone
# open indefinitely, and no legitimate utterance needs a minute.
MAX_CAPTURE_SECONDS = 60.0

# `context.py` documents that a percept can eat a large share of the per-turn
# character budget. A real utterance is a sentence or two; this cap exists for
# the other case - whisper.cpp inventing paragraphs when handed a noise
# burst - which would otherwise read as a confident percept.
MAX_TRANSCRIPT_CHARS = 1000

# Provenance for a percept whose source is a device rather than a file. The
# captured WAV is a temp file whose name means nothing after the fact.
SOURCE = "microphone"

# One capture per process. `AudioRecorder` refuses a *second* capture on the
# *same* instance, but nothing stops two instances each spawning their own
# `pw-record`. Taking a lock rather than queuing means the second caller is
# told immediately instead of blocking for the length of the first utterance.
_CAPTURE_LOCK = threading.Lock()


class HearingError(Exception):
    """Hearing could not happen, or produced nothing. Carries a user-facing reason.

    Every message here is written to be *shown*, not to be logged and read by
    a developer: a missing whisper model and a microphone already in use are
    two problems with two different fixes, and a traceback answers neither.
    """


def _refusal(reason: str) -> str:
    return f"Hearing unavailable: {reason}."


def _require(condition: object, message: str) -> None:
    if not condition:
        raise HearingError(message)


def _bounded_number(
    arguments: dict, key: str, default: float, low: float, high: float
) -> float:
    """Read `key` from `arguments` as a number inside `[low, high]`.

    Rejects bools explicitly - `isinstance(True, int)` is true in Python, and
    a sense that accepted `max_seconds=True` as 1.0 seconds would be a
    surprising way to lose a capture.
    """
    value = arguments.get(key, default)
    if value is None:
        return default
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{key} must be a number",
    )
    value = float(value)
    _require(low < value <= high, f"{key} must be between {low:g} and {high:g}")
    return value


def parse_request(arguments: dict) -> "tuple[float, float]":
    """Read and validate the sense's arguments. Raises `HearingError` on bad input."""
    max_seconds = _bounded_number(
        arguments, "max_seconds", DEFAULT_MAX_SECONDS, 0.0, MAX_CAPTURE_SECONDS
    )
    silence_seconds = _bounded_number(
        arguments, "silence_seconds", DEFAULT_SILENCE_SECONDS, 0.0, max_seconds
    )
    return max_seconds, silence_seconds


def resolve_whisper_model(config: ChronoaConfig) -> str:
    """The whisper.cpp model this machine should use.

    Same precedence as `app.py:_init_components` - the persisted
    `whisper-model` override if set, else the hardware tier's choice - so the
    hearing sense and the orb button transcribe with the *same* model instead
    of two senses disagreeing about what "the installed model" means.
    """
    if config.whisper_model:
        return config.whisper_model
    try:
        return HardwareProfile().get_whisper_model()
    except Exception as e:  # noqa: BLE001 - detection is best-effort, like app.py's
        logger.warning("Could not detect a hardware profile for the whisper model: %s", e)
        return "base"


def build_stt(config: Optional[ChronoaConfig] = None) -> STT:
    """Construct the STT this sense transcribes with.

    A real STT object either way; `is_available()` is the honest question about
    whether it can actually run, and answering it with a fabricated object
    would just move the failure somewhere less obvious.

    Goes through `stt.build_stt` rather than naming a backend, so the hearing
    sense transcribes with the engine the user actually selected. Constructing
    `WhisperSTT` here directly would have meant the assistant's speech input
    and this sense disagreed about what "the" backend is on any machine with
    Parakeet selected - the two halves of one feature.
    """
    cfg = config or ChronoaConfig()
    backend = getattr(cfg, "stt_backend", "whisper")
    if backend == "parakeet":
        model = stt_provision.PARAKEET_DEFAULT_MODEL
    else:
        model = resolve_whisper_model(cfg)
    return stt.build_stt(model=model, language=cfg.language, backend=backend)


def stt_problem(engine_stt: STT) -> str:
    """Why this STT cannot transcribe, or '' if it can.

    `is_available()` is a single boolean over two independent causes - no
    engine binary, or no downloaded model - and the two have completely
    different fixes. Collapsing them into one "unavailable" is how a user ends
    up reinstalling whisper-cpp on a machine that already has it and never
    downloads the model. Checks the same two paths the boolean does.

    The parameter is not named `stt` because that is the module imported at the
    top of this file, and shadowing it inside a function that reasons about
    engines is the kind of quiet trap this repo keeps paying for.

    **Duck-typed from 2026-10-06, and the previous version raised.**
    `engine_stt.whisper_path` and `.model_path` were read directly, so an engine
    without those attributes - which is every engine that is not one of the two
    local ones - raised `AttributeError`. `cloud_voice.CloudSTT` has neither, so
    on a machine with cloud recognition switched on and no local model this
    function blew up and the caller reported
    `unknown - AttributeError: 'CloudSTT' object has no attribute 'whisper_path'`
    - honest, and useless: it would send somebody to install whisper-cpp on a
    machine that was transcribing perfectly well in the cloud.

    So an engine with neither attribute is asked **itself** first. That is what
    `CloudSTT.refusal()` is for, and preferring it over a guess is the whole
    fix: an engine that can explain its own absence beats any explanation
    assembled from attributes it happens not to have.
    """
    if not hasattr(engine_stt, "whisper_path") and not hasattr(engine_stt, "model_path"):
        refusal = getattr(engine_stt, "refusal", None)
        if callable(refusal):
            try:
                return str(refusal() or "")
            except Exception:  # noqa: BLE001 - fall through to the generic answer
                logger.debug("the engine's own refusal() raised", exc_info=True)
        return ("this speech engine has no local program or model to check, so "
                "whether it can listen is not something this page can tell you")
    engine = "parakeet-cli" if isinstance(engine_stt, ParakeetSTT) else "whisper.cpp"
    if not engine_stt.whisper_path or not os.path.exists(engine_stt.whisper_path):
        return (
            f"{engine} is not installed (Arch: 'whisper-cpp'; "
            "Debian: 'whisper-cpp')"
        )
    if not engine_stt.model_path or not os.path.exists(engine_stt.model_path):
        return (
            f"the {engine} model {os.path.basename(engine_stt.model_path or '')} "
            f"is not downloaded (expected at {engine_stt.model_path})"
        )
    return ""


def is_available(config: Optional[ChronoaConfig] = None) -> bool:
    """True when both a capture backend and a usable STT are present."""
    if not AudioRecorder().is_available():
        return False
    return stt_problem(build_stt(config)) == ""


def capture_utterance(recorder: AudioRecorder, max_seconds: float, silence_seconds: float) -> Optional[str]:
    """Record one utterance and return the WAV path, or None if none was heard.

    `AudioRecorder.start_auto_stop` invokes its callback from the capture
    thread, and `Sense.run` is called on the caller's thread (the GTK main
    thread, a CLI process, a scheduler thread). This is the bridge between
    the two: an `Event` for the arrival and a `list` for the payload, because
    a bare `Event` cannot carry a value. The wait is bounded - the recorder
    enforces its own deadline, so an unbounded `wait()` here would be a hang
    in anything but theory.
    """
    arrived = threading.Event()
    captured: "list[Optional[str]]" = []

    def _on_done(path: Optional[str]) -> None:
        captured.append(path)
        arrived.set()

    if not recorder.start_auto_stop(
        _on_done, max_seconds=max_seconds, silence_seconds=silence_seconds
    ):
        raise HearingError(
            "the microphone could not be opened (a capture may already be running)"
        )

    if not arrived.wait(max_seconds + _CALLBACK_SLACK_SECONDS):
        # Best effort: the recorder owns the subprocess, so this is the only
        # handle available to stop it. Its own loop would end anyway.
        recorder.cancel_auto_stop()
        raise HearingError(
            f"the capture did not finish within {max_seconds + _CALLBACK_SLACK_SECONDS:.0f}s "
            "and was stopped"
        )
    return captured[0]


def transcribe(engine_stt: STT, audio_path: str) -> str:
    """Transcribe one recording, raising `HearingError` with a readable reason.

    `STT.transcribe` already swallows its own subprocess failures and returns
    "", and raises `FileNotFoundError` for a missing file or model. Both become
    reasons here, so the caller never has to distinguish "the model vanished
    between the check and the call" from "the engine produced no text", and
    neither reaches the user as a traceback.
    """
    engine = "parakeet-cli" if isinstance(engine_stt, ParakeetSTT) else "whisper.cpp"
    try:
        text = engine_stt.transcribe(audio_path)
    except FileNotFoundError as e:
        raise HearingError(str(e)) from e
    except Exception as e:  # noqa: BLE001 - one bad recording must not raise out of a sense
        raise HearingError(f"{engine} could not transcribe the recording: {e}") from e
    text = (text or "").strip()
    if not text:
        raise HearingError(f"{engine} transcribed the recording to nothing")
    return text


def _transcript_percept(
    text: str,
    engine_stt: STT,
    max_seconds: float,
    silence_seconds: float,
) -> Percept:
    """Wrap one utterance in this sense's declared shape.

    `source` is the device, not the temp WAV path: the path is meaningless
    provenance (it is removed in the same call) and would put a filesystem
    path in front of the user in the percept context. The transcript is
    capped for the reason in `MAX_TRANSCRIPT_CHARS`.
    """
    dropped = 0
    if len(text) > MAX_TRANSCRIPT_CHARS:
        dropped = len(text) - MAX_TRANSCRIPT_CHARS
        text = text[:MAX_TRANSCRIPT_CHARS] + " [...] (truncated)"
    return Percept(
        sense="hearing",
        kind="utterance",
        content=f'You said: "{text}"',
        created_at=time.time(),
        ttl_seconds=TTL_SECONDS,
        source=SOURCE,
        sensitivity=SENSITIVITY_PRIVATE,
        metadata={
            "model": engine_stt.model,
            "language": engine_stt.language,
            "max_seconds": max_seconds,
            "silence_seconds": silence_seconds,
            "chars_dropped": dropped,
        },
    )


def run(arguments: dict) -> "str | Percept":
    """Listen for one utterance and return it as a Percept, or a refusal string.

    Order matters and is the point of this function: consent, then argument
    validation, then the capture backend, then the STT, and only then is a
    microphone opened. Each unavailable capability produces a sentence naming
    what to install, in the same shape `senses/ocr.py` uses for a missing
    tesseract - a clear reason, never a traceback.
    """
    config = ChronoaConfig()
    if not config.hearing_sense_enabled:
        return _refusal(config.sense_allowed_reason("hearing"))

    try:
        max_seconds, silence_seconds = parse_request(arguments)
    except HearingError as e:
        return _refusal(str(e))

    if not _CAPTURE_LOCK.acquire(blocking=False):
        return _refusal("another capture is already in progress")
    try:
        recorder = AudioRecorder()
        if not recorder.is_available():
            return _refusal(
                "no microphone capture backend is installed "
                "(Arch/Debian: 'pipewire-audio' for pw-record, or 'alsa-utils' for arecord)"
            )

        engine_stt = build_stt(config)
        problem = stt_problem(engine_stt)
        if problem:
            return _refusal(problem)

        try:
            audio_path = capture_utterance(recorder, max_seconds, silence_seconds)
        except HearingError as e:
            return _refusal(str(e))

        if audio_path is None:
            # Not a Percept: silence is not an observation. Filing "I heard
            # nothing" as a percept would put a non-perception in front of the
            # model with the same authority as a real utterance.
            return _refusal("no speech was detected during the capture")

        try:
            text = transcribe(engine_stt, audio_path)
        except HearingError as e:
            return _refusal(str(e))
        finally:
            # The recording is the user's voice on disk. `app.py` deletes it
            # for the same reason; the TTL keeps the *transcript* short-lived
            # and this keeps the *audio* from outliving the call at all.
            try:
                os.remove(audio_path)
            except OSError as e:
                logger.warning("Could not remove the captured recording %s: %s", audio_path, e)

        return _transcript_percept(text, engine_stt, max_seconds, silence_seconds)
    finally:
        _CAPTURE_LOCK.release()


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "hearing",
        "description": (
            "Listen for one utterance on the default microphone and transcribe it. "
            "Recording auto-stops after the speaker falls silent. Needs the hearing "
            "sense enabled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "max_seconds": {
                    "type": "number",
                    "description": (
                        "Longest single capture, in seconds (default 20, maximum 60). "
                        "The capture also ends earlier once speech stops."
                    ),
                },
                "silence_seconds": {
                    "type": "number",
                    "description": (
                        "Trailing silence, in seconds, that ends the turn (default 1.2). "
                        "Measured against this room's own ambient noise, not a fixed level."
                    ),
                },
            },
            "required": [],
        },
    },
}

_HEARING_SENSE = Sense(
    # poll_interval=None, and not a parameter: a microphone on a timer is an
    # always-on microphone. See the module docstring.
    name="hearing",
    kind="utterance",
    ttl_seconds=TTL_SECONDS,
    sensitivity=SENSITIVITY_PRIVATE,
    schema=_SCHEMA,
    run=run,
)

SENSES = [_HEARING_SENSE]
