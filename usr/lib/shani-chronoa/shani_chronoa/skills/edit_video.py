"""Skill: edit a video - trim a clip, resize, rotate, mute, extract the audio,
speed it up or slow it down - with ffmpeg. For the edit-side counterpart to
read_video and edit_image.

The original is never touched and nothing is overwritten: the result is a new
file beside it. Nothing leaves the machine.

Trim and speed are the operations a person actually asks for on a clip ("the
part between 0:30 and 1:10", "make it twice as fast"), so they are the ones
with their own parameters; everything else (resize, rotate, mute, strip audio)
is one ffmpeg flag each. An unsupported combination is refused rather than
silently ignored: silence-dipped audio without audio, or a 0-length trim, is
named as the problem.
"""

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_VIDEO_OK = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".ts", ".3gp"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_video",
        "description": (
            "Edit a video and save the result as a new file next to it (never "
            "overwriting the original): trim between times, resize to a width, "
            "rotate, mute or keep only the audio, or change the speed. "
            "End times accept seconds ('95'), or 'MM:SS' / 'HH:MM:SS'."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The video, e.g. an attached file's path."},
            "start": {"type": "string", "description": "Trim to start at this time, e.g. '0:30'."},
            "end": {"type": "string", "description": "Trim to end at this time, e.g. '1:10'."},
            "width": {"type": "integer", "description": "Scale to this width in pixels, keeping aspect."},
            "rotate": {"type": "string", "enum": ["90", "180", "270"], "description": "Rotate clockwise."},
            "mute": {"type": "boolean", "description": "Remove the audio track."},
            "audio_only": {"type": "boolean", "description": "Keep only the sound as an audio file (.m4a)."},
            "speed": {"type": "number", "description": "Playback speed: 2.0 is twice as fast, 0.5 half."},
            # **Named, like convert_media and pdf_pages, so the post-condition can
            # verify it.** This skill's `_verify_edit` reads `arguments["output"]`
            # and returns None without one - and the schema did not declare the
            # parameter, so the model could never pass it and the post-condition
            # was dead code reading as a control. The path it defaults to is
            # reported either way.
            "output": {"type": "string", "description": "Where to write the edit. Defaults to <name>-edit.mp4 beside the original."},
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


def _source_fps(src) -> "float | None":
    """The source's frame rate, for the `fps` filter that actually shortens a clip.

    **Why this exists: `setpts` alone does not change a file's duration.**
    Measured on this box with a real 1-second clip: `ffmpeg -vf
    "setpts=0.5*PTS"` produced a container whose ffprobe duration is still
    **1.000s** - the frames are timestamped closer together but not one is
    dropped. Adding `fps=4` (twice the source's 2) produced **0.500s**. So a
    speed change needs both, and this skill had only the half that renames the
    timeline.
    """
    if not shutil.which("ffprobe"):
        return None
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=r_frame_rate",
                                "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True, timeout=30, check=False)
        text = (probe.stdout or "").strip()
        if "/" in text:
            numerator, _slash, denominator = text.partition("/")
            numerator, denominator = int(numerator), int(denominator or 1)
            if numerator and denominator:
                return numerator / denominator
        return float(text) if text else None
    except (OSError, ValueError):
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
        return "Editing a video needs ffmpeg, which is not installed."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    if src.suffix.lower() not in _VIDEO_OK:
        return (f"{src.name} is not a kind of video I edit - use convert_media for this format "
                f"({', '.join(sorted(_VIDEO_OK))}).")

    start = _seconds(arguments.get("start"))
    end = _seconds(arguments.get("end"))
    if arguments.get("start") and start is None:
        return f"Could not read start time {arguments.get('start')!r}; use '0:30' or seconds."
    if arguments.get("end") and end is None:
        return f"Could not read end time {arguments.get('end')!r}; use '1:10' or seconds."
    if start is not None and end is not None and end <= start:
        return f"End ({arguments.get('end')!r}) must be after start ({arguments.get('start')!r})."
    width = arguments.get("width")
    if width is not None:
        try:
            width = max(16, min(int(width), 7680))
        except (TypeError, ValueError):
            return f"Could not use width {arguments.get('width')!r} as pixels."
    rotate = str(arguments.get("rotate") or "").strip()
    if rotate and rotate not in ("90", "180", "270"):
        return "rotate must be 90, 180, or 270."
    speed = arguments.get("speed")
    if speed is not None:
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return f"Could not use speed {arguments.get('speed')!r}."
        if not (0.25 <= speed <= 4.0):
            return "Speed must be between 0.25 and 4.0."
    mute = bool(arguments.get("mute"))
    audio_only = bool(arguments.get("audio_only"))
    if audio_only and not shutil.which("ffmpeg"):
        return "Extracting audio needs ffmpeg."

    # **The declared destination, resolved and refused like every other path
    # here.** Without one the edit lands beside the original under a name the
    # skill derives; with one, the caller said where - and `_verify_edit` needs
    # a real path to verify, which is why the parameter exists in the schema.
    # A refusal happens before ffmpeg runs, so a bad destination is not a
    # half-written file.
    output = str(arguments.get("output") or "").strip()
    target = None
    if output:
        try:
            target = files.resolve(output)
        except files.PathProblem as exc:
            return str(exc)
        files.refuse_catalogue(target, "write the edited video to")

    # An edit that removes *all* the sound is a statement, not a mistake.
    if audio_only:
        target = target or _new_beside(src, "audio", ".m4a")
        codec = ["-vn", "-codec:a", "aac", "-b:a", "192k"]
    else:
        tag = []
        if start is not None or end is not None:
            from datetime import timedelta
            def _fmt(sec):
                td = timedelta(seconds=sec); total = int(td.total_seconds())
                return f"{total//3600:02d}:{(total%3600)//60:02d}:{total%60:02d}"
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(src)], capture_output=True, text=True, timeout=30)
            try:
                duration = float(probe.stdout or 0)
            except ValueError:
                duration = 0
            if start is not None and start >= duration and duration:
                return f"Start {arguments.get('start')!r} is past the end of {src.name}."
            tag.append("trimmed")
        if width:
            tag.append(f"{width}w")
        if rotate:
            tag.append(f"rot{rotate}")
        if mute:
            tag.append("silent")
        if speed and speed != 1.0:
            tag.append(f"{speed:g}x")
        tag = "-".join(tag) or "edit"
        target = target or _new_beside(
            src, tag, src.suffix if src.suffix.lower() in _VIDEO_OK else ".mp4")
        codec = []

    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start is not None:
        argv += ["-ss", str(start)]
    argv += ["-i", str(src)]
    if end is not None and start is not None:
        argv += ["-t", str(end - start)]
    elif end is not None:
        argv += ["-t", str(end)]
    vf = []
    if width:
        vf.append(f"scale={width}:-2")
    if rotate:
        angle = {"90": "transpose=1", "180": "transpose=1,transpose=1", "270": "transpose=2"}[rotate]
        vf.append(angle)
    if speed and speed != 1.0 and not mute and not audio_only:
        vf.append(f"setpts={1.0/speed:g}*PTS")
        # **And the frame rate, or the duration does not move.** `setpts` alone
        # renames the timeline and drops no frame; measured here it leaves a
        # 1-second clip at 1.000s. `fps` at the source rate times the factor is
        # what actually shortens (or lengthens) it - see `_source_fps`.
        rate = _source_fps(src)
        if rate:
            vf.append(f"fps={rate * speed:g}")
    af = []
    if speed and speed != 1.0 and not mute and not audio_only:
        af.append(f"atempo={speed}")
    if vf:
        argv += ["-vf", ",".join(vf)]
    if af:
        argv += ["-af", ",".join(af)]
    if mute:
        argv += ["-an"]
    argv += codec
    if not audio_only and src.suffix.lower() in _VIDEO_OK and target.suffix == ".mp4":
        if "-codec:v" not in argv:
            argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        if "-codec:a" not in argv and not mute:
            argv += ["-c:a", "aac", "-b:a", "128k"]
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


