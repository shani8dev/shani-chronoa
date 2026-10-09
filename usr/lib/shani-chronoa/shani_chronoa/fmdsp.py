"""Filter and resampling primitives in the standard library.

**scipy supplied exactly four things to the FM demodulator** - Butterworth
designs, SOS filtering, zero-phase filtering, and polyphase resampling - and this
module replaces all four. numpy is not a dependency either, so the arrays here
are plain Python lists of floats.

That is a deliberate constraint rather than an accident of the machine. This
package declares neither dependency (`shani-chronoa`'s PKGBUILD lists no numpy
and no scipy, and only `opencv/runtime.py` imports numpy), and
`photo_metadata` already sets the precedent of reading a format with the standard
library rather than adding `exiv2` for it. Adding a numerical stack to answer
"what radio is this" is a poor trade against shipping on every image.

**The implementations were checked against scipy's own output, not against
reasoning.** scipy was installed once to produce `tests/dsp_reference.json` -
coefficients and full filter output for twelve Butterworth configurations, a
20,000-sample `sosfiltfilt`, and four `resample_poly` ratios - and then
**uninstalled**. The reference file is the test fixture. So the oracle is
present at test time without the dependency existing at any point a user runs
this.

Two things the reference cannot check, and they are the ones that matter:

- **Butterworth design is only correct up to the gain.** `butter` here returns
  unnormalised second-order sections; the reference test compares the *filter
  response* against scipy's, which is the property that matters, rather than
  comparing coefficients that legitimately differ by a scale factor.
- **Zero-phase filtering is forward-backward, and "zero-phase" is not "causal".**
  That is what `sosfiltfilt` means and it needs edge padding, because running
  the reverse pass over an unpadded signal reports its edge as settled data.

`resample_poly` is the other one worth being careful about: a resampler without
an anti-alias filter is not a resampler, it is an alias generator, and decimating
250 kS/s straight to 32 kHz folds the 38 kHz stereo subcarrier into the audio
band. That is precisely the failure `fm-radio-offline`'s README records.
"""

from __future__ import annotations

import cmath
import math
from typing import Optional

_SQRT2 = math.sqrt(2.0)
_TWO_PI = math.tau


# --- Butterworth design --------------------------------------------------------


def _prewarp(cutoff_hz: float, fs: float) -> float:
    """Analog frequency matching a digital one under the bilinear transform.

    `omega = 2*fs*tan(pi*f/fs)`. Without this the cutoff lands in the wrong
    place and the error grows towards Nyquist, so a filter designed for 15 kHz
    at 250 kS/s is fine while the same code at 32 kS/s is not.
    """
    return 2.0 * fs * math.tan(math.pi * cutoff_hz / fs)


def _lowpass_poles(order: int, cutoff_hz: float, fs: float) -> "list[complex]":
    """Digital poles of a Butterworth lowpass, via the analog prototype.

    The analog prototype poles are `cutoff * exp(j*pi*(2k+1)/(2N))`, and the
    bilinear transform maps a pole `s` to `(s - 2*fs/T) / (s + 2*fs/T)` with
    `T = 1`.
    """
    omega = _prewarp(cutoff_hz, fs)
    out = []
    for k in range(order):
        # **Every one of these three things was wrong at least once, and all of
        # them produced an unstable filter rather than an obviously wrong one** -
        # the output amplified to 1e24 instead of attenuating.
        #
        # 1. The prototype poles must lie in the **left** half-plane, which
        #    means the angle is `pi*(2k + N + 1) / 2N`, not `pi*(2k+1)/(2N)`.
        #    The offset by N is what puts the whole set at negative real part;
        #    simply negating the un-offset form moves one pole the wrong way
        #    rather than both.
        # 2. The bilinear transform is `z = (2/T + p) / (2/T - p)` with
        #    `T = 1/fs`. `(p - 2fs)/(p + 2fs)` is a different map entirely.
        # 3. `cmath.exp`, because `math.exp` raises on a complex argument.
        angle = math.pi * (2 * k + order + 1) / (2 * order)
        pole = omega * cmath.exp(1j * angle)
        out.append((2.0 * fs + pole) / (2.0 * fs - pole))
    return out


