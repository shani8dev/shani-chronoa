"""Who spoke when: speaker diarization with sherpa-onnx, for transcripts that say who said what.

Telling voices apart needs a model - nothing on the system does it - so this is
an optional extra: pyannote's segmentation 3.0 (MIT; where speech is and where
it changes hands) and 3D-Speaker's CAM++ English embedding (Apache-2.0; what
each voice sounds like), run by the sherpa-onnx release Kokoro also uses.
Measured on this machine: 3.4 s for 34 s of two people talking.

`recordings.assign_speakers` gives each of whisper's timed segments to the
speaker whose turns overlap it most.

Sizes and sha256 are from downloads of each file, matching the sizes GitHub
publishes for them (2026-10-02).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from shani_chronoa import files, sherpa
from shani_chronoa.stt_provision import ModelSpec, install_verified

SEGMENTATION = ModelSpec("pyannote-segmentation-3-0", "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2", 6_958_444,
                         "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
                         "pyannote segmentation 3.0 (MIT)", f"{sherpa.MODELS_URL}/speaker-segmentation-models")
EMBEDDING = ModelSpec("campplus-en", "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx", 29_596_978,
                      "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b",
                      "3D-Speaker CAM++ English (Apache-2.0)", f"{sherpa.MODELS_URL}/speaker-recongition-models")
PROGRAM = "sherpa-onnx-offline-speaker-diarization"
#: distance under which two stretches of speech are the same person (sherpa-onnx's example value)
THRESHOLD = 0.9
_LINE = re.compile(r"^\s*([0-9.]+)\s+--\s+([0-9.]+)\s+speaker_(\d+)\s*$", re.M)


def model_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "speakers"


def _segmentation_model() -> Path:
    return model_dir() / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"


def installed() -> bool:
    return bool(sherpa.binary(PROGRAM)) and _segmentation_model().is_file() and (model_dir() / EMBEDDING.filename).is_file()


def problem() -> str:
    if installed():
        return ""
    return "telling speakers apart is not set up (Chronoa's setup, More -> Who said what)"


def install(progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> None:
    sherpa.install(progress=progress, config=config, transport=transport, label="speakers")
    sherpa.install_archive(SEGMENTATION, model_dir(), _segmentation_model(), progress=progress, config=config,
                           transport=transport, label="speakers")
    install_verified(EMBEDDING, model_dir(), config=config, progress=progress, transport=transport, label="speakers")


def parse(output: str) -> List[Tuple[float, float, str]]:
    """sherpa-onnx's '0.031 -- 23.858 speaker_00' lines as (start, end, 'Speaker 1')."""
    return [(float(a), float(b), f"Speaker {int(n) + 1}") for a, b, n in _LINE.findall(output)]


def diarize(wav: Path, count: Optional[int] = None, timeout: float = 1800) -> List[Tuple[float, float, str]]:
    """Speaker turns in a 16 kHz mono WAV. `count` when the number of people is known (it helps)."""
    args = [sherpa.binary(PROGRAM), f"--segmentation.pyannote-model={_segmentation_model()}",
            f"--embedding.model={model_dir() / EMBEDDING.filename}", "--print-args=false"]
    args += [f"--clustering.num-clusters={int(count)}"] if count else [f"--clustering.cluster-threshold={THRESHOLD}"]
    proc = subprocess.run(args + [str(wav)], capture_output=True, text=True, timeout=timeout)
    turns = parse(proc.stdout + "\n" + proc.stderr)
    if proc.returncode != 0 and not turns:
        raise RuntimeError(f"telling speakers apart failed: {proc.stderr.strip()[-200:]}")
    return turns
