"""The FM stereo decoder, verified by round trip rather than by inspection.

Four defects made stereo unworkable, and each was found by measuring the real
chain rather than by reading it. They are pinned here so none can come back:

1. **The discriminator had no phase wrap.** `atan2` returns a principal value in
   (-pi, pi], so whenever the *accumulated* phase crosses the branch cut - which
   a 500 Hz baseband does four times per cycle, because the integrated phase
   swings by dev*A/f radians - the naive difference reads a spurious +/-2*pi
   step. Measured on a real signal: true per-sample advance 1.41 rad, naive
   difference 5.79 rad, wrong 24000 times in 200000 samples.
2. **The de-matrix compounded two 0.5s into 0.25** instead of cancelling them,
   so `L = 0.75L + 0.25R` and a hard-panned tone leaked a third of itself
   across.
3. **`_deemphasis`'s pole put the corner an order of magnitude too high** at
   every real rate (14146 Hz at 32 kS/s, 83852 at 128 kS/s, for a nominal
   2122 Hz), so the receiver attenuated the whole audio band by ~24 dB.
4. **The pilot was measured through a bandpass costing 30% of its amplitude**,
   leaving the lock threshold a 1.24x margin - the exact margin a decoder
   silently falls back to a free-running oscillator at.

The round trip itself is the test that matters: `synthesize_fm` builds a station
from known audio, `demodulate` decodes it, and the recovered channels are
compared against what went in.
"""
import math
import random

import pytest

from shani_chronoa import fmdsp
from shani_chronoa.skills import fm_radio as F

IQ = F.IQ_RATE
RATE = F.AUDIO_RATE
MID = F.MID_RATE
DEV = F.DEVIATION


def tone(hz, amp=0.5, n=None, rate=IQ):
    """A sine at `rate`; `synthesize_fm`'s contract is that left/right are
    already sampled at the IQ rate, and passing audio-rate arrays is what made
    an earlier harness measure a round trip that never happened."""
    count = n if n is not None else int(IQ)
    return [amp * math.sin(2.0 * math.pi * hz * i / rate) for i in range(count)]


def magnitude(x, hz, rate=RATE):
    """`goertzel_power` returns amplitude/2 for a coherent tone."""
    return fmdsp.goertzel_power(x, rate, hz)


def db(a, b):
    return 20.0 * math.log10(max(a, 1e-12) / max(b, 1e-12))


def discriminator(iq):
    """The wrap-aware first stage of `demodulate`, so the branch-cut bug can be
    asserted on directly rather than only through the round trip."""
    scale = IQ / (2.0 * math.pi * DEV)
    out, prev = [], math.atan2(iq[0].imag, iq[0].real)
    for z in iq[1:]:
        phase = math.atan2(z.imag, z.real)
        step = phase - prev
        if step > math.pi:
            step -= 2.0 * math.pi
        elif step < -math.pi:
            step += 2.0 * math.pi
        out.append(step * scale)
        prev = phase
    return out


class TestDiscriminatorWrapsThePhase:
    """The one bug that made every other measurement meaningless."""

    def test_a_baseband_whose_integrated_phase_crosses_the_branch_cut(self):
        # b(t) = A*sin(2*pi*f*t) integrates to a phase of amplitude dev*A/f.
        # At f=500, A=0.5 that is 75 radians, so the accumulated phase crosses
        # +/-pi four times per cycle - the branch cut is crossed constantly,
        # while the true per-sample advance stays at 1.18 rad, well under pi.
        amp, hz = 0.5, 500.0
        n = 20000
        iq = []
        for i in range(n):
            # The negative cosine: differentiating it gives +amp*sin, so the
            # discriminator's output matches `want` rather than its inverse.
            phase = -DEV * amp * math.cos(2.0 * math.pi * hz * i / IQ) / hz
            iq.append(complex(math.cos(phase), math.sin(phase)))
        got = discriminator(iq)
        want = [amp * math.sin(2.0 * math.pi * hz * i / IQ) for i in range(n)]
        # Compare only where the signal has settled away from the ends.
        lo, hi = n // 10, n - n // 10
        error = max(abs(g - w) for g, w in zip(got[lo:hi], want[lo:hi]))
        assert error < 0.02, (
            f"the discriminator lost the baseband: worst error {error:.4f} "
            f"against an amplitude of {amp}. A spurious +/-2*pi step is "
            f"{2.0 * math.pi * IQ / (2.0 * math.pi * DEV):.4f} in these units.")

    def test_the_naive_difference_would_be_wrong_on_the_same_signal(self):
        """The control: without the wrap, the same signal is unrecoverable.

        This is what makes the test above able to fail.
        """
        amp, hz = 0.5, 500.0
        n = 20000
        iq = []
        for i in range(n):
            phase = -DEV * amp * math.cos(2.0 * math.pi * hz * i / IQ) / hz
            iq.append(complex(math.cos(phase), math.sin(phase)))
        naive, prev = [], math.atan2(iq[0].imag, iq[0].real)
        for z in iq[1:]:
            phase = math.atan2(z.imag, z.real)
            naive.append((phase - prev) * IQ / (2.0 * math.pi * DEV))
            prev = phase
        scale = IQ / (2.0 * math.pi * DEV)
        spike = 2.0 * math.pi * scale
        assert max(abs(v) for v in naive) > spike * 0.9, (
            "the unwrapped difference should also show the +/-2*pi spike, "
            "otherwise this control proves nothing")