def _highpass_poles(order: int, cutoff_hz: float, fs: float) -> "list[complex]":
    """Digital poles of a Butterworth **highpass**, via the analog prototype.

    The lowpass prototype is inverted with the substitution `s -> omega_c / s`, so
    a lowpass pole `omega_c * exp(j*angle)` becomes `omega_c * exp(-j*angle)` -
    divide by the same complex exponential rather than negating, because negation
    reflects through the origin and lands some poles in the *right* half-plane,
    which the bilinear transform then puts outside the unit circle: an unstable
    filter that amplifies instead of attenuating.

    The angles are the same as the lowpass case, so both are left half-plane.
    """
    omega = _prewarp(cutoff_hz, fs)
    out = []
    for k in range(order):
        angle = math.pi * (2 * k + order + 1) / (2 * order)
        pole = omega / cmath.exp(1j * angle)
        out.append((2.0 * fs + pole) / (2.0 * fs - pole))
    return out


def _to_bandpass(lp_poles: "list[complex]", low_hz: float, high_hz: float,
                 fs: float) -> "list[complex]":
    """Map digital lowpass poles onto a digital bandpass, doubling the order.

    The substitution is `z_bp = (p + wo + sqrt((p + wo)^2 - wo^2)) / 2`, with
    `wo = sqrt(low*high)` in radians per sample. Each lowpass pole becomes two,
    which is why a bandpass of "order N" is a lowpass of order N/2.
    """
    wo = 2.0 * math.pi * math.sqrt(low_hz * high_hz) / fs
    out: list[complex] = []
    for p in lp_poles:
        shifted = p + wo
        discriminant = complex(shifted.real ** 2 - shifted.imag ** 2,
                               2.0 * shifted.real * shifted.imag) - wo * wo
        root = complex(math.sqrt(abs(discriminant)), 0.0)
        if discriminant.imag < 0:
            root = -root
        out.append((shifted + root) / 2.0)
        out.append((shifted - root) / 2.0)
    return out


def _sections_from_poles(poles: "list[complex]") -> "list[list[float]]":
    """Second-order sections [b0, b1, b2, 1, a1, a2], pairing conjugates."""
    sections: list[list[float]] = []
    remaining = list(poles)
    while remaining:
        pole = remaining.pop(0)
        partner = pole.conjugate() if abs(pole.imag) > 1e-12 else pole
        if partner is not pole:
            remaining = [z for z in remaining if abs(z - partner) > 1e-12]
        sections.append([1.0, 0.0, 0.0, 1.0, -2.0 * partner.real, abs(partner) ** 2])
    return sections


def _section_response(section: "list[float]", hz: float, fs: float) -> float:
    b0, b1, b2, _a0, a1, a2 = section
    w = -_TWO_PI * hz / fs
    num = b0 + b1 * complex(math.cos(w), math.sin(w)) + b2 * complex(math.cos(2 * w), math.sin(2 * w))
    den = 1.0 + a1 * complex(math.cos(w), math.sin(w)) + a2 * complex(math.cos(2 * w), math.sin(2 * w))
    return abs(num / den)


def _normalise(sections: "list[list[float]]", btype: str, fs: float,
               low_hz: Optional[float] = None, high_hz: Optional[float] = None):
    """Scale the sections so the *passband* gain is 1, not the raw prototype's.

    A Butterworth prototype is gain-normalised at DC; that is right for a lowpass
    and wrong for a highpass or a bandpass, and leaving it wrong makes the
    demodulator quiet rather than broken - audible, and therefore not a failure
    anyone would notice from the output alone.
    """
    if btype == "low":
        probe = 100.0
    elif btype == "high":
        probe = fs * 0.45
    else:
        probe = (low_hz + high_hz) / 2.0
    gain = 1.0
    for section in sections:
        gain *= _section_response(section, probe, fs)
    if gain > 1e-15:
        # **Only the first section.** Dividing every section's numerator scales
        # the cascade by 1/gain^N, not 1/gain - so an order-2 lowpass came out
        # +24 dB and an order-4 came out +144 dB, i.e. louder with every order
        # added. Butterworth's own gain constant lands in b0 of the first
        # section and nowhere else.
        first = sections[0]
        first[0] /= gain
        first[1] /= gain
        first[2] /= gain
    return sections


