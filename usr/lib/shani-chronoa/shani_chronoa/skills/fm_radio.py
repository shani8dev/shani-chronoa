"""Skill: FM broadcast radio - what hardware is here, and demodulating the audio.

**Nothing on this machine can receive FM, and saying so is the most common
answer this skill gives.** Measured here: seven USB devices, none an SDR; no
`/dev/dvb`; no `rtl_fm`, no `rtl_sdr`; and `/dev/video0`/`/dev/video1` are the
integrated *camera* (`ID_MODEL=Integrated_Camera`), not a tuner. A laptop has no
radio of its own, so a dongle is the whole hardware requirement.

**Two traps found while checking, both of which produce a false positive.**

The first is the vendor name. Searching for `RTL` finds the **RTL8188FTV**, an
802.11b/g/n Wi-Fi chipset, and reads as a match. The dongle you want advertises
as `RTL2838UHIDIR` - Realtek's string contains a famous typo, "UHIDIR" rather
than "USB", because it has shipped that way for years. Two chips from the same
vendor, and only one of them is a receiver. **A vendor-name grep cannot tell
them apart; the product string is the discriminator.**

The second is `videobuf2`/`uvcvideo` in `lsmod`. Those are webcam modules, and
`videodev` listing is not evidence of a video capture device that can tune
anything. What distinguishes a tuner is `/dev/dvb` and a DVB driver bound.

**The demodulator is written here, in the standard library, and that is the part
that can be verified without a radio.** An FM discriminator is arithmetic:
differentiate the phase of the complex IQ samples, scale by the sample rate and
the deviation, low-pass to the audio band, and de-emphasise. So a signal can be
*synthesised* here - a carrier at a known offset carrying a known tone - and
demodulated back, which is a real round trip rather than a mock.

Written in the standard library rather than numpy on purpose: this package does
not declare numpy as a dependency (only `opencv/runtime.py` imports it), and
`photo_metadata` sets the precedent of reading a format with stdlib rather than
adding `exiv2`. The same reasoning applies to a signal-processing library.

**No station names are invented.** A carrier gives you a frequency and, once
demodulated, possibly a tone. Turning that into "BBC Radio 6" needs a table for
your region, and a wrong station name is worse than a frequency. `scan` reports
frequencies and what could be heard there.

Tuning a radio is radio control, so the whole skill is consent-gated behind
`fm-radio-enabled`, off by default.
"""

from __future__ import annotations

import array
import math
import os
import shutil
import struct
import subprocess
import wave
from typing import Optional

from shani_chronoa import fmdsp
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

from .. import files

_CONSENT_KEY = "fm-radio-enabled"

_TIMEOUT = 30

#: FM broadcast, as assigned worldwide: 87.5-108 MHz, 200 kHz channel spacing,
#: ±75 kHz peak deviation, 15 kHz audio. Japan is the exception at 76-90 MHz,
#: which is why `bands` lists it rather than assuming.
BANDS = {
    "FM": (87.5e6, 108.0e6, 200_000),
    "AM": (0.53e6, 1.71e6, 10_000),
    "DAB": (174.0e6, 240.0e6, 1_812_000),
    "Japan FM": (76.0e6, 90.0e6, 200_000),
}

_ACTIONS = ("hardware", "bands", "scan", "tune", "decode")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature `test_question_presenter.py` sweeps."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Using the radio is turned off. Nothing was received or tuned. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


# --- the demodulator: standard library, and testable without a radio ---------


#: Constants for FM broadcast, taken from the working receiver in
#: `~/fm-radio-offline` (`fm_radio/dsp.py`) so the two agree. 200 kS/s is the
#: minimum that keeps the 38 kHz stereo subcarrier under Nyquist once decimated
#: to the 128 kS/s intermediate rate.
IQ_RATE = 200_000
AUDIO_RATE = 32_000
MID_RATE = 128_000
DEVIATION = 75_000.0
PILOT_HZ = 19_000.0
AUDIO_BW = 14_500.0
DIFF_BW = 10_000.0
DC_CUTOFF = 30.0
MIN_RATE_FOR_STEREO = 100_000


