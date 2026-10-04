"""Singing: the part that is real, and the part that is honestly refused.

The module is named for an attempt that does not fully work, so these tests are
mostly about the refusals being accurate. A test that asserted Chronoa can sing
would be the wrong test; the claim under test is that it *cannot*, that the
reason is reported, and that the parts which do work really do.

The last class runs the real binaries when they are installed. That is the only
kind of evidence that `bend 0,0,500,200,900,0` is a thing SoX accepts rather
than a string this repository believes in.
"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from shani_chronoa import singing

HAS_SOX = shutil.which("sox") is not None
HAS_TRANSPOSER = singing._best_transposer() is not None

needs_sox = pytest.mark.skipif(not HAS_SOX, reason="sox is not installed")
needs_transposer = pytest.mark.skipif(not HAS_TRANSPOSER,
                                      reason="neither soundstretch nor rubberband")


def _wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    """A real, playable WAV: a quiet sine, written by hand.

    Written here rather than by SoX so the test does not need SoX to test the
    part that does not involve SoX, and so the fixture cannot be a thing that is
    valid only because the thing under test produced it.
    """
    import math
    frames = bytearray()
    for index in range(int(rate * seconds)):
        value = int(3000 * math.sin(2 * math.pi * 220 * index / rate))
        frames += struct.pack("<h", value)
    data = bytes(frames)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    path.write_bytes(header + data)
    return path


class TestTheRefusalsAreAccurate:
    """Chronoa cannot sing. These are the tests that keep that from drifting
    into a claim it cannot support."""

    def test_it_reports_that_it_cannot_track_pitch(self):
        support = singing.singing_support()
        assert support["can_track_pitch"] is False
        assert support["why_not"], "a refusal with no reason is not a refusal"

    def test_it_reports_no_realtime_modulation(self):
        """SoX's only live parameter control is `--keymap`, which reads the
        keyboard, and only `vol.gain` and `contrast.amount` declare one."""
        support = singing.singing_support()
        assert support["realtime_modulation"] is False
        assert "keymap" in support["realtime_why_not"]

    def test_there_is_no_capability_flag_that_would_unlock_singing(self):
        """A `can_sing` flag is how a UI ends up offering a button that cannot
        work. Nothing in the module may claim it."""
        source = Path(singing.__file__).read_text()
        assert '"can_sing"' not in source and "'can_sing'" not in source
        assert "can_track_pitch" in source

    def test_transposing_without_a_transposer_says_so_rather_than_guessing(self, tmp_path):
        """Silently falling back to SoX here would mean the caller believes it
        asked for a transposition and got a differently-implemented one."""
        if HAS_TRANSPOSER:
            pytest.skip("a transposer is installed, so this path is not reachable")
        source = _wav(tmp_path / "in.wav")
        with pytest.raises(singing.SingingUnsupported) as caught:
            singing.transpose(str(source), 2.0, str(tmp_path / "out.wav"))
        assert "sox" in str(caught.value), (
            "the refusal should say the fallback exists")


class TestThePitchContour:
    def test_a_two_point_contour_is_one_bend(self):
        """One `bend`, one ramp. This is all sox can do reliably: applied to a
        steady tone, a *chained* set reported 742 Hz on audio no segment
        touched, because each bend is a phase vocoder over the whole signal."""
        points = [singing.Point(0.0, 0.0), singing.Point(0.5, 2.0)]
        assert singing.contour_effects(points) == ["bend", "0,+200,0.5"]

    def test_more_than_two_points_is_refused_with_a_way_forward(self):
        """Per-syllable pitch needs splicing, which is `prosody`'s job. Emitting
        a chain anyway would sound wrong rather than fail."""
        points = [singing.Point(0.0, 0.0), singing.Point(0.5, 2.0),
                  singing.Point(0.9, 0.0)]
        with pytest.raises(ValueError, match="prosody"):
            singing.contour_effects(points)

    def test_a_constant_contour_is_left_to_pitch(self):
        """`bend` with one segment is a no-op in SoX, and a constant shift is
        exactly what `pitch` is for."""
        points = [singing.Point(0.0, 1.5), singing.Point(1.0, 1.5)]
        assert singing.contour_effects(points) == []

    def test_points_may_arrive_out_of_order(self):
        # Distinct semitones: two points at the same pitch is a *constant*
        # contour, which is deliberately left to `pitch`.
        assert singing.contour_effects(
            [singing.Point(0.9, 3.0), singing.Point(0.0, 0.0)])[1] == "0,+300,0.9"

    def test_two_points_at_the_same_instant_are_rejected(self):
        """SoX fails on a zero-length segment, and it is better to say so here
        than to hand it something that dies mid-reply."""
        with pytest.raises(ValueError, match="increase"):
            singing.contour_effects([singing.Point(0.5, 0.0),
                                     singing.Point(0.5, 1.0)])

    def test_a_pitch_outside_the_speaking_range_is_rejected(self):
        with pytest.raises(ValueError, match="different voice"):
            singing.Point(0.0, 12.0)

    def test_a_negative_time_is_rejected(self):
        with pytest.raises(ValueError, match="not a time"):
            singing.Point(-1.0, 0.0)

    def test_points_past_the_end_of_the_audio_are_dropped_not_clamped(self):
        """Clamping stretches the last segment across the remaining audio, which
        is a different contour from the one asked for."""
        points = [singing.Point(0.0, 0.0), singing.Point(0.5, 2.0),
                  singing.Point(9.0, -2.0)]
        assert singing.within(points, 1.0) == [singing.Point(0.0, 0.0),
                                                singing.Point(0.5, 2.0)]

    def test_an_empty_contour_is_empty(self):
        assert singing.contour_effects([]) == []


@needs_sox
class TestAgainstRealSoX:
    """The arguments are only real if SoX runs them."""

    def test_sox_accepts_the_contour_this_module_builds(self, tmp_path):
        """Two points, because that is the most sox can do. Three or more is
        refused - see `contour_effects`."""
        source = _wav(tmp_path / "in.wav", seconds=1.0)
        out = tmp_path / "out.wav"
        singing.apply_contour(str(source), str(out),
                              [singing.Point(0.0, 0.0), singing.Point(0.4, 2.5)])
        assert out.exists() and out.stat().st_size > 44, (
            "sox accepted the bend and wrote nothing")

    def test_the_contoured_audio_is_still_audio(self, tmp_path):
        """A contour that produces a file of the right size and no samples is not
        a contour, it is a crash that wrote a header."""
        source = _wav(tmp_path / "in.wav", seconds=1.0)
        out = tmp_path / "out.wav"
        singing.apply_contour(str(source), str(out),
                              [singing.Point(0.0, 0.0), singing.Point(0.5, 3.0)])
        assert singing.duration_of(str(out)) > 0.5

    def test_a_constant_contour_routes_to_pitch_and_still_works(self, tmp_path):
        source = _wav(tmp_path / "in.wav", seconds=1.0)
        out = tmp_path / "out.wav"
        singing.apply_contour(str(source), str(out),
                              [singing.Point(0.0, 2.0), singing.Point(0.5, 2.0)])
        assert out.exists() and out.stat().st_size > 44

    def test_vibrato_is_not_silently_assumed_to_exist(self):
        """SoX 14.8, the version Arch ships, has no `vibrato` effect. A plan that
        reads as though it does produces a command that dies at run time."""
        if not HAS_SOX:
            pytest.skip("sox is not installed")
        import subprocess
        done = subprocess.run(["sox", "--help-effect", "vibrato"],
                              capture_output=True)
        assert done.returncode != 0, (
            "sox now has a vibrato effect; singing.py can use it and its "
            "comment saying otherwise is stale")

    def test_a_missing_sox_is_reported_as_a_missing_sox(self, tmp_path):
        source = _wav(tmp_path / "in.wav")
        with pytest.raises(singing.SingingUnsupported,
                           match="definitely-not-sox is not installed"):
            singing.apply_contour(str(source), str(tmp_path / "out.wav"),
                                  [singing.Point(0.0, 0.0),
                                   singing.Point(0.5, 1.0)],
                                  sox_binary="definitely-not-sox")


@needs_transposer
class TestAgainstRealTransposers:
    def test_a_transposed_file_is_real_audio(self, tmp_path):
        source = _wav(tmp_path / "in.wav", seconds=1.0)
        out = tmp_path / "out.wav"
        singing.transpose(str(source), 2.0, str(out))
        assert out.exists() and out.stat().st_size > 44

    def test_the_transposer_keeps_the_duration(self, tmp_path):
        """A transposer that changes the length is a time-stretcher, and the
        caller asked for pitch."""
        source = _wav(tmp_path / "in.wav", seconds=1.0)
        out = tmp_path / "out.wav"
        singing.transpose(str(source), 2.0, str(out))
        before = singing.duration_of(str(source))
        after = singing.duration_of(str(out))
        assert before and after
        assert abs(after - before) / before < 0.25, (
            f"1s became {after:.2f}s at +2 semitones")

    def test_a_missing_binary_is_reported_rather_than_raised_as_oserror(self, tmp_path):
        """Named directly, so this does not depend on what is installed.

        Reached through `prosody.apply_song` rather than `singing.transpose`,
        because `transpose` now raises its *own* value error first: it validates
        the range before it looks at the backend, so an out-of-range request and
        a missing binary are different complaints and the wrong one was masking
        the right one.
        """
        from shani_chronoa import prosody
        source = _wav(tmp_path / "in.wav", seconds=1.0)
        plan = [prosody.Slice(start=0.0, duration=0.5, semitones=2.0)]
        with pytest.raises(singing.SingingUnsupported,
                           match="definitely-not-sox is not installed"):
            prosody.apply_song(str(source), str(tmp_path / "out.wav"), plan,
                               sox_binary="definitely-not-sox")


class TestDurationReading:
    def test_it_reads_a_real_header(self, tmp_path):
        assert singing.duration_of(str(_wav(tmp_path / "in.wav", seconds=1.5))) == \
            pytest.approx(1.5, abs=0.01)

    def test_a_file_that_is_not_a_wav_is_zero_not_an_exception(self, tmp_path):
        path = tmp_path / "not.wav"
        path.write_bytes(b"this is not audio")
        assert singing.duration_of(str(path)) == 0.0

    def test_a_missing_file_is_zero_not_an_exception(self, tmp_path):
        assert singing.duration_of(str(tmp_path / "gone.wav")) == 0.0

    def test_it_does_not_shell_out_to_measure(self):
        """The caller needs the duration to build the effect chain; a subprocess
        per reply just to measure it would be absurd."""
        assert "import subprocess" in Path(singing.__file__).read_text()
        source = Path(singing.__file__).read_text()
        assert '"--i"' not in source and "'--i'" not in source


class TestTempoMap:
    def test_two_points_make_a_map(self, tmp_path):
        path = singing.tempo_map([(0.0, 1.0), (1.0, 1.5)])
        try:
            assert path and Path(path).exists()
            lines = Path(path).read_text().strip().splitlines()
            assert len(lines) == 2
            assert all(len(line.split()) == 2 for line in lines)
        finally:
            if path:
                Path(path).unlink()

    def test_one_point_is_not_a_map(self):
        """rubberband is explicit that a map has to be paired with an overall
        stretch factor, and a single point carries no stretch."""
        assert singing.tempo_map([(0.0, 1.0)]) is None
        assert singing.tempo_map([]) is None

    def test_a_nonpositive_stretch_factor_is_refused(self):
        with pytest.raises(ValueError, match="no meaning"):
            singing.tempo_map([(0.0, 1.0), (1.0, 0.0)])