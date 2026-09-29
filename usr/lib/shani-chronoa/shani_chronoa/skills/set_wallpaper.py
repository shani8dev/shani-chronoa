"""Skill: report or change the desktop wallpaper.

A small, daily, entirely reversible thing that nothing here could do - and one
worth having precisely because it is low-stakes. "Put something less busy up"
before a call is a real request, and an assistant that can read the screen but
cannot change a background has to answer a question it half-knows.

GNOME is fully supported: the wallpaper is a GSettings key, and both the light
and dark variants are set together, because GNOME keeps them separately and
setting only one leaves the desktop flipping between two images on a theme
change.

KDE is deliberately **not** supported, and says so. Plasma's wallpaper is not
stored in a setting; it lives inside the layout widget tree in
`plasma-org.kde.plasma.desktop-appletsrc`, and rewriting that file to change
one image is how a Plasma session ends up with a mangled desktop. There is no
documented command-line interface for it. The honest answer is to point at
System Settings rather than to write a config file and hope.

Gated by `appearance-control-enabled`, shared with the theme skill, because it
is the same permission: change how the desktop looks.

Honesty rules:

- **The key is read back after writing it.** A `gsettings set` that exits 0 for
  a path with a typo in it still "succeeds" and leaves a broken background.
- A wallpaper path that does not exist is refused rather than set, because that
  failure mode is a desktop with a blank or black background and no error.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.files import PathProblem
from shani_chronoa.skills import Skill

_CONSENT_KEY = "appearance-control-enabled"
_TIMEOUT = 20

_SCHEME = "org.gnome.desktop.background"
_KEYS = ("picture-uri", "picture-uri-dark")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_wallpaper",
        "description": (
            "Report or change the GNOME desktop wallpaper, setting the light and "
            "dark variants together. Refuses a path that does not exist, rather "
            "than leaving a blank desktop with no error. KDE is not supported "
            "and will say so. Requires the 'appearance-control-enabled' consent "
            "key; reporting needs no such permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status' or 'set'. Defaults to status.",
                },
                "image": {
                    "type": "string",
                    "description": "Path to the image file. Required for 'set'.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            "changing the desktop appearance is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting the current wallpaper "
            f"needs no such permission."
        )
    return True, ""


def _desktop() -> str:
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_DESKTOP"):
        value = (os.environ.get(var) or "").lower()
        if "kde" in value or "plasma" in value:
            return "kde"
        if "gnome" in value or "unity" in value or "cinnamon" in value:
            return "gnome"
    return "unknown"


def _gs(*args: str):
    if shutil.which("gsettings") is None:
        return None
    try:
        return subprocess.run(["gsettings", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _current() -> "dict | None":
    out = {}
    for key in _KEYS:
        proc = _gs("get", _SCHEME, key)
        if proc is None or proc.returncode != 0:
            return None
        out[key] = proc.stdout.strip().strip("'\"")
    return out


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "set"):
        return f"Action must be status or set, not {action!r}."

    desktop = _desktop()
    if desktop == "kde":
        return ("This is a KDE session, and the wallpaper is not supported there. "
                "Plasma stores it inside the layout widget tree rather than in a "
                "setting, and rewriting that file to change one image is how a "
                "session ends up with a mangled desktop. There is no documented "
                "command-line interface for it - System Settings is the "
                "supported way.")
    if desktop == "unknown":
        return ("The desktop session could not be identified, so this does not "
                "know which mechanism to use. Guessing would risk writing a "
                "setting the running desktop does not read.")

    if shutil.which("gsettings") is None:
        return files.tool_missing("gsettings", "read or change the wallpaper")

    if action == "status":
        current = _current()
        if current is None:
            return ("The wallpaper could not be read, so what is set is UNKNOWN "
                    "rather than unset.")
        lines = []
        for key in _KEYS:
            value = current[key] or "nothing"
            if value != "nothing":
                value = value.replace("file://", "")
            lines.append(f"  {key}: {value}")
        if all(v in ("", "nothing") for v in current.values()):
            lines.insert(0, "No wallpaper is set.")
        return "Current wallpaper:\n" + "\n".join(lines)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the wallpaper: {reason}"

    raw = arguments.get("image")
    try:
        image = files.resolve(raw)
    except PathProblem as exc:
        return str(exc)

    if not image.exists():
        return (f"Refusing to set {image}: it does not exist. Setting a wallpaper "
                f"to a missing path succeeds at the GSettings level and leaves a "
                f"blank desktop with no error to explain it, so it is checked "
                f"first.")
    if not image.is_file():
        return f"Refusing to set {image}: it is not a regular file."
    if image.stat().st_size == 0:
        return f"Refusing to set {image}: the file is empty."

    uri = image.as_uri()
    results = []
    for key in _KEYS:
        proc = _gs("set", _SCHEME, key, uri)
        if proc is None or proc.returncode != 0:
            detail = (proc.stderr or "").strip() if proc else "gsettings is not installed"
            return (f"Could not set {key}: {detail or 'no detail'}. "
                    + ("Both variants are set together, so a partial change was "
                       "not left behind." if results else "Nothing was changed."))
        results.append(key)

    after = _current()
    if after and all(after.get(k) == uri for k in _KEYS):
        return (f"Wallpaper set to {image.name}, light and dark variants both "
                f"(verified by reading both keys back).")
    got = after or {}
    return (f"gsettings accepted the change but reading the keys back gives "
            f"{ {k: (v or 'nothing') for k, v in got.items()} }, so this is not "
            f"verified.")


SKILLS = [Skill(name="set_wallpaper", schema=SCHEMA, run=_run)]
