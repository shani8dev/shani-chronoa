"""`fm_radio` action='decode' raised `TypeError` on every real capture.

Measured here, by calling `run()` on a real stereo 16-bit IQ capture — 0.1 s,
250 kS/s:

    run() RAISED TypeError: can only concatenate tuple (not "float") to tuple

**Three wrong-shape assumptions in one block**, none of them caught:

| line | assumption | reality |
|---|---|---|
| `max((abs(v) for v in audio))` | `audio` is a flat list of floats | `demodulate` returns `list[tuple[float, float]]` — stereo (left, right) pairs |
| `dominant_tone(audio, rate)` | as above, plus `rate` is the audio rate | it takes `list[float]`, and the audio is at `AUDIO_RATE` (32 kHz), not the capture's rate |
| `len(audio) / rate` | `rate` is the audio rate | it is the **IQ** rate — 250 kHz |

So the two type errors, and a duration **7.8× short**: 3200 audio samples over a
250 kHz IQ rate reads as 0.01 s for a clip that is 0.10 s.

Only `read_iq_wav` was wrapped in a `try`. The rest of the block was not, so the
exception **escaped `run()`** entirely. The skill's own description advertises
*"'decode' to demodulate a recorded IQ capture"*, and nothing in the suite covered
it.

## The audio was thrown away even when it worked

`write_wav` — *"16-bit mono WAV, the shape every player here takes"* — sat in
this module with no caller anywhere in the tree. A person who demodulated a
recording got `Peak audio level: 0.412` and no way to hear it. Demodulating is
the expensive part; discarding the result made the action a dead end.

It is now written beside the capture (`.audio.wav`), because `files.py` has **no**
"where Chronoa puts files for you" helper — inventing a directory convention here
would be one nothing else follows. `out=` overrides it; `out='-'` writes nothing,
which is the honest answer for someone who only wanted the numbers.
"""

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import fm_radio as F  # noqa: E402


@pytest.fixture(autouse=True)
def _radio_on(monkeypatch):
    """The consent gate, so the decode block is reached at all."""
    monkeypatch.setattr(F, "_consent", lambda config: (True, ""))


