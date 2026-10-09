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
        for order in (2, 4, 8):
            sos = F.butter_sos(order, 5000.0, RATE, "low")
            g = []
            for hz in (5000.0, 10000.0, 20000.0):
                out = F.sosfiltfilt(sos, sine(hz))
                g.append(20 * math.log10(max(amp(out[RATE_CHUNK:2 * RATE_CHUNK], hz), 1e-9)))
            slope = (g[1] - g[2]) / 6.0
            assert abs(slope - 2 * order) < 1.5, (order, slope)

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
        x = [1000.0 + math.sin(math.tau * 1000 * i / RATE) for i in range(40000)]
        y = F.dc_block(x, int(RATE), 30.0)
        mid = y[10000:30000]
        assert abs(sum(mid) / len(mid)) < 0.01
        assert 0.9 < max(abs(v) for v in mid) < 1.2

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
        assert len(F.resample(sig, 32000, 128000)) in (31999, 32000)

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