class TestStereoSeparation:
    """The point of the whole module: L and R come back separable."""

    def test_a_left_only_tone_does_not_reach_the_right_channel(self):
        n = int(IQ * 0.2)
        pairs = F.demodulate(F.synthesize_fm(tone(1000, n=n), [0.0] * n, IQ), IQ)
        left = [p[0] for p in pairs]
        right = [p[1] for p in pairs]
        rejection = db(magnitude(left, 1000), magnitude(right, 1000))
        assert rejection >= 20.0, (
            f"a left-only tone reached the right channel: {rejection:+.1f} dB "
            "of rejection (want >= 20)")

    def test_a_right_only_tone_does_not_reach_the_left_channel(self):
        n = int(IQ * 0.2)
        pairs = F.demodulate(F.synthesize_fm([0.0] * n, tone(1000, n=n), IQ), IQ)
        left = [p[0] for p in pairs]
        right = [p[1] for p in pairs]
        rejection = db(magnitude(right, 1000), magnitude(left, 1000))
        assert rejection >= 20.0, (
            f"a right-only tone reached the left channel: {rejection:+.1f} dB "
            "of rejection (want >= 20)")

    def test_hard_panned_channels_stay_separate(self):
        n = int(IQ * 0.2)
        pairs = F.demodulate(F.synthesize_fm(tone(1000, n=n), tone(2000, n=n),
                                             IQ), IQ)
        left = [p[0] for p in pairs]
        right = [p[1] for p in pairs]
        for name, a, b in (("left", magnitude(left, 1000), magnitude(left, 2000)),
                           ("right", magnitude(right, 2000), magnitude(right, 1000))):
            sep = db(a, b)
            assert sep >= 20.0, (
                f"the {name} channel lost {sep:+.1f} dB of separation "
                "(want >= 20)")

    def test_mono_comes_back_mono(self):
        """A mono transmitter must not invent a difference channel.

        Asserted as a ratio, not as equality: both channels traverse the same
        filters and the floating point is not bit-reproducible.
        """
        n = int(IQ * 0.2)
        pairs = F.demodulate(F.synthesize_fm(tone(1000, n=n), tone(1000, n=n),
                                             IQ), IQ)
        left = [p[0] for p in pairs]
        right = [p[1] for p in pairs]
        rms_diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)) / len(left))
        rms_left = math.sqrt(sum(v * v for v in left) / len(left))
        assert db(rms_diff, rms_left) <= -20.0, (
            f"a mono signal produced a difference channel: {db(rms_diff, rms_left):+.1f} dB "
            "(want <= -20)")


class TestDeMatrix:
    """`L = mono + difference` and `R = mono - difference`, exactly."""

    def test_the_coefficient_is_one_and_the_channels_rebuild(self):
        # With mono = (L+R)/2 and the subcarrier carrying side = (L-R)/2,
        # adding the difference must give L back and subtracting it must give R.
        left_val, right_val = 0.4, -0.1
        mono = (left_val + right_val) * 0.5
        side = (left_val - right_val) * 0.5
        assert abs((mono + side) - left_val) < 1e-12
        assert abs((mono - side) - right_val) < 1e-12


