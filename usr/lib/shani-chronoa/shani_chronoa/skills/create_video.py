"""Skill: create a video - a slideshow from pictures, or a simple clip from a
colour and duration - with ffmpeg. The video-side of generate_image.

The result is a new file beside the inputs; the originals are never touched.
Nothing leaves the machine.
"""

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_video",
        "description": (
            "Create a video: a slideshow from a list of pictures (each shown for "
            "a few seconds), or a simple clip of a colour for a duration. Writes "
            "a new .mp4 next to the inputs; never overwrites."
        ),
        "parameters": {"type": "object", "properties": {
            "images": {"type": "array", "items": {"type": "string"},
                        "description": "Pictures to show in order, each for 'seconds' long."},
            "seconds": {"type": "number", "description": "How long each picture shows (default 3)."},
            "color": {"type": "string", "description": "For a colour clip: a colour name or hex."},
            "duration": {"type": "number", "description": "For a colour clip: length in seconds."},
            "width": {"type": "integer", "description": "Output width in pixels (default 640)."},
        }},
    },
}


def _run(arguments: dict) -> str:
    if not shutil.which("ffmpeg"):
        return "Creating a video needs ffmpeg, which is not not installed."
    images = arguments.get("images") or []
    color = (arguments.get("color") or "").strip()
    duration = arguments.get("duration")
    seconds = arguments.get("seconds") or 3
    width = arguments.get("width") or 640

    if not images and not color:
        return "Give me pictures to show, or a colour and a duration."

    if images:
        resolved = []
        for raw in images:
            try:
                p = files.resolve(str(raw).strip())
            except files.PathProblem as e:
                return str(e)
            if not p.is_file():
                return f"{p} is not a file."
            if p.suffix.lower() not in _IMAGES:
                return f"{p.name} is not a picture I can put in a video."
            resolved.append(p)
        if not resolved:
            return "No usable pictures were given."
        try:
            seconds = max(0.5, min(float(seconds), 60))
        except (TypeError, ValueError):
            seconds = 3
        try:
            width = max(16, min(int(width), 3840))
        except (TypeError, ValueError):
            width = 640
        # ffmpeg concat demuxer: one file per line, each with a duration
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listfile:
            for p in resolved:
                listfile.write(f"file '{p}'\n")
                listfile.write(f"duration {seconds}\n")
            listfile.write(f"file '{resolved[-1]}'\n")
            listpath = listfile.name
        target = resolved[0].with_name(f"{resolved[0].stem}-slideshow.mp4")
        n = 1
        while target.exists():
            target = resolved[0].with_name(f"{resolved[0].stem}-slideshow-{n}.mp4")
            n += 1
        argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "concat", "-safe", "0", "-i", listpath,
                "-vf", f"scale={width}:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-r", "15", str(target)]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False)
        except subprocess.TimeoutExpired:
            target.unlink(missing_ok=True)
            return "The slideshow took longer than ten minutes, so I stopped it."
        finally:
            import os
            os.unlink(listpath)
        if r.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            return f"ffmpeg could not make the slideshow: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
        return f"Saved {target} ({target.stat().st_size / 1e6:.1f} MB) from {len(resolved)} picture(s)."

    # colour clip
    try:
        duration = max(0.5, min(float(duration or 5), 300))
    except (TypeError, ValueError):
        duration = 5
    try:
        width = max(16, min(int(width), 3840))
    except (TypeError, ValueError):
        width = 640
    target = files.data_home() / "shani-chronoa" / "videos" / f"clip-{int(duration)}s.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while target.exists():
        target = target.with_name(f"clip-{int(duration)}s-{n}.mp4")
        n += 1
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={width}x{int(width*9/16)}:d={duration}:r=15",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        return "The clip took longer than five minutes, so I stopped it."
    if r.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return f"ffmpeg could not make the clip: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
    return f"Saved {target} ({target.stat().st_size / 1e6:.1f} MB)."


POST_CONDITION = None

SKILLS = [Skill(name="create_video", schema=_SCHEMA, run=_run)]
