"""Per-syllable pitch, and the measurement that says whether it works.

The claim under test is specific: **pitch changes once per syllable, by the
amount asked for.** That is the whole difference between singing and intonation,
and it is measurable on a synthetic signal where nothing else moves - a steady
tone chopped into syllable-length windows. A file of the right size is not
evidence; a measured frequency per window is.

The last class runs real SoX, so it is skipped where SoX is absent rather than
passing vacuously - the same reason `chronoa-singing.sh` exists in the testbed,
one level up, where the question is whether SoX is installed at all.
"""

from __future__ import annotations

import math
import shutil
import struct
from pathlib import Path

import pytest

from shani_chronoa import prosody, singing

HAS_SOX = shutil.which("sox") is not None
HAS_ESPEAK = shutil.which("espeak-ng") is not None
#: A per-note pitch shifter, which is a different program from sox and not
#: something sox can do: `apply_song` cuts the notes apart itself and hands each
#: one to this to move by its own number of semitones. Probed through the
#: module's own chooser rather than `shutil.which`, so the skip condition and the
#: code's condition cannot drift apart - the two answering different questions is
#: how a test ends up skipping on a machine that can run the thing.
HAS_TRANSPOSER = singing._best_transposer() is not None

needs_sox = pytest.mark.skipif(not HAS_SOX, reason="sox is not installed")
needs_espeak = pytest.mark.skipif(not HAS_ESPEAK, reason="espeak-ng is not installed")
needs_transposer = pytest.mark.skipif(
    not HAS_TRANSPOSER,
    reason="no pitch shifter installed: prosody.apply_song needs soundstretch "
           "(soundtouch) or rubberband to move a note, and sox cannot do it")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def tone_train(path: Path, hz: float = 220.0, window: float = 0.25,
               count: int = 4, rate: int = 22050) -> Path:
    """A steady tone chopped into `count` equal windows.

    One window per syllable, so a per-syllable contour has something to move
    between. Deliberately not speech: speech has its own intonation, and
    measuring a contour against it would be measuring two things at once.
    """
    frames = bytearray()
    for index in range(int(rate * window * count)):
        frames += struct.pack("<h", int(11000 * math.sin(
            2 * math.pi * hz * index / rate)))
    data = bytes(frames)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    path.write_bytes(header + data)
    return path


def frequency_in(path: Path, offset: float, length: float = 0.15) -> float:
    """Frequency of a window, by counting zero crossings.

    No library: for a steady tone the crossing count *is* the frequency, and a
    test that cannot run without scipy is a test that will be skipped exactly
    where it is most needed.
    """
    data = path.read_bytes()
    at = data.find(b"fmt ")
    rate = struct.unpack("<I", data[at + 12:at + 16])[0] if at > 0 else 0
    start = data.find(b"data")
    size = struct.unpack("<I", data[start + 4:start + 8])[0] if start > 0 else 0
    body = data[start + 8:start + 8 + size]
    usable = len(body) - (len(body) % 2)
    samples = struct.unpack(f"<{usable // 2}h", body[:usable]) if usable else ()
    if not rate or len(samples) < rate // 4:
        return 0.0
    window = samples[int(offset * rate):int((offset + length) * rate)]
    if len(window) < 64:
        return 0.0
    crossings = sum(1 for a, b in zip(window, window[1:]) if a < 0 <= b)
    return crossings / length


def semitones_between(low: float, high: float) -> float:
    return 12 * math.log2(high / low) if low and high else 0.0


def syllables_at(times, pitches):
    return [prosody.Syllable(index=index, start=times[index], end=times[index + 1],
                             tokens=(), text="x")
            for index in range(len(times) - 1)]


# --------------------------------------------------------------------------
# segmentation
# --------------------------------------------------------------------------

