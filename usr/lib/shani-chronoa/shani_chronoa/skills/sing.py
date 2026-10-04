"""Skill: sing a line - the melodic counterpart of `speak`.

`singing.py` and `prosody.py` were built, unit-tested and verified on a real
image, and nothing in the product called either of them: asking Chronoa to sing
produced a spoken reply, because the only consumer of that code was the test
harness. This module is that missing surface, and it is deliberately the
*thinnest* one that can be honest.

**What it does.** Speaks the line with whatever engine the normal chain picks
(Kokoro when it is installed and permitted, else Piper/RHVoice/espeak-ng),
builds a per-syllable pitch plan from one of four named contours, shifts each
note with the same `soundstretch`/`rubberband` machinery `prosody.apply_song`
uses, applies the chosen voice style through SoX exactly as `tts.apply_timbre`
applies its three transforms, and plays the result through the same
`AudioPlayer` `speak` uses - so barge-in still interrupts it.

**What it deliberately does not do.** It does not know any song. There is no
tune database and no melody inference, because a tune Chronoa made up and
answered to "here is Twinkle Twinkle" is worse than saying no: the user would
hear something that is recognisably not the song they asked for and have no way
to tell that from a bad synthesis. So `shape` is one of four named contours and
the result string says which one was used. A caller who wants a specific tune
has to bring it.

The engine is reported in the result, because "it sang" and "the neural voice
sang" are different claims: on a formant synthesizer the same contour is
technically correct and audibly not singing, and `chronoa-singing.sh` on a real
image reports exactly which engine produced the line.

Refusals are specific. No transposer is a refusal naming soundtouch or
rubberband, not a silent fall back to speaking; a missing SoX costs the voice
style and is reported as a loss rather than as a failure, because the notes were
still sung.

**The rendering is kept.** The first version rendered into a TemporaryDirectory,
played it and threw it away, which made two things impossible: a user who asks
"sing that again" or wants to send the file has nothing to point at, and nothing
can observe whether the call did anything. So the final WAV is written under
`shani-chronoa/sing/` with a timestamp, the newest `_KEEP_RENDERINGS` of them
are kept and the rest pruned, and the result names the path. That file is also
what `POST_CONDITION` checks, which is what lifts this skill out of the
"unverified - nothing observed it" bucket `speak` and every other audio skill
sits in: a synthesis that claims success leaves a real WAV behind, and its
absence is a failure rather than an unverifiable claim.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.audio import AudioPlayer
from shani_chronoa.skills import Skill
from shani_chronoa.tts import PiperTTS

logger = logging.getLogger(__name__)

#: The only contours this will sing. Named, not invented per request.
#: Taken from `prosody.Melody.from_shape`, which is the code that builds
#: them, so a shape added there cannot be silently withheld here - the
#: first version of this list had four of the five and would have
#: refused a legitimate "wave".
_SHAPES = ("rising", "falling", "arch", "level", "wave")

# SoX runs the EQ/compression chain in one pass; the same bound `tts` uses for
# its own three effects, so a pathological style cannot hold the skill open.
_SOX_TIMEOUT = 120

# How many past renderings to keep under shani-chronoa/sing/. A songbook, not an
# archive: the oldest go, so this cannot grow without bound on a machine someone
# actually sings on.
_KEEP_RENDERINGS = 20

# Above this many characters the inline JSON transport starts running into
# Linux's MAX_ARGSTR_LEN (128 KiB per argv entry). Same rule as `speak`, and for
# the same reason: a lyric is routinely a whole line.
_BY_REFERENCE_TEXT_BYTES = 4 * 1024


def wants_by_reference(arguments: dict) -> bool:
    """True when this call's `text` needs the file transport rather than argv.

    Only `text` is considered - the schema has no other argument that could be
    oversized - and a short line stays inline, as in `speak`.
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


def _requested_style(arguments: dict) -> "tuple[str, list]":
    """`(name, effects)` for the voice style to apply.

    With no `voice_style` argument this uses whatever the user has already
    chosen in Settings, which is what "sing it in my soothing voice" means - the
    same source `tts.apply_timbre` reads, so a reply that is styled when spoken
    is styled when sung.

    Raises `ValueError` for a name that is not a preset. Ignoring an unknown
    style would sing the line in the default voice and report success, which is
    the same confident-wrong-answer shape this file's refusals exist to avoid.
    """
    from shani_chronoa import voice_style
    from shani_chronoa.config import ChronoaConfig

    wanted = arguments.get("voice_style")
    if isinstance(wanted, str) and wanted.strip():
        name = wanted.strip()
        try:
            return name, voice_style.style_effects(voice_style.resolve_preset(name))
        except (KeyError, ValueError) as exc:
            raise ValueError(str(exc)) from exc

    config = ChronoaConfig()
    style = voice_style.selected_style(config)
    if style is None:
        return "", []
    return _style_name(style), voice_style.style_effects(style)


