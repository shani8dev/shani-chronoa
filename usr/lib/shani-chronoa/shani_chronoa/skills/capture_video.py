"""Skill: record a video from the camera. Gap in the media matrix that was
shown plainly: screenshot captures a still of the screen, and recording.py
captures sound, but nothing captured a video from the camera.

Like scan_document, which uses screengrab.capture_camera under the same
`vision-sense-enabled` consent: reading a camera is gated, and a machine with
no camera says so rather than pretending to record.
"""

import re
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "capture_video",
        "description": (
            "Record a short video from the camera (webcam), e.g. '/dev/video0', "
            "for a few seconds. Requires the vision/camera consent. Writes a "
            "new .mp4; no camera means a clear refusal."
        ),
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Camera device, e.g. /dev/video0."},
            "seconds": {"type": "number", "description": "How long to record (default 5, max 60)."},
        }, "required": []},
    },
}


def _run(arguments: dict) -> str:
    device = (arguments.get("device") or "/dev/video0").strip()
    if not re.fullmatch(r"/dev/video[0-9]{1,3}", device):
        return f"{device!r} is not a camera like /dev/video0."
    # Through `vision_sense_enabled`, not a raw `get_bool` on the key: this skill
    # reads a camera, so it must answer the *same* question every other vision
    # reader answers. A hand-rolled key read skips the consent-key aliases and
    # the privacy-mode refusal that `sense_allowed` applies, which is how a
    # skill ends up able to open a device the senses layer considers refused.
    config = ChronoaConfig()
    if not config.vision_sense_enabled:
        return config.sense_allowed_reason("vision") or (
            "Recording from the camera is off - enable the vision sense in "
            "Settings before I will read a camera.")
    seconds = arguments.get("seconds") or 5
    try:
        seconds = max(1, min(float(seconds), 60))
    except (TypeError, ValueError):
        return f"Could not use record length {seconds!r}."
    if not Path(device).exists():
        return f"No camera at {device}. Reading a camera needs the device to be present."
    if not shutil.which("ffmpeg"):
        return "Recording from a camera needs ffmpeg, which is not installed."

    out_dir = files.data_home() / "shani-chronoa" / "recordings"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"camera-{int(seconds)}s.mp4"
    n = 1
    while target.exists():
        target = out_dir / f"camera-{int(seconds)}s-{n}.mp4"
        n += 1
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "v4l2", "-i", device, "-t", str(seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)]
    from shani_chronoa import body
    try:
        # "looking" for as long as the camera is open: this path drives ffmpeg
        # on /dev/video directly, not through screengrab, so it lit only
        # "acting" while the camera recorded.
        with body.lit("eyes", "recording the camera", device, deadline=seconds + 60):
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=int(seconds) + 60, check=False)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        return "Recording from the camera took too long, so I stopped it."
    if r.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return (f"ffmpeg could not record from {device}: "
                f"{(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}")
    return f"Saved {target} ({target.stat().st_size / 1e6:.1f} MB) from {device}."


POST_CONDITION = None

SKILLS = [Skill(name="capture_video", schema=_SCHEMA, run=_run)]