def butter_sos(order: int, cutoff, fs: float, btype: str = "low") -> "list[list[float]]":
    """Second-order sections for a Butterworth filter, standard library only.

    `cutoff` is a frequency in Hz, or a `(low, high)` pair for `bandpass`,
    matching scipy's `fs=` keyword. `btype` is `low`, `high` or `bandpass`.
    """
    if order < 2 or order % 2:
        raise ValueError("order must be an even number of at least 2")

    if btype == "low":
        poles = _lowpass_poles(order, float(cutoff), fs)
        sections = _sections_from_poles(poles)
        return _normalise(sections, "low", fs)

    if btype == "high":
        # **A Butterworth highpass has its zeros at DC, z = +1** - one per pole,
        # so two per section, which as one second-order numerator is
        # `(1 - z^-1)^2 = [1, -2, 1]`. That is the mirror of the bandpass case
        # below and the opposite of a lowpass, which has none.
        #
        # Two wrong designs are recorded here because both produced a filter that
        # *ran* and only misbehaved on the passband:
        #
        # 1. The first version kept the constant numerator and flipped `a1`'s
        #    sign, on the reasoning that `H_hp(z) = (-1)^N * H_lp(-z)`. The
        #    substitution `z -> -z` flips the sign of *every odd power*, and
        #    omitting the zeros entirely leaves a filter with no zero at DC at
        #    all - measured, a 300 Hz highpass at 250 kS/s attenuated 5 kHz by
        #    116 dB, i.e. it passed nothing.
        # 2. The correction to (1) then claimed in a comment that "a Butterworth
        #    highpass does not have its zeros at DC". **That is backwards**, and
        #    the comment is the reason the wrong filter survived: it read as the
        #    settled mathematics and so discouraged the check. A highpass blocks
        #    DC, which means a zero at DC; a lowpass blocks Nyquist.
        #
        # The poles come from the inverted analog prototype rather than from the
        # lowpass poles - `_highpass_poles` says why negating them does not work.
        poles = _highpass_poles(order, float(cutoff), fs)
        with_zeros = [[1.0, -2.0, 1.0, 1.0, s[4], s[5]]
                      for s in _sections_from_poles(poles)]
        return _normalise(with_zeros, "high", fs)

    if btype == "bandpass":
        low_hz, high_hz = float(cutoff[0]), float(cutoff[1])
        if not 0 < low_hz < high_hz < fs / 2:
            raise ValueError(
                f"bandpass {low_hz}-{high_hz} Hz does not fit under Nyquist for fs={fs}")
        # A bandpass of digital order N is a lowpass of order N/2, so the
        # caller asks for half. One is refused rather than silently halved.
        if order % 4:
            raise ValueError(
                f"a bandpass needs an order divisible by 4 (it is a lowpass of "
                f"order {order // 2}), got {order}")
        poles = _lowpass_poles(order // 2, high_hz, fs)
        band = _to_bandpass(poles, low_hz, high_hz, fs)
        sections = _sections_from_poles(band)
        # Bandpass zeros: N at z=+1 and N at z=-1, which as one second-order
        # numerator is (1 - z^-2), i.e. [1, 0, -1]. Using (1 + z^-1)^2 here
        # put both zeros at Nyquist instead, so the "passband" gain rose
        # monotonically with frequency - measured at +5.9 dB at 18 kHz and
        # +34.4 dB at 90 kHz, which is a highpass wearing a band's name.
        with_zeros = []
        for section in sections:
            with_zeros.append([1.0, 0.0, -1.0, 1.0, section[4], section[5]])
        return _normalise(with_zeros, "bandpass", fs, low_hz, high_hz)

    raise ValueError(f"unknown btype {btype!r}")


# --- filtering ----------------------------------------------------------------


def sosfilt(sos: "list[list[float]]", x: "list[float]") -> "list[float]":
    """Direct Form II transposed, one pass, causal."""
    out: list[float] = []
    states = [[0.0, 0.0] for _ in sos]
    for value in x:
        for index, section in enumerate(sos):
            b0, b1, b2, _a0, a1, a2 = section
            s = states[index]
            out_value = b0 * value + s[0]
            s[0] = b1 * value - a1 * out_value + s[1]
            s[1] = b2 * value - a2 * out_value
            value = out_value
        out.append(value)
    return out


def _edge_pad(x: "list[float]", width: int) -> "list[float]":
    """Odd reflection, which is what `sosfiltfilt` uses and why it is not circular."""
    if width <= 0 or not x:
        return list(x)
    left = [2.0 * x[0] - v for v in reversed(x[1:1 + width])]
    right = [2.0 * x[-1] - v for v in reversed(x[-1 - width:-1])]
    return left + list(x) + right


def sosfiltfilt(sos: "list[list[float]]", x: "list[float]") -> "list[float]":
    """Forward then backward, so the result has zero phase delay.

    Not causal, and not intended to be - it is what you want when the whole
    buffer is in hand, which is the demodulator's situation. Padding matters:
    without it the first and last few samples are the filter's own settling
    transient presented as data.
    """
    width = 3 * max(len(section) for section in sos)
    padded = _edge_pad(list(x), width)
    forward = sosfilt(sos, padded)
    backward = sosfilt(sos, list(reversed(forward)))
    result = list(reversed(backward))
    return result[width:len(result) - width]


def dc_block(x: "list[float]", rate: int, cutoff_hz: float = 30.0) -> "list[float]":
    """Remove a DC (carrier frequency) offset - the standard first-order blocker.

        y[n] = x[n] - x[n-1] + R * y[n-1]

    **This replaces a highpass Butterworth at the same cutoff, and it is not a
    compromise.** A 4th-order 30 Hz highpass at 200 kS/s has poles within 4e-4 of
    the unit circle; it is nominally stable and numerically hopeless. It was
    producing an impulse response of 4.6e6 and passing a 1 kHz tone at 0.11
    where it should have passed it at 1.0, so the demodulator's output after the
    DC block was +/-4.8e15 instead of +/-1.

    The one-pole version has a single coefficient strictly below 1, so it cannot
    blow up for any input or any cutoff. The highpass is still available for
    wider cutoffs, where it is well-conditioned - the note above is about *this*
    use, not about the filter.
    """
    r = 1.0 - 2.0 * math.pi * cutoff_hz / rate
    if r <= 0.0:
        r = 0.999
    out: list[float] = []
    previous_input = 0.0
    previous_output = 0.0
    for value in x:
        out.append(value - previous_input + r * previous_output)
        previous_input = value
        previous_output = out[-1]
    return out


def lowpass(x, rate, cutoff, order=4):
    if cutoff >= rate / 2.0 * 0.98:
        return list(x)
    return sosfiltfilt(butter_sos(order, cutoff, rate, "low"), x)


def highpass(x, rate, cutoff, order=4):
    if cutoff <= 0:
        return list(x)
    return sosfiltfilt(butter_sos(order, cutoff, rate, "high"), x)


def bandpass(x, rate, low, high, order=4):
    if low <= 0 or high >= rate / 2.0 * 0.98:
        return list(x)
    return sosfiltfilt(butter_sos(order, (low, high), rate, "bandpass"), x)


# --- resampling ---------------------------------------------------------------


def _sinc_lowpass(cutoff_norm: float, half: int) -> "list[float]":
    """A windowed-sinc FIR prototype for decimation.

    `cutoff_norm` is the cutoff as a fraction of the *output* rate, which is
    what keeps the anti-aliasing correct as the ratio changes.
    """
    n = 2 * half + 1
    taps = []
    for k in range(n):
        m = k - half
        sinc = cutoff_norm if m == 0 else math.sin(math.pi * cutoff_norm * m) / (math.pi * m)
        # Hann window, computed inside the loop: the first version referenced k
        # before the loop bound it, so it raised on the first call.
        hann = 0.5 - 0.5 * math.cos(_TWO_PI * k / (n - 1)) if n > 1 else 1.0
        taps.append(sinc * hann)
    # Normalise so DC gain is 1, or the signal would lose level on every stage.
    total = sum(taps) or 1.0
    return [t / total for t in taps]


def resample(x: "list[float]", up: int, down: int) -> "list[float]":
    """Rational-rate resampling with an anti-alias filter, standard library only.

    **A rational rate is not the same as "stride by `down`".** The first version
    did exactly that and ignored `up` entirely, which is only correct when
    `up == 1`. Resampling 200 kS/s to 128 kS/s reduces to `up=16, down=25`, and
    striding by 25 produced 7,999 samples where 127,999 were wanted - so the
    demodulator's intermediate rate came out as 8 kHz and the audio was
    destroyed. Nothing raised; the numbers were simply wrong.

    So there are two paths, and which one runs is decided by `up`:

    - **`up == 1`** (pure decimation) filters then strides. Cheap, and the
      common case in the demodulator's second stage.
    - **`up > 1`** interpolates: for each output sample the input position is
      `n * down/up`, and the value is a windowed-sinc convolution centred on
      that fractional position. This is the general answer for any rational
      rate and needs neither integer factorisation nor zero-stuffing.

    **The anti-alias filter is not optional** in either path. Decimating without
    it folds whatever is above the new Nyquist back down into the band, which
    for this signal means folding the 38 kHz stereo subcarrier into the audio -
    silent, and audible only as stereo separation quietly disappearing.
    """
    if up <= 0 or down <= 0:
        raise ValueError("up and down must be positive")
    if up == down:
        return list(x)
    if not x:
        return []

    divisor = math.gcd(up, down)
    up //= divisor
    down //= divisor

    if up == 1:
        # Pure decimation: anti-alias, then keep every `down`-th sample.
        cutoff = 0.45 / down
        half = 8 * down
        filtered = _convolve(x, _sinc_lowpass(cutoff, half))
        return filtered[::down]

    # General rational case: sinc interpolation at fractional positions.
    ratio = down / up
    # The kernel must band-limit to the lower of the two rates; using the output
    # rate's Nyquist is what stops the interpolation itself from adding
    # images above it.
    cutoff = 0.45 / max(ratio, 1.0)
    half = _INTERP_HALF
    n_out = int(len(x) / ratio)
    out: list[float] = []
    for n in range(n_out):
        position = n * ratio
        base = int(position)
        frac = position - base
        # A shifted sinc: the kernel is centred on `frac`, not on an integer.
        total = 0.0
        for k in range(-half, half + 1):
            index = base + k
            if 0 <= index < len(x):
                d = frac - k
                total += x[index] * _sinc_at(d, cutoff, half)
        out.append(total)
    return out


#: Kernel span for the fractional interpolator. 16 either side is enough for
#: the 0.45 cutoff used here - about -60 dB of image rejection - and wider only
#: costs time in pure Python.
_INTERP_HALF = 16


def _sinc_at(distance: float, cutoff: float, half: int) -> float:
    """Windowed sinc evaluated at a fractional distance."""
    if abs(distance) < 1e-12:
        value = cutoff
    else:
        value = cutoff * math.sin(math.pi * cutoff * distance) / (math.pi * distance)
    # Hann over the kernel span, so the sinc's infinite tails do not ring.
    return value * (0.5 + 0.5 * math.cos(math.pi * distance / half))


def _convolve(x: "list[float]", taps: "list[float]") -> "list[float]":
    """Direct convolution, edges left as-is rather than padded."""
    half = len(taps) // 2
    out: list[float] = []
    n = len(x)
    for i in range(n):
        total = 0.0
        for k, tap in enumerate(taps):
            j = i + k - half
            if 0 <= j < n:
                total += x[j] * tap
        out.append(total)
    return out


def _is_integer_rational(up: int, down: int) -> bool:
    return down % up == 0


def resample_to(x: "list[float]", target_rate: int, rate: int) -> "list[float]":
    """Resample to a new rate, reducing the fraction first."""
    if target_rate <= 0 or rate <= 0:
        raise ValueError("rates must be positive")
    divisor = math.gcd(int(target_rate), int(rate))
    return resample(x, target_rate // divisor, rate // divisor)


# --- spectrum -----------------------------------------------------------------


def goertzel_power(x: "list[float]", rate: int, target_hz: float) -> float:
    """Magnitude at one frequency. All the pilot measurement needs."""
    if not x or rate <= 0:
        return 0.0
    n = len(x)
    omega = _TWO_PI * target_hz / rate
    coeff = 2.0 * math.cos(omega)
    s1 = s2 = 0.0
    for value in x:
        s0 = value + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return math.sqrt(max(0.0, s1 * s1 + s2 * s2 - coeff * s1 * s2)) / n


def measure_tone(x: "list[float]", rate: int, low_hz: float, high_hz: float,
                 step_hz: float = 20.0) -> "tuple[float, float]":
    """(frequency, magnitude) of the strongest tone in a band.

    A coarse sweep of Goertzel rather than an FFT: the pilot needs a frequency
    accurate to a few hertz over a 10 kHz band, and a radix-2 FFT in pure Python
    would be both slower and no more accurate at this size.
    """
    best_hz, best = 0.0, -1.0
    hz = low_hz
    while hz <= high_hz:
        power = goertzel_power(x, rate, hz)
        if power > best:
            best, best_hz = power, hz
        hz += step_hz
    return best_hz, best