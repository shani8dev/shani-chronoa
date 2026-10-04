"""Standard MIDI File reading, so a melody can be a real tune instead of a shape.

`singing.py` and `prosody.py` could already sing `Melody.from_notes(...)` - a
list of semitones per syllable - and nothing in the tree ever built one. That is
the whole reason Chronoa could only offer four named contours: a contour is a
shape chosen by the machine, and a song is notes someone else wrote. This module
is the missing half on the reading side: it turns a `.mid` file into notes, and
notes into a `prosody.Melody` aligned to the syllables of a line.

**Why parse it here when the image has `pw-mididump` and `wildmidi`.** Both are
on every image, and both were found by `tools/cli_matrix.py`'s inventory of
3990 commands. Neither helps: `wildmidi` *renders* a MIDI file to audio (which
is not singing - it is playing someone else's instrument patch, with no voice
and no words), and `pw-mididump` prints raw event bytes, which would mean parsing
the same variable-length quantities and running status anyway, from a pipe,
with the parse split across two processes. A Standard MIDI File header is 14
bytes and the interesting events are two opcodes; reading it directly is smaller
than wrapping a printer, has no process to start, and is testable from a byte
string with nothing installed.

**What is deliberately not here.** No URL fetching. `fetch_bytes()` will fetch
a MIDI file over HTTP because the user asked for that and a melody you cannot
get is not a melody - but it goes through the same egress policy
(`egress.check_destination`) and the same web-sense consent as every other fetch
Chronoa makes, it caps the size, and it sniffs the bytes rather than trusting the
content type. `sing` prefers a local file when the user gives a path.

**Range is a real limit and is reported, never hidden.** `singing.MAX_SEMITONES`
is +/-6: a voice cannot be shifted further without the result being a different
voice. Most melodies span an octave. So `melody_for_syllables` centres the
tune on the voice and clips what is left over, and returns how many notes it had
to clip - a caller that does not say so is reporting a tune it did not sing.
"""

import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Enough for a piano roll or a full arrangement; past this the file is not a
#: melody anyone asked Chronoa to sing, and reading it is how a memory-hungry
#: parser gets fed.
MAX_BYTES = 4 * 1024 * 1024

#: Standard MIDI Files divide everything by this per quarter note.
TICKS_PER_BEAT = 480

#: No tempo meta event means this, which is the spec default (120 bpm).
DEFAULT_BPM = 120.0


class MidiError(Exception):
    """A file that is not a MIDI file Chronoa can read, said in a way a caller can show."""


@dataclass(frozen=True)
class Note:
    """One note, in seconds from the start of the piece.

    `semitone` is the MIDI note number. Kept as the raw number on purpose: the
    reference pitch is a decision for whoever maps these onto a voice
    (`melody_for_syllables`), and baking it in here would hide that decision in
    the parser.
    """

    start: float
    duration: float
    semitone: int
    track: int

    @property
    def end(self) -> float:
        return self.start + self.duration


def _read_varlen(data: bytes, offset: int) -> Tuple[int, int]:
    """One MIDI variable-length quantity: 7 bits per byte, high bit = continue."""
    value = 0
    for _ in range(4):  # the spec caps this at 4 bytes
        if offset >= len(data):
            raise MidiError("the file ends in the middle of an event")
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset
    raise MidiError("a number in the file is longer than the format allows")