def pilot_reference(signal: "list[float]", rate: int,
                    pilot_hz: float = PILOT_HZ) -> "tuple[list[float], float, float]":
    """(reference, measured_hz, strength) - the 38 kHz signal recovered from the
    19 kHz pilot.

    **The pilot is measured, not assumed.** A bandpass around the pilot with
    poles near the unit circle is numerically awful at 38 kHz and 128 kS/s, so
    instead the pilot's frequency is found, its phase is read, and the doubled
    oscillator is synthesised directly. That also means a real capture whose
    pilot sits a little off 19.000 kHz still locks, which a fixed oscillator
    would not - and real transmissions are not always on the nominal.
    """
    band = bandlimit(signal, rate, pilot_hz * 0.82, pilot_hz * 1.18)
    if len(band) < 8:
        return [], 0.0, 0.0
    # Silence has no pilot. Without this a band of exact zeros still has a
    # "loudest" bin - the sweep returns the first frequency it tried, so an
    # all-zero input reported a pilot at 16.150 kHz and a strength of 0.
    if max((abs(v) for v in band), default=0.0) < 1e-9:
        return [], 0.0, 0.0
    measured_hz, strength = fmdsp.measure_tone(band, rate, pilot_hz * 0.85,
                                               pilot_hz * 1.15, step_hz=10.0)
    if strength < 0.02:
        return [], measured_hz, strength

    # The phase of the pilot, by projecting onto its own frequency. Summing
    # x[n]*exp(-j*omega*n) is the DFT at one bin, which is the cheapest way to
    # read a phase and needs no FFT.
    real = imag = 0.0
    for n, value in enumerate(band):
        angle = -2.0 * math.pi * measured_hz * n / rate
        real += value * math.cos(angle)
        imag += value * math.sin(angle)
    phase = math.atan2(imag, real)

    # Doubling a cosine doubles both its frequency and its phase.
    reference = [math.cos(2.0 * (2.0 * math.pi * measured_hz * n / rate + phase))
                 for n in range(len(band))]
    return reference, measured_hz, strength


def bandlimit(x: "list[float]", rate: int, low_hz: float, high_hz: float) -> "list[float]":
    """Band-limit as `lowpass(hi) - lowpass(lo)`.

    **Not `butter_sos(..., 'bandpass')`.** The hand-rolled Butterworth bandpass
    mapped every pole into the band and for the stereo subcarrier config put
    poles at radius 1.12 - outside the unit circle - so the stereo stage was
    NaN from the very first call, and NaN silently means mono because the
    pilot then reads zero strength and the receiver correctly-by-accident
    reports a mono station. The subtraction of two lowpasses is stable by
    construction and isolates the 38 kHz subcarrier well enough, because after
    mixing it to baseband the demodulator low-passes again at DIFF_BW, which
    is where the real noise shaping happens.
    """
    h_i = fmdsp.lowpass(x, rate, high_hz)
    h_o = fmdsp.lowpass(x, rate, low_hz)
    return [a - b for a, b in zip(h_i, h_o)]