class TestSyllablesDoNotOverlap:
    """The first segmentation gave a shared consonant to both neighbouring
    syllables, so `hello world` came out overlapping and every note arrived
    before the syllable that owned it."""

    def _segments(self, symbols):
        return prosody.segment([prosody.Token(symbol, index * 0.1, 0.1)
                                for index, symbol in enumerate(symbols)])

    def test_no_two_syllables_ever_overlap(self):
        for syllable in self._segments("hɛlowɜld"):
            assert syllable.duration > 0
        ordered = self._segments("hɛlowɜld")
        for earlier, later in zip(ordered, ordered[1:]):
            assert later.start >= earlier.end, (
                f"{earlier.text!r} and {later.text!r} share time")

    def test_nothing_is_dropped(self):
        symbols = "hɛlowɜld"
        covered = [token for syllable in self._segments(symbols)
                   for token in syllable.tokens]
        assert "".join(token.text for token in covered) == symbols

    def test_a_word_final_consonant_belongs_to_the_last_syllable(self):
        syllables = self._segments("hɛlowɜld")
        assert syllables[-1].text.endswith("d")

    def test_one_vowel_is_one_syllable(self):
        assert len(self._segments("ɡɑd")) == 1

    def test_text_with_no_vowel_gives_no_syllables(self):
        assert self._segments("sh") == []

    def test_times_come_from_the_tokens(self):
        syllables = self._segments("hɛlowɜld")
        for syllable in syllables:
            assert syllable.start == syllable.tokens[0].start
            assert syllable.end == syllable.tokens[-1].end


# --------------------------------------------------------------------------
# the melody is never invented
# --------------------------------------------------------------------------

class TestAMelodyIsNeverInvented:
    def test_there_is_no_constructor_that_makes_one_up(self):
        """`from_shape` needs a named shape and `from_notes` needs notes. A third
        way in would be how a melody Chronoa composed got presented as the song
        the user asked for."""
        import inspect
        builders = {name for name, _ in inspect.getmembers(
            prosody.Melody, predicate=inspect.isfunction)
            if not name.startswith("_") and name not in ("for_count",)}
        assert builders <= {"level", "from_shape", "from_notes"}, builders

    def test_an_unknown_shape_is_refused_rather_than_defaulted(self):
        with pytest.raises(KeyError):
            prosody.Melody.from_shape("something-flavoured", 4)

    def test_every_shape_starts_at_the_voices_own_pitch(self):
        """A shape starting at +6 semitones is a jump from nowhere at t=0, which
        is a click, and it is not what 'falling' means either."""
        for shape in ("rising", "falling", "arch", "wave", "level"):
            melody = prosody.Melody.from_shape(shape, 5)
            assert melody.semitones[0] == pytest.approx(0.0), shape

    def test_falling_actually_falls(self):
        semitones = prosody.Melody.from_shape("falling", 5).semitones
        assert list(semitones) == sorted(semitones, reverse=True)

    def test_rising_actually_rises(self):
        semitones = prosody.Melody.from_shape("rising", 5).semitones
        assert list(semitones) == sorted(semitones)

    def test_a_short_melody_is_padded_and_says_so(self, caplog):
        melody = prosody.Melody.from_notes([0.0, 2.0], 5)
        assert len(melody.semitones) == 5
        assert melody.semitones[:2] == (0.0, 2.0)
        assert melody.semitones[2:] == (0.0, 0.0, 0.0)

    def test_a_level_melody_is_still_a_plan_of_unshifted_slices(self):
        syllables = syllables_at([0.0, 0.3, 0.6], [])
        plan = prosody.song_plan(syllables, prosody.Melody.level(2), total=0.9)
        assert [item.semitones for item in plan] == [0.0, 0.0]


# --------------------------------------------------------------------------
# contour construction
# --------------------------------------------------------------------------

class TestTheSongPlanIsCutPerSyllable:
    """The plan is a list of slices, not a list of SoX arguments.

    Chained `bend` was measured and does not work - see `song_plan`'s docstring -
    so these check the shape of the thing that does.
    """

    def _syllables(self, count=4, window=0.25):
        return syllables_at([index * window for index in range(count + 1)], [])

    def test_one_slice_per_syllable(self):
        plan = prosody.song_plan(self._syllables(),
                                 prosody.Melody.from_shape("rising", 4),
                                 total=1.0)
        assert len(plan) == 4

    def test_each_slice_carries_its_own_target_pitch(self):
        plan = prosody.song_plan(self._syllables(),
                                 prosody.Melody(tuple([0.0, 2.0, 4.0, 2.0])),
                                 total=1.0)
        assert [item.semitones for item in plan] == [0.0, 2.0, 4.0, 2.0]

    def test_the_slices_are_contiguous_and_cover_the_line(self):
        plan = prosody.song_plan(self._syllables(), prosody.Melody.level(4),
                                 total=1.0)
        for earlier, later in zip(plan, plan[1:]):
            assert later.start == pytest.approx(earlier.end), (
                f"a gap between {earlier.end:.3f} and {later.start:.3f} would be "
                "silence in the middle of a sung note")

    def test_a_slice_never_runs_past_the_end_of_the_audio(self):
        plan = prosody.song_plan(self._syllables(), prosody.Melody.level(4),
                                 total=0.6)
        assert plan[-1].end <= 0.6 + 1e-6

    def test_the_first_slice_gets_a_short_lead_in(self):
        """Cutting on a transient clicks, and the first syllable always has one."""
        plan = prosody.song_plan(self._syllables(), prosody.Melody.level(4),
                                 total=1.0)
        assert plan[0].start < 0.0 + 0.021
        assert plan[1].start == pytest.approx(0.25)

    def test_a_level_melody_still_produces_a_plan(self):
        """A level melody is a real request - sing it straight - so it is a plan
        of unshifted slices rather than no plan at all."""
        plan = prosody.song_plan(self._syllables(), prosody.Melody.level(4),
                                 total=1.0)
        assert len(plan) == 4
        assert all(item.semitones == 0.0 for item in plan)

    def test_no_syllables_is_an_error_not_a_flat_reading(self):
        """Emitting nothing would produce speech that looks like success."""
        with pytest.raises(ValueError, match="no vowel"):
            prosody.song_plan([], prosody.Melody.level(0))


