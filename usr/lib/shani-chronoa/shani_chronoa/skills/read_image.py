"""Skill: read a picture's basics - dimensions, format, and colour mode - with
Pillow, which ShaniOS has. The picture-side of read_video/read_audio and of
read_document's OCR branch (which answers "what does it say"). No image is
written; the original is never touched.
"""

from shani_chronoa import files
from shani_chronoa.skills import Skill

try:
    from PIL import Image as _Image
except Exception:  # noqa: BLE001 - Pillow is optional, same class of optional as ffmpeg
    _Image = None

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_image",
        "description": (
            "Read a picture's basics out loud: width by height, format, and "
            "colour type, plus file size. For what the picture says, use "
            "read_document on it. The original is never modified."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The picture file."},
        }, "required": ["path"]},
    },
}


def _run(arguments: dict) -> str:
    if _Image is None:
        return "Reading a picture needs Pillow (PIL), which is not installed."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    try:
        with _Image.open(src) as img:
            width, height = img.size
            fmt = (img.format or src.suffix.lstrip(".")).upper()
            mode = img.mode
        size = src.stat().st_size
        return f"{src.name}:\n  {width}x{height} {fmt}, {mode}\n  {size / 1e6:.2f} MB"
    except Exception:  # noqa: BLE001 - an unreadable image must not traceback
        return f"{src.name} is not a picture I can read."


POST_CONDITION = None

SKILLS = [Skill(name="read_image", schema=_SCHEMA, run=_run)]
