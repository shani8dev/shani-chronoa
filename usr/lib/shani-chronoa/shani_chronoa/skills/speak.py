"""Skill: speak text aloud through the text-to-speech engine.

This is the actuation half of Chronoa's voice output. It deliberately does
**not** shell out to a TTS binary directly: it reuses `tts.py`'s `PiperTTS`,
which already owns the engine fallback chain (Piper -> RHVoice -> espeak-ng)
and the WAV synthesis, and it plays the resulting bytes through `audio.py`'s
`AudioPlayer`, which already owns the PipeWire/ALSA playback path and the
barge-in `stop()` hook. Writing a second, parallel playback path here would
have duplicated both and drifted from the one that is actually wired into the
app.

The engine that actually ran is reported in the result string, because on a
real machine the chain is environment-dependent (this host has espeak-ng but
no Piper), and "it spoke" is not the same claim as "Piper spoke".

Long text travels by reference. `tools.py:execute_tool()` asks this module
`wants_by_reference()` before dispatch, and when it returns True the text is
written to a private 0600 payload file and the skill receives a `FileRef`
instead of the string - the same transport `argfile.py` uses for binary
arguments, so a paragraph of speech is never subject to Linux's 128 KiB
per-argv cap. A short text stays inline, exactly as before.
"""

import logging

from shani_chronoa.audio import AudioPlayer
from shani_chronoa.skills import Skill
from shani_chronoa.tts import PiperTTS

logger = logging.getLogger(__name__)

_SCHEME = "tts"

# Above this many characters the inline JSON transport starts bumping into
# Linux's MAX_ARG_STRLEN (128 KiB per argv entry). Speech is the one skill
# whose argument is routinely a full sentence, so it opts into the by-reference
# transport well before that ceiling rather than waiting to hit it.
_BY_REFERENCE_TEXT_BYTES = 4 * 1024


def wants_by_reference(arguments: dict) -> bool:
    """True when this call's `text` is big enough to need the file transport.

    Checked by `tools.py` before dispatch; a short utterance stays inline so
    the common case is unchanged. Only `text` is considered - the schema has
    no other argument that could be oversized.
    """
    text = arguments.get("text")
    if not isinstance(text, str):
        return False
    return len(text.encode("utf-8", errors="replace")) > _BY_REFERENCE_TEXT_BYTES


def _read_text(arguments: dict) -> str:
    """The text argument, whether it arrived inline or as a `FileRef`."""
    from shani_chronoa.argfile import FileRef

    text = arguments.get("text")
    if isinstance(text, FileRef):
        return text.read_text()
    if not isinstance(text, str):
        return ""
    return text


def _run(arguments: dict) -> str:
    text = _read_text(arguments)
    if not text:
        return "Nothing to speak: the text argument was empty."

    tts = PiperTTS()
    engine = tts.engine()
    if engine is None:
        return (
            "Could not speak: no text-to-speech engine is available on this "
            "host. Install piper-tts (+ a voice model), rhvoice (+ a voice), "
            "or espeak-ng to enable the speak skill."
        )

    try:
        audio = tts.synthesize_to_bytes(text)
    except Exception as e:  # noqa: BLE001 - surface any failure as a message
        return f"Could not synthesize speech ({engine}): {e}"

    if not audio:
        return (
            f"Could not speak: the {engine} engine produced no audio. "
            "Check that a voice model is installed for it."
        )

    player = AudioPlayer()
    if not player.is_available():
        return (
            f"Speech was synthesized ({engine}, {len(audio)} bytes) but no "
            "audio playback backend is available (need pw-play or aplay)."
        )

    try:
        player.play_bytes(audio)
    except Exception as e:  # noqa: BLE001 - never a traceback to the caller
        return f"Could not play the synthesized speech: {e}"

    return f"Spoke using {engine}."


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "speak",
        "description": (
            "Speak text aloud using the local text-to-speech engine. "
            "Reuses the app's own Piper/RHVoice/espeak-ng fallback chain and "
            "its own audio playback path. The engine actually used is named "
            "in the result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to speak aloud.",
                },
            },
            "required": ["text"],
        },
    },
}

def _verify_spoke(arguments: dict, tool=None):
    """Post-condition: does the text actually become playable audio?

    **`speak` plays rather than writes.** It synthesises to bytes, hands them to
    PipeWire, and the file it used is deleted before this runs - so unlike
    `sing`, which can look for a WAV it left behind, there is no artifact to
    inspect. The evidence available is therefore a *re-synthesis*: if the engine
    produces real audio for this text now, it produced real audio a moment ago,
    and the failure mode worth catching is the one that is reproducible -
    no voice model installed, espeak-ng missing, a stub that returns silence.

    What this deliberately does **not** claim: that sound reached a speaker.
    Whether a sink is muted, the volume is zero, or the machine is in a
    container with no audio device is not observable from here, and `play_file`
    reporting success only means the backend accepted the stream. So the
    evidence says "audio was produced and offered to the backend", and the
    check is honest about that rather than claiming a person heard something.

    This is the same shape as `sing._verify_sung` - trust the artifact, not the
    skill's own claim - adapted to the fact that `speak` leaves no artifact.
    """
    text = str(arguments.get("text") or "").strip()
    if not text:
        return None  # nothing was asked for, so nothing to check
    try:
        from shani_chronoa.tts import PiperTTS

        tts = PiperTTS()
        engine = tts.engine()
        audio = tts.synthesize_to_bytes(text)
    except Exception as exc:  # noqa: BLE001 - cannot check is not a pass
        return (False, f"could not re-synthesise the text to check it: "
                       f"{type(exc).__name__}: {str(exc)[:100]}")
    if not audio:
        return (False, f"the {engine} engine produced no audio for this text - "
                       f"check that a voice model is installed")
    # A WAV header with nothing behind it is exactly what a stub returns.
    header = bytes(audio[:12])
    if len(audio) <= 44 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return (False, f"the {engine} engine produced {len(audio)} bytes that "
                       f"are not a WAV, so nothing could have been played")
    samples = len(audio) - 44
    if samples <= 0:
        return (False, f"the {engine} engine produced a WAV header with no "
                       f"audio behind it")
    return (True, f"the {engine} engine re-synthesised {len(audio)} bytes "
                  f"({samples} after the 44-byte header) as a valid WAV for "
                  f"this text; playback itself is not observable from here")


POST_CONDITION = _verify_spoke

SKILLS = [Skill(name="speak", schema=_SCHEMA, run=_run)]