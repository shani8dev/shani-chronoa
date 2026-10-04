"""Skill: turn the blue-light filter on or off, and report its schedule.

Night light / night shift is on for most people most of the evening, and it
changes the colour of every pixel on the screen. That makes it a real source of
mismatch between what a screenshot shows and what the user sees - an assistant
that reads a screenshot at 9pm is reading a warmer image than the user is
looking at, and nothing in the project accounted for that.

Gated by `appearance-control-enabled`, like the other appearance changes.

KDE is not supported and says so: Plasma's night light is configured through
KWin's colour-management settings, and the supported interface is the System
Settings panel rather than a command.

Honesty rules:

- **Read back after writing.** The preference and the *schedule* are separate
  settings, and a machine can have the filter enabled with the schedule turned
  off, which means it is on but not currently doing anything. That state is
  reported as two facts rather than as one "on".
"""

from __future__ import annotations

import shutil

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa import desktop_session

_CONSENT_KEY = "appearance-control-enabled"
_TIMEOUT = 20


def _gsettings(*args: str):
    return desktop_session.gsettings(*args, timeout=_TIMEOUT)

_SCHEME = "org.gnome.settings-daemon.plugins.color"
_KEY = "night-light-enabled"
_SCHED_KEY = "night-light-schedule"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "toggle_night_light",
        "description": (
            "Report or turn on/off the blue-light filter (GNOME night light), "
            "reporting the schedule separately from the on/off state, because a "
            "filter that is enabled with its schedule off is not currently "
            "doing anything. KDE is not supported and will say so. Requires the "
            "'appearance-control-enabled' consent key; reporting needs no such "
            "permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status', 'on' or 'off'. Defaults to status.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            "changing the blue-light filter is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting its state needs no such "
            f"permission."
        )
    return True, ""


def _read(key: str):
    proc = _gsettings("get", _SCHEME, key)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "on", "off"):
        return f"Action must be status, on or off, not {action!r}."

    desktop = desktop_session.kind()
    if desktop == "kde":
        return ("This is a KDE session. Plasma's night light is configured "
                "through KWin's colour-management settings and has no supported "
                "command-line interface, so it is not changed from here - System "
                "Settings is the way.")
    if desktop == "unknown":
        return ("The desktop session could not be identified, so this does not "
                "know which mechanism to use.")

    if shutil.which("gsettings") is None:
        return "gsettings is not installed, so the blue-light filter is UNKNOWN."

    if action == "status":
        enabled = _read(_KEY)
        schedule = _read(_SCHED_KEY)
        if enabled is None:
            return ("Whether the blue-light filter is on is UNKNOWN - the "
                    "setting could not be read.")
        on = enabled == "true"
        lines = [f"Blue-light filter (night light): {'on' if on else 'off'}"]
        if schedule is None:
            lines.append("Its schedule is UNKNOWN - that setting could not be read.")
        elif schedule == "false":
            lines.append("Its automatic schedule is OFF, so although the filter is "
                         f"{'enabled' if on else 'disabled'} it is not switching "
                         "itself on and off through the day.")
        else:
            lines.append(f"Automatic schedule: on ({schedule})")
        lines.append("This shifts the colour of every pixel, so a screenshot read "
                     "now is warmer than it looks to someone with it off.")
        return "\n".join(lines)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the blue-light filter: {reason}"

    want = "true" if action == "on" else "false"
    proc = _gsettings("set", _SCHEME, _KEY, want)
    if proc is None or proc.returncode != 0:
        detail = (proc.stderr or "").strip() if proc else "gsettings is not installed"
        return f"Could not change the blue-light filter: {detail or 'no detail'}."

    after = _read(_KEY)
    if after == want:
        schedule = _read(_SCHED_KEY)
        note = ""
        if schedule == "false":
            note = (" Its automatic schedule is off, so this is now on "
                    "permanently rather than on a timetable.")
        return f"Blue-light filter is now {action} (verified by reading it back).{note}"
    return (f"gsettings accepted the change but the setting reads back as {after}, "
            f"so this is not verified.")


SKILLS = [Skill(name="toggle_night_light", schema=SCHEMA, run=_run)]