def _capture(directory, rate=250_000, seconds=0.10, tone_hz=1000):
    """A stereo 16-bit IQ WAV: real in the left channel, imaginary in the right."""
    path = Path(directory) / "cap.iq.wav"
    payload = bytearray()
    for n in range(int(rate * seconds)):
        payload += struct.pack(
            "<hh",
            int(20000 * math.sin(2 * math.pi * tone_hz * n / rate)),
            int(20000 * math.cos(2 * math.pi * tone_hz * n / rate)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(payload))
    return path


def test_decode_does_not_raise(tmp_path):
    """**The defect.** Before this, this raised `TypeError` out of `run()`."""
    said = F.run({"action": "decode", "file": str(_capture(tmp_path))})

    assert said, "decode returned nothing"


def test_the_duration_is_the_audio_not_the_capture_rate(tmp_path):
    """**7.8x short before.** 3200 samples at 32 kHz is 0.10 s; dividing by the
    250 kHz IQ rate said 0.01 s."""
    said = F.run({"action": "decode", "file": str(_capture(tmp_path, seconds=0.10))})

    assert f"{0.10:.2f}s" in said, said
    assert "0.01s" not in said, (
        f"the duration is still computed from the IQ rate: {said!r}")
    assert f"{F.AUDIO_RATE:,} Hz" in said, (
        f"the answer does not name the rate the audio is actually at: {said!r}")


def test_the_audio_is_written_and_can_be_played(tmp_path):
    """**The orphan.** `write_wav` had no caller; the demodulated audio was
    discarded."""
    capture = _capture(tmp_path)
    said = F.run({"action": "decode", "file": str(capture)})

    written = Path(str(capture)[:-len(".iq.wav")] + ".iq.audio.wav")
    assert "Wrote" in said, said
    assert written.exists(), f"nothing was written; the answer said: {said!r}"

    with wave.open(str(written)) as handle:
        assert handle.getnchannels() == 1, "not mono, which is what write_wav writes"
        assert handle.getsampwidth() == 2, "not 16-bit"
        assert handle.getframerate() == F.AUDIO_RATE, handle.getframerate()
        assert abs(handle.getnframes() / handle.getframerate() - 0.10) < 0.01
        data = handle.readframes(handle.getnframes())

    samples = struct.unpack("<%dh" % (len(data) // 2), data)
    assert any(samples), "the file is digital silence - the audio was not in it"


def _fm_capture(directory, hz=1000.0, seconds=0.5):
    """A **real** FM capture carrying a known audio tone.

    `synthesize_fm` exists so the demodulator is verifiable with no radio: the
    audio that goes in is known, so the audio that comes out can be compared
    against it. A hand-rolled IQ tone is not the same thing - it is a tone in the
    *baseband*, which is not an audio tone once discriminated, and decoding one
    reports no dominant tone for entirely correct reasons.
    """
    rate = F.IQ_RATE
    n = int(rate * seconds)
    tone = [0.4 * math.sin(2 * math.pi * hz * k / rate) for k in range(n)]
    iq = F.synthesize_fm(tone, [0.0] * n, rate)
    path = Path(directory) / "station.iq.wav"
    payload = bytearray()
    for sample in iq:
        payload += struct.pack("<hh",
                               max(-32768, min(32767, int(sample.real * 20000))),
                               max(-32768, min(32767, int(sample.imag * 20000))))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(payload))
    return path


def test_the_tone_is_reported_at_the_audio_rate(tmp_path):
    """**The control a surviving mutation exposed.** Reverting
    `dominant_tone(mono, AUDIO_RATE)` to `dominant_tone(mono, rate)` still passed
    every other assertion here, because nothing pinned the *frequency* - only the
    duration. Analysing the audio at 250 kHz instead of 32 kHz reports the same
    tone at 7.8x the frequency, which is a confidently wrong answer rather than an
    obvious failure.
    """
    said = F.run({"action": "decode", "file": str(_fm_capture(tmp_path, hz=1000.0))})

    assert "dominant at about" in said, (
        f"the synthesised station did not demodulate to a detectable tone: {said!r}")
    assert "Hz" in said
    import re

    # Measured once: a 1000 Hz tone analysed at the 250 kHz IQ rate instead of
    # 32 kHz comes back at 7812 Hz. So the margin here is not decorative - it is
    # the difference between the right answer and one that is still confidently
    # wrong. (A coarser "about 1000 not about 2000" check flagged the correct
    # answer as wrong, because 1000 * 1 is 1000.)
    reported = int(re.search(r"about (\d+) Hz", said).group(1))
    assert abs(reported - 1000) <= 50, (
        f"a 1000 Hz tone came back as {reported} Hz; the analysis rate is wrong")


def test_out_minus_writes_nothing(tmp_path):
    """Someone who wants the numbers should not get a file as well."""
    capture = _capture(tmp_path)
    said = F.run({"action": "decode", "file": str(capture), "out": "-"})

    assert "Wrote" not in said, said
    # **Scoped to audio files, not to the whole directory.** The first version
    # asserted the directory held exactly `["cap.iq.wav"]` and failed on a `home/`
    # directory the harness itself had created - asserting about the harness, not
    # about this code.
    written = [p.name for p in Path(tmp_path).iterdir()
               if p.suffix == ".wav" and p.name != "cap.iq.wav"]
    assert written == [], f"out='-' still wrote {written}"


def test_out_names_the_destination(tmp_path):
    capture = _capture(tmp_path)
    target = Path(tmp_path) / "elsewhere.wav"

    said = F.run({"action": "decode", "file": str(capture), "out": str(target)})

    assert target.exists(), said
    assert str(target) in said, said


def test_a_mix_not_a_channel_pick(tmp_path):
    """FM stereo puts a 38 kHz subcarrier in the difference signal, so picking one
    channel discards half of what was received for no reason a caller can see."""
    assert F._mix(1.0, -1.0) == pytest.approx(0.0)
    assert F._mix(0.5, 0.5) == pytest.approx(0.5)
    assert F._mix(0.0, 1.0) == pytest.approx(0.5)


def test_a_capture_too_short_is_still_a_sentence(tmp_path):
    path = Path(tmp_path) / "tiny.iq.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(250_000)
        handle.writeframes(b"\x00\x00\x00\x00" * 2)

    said = F.run({"action": "decode", "file": str(path)})

    assert "too few" in said, said


def test_write_wav_now_has_a_caller():
    """The smallest claim that was false."""
    source = (ROOT / "usr/lib/shani-chronoa/shani_chronoa/skills/fm_radio.py").read_text(
        encoding="utf-8")
    assert "write_wav(target" in source, (
        "fm_radio.write_wav is unreached again; the decoded audio would be "
        "discarded, which is the dead end this file exists to close")
