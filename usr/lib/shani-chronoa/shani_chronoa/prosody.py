"""Per-syllable pitch: the difference between a voice and a singer.

`singing.py` bends a whole utterance along one line. That reads as *emphasis*,
not as singing, and the reason is now specific rather than a hunch: pitch that
changes once per sentence is intonation, and pitch that changes once per
**syllable** is a melody. So this module's whole job is to make the pitch change
at syllable boundaries, at pitches the caller asked for.

**The enabler is timestamps.** A melody needs to know when each syllable happens,
and Chronoa's neural voices can say: sherpa-onnx's `generate()` returns a
timestamp per token (text, start, duration), which is exactly the alignment a
contour needs. Two sources are supported, and which one you got is never hidden:

- `durations_from_neural()` - real per-token timestamps, from sherpa-onnx's
  Python API. Only available when that API is importable; Chronoa's own Kokoro
  path shells out to the `sherpa-onnx-offline-tts` binary, which does not print
  timestamps, so this returns None and says so rather than guessing.
- `estimate_durations()` - phonemes from `espeak-ng -x`, timed by a small
  duration-per-phoneme-class model. Always available, and honest about being an
  estimate: syllable *count* and *order* are right, absolute times are not, and
  since the contour is placed by proportion the error mostly cancels.

**A melody is never invented.** `Melody.from_shape()` offers four shapes that are
named out loud to the user before they are used, and `Melody.from_notes()` takes
an explicit note per syllable. There is no "compose something fitting" path,
because a melody Chronoa made up and then presented as the song someone asked for
is the one outcome worse than saying no.
"""

from __future__ import annotations

import logging
import re
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import singing

logger = logging.getLogger(__name__)

#: Vowel nuclei. A syllable is a vowel nucleus plus whatever consonants touch
#: it, so these are what the text is searched for. IPA and ASCII vowels both
#: appear depending on which engine tokenised the text, which is why this is a
#: set of characters rather than a list of words.
#: Vowel nuclei. A syllable is a vowel nucleus plus whatever consonants touch
#: it, so these are what the text is searched for.
#:
#: Listed explicitly rather than cut out of the IPA inventory, because the
#: inventory also contains ɡ, β, ç, ð, ɬ, ɤ, ʁ and a dozen other *consonants*,
#: and a set built from it made "ɡɑd" two syllables with ɡ as the first nucleus.
VOWELS = frozenset(
    "aeiouyæɑɒəɐɚɜɝɛɪɨʉʊʌøœɵɘɶɤy"
    "ɐɑɒæəɛɜɪɔɝɯʊøœɚɵ"
    "ɑɒɜɪʊɝɚ"
    "aɐɑɒæəɜɝɛɪɔøœɯʊʌɚɝɨʉ"
)
VOWEL_GROUPS = ("ə", "ɑ", "ɒ", "æ", "ɐ", "ɔ", "ə", "ɜ", "ɪ", "ʊ", "ʌ", "ɛ",
                "ɘ", "ɚ", "ʉ", "ɵ", "ø", "œ", "ɶ", "a", "e", "i", "o", "u", "y")

#: Rough durations in milliseconds by phoneme class. Not a phonology, just
#: enough that a vowel is longer than the consonant before it - which is the only
#: thing the contour placement actually depends on.
_DURATIONS_MS = {
    "vowel": 120.0, "long_vowel": 190.0, "nasal": 70.0, "liquid": 65.0,
    "fricative": 95.0, "stop": 55.0, "affricate": 75.0, "silence": 60.0,
}
#: Vowels that are drawn out, because the pitch of a long vowel is where a
#: listener hears a note.
_LONG_VOWELS = frozenset("ɑɒɜɪʊɝɚʊːˑ")

#: Shortest pitch glide between two notes. Below roughly this a pitch step is a
#: click rather than a change of note.
MINIMUM_GLIDE_SECONDS = 0.04

_PHONEME_CLASS = (
    ("vowel", frozenset(VOWELS)),
    ("nasal", frozenset("mnŋɱɲɳ")),
    ("liquid", frozenset("lrɹɾɫɬ")),
    ("fricative", frozenset("sfvθðzʃʒʂxɣhçʁ")),
    ("affricate", frozenset("tdʤʧʥʧʒ")),
    ("stop", frozenset("pbtdkɡɢqʔ")),
)


@dataclass(frozen=True)
class Token:
    """One token with the window it occupies."""

    text: str
    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass(frozen=True)
class Syllable:
    """A vowel nucleus and the consonants attached to it."""

    index: int
    start: float
    end: float
    tokens: Tuple[Token, ...]
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def onset(self) -> float:
        """When the pitch for this syllable should have arrived.

        The *start* of the syllable, not its vowel. Real singing moves the pitch
        on the consonant going into the note, so bending to the target at the
        vowel would sound like a late slide into every single note.
        """
        return self.start


