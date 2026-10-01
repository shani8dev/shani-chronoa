"""Skill: colour codes - hex, RGB and HSL, and the common CSS colour names.
'What is #ff8800 in RGB', 'hex for teal'. Pure Python; nothing leaves the machine."""

import colorsys
import re

from shani_chronoa.skills import Skill

NAMES = {"black": "000000", "white": "ffffff", "red": "ff0000", "green": "008000", "lime": "00ff00",
         "blue": "0000ff", "yellow": "ffff00", "cyan": "00ffff", "aqua": "00ffff", "magenta": "ff00ff",
         "fuchsia": "ff00ff", "silver": "c0c0c0", "gray": "808080", "grey": "808080", "maroon": "800000",
         "olive": "808000", "purple": "800080", "teal": "008080", "navy": "000080", "orange": "ffa500",
         "pink": "ffc0cb", "brown": "a52a2a", "gold": "ffd700", "indigo": "4b0082", "violet": "ee82ee",
         "coral": "ff7f50", "salmon": "fa8072", "turquoise": "40e0d0", "beige": "f5f5dc", "lavender": "e6e6fa",
         "khaki": "f0e68c", "crimson": "dc143c", "tomato": "ff6347", "chocolate": "d2691e", "skyblue": "87ceeb"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_color",
        "description": "Convert a colour between hex (#ff8800), rgb(255, 136, 0), hsl and its CSS name.",
        "parameters": {"type": "object", "properties": {
            "color": {"type": "string", "description": "e.g. '#ff8800', 'rgb(255,136,0)', 'hsl(32,100%,50%)', 'teal'."}},
            "required": ["color"]},
    },
}


def parse(text: str) -> "tuple[int, int, int]":
    t = text.strip().lower().replace(" ", "")
    if t in NAMES:
        t = "#" + NAMES[t]
    m = re.fullmatch(r"#?([0-9a-f]{6}|[0-9a-f]{3})", t)
    if m:
        h = m.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgb\((\d+),(\d+),(\d+)\)", t)
    if m:
        rgb = tuple(int(x) for x in m.groups())
        if all(0 <= x <= 255 for x in rgb):
            return rgb
    m = re.fullmatch(r"hsl\((\d+(?:\.\d+)?),(\d+(?:\.\d+)?)%,(\d+(?:\.\d+)?)%\)", t)
    if m:
        h, s, l = (float(x) for x in m.groups())
        r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l / 100, s / 100)
        return round(r * 255), round(g * 255), round(b * 255)
    raise ValueError(f"I can't read '{text}' as a colour")


def _run(arguments: dict) -> str:
    try:
        r, g, b = parse(str(arguments.get("color") or ""))
    except ValueError as e:
        return f"{e}."
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hexv = f"#{r:02x}{g:02x}{b:02x}"
    name = next((n for n, v in NAMES.items() if v == hexv[1:]), None)
    return (f"{hexv} = rgb({r}, {g}, {b}) = hsl({h * 360:.0f}, {s * 100:.0f}%, {l * 100:.0f}%)"
            + (f" - the CSS colour '{name}'." if name else "."))


SKILLS = [Skill(name="convert_color", schema=_SCHEMA, run=_run)]
