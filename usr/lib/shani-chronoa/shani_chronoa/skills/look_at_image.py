"""Skill: look at a picture file and say what it shows, with the local vision
model - the file-side twin of the vision sense, which captures the screen or a
camera. shani-shell's Lens hands Chronoa a snippet of the screen this way:
"what is this plant", "explain this chart", "what does this error mean".

**The same gate and the same path as the vision sense, on purpose.** Sending a
picture's pixels to a model is the data class the `vision` sense is consented
for, so this skill is gated on it too (`capabilities.GATED`). The model is
chosen by `vision.choose_backend` (Chronoa's own llama.cpp vision first, a
local Ollama second, never a remote endpoint) and the image reaches it through
`vision._describe_via_child`, the by-reference child the sense uses. Nothing
here opens a socket of its own.

**The words in a picture are read_document's job.** This describes; it does
not transcribe. The schema says so, so the model picks the right tool.

**Normalised before it is sent.** Any format Pillow opens is converted to PNG
with its long side capped at `MAX_SIDE`: a 40-megapixel photo would cost the
model minutes and a context window to say what a 1500-pixel copy says.
"""

from __future__ import annotations

import io

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import vision
from shani_chronoa.skills import Skill

try:
    from PIL import Image as _Image
except Exception:  # noqa: BLE001 - Pillow is optional, as for read_image
    _Image = None

#: The long side of what the model is shown, in pixels.
MAX_SIDE = 1536
#: A file bigger than this is not opened at all.
MAX_FILE_BYTES = 40 * 1024 * 1024

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "look_at_image",
        "description": (
            "Look at a picture file and say what it shows, with the local vision "
            "model: objects, scenes, charts, screenshots, UI, handwriting. Use a "
            "prompt to ask something specific about it. For the exact words "
            "printed in a picture, use read_document. Needs the vision sense."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The picture file."},
            "prompt": {"type": "string",
                       "description": "What to ask about it. Defaults to a short description."},
        }, "required": ["path"]},
    },
}


def _png(raw: bytes) -> bytes:
    """The picture as a PNG no larger than MAX_SIDE on its long side."""
    with _Image.open(io.BytesIO(raw)) as img:
        img.load()
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed(vision.CONSENT_SENSE):
        return config.sense_allowed_reason(vision.CONSENT_SENSE)
    if _Image is None:
        return "Looking at a picture needs Pillow (PIL), which is not installed."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    if src.stat().st_size > MAX_FILE_BYTES:
        return f"{src.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB; I will not open it."
    prompt = str(arguments.get("prompt") or vision.DEFAULT_PROMPT).strip()
    try:
        image = _png(src.read_bytes())
    except Exception:  # noqa: BLE001 - an unreadable picture must not traceback
        return f"{src.name} is not a picture I can read."
    try:
        backend, model, host = vision.choose_backend({}, config)
        timeout = vision._timeout({})
        said = vision._describe_via_child(image, model, host, prompt, timeout, backend)
    except vision.VisionError as e:
        return str(e)
    return f"What {src.name} shows, described by {model}:\n{said}"


POST_CONDITION = None

SKILLS = [Skill(name="look_at_image", schema=_SCHEMA, run=_run)]
