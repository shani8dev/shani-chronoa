"""The standard-library DSP primitives, pinned by their mathematical invariants.

No oracle and no hardware: every property is checked from directly-run output.

Run: python3 -m pytest tests/test_fmdsp.py
"""

from __future__ import annotations

import math
import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import fmdsp as F  # noqa: E402

RATE = 250_000.0


def sine(hz: float, n: int = 60000, rate: int = int(RATE)) -> "list[float]":
    return [math.sin(math.tau * hz * i / rate) for i in range(n)]


def amp(sig: "list[float]", hz: float, rate: int = int(RATE)) -> float:
    return 2.0 * max(F.goertzel_power(sig, rate, f) for f in (hz - 8, hz, hz + 8))


def gains_db(sos, f) -> "list[float]":
    return [20 * math.log10(max(amp(F.sosfiltfilt(sos, sine(f))[RATE_CHUNK:2 * RATE_CHUNK]
                                if False else F.sosfiltfilt(sos, sine(f)), *[]), 1e-9))]


RATE_CHUNK = 15000


class TestButterworthShape:
    def test_passband_is_near_unity(self):
        for order in (2, 4, 8):
            sos = F.butter_sos(order, 5000.0, RATE, "low")
            out = F.sosfiltfilt(sos, sine(500.0))
            assert amp(out[RATE_CHUNK:2 * RATE_CHUNK], 500.0) > 0.95, (order,)

    def test_cutoff_is_six_db_down_because_filtfilt_doubles_it(self):
        sos = F.butter_sos(4, 5000.0, RATE, "low")
        out = F.sosfiltfilt(sos, sine(5000.0))
        db = 20 * math.log10(max(amp(out[RATE_CHUNK:2 * RATE_CHUNK], 5000.0), 1e-9))
        # filtfilt squares the response, so the -3 dB single-pass point reads
        # -6 dB here; that doubling is the *intended* zero-phase behaviour.
        assert -7.5 < db < -4.5, db

    def test_rolloff_is_twice_the_order_because_of_filtfilt(self):
        # The clamp is a **measurement floor**, and where it sits decides whether
        # order 8 is testable at all. At 20 kHz the true amplitude is 2.8e-10;
        # `max(amp, 1e-9)` reports -180.0 dB for it, which is the clamp talking,
        # and the slope computed from a clamped value is 14.0 dB/octave instead
        # of 16 - the test failed on its own floor, not on the filter. Measured
        # single-pass slopes, which never reach the floor: 1.94, 3.97, 7.94
        # against 2, 4, 8.
        #
        # 1e-15 is chosen from this signal length, not picked to pass: Goertzel
        # over 30000 float64 samples of an all-zero signal reads exactly 0.0,
        # and the order-8 response at 40 kHz is 7.1e-15, i.e. at the noise. A
        # clamp below that would be reporting noise as attenuation.
        FLOOR = 1e-15
        for order in (2, 4, 8):
            sos = F.butter_sos(order, 5000.0, RATE, "low")
            single = []
            doubled = []
            for hz in (5000.0, 10000.0, 20000.0):
                raw = sine(hz)
                single.append(20 * math.log10(max(amp(F.sosfilt(sos, raw)[RATE_CHUNK:2 * RATE_CHUNK], hz), FLOOR)))
                doubled.append(20 * math.log10(max(amp(F.sosfiltfilt(sos, raw)[RATE_CHUNK:2 * RATE_CHUNK], hz), FLOOR)))
            assert abs((single[1] - single[2]) / 6.0 - order) < 1.5, (
                order, "single pass")
            assert abs((doubled[1] - doubled[2]) / 6.0 - 2 * order) < 1.5, (
                order, "filtfilt")

    def test_a_clamped_reading_is_distinguishable_from_a_measured_one(self):
        """The control for the floor above: it must be able to say "not measurable".

        If the clamp were low enough to swallow real responses, an order-16
        filter would report a slope it had not measured. Asserted on the raw
        amplitudes, so the floor stays honest as orders grow.
        """
        sos = F.butter_sos(8, 5000.0, RATE, "low")
        raw = amp(F.sosfiltfilt(sos, sine(20000.0))[RATE_CHUNK:2 * RATE_CHUNK], 20000.0)
        assert raw > 1e-15, (
            f"order 8 at 20 kHz reads {raw:.3e}, which the 1e-15 floor would "
            f"report as pure attenuation rather than a measurement")
        assert amp([0.0] * 30000, 20000.0) == 0.0, (
            "an all-zero signal reads non-zero, so the noise floor is not what "
            "this test assumes")

    def test_highpass_blocks_low_and_passes_high(self):
        sos = F.butter_sos(4, 300.0, RATE, "high")
        low = amp(F.sosfiltfilt(sos, sine(100.0))[RATE_CHUNK:2 * RATE_CHUNK], 100.0)
        hi = amp(F.sosfiltfilt(sos, sine(5000.0))[RATE_CHUNK:2 * RATE_CHUNK], 5000.0)
        assert low < 0.0002, low
        assert hi > 0.95, hi


class TestEveryPoleIsInsideTheUnitCircle:
    def test_lowpass_poles(self):
        for order in (2, 4, 6, 8):
            for f in (300.0, 5000.0, 15000.0, 38000.0, 100000.0):
                for p in F._lowpass_poles(order, f, RATE):
                    assert abs(p) < 1.0, (order, f, abs(p))

    def test_all_filters_produce_bounded_impulse_response(self):
        for btype_args in [
            dict(order=4, cutoff=5000.0, fs=RATE, btype="low"),
            dict(order=4, cutoff=300.0, fs=RATE, btype="high"),
            dict(order=4, cutoff=(18000.0, 40000.0), fs=RATE, btype="bandpass"),
        ]:
            sos = F.butter_sos(**btype_args)
            imp = F.sosfilt(sos, [1.0] + [0.0] * 8000)
            assert max(abs(v) for v in imp) < 1000.0, btype_args


