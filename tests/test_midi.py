"""midi.py: parsing a Standard MIDI File and fitting its melody to a voice.

This file exists because the module had **no test at all**, which is how it got
to carry a module-level `_ = (os, struct)` that raised `NameError` on import
and stayed green across 4603 passing tests - nothing imported it, so nothing
could fail. AGENTS.md records the bug; this file is the fix for the class of
it.

The MIDI bytes here are built by hand in `midi_bytes()` rather than checked in
as a binary blob, so every field the parser depends on is visible in the test
and a wrong expectation is obvious. No sample, no dependency, no fixture to go
missing.
"""

import struct

import pytest

from shani_chronoa import midi


def _varlen(value: int) -> bytes:
    """A MIDI variable-length quantity: 7 bits per byte, high bit = continue."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def midi_bytes(notes, *, division=480, tempo_bpm=120.0) -> bytes:
    """A one-track format-0 SMF. `notes` is [(start_quarters, length_quarters, pitch)]."""
    track = bytearray()
    ticks_per_second = division * tempo_bpm / 60.0
    # A tempo meta event, so the parser's own conversion has something to read.
    us_per_quarter = int(60_000_000 / tempo_bpm)
    track += b"\x00\xFF\x51\x03" + us_per_quarter.to_bytes(3, "big")
    last = 0
    for start_q, length_q, pitch in sorted(notes):
        on = int(start_q * division)
        off = on + int(length_q * division)
        track += _varlen(on - last) + bytes([0x90, pitch, 100])
        track += _varlen(off - on) + bytes([0x80, pitch, 0])
        last = off
    track += _varlen(0) + b"\xFF\x2F\x00"

    # `MThd` + a 4-byte length + 6 bytes of header, then `MTrk` + length + data.
    # An earlier version of this builder emitted the 6 header bytes twice, which
    # parsed as a valid-looking file with no notes in it - a reminder that a
    # fixture built wrong looks exactly like a fixture asserting the wrong thing.
    return (b"MThd" + struct.pack(">IHHH", 6, 0, 1, division)
            + b"MTrk" + struct.pack(">I", len(track)) + bytes(track))


@pytest.fixture
def written(tmp_path):
    def write(name, notes, **kwargs):
        path = tmp_path / name
        path.write_bytes(midi_bytes(notes, **kwargs))
        return str(path)
    return write


# --- the parse ----------------------------------------------------------------


def test_reads_pitches_and_durations_in_seconds(written):
    # 120bpm, 480 ticks/quarter: one quarter note is exactly 0.5s.
    path = written("a.mid", [(0, 1, 60), (1, 1, 62), (2, 2, 64)])
    notes = midi.read_notes(path)
    assert [n.semitone for n in notes] == [60, 62, 64]
    assert [round(n.start, 3) for n in notes] == [0.0, 0.5, 1.0]
    assert [round(n.duration, 3) for n in notes] == [0.5, 0.5, 1.0]
    assert notes[0].end == pytest.approx(0.5)


def test_a_tempo_change_moves_the_times_that_follow_it(written):
    """The tempo event is real data, not decoration."""
    slow = midi.read_notes(written("slow.mid", [(0, 1, 60)], tempo_bpm=60.0))
    fast = midi.read_notes(written("fast.mid", [(0, 1, 60)], tempo_bpm=120.0))
    assert slow[0].duration == pytest.approx(1.0)
    assert fast[0].duration == pytest.approx(0.5)


def test_a_note_left_hanging_is_closed_at_the_end_not_dropped(written):
    """A melody whose last note vanished is not the melody."""
    path = tmp_path_path = written("open.mid", [(0, 1, 60), (1, 4, 62)])
    # Truncate the file so the second note-on has no matching note-off.
    data = bytearray(open(path, "rb").read())
    data = data[: -len(b"\x00\x80\x62\x00\xFF\x2F\x00")]
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as handle:
        handle.write(bytes(data))
        cut = handle.name
    notes = midi.read_notes(cut)
    assert [n.semitone for n in notes] == [60, 62]
    assert notes[-1].duration > 0, "a hanging note must still have a length"


def test_a_file_that_is_not_midi_is_refused(tmp_path):
    for name, payload in [
        ("empty.mid", b""),
        ("text.mid", b"this is a text file, not a MIDI one"),
        ("short.mid", b"MThd\x00\x00\x00"),
        ("wrongmagic.mid", b"RIFF" + b"\x00" * 20),
    ]:
        path = tmp_path / name
        path.write_bytes(payload)
        with pytest.raises(midi.MidiError):
            midi.read_notes(str(path))


def test_varlen_round_trips_through_the_parser():
    for value in (0, 1, 127, 128, 255, 16383, 16384, 1000000):
        raw = _varlen(value)
        assert midi._read_varlen(raw, 0) == (value, len(raw))


# --- picking the track --------------------------------------------------------


def test_the_busiest_track_is_the_melody(written):
    notes = [
        midi.Note(0.0, 0.5, 60, track=1),
        midi.Note(0.0, 0.5, 62, track=1),
        midi.Note(0.0, 0.5, 64, track=1),
        midi.Note(0.0, 4.0, 36, track=2),
    ]
    assert midi.melody_track(notes) == 1
    assert midi.notes_from(notes) == [n for n in notes if n.track == 1]
    assert midi.notes_from(notes, track=2) == [n for n in notes if n.track == 2]
    with pytest.raises(midi.MidiError, match="no notes"):
        midi.notes_from([])


# --- fitting to a voice -------------------------------------------------------


class FakeSyllable:
    def __init__(self, start, end):
        self.start = start
        self.end = end


def test_a_tune_wider_than_the_voice_is_centred_and_the_clip_is_counted():
    """`MAX_SEMITONES` is +/-6; most melodies span an octave."""
    notes = [midi.Note(index * 0.5, 0.5, 40 + index * 2, track=0) for index in range(13)]
    syllables = [FakeSyllable(index * 0.5, index * 0.5 + 0.5) for index in range(13)]
    fitted = midi.melody_for_syllables(notes, syllables, max_semitones=6.0)
    assert fitted.source_notes == 13
    assert fitted.span <= 12.0 + 1e-9
    assert fitted.clipped > 0, "a 24-semitone tune cannot fit in +/-6 without clipping"
    assert midi.describe(fitted).count("clipped") == 1


def test_a_narrow_tune_needs_no_clipping_and_says_so():
    notes = [midi.Note(index * 0.5, 0.5, 60 + index, track=0) for index in range(5)]
    syllables = [FakeSyllable(index * 0.5, index * 0.5 + 0.5) for index in range(5)]
    fitted = midi.melody_for_syllables(notes, syllables, max_semitones=6.0)
    assert fitted.clipped == 0
    assert "inside the voice's range" in midi.describe(fitted)
    assert fitted.span == pytest.approx(4.0)


def test_describe_states_every_number_it_reports():
    """A listener could disagree with any of these, so all of them are said."""
    notes = [midi.Note(index * 0.5, 0.5, 40 + index * 3, track=0) for index in range(10)]
    syllables = [FakeSyllable(index * 0.5, index * 0.5 + 0.5) for index in range(10)]
    text = midi.describe(midi.melody_for_syllables(notes, syllables, max_semitones=6.0))
    assert "track 0" in text
    assert "10 notes" in text
    assert "semitones" in text


def test_no_notes_is_refused_rather_than_fitted_to_nothing():
    with pytest.raises(midi.MidiError):
        midi.melody_for_syllables([], [FakeSyllable(0, 1)], max_semitones=6.0)


def test_fetch_refuses_bytes_that_are_not_a_midi_file():
    """`opener` returns bytes, and the MThd sniff is the check that matters."""
    with pytest.raises(midi.MidiError, match="not a MIDI file"):
        midi.fetch_bytes("https://example.invalid/x.mid",
                         opener=lambda url: b"<html>404</html>")


def test_fetch_refuses_a_response_over_the_limit():
    big = b"MThd" + b"\x00" * (midi.MAX_BYTES + 1)
    with pytest.raises(midi.MidiError, match="past the"):
        midi.fetch_bytes("https://example.invalid/x.mid", opener=lambda url: big)


def test_fetch_passes_the_bytes_through_when_they_are_a_midi_file(written, tmp_path):
    path = written("ok.mid", [(0, 1, 60)])
    raw = open(path, "rb").read()
    assert midi.fetch_bytes("https://example.invalid/x.mid",
                            opener=lambda url: raw) == raw
    assert [n.semitone for n in midi.from_url("https://example.invalid/x.mid",
                                              opener=lambda url: raw)] == [60]


def test_an_empty_note_list_is_a_midi_error_not_a_bare_value_error():
    """Found by running: `max()` on an empty dict raised `ValueError`."""
    with pytest.raises(midi.MidiError, match="no notes"):
        midi.melody_track([])


# --- the control: the import itself, which is what was broken -----------------


def test_the_module_imports_at_all():
    """The defect this file was written for: `import midi` raised NameError."""
    import importlib

    reloaded = importlib.import_module("shani_chronoa.midi")
    assert reloaded.read_notes is midi.read_notes


def test_fetch_bytes_declares_a_size_limit():
    """No unbounded read of a remote file."""
    assert isinstance(midi.MAX_BYTES, int)
    assert 0 < midi.MAX_BYTES <= 64 * 1024 * 1024


# --- mutation controls --------------------------------------------------------


def test_control_a_wrong_delta_changes_the_parse(written, monkeypatch):
    """The varlen reader must be load-bearing, not decorative.

    The first version of this mutation made every delta 0 and consumed one byte,
    which does not produce a wrong answer - it sends the event loop into an
    unbounded walk, and the test hung instead of failing. The delta is skewed by
    a tick here instead, so the parse terminates.
    """
    real = midi._read_varlen
    path = written("m.mid", [(0, 1, 60), (2, 1, 62)])

    correct = midi.read_notes(path)
    assert [n.semitone for n in correct] == [60, 62]
    assert correct[0].start == 0.0

    monkeypatch.setattr(midi, "_read_varlen", lambda data, offset: (
        real(data, offset)[0] + 1, real(data, offset)[1]))
    # Skewing the deltas desynchronises the note-on/note-off pairing, so the
    # file stops parsing as a melody at all. That is the point: the reader is
    # what pairs those events, and it is not incidental to the result.
    with pytest.raises(midi.MidiError):
        midi.read_notes(path)


def test_control_a_zero_clip_count_breaks_describe():
    """`describe` must report a clip that happened, not a clean tune."""
    fitted = midi.Fitted(semitones=(0.0, 6.0), reference=0.0, centre_shift=0.0,
                         clipped=3, track=0, source_notes=9)
    assert "3 note(s) clipped" in midi.describe(fitted)
    uncut = midi.Fitted(semitones=(0.0, 6.0), reference=0.0, centre_shift=0.0,
                        clipped=0, track=0, source_notes=9)
    assert "clipped" not in midi.describe(uncut), "the control must differ"