def read_notes(path) -> List[Note]:
    """Every note in `path`, in seconds, sorted by onset.

    Reads all tracks and returns their notes with the track index kept, so
    `melody_track` can choose one and say which. Notes still sounding when the
    file ends are closed at the end of the piece rather than dropped: a melody
    whose last note vanished is not the melody.

    `path` is a filesystem path, or anything with a `read_bytes()` - which is
    how `from_url` hands over a download without writing it to disk first.
    `Path(...)` would raise `TypeError` on such an object, so the two are told
    apart rather than assumed.
    """
    if hasattr(path, "read_bytes"):
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MidiError(f"could not read the downloaded file: {exc}") from exc
    else:
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise MidiError(f"could not read {path}: {exc}") from exc
    if len(raw) < 14:
        raise MidiError("that file is too short to be a MIDI file")
    if len(raw) > MAX_BYTES:
        raise MidiError(f"that MIDI file is {len(raw) // 1024} KB, past the "
                        f"{MAX_BYTES // 1024} KB this will read")
    if raw[:4] != b"MThd":
        raise MidiError("that is not a MIDI file (it does not start with MThd)")

    _fmt, ntrks, division = struct.unpack(">HHH", raw[8:14])
    if division & 0x8000:
        # SMPTE timing: frames per second and ticks per frame, not ticks per beat.
        # Tempo does not apply here at all - the clock is the recording
        # equipment's - so this is the whole of the conversion.
        frames = 256 - (division >> 8)
        smpte_ticks_per_second = frames * (division & 0xFF)
        if smpte_ticks_per_second <= 0:
            raise MidiError("that MIDI file declares a division of zero")
    elif division == 0:
        raise MidiError("that MIDI file declares a division of zero")

    offset = 14
    tempo_us: Optional[int] = None
    tracks: "list[list[tuple]]" = []
    for track_index in range(ntrks):
        if offset + 8 > len(raw) or raw[offset:offset + 4] != b"MTrk":
            break
        length = struct.unpack(">I", raw[offset + 4:offset + 8])[0]
        body = raw[offset + 8:offset + 8 + length]
        offset += 8 + length
        tick, running, pending = 0, 0, []
        open_notes: "dict[tuple, list]" = {}
        events: "list[tuple]" = []
        position = 0
        while position < len(body):
            delta, position = _read_varlen(body, position)
            tick += delta
            if position >= len(body):
                break
            status = body[position]
            if status & 0x80:
                position += 1
                running = status
            else:
                status = running      # running status: the high bit is optional
                if not status:
                    raise MidiError("an event in the file has no status byte")
            kind = status & 0xF0
            if status == 0xFF:        # meta event
                if position >= len(body):
                    break
                meta = body[position]
                position += 1
                size, position = _read_varlen(body, position)
                data = body[position:position + size]
                position += size
                if meta == 0x51 and len(data) == 3 and tempo_us is None:
                    tempo_us = int.from_bytes(data, "big") or 500_000
                continue
            if status in (0xF0, 0xF7):   # sysex: skip the payload
                size, position = _read_varlen(body, position)
                position += size
                continue
            needed = 1 if kind in (0xC0, 0xD0) else 2
            if position + needed > len(body):
                break
            data = body[position:position + needed]
            position += needed
            channel = status & 0x0F
            if kind == 0x90 and data[1] > 0:            # note on
                open_notes.setdefault((channel, data[0]), []).append(tick)
                pending.append((tick, (channel, data[0], tick)))
            elif kind == 0x80 or (kind == 0x90 and data[1] == 0):   # note off
                key = (channel, data[0])
                if open_notes.get(key):
                    started = open_notes[key].pop(0)
                    events.append((started, (channel, data[0], tick)))
        # Anything still held is closed at the end of this track.
        for (channel, pitch), starts in open_notes.items():
            for started in starts:
                events.append((started, (channel, pitch, tick)))
        tracks.append(events)

    bpm = DEFAULT_BPM if not tempo_us else 60_000_000.0 / tempo_us
    # One tick is one `1/division` of a beat, and a beat is `60/bpm` seconds.
    # Dividing by a `ticks_per_second` derived from `TICKS_PER_BEAT` instead
    # applies the beat length twice: at the default 480 division and 120bpm that
    # made every onset and duration **half** what they should be, so a melody
    # sung from a parsed file ran at double speed, and any file using a division
    # other than 480 - which is most of them, since 96 and 240 are common - was
    # wrong by a different factor again. `division` is the file's own number
    # and is the only denominator that is right for all of them.
    seconds_per_tick = (1.0 / smpte_ticks_per_second if division & 0x8000
                        else (60.0 / bpm) / division)
    notes: List[Note] = []
    for track_index, events in enumerate(tracks):
        for start_tick, (_channel, pitch, end_tick) in events:
            duration = max(0.0, (end_tick - start_tick) * seconds_per_tick)
            notes.append(Note(start=start_tick * seconds_per_tick,
                              duration=duration,
                              semitone=pitch,
                              track=track_index))
    if not notes:
        raise MidiError("that MIDI file has no notes in it")
    notes.sort(key=lambda n: (n.start, n.semitone))
    return notes


def melody_track(notes: Sequence[Note]) -> int:
    """Which track holds the melody: the one with the most notes.

    A plain choice with a plain failure mode - an accompaniment-heavy file puts
    the tune in a quiet voice, not the busiest track - so the caller is expected
    to report the track number and the note count it used. Guessing silently
    would produce a confident wrong tune, which is the specific thing this repo
    keeps refusing to ship.
    """
    counts: "dict[int, int]" = {}
    for note in notes:
        counts[note.track] = counts.get(note.track, 0) + 1
    if not counts:
        # `max()` on an empty sequence raises a bare `ValueError: max() iterable
        # argument is empty`, which is not something a caller can show a user -
        # this module's own contract is that a file Chronoa cannot sing from is
        # reported as `MidiError`. Found by running it; there was no test here
        # to have found it, which is the other half of the problem.
        raise MidiError("that MIDI file has no notes in it")
    return max(counts, key=lambda track: (counts[track], -track))


def notes_from(notes: Sequence[Note], track: Optional[int] = None) -> List[Note]:
    """The notes of one track (the melody track by default), in onset order."""
    chosen = melody_track(notes) if track is None else track
    return [note for note in notes if note.track == chosen]


@dataclass(frozen=True)
class Fitted:
    """A melody fitted to what a voice can actually do."""

    semitones: Tuple[float, ...]
    reference: float
    centre_shift: float
    clipped: int
    track: int
    source_notes: int

    @property
    def span(self) -> float:
        if not self.semitones:
            return 0.0
        return max(self.semitones) - min(self.semitones)


