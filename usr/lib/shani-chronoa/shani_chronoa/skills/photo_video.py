"""Skill: what is in a photo or video, effects that depend on it, and going through a video.

- identify (OpenCV): faces and 80 kinds of everyday object ("2 people, a dog
  and a bicycle"), in a photo or across a video's moments; optionally a copy
  with each one boxed and labelled.
- effect (OpenCV): blur or pixelate faces (before sharing), blur the
  background behind people, or remove it (a transparent PNG). A video gets the
  effect on every frame and keeps its sound. `remove_background` takes a
  subject: `person` (the default, PP-HumanSeg) or `object`, which cuts out
  whatever the picture is of - a dog, a book, a laptop - with u2net, an
  optional extra that is refused by name when it is not installed.
- keyframes (ffmpeg): the moments a video changes, saved as pictures.
- describe (ffmpeg + the vision model): what happens in a video.

Only identify and effect need the OpenCV extra; keyframes needs nothing extra
and describe needs the eyes. A look that does not depend on what is in the
picture (sepia, vignette, sketch...) is `edit_image`'s, and resizing or
converting is `edit_image`'s and `convert_media`'s. The original is never touched and nothing is overwritten:
results are new files beside it. Nothing leaves the machine.
"""

from __future__ import annotations

from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_VIDEO = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".3gp"}
_PHOTO = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_ACTIONS = ("identify", "effect", "keyframes", "describe")
_EFFECT_NAMES = ("blur_faces", "pixelate_faces", "blur_background", "remove_background")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "photo_video",
        "description": (
            "Work on a photo or video by what is in it, on this computer. identify: find faces and "
            "everyday objects (people, cars, dogs, cups...); effect: blur_faces, pixelate_faces, "
            "blur_background or remove_background, saved as a new file (videos keep their sound); "
            "keyframes: save the moments a video changes; describe: say what happens in a video. For a "
            "look like sepia or sketch, or resizing and converting, use edit_image or convert_media."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "path": {"type": "string", "description": "The photo or video."},
            "effect": {"type": "string", "enum": list(_EFFECT_NAMES)},
            "subject": {"type": "string", "enum": ["person", "object"],
                        "description": "remove_background only: person (the default) keeps the people in the "
                                       "picture; object cuts out whatever the picture is of - a dog, a book, a "
                                       "laptop - and needs the u2net extra."},
            "labelled_copy": {"type": "boolean", "description": "identify on a photo: also save a copy with boxes."},
        }, "required": ["action", "path"]},
    },
}


def _new_beside(source: Path, tag: str, suffix: str) -> Path:
    for n in range(1, 1000):
        candidate = source.with_name(f"{source.stem}-{tag}{'' if n == 1 else f'-{n}'}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"too many {tag} copies beside {source.name} already")


def _read_photo(path: Path):
    from shani_chronoa.opencv import runtime
    cv2, np = runtime.load()
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"{path.name} is not a picture OpenCV can read")
    return image


def _write_photo(image, target: Path) -> None:
    from shani_chronoa.opencv import runtime
    cv2, _np = runtime.load()
    ok, data = cv2.imencode(target.suffix if target.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else ".png",
                            image)
    if not ok:
        raise ValueError("the result could not be encoded")
    with open(target, "xb") as handle:
        handle.write(data.tobytes())


def _identify(path: Path, video: bool, labelled: bool) -> str:
    from shani_chronoa import video_frames
    from shani_chronoa.opencv import detect, effects
    if video:
        tmp, frames = video_frames.temporary_keyframes(path)
        seen, faces = {}, 0
        try:
            images = [_read_photo(f.path) for f in frames]
        finally:
            tmp.cleanup()
        for image in images:
            found = detect.objects(image)
            # the most of each thing seen in one moment, not a sum that counts one dog per frame
            for label in {b.label for b in found}:
                seen[label] = max(seen.get(label, 0), sum(1 for b in found if b.label == label))
            faces = max(faces, len(detect.faces(image)))
        things = detect.summarise([detect.Box(label, 1.0, 0, 0, 0, 0) for label, n in seen.items() for _ in range(n)])
        return (f"Across {len(frames)} moments of {path.name}: {things} (the most seen at once), "
                f"and at most {faces} face(s) at once.")
    image = _read_photo(path)
    found = detect.objects(image)
    faces = detect.faces(image)
    said = f"In {path.name}: {detect.summarise(found)}; {len(faces)} face(s)."
    if labelled and (found or faces):
        target = _new_beside(path, "labelled", ".png")
        _write_photo(effects.label_boxes(image, found + faces), target)
        said += f" A labelled copy is at {target}."
    return said


