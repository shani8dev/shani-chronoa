"""Skill: convert audio, video and pictures - "make this an mp3", "turn this
video into a gif", "shrink this picture to 1280 wide" - with ffmpeg, which
every Shanios install has. The original is never touched and nothing is
overwritten: the result is a new file beside it. Nothing leaves the machine.
"""

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

FORMATS = {
    # target extension -> extra ffmpeg arguments
    "mp3": ["-vn", "-codec:a", "libmp3lame", "-q:a", "2"],
    "ogg": ["-vn", "-codec:a", "libvorbis", "-q:a", "5"],
    "opus": ["-vn", "-codec:a", "libopus", "-b:a", "96k"],
    "wav": ["-vn"], "flac": ["-vn"], "m4a": ["-vn", "-codec:a", "aac", "-b:a", "192k"],
    "mp4": ["-codec:v", "libx264", "-preset", "veryfast", "-crf", "23", "-codec:a", "aac", "-pix_fmt", "yuv420p"],
    "webm": ["-codec:v", "libvpx-vp9", "-crf", "33", "-b:v", "0", "-codec:a", "libopus"],
    "gif": ["-an", "-vf", "fps=12"],
    "png": [], "jpg": ["-q:v", "3"], "webp": ["-quality", "85"],
}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_media",
        "description": "Convert an audio, video or image file to another format (mp3, ogg, opus, wav, "
                       "flac, m4a, mp4, webm, gif, png, jpg, webp), optionally resizing to a width. "
                       "Writes a new file next to the original; never overwrites.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The file to convert."},
            "to": {"type": "string", "enum": sorted(FORMATS)},
            "width": {"type": "integer", "description": "Optional: scale to this width in pixels."},
        }, "required": ["path", "to"]},
    },
}


def _run(arguments: dict) -> str:
    if not shutil.which("ffmpeg"):
        return "Converting media needs ffmpeg, which is not installed."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    to = (arguments.get("to") or "").lower().lstrip(".")
    if to not in FORMATS:
        return f"I can convert to: {', '.join(sorted(FORMATS))}."
    dst = src.with_suffix("." + to)
    n = 1
    while True:
        try:
            # claimed, not just checked: ffmpeg's -n exits 0 without writing
            # when the file exists, so its exit code cannot prove it made this
            with open(dst, "x"):
                break
        except FileExistsError:
            dst = src.with_name(f"{src.stem}-{n}.{to}")
            n += 1
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src), *FORMATS[to]]
    width = arguments.get("width")
    if width:
        w = max(16, min(int(width), 7680))
        if "-vf" in argv:
            i = argv.index("-vf") + 1
            argv[i] += f",scale={w}:-2"
        else:
            argv += ["-vf", f"scale={w}:-2"]
    try:
        r = subprocess.run(argv + [str(dst)], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        dst.unlink(missing_ok=True)
        return "The conversion took longer than ten minutes, so I stopped it."
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        return f"ffmpeg could not convert {src.name}: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
    return f"Made {dst} ({dst.stat().st_size / 1e6:.1f} MB) from {src.name}; the original is unchanged."


SKILLS = [Skill(name="convert_media", schema=_SCHEMA, run=_run)]
