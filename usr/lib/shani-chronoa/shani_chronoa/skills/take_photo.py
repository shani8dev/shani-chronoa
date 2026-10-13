"""Skill: take a photo (a selfie) with the webcam, optionally after a countdown.

`capture_video` records a clip and `scan_document` reads a page off the
camera, but nothing simply took a picture and kept it - "take a selfie",
"take a picture in 5 seconds".

Same camera gate as `capture_video` and `scan_document`: the vision sense's
consent, asked through `vision_sense_enabled` so the consent aliases and the
privacy-mode refusal apply exactly as they do to every other camera reader.
Same capture tool as `capture_video`: ffmpeg on the v4l2 device, one frame.
The first few frames a webcam produces are often dark while its auto-exposure
settles, so a handful are skipped and the next one is kept.

Every check that can fail (consent, device, ffmpeg, the output path) runs
*before* the countdown, so the user is never counted down to a refusal. The
countdown is announced with one desktop notification (notify-send), honouring
`notification-enabled`: with notifications off the countdown still runs and
the reply says nothing was shown. The organ strip reads "looking" for the
whole time the camera is open.

Saved under the XDG Pictures folder (`Pictures/Chronoa`, beside generated
images) with a timestamped name, or at `path` inside the home directory. An
existing file is never overwritten, and the reply names the file only once it
exists and is not empty.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

MAX_COUNTDOWN = 30
#: frames thrown away while the webcam's auto-exposure settles
WARMUP_FRAMES = 5
_EXTENSIONS = (".jpg", ".jpeg", ".png")
_CAPTURE_TIMEOUT = 30

SCHEMA = {
    "type": "function",
    "function": {
        "name": "take_photo",
        "description": ("Take a photo or selfie with the webcam and save it to the Pictures folder, "
                        "optionally after a countdown in seconds (0-30). Requires the vision/camera "
                        "consent. Not for screenshots (use screenshot) or video (use capture_video)."),
        "parameters": {"type": "object", "properties": {
            "countdown": {"type": "integer", "description": "Seconds to wait before taking it, 0-30 (default 0)."},
            "path": {"type": "string",
                     "description": "Optional file (.jpg/.png) or folder inside the home directory to save to."},
            "device": {"type": "string", "description": "Camera device, e.g. /dev/video0 (default)."},
            "infrared": {"type": "boolean",
                         "description": "Capture in infrared from an IR camera rather than in "
                                        "ordinary light. Refuses when none is attached."},
        }, "required": []},
    },
}


def _wait(seconds: int) -> None:
    time.sleep(seconds)


def _ir_node(requested: str) -> "tuple[str, str]":
    """`(node, refusal)` for an infrared capture.

    **The requested node wins when it is an infrared one**, because a person who
    names `/dev/video9` has already answered which camera; otherwise the first
    capture-capable infrared node is chosen, ordered so a node that *can* capture
    comes first - a card name is a weak signal and a node that cannot capture is not
    a target however infrared it looks.
    """
    from shani_chronoa import camera
    found = [info for info in camera.collect() if info.get("looks_ir")]
    wanted = [str(i["node"]) for i in found if i.get("can_capture")] \
        or [str(i["node"]) for i in found]
    if requested:
        # **A device the caller named is never silently replaced.** Substituting
        # /dev/video9 for the /dev/video1 they asked for is a photo from a camera
        # they did not name - the shape this repository refuses everywhere else,
        # where an instrument answers the question it was asked rather than a
        # nearby one. It is either the infrared one or a refusal that says which
        # infrared nodes there are.
        if requested in wanted:
            return requested, ""
        cards = ", ".join(sorted({str(i.get("card") or i["node"]) for i in found})) \
            or "none"
        return "", (f"{requested} is not an infrared camera on this machine. The "
                    f"infrared nodes here are: {cards}. Nothing was taken.")
    if wanted:
        return wanted[0], ""
    return "", (f"Taking an infrared photo is not possible: {camera.none_sentence()}. "
                f"Nothing was taken.")


def _camera_present(device: str) -> bool:
    """Does this path exist as a camera node?

    **Existence, not capability** - the node a model names may be a V4L2 metadata
    node that answers for itself and cannot capture video. That is a richer
    question, answered by `camera.probe`, and the refusal below names it when the
    capture fails so "exists" is never read as "works".
    """
    return Path(device).exists()


def _pictures() -> Path:
    from shani_chronoa import imagegen
    return imagegen.pictures_dir()


def _free(folder: Path, name: str) -> Path:
    target, n = folder / name, 1
    while target.exists():
        target = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    return target


def _target(raw) -> "tuple[Path | None, str]":
    """(file to write, problem)."""
    name = f"photo-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return _free(_pictures(), name), ""
    if not isinstance(raw, str):
        return None, f"path must be a file or folder name, not {raw!r}."
    try:
        chosen = files.resolve_in_home(raw)
    except files.PathProblem as exc:
        return None, str(exc)
    if chosen.is_dir() or raw.strip().endswith("/"):
        return _free(chosen, name), ""
    if chosen.suffix.lower() not in _EXTENSIONS:
        return None, f"A photo is saved as .jpg or .png, not {chosen.suffix or 'a file with no extension'}."
    if chosen.exists():
        return None, f"{chosen} already exists; not overwriting it."
    return chosen, ""


def _countdown_notice(seconds: int, config) -> str:
    """Show the countdown notification; return a note for the reply ('' when shown)."""
    if not getattr(config, "notification_enabled", False):
        return " The countdown was not shown because notifications are turned off."
    if shutil.which("notify-send") is None:
        return " The countdown could not be shown: notify-send is not installed."
    try:
        r = subprocess.run(["notify-send", "--app-name=Shani Chronoa", "--icon=camera-photo",
                            f"--expire-time={seconds * 1000}", f"Taking a photo in {seconds} seconds",
                            "Look at the camera."], capture_output=True, text=True, timeout=10, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return " The countdown notification could not be shown."
    return "" if r.returncode == 0 else " The countdown notification could not be shown."


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "take_photo takes an optional countdown (0-30 seconds) and an optional path."
    device = arguments.get("device") or "/dev/video0"
    if not isinstance(device, str) or not re.fullmatch(r"/dev/video[0-9]{1,3}", device.strip()):
        return f"{device!r} is not a camera like /dev/video0."
    device = device.strip()
    infrared = arguments.get("infrared")
    if not isinstance(infrared, bool):
        infrared = False
    if infrared:
        #: **The default device is not chosen for an infrared capture.** `/dev/video0`
        #: is right on this box by enumeration luck and wrong on a machine where the
        #: metadata node registers first, so the node is selected rather than assumed.
        device, refusal = _ir_node(device if arguments.get("device") else "")
        if refusal:
            return refusal
    raw_count = arguments.get("countdown") or 0
    try:
        if isinstance(raw_count, bool):
            raise ValueError
        countdown = float(str(raw_count).strip().removesuffix("s"))
    except ValueError:
        return f"Could not use {raw_count!r} as a countdown in seconds."
    if countdown != countdown or countdown < 0 or countdown > MAX_COUNTDOWN:
        return f"The countdown must be between 0 and {MAX_COUNTDOWN} seconds, not {raw_count!r}."
    countdown = int(round(countdown))

    config = ChronoaConfig()
    if not config.vision_sense_enabled:
        reason = config.sense_allowed_reason("vision") or "the vision sense is turned off"
        return f"Taking a photo is not permitted: {reason}."
    if not _camera_present(device):
        return f"No camera at {device}, so no photo was taken."
    if not shutil.which("ffmpeg"):
        return files.tool_missing("ffmpeg", "take a photo with the camera")
    target, problem = _target(arguments.get("path"))
    if problem:
        return problem
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Could not create {target.parent}: {exc}"

    note = ""
    if countdown:
        note = _countdown_notice(countdown, config)
        _wait(countdown)

    stream = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
              "-f", "v4l2"]
    if infrared:
        # **`y16` is the infrared format, named rather than negotiated.** A v4l2
        # node that advertises several formats may hand back an ordinary one if the
        # demuxer is left to choose, and an ordinary capture is not what was asked
        # for - it is a confident answer to a different question. The hidden cost is
        # that ffmpeg then converts 16-bit greyscale to the output format itself,
        # and **that conversion interpolates**: measured on a synthetic 16-bit
        # frame, the source held 127 distinct levels and ffmpeg's output held 153.
        # Fine for a picture, and it is why the reply says so
        # rather than leaving a reader to assume the levels are the camera's.
        stream += ["-input_format", "y16"]
    stream += ["-i", device]
    argv = stream + ["-vf", f"select=gte(n\\,{WARMUP_FRAMES})",
                     "-frames:v", "1", "-update", "1", str(target)]
    from shani_chronoa import body
    try:
        with body.lit("eyes", "taking a photo", device, deadline=_CAPTURE_TIMEOUT + 5):
            r = subprocess.run(argv, capture_output=True, text=True, timeout=_CAPTURE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        return f"The camera at {device} gave no picture within {_CAPTURE_TIMEOUT}s, so no photo was taken."
    except OSError as exc:
        return f"Could not run ffmpeg: {exc}"
    if r.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return (f"ffmpeg could not take a photo from {device}: "
                f"{(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}")
    extra = ""
    if infrared:
        # **The reply says what the conversion did**, because a reader who assumes
        # the levels are the camera's would be wrong: ffmpeg produced more of them
        # than went in. Measured on a synthetic 16-bit frame - 127 distinct levels
        # in, 153 out. The shape `local_llm.perplexity` already uses, where an
        # unmeasured result is named as such rather than handed over silently.
        extra = (" It is an infrared frame, converted from 16-bit greyscale by "
                 "ffmpeg, which smooths between levels - so it shows what the "
                 "camera saw, and the values in it are not the camera's raw ones.")
    return (f"Saved a photo to {target} "
            f"({files.human_size(target.stat().st_size)})." + extra + note)


def _post_condition(arguments: dict):
    """Only an explicit file path can be checked afterwards: the default name
    carries a timestamp, so it is not derivable from the arguments (the
    stale-file trap `test_postcondition_coverage` records)."""
    raw = arguments.get("path") if isinstance(arguments, dict) else None
    if not isinstance(raw, str) or not raw.strip() or raw.strip().endswith("/"):
        return None
    try:
        path = files.resolve_in_home(raw)
    except files.PathProblem:
        return None
    if path.is_dir() or path.suffix.lower() not in _EXTENSIONS:
        return None
    if not path.is_file() or path.stat().st_size == 0:
        return False, f"{path} does not exist or is empty"
    # a file that was already there (the call refuses to overwrite it) is not
    # this call's photo: only a fresh one counts
    age = time.time() - path.stat().st_mtime
    if age > MAX_COUNTDOWN + _CAPTURE_TIMEOUT + 60:
        return False, f"{path} exists but was written {int(age)}s ago, not by this call"
    return True, f"{path} exists ({path.stat().st_size} bytes, written {int(age)}s ago)"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="take_photo", schema=SCHEMA, run=_run)]

#: **This skill's own declaration of what it wrote**, read out of the sentence
#: above. A skill runs in a subprocess, so that sentence is the only channel it
#: has back to the app - see `files.paths_in` for why there is no alternative.
#: The pattern is deliberately narrow: it matches this skill's *own* wording,
#: and `tools.artifacts_for` still requires the result to be an absolute path
#: that exists, so a loose match is dropped rather than rendered.
DECLARES_ARTIFACTS = files.paths_in(r"^Saved a photo to (.+?) \(")