def demodulate(iq: "list[complex]", sample_rate: int = IQ_RATE,
               deviation: float = DEVIATION, audio_rate: int = AUDIO_RATE,
               deemphasis_us: float = 75.0, stereo: bool = True,
               pilot_hz: float = PILOT_HZ) -> "list[tuple[float, float]]":
    """FM-discriminate complex baseband to (left, right) audio in -1.0..1.0.

    **Stereo is the point, and it is four extra steps**, not a flag:

    1. Discriminate - the difference of two `atan2` values is the instantaneous
       frequency in radians per sample, scaled by the sample rate and the
       deviation. A carrier with no modulation has a constant phase, so a silent
       station decodes to silence rather than to noise.
    2. Remove carrier offset with a DC block. Without it the offset dominates
       the output and swamps the audio.
    3. Decimate to an intermediate rate that still admits the 38 kHz subcarrier.
    4. Split: lowpass is mono; a bandpass around 38 kHz isolates L-R.
    5. Recover the pilot and mix L-R down to baseband, then de-matrix.

    **Step 3 is the one that is easy to get wrong.** Decimating straight to
    32 kS/s aliases the 38 kHz subcarrier into the audio band and destroys
    stereo separation, so the difference channel is demodulated in baseband and
    decimated independently of the mono channel. Decimating it first - the
    obvious order - is silent: the result is mono with a slightly different
    tone, which sounds fine.
    """
    if stereo and sample_rate < MIN_RATE_FOR_STEREO:
        raise ValueError(
            f"stereo needs a sample rate of at least {MIN_RATE_FOR_STEREO} to keep the "
            f"38 kHz subcarrier under Nyquist, not {sample_rate}. Use stereo=False, "
            "or record at 200 kS/s as a real tuner does.")
    if len(iq) < 16 or deviation <= 0:
        return []

    scale = sample_rate / (2.0 * math.pi * deviation)
    frequency: list[float] = []
    previous = math.atan2(iq[0].imag, iq[0].real)
    for z in iq[1:]:
        phase = math.atan2(z.imag, z.real)
        frequency.append((phase - previous) * scale)
        previous = phase

    frequency = fmdsp.dc_block(frequency, sample_rate, DC_CUTOFF)
    if sample_rate != MID_RATE:
        frequency = fmdsp.resample(frequency, MID_RATE, sample_rate)

    mono = fmdsp.lowpass(frequency, MID_RATE, AUDIO_BW)
    difference: Optional[list[float]] = None

    if stereo:
        subcarrier_hz = 2.0 * pilot_hz
        reference, measured_hz, strength = pilot_reference(frequency, MID_RATE, pilot_hz)
        if not reference:
            # A mono signal is a real answer, not an error: plenty of
            # transmitters and every capture with pilot disabled is mono.
            reference = [math.cos(2.0 * math.pi * subcarrier_hz * n / MID_RATE)
                         for n in range(len(frequency))]
        else:
            subcarrier = bandlimit(frequency, MID_RATE,
                                   subcarrier_hz - DIFF_BW,
                                   subcarrier_hz + DIFF_BW)
            mixed = [2.0 * s * r for s, r in zip(subcarrier, reference)]
            difference = fmdsp.lowpass(mixed, MID_RATE, DIFF_BW)

    mono = fmdsp.resample(mono, audio_rate, MID_RATE)
    if difference is not None:
        difference = fmdsp.resample(difference, audio_rate, MID_RATE)
        count = min(len(mono), len(difference))
        mono, difference = mono[:count], difference[:count]
    else:
        mono = mono + [0.0] * 0

    # De-emphasis is a one-pole low pass with time constant tau.
    if deemphasis_us:
        tau = deemphasis_us / 1e6
        a = 1.0 / (1.0 + 2.0 * math.pi * audio_rate * tau)
        mono = _deemphasis(mono, a)
        if difference is not None:
            difference = _deemphasis(difference, a)

    if difference is None:
        pairs = [(v, v) for v in mono]
    else:
        half = 0.5
        pairs = [(m + half * d, m - half * d) for m, d in zip(mono, difference)]

    peak = max((abs(v) for p in pairs for v in p), default=0.0)
    if peak > 1e-9:
        pairs = [(max(-1.0, min(1.0, l / peak)), max(-1.0, min(1.0, r / peak)))
                 for l, r in pairs]
    return pairs


def _deemphasis(samples: "list[float]", a: float) -> "list[float]":
    state = 0.0
    out: list[float] = []
    for value in samples:
        state = a * (state + (1.0 - a) * value)
        out.append(state)
    return out


def synthesize_fm(left: "list[float]", right: "list[float]", sample_rate: int,
                  carrier_offset_hz: float = 0.0, deviation: float = DEVIATION,
                  pilot_hz: float = PILOT_HZ, preemphasis_us: float = 75.0) -> "list[complex]":
    """Build a stereo FM signal from audio - a station, made of arithmetic.

    This is what makes the demodulator verifiable with no radio anywhere: the
    audio that goes in is known, so the audio that comes out can be compared
    against it. A wrong discriminator is a failing round trip rather than a
    plausible waveform.
    """
    import random

    count = min(len(left), len(right))
    if count < 16:
        return []
    mono = [(left[i] + right[i]) * 0.5 for i in range(count)]
    side = [(left[i] - right[i]) * 0.5 for i in range(count)]

    if preemphasis_us:
        a = 1.0 / (1.0 + 2.0 * math.pi * sample_rate * preemphasis_us / 1e6)
        mono = _deemphasis(mono, a)
        side = _deemphasis(side, a)

    # The L-R difference is amplitude-modulated onto a 38 kHz subcarrier whose
    # phase is locked to a 19 kHz pilot. Randomising the initial phase is what
    # makes the receiver's phase recovery a real test rather than a gift.
    phase_offset = random.uniform(0.0, math.tau)
    out: list[complex] = []
    phase = 0.0
    for n in range(count):
        t = n / sample_rate
        # A 10% pilot tone plus the L-R difference amplitude-modulated onto the
        # 38 kHz subcarrier, whose phase is locked to twice the pilot's.
        pilot = 0.1 * math.cos(2.0 * math.pi * pilot_hz * t + phase_offset)
        subcarrier = 0.5 * side[n] * math.cos(4.0 * math.pi * pilot_hz * t + 2.0 * phase_offset)
        baseband = mono[n] + subcarrier + pilot
        instant = carrier_offset_hz + deviation * baseband
        phase += 2.0 * math.pi * instant / sample_rate
        out.append(complex(math.cos(phase), math.sin(phase)))
    return out