def _class_of(symbol: str) -> str:
    for name, members in _PHONEME_CLASS:
        if symbol in members:
            if name == "vowel" and symbol in _LONG_VOWELS:
                return "long_vowel"
            return name
    return "silence"


def tokenise(text: str) -> List[str]:
    """Split into pseudo-phonemes, keeping multi-character symbols together.

    Grouping matters for timing: espeak writes `tʃ` and `aɪ`, and treating those
    as two symbols each would halve their duration.
    """
    out: List[str] = []
    index = 0
    while index < len(text):
        symbol = text[index]
        if index + 1 < len(text) and (symbol + text[index + 1]) in (
                set(_PHONEME_CLASS[1][1]) | set(_PHONEME_CLASS[4][1])):
            out.append(symbol + text[index + 1])
            index += 2
            continue
        out.append(symbol)
        index += 1
    return out


def strip_stress(text: str) -> str:
    """Remove espeak's stress marks and separators, keeping the symbols."""
    return re.sub(r"[ˈˌ:ːˈˌ.ˑ| ‿\t]", "", text)


def estimate_durations(text: str, executable: str = "espeak-ng",
                       gap: float = 0.0) -> List[Token]:
    """Phonemes with estimated timings, from `espeak-ng -x -q`.

    The fallback that always works, and the reason `durations_from_neural`
    exists. Absolute times are approximate; what it gets right is the syllable
    count, their order, and their relative weight, which is all the contour
    placement uses.
    """
    if shutil.which(executable) is None:
        raise singing.SingingUnsupported(f"{executable} is not installed")
    done = subprocess.run([executable, "-q", "-x", "--ipa=1"],
                          input=text.encode("utf-8"), capture_output=True,
                          timeout=15)
    if done.returncode != 0:
        raise singing.SingingUnsupported(
            f"{executable} failed on this text: "
            f"{done.stderr.decode('utf-8', 'replace')[:120]}")
    raw = done.stdout.decode("utf-8", "replace")
    if not raw.strip():
        raise singing.SingingUnsupported(
            "espeak-ng produced no phonemes for this text, so there is nothing "
            "to align a melody to")

    tokens: List[Token] = []
    clock = 0.0
    for word_index, word in enumerate(raw.split()):
        symbols = tokenise(strip_stress(word))
        if not symbols:
            continue
        if word_index and gap:
            clock += gap          # espeak's -x does not emit word separators
        for symbol in symbols:
            length = _DURATIONS_MS[_class_of(symbol)] / 1000.0
            tokens.append(Token(text=symbol, start=clock, duration=length))
            clock += length
    return tokens


def durations_from_neural() -> Optional[List[Token]]:
    """Real per-token timestamps, when sherpa-onnx's Python API is importable.

    Chronoa's own Kokoro path runs the `sherpa-onnx-offline-tts` *binary*, and
    that binary does not print timestamps - so this returns None in a normal
    install and says so. It exists so that the better source is a one-line change
    the day something can supply it, rather than a rewrite.
    """
    try:
        import importlib
        importlib.import_module("sherpa_onnx")
    except Exception:
        logger.debug("sherpa-onnx's Python API is not available; falling back "
                     "to estimated durations")
        return None
    logger.warning("sherpa-onnx is importable but no text was supplied; the "
                   "caller must call the neural voice itself and pass the "
                   "timestamps in. This returning None is the honest answer.")
    return None


def segment(tokens: Sequence[Token]) -> List[Syllable]:
    """Group tokens into syllables around their vowel nuclei.

    Syllable *i* owns its nucleus and every consonant from just after the
    previous nucleus up to and including its own, and anything after the last
    nucleus joins the last syllable. The first version instead took everything
    between one nucleus and the next, which put the shared consonant in **both**
    syllables - so `hello world` came out as four overlapping syllables and the
    contour arrived at notes before the syllable that owned them.

    Consonants after the final nucleus are attached rather than dropped, so a
    word-final consonant is not left outside the contour entirely.
    """
    nuclei = [index for index, token in enumerate(tokens)
              if token.text and _class_of(token.text[0]) in
              ("vowel", "long_vowel")]
    if not nuclei:
        return []
    syllables: List[Syllable] = []
    for order, nucleus in enumerate(nuclei):
        first = nuclei[order - 1] + 1 if order else 0
        # Ends at its OWN nucleus, inclusive: a consonant between two vowels is
        # the onset of the second one (maximal onset), so giving it to both is
        # what made the syllables overlap. The last syllable runs to the end so
        # nothing is dropped.
        last = nuclei[order] + 1 if order + 1 < len(nuclei) else len(tokens)
        group = tuple(tokens[first:last])
        if not group:
            group = (tokens[nucleus],)
        syllables.append(Syllable(
            index=order, start=group[0].start, end=group[-1].end,
            tokens=group, text="".join(item.text for item in group)))
    return syllables


