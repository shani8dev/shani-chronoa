"""Singing: what Chronoa can honestly do with pitch, and what it cannot.

The question this answers is "can Chronoa sing?", and the answer is *no* - so
this module is named for the attempt rather than the result, and it says so in
`singing_support()` rather than in a docstring nobody reads. What it does build
is the part that is real.

**What is not possible here, and why.** Singing means sustaining a target pitch
while the voice speaks: the voice has to be measured, and bent toward a note.
Nothing in this stack can measure it. SoX has no pitch-detection effect - its
`spectrogram`, `stat` and `stats` write analysis *files*, they are not a control
path - and its realtime modulation is `--keymap`, which is driven by **keyboard
keypresses**, and which only two of its ~70 effects declare at all (`vol.gain`
and `contrast.amount`). `soundstretch` and `rubberband` shift pitch but do not
report the pitch of the audio passing through them. So there is no loop to
close, and no amount of scripting produces one.

Two traps found by running the binaries, both of which a plan written from
documentation gets wrong:

- **`vibrato` does not exist in SoX 14.8** (the version Arch ships). It is not
  in the effect list. Anything specifying it fails at run time with "cannot find
  an effect called 'vibrato'".
- **`rubberband --timemap` is a *time* map, not a pitch map.** It varies the
  stretch factor over the file; `-p` is a constant semitone shift. So it gives a
  tempo contour, and nothing gives a pitch contour except SoX `bend`.

**What is possible, and is built here:**

- **A pitch contour** - SoX `bend`, given cents per time segment. "The last
  sentence rises" is `bend 0,0 500,+200 900,0`. One pass, no extra dependency.
- **A whole-utterance transposition** - `soundstretch -pitch=` (cheap, realtime
  oriented) or `rubberband -p=` (better quality). Only worth reaching for when
  SoX's phase vocoder sounds bad; a *constant* shift is just a contour whose
  every point is the same number, so SoX alone covers it.
- **A tempo contour** - `rubberband --timemap`, which genuinely varies over time.

So the shape of it: `Contour` is the interesting type, `bend` is how it is
realised, and `transpose` exists for the quality upgrade rather than the
capability. The tone shaping from `voice_style` composes with all of it, because
everything here is one more list of SoX effects appended to the same single pass
`tts.apply_timbre` already runs.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: SoX `bend` works in cents. A semitone is 100, and ±6 semitones is roughly the
#: useful speaking range - beyond that the voice is a different character and
#: the timbre layer's `pitch` range is the honest limit.
CENTS_PER_SEMITONE = 100.0
MAX_SEMITONES = 6.0

#: Guards on a subprocess that must not wedge a reply. `tts.SOX_TIMEOUT` exists
#: for the timbre pass; the same budget applies here and there is no reason for
#: two different numbers to mean "long enough".
TIMEOUT_SECONDS = 30


class SingingUnsupported(Exception):
    """Raised when asked to do the part that is not possible.

    A separate exception type on purpose: this is not a missing package or a
    broken file, it is a capability that does not exist, and a caller should say
    so rather than retry.
    """


@dataclass(frozen=True)
class Point:
    """One point on a pitch contour: `seconds` in, `semitones` above the voice."""

    seconds: float
    semitones: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.seconds <= 3600.0:
            raise ValueError(f"contour time {self.seconds}s is not a time in a reply")
        if not -MAX_SEMITONES <= self.semitones <= MAX_SEMITONES:
            raise ValueError(
                f"{self.semitones} semitones is outside +/-{MAX_SEMITONES}; past "
                "that it is a different voice, not a different delivery")

    def cents(self) -> float:
        return self.semitones * CENTS_PER_SEMITONE


def contour_effects(points: Sequence[Point]) -> List[str]:
    """The SoX effects for a pitch contour, or [] when there is nothing to do.

    **One `bend` per segment, not one `bend` with many segments.** SoX's `bend`
    takes a single `start,cents,end` triple - a time window and the shift to
    apply across it - and a contour is expressed by chaining several of them. The
    obvious-looking `bend 0,0,500,200,900,0` fails outright:

        sox FAIL bend: cannot parse `0,0,500,200,900,0' as start,cents,end

    which is only knowable by running it; the usage string does not say it.

    Segments after the first are written with a leading `+` so they are
    *relative* to the pitch the previous segment left. Without it each `bend`
    would be absolute and the segments would overwrite one another instead of
    accumulating into a contour. The `+` is also the only way to write a negative
    cents value: `-200,0,4` is parsed as an option and fails with
    "invalid option `-2'".

    A contour whose points all carry the same number is a constant shift, and
    SoX's `pitch` does that more cheaply, so it is returned empty.
    """
    ordered = sorted(points, key=lambda point: point.seconds)
    for earlier, later in zip(ordered, ordered[1:]):
        if later.seconds <= earlier.seconds:
            raise ValueError(
                f"contour times must increase: {earlier.seconds} then "
                f"{later.seconds}")
    if len(ordered) < 2:
        return []          # one point is a constant shift: `pitch` is right
    if all(point.semitones == ordered[0].semitones for point in ordered):
        return []

    if len(ordered) > 2:
        # Measured: chaining `bend` does not compose. Applied to a steady 220 Hz
        # tone through four notes, a chained version reported **742 Hz** on the
        # first syllable, which no segment touched - each `bend` is a phase
        # vocoder over the whole signal and they compound. Splicing the audio and
        # shifting each piece measures within 1.5%, and lives in `prosody`.
        #
        # Refusing here rather than emitting something that sounds wrong: a
        # caller that wants per-syllable pitch needs the splice path, and this
        # function cannot provide it.
        raise ValueError(
            "a contour of more than two points cannot be built from sox bends - "
            "each one smears across the whole file. Use prosody.song_plan() and "
            "prosody.apply_song(), which cut and shift each note separately")
    first, last = ordered[0], ordered[-1]
    step = last.semitones - first.semitones
    if abs(step) < 0.01:
        return []
    effects = ["bend", f"{first.seconds:g},{step * CENTS_PER_SEMITONE:+.0f},"
                       f"{max(last.seconds, first.seconds + 0.04):g}"]
    return effects


def transpose(wav: str, semitones: float, output: str,
              backend: Optional[str] = None) -> str:
    """Shift a whole WAV by `semitones`, using whichever shifter is installed.

    `backend` is `"soundstretch"`, `"rubberband"` or None to choose. Returns the
    output path. Raises `SingingUnsupported` when neither is present, because
    falling back silently to SoX here would mean the caller believes it asked
    for a transposition and got a different one.

    This is a *quality* upgrade, not a capability one: a constant shift is
    expressible as a one-point contour and SoX's `bend`/`pitch` will do it with
    no extra package installed. Reach for this only when SoX's phase vocoder is
    audibly worse on the voice in question.
    """
    if not -MAX_SEMITONES <= semitones <= MAX_SEMITONES:
        raise ValueError(f"{semitones} semitones is outside +/-{MAX_SEMITONES}")
    chosen = backend or _best_transposer()
    if chosen is None:
        raise SingingUnsupported(
            "neither soundstretch (soundtouch) nor rubberband is installed; "
            "a constant pitch shift still works through sox, as a one-point "
            "contour")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    if chosen == "soundstretch":
        # Filenames first, switches after: `soundstretch infile outfile
        # [switches]`. The other order fails with "Illegal parameter
        # "<outfile>"" and prints its entire usage, which is how the mistake
        # costs more time than it should.
        #
        # `-speech` is not cosmetic. SoundTouch tunes for music by default, and
        # Chronoa only ever shifts a voice, so the default is the wrong algorithm
        # for every input this will ever see.
        argv = ["soundstretch", wav, output, f"-pitch={semitones:g}", "-speech"]
    else:
        argv = ["rubberband", f"-p{semitones:g}", "--quiet", wav, output]
    _run(argv)
    return output


def _best_transposer() -> Optional[str]:
    """soundstretch when present, rubberband otherwise.

    soundstretch first because it is realtime-oriented and cheap, which is the
    common case; rubberband is the better-sounding one and is worth naming
    explicitly when it is what you want.
    """
    if shutil.which("soundstretch"):
        return "soundstretch"
    if shutil.which("rubberband"):
        return "rubberband"
    return None


def tempo_map(points: Sequence[Tuple[float, float]]) -> Optional[str]:
    """Write a rubberband time map from `(seconds, stretch_factor)` pairs.

    Returns the path, or None for fewer than two points - a single point is not
    a map, and rubberband is explicit that a map must be paired with an overall
    stretch factor.
    """
    if len(points) < 2:
        return None
    ordered = sorted(points)
    frames: List[str] = []
    for seconds, factor in ordered:
        if factor <= 0:
            raise ValueError(f"a stretch factor of {factor} has no meaning")
        frames.append(f"{int(round(seconds * 48000))} {int(round(seconds * 48000 * factor))}")
    handle = tempfile.NamedTemporaryFile("w", suffix=".timemap", delete=False,
                                         prefix="chronoa-sing-")
    try:
        handle.write("\n".join(frames) + "\n")
    finally:
        handle.close()
    return handle.name


def singing_support() -> dict:
    """What this machine can actually do, as data.

    Built for a settings row or a panel: `can_track_pitch` is the one that
    matters and it is False everywhere, on every machine, forever, because it is
    a property of the approach rather than of the installation. Reporting it as
    a capability flag would be how a UI ended up offering a "sing" button that
    can never work.
    """
    return {
        "can_track_pitch": False,
        "why_not": ("nothing here measures the pitch of the audio it is "
                    "shifting, so it cannot bend a voice toward a note"),
        "can_contour_pitch": True,
        "can_transpose": _best_transposer() is not None,
        "transposer": _best_transposer(),
        "can_contour_tempo": bool(shutil.which("rubberband")),
        "sox_bend": bool(shutil.which("sox")),
        "realtime_modulation": False,
        "realtime_why_not": ("sox's --keymap is the only live parameter control it "
                             "has, it reads the keyboard, and only vol.gain and "
                             "contrast.amount declare one"),
    }


def _run(argv: Sequence[str]) -> None:
    """Run a subprocess, turning every failure into something reportable.

    Never raises `subprocess.CalledProcessError` out of here: a caller in the
    middle of assembling a reply needs a sentence to put in the log, not a
    traceback, and the failure has to name which of the two shifters it was.
    """
    try:
        done = subprocess.run(list(argv), capture_output=True,
                              timeout=TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise SingingUnsupported(f"{argv[0]} is not installed")
    except subprocess.TimeoutExpired:
        raise SingingUnsupported(
            f"{argv[0]} took longer than {TIMEOUT_SECONDS}s on a reply-sized file")
    except OSError as exc:
        raise SingingUnsupported(f"{argv[0]} could not be run: {exc}")
    if done.returncode != 0:
        detail = (done.stderr.decode("utf-8", "replace").strip()
                  or f"exit {done.returncode}")
        # First two lines only. `soundstretch` prints its whole usage on a bad
        # argument, and a 30-line error inside a log line helps nobody.
        detail = "; ".join(detail.splitlines()[:2])
        raise SingingUnsupported(f"{argv[0]} failed: {detail}")


def apply_contour(wav: str, output: str, points: Sequence[Point],
                  sox_binary: str = "sox") -> str:
    """Apply a pitch contour to `wav` with SoX, writing `output`.

    One pass, like `tts.apply_timbre` - the reason this lives beside the timbre
    code rather than beside an engine. A contour of fewer than two distinct
    points is a constant shift, and this routes it to `pitch` rather than
    emitting a `bend` with one segment, which SoX treats as a no-op.
    """
    effects = contour_effects(points)
    if not effects:
        ordered = sorted(points, key=lambda point: point.seconds)
        if not ordered:
            return wav
        effects = ["pitch", f"{int(round(ordered[0].cents()))}"]
    if not shutil.which(sox_binary):
        raise SingingUnsupported(f"{sox_binary} is not installed")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    _run([sox_binary, wav, output] + effects)
    return output


def duration_of(wav: str) -> float:
    """A WAV's length in seconds, from its own header.

    Parsed here rather than by running `sox --i`, because the caller needs the
    duration to *build* the effect chain and shelling out to measure it would
    mean a second subprocess before every reply that has a contour.
    """
    import struct
    try:
        with open(wav, "rb") as handle:
            header = handle.read(12)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return 0.0
            rate = 0
            while True:
                chunk = handle.read(8)
                if len(chunk) < 8:
                    return 0.0
                name, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                if name == b"fmt ":
                    body = handle.read(min(size, 16))
                    if len(body) >= 12:
                        rate = struct.unpack("<I", body[8:12])[0]
                elif name == b"data":
                    if not rate:
                        return 0.0
                    return size / float(rate)
                else:
                    handle.seek(size + (size % 2), os.SEEK_CUR)
    except (OSError, struct.error, ValueError):
        return 0.0


def within(points: Sequence[Point], seconds: float) -> List[Point]:
    """The contour's points that fall inside a WAV of `seconds`.

    A contour is written in seconds and a reply lasts however long it lasts, so a
    point past the end has nowhere to go. Dropped rather than clamped to the end:
    clamping would stretch the last segment to fill the remaining audio, which is
    a different shape from the one that was asked for and would sound like one.
    """
    if seconds <= 0:
        return []
    inside = [point for point in points if point.seconds < seconds]
    return sorted(inside, key=lambda point: point.seconds)
