"""Skill: list or change the keyboard layout.

Reuses the existing `input-control-enabled` gate rather than minting one. A
keyboard layout is not a lesser form of key pressing - it decides which character
each key produces, so changing it changes what every subsequent keystroke does,
including in a terminal and including in a password field. `press_key` already
reuses that same gate for the same reason, and a second key here would imply a
finer distinction than exists.

Prefers the user-level mechanism. `setxkbmap` changes the layout for this
session without root and without touching the system configuration, so it is
tried first. `localectl` is the system-wide answer and needs privilege; it is
offered as a fallback and its failure is reported as a privilege problem rather
than as "layout unchanged", because those read identically from the outside.

Honesty rules:

- **The layout is read back after setting it.** `setxkbmap` exiting 0 and the
  layout actually being what was asked for are different claims, and X keyboard
  groups have their own state that can disagree with the request.
- A layout name that is not in the system's own list is refused rather than
  passed through, because `setxkbmap` will accept an arbitrary string and fail
  later and less clearly.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_MAX_LISTED = 60

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_keyboard_layout",
        "description": (
            "List the keyboard layouts this system knows, report the one in "
            "use, or switch to another. Changing it requires the existing "
            "'input-control-enabled' consent key; listing needs no permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'list', 'status' or 'set'. Defaults to status.",
                },
                "layout": {
                    "type": "string",
                    "description": "The layout to switch to, e.g. us, de, in. For 'set'.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed."""
    if not config.input_control_enabled:
        return False, (
            "acting on the keyboard is turned off (enable "
            "'input-control-enabled' in Settings). The keyboard layout reuses "
            "that permission rather than having its own, because it decides what "
            "every later keystroke produces."
        )
    return True, ""


def _run_cmd(argv: list):
    if shutil.which(argv[0]) is None:
        return None
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _current() -> str:
    proc = _run_cmd(["setxkbmap", "-query"])
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if line.startswith("layout:"):
                return line.split(":", 1)[1].strip()
    proc = _run_cmd(["localectl", "status"])
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            if "X11 Layout" in line:
                return line.split(":", 1)[1].strip()
    return "UNKNOWN"


def _available() -> "list | None":
    proc = _run_cmd(["localectl", "list-x11-keymap-layouts"])
    if proc is not None and proc.returncode == 0:
        return [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    proc = _run_cmd(["setxkbmap", "-query"])
    if proc is not None and proc.returncode == 0:
        return None
    return None


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("list", "status", "set"):
        return f"Action must be list, status or set, not {action!r}."

    have_tool = any(shutil.which(t) for t in ("setxkbmap", "localectl"))
    if not have_tool:
        return files.tool_missing(
            "setxkbmap", "read or change the keyboard layout (xorg-setxkbmap)")

    if action == "status":
        return f"Keyboard layout in use: {_current()}"

    if action == "list":
        layouts = _available()
        if layouts is None:
            return ("The list of known layouts could not be read: localectl did "
                    "not answer and setxkbmap -query does not enumerate them. "
                    "That is UNKNOWN, not an empty list.")
        shown = layouts[:_MAX_LISTED]
        out = [f"{len(layouts)} layout(s) this system knows about:"]
        out += [f"  {name}" for name in shown]
        if len(layouts) > _MAX_LISTED:
            out.append(f"  and {len(layouts) - _MAX_LISTED} more")
        out.append(f"In use: {_current()}")
        return "\n".join(out)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the keyboard layout: {reason}"

    layout = (arguments.get("layout") or "").strip()
    if not layout:
        return "No layout was named, so there is nothing to switch to."

    known = _available()
    if known is not None and layout not in known:
        return (f"Refusing to set layout {layout!r}: it is not among the "
                f"{len(known)} layouts this system knows about. Use "
                f"action='list' to see them.")

    before = _current()
    if layout == before:
        return f"The layout is already {layout}, so nothing was changed."

    # User-level first: no root, and it does not alter system configuration.
    proc = _run_cmd(["setxkbmap", layout])
    if proc is not None and proc.returncode == 0:
        after = _current()
        if after == layout:
            return f"Keyboard layout is now {layout} (verified by reading it back)."
        return (f"setxkbmap reported success but the layout reads back as "
                f"{after}, so this is not verified.")

    # System-wide fallback. Needs privilege, and saying so is more useful than
    # reporting the unchanged layout as though the request had been declined.
    if shutil.which("localectl") is not None:
        sys_proc = _run_cmd(["localectl", "set-x11-keymap-layout", layout])
        if sys_proc is not None and sys_proc.returncode == 0:
            after = _current()
            if after == layout:
                return f"Keyboard layout is now {layout}, system-wide."
            return (f"localectl reported success but the layout reads back as "
                    f"{after}, so this is not verified.")
        detail = (sys_proc.stderr or "").strip() if sys_proc is not None else "not installed"
        return (f"Could not set the layout. The per-session change failed and "
                f"the system-wide one did not work either: {detail or 'no detail'}"
                + ". Changing it system-wide needs root, so on this account the "
                  "layout is unchanged.")

    detail = (proc.stderr or proc.stdout or "").strip() if proc is not None else ""
    return (f"Could not set the layout to {layout}: "
            f"{detail or 'no detail was given'}. Nothing was changed.")


SKILLS = [Skill(name="set_keyboard_layout", schema=SCHEMA, run=_run)]