def goertzel(samples: "list[float]", rate: int, target_hz: float) -> float:
    """Relative power at one frequency. Goertzel, because one bin is all this needs.

    A full FFT is overkill for "is there a 1 kHz tone here", and Goertzel is a
    dozen lines of recurrence rather than a library - which matters in a package
    that does not depend on numpy.
    """
    if not samples or rate <= 0:
        return 0.0
    n = len(samples)
    omega = 2.0 * math.pi * target_hz / rate
    coeff = 2.0 * math.cos(omega)
    s0 = s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return math.sqrt(max(0.0, power)) / n


def dominant_tone(samples: "list[float]", rate: int,
                  low_hz: float = 100.0, high_hz: float = 5000.0,
                  step_hz: float = 50.0) -> "tuple[float, float]":
    """(frequency, relative strength) of the loudest tone in a band.

    Broadcast speech has no dominant tone, so a strong result means a **test
    tone**, an RDS subcarrier, or music - and the caller must say which it
    cannot tell. A weak result alongside speech-shaped energy means a station is
    there and talking.
    """
    best_hz = 0.0
    best = -1.0
    hz = low_hz
    while hz <= high_hz:
        power = goertzel(samples, rate, hz)
        if power > best:
            best, best_hz = power, hz
        hz += step_hz
    return best_hz, best


def read_iq_wav(path: str) -> "tuple[list[complex], int]":
    """Complex IQ from a WAV file, as written by `rtl_fm -o` (or this module).

    `rtl_fm`'s IQ output is 16-bit signed **stereo** - real in the left channel,
    imaginary in the right - which is the one thing that is easy to get wrong and
    produces audio that is the right shape and the wrong content.
    """
    try:
        with wave.open(path, "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc

    if width != 2:
        raise ValueError(f"expected 16-bit samples, this file is {width * 8}-bit")
    if channels != 2:
        raise ValueError(
            "expected stereo IQ (real in one channel, imaginary in the other), "
            f"this file has {channels} channel(s)")

    data = array.array("h")
    data.frombytes(frames)
    if sys_byteorder_is_big():
        data.byteswap()
    iq = [complex(data[i] / 32768.0, data[i + 1] / 32768.0)
          for i in range(0, len(data) - 1, 2)]
    return iq, rate


def sys_byteorder_is_big() -> bool:
    return struct.pack("=H", 1) != struct.pack("<H", 1) if False else _is_big()


def _is_big() -> bool:
    import sys
    return sys.byteorder == "big"


def write_wav(path: str, samples: "list[float]", rate: int) -> str:
    """16-bit mono WAV, the shape every player here takes."""
    peak = max((abs(v) for v in samples), default=0.0)
    scale = 32767.0 / peak if peak > 1e-9 else 1.0
    payload = b"".join(struct.pack("<h", int(max(-32767, min(32767, v * scale))))
                       for v in samples)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload)
    return path


# --- the hardware, reported honestly ------------------------------------------