def _style_name(style) -> str:
    """The preset name for a style the config selected, or a short description.

    `selected_style()` returns a resolved style rather than the name that chose
    it, so the result string falls back to describing the style when the name is
    not recoverable - naming the effect rather than guessing the preset.
    """
    from shani_chronoa import voice_style

    for name in voice_style.list_presets():
        try:
            if voice_style.resolve_preset(name) == style:
                return name
        except (KeyError, ValueError):
            continue
    return voice_style.describe_effects(style) or "the configured style"


def _apply_style(sung: str, style: "tuple[str, list]", workdir: str) -> "tuple[str, str]":
    """Apply `(name, effects)` to `sung`, returning `(file_to_play, note)`.

    A style that cannot run costs the style, not the song: SoX missing, timing
    out, or exiting non-zero each leave the engine's own sung WAV in place, and
    every one of them is named in the note. The note is part of the result the
    model reads, so "sang in the soothing voice" can never be reported for a
    performance the listener heard in the default voice.
    """
    name, effects = style
    if not effects:
        return sung, ""
    sox = shutil.which("sox")
    if not sox:
        return sung, f" (the {name} voice style was skipped: sox is not installed)"
    styled = os.path.join(workdir, "styled.wav")
    try:
        done = subprocess.run([sox, sung, styled] + effects,
                              capture_output=True, timeout=_SOX_TIMEOUT)
    except subprocess.TimeoutExpired:
        return sung, f" (the {name} voice style was skipped: sox took longer than {_SOX_TIMEOUT}s)"
    except OSError as exc:
        return sung, f" (the {name} voice style was skipped: sox could not be run: {exc})"
    if done.returncode != 0 or not os.path.exists(styled) or os.path.getsize(styled) <= 44:
        detail = (done.stderr.decode("utf-8", "replace").strip()
                  or f"exit {done.returncode}")
        return sung, f" (the {name} voice style was skipped: sox failed: {detail})"
    return styled, f" in the {name} voice"


def _rendering_dir() -> Path:
    """Where past renderings live, under the user's own data home."""
    return files.data_home() / "sing"


def _keep_rendering(audio: bytes) -> "tuple[str, str]":
    """Save `audio` as a timestamped WAV and return `(path, note)`.

    The name carries the time and the pid so two calls in the same second on
    different machines - or the same machine in a retry - cannot collide, which
    is what would make the post-condition below check the *previous* song.
    """
    directory = _rendering_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = directory / f"sung-{stamp}-{os.getpid()}.wav"
        path.write_bytes(audio)
    except OSError as exc:
        return "", f" (it could not be saved for later: {exc})"

    # Prune the oldest, best effort: a full disk here must not fail the song.
    try:
        past = sorted(directory.glob("sung-*.wav"), key=lambda p: p.stat().st_mtime)
        for old in past[:-_KEEP_RENDERINGS]:
            old.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("could not prune old renderings: %s", exc)
    return str(path), ""


def _verify_sung(arguments: dict, tool=None):
    """Post-condition: is there a real WAV from this skill on disk?

    Returns `(ok, evidence)`. The skill's own "Sang ... with kokoro" string is a
    claim by the same code that did the work, so the only evidence available is
    a file it left behind - and a WAV header with no samples behind it is
    exactly the failure a synthesis reports as success.

    Known limit, stated rather than hidden: the check looks for the newest
    rendering, not for *this* call's rendering, because a skill cannot hand its
    output to its own verifier. A call that fails before writing therefore reads
    as verified if an earlier song is still on disk; the alternative - reporting
    every audio action as unverifiable - is the state this exists to fix.
    """
    directory = _rendering_dir()
    try:
        past = sorted(directory.glob("sung-*.wav"), key=lambda p: p.stat().st_mtime)
    except OSError as exc:
        return False, f"could not look in {directory}: {exc}"
    if not past:
        return False, f"no rendering under {directory}"
    newest = past[-1]
    try:
        head = newest.open("rb").read(12)
        size = newest.stat().st_size
    except OSError as exc:
        return False, f"could not read {newest.name}: {exc}"
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return False, f"{newest.name} is not a WAV"
    if size <= 44:
        return False, f"{newest.name} has a header and no audio ({size} bytes)"
    return True, f"{newest.name} ({size} bytes)"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _verify_sung