class TestDcBlock:
    def test_it_removes_offset_and_keeps_audio(self):
        # Measured window, not an assumed one. `dc_block` is a one-pole
        # `y = x - x[n-1] + r*y[n-1]` with `r = 1 - 2*pi*fc/rate`, so at 30 Hz
        # and 250 kS/s its time constant is **1326 samples** - the offset decays
        # as `r**n` and is not gone until about `1326 * ln(1e5)` samples in.
        # The previous window (`y[10000:30000]`) started well inside that
        # transient and read a mean of 0.035, which is `1000 * r**n` still
        # decaying, not a filter that failed to remove the offset. Measured, on
        # this input: mean 33.16 over the whole signal, 0.0351 over
        # `[10000:30000]`, 1.9e-5 over `[20000:40000]`, 0.0 over the tail.
        x = [1000.0 + math.sin(math.tau * 1000 * i / RATE) for i in range(40000)]
        y = F.dc_block(x, int(RATE), 30.0)
        settled = y[len(y) // 2:]
        assert abs(sum(settled) / len(settled)) < 0.01, (
            f"offset survives the transient: {sum(settled) / len(settled):.6f}")
        assert 0.9 < max(abs(v) for v in settled) < 1.2, "the audio was filtered away"

    def test_the_transient_decays_at_the_rate_the_one_pole_predicts(self):
        """The settling is asserted, not skipped past.

        A DC blocker that removed the offset instantly would be a different
        filter, and so would one that never settled. `r**n` is checked directly
        against the implementation's own coefficient, so a change in the filter
        that breaks the decay fails here rather than being absorbed into a
        looser window in the test above.
        """
        cutoff, rate = 30.0, int(RATE)
        r = 1.0 - 2.0 * math.pi * cutoff / rate
        x = [1000.0] * 40000
        y = F.dc_block(x, rate, cutoff)
        for n in (2000, 8000):
            expected = 1000.0 * r ** n
            assert abs(y[n] - expected) < max(1e-6, expected * 0.01), (
                f"at n={n} the offset is {y[n]:.6f}, the one-pole model predicts "
                f"{expected:.6f}")

    def test_it_is_stable_where_the_highpass_was_not(self):
        # The 4th-order Butterworth at 30 Hz produced an impulse response with a
        # 4.6e6 peak. The DC blocker must be well-behaved on the same input.
        x = [1.0] + [0.0] * 8000
        y = F.dc_block(x, int(RATE), 30.0)
        assert max(abs(v) for v in y) < 2.0


class TestResample:
    def test_lengths_match_the_ratio(self):
        sig = sine(3000.0, n=199999)
        assert len(F.resample(sig, 128000, 200000)) in (127999, 128000)
        # 32000/128000 is a quarter, and 199999/4 is 49999.75. The previous
        # expectation of `(31999, 32000)` is the answer for a **128000**-sample
        # input, not this one - the test was asking for a length the input cannot
        # produce. Measured: 50000, which is what the ratio gives.
        assert len(F.resample(sig, 32000, 128000)) in (49999, 50000)

    def test_the_length_is_the_ratio_and_not_a_constant(self):
        """The property behind the numbers, so a future rate pair cannot pass by luck.

        **Two equivalent mutants, kept rather than hidden.** Reducing the pair
        list to one entry leaves this file green, because the ratio holds for
        every pair listed - the multi-pair list documents coverage rather than
        catching a live bug, and `test_lengths_match_the_ratio` asserts the
        32000/128000 case separately. Weakening `raw > 1e-15` to `raw > 0.0` in
        the floor control is equivalent for the same reason: the order-8 response
        really is positive (2.8e-10), so both bounds are true. Neither is a gap;
        both are recorded so the next mutation run does not re-derive them.
        """
        sig = sine(3000.0, n=199999)
        for up, down in ((128000, 200000), (32000, 128000), (16000, 200000),
                         (44100, 48000), (8000, 48000)):
            got = len(F.resample(sig, up, down))
            want = len(sig) * up / down
            assert abs(got - want) <= 1, (
                f"resample to {up} from {down} gave {got}, the ratio says {want:.1f}")

    def test_a_tone_keeps_its_frequency(self):
        for hz in (1000.0, 7000.0):
            out = F.resample(sine(hz), 1, 2)
            assert amp(out[15000:30000], hz, rate=int(RATE / 2)) > 0.98

    def test_above_nyquist_is_suppressed_not_leaned_on(self):
        # The whole point of the filter: 40 kHz must not fold into the audio band.
        out = F.resample(sine(40000.0), 1, 2)
        assert amp(out[15000:30000], 4000.0, rate=int(RATE / 2)) < 0.001

    def test_it_refuses_the_non_artefact(self):
        try:
            F.resample(sine(1000.0), 0, 2)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


class TestGoertzel:
    def test_a_unit_sine_reads_a_half(self):
        sig = sine(5000.0, n=40000)
        assert abs(F.goertzel_power(sig, RATE, 5000.0) - 0.5) < 0.02

    def test_a_far_frequency_reads_zero(self):
        sig = sine(5000.0, n=40000)
        assert F.goertzel_power(sig, RATE, 100.0) < 0.01
