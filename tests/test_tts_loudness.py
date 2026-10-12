"""One loudness for every voice engine, measured and then enforced.

Measured 2026-10-12 through this repo's own `cache/data-home`, one sentence per
engine via `synthesize_to_bytes`:

    espeak-ng  -19.89 dBFS RMS     piper  -18.86     kokoro  -24.69

A 5.83 dB spread against a just-noticeable-difference of about 1 dB, and `piper`
arriving at -0.00 dBFS peak - full scale, no headroom for a voice style's EQ to
lift. Both are the axis PersonaPlex normalises for (`normalize_audio(..., -24.0)`
on every voice prompt) and neither had a Chronoa equivalent.

Split out of `test_voice_loop_live.py` because a mutation set runs one whole file,
and that file is full of live-PipeWire and whisper-transcription measurements that
take minutes each - so five mutations against it exceeded the runner's budget and
reported nothing. The engine-free half here runs in well under a second.

The engine half needs the voices in `cache/data-home`; `tests/conftest.py` redirects
`XDG_DATA_HOME` to a scratch directory, so the cache is symlinked in here exactly as
`in_that_graph` already does, rather than skipping on a box that has them.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "cache" / "data-home"


def _rms_dbfs(path) -> float:
    """RMS of a WAV in dBFS, through the same `sox stat` the pass reads."""
    import math
    import subprocess
    stat = subprocess.run(["sox", str(path), "-n", "stat"], capture_output=True, text=True)
    for line in (stat.stderr or "").splitlines():
        if line.strip().startswith("RMS") and "amplitude" in line:
            value = float(line.split(":", 1)[1].strip())
            return 20 * math.log10(value) if value > 0 else float("-inf")
    raise AssertionError(f"sox reported no RMS for {path}")


def _peak_dbfs(path) -> float:
    """Peak of a WAV in dBFS, through the same `sox stat` the pass reads."""
    import math
    import subprocess
    stat = subprocess.run(["sox", str(path), "-n", "stat"], capture_output=True, text=True)
    for line in (stat.stderr or "").splitlines():
        if line.strip().startswith("Maximum") and "amplitude" in line:
            value = float(line.split(":", 1)[1].strip())
            return 20 * math.log10(value) if value > 0 else float("-inf")
    raise AssertionError(f"sox reported no peak for {path}")


def _tone(path, seconds: float = 0.5, rate: int = 22050, gain_db: float = 0.0,
          crest_db: float = 0.0):
    """A WAV at a chosen gain, so a level can be set without an engine.

    `crest_db` adds a sparse pulse train on top of the sine, which is how a signal
    comes to have a peak well above its RMS the way speech does. **It is not
    cosmetic.** A pure sine has peak == RMS, so no peak-based assertion can
    discriminate against anything: the first version of these tests used one and
    three mutations survived because the peak never approached the ceiling.
    """
    import math
    import struct
    import wave
    frames = []
    body = 0.2 * (10 ** (gain_db / 20))
    spikes = []
    n_spikes = int(seconds * 6)          # ~6 speech-like bursts a second
    step = max(1, int(seconds * rate) // max(n_spikes, 1))
    for i in range(n_spikes):
        spikes.append((i * step) % int(seconds * rate))
    spike_amp = body * (10 ** (crest_db / 20))
    for n in range(int(seconds * rate)):
        t = n / rate
        v = math.sin(2 * math.pi * 220 * t) * body
        if n in spikes:
            v += spike_amp * 0.5
        frames.append(int(max(-1.0, min(1.0, v)) * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(frames), *frames))


class TestTheLoudnessPass:
    """Measured 2026-10-12, through this repo's own `cache/data-home`, one sentence
    per engine via `synthesize_to_bytes`:

        espeak-ng  -19.89 dBFS RMS     piper  -18.86     kokoro  -24.69

    A 5.83 dB spread against a just-noticeable-difference of about 1 dB - so
    choosing a voice engine changed perceived loudness by roughly a factor of two,
    and `piper` arrived at -0.00 dBFS peak, i.e. full scale with no headroom for a
    style's EQ to lift. Both are the axis PersonaPlex normalises for
    (`normalize_audio(..., -24.0)` on every voice prompt).

    These are the engine-free half: the pass's own behaviour on files whose level is
    already known, so neither `sox`'s presence nor an installed voice decides whether
    they run.
    """

    @pytest.mark.skipif(not shutil.which("sox"), reason="the pass measures with sox")
    @pytest.mark.parametrize("gain_db", [-30.0, -12.0, -1.0, 0.0, 3.0])
    def test_the_pass_moves_a_file_towards_the_target_never_past_it(
            self, tmp_path, gain_db):
        """The whole pass in one property: whichever side of the target a file
        arrives on, the gain moves it *towards* it, and stops there.

        The first version of this asserted "a quiet file is lifted" for a +3 dB
        input, which is a *loud* file and gets a cut. The direction belongs to the
        measurement, not to the case label, so it is read from the number.
        """
        from shani_chronoa.tts import (LOUDNESS_TOLERANCE_DB, TARGET_RMS_DBFS,
                                       PiperTTS)
        tone = tmp_path / f"tone{gain_db}.wav"
        _tone(tone, gain_db=gain_db)
        before = _rms_dbfs(tone)
        assert PiperTTS().apply_loudness(str(tone)) == ""
        after = _rms_dbfs(tone)
        if abs(before - TARGET_RMS_DBFS) <= LOUDNESS_TOLERANCE_DB:
            # Already inside the deadband: left alone rather than re-encoded for no
            # audible gain.
            assert abs(after - before) < 0.05, (before, after)
        else:
            assert abs(after - TARGET_RMS_DBFS) < abs(before - TARGET_RMS_DBFS), (
                f"{before:.2f} -> {after:.2f} moved away from the target "
                f"{TARGET_RMS_DBFS}")

    @pytest.mark.skipif(not shutil.which("sox"), reason="the pass measures with sox")
    def test_a_lift_stops_at_the_peak_ceiling(self, tmp_path):
        """A quiet file lifted until it clips is worse than one left quiet, and
        clipping is audible on speech in a way a level error is not."""
        from shani_chronoa.tts import PEAK_CEILING_DBFS, TARGET_RMS_DBFS, PiperTTS
        # Quiet enough that the target asks for a lift, with a crest factor big
        # enough that the peak reaches the ceiling before the RMS reaches the
        # target - which is the whole condition the ceiling exists for.
        tone = tmp_path / "quiet.wav"
        _tone(tone, gain_db=-30.0, crest_db=30.0)
        assert PiperTTS().apply_loudness(str(tone)) == ""
        peak = _peak_dbfs(tone)
        assert peak <= PEAK_CEILING_DBFS + 0.5, (peak, PEAK_CEILING_DBFS)

    @pytest.mark.skipif(not shutil.which("sox"), reason="the pass measures with sox")
    def test_a_cut_is_not_held_back_by_the_ceiling(self, tmp_path):
        """The ceiling limits a **lift** only. A cut never raises a peak, so applying
        it to one as well holds an already-loud engine further below target than the
        numbers ask for - which is what the first version of this pass did, measured
        as `piper` landing 1.9 dB under target for no reason the measurement
        explained.
        """
        from shani_chronoa.tts import (LOUDNESS_TOLERANCE_DB, PEAK_CEILING_DBFS,
                                     TARGET_RMS_DBFS, PiperTTS)
        # **The case that distinguishes a cut from a lift, and it is not obvious.**
        # `allowed = ceiling - peak` is positive whenever the peak is under the
        # ceiling, so for a negative gain `gain > allowed` is false and the
        # ceiling has nothing to clamp. The condition that *does* over-clamp is a
        # file whose peak is already **above** the ceiling - `piper`'s real
        # situation, arriving at -0.00 dBFS - together with a cut small enough to
        # fit inside the headroom. Then the buggy version cuts the full headroom
        # instead of the amount the numbers asked for, and the file lands below
        # target. Measured on this input: needed -2.63 dB, clamped -3.00, so the
        # buggy file sits at -20.37 instead of -20.00.
        tone = tmp_path / "loud.wav"
        _tone(tone, gain_db=-0.46, crest_db=20.0)
        before = _rms_dbfs(tone)
        assert before > TARGET_RMS_DBFS, before
        peak_before = _peak_dbfs(tone)
        assert peak_before > PEAK_CEILING_DBFS, \
            f"this input's peak {peak_before:.2f} is not above the ceiling, so it "            f"cannot discriminate: {before:.2f} RMS"
        assert PiperTTS().apply_loudness(str(tone)) == ""
        after = _rms_dbfs(tone)
        # A cut is exactly as big as needed - never bigger, which is the direction
        # a wrongly-applied ceiling pushes it. The bound is tight on purpose: the
        # discriminating gap here is 0.40 dB (-20.00 against -20.40), so the pass's
        # own 1 dB deadband would pass both. 0.25 leaves SoX's 16-bit quantisation
        # room and still sits under the gap.
        assert after >= TARGET_RMS_DBFS - 0.25, \
            f"cut past the target: {before:.2f} -> {after:.2f}, target {TARGET_RMS_DBFS}"

    @pytest.mark.skipif(not shutil.which("sox"), reason="the pass measures with sox")
    def test_a_file_that_cannot_be_measured_returns_a_reason(self, tmp_path):
        """A normaliser must never cost a reply, and must never claim a change it did
        not make."""
        from shani_chronoa.tts import PiperTTS
        # A path sox cannot even be handed: `subprocess.run` raises, which is the
        # `except` branch. A *missing* file is not this - sox answers with a
        # non-zero rc and the method returns before the try is ever entered, so
        # the first version of this test could not discriminate against a mutated
        # except clause.
        reason = PiperTTS().apply_loudness(None)
        assert reason, "an unmeasurable path must say why, not report success"
        assert "not normalised" in reason, reason

    @pytest.mark.parametrize("engine", ["espeak-ng", "piper", "kokoro"])
    def test_every_engine_lands_on_the_target(self, engine, tmp_path, monkeypatch):
        """The half that needs an installed voice, so it skips and says which one is
        absent rather than passing for the wrong reason.

        Run against this repo's own `cache/data-home`: `espeak-ng` is on PATH, and
        `piper`/`kokoro` are installed there by `test_setup_extras`.
        """
        from shani_chronoa import voices
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.tts import LOUDNESS_TOLERANCE_DB, TARGET_RMS_DBFS, PiperTTS
        # The suite redirects XDG_DATA_HOME to a scratch dir, so an engine
        # installed in the repo's git-ignored cache is invisible to it - and a
        # test that skips on a box that has the engine is a test that cannot run,
        # which is the trap this whole class exists to avoid. `in_that_graph`
        # already links the cache in for the measured-engine tests; this does the
        # same for the ones that only need the voice files, without the PipeWire
        # graph this measurement does not use.
        for rel in ("piper", "kokoro", "shani-chronoa/piper",
                    "shani-chronoa/sherpa-onnx"):
            source = CACHE / rel
            if source.exists():
                target = tmp_path / "data" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    os.symlink(source, target)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        if engine == "espeak-ng":
            if not shutil.which("espeak-ng"):
                pytest.skip("espeak-ng is not installed")
        elif engine == "piper":
            if not (voices.piper_binary()
                    and (voices.voice_dir() / "en_US-lessac-medium.onnx").is_file()):
                pytest.skip("piper or its voice is not installed in cache/data-home")
        elif not voices.kokoro_binary() or voices.kokoro_problem(voices.KOKORO_DEFAULT_VOICE):
            pytest.skip(f"kokoro is not installed in cache/data-home: "
                        f"{voices.kokoro_problem(voices.KOKORO_DEFAULT_VOICE)}")
        config = ChronoaConfig()
        tts = PiperTTS(config=config)
        original = PiperTTS._engine
        PiperTTS._engine = lambda self, reason, e=engine: e
        try:
            data = tts.synthesize_to_bytes("The volume is at forty percent right now.")
        finally:
            PiperTTS._engine = original
        assert data[:4] == b"RIFF", f"{engine} produced no speech"
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(data)
            tmp = fh.name
        try:
            level = _rms_dbfs(tmp)
        finally:
            os.unlink(tmp)
        assert abs(level - TARGET_RMS_DBFS) <= LOUDNESS_TOLERANCE_DB, \
            f"{engine} speaks at {level:.2f} dBFS, target {TARGET_RMS_DBFS}"