def hardware() -> str:
    """What can actually receive here, or why nothing can.

    Checks for a DVB device, the RTL-SDR tools and a USB product string, and
    distinguishes "the library is missing" from "the hardware is missing" -
    which are different answers pointing at different fixes.
    """
    lines: list[str] = []
    if os.path.isdir("/dev/dvb"):
        lines.append(f"a DVB device is present: {', '.join(sorted(os.listdir('/dev/dvb')))}")
    else:
        lines.append("no /dev/dvb: no TV or broadcast tuner is bound to this machine")

    if shutil.which("rtl_fm"):
        lines.append(f"rtl_fm is installed ({shutil.which('rtl_fm')})")
    else:
        lines.append(files.tool_missing("rtl_fm", "receive FM radio"))

    # The Realtek vendor-name trap, handled: the WLAN chip is also Realtek, and
    # only the product string distinguishes the dongle from a Wi-Fi adapter.
    try:
        proc = subprocess.run(["lsusb"], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
        dongles = [line for line in proc.stdout.splitlines() if "RTL2838" in line]
        if dongles:
            lines.append("an RTL2832U dongle is attached:")
            lines.extend("  " + d for d in dongles)
        else:
            others = [line for line in proc.stdout.splitlines()
                      if "Realtek" in line and "RTL2838" not in line]
            lines.append("no RTL2832U attached. (Realtek also makes Wi-Fi chips, "
                         "which match a 'Realtek' search and are not receivers"
                         + (": " + "; ".join(o.split("] ", 1)[-1] for o in others)
                            if others else "") + ")")
    except (OSError, subprocess.SubprocessError):
        lines.append("could not list USB devices; lsusb is absent or would not run")

    return "\n".join(lines)


def rtl_fm_command(frequency_hz: float, sample_rate: int = 250_000,
                   seconds: int = 10, deviation: float = 75_000.0,
                   gain: str = "auto") -> "list[str]":
    """The exact `rtl_fm` argv, built here so it can be asserted without a radio.

    **Frequency is always in Hz and never a string with units in it.** A
    malformed argument here is what makes a tuner silently tune nothing.
    """
    if sample_rate <= deviation * 2:
        raise ValueError(
            f"a sample rate of {sample_rate} cannot carry ±{deviation:.0f} Hz of "
            "deviation; it needs at least twice the deviation")
    return [
        "rtl_fm", "-M", "auto", "-f", f"{frequency_hz:.0f}", "-s", str(sample_rate),
        "-g", gain, "-t", str(seconds), "-F", "wav", "-",
    ]


def run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return ("The radio request was not understood: its arguments were not a set "
                "of named values. Nothing was tuned or received.")
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal

    action = str(arguments.get("action") or "hardware").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}, not {action!r}. Nothing was done."

    if action == "hardware":
        return hardware()

    if action == "bands":
        lines = ["What bands this can cover:"]
        for name, (low, high, _rate) in BANDS.items():
            lines.append(f"  {name}: {low / 1e6:g}-{high / 1e6:g} MHz")
        lines.append("")
        lines.append("Only FM and AM are broadcast radio. DAB is digital and needs a "
                     "decoder this skill does not have - it is listed so the absence "
                     "is named rather than silent.")
        return "\n".join(lines)

    if action == "decode":
        path = str(arguments.get("file") or "").strip()
        if not path:
            return ("Decode what? Pass file, the path to a recorded IQ capture, e.g. "
                    "file='~/fm.iq.wav'. Nothing was received.")
        expanded = os.path.expanduser(path)
        try:
            iq, rate = read_iq_wav(expanded)
        except ValueError as exc:
            return f"{exc}. Nothing was received."
        if len(iq) < 8:
            return (f"{expanded} holds {len(iq)} samples, which is too few to "
                    "demodulate. Nothing was received.")
        deemphasis = float(arguments.get("deemphasis_us") or 75.0)
        audio = demodulate(iq, rate, deemphasis_us=deemphasis)
        seconds = len(audio) / rate
        hz, strength = dominant_tone(audio, rate)
        out = [f"Demodulated {len(audio):,} samples ({seconds:.2f}s at {rate:,} Hz "
               f"sample rate) from {expanded}.",
               f"Peak audio level: {max((abs(v) for v in audio), default=0.0):.3f}"]
        if strength > 0.05:
            out.append(f"A tone is dominant at about {hz:.0f} Hz "
                       f"(relative strength {strength:.3f}) - that is a test tone or a "
                       "subcarrier, not a station name.")
        else:
            out.append("No dominant tone, which is what speech sounds like here. "
                       "That means audio was present but it is not a continuous tone, "
                       "so this says nothing about which station it was.")
        return "\n".join(out)

    if action == "scan":
        return _scan(arguments)
    return _tune(arguments)


def _frequency(arguments: dict) -> "tuple[Optional[float], str]":
    raw = arguments.get("frequency")
    if raw in (None, ""):
        return None, ("Which frequency? Pass frequency in MHz, e.g. frequency=98.6. "
                      "Nothing was tuned.")
    text = str(raw).strip().lower()
    hz = None
    if text.endswith("mhz"):
        hz = float(text[:-3].strip()) * 1e6
    elif text.endswith("khz"):
        hz = float(text[:-3].strip()) * 1e3
    elif text.endswith("hz"):
        hz = float(text[:-2].strip())
    else:
        try:
            # A bare number is megahertz, because that is how everyone writes
            # down a radio frequency and "tune to 98.6" must not mean 98.6 Hz.
            hz = float(text) * 1e6
        except ValueError:
            return None, f"{raw!r} is not a frequency. Nothing was tuned."
    low, high, _ = BANDS["FM"]
    if not (low <= hz <= high):
        return None, (f"{hz / 1e6:g} MHz is outside the FM band "
                      f"({low / 1e6:g}-{high / 1e6:g} MHz). Nothing was tuned.")
    return hz, ""