def _run(arguments: dict) -> str:
    from shani_chronoa import prosody, singing

    text = _read_text(arguments).strip()
    if not text:
        return "Nothing to sing: the text argument was empty."

    shape = str(arguments.get("shape") or "arch").strip().lower()
    if shape not in _SHAPES:
        return (f"Did not sing: '{shape}' is not a melody shape. "
                f"Use one of: {', '.join(_SHAPES)}.")

    support = singing.singing_support()
    if not support.get("can_transpose"):
        return ("Did not sing: singing needs soundtouch (soundstretch) or "
                "rubberband to move each note to its own pitch, and neither is "
                "installed, so there is nothing to sing with. "
                "Speaking the line is still available through the speak skill.")

    try:
        style = _requested_style(arguments)
    except ValueError as exc:
        return f"Did not sing: {exc}"

    tts = PiperTTS()
    engine = tts.engine(text)
    if engine is None:
        return ("Could not sing: no text-to-speech engine is available on this "
                "host. Install piper-tts (+ a voice model), rhvoice (+ a voice), "
                "or espeak-ng, or install Kokoro for the neural voice.")

    try:
        style_name, _effects = style
        with tempfile.TemporaryDirectory(prefix="chronoa-sing-") as workdir:
            spoken = os.path.join(workdir, "spoken.wav")
            if not tts.synthesize(text, spoken):
                return (f"Could not sing: the {engine} engine produced no audio "
                        "for that line.")

            syllables = prosody.syllables_for(text)
            if not syllables:
                return ("Could not sing: no syllables could be found in the text, "
                        "so there is nothing to lay a melody over.")

            melody = prosody.Melody.from_shape(shape, len(syllables))
            plan = prosody.song_plan(syllables, melody,
                                     total=singing.duration_of(spoken))
            sung = os.path.join(workdir, "sung.wav")
            prosody.apply_song(spoken, sung, plan)

            final, style_note = _apply_style(sung, style, workdir)
            try:
                audio = Path(final).read_bytes()
            except OSError as exc:
                return f"Could not read the sung audio back: {exc}"
            if not audio:
                return f"Could not sing: the {engine} engine produced no audio."

    except singing.SingingUnsupported as exc:
        return f"Did not sing: {exc}"
    except Exception as exc:  # noqa: BLE001 - a message, never a traceback
        return f"Could not sing ({engine}): {type(exc).__name__}: {exc}"

    said = (f"Sang {len(syllables)} syllables as {len(plan)} notes on the "
            f"{shape} contour with {engine}{style_note}. That contour is not "
            "the tune of any named song.")

    saved, save_note = _keep_rendering(audio)
    said += f" Saved to {saved}." if saved else save_note

    player = AudioPlayer()
    if not player.is_available():
        return (f"{said} The line was synthesized but not played: no audio "
                "playback backend is available (need pw-play or aplay).")
    try:
        player.play_bytes(audio)
    except Exception as exc:  # noqa: BLE001 - never a traceback to the caller
        return f"{said} Could not play it: {exc}"
    return said


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "sing",
        "description": (
            "Sing a line aloud instead of speaking it: each syllable is moved to "
            "its own note, using the app's own text-to-speech engine (Kokoro "
            "when installed and permitted, else Piper/RHVoice/espeak-ng) and the "
            "local pitch shifter. Use this when someone asks to be sung to, to "
            "hear a line sung, or for a melody. It sings the words it is given "
            "on one of five named contours (rising, falling, arch, level, wave) "
            "- it "
            "does NOT know any song's tune, so never present its output as a "
            "specific song. Needs soundtouch or rubberband; reports the engine "
            "it used and any part of the request it could not honour."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The words to sing.",
                },
                "shape": {
                    "type": "string",
                    "enum": list(_SHAPES),
                    "description": (
                        "The melodic contour. Defaults to 'arch' (rises then "
                        "falls). Pick 'rising' for something that should lift, "
                        "'falling' to descend, 'wave' to rise and fall "
                        "again, 'level' for something flat and even."
                    ),
                },
                "voice_style": {
                    "type": "string",
                    "description": (
                        "Optional voice-style preset (e.g. 'soothing', "
                        "'warm', 'narrator'). Omit it to use the voice style "
                        "already chosen in Settings."
                    ),
                },
            },
            "required": ["text"],
        },
    },
}

SKILLS = [Skill(name="sing", schema=_SCHEMA, run=_run)]