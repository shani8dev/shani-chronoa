"""An OpenCV effect over every frame of a video, keeping its sound.

Finding a video's moments needs no OpenCV and is `video_frames` (ffmpeg).
OpenCV's wheel bundles FFmpeg, so `cv2.VideoCapture` reads ordinary video
files; `apply_effect` writes a new video frame by frame and then gives it the
original's sound back with ffmpeg (OpenCV writes pictures only); without ffmpeg
the result is silent and the answer says so.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from shani_chronoa.opencv import runtime

#: never read more than this much of a video, so a feature-length file does not run for an hour
MAX_SECONDS = 1800


def apply_effect(path: Path, effect, out_path: Path, frame_limit: Optional[int] = None) -> str:
    """Write `out_path` with `effect` on every frame; returns a note about the sound."""
    cv2, _np = runtime.load()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"{path.name} is not a video OpenCV can read")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    silent = out_path.with_name(out_path.stem + ".frames.mp4")
    writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n = 0
    try:
        while frame_limit is None or n < frame_limit:
            ok, frame = cap.read()
            if not ok or n / fps > MAX_SECONDS:
                break
            writer.write(effect(frame))
            n += 1
    finally:
        cap.release()
        writer.release()
    if n == 0:
        silent.unlink(missing_ok=True)
        raise ValueError(f"no frames could be read from {path.name}")
    if shutil.which("ffmpeg"):
        try:  # claimed first: ffmpeg's -n exits 0 without writing when the file exists
            with open(out_path, "x"):
                pass
        except FileExistsError:
            silent.unlink(missing_ok=True)
            raise ValueError(f"{out_path} already exists")
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(silent), "-i", str(path), "-map", "0:v:0",
                               "-map", "1:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                               "-c:a", "copy", str(out_path)], capture_output=True, text=True, timeout=600)
        silent.unlink(missing_ok=True)
        if proc.returncode != 0:
            out_path.unlink(missing_ok=True)
            raise ValueError(f"ffmpeg could not finish the video: {proc.stderr.strip()[-200:]}")
        return f"{n} frames, with the original sound"
    silent.rename(out_path)
    return f"{n} frames, without sound (ffmpeg is not installed to copy it over)"
