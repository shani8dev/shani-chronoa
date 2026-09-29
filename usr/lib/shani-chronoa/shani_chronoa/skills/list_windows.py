"""Skill: list the windows currently open on the desktop.

The first of three window skills, and the only one that only reads.

**X11 only, and it says so.** `xdotool` drives X11; under Wayland it is either
absent or, worse, present and talking to an Xwayland server that only knows
about X11 clients - so it would list a fraction of the windows and look like it
had listed them all. The skill therefore detects Wayland first and refuses,
rather than returning a plausible partial list. This is a known limitation of
the whole window group, and pretending otherwise on a GNOME, Plasma or COSMIC
session would be exactly the kind of quiet wrongness this project avoids.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 15
_MAX_WINDOWS = 60

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_windows",
        "description": (
            "List the windows currently open, with their title, class and "
            "geometry. X11 only - it reports plainly when it cannot work on "
            "this session rather than returning a partial list."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def session_problem() -> str:
    """Why window control cannot work here, or '' when it can."""
    session = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return (
            "Window control is X11-only here, and this is a Wayland session. "
            "There is no verified way to enumerate windows across GNOME, Plasma "
            "and COSMIC, so nothing is listed rather than a partial list. "
            "Chronoa does not drive the compositor directly."
        )
    if not os.environ.get("DISPLAY"):
        return (
            "No DISPLAY is set, so there is no X11 session to ask. This may be a "
            "Wayland session or a headless login."
        )
    if shutil.which("xdotool") is None:
        return files.tool_missing("xdotool", "list or control windows")
    return ""


def _run(arguments: dict) -> str:
    problem = session_problem()
    if problem:
        return f"Could not list windows: {problem}"
    try:
        proc = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", ""],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"xdotool did not answer within {_TIMEOUT}s, so no windows are listed."
    except OSError as exc:
        return f"Could not list windows: {exc}"

    ids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not ids:
        note = (proc.stderr or "").strip()
        return (
            "xdotool found no visible windows with a name. Some windows really "
            "do have no title set, so this is not proof the desktop is empty."
            + (f" xdotool said: {note}" if note else "")
        )

    rows = []
    for wid in ids[:_MAX_WINDOWS]:
        def ask(*args):
            try:
                out = subprocess.run(["xdotool", *args, wid], capture_output=True,
                                     text=True, timeout=_TIMEOUT, check=False)
                return out.stdout.strip() if out.returncode == 0 else ""
            except (subprocess.TimeoutExpired, OSError):
                return ""
        name = ask("getwindowname")
        cls = ask("getwindowclassname")
        geo = ask("getwindowgeometry")
        rows.append((wid, name, cls, geo))

    lines = [f"{len(ids)} visible window(s); showing {len(rows)}:"]
    for wid, name, cls, geo in rows:
        geo_text = ""
        if geo:
            parts = [p for p in geo.splitlines() if p.strip()]
            geo_text = " " + parts[1].strip() if len(parts) > 1 else ""
        lines.append(
            f"  {wid:>10}  {cls or '(no class)':<24} {(name or '(no title)')[:60]}{geo_text}"
        )
    if len(ids) > len(rows):
        lines.append(f"  ... {len(ids) - len(rows)} more not shown (limit {_MAX_WINDOWS}).")
    lines.append("  Use the window id with focus_window or close_window.")
    return "\n".join(lines)


SKILLS = [Skill(name="list_windows", schema=SCHEMA, run=_run)]