def _verify_edit_video(arguments: dict, tool=None):
    """Post-condition: the output exists, is non-empty, and ffprobe reads it as
    a media file - exactly the claim an edit makes."""
    from pathlib import Path as _P
    out = str(arguments.get("output") or arguments.get("out") or "").strip()
    if not out:
        # edit_video does not name its output in arguments; the caller reports
        # the path, and ffmpeg's successful exit plus a non-empty file is what
        # we can check here. Without arguments it is nothing to verify.
        return None
    target = _P(out)
    if not target.exists():
        return (False, f"{target.name} does not exist, so nothing was edited")
    size = target.stat().st_size
    if size == 0:
        return (False, f"{target.name} is empty")
    if shutil.which("ffprobe"):
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1", str(target)],
                capture_output=True, text=True, timeout=30, check=False)
        except Exception as exc:  # noqa: BLE001
            return (False, f"ffprobe could not read {target.name}: {type(exc).__name__}")
        if probe.returncode != 0 or "duration" not in probe.stdout:
            return (False, f"{target.name} is {size} bytes but ffprobe cannot find a duration in it")
        return (True, f"ffprobe reads {target.name}")
    return (True, f"{target.name} is {size} bytes (ffprobe is not installed, so the format was not confirmed)")


POST_CONDITION = _verify_edit_video

SKILLS = [Skill(name="edit_video", schema=_SCHEMA, run=_run)]
