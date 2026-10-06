"""Skill: read an audio file's basics - length, codec, sample rate, channels,
and bitrate - with ffprobe. The audio side of read_video; no audio is written.
"""

import json
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_AUDIO_OK = {".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a", ".aac", ".wma", ".aiff", ".alac", ".mka"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_audio",
        "description": (
            "Read an audio file's basics out loud: how long it is, its codec, "
            "sample rate, channels, and bitrate. The original is never modified."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The audio file."},
        }, "required": ["path"]},
    },
}


def _read(path) -> str:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_entries", "format=duration,size,bit_rate,format_name:stream=codec_type,codec_name,channels,sample_rate,bit_rate",
             str(path)],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "ffprobe was not able to run."
    if r.returncode != 0 or not r.stdout.strip():
        return f"ffprobe could not read {path.name} - it may not be an audio file."
    try:
        fmt = json.loads(r.stdout)
    except ValueError:
        return f"ffprobe could not parse {path.name}."
    format_info = fmt.get("format") or {}
    streams = fmt.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    try:
        seconds = float(format_info.get("duration") or audio.get("duration") or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    mins, secs = divmod(int(seconds), 60)
    lines = [f"{path.name}:", f"  length {mins} min {secs} s"]
    if audio:
        lines.append(f"  audio {audio.get('codec_name', '?')} "
                     f"{audio.get('channels', '?')} channel(s)"
                     + (f" at {audio.get('sample_rate')} Hz" if audio.get('sample_rate') else ""))
    else:
        lines.append("  no audio stream found")
    size = format_info.get("size")
    if size:
        lines.append(f"  {int(size) / 1e6:.1f} MB")
    bitrate = format_info.get("bit_rate") or audio.get("bit_rate")
    if bitrate:
        lines.append(f"  about {int(bitrate) / 1e3:.0f} kbps")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    if not shutil.which("ffprobe"):
        return "Reading an audio file needs ffprobe (the ffmpeg package), which is not installed."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    if src.suffix.lower() not in _AUDIO_OK and src.suffix.lower() not in {".mp4", ".webm", ".mkv", ".mov"}:
        return (f"{src.name} does not look like audio; I read .mp3 .wav .flac .m4a and similar "
                f"(or an audio track inside common video files).")
    return _read(src)


POST_CONDITION = None

SKILLS = [Skill(name="read_audio", schema=_SCHEMA, run=_run)]
