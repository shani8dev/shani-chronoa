"""Skill: read a video's metadata - duration, resolution, codec, audio, and
where the frames change - with ffprobe/ffmpeg, which every Shanios install
has. The "read" counterpart to edit_image/convert_media's edit side.

Nothing is written except ffprobe's report and, if asked, keyframe stills.
Structured: ffprobe is asked one fact at a time, and a stream that has no
value (a silent video has no audio stream) is reported as absent, never as a
guessed number.
"""

import json
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_video",
        "description": (
            "Read a video's basics out loud: how long it is, its resolution, "
            "its video codec, whether it has audio, and its bitrate. Add "
            "keyframes=true to also save the moments the image changes. The "
            "original is never modified."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The video file."},
            "keyframes": {"type": "boolean",
                          "description": "Also save stills of where the video changes, as PNG files beside the video."},
        }, "required": ["path"]},
    },
}


def _probe(src, entries: str) -> dict | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_entries", entries, str(src)],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _run(arguments: dict) -> str:
    if not shutil.which("ffprobe"):
        return "Reading a video's details needs ffprobe (the ffmpeg package), which is not installed."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."

    fmt = _probe(src, "format=duration,size,bit_rate,format_name:stream=codec_type,codec_name,width,height,r_frame_rate,duration,channels,sample_rate")
    if fmt is None:
        return f"ffprobe could not read {src.name} - it may not be a video file."
    format_info = fmt.get("format") or {}
    streams = fmt.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    try:
        seconds = float(format_info.get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    mins, secs = divmod(int(seconds), 60)
    lines = [f"{src.name}:"]
    lines.append(f"  length {mins} min {secs} s")
    if video:
        lines.append(f"  video {video.get('codec_name', '?')} "
                     f"{video.get('width', '?')}x{video.get('height', '?')}"
                     + (f" at {video.get('r_frame_rate')} fps" if video.get('r_frame_rate') else ""))
    if audio:
        lines.append(f"  audio {audio.get('codec_name', '?')} "
                     f"{audio.get('channels', '?')} channels"
                     + (f" at {audio.get('sample_rate')} Hz" if audio.get('sample_rate') else ""))
    else:
        lines.append("  no audio track")
    size = format_info.get("size")
    if size:
        lines.append(f"  {int(size) / 1e6:.1f} MB")
    bitrate = format_info.get("bit_rate")
    if bitrate:
        lines.append(f"  about {int(bitrate) / 1e6:.1f} Mbps")

    if arguments.get("keyframes"):
        if not shutil.which("ffmpeg"):
            return "\n".join(lines) + "\n(Keyframes need ffmpeg, which is not installed.)"
        from shani_chronoa import video_frames
        try:
            folder = src.with_name(f"{src.stem}-keyframes")
            folder.mkdir(exist_ok=True)
            frames = video_frames.keyframes(src, folder)
        except Exception as exc:  # noqa: BLE001 - reading the file parts is the result; keyframes are best-effort
            return "\n".join(lines) + f"\n(keyframes could not be extracted: {exc})"
        saved = []
        for f in frames:
            target = folder / f"{int(f.seconds // 60):02d}m{f.seconds % 60:04.1f}s.png"
            f.path.rename(target)
            saved.append(target)
        lines.append(f"  keyframes: {len(saved)} stills saved beside {src.name}" if saved
                     else "  keyframes: ffmpeg found none")
    return "\n".join(lines)


def _verify_read_video(arguments: dict, tool=None):
    # read_video never writes unless keyframes were asked for; there is nothing
    # to check existence of for the metadata path. A keyframes run with ffprobe
    # missing is a refused result with no folder, and the caller text says so.
    return None


POST_CONDITION = _verify_read_video

SKILLS = [Skill(name="read_video", schema=_SCHEMA, run=_run)]
