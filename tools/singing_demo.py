"""Make something listenable, and measure whether the contour did anything.

Run inside a container that has espeak-ng, sox, soundtouch and rubberband. Writes
WAVs to the output directory and prints what each one is, because a demo nobody
can check is a demo nobody can trust.

The measurement matters as much as the audio: a rising contour that produces a
file of the right size has still done nothing. So a steady 220 Hz tone goes
through the same code path and its frequency is counted from zero crossings
before and after, which is a real check that the pitch moved by the amount asked
for and not merely that a file appeared.
"""

from __future__ import annotations

import math
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import singing, voice_style  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/singing-demo")
OUT.mkdir(parents=True, exist_ok=True)
TEXT = ("Chronoa is a digital organism. It hears, it sees, it remembers, "
        "and it tells you what it is doing right now.")


def run(argv):
    done = subprocess.run([str(a) for a in argv], capture_output=True)
    if done.returncode != 0:
        raise SystemExit(f"{argv[0]} failed: "
                         f"{done.stderr.decode('utf-8', 'replace').strip()}")
    return done


def speech(path: Path) -> Path:
    """Real speech from espeak-ng, at 16 kHz mono like every engine Chronoa uses."""
    run(["espeak-ng", "-v", "en", "-s", "150", "-p", "45", "-a", "170",
         "-g", "4", "-w", str(path), TEXT])
    return path


def _write_tone(path: Path, hz: float, seconds: float = 3.0,
                rate: int = 22050) -> Path:
    """A real sine, written by hand."""
    frames = bytearray()
    for index in range(int(rate * seconds)):
        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz
                                                         * index / rate)))
    data = bytes(frames)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    path.write_bytes(header + data)
    return path


def dominant_hz(wav: Path, seconds: float = 0.4, offset: float = 0.0) -> float:
    """Frequency by counting zero crossings over a window.

    Crude and sufficient: for a steady tone the crossing count *is* the
    frequency, and it needs no library. Returns 0.0 for silence.
    """
    data = wav.read_bytes()
    rate, samples = 22050, []
    position = data.find(b"fmt ")
    if position < 0:
        return 0.0
    rate = struct.unpack("<I", data[position + 12:position + 16])[0]
    start = data.find(b"data")
    if start < 0:
        return 0.0
    size = struct.unpack("<I", data[start + 4:start + 8])[0]
    body = data[start + 8:start + 8 + size]
    usable = len(body) - (len(body) % 2)
    samples = struct.unpack(f"<{usable // 2}h", body[:usable])
    window = samples[int(offset * rate):int((offset + seconds) * rate)]
    if len(window) < 64:
        return 0.0
    crossings = sum(1 for a, b in zip(window, window[1:]) if a < 0 <= b)
    return crossings / seconds


def main() -> int:
    print(f"writing to {OUT}\n")
    made = []

    raw = speech(OUT / "0-raw-speech.wav")
    print(f"0-raw-speech.wav      {singing.duration_of(str(raw)):.2f}s  "
          f"espeak-ng, no processing at all")

    # The voice style, which is what a reply actually goes through today.
    styled = OUT / "1-styled-assistant.wav"
    effects = voice_style.style_effects(voice_style.resolve_preset("assistant"))
    run(["sox", str(raw), str(styled)] + effects)
    made.append(styled)
    print(f"1-styled-assistant.wav  'assistant' preset: "
          f"{voice_style.describe_effects(voice_style.resolve_preset('assistant'))}")

    # A rising-then-falling pitch contour over the same speech.
    length = singing.duration_of(str(styled))
    points = [singing.Point(0.0, 0.0),
              singing.Point(length * 0.45, 3.0),
              singing.Point(length * 0.95, -1.0)]
    within = singing.within(points, length)
    contoured = OUT / "2-contour-rise-fall.wav"
    singing.apply_contour(str(styled), str(contoured), within)
    made.append(contoured)
    print(f"2-contour-rise-fall.wav  {singing.contour_effects(within)}")

    # The quality upgrade: the same speech, transposed a whole tone up.
    up = OUT / "3-transposed-up-2-semitones.wav"
    singing.transpose(str(styled), 2.0, str(up),
                      backend="soundstretch" if singing._best_transposer()
                      == "soundstretch" else None)
    made.append(up)
    print(f"3-transposed-up-2-semitones.wav  via "
          f"{singing._best_transposer()}")

    # A deliberately narrow, testable contour on a steady tone.
    # Written in Python rather than by `sox synth`, because `sox -n out.wav
    # synth 3 sine 220` does not produce a 220 Hz sine on SoX 14.8 - it produces
    # something with no measurable period, and the measurement below then reports
    # 5313 Hz for a tone that is supposed to be 220. Verified by counting zero
    # crossings, which is how that was found.
    tone = OUT / "4-tone-220hz-original.wav"
    _write_tone(tone, 220.0, seconds=3.0, rate=22050)
    bent = OUT / "5-tone-220hz-rising.wav"
    singing.apply_contour(
        str(tone), str(bent),
        singing.within([singing.Point(0.0, 0.0), singing.Point(1.4, 4.0)], 3.0))
    made += [tone, bent]

    print("\n-- did the pitch actually move? (220 Hz tone, +4 semitones) --")
    for offset in (0.2, 2.6):
        before = dominant_hz(tone, 0.3, offset)
        after = dominant_hz(bent, 0.3, offset)
        semitones = (12 * math.log2(after / before)) if before and after else 0.0
        print(f"  at {offset:>3.1f}s: {before:6.1f} Hz -> {after:6.1f} Hz "
              f"({semitones:+.2f} semitones)")

    print("\n-- support on this machine --")
    for key, value in sorted(singing.singing_support().items()):
        print(f"  {key}: {value}")
    print(f"\nlisten to: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())