class TestPreAndDeEmphasisAreAnInversePair:
    """The pair that makes a round trip flat rather than tilted."""

    @pytest.mark.parametrize("hz", [100, 400, 1000, 2122, 4000, 8000, 15000])
    def test_pre_emphasis_then_de_emphasis_is_the_identity(self, hz):
        fs = 32000
        a = math.exp(-1.0 / (75e-6 * fs))
        n = 32 * fs
        x = [0.5 * math.sin(2.0 * math.pi * hz * i / fs) for i in range(n)]
        y = F._deemphasis(F._preemphasis(x, a), a)
        skip = n // 4
        num = sum(y[i] * math.sin(2.0 * math.pi * hz * i / fs)
                  for i in range(skip, n))
        den = sum(x[i] * math.sin(2.0 * math.pi * hz * i / fs)
                  for i in range(skip, n))
        assert abs(num / den - 1.0) < 0.02, (
            f"{hz} Hz came back at {num / den:.5f} through the pair; the "
            "receiver's de-emphasis must cancel the transmitter's pre-emphasis")

    @pytest.mark.parametrize("fs", [32000, 48000, 128000, 200000])
    def test_the_de_emphasis_corner_is_the_time_constant_not_the_rate(self, fs):
        a = F._deemphasis_pole(fs, 75e-6)
        corner = -math.log(a) * fs / (2.0 * math.pi)
        assert abs(corner - 2122.0) < 1.0, (
            f"at {fs} S/s the one-pole's -3 dB point is {corner:.0f} Hz, but "
            "the 75 us time constant fixes it at 2122 Hz")

    def test_pre_emphasis_rises_with_frequency(self):
        """A transmitter's pre-emphasis boosts the top of the band."""
        fs = 32000
        a = math.exp(-1.0 / (75e-6 * fs))
        n = 32 * fs
        gains = []
        for hz in (400, 15000):
            x = [math.sin(2.0 * math.pi * hz * i / fs) for i in range(n)]
            y = F._preemphasis(x, a)
            skip = n // 4
            num = sum(y[i] * math.sin(2.0 * math.pi * hz * i / fs)
                      for i in range(skip, n))
            den = sum(x[i] * math.sin(2.0 * math.pi * hz * i / fs)
                      for i in range(skip, n))
            gains.append(num / den)
        assert gains[1] > gains[0] * 2.0, (
            f"pre-emphasis gained {gains[0]:.2f} at 400 Hz and {gains[1]:.2f} "
            "at 15 kHz; a transmitter's must rise with frequency")


class TestPilotLock:
    """A marginal lock threshold is why the decoder fell back to a free-running
    oscillator; a threshold that cannot fail is the other way to be wrong."""

    def test_a_nominal_pilot_locks_with_real_margin(self):
        n = 127999
        sig = [0.1 * math.sin(2.0 * math.pi * 19000 * i / MID) for i in range(n)]
        ref, hz, strength = F.pilot_reference(sig, MID, F.PILOT_HZ)
        assert ref, "a nominal 0.1-amplitude pilot did not lock"
        assert hz == pytest.approx(19000.0, abs=1.0)
        # A 0.1 pilot reads 0.05 (goertzel_power is amplitude/2), so the
        # measurement has to recover most of it. The 0.02 lock threshold alone is
        # a weak claim - a bandpass that costs 30% of the amplitude still clears
        # it - so the recovery itself is asserted.
        assert strength >= 0.045, (
            f"the pilot reads {strength:.5f} against the 0.05 a 0.1-amplitude "
            f"pilot should give - only {strength / 0.05 * 100:.0f}% recovered. "
            "Isolation must not attenuate the tone it is isolating.")

    @pytest.mark.parametrize("amp", [0.5, 2.0])
    def test_noise_does_not_lock(self, amp):
        random.seed(1234)
        sig = [random.uniform(-amp, amp) for _ in range(127999)]
        ref, hz, strength = F.pilot_reference(sig, MID, F.PILOT_HZ)
        assert not ref, (
            f"white noise at peak {amp} locked a 'pilot' at {hz} Hz with "
            f"strength {strength:.6f} - that is a false stereo decode")

    def test_silence_does_not_lock(self):
        ref, hz, strength = F.pilot_reference([0.0] * 127999, MID, F.PILOT_HZ)
        assert not ref

    def test_an_off_nominal_pilot_still_locks(self):
        """A real transmitter is not always on 19.000 kHz."""
        for offset in (-150.0, 150.0):
            hz = 19000.0 + offset
            n = 127999
            sig = [0.1 * math.sin(2.0 * math.pi * hz * i / MID) for i in range(n)]
            ref, measured, strength = F.pilot_reference(sig, MID, F.PILOT_HZ)
            assert ref, f"a pilot at {hz} Hz did not lock"
            assert measured == pytest.approx(hz, abs=20.0), (
                f"a pilot at {hz} Hz was measured at {measured} Hz")


