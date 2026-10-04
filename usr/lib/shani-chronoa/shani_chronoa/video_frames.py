"""A video's moments - the first frame and each scene change - as pictures, with ffmpeg alone.

ffmpeg is on every ShaniOS install, and its `select` filter scores how much
each frame differs from the one before (`scene`, 0-1): a cut, or a scene that
has moved on. Those frames are what the vision model describes, OCR reads, or
OpenCV's detectors count - so going through a video needs no extra download,
only the thing that then looks at the frames.

A video with no cuts (one long shot) gets evenly spaced frames instead, so
"what happens in this video" still has something to look at.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, NamedTuple

SCENE_CHANGE = 0.3
#: never read more than this much of a video, so a feature-length file does not run for an hour
MAX_SECONDS = 1800
_PTS = re.compile(r"pts_time:([0-9.]+)")


class Frame(NamedTuple):
    seconds: float
    path: Path


def available() -> bool:
    return bool(shutil.which("ffmpeg"))


def duration(path: Path) -> float:
    if not shutil.which("ffprobe"):
        return 0.0
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, timeout=60)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _grab(path: Path, out_dir: Path, vf: str, limit: int) -> List[Frame]:
    proc = subprocess.run(["ffmpeg", "-v", "info", "-nostdin", "-t", str(MAX_SECONDS), "-i", str(path),
                           "-vf", f"{vf},showinfo", "-fps_mode", "vfr", "-frames:v", str(limit),
                           str(out_dir / "frame-%03d.png")], capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise ValueError(f"ffmpeg could not read {path.name}: {proc.stderr.strip().splitlines()[-1][:200] if proc.stderr.strip() else 'no message'}")
    times = [float(t) for t in _PTS.findall(proc.stderr)]
    files = sorted(out_dir.glob("frame-*.png"))
    return [Frame(t, f) for t, f in zip(times, files)]


def keyframes(path: Path, out_dir: Path, limit: int = 12) -> List[Frame]:
    """Up to `limit` moments of `path` written into `out_dir` as PNGs, earliest first."""
    if not available():
        raise ValueError("ffmpeg is not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = _grab(path, out_dir, f"select='eq(n,0)+gt(scene,{SCENE_CHANGE})'", limit)
    if len(frames) < 2:  # one long shot (only the first frame): spread frames over it instead
        for f in out_dir.glob("frame-*.png"):
            f.unlink()
        length = min(duration(path), MAX_SECONDS) or 60.0
        every = max(1.0, length / max(1, limit))
        frames = _grab(path, out_dir, f"fps=1/{every:.3f}", limit)
    return frames


def temporary_keyframes(path: Path, limit: int = 12) -> "tuple[tempfile.TemporaryDirectory, List[Frame]]":
    """Keyframes in a private temporary directory the caller cleans up (for looking, not keeping)."""
    tmp = tempfile.TemporaryDirectory(prefix="chronoa-frames-")
    try:
        return tmp, keyframes(path, Path(tmp.name), limit)
    except Exception:
        tmp.cleanup()
        raise
