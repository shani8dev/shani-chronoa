"""Skill: send a key or key combination to the focused window.

Distinct from `type_text`, which types a literal string. This sends keys by
name - Return, Escape, Tab, or a combination like `ctrl+shift+t` - which is how
a person triggers a shortcut without touching the keyboard.

Gated by the existing `input-control-enabled` key rather than a new one, and
deliberately so: this is the same risk as moving the pointer and typing, and
splitting them would give the illusion of finer control than exists. A key
combination can do anything a mouse and keyboard can do, including confirming a
dialog the user cannot see.

X11 only, via `xdotool`, and it refuses under Wayland for the same reason the
window skills do: an X11-only tool on a Wayland session silently addresses the
wrong server.
"""

from __future__ import annotations

import os
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.skills.list_windows import session_problem

_TIMEOUT = 15

#: Names a person is likely to say, mapped to what xdotool expects. Anything
#: not listed is passed through, so `XF86AudioPlay` and `ctrl+alt+Delete` work
#: without a table entry.
_ALIASES = {
    "return": "Return", "enter": "Return", "newline": "Return",
    "escape": "Escape", "esc": "Escape",
    "tab": "Tab", "space": "space", "spacebar": "space",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "play": "XF86AudioPlay", "pause": "XF86AudioPause",
    "next": "XF86AudioNext", "previous": "XF86AudioPrev",
    "volumeup": "XF86AudioRaiseVolume", "volumedown": "XF86AudioLowerVolume",
    "mute": "XF86AudioMute",
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "press_key",
        "description": (
            "Press a key or key combination in the focused window, e.g. "
            "'Return', 'Escape', 'Tab' or 'ctrl+shift+t'. This sends a key, not "
            "text - use type_text to type a string. Requires the "
            "'input-control-enabled' consent key. X11 only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Key name, or '+'-separated combination, e.g. "
                        "'Return', 'alt+F4', 'ctrl+alt+Delete'."
                    ),
                },
                "repeat": {
                    "type": "integer",
                    "description": "How many times to press it. Defaults to 1.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.input_control_enabled:
        return (
            "Refusing to press the key: input control is turned off (enable "
            "'input-control-enabled' in Settings). A key combination can do "
            "anything a person at this machine could, including confirming a "
            "dialog the user cannot see."
        )
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    problem = "" if wayland else session_problem()
    if problem:
        return f"Could not press the key: {problem}"

    raw = (arguments.get("key") or "").strip()
    if not raw:
        return "No key was named. Give one key name or a '+' separated combination."
    parts = [p for p in raw.split("+") if p]
    if not parts:
        return f"{raw!r} does not contain a key name."
    mapped = []
    for part in parts:
        lowered = part.strip().lower()
        if lowered in _ALIASES:
            mapped.append(_ALIASES[lowered])
        elif part.startswith(("ctrl+", "alt+", "shift+", "super+", "meta+")):
            mapped.append(lowered)
        else:
            mapped.append(part if len(part) > 1 else part.capitalize())
    keyspec = "+".join(mapped)

    try:
        count = max(1, min(int(arguments.get("repeat") or 1), 50))
    except (TypeError, ValueError):
        count = 1

    if wayland:
        # xdotool cannot drive a Wayland session; the desktop portal can (GNOME and Plasma).
        from shani_chronoa import portal
        try:
            syms = [portal.keysym_for(p.strip()) for p in raw.split("+") if p.strip()]
            with portal.RemoteInput(portal.KEYBOARD) as ri:
                for _ in range(count):
                    ri.chord(syms)
        except (portal.PortalError, ValueError) as exc:
            return f"Could not press {raw} through the desktop portal: {exc}."
        return f"Pressed {raw}" + (f" {count} times" if count > 1 else "") + " (through the desktop portal)."

    for _ in range(count):
        try:
            proc = subprocess.run(["xdotool", "key", "--clearmodifiers", keyspec],
                                  capture_output=True, text=True,
                                  timeout=_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            return f"Sent {keyspec} but xdotool did not answer in {_TIMEOUT}s."
        except OSError as exc:
            return f"Could not send {keyspec}: {exc}"
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            return f"xdotool refused {keyspec} (exit {proc.returncode})" + (
                f": {detail[-1]}" if detail else ". Check the key name.")

    times = f" {count} times" if count > 1 else ""
    return (
        f"Sent {keyspec}{times} to the focused window. Nothing was verified - "
        f"what that key did depends on what had focus."
    )


SKILLS = [Skill(name="press_key", schema=SCHEMA, run=_run)]
