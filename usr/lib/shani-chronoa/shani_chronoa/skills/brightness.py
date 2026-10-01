"""Screen-brightness skill: read or set the panel backlight.

Writes `/sys/class/backlight/<panel>/brightness` directly rather than shelling
out to `brightnessctl`, which is not a ShaniOS dependency and was absent on the
machine this was written on. The sysfs attribute is the same one `brightnessctl`
ultimately writes, so nothing is lost by not depending on it, and one fewer
package is needed for a machine whose brightness control is already in sysfs.

**Writes need privileges the plain sense does not.** The backlight node is
root-owned, so a set will fail with EACCES for an unprivileged caller. That is
reported as exactly that - a permission failure - rather than as a silent
success, because the alternative is a user being told their screen is at 40%
when nothing changed.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_BACKLIGHT = Path("/sys/class/backlight")
_RANGE = re.compile(r"^brightness$")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_brightness",
        "description": (
            "Get or set screen brightness as a percentage. With no level, "
            "reports the current brightness of each panel. With a level, sets "
            "it. Writing needs root - the backlight node is root-owned - and a "
            "permission failure is reported as such rather than as success."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "description": "Target brightness, 0-100. Omit to only read.",
                }
            },
        },
    },
}


def _panels() -> List[dict]:
    out = []
    try:
        entries = sorted(_BACKLIGHT.iterdir())
    except OSError as exc:
        logger.debug("cannot list %s: %s", _BACKLIGHT, exc)
        return out
    for entry in entries:
        current = _read_int(entry / "brightness")
        if current is None:
            continue
        out.append({
            "name": entry.name,
            "brightness": current,
            "max_brightness": _read_int(entry / "max_brightness"),
        })
    return out


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _logind_set(panel: str, raw: int) -> bool:
    """systemd-logind's Session.SetBrightness: lets the seat's own user set the
    backlight with no root and no udev rule - the path brightnessctl and the
    desktops use. True when logind accepted it."""
    try:
        r = subprocess.run(["busctl", "call", "org.freedesktop.login1",
                            "/org/freedesktop/login1/session/auto", "org.freedesktop.login1.Session",
                            "SetBrightness", "ssu", "backlight", panel, str(raw)],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ddc(level: Optional[int]) -> str:
    """External monitors over DDC/CI with ddcutil (VCP feature 0x10, brightness).
    A desktop has no backlight node at all, so this is its only brightness."""
    if not shutil.which("ddcutil"):
        return ""
    try:
        if level is None:
            r = subprocess.run(["ddcutil", "getvcp", "10", "--brief"], capture_output=True, text=True, timeout=20)
            vals = re.findall(r"VCP 10 C (\d+) (\d+)", r.stdout)
            return "; ".join(f"monitor {i + 1}: {round(100 * int(c) / int(m))}%" for i, (c, m) in enumerate(vals) if int(m))
        r = subprocess.run(["ddcutil", "setvcp", "10", str(level)], capture_output=True, text=True, timeout=20)
        return f"external monitor brightness set to {level}%" if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _set(panel: str, raw: int) -> Union[str, str]:
    target = _BACKLIGHT / panel / "brightness"
    try:
        target.write_text(str(raw))
    except PermissionError:
        if _logind_set(panel, raw):
            return ""
        return (
            f"Could not set brightness on {panel}: permission denied. The "
            "backlight node is root-owned, so this needs to run as root or "
            "with a udev rule granting the seat write access. The brightness "
            "was NOT changed."
        )
    except OSError as exc:
        return f"Could not set brightness on {panel}: {exc}. The brightness was NOT changed."
    return ""


def run(arguments: dict) -> str:
    panels = _panels()
    if not panels:
        raw = arguments.get("level")
        try:
            want = None if raw is None or str(raw).strip() == "" else max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            want = None
        ddc = _ddc(want)
        if ddc:
            return ddc[:1].upper() + ddc[1:] + "."
        return (
            "This machine exposes no backlight node, so its screen brightness "
            "cannot be read or set. That is expected on a desktop with no "
            "internal panel."
        )

    raw_level = arguments.get("level")
    if raw_level is None or str(raw_level).strip() == "":
        lines = []
        for panel in panels:
            pct = (
                round(100.0 * panel["brightness"] / panel["max_brightness"])
                if panel["max_brightness"] else None
            )
            suffix = f" ({pct}%)" if pct is not None else ""
            lines.append(f"{panel['name']}: {panel['brightness']}/{panel['max_brightness']}{suffix}")
        return "\n".join(lines)

    try:
        level = int(raw_level)
    except (TypeError, ValueError):
        return f"{raw_level!r} is not a whole number; brightness takes 0-100."
    if not 0 <= level <= 100:
        return f"Brightness must be between 0 and 100, not {level}."

    messages = []
    for panel in panels:
        target = (
            round(panel["brightness"] * level / 100.0)
            if panel["max_brightness"] is None
            else round(panel["max_brightness"] * level / 100.0)
        )
        if panel["max_brightness"] is not None:
            target = max(0, min(target, panel["max_brightness"]))
        error = _set(panel["name"], target)
        if error:
            messages.append(error)
        else:
            messages.append(f"{panel['name']} set to {level}% ({target})")

    if any("NOT changed" in m for m in messages):
        return "\n".join(messages)
    return "Set screen brightness: " + "; ".join(messages)


_SKILL = Skill(name="set_brightness", schema=SCHEMA, run=run)

SKILLS = [_SKILL]