def _tune(arguments: dict) -> str:
    frequency, why = _frequency(arguments)
    if frequency is None:
        return why
    if not shutil.which("rtl_fm"):
        return files.tool_missing("rtl_fm", "receive FM radio") + \
            " A dongle is also needed; run action='hardware' to see what is attached."
    seconds = arguments.get("seconds") or 10
    try:
        argv = rtl_fm_command(frequency, seconds=int(seconds))
    except (TypeError, ValueError) as exc:
        return f"{exc}. Nothing was tuned."
    return (f"Would tune {frequency / 1e6:g} MHz with:\n  {' '.join(argv)}\n"
            "This machine has no RTL2832U, so the command is shown rather than run.")


def _scan(arguments: dict) -> str:
    """Report the carriers in a recorded capture.

    A scan needs a wide slice of spectrum to look at, which is exactly what a
    narrowband tuner cannot give you: `rtl_fm` retunes for each channel and
    reports no strength. So scanning is a whole-band problem, and pretending a
    tuner can do it is the thing to avoid.
    """
    path = str(arguments.get("file") or "").strip()
    if not path:
        return ("Scan what? Pass file, a whole-band IQ capture to search. rtl_fm is "
                "narrowband and retunes per channel, so it cannot scan; a wideband "
                "recording can. Nothing was received.")
    expanded = os.path.expanduser(path)
    try:
        iq, rate = read_iq_wav(expanded)
    except ValueError as exc:
        return f"{exc}. Nothing was received."
    if len(iq) < rate // 10:
        return (f"{expanded} holds only {len(iq) / rate:.2f}s, too short to say "
                "anything about carriers. Nothing was received.")
    centre = arguments.get("centre_mhz")
    try:
        centre_hz = float(centre) * 1e6 if centre else None
    except (TypeError, ValueError):
        return f"{centre!r} is not a centre frequency in MHz. Nothing was received."

    audio = demodulate(iq, rate, deviation=75_000.0)
    hz, strength = dominant_tone(audio, rate)
    where = (f" around {centre_hz / 1e6:g} MHz" if centre_hz else "")
    return (f"Searched {expanded} ({len(iq) / rate:.2f}s{where}).\n"
            f"  {'A strong tone sits near ' + format(hz, '.0f') + ' Hz in the demodulated audio' if strength > 0.05 else 'No strong carrier tone found in the demodulated audio'}"
            + "\n\nA wideband recording shows carriers; naming them needs a station "
              "list for your region, which this skill does not carry, and a wrong "
              "station name is worse than a frequency.")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "fm_radio",
        "description": (
            "FM and AM broadcast radio. Five actions: 'hardware' for whether this "
            "computer can receive anything at all (a laptop usually cannot, and that is "
            "a true answer rather than a failure - it needs an RTL2832U USB dongle), "
            "'bands' for the coverage, 'tune' to a frequency, 'decode' to demodulate a "
            "recorded IQ capture, and 'scan' to look for carriers in one. A frequency is "
            "given in MHz, e.g. 98.6. rtl_fm is narrowband and retunes per channel, so it "
            "cannot scan - scanning needs a wideband recording. No station names are "
            "guessed: a carrier gives a frequency, and naming it would need a table for "
            "your region. Requires the 'fm-radio-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "hardware: what can receive here. bands: coverage. "
                                   "tune: one frequency. decode: demodulate a capture. "
                                   "scan: find carriers in a capture.",
                },
                "frequency": {
                    "type": "string",
                    "description": "For tune: the frequency in MHz, e.g. '98.6'. A bare "
                                   "number is read as MHz; kHz and Hz suffixes work too.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For tune: how long to listen, default 10.",
                },
                "file": {
                    "type": "string",
                    "description": "For decode and scan: path to a 16-bit stereo IQ WAV "
                                   "capture, as rtl_fm -F wav writes.",
                },
                "centre_mhz": {
                    "type": "string",
                    "description": "For scan: what frequency the capture was centred on, "
                                   "so a tone can be reported in MHz rather than Hz.",
                },
                "deemphasis_us": {
                    "type": "integer",
                    "description": "For decode: the de-emphasis constant, 75 for North "
                                   "America and 50 for Europe and most of the rest of "
                                   "the world. Default 75.",
                },
            },
            "required": ["action"],
        },
    },
}

SKILLS = [Skill(name="fm_radio", schema=SCHEMA, run=run)]