def _effect(path: Path, video: bool, name: str, subject: str = "person") -> str:
    from shani_chronoa import matting_cutout
    from shani_chronoa.opencv import effects, video as videos
    if name not in effects.EFFECTS:
        return f"effect must be one of {', '.join(_EFFECT_NAMES)}."
    if video:
        if name in effects.PHOTO_ONLY:
            return f"{name} is for photos only; on a whole video it would take a second a frame."
        target = _new_beside(path, name.replace("_", "-"), ".mp4")
        note = videos.apply_effect(path, effects.EFFECTS[name], target)
        return f"Saved {path.name} with {name.replace('_', ' ')} to {target} ({note})."
    image = _read_photo(path)
    if name == "remove_background":
        # Always PNG, whatever came in: a jpg has no alpha channel, so a cut-out
        # saved as one comes back with its background intact and nothing said so.
        target = _new_beside(path, "remove-background", ".png")
        try:
            image = matting_cutout.cut_out(image, subject)
        except matting_cutout.CutoutUnavailable as exc:
            return f"Not done: {exc}. Removing the background behind people still works."
        _write_photo(image, target)
        return (f"Saved {path.name} with the background removed to {target} "
                f"(cut out with {matting_cutout.model_of(subject)}).")
    target = _new_beside(path, name.replace("_", "-"), path.suffix if path.suffix.lower() != ".bmp" else ".png")
    _write_photo(effects.EFFECTS[name](image), target)
    return f"Saved {path.name} with {name.replace('_', ' ')} to {target}."


def _keyframes(path: Path) -> "tuple[list, Path]":
    from shani_chronoa import video_frames
    folder = _new_beside(path, "keyframes", "")
    frames = video_frames.keyframes(path, folder)
    saved = []
    for f in frames:
        target = folder / f"{int(f.seconds // 60):02d}m{f.seconds % 60:04.1f}s.png"
        f.path.rename(target)
        saved.append((f.seconds, target))
    return saved, folder


def _describe(path: Path) -> str:
    from shani_chronoa import local_vision
    if not local_vision.available():
        return ("Describing a video needs the vision model (Chronoa's setup, More -> Eyes). The keyframes action "
                "saves the moments it changes without one.")
    saved, folder = _keyframes(path)
    lines = []
    for seconds, frame in saved[:6]:
        try:
            said = local_vision.describe(frame.read_bytes(), "Describe this video frame in one sentence.", 120)
        except local_vision.VisionError as exc:
            said = f"(could not describe it: {exc})"
        lines.append(f"- {int(seconds // 60)}:{seconds % 60:04.1f} {said}")
    return f"{path.name}, moment by moment (frames in {folder}):\n" + "\n".join(lines)


def _subject(arguments: dict) -> str:
    """The subject asked for, or '' when the arguments do not make sense."""
    from shani_chronoa import matting_cutout

    action = (arguments.get("action") or "").strip().lower()
    effect = (arguments.get("effect") or "").strip().lower()
    subject = str(arguments.get("subject") or "person").strip().lower()
    if subject not in matting_cutout.SUBJECTS:
        return ""
    # `subject` on anything but remove_background is a mistake worth naming:
    # silently ignoring it would make "blur_background of the dog" do the
    # people thing and report success.
    if subject != "person" and not (action == "effect" and effect == "remove_background"):
        return ""
    return subject


def _run(arguments: dict) -> str:
    from shani_chronoa import matting_cutout
    from shani_chronoa.opencv import runtime
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    if arguments.get("subject") and not _subject(arguments):
        return (f"subject must be {' or '.join(matting_cutout.SUBJECTS)}, and only remove_background "
                f"takes one.")
    try:
        path = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if not path.is_file():
        return f"{path} does not exist."
    suffix = path.suffix.lower()
    video = suffix in _VIDEO
    if not video and suffix not in _PHOTO:
        return f"{path.name} is not a photo or video this works on ({', '.join(sorted(_PHOTO | _VIDEO))})."
    if action in ("keyframes", "describe") and not video:
        return f"{action} is for videos; {path.name} is a photo."
    if action in ("identify", "effect"):
        problem = runtime.problem()
        if problem:
            return problem
    try:
        if action == "identify":
            return _identify(path, video, bool(arguments.get("labelled_copy")))
        if action == "effect":
            return _effect(path, video, str(arguments.get("effect") or ""), _subject(arguments) or "person")
        if action == "keyframes":
            saved, folder = _keyframes(path)
            return (f"Saved {len(saved)} moments of {path.name} to {folder}: "
                    + ", ".join(f"{int(s // 60)}:{s % 60:04.1f}" for s, _ in saved) + ".")
        return _describe(path)
    except (ValueError, OSError) as exc:
        return f"Could not work on {path.name}: {exc}"


def _post_condition(arguments: dict):
    """The written file, read back - not the sentence above claiming it.

    Only `remove_background` writes a file whose content is a claim about the
    picture, so the other actions return None and `verification.verify` reports
    them UNVERIFIED rather than as success. The check itself lives in
    `matting_cutout.check_cutout`, which is where the alpha and the subject's
    pixels are read.
    """
    from shani_chronoa import matting_cutout

    if (arguments.get("action") or "").strip().lower() != "effect":
        return None
    if (arguments.get("effect") or "").strip().lower() != "remove_background":
        return None
    try:
        source = files.resolve(arguments.get("path") or "")
    except files.PathProblem:
        return None
    if source.suffix.lower() in _VIDEO:
        return None
    # `_new_beside` picks the first *free* name, so which one this call wrote is
    # not recoverable from the arguments. The newest match is the one just
    # written; reading it back is the whole point of the check.
    written = None
    for n in range(1, 1000):
        candidate = source.with_name(f"{source.stem}-remove-background{'' if n == 1 else f'-{n}'}.png")
        if not candidate.exists():
            break
        written = candidate
    if written is None:
        return False, f"no remove-background copy of {source.name} is beside it"
    return matting_cutout.check_cutout(written, source)


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="photo_video", schema=SCHEMA, run=_run)]