def melody_for_syllables(notes: Sequence[Note], syllables: Sequence, max_semitones: float,
                         track: Optional[int] = None) -> Fitted:
    """Fit the melody in `notes` onto `syllables`, in the voice's own semitones.

    Each syllable takes the note it overlaps most, which is what makes this work
    on a real tune: a lyric has more syllables than a melody has notes (and
    sometimes fewer), and repeating the note a syllable sits inside is musically
    right where padding a list to a length would not be.

    The mapping to *relative* semitones has three steps, each of which is a
    decision that changes the result, so all three are returned:

    1. the melody's **median** pitch becomes 0, so the tune sits in the middle of
       the voice's range instead of wherever the file happened to be written;
    2. the result is centred, so its extremes fit +/-`max_semitones`;
    3. whatever is still outside is clipped, and counted.

    A tune that had notes clipped reports `clipped > 0` and the caller says so.
    That is the difference between singing a melody and singing something shaped
    like it.
    """
    from shani_chronoa import prosody  # imported here: prosody imports nothing from here

    chosen = notes_from(notes, track)
    if not chosen:
        raise MidiError("that MIDI file has an empty melody track")
    pitches = sorted(float(note.semitone) for note in chosen)
    median = pitches[len(pitches) // 2] if len(pitches) % 2 else (
        (pitches[len(pitches) // 2 - 1] + pitches[len(pitches) // 2]) / 2)

    per_syllable: "list[float]" = []
    for syllable in syllables:
        start = getattr(syllable, "start", 0.0)
        end = getattr(syllable, "end", start)
        overlap = [note for note in chosen if note.start < end and note.end > start]
        if not overlap:
            # A syllable with no note under it takes the nearest one rather than
            # silence: a rest would read as the tune having a hole in it.
            nearest = min(chosen, key=lambda note: min(abs(note.start - end),
                                                      abs(note.end - start)))
            overlap = [nearest]
        per_syllable.append(float(overlap[0].semitone) - median)

    if per_syllable:
        middle = sorted(per_syllable)[len(per_syllable) // 2] if len(per_syllable) % 2 else (
            (sorted(per_syllable)[len(per_syllable) // 2 - 1]
             + sorted(per_syllable)[len(per_syllable) // 2]) / 2)
        shift = -middle
        per_syllable = [value + shift for value in per_syllable]
    clipped = sum(1 for value in per_syllable if abs(value) > max_semitones)
    per_syllable = [max(-max_semitones, min(max_semitones, value)) for value in per_syllable]

    melody = prosody.Melody(tuple(per_syllable))
    if not per_syllable:
        raise MidiError("there were no syllables to put that melody on")
    _ = melody  # constructed to validate the shape prosody will accept
    return Fitted(semitones=tuple(per_syllable), reference=median,
                  centre_shift=0.0, clipped=clipped,
                  track=melody_track(notes), source_notes=len(chosen))


def fetch_bytes(url: str, opener=None, limit: int = MAX_BYTES) -> bytes:
    """Download a MIDI file, under the same policy as any other fetch.

    The web-sense consent is the *caller's* to check (see `skills/sing.py`, which
    refuses before reaching here) - this function's job is that nothing else gets
    out: the destination goes through `egress.check_destination`, the response
    is capped, and the bytes are sniffed for `MThd` instead of trusting a
    content type. `opener` exists so a test can hand in bytes without a socket.
    """
    from shani_chronoa import webtext

    if opener is not None:
        raw = opener(url)
    else:
        raw = webtext.retrieve(url).text.encode("utf-8", "replace")
    if len(raw) > limit:
        raise MidiError(f"that file is {len(raw) // 1024} KB, past the "
                        f"{limit // 1024} KB this will read")
    if raw[:4] != b"MThd":
        raise MidiError("what came back is not a MIDI file")
    return raw


def from_url(url: str, opener=None) -> List[Note]:
    """The notes of a MIDI file at `url`."""
    raw = fetch_bytes(url, opener=opener)
    return read_notes(_Bytes(raw))


class _Bytes:
    """A tiny file-like over bytes, so `read_notes` takes one input shape."""

    def __init__(self, raw: bytes):
        self._raw = raw

    def read_bytes(self) -> bytes:
        return self._raw


def describe(fitted: Fitted) -> str:
    """One sentence about what was fitted, for the result string.

    Every number in here is something a listener could disagree with, so all of
    them are stated: which track, how many notes, how wide the tune is, and how
    many notes the voice's range forced to be clipped.
    """
    parts = [f"melody from MIDI track {fitted.track} ({fitted.source_notes} notes)"]
    if fitted.clipped:
        parts.append(f"{fitted.clipped} note(s) clipped to the voice's range")
    else:
        parts.append("every note inside the voice's range")
    if fitted.span:
        parts.append(f"spanning {fitted.span:.1f} semitones")
    return ", ".join(parts)