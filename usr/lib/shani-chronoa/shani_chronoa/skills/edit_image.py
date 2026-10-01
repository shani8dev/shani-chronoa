"""Skill: edit a picture - resize, compress (to a size), crop, rotate, flip,
grayscale, strip metadata, change format - with ImageMagick, which every
Shanios install has. For a picture someone attaches ("make this smaller",
"compress it under 500 KB", "turn it black and white").

The original is never touched: the result is a new file beside it. Only
ordinary image formats are read, and the input is pinned to its own coder
(`png:/path/x.png`) - ImageMagick understands scriptable formats (MVG, MSL,
SVG with external references), and a file named .png must be read as a PNG.
Nothing leaves the machine.
"""

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

READ = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".gif": "gif",
        ".bmp": "bmp", ".tif": "tiff", ".tiff": "tiff", ".heic": "heic", ".avif": "avif"}
WRITE = ("png", "jpg", "webp", "gif", "bmp", "tiff", "avif")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_image",
        "description": "Edit a picture and save the result as a new file next to it: resize (width "
                       "and/or height, or percent), compress (quality, or to under a size in KB), crop, "
                       "rotate, flip, grayscale, strip metadata (location/camera), or convert format. "
                       "Several edits can be combined in one call.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The picture, e.g. an attached file's path."},
            "width": {"type": "integer"}, "height": {"type": "integer"},
            "percent": {"type": "integer", "description": "Scale to this percent of the size."},
            "quality": {"type": "integer", "description": "1-100 for jpg/webp/avif (lower = smaller)."},
            "max_kb": {"type": "integer", "description": "Compress until the file is under this many KB (jpg)."},
            "crop": {"type": "string", "description": "WIDTHxHEIGHT+X+Y, e.g. '800x600+100+50'."},
            "rotate": {"type": "integer", "description": "Degrees clockwise."},
            "flip": {"type": "string", "enum": ["horizontal", "vertical"]},
            "grayscale": {"type": "boolean"},
            "strip_metadata": {"type": "boolean", "description": "Remove EXIF (camera, GPS location)."},
            "format": {"type": "string", "enum": list(WRITE)},
        }, "required": ["path"]},
    },
}


def build(src, arguments: dict) -> "tuple[list, str]":
    """The magick argv (minus the output) and the output extension."""
    coder = READ[src.suffix.lower()]
    ops, done = [], []
    w, h, pct = arguments.get("width"), arguments.get("height"), arguments.get("percent")
    crop = (arguments.get("crop") or "").strip()
    if crop:
        if not re.fullmatch(r"\d{1,5}x\d{1,5}\+\d{1,5}\+\d{1,5}", crop):
            raise ValueError("crop looks like 800x600+100+50")
        ops += ["-crop", crop, "+repage"]
        done.append(f"cropped to {crop}")
    if arguments.get("rotate"):
        deg = int(arguments["rotate"]) % 360
        ops += ["-rotate", str(deg)]
        done.append(f"rotated {deg}°")
    if arguments.get("flip") == "horizontal":
        ops += ["-flop"]
        done.append("flipped left-right")
    elif arguments.get("flip") == "vertical":
        ops += ["-flip"]
        done.append("flipped upside down")
    if pct:
        p = max(1, min(int(pct), 1000))
        ops += ["-resize", f"{p}%"]
        done.append(f"scaled to {p}%")
    elif w or h:
        geo = f"{int(w) if w else ''}x{int(h) if h else ''}"
        ops += ["-resize", geo]
        done.append(f"resized to {geo.strip('x')} (keeping its proportions)")
    if arguments.get("grayscale"):
        ops += ["-colorspace", "Gray"]
        done.append("made grayscale")
    if arguments.get("strip_metadata") or arguments.get("max_kb") or arguments.get("quality"):
        ops += ["-strip"]
        if arguments.get("strip_metadata"):
            done.append("metadata removed")
    fmt = (arguments.get("format") or "").lower() or ("jpg" if arguments.get("max_kb") else
                                                      src.suffix.lower().lstrip(".").replace("jpeg", "jpg"))
    if fmt not in WRITE:
        fmt = "png"
    if arguments.get("max_kb"):
        kb = max(10, min(int(arguments["max_kb"]), 50000))
        ops += ["-define", f"jpeg:extent={kb}KB"]
        done.append(f"compressed to under {kb} KB")
    elif arguments.get("quality"):
        q = max(1, min(int(arguments["quality"]), 100))
        ops += ["-quality", str(q)]
        done.append(f"quality {q}")
    return ["magick", f"{coder}:{src}", *ops], fmt, done


def _run(arguments: dict) -> str:
    tool = shutil.which("magick")
    if not tool:
        return "Editing pictures needs ImageMagick (the imagemagick package)."
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    if src.suffix.lower() not in READ:
        return f"I edit pictures ({', '.join(sorted(READ))}); {src.name} is not one."
    try:
        argv, fmt, done = build(src, arguments)
    except ValueError as e:
        return f"Not done: {e}."
    if not done and fmt == src.suffix.lower().lstrip(".").replace("jpeg", "jpg"):
        return "What should I change - size, compression, crop, rotation, colour, metadata or format?"
    dst = src.with_name(f"{src.stem}-edited.{fmt}")
    n = 1
    while dst.exists():
        dst = src.with_name(f"{src.stem}-edited-{n}.{fmt}")
        n += 1
    try:
        r = subprocess.run(argv + [f"{fmt}:{dst}"], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        dst.unlink(missing_ok=True)
        return "The edit took longer than two minutes, so I stopped it."
    if r.returncode != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        return f"ImageMagick could not edit {src.name}: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
    before, after = src.stat().st_size, dst.stat().st_size
    what = ", ".join(done) or f"converted to {fmt}"
    return (f"Saved {dst.name} ({what}): {after / 1024:.0f} KB, was {before / 1024:.0f} KB. "
            f"The original is unchanged.")


SKILLS = [Skill(name="edit_image", schema=_SCHEMA, run=_run)]
