"""Generate the three shipped mascot sounds as tiny synthsized WAVs.

Coucou's own receipt names its sounds by *event* - finish.wav, approval.mp3,
greet.m4a - files a person can replace in a folder (HARVEST PART 15). These are
our three, synthesised so the repo ships no third-party audio at all:

  greet.wav    two rising notes - someone wants your attention
  approval.wav one firm ping - a gate is asking for a decision
  finish.wav   a three-note rise-and-settle - the turn answered

44.1 kHz 16-bit mono so any player (pw-play, paplay, or a browser) takes them
without asking, and each well under 0.4 s so a rapid turn never stacks noise.
Regenerate only with `python3 tools/make_mascot_sounds.py`; the WAVs are
committed, not built at install.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

RATE = 44_100
OUT = Path(__file__).resolve().parents[1] / "usr/share/shani-chronoa/mascot-sounds"


def _tone(freqs, seconds, volume=0.5):
    """A sum of sines with a one-shot attack and a long, quiet tail."""
    n = int(RATE * seconds)
    out = bytearray()
    for i in range(n):
        t = i / RATE
        # 12 ms attack, longer semi-exponential decay - a face, not a beep.
        env = min(1.0, t / 0.012) * math.exp(-3.0 * t)
        s = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
        v = int(volume * env * s * 32767)
        out += struct.pack("<h", v)
    # 30 ms of silence, so players never cut the envelopes' tails.
    out += struct.pack("<h", 0) * int(0.03 * RATE)
    return out


def _write(name, chunks):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(chunks))
    print(f"{path.relative_to(OUT.parents[3])}  {path.stat().st_size} bytes")


# The friendly knock: someone's tool wants to ask.
_write("greet.wav", [_tone((523, 784), 0.16)])

# The gate: one ping, held a touch longer, asking for a decision.
_write("approval.wav", [_tone((660, 990), 0.22)])

# The answer: rise, rise, settle - the turn answered.
_write("finish.wav", [
    _tone((523, 659), 0.12, 0.45),
    _tone((659, 784), 0.12, 0.45),
    _tone((784, 659, 523), 0.22, 0.5),
])