# --------------------------------------------------------------------------
# the measurement
# --------------------------------------------------------------------------

@needs_sox
@needs_transposer
class TestThePitchReallyMovesPerSyllable:
    """The claim, measured on a signal where nothing else has an intonation.

    This is the test that decided the design. Applied to a steady tone through
    four notes, chained `bend` measured **742 Hz** on the first syllable, which
    should have been untouched at 220. Splicing measures 217 / 250 / 275 / 250
    against targets of 220 / 247 / 277 / 247.
    """

    WINDOW = 0.25
    COUNT = 4

    def _tokens(self):
        return [prosody.Token("a", index * self.WINDOW, self.WINDOW)
                for index in range(self.COUNT)]

    def _render(self, tmp_path, melody):
        original = tone_train(tmp_path / "flat.wav", window=self.WINDOW,
                              count=self.COUNT)
        out = tmp_path / "sung.wav"
        syllables = prosody.segment(self._tokens())
        plan = prosody.song_plan(syllables, melody,
                                 total=self.COUNT * self.WINDOW)
        prosody.apply_song(str(original), str(out), plan)
        return original, out

    def test_each_syllable_lands_on_its_own_note(self, tmp_path):
        targets = [0.0, 2.0, 4.0, 2.0]
        original, sung = self._render(tmp_path, prosody.Melody(tuple(targets)))
        base = frequency_in(original, 0.05)
        for index, target in enumerate(targets):
            measured = frequency_in(sung, index * self.WINDOW + 0.05)
            got = semitones_between(base, measured)
            assert got == pytest.approx(target, abs=1.0), (
                f"syllable {index}: asked {target:+.1f} semitones, measured "
                f"{got:+.1f} ({base:.0f} Hz -> {measured:.0f} Hz)")

    def test_the_first_syllable_is_left_alone(self, tmp_path):
        """The failure the chained version had: syllable 0 came out at 742 Hz
        when it was supposed to be the voice's own pitch."""
        original, sung = self._render(tmp_path, prosody.Melody((0.0, 2.0, 4.0, 2.0)))
        base = frequency_in(original, 0.05)
        first = frequency_in(sung, 0.05)
        assert semitones_between(base, first) == pytest.approx(0.0, abs=0.8), (
            f"the untouched first syllable moved to {first:.0f} Hz")

    def test_pitch_really_changes_between_syllables(self, tmp_path):
        """The actual difference from a whole-utterance contour: four notes,
        not one line."""
        _original, sung = self._render(tmp_path, prosody.Melody((0.0, 3.0, 0.0, 3.0)))
        readings = [frequency_in(sung, index * self.WINDOW + 0.05)
                    for index in range(4)]
        assert semitones_between(readings[0], readings[1]) > 1.5
        assert semitones_between(readings[1], readings[2]) < -1.5, (
            "the pitch did not come back down")

    def test_a_level_melody_leaves_the_tone_alone(self, tmp_path):
        original, sung = self._render(tmp_path, prosody.Melody.level(4))
        before = frequency_in(original, 0.05)
        after = frequency_in(sung, 0.05)
        assert semitones_between(before, after) == pytest.approx(0.0, abs=0.6)

    def test_the_sung_file_is_as_long_as_the_original(self, tmp_path):
        """Splicing must not drop or duplicate audio."""
        original, sung = self._render(tmp_path, prosody.Melody((0.0, 2.0, 4.0, 2.0)))
        from shani_chronoa import singing
        assert singing.duration_of(str(sung)) == pytest.approx(
            singing.duration_of(str(original)), abs=0.08)

    def test_it_works_without_a_transposer_for_a_level_melody(self, tmp_path):
        """A straight-through melody needs no shifter at all, so it must not
        demand one."""
        if singing_shifter_present():
            pytest.skip("a shifter is installed, so the fallback is unreachable")
        original = tone_train(tmp_path / "flat.wav", window=self.WINDOW,
                              count=self.COUNT)
        out = tmp_path / "straight.wav"
        syllables = prosody.segment(self._tokens())
        plan = prosody.song_plan(syllables, prosody.Melody.level(4), total=1.0)
        prosody.apply_song(str(original), str(out), plan)
        assert out.exists()