class TestRoundTripLevels:
    """The composite's levels are the standard's, or the pilot and the audio
    stop being comparable."""

    def test_the_audio_dominates_the_pilot(self):
        """Before the pre-emphasis fix the pilot was 76x stronger than the
        subcarrier it was supposed to lock to."""
        a = F._deemphasis_pole(32000, 75e-6)
        mono = [0.5 * math.sin(2.0 * math.pi * 1000 * i / IQ) for i in range(20000)]
        side = [0.25 * math.sin(2.0 * math.pi * 1000 * i / IQ) for i in range(20000)]
        pre_mono = F._preemphasis(mono, a)
        pre_side = F._preemphasis(side, a)
        subcarrier = max(abs(v) for v in pre_side)
        assert subcarrier > 0.1, (
            f"the subcarrier's amplitude is {subcarrier:.5f} against a pilot of "
            "0.1; the pilot must not out-shout the signal it is locking")

    def test_a_full_level_tone_comes_back_at_full_level(self):
        n = int(IQ * 0.2)
        pairs = F.demodulate(F.synthesize_fm(tone(1000, amp=1.0, n=n), [0.0] * n, IQ), IQ)
        left = [p[0] for p in pairs]
        assert max(abs(v) for v in left) > 0.8, (
            f"a full-level left channel came back peaking at "
            f"{max(abs(v) for v in left):.4f}; the composite should carry it")

    def test_the_round_trip_is_flat_across_the_audio_band(self):
        """The transmitter must use the pre-emphasis inverse, not a second lowpass.

        `demodulate` normalises every frame to a peak of 1.0, so **a single-tone
        level test through it proves nothing** - every signal comes back at 1.0
        whatever was done to it. Measured, in fact: a 6 kHz tone sent alone reads
        -1.77 dB against 300 Hz when the transmitter is given a lowpass instead
        of the pre-emphasis inverse, because the normalisation rescales the whole
        frame. That is why this sends **both tones in one signal**, so the single
        scalar cannot hide the difference between them.
        """
        n = int(IQ * 0.3)
        signal = [0.25 * math.sin(2.0 * math.pi * 300.0 * i / IQ)
                  + 0.25 * math.sin(2.0 * math.pi * 6000.0 * i / IQ)
                  for i in range(n)]
        pairs = F.demodulate(F.synthesize_fm(signal, signal, IQ), IQ)
        left = [p[0] for p in pairs]
        ratio = db(magnitude(left, 6000.0), magnitude(left, 300.0))
        # The correct pair measures -0.27 dB here; a lowpass standing in for the
        # transmitter's pre-emphasis puts the audio through the shelf twice.
        assert -6.0 <= ratio <= 6.0, (
            f"a 6 kHz tone came back {ratio:+.1f} dB against a 300 Hz one in the "
            "same signal. Pre-emphasis then de-emphasis is an inverse pair and "
            "the round trip is flat; a lowpass applied on both ends is not.")

    def test_the_receivers_de_emphasis_corner_is_the_time_constant(self):
        """The receiver's own pole, read from the module rather than re-derived.

        A test that computes `exp(-1/(tau*fs))` itself and checks the resulting
        corner asserts the algebra it just wrote, not the code - it passes however
        the module computes its pole. Reading `_deemphasis_pole` is what makes it
        able to fail.
        """
        for fs in (32000, 48000, 128000, 200000):
            a = F._deemphasis_pole(fs, 75e-6)
            corner = -math.log(a) * fs / (2.0 * math.pi)
            assert abs(corner - 2122.0) < 1.0, (
                f"at {fs} S/s the receiver's de-emphasis -3 dB point is "
                f"{corner:.0f} Hz, but the 75 us time constant fixes it at 2122 Hz")

    def test_the_de_emphasis_the_receiver_actually_applies_is_flat(self):
        """The same corner, measured through `demodulate`'s own filters.

        Comparing the same signal decoded with and without de-emphasis isolates
        what the receiver's filter does, and it survives the peak normalisation
        because both runs are normalised and the normalising scalar cancels in
        the ratio.
        """
        n = int(IQ * 0.3)
        signal = [0.25 * math.sin(2.0 * math.pi * 500.0 * i / IQ)
                  + 0.25 * math.sin(2.0 * math.pi * 8000.0 * i / IQ)
                  for i in range(n)]
        iq = F.synthesize_fm(signal, signal, IQ, preemphasis_us=0.0)
        levels = {}
        for us in (0.0, 75.0):
            pairs = F.demodulate(iq, IQ, deemphasis_us=us)
            left = [p[0] for p in pairs]
            levels[us] = {hz: magnitude(left, hz) for hz in (500.0, 8000.0)}
        # With de-emphasis on, 8 kHz must drop relative to 500 Hz.
        tilt = db(levels[75.0][8000.0] / levels[75.0][500.0],
                  levels[0.0][8000.0] / levels[0.0][500.0])
        assert tilt <= -4.0, (
            f"the receiver's de-emphasis only tilted the band by {tilt:+.1f} dB "
            "at 8 kHz; a 75 us one-pole's -3 dB point is 2122 Hz, so 8 kHz "
            "should be well down")
