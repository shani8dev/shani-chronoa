"""What a sound is: the doorbell, a knock, a dog, a baby, an alarm - AudioSet's 527 classes.

Identifying a sound needs a model (nothing on the system can tell a doorbell
from a dog), so this is an optional extra: CED-tiny (Apache-2.0, 28.5 MB), run
by the sherpa-onnx release Kokoro also uses. Measured on this machine: 0.03 s
for 10 s of audio, so listening costs almost nothing once something is recorded.

The microphone is the user's most private sensor. This module only labels a
recording it is given; `senses/sounds.py` decides when to record, behind its
own consent key, off by default.

Size and sha256 are from a download matching the size GitHub publishes (2026-10-02).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional

from shani_chronoa import files, sherpa
from shani_chronoa.stt_provision import ModelSpec

MODEL = ModelSpec("ced-tiny", "sherpa-onnx-ced-tiny-audio-tagging-2024-04-19.tar.bz2", 28_531_989,
                  "84baf315b57d61aa69480c4fee878dab54cbc7be3e877db334e65d8b087e23c3",
                  "CED-tiny audio tagging (Apache-2.0)", f"{sherpa.MODELS_URL}/audio-tagging-models")
PROGRAM = "sherpa-onnx-offline-audio-tagging"
_EVENT = re.compile(r'AudioEvent\(name="([^"]+)", index=(\d+), prob=([0-9.eE+-]+)\)')


class Heard(NamedTuple):
    name: str
    probability: float


def _dir() -> Path:
    return files.data_home() / "shani-chronoa" / "sounds" / "sherpa-onnx-ced-tiny-audio-tagging-2024-04-19"


def installed() -> bool:
    return bool(sherpa.binary(PROGRAM)) and (_dir() / "model.int8.onnx").is_file()


def problem() -> str:
    return "" if installed() else "recognising sounds is not set up (Chronoa's setup, More -> Sounds)"


def install(progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> None:
    sherpa.install(progress=progress, config=config, transport=transport, label="sounds")
    sherpa.install_archive(MODEL, _dir().parent, _dir() / "model.int8.onnx", progress=progress, config=config,
                           transport=transport, label="sounds")


def parse(output: str) -> List[Heard]:
    return [Heard(name, float(p)) for name, _i, p in _EVENT.findall(output)]


def tag(wav: Path, top: int = 5, timeout: float = 120) -> List[Heard]:
    """The most likely sounds in a 16 kHz mono WAV, most likely first."""
    proc = subprocess.run([sherpa.binary(PROGRAM), f"--ced-model={_dir() / 'model.int8.onnx'}",
                           f"--labels={_dir() / 'class_labels_indices.csv'}", f"--top-k={int(top)}",
                           "--print-args=false", str(wav)], capture_output=True, text=True, timeout=timeout)
    heard = parse(proc.stdout + "\n" + proc.stderr)
    if proc.returncode != 0 and not heard:
        raise RuntimeError(f"recognising the sound failed: {proc.stderr.strip()[-200:]}")
    return sorted(heard, key=lambda h: -h.probability)


def describe(heard: List[Heard], floor: float = 0.3) -> str:
    kept = [h for h in heard if h.probability >= floor]
    if not kept:
        return "nothing it recognises clearly"
    return ", ".join(f"{h.name.lower()} ({h.probability:.0%})" for h in kept)


def _record(wav: Path, seconds: float) -> str:
    """Record `seconds` of the default microphone as 16 kHz mono WAV; '' on success, else why not.

    ffmpeg through PipeWire's Pulse interface first (every install has both, and
    `-t` stops it exactly), arecord as the fallback.
    """
    import shutil
    attempts = []
    if shutil.which("ffmpeg"):
        attempts.append(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "pulse", "-i", "default",
                         "-t", f"{seconds:.1f}", "-ac", "1", "-ar", "16000", str(wav)])
    if shutil.which("arecord"):
        attempts.append(["arecord", "-q", "-d", str(max(1, int(round(seconds)))), "-f", "S16_LE", "-r", "16000",
                         "-c", "1", str(wav)])
    if not attempts:
        return "neither ffmpeg nor arecord is installed to record with"
    last = ""
    for argv in attempts:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=seconds + 15)
        except subprocess.TimeoutExpired:
            last = f"{argv[0]} did not stop"
            continue
        if proc.returncode == 0 and wav.is_file() and wav.stat().st_size > 1000:
            return ""
        last = (proc.stderr.strip().splitlines() or [f"{argv[0]} recorded nothing"])[-1][:160]
    return f"the microphone could not be recorded: {last}"


def listen(seconds: float = 3.0, top: int = 5) -> List[Heard]:
    """What can be heard right now. The recording is labelled and deleted; nothing of it is kept."""
    import tempfile
    if not 1.0 <= seconds <= 30.0:
        raise ValueError("listen for between 1 and 30 seconds")
    with tempfile.TemporaryDirectory(prefix="chronoa-listen-") as tmp:
        wav = Path(tmp) / "heard.wav"
        problem = _record(wav, seconds)
        if problem:
            raise RuntimeError(problem)
        return tag(wav, top=top)


def matches(heard: List[Heard], wanted: str, floor: float) -> Optional[Heard]:
    """The heard sound whose name contains `wanted` (case-insensitive) at `floor` or more, else None."""
    wanted = wanted.strip().lower()
    return next((h for h in heard if wanted in h.name.lower() and h.probability >= floor), None)