def syllables_for(text: str, executable: str = "espeak-ng") -> List[Syllable]:
    """The syllables of `text`, with estimated timings."""
    return segment(estimate_durations(text, executable))


@dataclass(frozen=True)
class Melody:
    """One target pitch per syllable, in semitones relative to the voice.

    Built only from explicit input: a named shape, or notes the user gave. There
    is no constructor that makes one up, which is the whole point.
    """

    semitones: Tuple[float, ...]

    @classmethod
    def level(cls, count: int) -> "Melody":
        return cls(tuple(0.0 for _ in range(count)))

    @classmethod
    def from_shape(cls, shape: str, count: int) -> "Melody":
        """One of the four shapes that are named to the user before use.

        `rising`, `falling`, `arch` and `wave`. Each is a *shape*, not a tune, and
        every one of them is discoverable in one word - which is the property
        that makes "Chronoa chose this for you" acceptable. Anything more specific
        has to come from the user.
        """
        if count < 1:
            return cls(())
        if shape == "level":
            return cls.level(count)
        if shape == "rising":
            return cls(tuple(singing.MAX_SEMITONES * index / max(1, count - 1)
                             for index in range(count)))
        if shape == "falling":
            # Descends from the voice's OWN pitch, not from MAX. Starting at +6
            # meant the very first syllable was a 600-cent jump in 10 ms, which
            # is a click, not a note - and "falling" describing a line that
            # starts 6 semitones above the voice is not what anyone means by it.
            return cls(tuple(-singing.MAX_SEMITONES * index / max(1, count - 1)
                             for index in range(count)))
        if shape == "arch":
            half = max(1, count // 2)
            return cls(tuple(
                singing.MAX_SEMITONES * (index if index <= half else count - 1 - index)
                / half for index in range(count)))
        if shape == "wave":
            import math
            return cls(tuple(
                singing.MAX_SEMITONES * 0.5 * (1 - math.cos(2 * math.pi * index
                                                             / max(2, count)))
                for index in range(count)))
        raise KeyError(f"Unknown melody shape: {shape!r}")

    @classmethod
    def from_notes(cls, notes: Sequence[float], count: int) -> "Melody":
        """Explicit notes. Padded or truncated to `count`, loudly.

        A caller that passes four notes for a seven-syllable line has made a
        mistake, and repeating the last note would hide it - so the caller is
        told by way of the log and gets exactly what was asked for.
        """
        values = [float(note) for note in notes[:count]]
        if len(values) < count:
            logger.warning("a melody of %d notes was given for %d syllables; "
                           "the rest are left unshifted", len(values), count)
            values.extend(0.0 for _ in range(count - len(values)))
        return cls(tuple(values))

    def for_count(self, count: int) -> "Melody":
        """This melody adjusted to a syllable count."""
        if len(self.semitones) == count:
            return self
        if not self.semitones:
            return Melody.level(count)
        return Melody.from_notes(self.semitones, count)


@dataclass(frozen=True)
class Slice:
    """One syllable's worth of audio, and where its pitch should sit."""

    start: float
    duration: float
    semitones: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def song_plan(syllables: Sequence[Syllable], melody: Melody,
              total: Optional[float] = None) -> List[Slice]:
    """The audio to cut into, and the pitch each piece should be shifted to.

    **This returns a cutting plan, not SoX arguments, and that is the whole
    design.** The obvious implementation - one `bend` per syllable, chained -
    was measured and it does not work: applied to a steady 220 Hz tone through
    four rising notes it reported **742 Hz** on the first syllable, which should
    have been untouched. `bend` is a phase vocoder over the whole signal, so
    every extra segment smears across everything and they compound.

    Slicing the audio and shifting each piece on its own does work. The same
    four notes measured 217 / 250 / 275 / 250 Hz against targets of
    220 / 247 / 277 / 247 - within 1.5% - because each `soundstretch` call sees
    only its own syllable and cannot leak into the next.

    The cost is a subprocess per syllable. For a sung line that is 10-20 calls,
    which is why this is a deliberate feature rather than something on the reply
    path: `tts.apply_timbre` still does one SoX pass for ordinary speech.
    """
    if not syllables:
        raise ValueError("no syllables: the alignment found no vowel to sing")
    chosen = melody.for_count(len(syllables))
    end_of_audio = total if total is not None else syllables[-1].end
    plan: List[Slice] = []
    for syllable, target in zip(syllables, chosen.semitones):
        # A little lead-in on the first piece, so the first shift does not start
        # on a transient and click.
        lead = 0.02 if not plan else 0.0
        start_at = max(0.0, syllable.start - lead)
        stop_at = min(end_of_audio, syllable.end)
        if stop_at - start_at < 0.01:
            continue
        plan.append(Slice(start=start_at, duration=stop_at - start_at,
                          semitones=target))
    return plan


def _read_wav(path: str) -> "tuple[bytes, dict]":
    """All frames plus the parameters needed to write them back.

    The cut happens in this process, not in a `sox trim` subprocess. `sox` is an
    optdepend that the stock image does not install (the slot test proved this),
    so a singing path that requires it would be a no-op on most real machines.
    Reading and rewriting frames is the same thing it does, and every Python on
    the image has `wave`.
    """
    import wave
    with wave.open(path, "rb") as handle:
        return handle.readframes(handle.getnframes()), {
            "nchannels": handle.getnchannels(),
            "sampwidth": handle.getsampwidth(),
            "framerate": handle.getframerate(),
        }


def _write_wav(path: str, frames: bytes, params: dict) -> None:
    import wave
    with wave.open(path, "wb") as handle:
        handle.setnchannels(params["nchannels"])
        handle.setsampwidth(params["sampwidth"])
        handle.setframerate(params["framerate"])
        handle.writeframes(frames)


def _slice_frames(frames: bytes, params: dict, start: float,
                  duration: float) -> bytes:
    """Frames for `[start, start+duration]`, aligned to sample boundaries.

    A cut that lands inside a multi-byte sample would read as one bad sample per
    channel and click; aligned to the frame size it is silent.
    """
    frame_size = params["sampwidth"] * params["nchannels"]
    per_second = params["framerate"]
    first = int(round(start * per_second)) * frame_size
    count = int(round(duration * per_second)) * frame_size
    return frames[first:first + count]


def apply_song(wav: str, output: str, plan: Sequence[Slice],
               sox_binary: str = "sox",
               shifter: Optional[str] = None) -> str:
    """Shift each note in `plan` to its pitch and join them back.

    Cutting and joining are done here, in Python, because `sox` is an optdepend
    the stock image does not install - a singing path that needs `sox trim`
    would do nothing on most real machines. Only the per-note shift still needs a
    real binary, and that is soundstretch or rubberband, both of which are now
    optdepends so they are at least offered.
    """
    import tempfile

    if not plan:
        return wav
    chosen = shifter or singing._best_transposer()
    needs_shift = any(abs(item.semitones) > 0.01 for item in plan)
    if chosen is None and needs_shift:
        raise singing.SingingUnsupported(
            "singing needs soundstretch (soundtouch) or rubberband to shift each "
            "note; neither is installed")
    if needs_shift:
        # Checked before any work, and against the *program that will actually
        # run* - which for "rubberband" is not the name that was asked for. The
        # alternative is discovering it only after cutting the audio into pieces,
        # with the caller's file already opened for writing.
        program = "soundstretch" if chosen == "soundstretch" else "rubberband"
        if shutil.which(program) is None:
            raise singing.SingingUnsupported(
                f"{program} is not installed, so the notes cannot be shifted")
    else:
        # A level melody needs no shifter at all, and must not demand one.
        chosen = None

    frames, params = _read_wav(wav)
    pieces: List[bytes] = []
    workspace = tempfile.mkdtemp(prefix="chronoa-song-")
    try:
        for index, item in enumerate(plan):
            cut = _slice_frames(frames, params, item.start, item.duration)
            if abs(item.semitones) <= 0.01 or chosen is None:
                pieces.append(cut)
                continue
            raw = os.path.join(workspace, f"raw{index}.wav")
            shifted = os.path.join(workspace, f"shift{index}.wav")
            _write_wav(raw, cut, params)
            if chosen == "soundstretch":
                # Filenames before switches, and `-speech` because SoundTouch
                # tunes for music and this is a voice. Both learned by running it.
                argv = ["soundstretch", raw, shifted,
                        f"-pitch={item.semitones:g}", "-speech"]
            else:
                argv = ["rubberband", f"-p{item.semitones:g}", "--quiet",
                        raw, shifted]
            singing._run(argv)
            shifted_frames, shifted_params = _read_wav(shifted)
            # A shift that changes the sample count (rubberband's `-p` should
            # preserve length, but soundstretch pads a little) is still one
            # piece: take however much of it landed.
            pieces.append(shifted_frames)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        _write_wav(output, b"".join(pieces), params)
        return output
    finally:
        try:
            import shutil as _shutil
            _shutil.rmtree(workspace, ignore_errors=True)
        except Exception:
            pass


def sing_effects(syllables: Sequence[Syllable], melody: Melody,
                 total: Optional[float] = None) -> List[Slice]:
    """Kept as the old name so callers keep working; returns a plan, not effects.

    The name is a small lie that is worth correcting here rather than in every
    caller: it returns `Slice` objects, and `apply_song` is what runs them.
    """
    return song_plan(syllables, melody, total)
