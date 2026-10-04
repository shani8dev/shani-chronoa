"""Skill: make a picture from a description, on this computer (stable-diffusion.cpp, SD-Turbo).

The model runs in its own local server (`imagegen`, set up from Chronoa's
setup), so this skill only sends the description over 127.0.0.1 and saves the
PNG it gets back; nothing leaves the machine. A picture is saved as a new file
under ~/Pictures/Chronoa (or the `output` path, inside the home folder) and is
never written over an existing one.

On a processor a 512x512 picture takes tens of seconds, and the first one after
the server starts also loads the 2 GB model - so the skill's time limit is
longer than other skills' (`tools._SLOW_TOOLS`).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from shani_chronoa import files, imagegen
from shani_chronoa.skills import Skill

#: under tools._SLOW_TOOLS["generate_image"], so this answers before the sandbox stops it
_TIMEOUT = 280.0

SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "Make a new picture from a text description, or change a photo the way a description says "
            "(from_photo, e.g. 'make it snowy', 'as a watercolour'), drawn locally by stable-diffusion.cpp. "
            "Describe it in English. Saves a PNG and returns its path."
        ),
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "What the picture shows, e.g. 'a red fox in snow, watercolour'."},
            "avoid": {"type": "string", "description": "Things to keep out of it, e.g. 'text, blur'."},
            "size": {"type": "integer", "enum": list(imagegen.SIZES),
                     "description": f"Square size in pixels; {imagegen.DEFAULT_SIZE} looks best."},
            "steps": {"type": "integer", "description": f"1-{imagegen.MAX_STEPS}; more is slower and a little cleaner."},
            "seed": {"type": "integer", "description": "The same seed and prompt give the same picture."},
            "output": {"type": "string", "description": "Where to save, e.g. '~/Pictures/fox.png'."},
            "from_photo": {"type": "string", "description": "A photo to change instead of drawing from nothing."},
            "strength": {"type": "number",
                         "description": "from_photo: how much to change it, 0.1 (a touch) to 0.9 (mostly new). Default 0.5."},
        }, "required": ["prompt"]},
    },
}


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    return "-".join(words) or "picture"


def _target(arguments: dict, prompt: str) -> Path:
    if arguments.get("output"):
        target = files.resolve_in_home(str(arguments["output"]))
        return target if target.suffix.lower() == ".png" else target.with_suffix(".png")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return imagegen.pictures_dir() / f"{_slug(prompt)}-{stamp}.png"


def _int(arguments: dict, key: str, default: int) -> int:
    value = arguments.get(key)
    if value in (None, ""):
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{key} must be a whole number")
    return int(value)


def _run(arguments: dict) -> str:
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return "Say what the picture should show."
    if not imagegen.available():
        return ("Making pictures is not set up on this computer. Open Chronoa's setup (More -> Imagine) "
                "to download the picture model (about 2 GB).")
    try:
        size = _int(arguments, "size", imagegen.DEFAULT_SIZE)
        steps = _int(arguments, "steps", imagegen.DEFAULT_STEPS)
        seed = _int(arguments, "seed", -1)
        target = _target(arguments, prompt)
    except (ValueError, files.PathProblem) as exc:
        return str(exc)
    if target.exists():
        return f"{target} already exists; give another output."
    started = time.monotonic()
    source = None
    try:
        if arguments.get("from_photo"):
            source = files.resolve(str(arguments["from_photo"]))
            if not source.is_file():
                return f"{source} does not exist."
            strength = float(arguments.get("strength") or 0.5)
            image, width, height = imagegen.fit_for_model(source, size)
            png = imagegen.edit(image, width, height, prompt, strength=strength, steps=steps, seed=seed,
                                negative=str(arguments.get("avoid") or ""), timeout=_TIMEOUT)
            size_said = f"{width}x{height}"
        else:
            png = imagegen.generate(prompt, size=size, steps=steps, seed=seed,
                                    negative=str(arguments.get("avoid") or ""), timeout=_TIMEOUT)
            size_said = f"{size}x{size}"
    except (imagegen.ImageError, files.PathProblem, ValueError) as exc:
        return f"Could not make the picture: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "xb") as handle:  # never over an existing file, even one that appeared meanwhile
        handle.write(png)
    what = f"Changed {source.name} to {imagegen.clean_prompt(prompt)!r}" if source else \
        f"Made a {size_said} picture of {imagegen.clean_prompt(prompt)!r}"
    if source:
        what += f" ({size_said})"
    return f"{what} in {time.monotonic() - started:.0f} s and saved it to {target}."


SKILLS = [Skill(name="generate_image", schema=SCHEMA, run=_run)]
