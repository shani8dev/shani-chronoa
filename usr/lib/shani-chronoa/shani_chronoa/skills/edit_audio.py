"""Skill: edit an audio file - trim it, make it louder or quieter, normalize
it, change speed, or convert it to a different audio format - with ffmpeg. The
audio side of edit_video.

The original is never touched and nothing is overwritten: the result is a new
file beside it. Nothing leaves the machine.
"""

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_AUDIO_OK = {".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a", ".aac", ".wma", ".aiff", ".alac"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_audio",
        "description": (
            "Edit a sound file and save the result as a new file next to it "
            "(never overwriting the original): trim between times, raise or "
            "lower the volume, normalize loudness, change speed, or convert to "
            "an audio format. End times accept seconds or 'MM:SS'/'HH:MM:SS'."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The audio file."},
            "start": {"type": "string", "description": "Trim to start at this time."},
            "end": {"type": "string", "description": "Trim to end at this time."},
            "volume": {"type": "number", "description": "Volume multiplier: 2.0 doubles, 0.5 halves."},
            "normalize": {"type": "boolean", "description": "Even out loudness with loudnorm."},
            "speed": {"type": "number", "description": "Playback speed: 2.0 faster, 0.5 slower."},
            "to": {"type": "string", "enum": ["mp3", "ogg", "opus", "wav", "flac", "m4a"],
                   "description": "Convert to this audio format."},
        }, "required": ["path"]},
    },
}


def _seconds(raw: str) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if ":" in text:
            parts = [int(p) for p in text.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            return None
        return float(text)
    except ValueError:
        return None


def _new_beside(src, tag: str, suffix: str):
    target = src.with_name(f"{src.stem}-{tag}{suffix}")
    n = 1
    while target.exists():
        target = src.with_name(f"{src.stem}-{tag}-{n}{suffix}")
        n += 1
    return target


def _run(arguments: dict) -> str:
    if not shutil.which("ffmpeg"):
        return "Editing audio needs ffmpeg, which is not installed."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."

    start = _seconds(arguments.get("start"))
    end = _seconds(arguments.get("end"))
    if arguments.get("start") and start is None:
        return f"Could not read start time {arguments.get('start')!r}."
    if arguments.get("end") and end is None:
        return f"Could not read end time {arguments.get('end')!r}."
    if start is not None and end is not None and end <= start:
        return f"End ({arguments.get('end')!r}) must be after start ({arguments.get('start')!r})."
    volume = arguments.get("volume")
    if volume is not None:
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            return f"Could not use volume {arguments.get('volume')!r}."
        if volume <= 0 or volume > 8:
            return "Volume multiplier must be a positive number up to 8."
    speed = arguments.get("speed")
    if speed is not None:
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return f"Could not use speed {arguments.get('speed')!r}."
        if not (0.25 <= speed <= 4.0):
            return "Speed must be between 0.25 and 4.0."
    to = (arguments.get("to") or "").lower().lstrip(".")
    if to and to not in {"mp3", "ogg", "opus", "wav", "flac", "m4a"}:
        return "to must be mp3, ogg, opus, wav, flac, or m4a."
    normalize = bool(arguments.get("normalize"))

    tag = []
    if start is not None or end is not None:
        tag.append("trimmed")
    if volume is not None and volume != 1.0:
        tag.append(f"{volume:g}x")
    if normalize:
        tag.append("normalized")
    if speed is not None and speed != 1.0:
        tag.append(f"{speed:g}x")
    tag = "-".join(tag) or "edit"

    suffix = "." + to if to else src.suffix
    if suffix.lower() not in _AUDIO_OK and suffix.lower() != ".mka":
        suffix = src.suffix if src.suffix.lower() in _AUDIO_OK else ".mp3"
    target = _new_beside(src, tag, suffix)

    af = []
    if volume is not None and volume != 1.0:
        af.append(f"volume={volume}")
    if normalize:
        af.append("loudnorm")
    if speed is not None and speed != 1.0:
        af.append(f"atempo={speed}")
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start is not None:
        argv += ["-ss", str(start)]
    argv += ["-i", str(src)]
    if end is not None and start is not None:
        argv += ["-t", str(end - start)]
    elif end is not None:
        argv += ["-t", str(end)]
    if af:
        argv += ["-af", ",".join(af)]
    argv += {"mp3": ["-c:a", "libmp3lame", "-q:a", "2"], "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
             "opus": ["-c:a", "libopus", "-b:a", "96k"], "wav": ["-c:a", "pcm_s16le"],
             "flac": ["-c:a", "flac"], "m4a": ["-c:a", "aac", "-b:a", "192k"]}.get(suffix.lstrip("."), ["-c:a", "libmp3lame", "-q:a", "2"])
    argv += [str(target)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        return "That edit took longer than fifteen minutes, so I stopped it."
    if r.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return f"ffmpeg could not edit {src.name}: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
    return f"Saved {target} ({target.stat().st_size / 1e6:.1f} MB); the original is unchanged."


POST_CONDITION = None

SKILLS = [Skill(name="edit_audio", schema=_SCHEMA, run=_run)]