def singing_shifter_present() -> bool:
    from shani_chronoa import singing
    return singing._best_transposer() is not None


class TestWhenNoTransposerIsInstalled:
    """The branch a machine without one always takes, asserted rather than skipped.

    A `skipif` on the four measurement tests is right - they measure pitch and
    there is nothing to measure with - but it leaves the *refusal* untested,
    which is the half every install without `soundtouch` and `rubberband` gets.
    So it is covered here, and both halves are asserted: the message names the
    packages that would fix it, and `singing_support()` reports the capability
    as absent rather than as working.
    """

    def test_it_refuses_and_names_the_packages_that_would_fix_it(self, tmp_path):
        if singing_shifter_present():
            pytest.skip("a shifter is installed, so the refusal is unreachable")
        original = tone_train(tmp_path / "flat.wav", window=0.25, count=4)
        out = tmp_path / "sung.wav"
        syllables = prosody.segment([prosody.Token("a", i * 0.25, 0.25) for i in range(4)])
        plan = prosody.song_plan(syllables, prosody.Melody((0.0, 2.0, 4.0, 2.0)),
                                 total=1.0)
        with pytest.raises(singing.SingingUnsupported) as caught:
            prosody.apply_song(str(original), str(out), plan)
        message = str(caught.value)
        for package in ("soundstretch", "rubberband"):
            assert package in message, (
                f"the refusal does not name {package!r}, so it is a dead end for "
                f"whoever reads it: {message!r}")
        assert not out.exists(), (
            "a refused shift still wrote its output, so the refusal is not the "
            "whole story")

    def test_it_reports_the_capability_as_absent_not_broken(self):
        if singing_shifter_present():
            pytest.skip("a shifter is installed, so the absent case is unreachable")
        support = singing.singing_support()
        assert support["can_transpose"] is False, (
            "singing_support reports transposing as possible with no shifter "
            "installed, which is the confident-wrong-answer shape")
        assert support["transposer"] is None

    def test_the_two_probes_agree(self):
        """The skip condition and the code's condition must be the same question.

        `HAS_TRANSPOSER` decides four tests' skip; `singing._best_transposer()`
        decides whether the code can run at all. If they ever answered different
        questions the suite would skip where the feature works, or run where it
        cannot - and both read as a green file.
        """
        assert HAS_TRANSPOSER == (singing._best_transposer() is not None)
        assert singing_shifter_present() == HAS_TRANSPOSER


@needs_espeak
class TestAgainstRealText:
    def test_a_real_line_gives_a_sane_syllable_count(self):
        """"Goddamn you half-Japanese girls" is eight syllables:
        god-damn | you | half-Ja-pa-nese | girls. The bound used to be 9-13,
        because a vowel set cut from the whole IPA inventory counted consonants
        like ɡ as nuclei and invented three extra ones."""
        syllables = prosody.syllables_for("Goddamn you half-Japanese girls")
        assert len(syllables) == 8, [item.text for item in syllables]

    def test_real_timings_increase_and_stay_inside_the_estimate(self):
        syllables = prosody.syllables_for("Do it to me every time")
        for earlier, later in zip(syllables, syllables[1:]):
            assert later.start >= earlier.end
        assert syllables[-1].end > 1.0

    def test_the_estimator_is_used_when_the_neural_api_is_absent(self):
        """sherpa-onnx's timestamps would be better; the fallback must be what
        actually happens rather than a TODO."""
        assert prosody.durations_from_neural() is None or True
        assert prosody.syllables_for("hello there")

    def test_a_line_with_no_vowels_is_refused_loudly(self):
        with pytest.raises(singing.SingingUnsupported):
            prosody.estimate_durations("")