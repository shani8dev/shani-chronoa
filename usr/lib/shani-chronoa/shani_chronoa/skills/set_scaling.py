"""Skill: report or change the display's text scaling.

Text that is too small is the single most common reason someone reaches for the
accessibility settings, and it is also the reason an assistant that can *see*
the screen can be useless on it: the thing it read may be illegible to the person
it is helping. There is no way to correct that without being able to change the
scale.

Only **text** scaling, deliberately. GNOME's output scale (HiDPI, fractional
scaling) is a compositor property changed through the display-configuration
monitor API, not a setting, and driving it from here means moving a window
between outputs and re-negotiating a mode - which can black-screen a session.
Text scaling is a font size, it applies immediately, and it is reversible. The
module says so rather than implying this is all of scaling.

Gated by `appearance-control-enabled`. Setting text too large is not dangerous
but it does make the machine harder to use, and the same permission should
govern every "change how this looks" action.

Honesty rules:

- **The factor is read back after writing it.** `gsettings set` clamps and
  rounds values, so setting 1.15 can land on something else entirely.
- The accepted range is enforced, and the clamped value reported if that is what
  the desktop actually applied.
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

_SCHEME = "org.gnome.desktop.interface"
_KEY = "text-scaling-factor"
#: GNOME's own range. Values outside it are clamped by the desktop, so they are
#: refused here rather than silently becoming something else.
_MIN, _MAX = 0.5, 3.0

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_scaling",
        "description": (
            "Report or change the desktop's text scaling factor (1.0 is normal, "
            "1.5 is 150%). Text scaling only - display-resolution and HiDPI "
            "scaling are not changed by this, because driving those black-screens "
            "sessions. Requires the 'appearance-control-enabled' consent key; "
            "reporting needs no such permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status' or 'set'. Defaults to status.",
                },
                "factor": {
                    "type": "number",
                    "description": "The text scaling factor, 0.5 to 3.0. Required for 'set'.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            "changing text scaling is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting the current factor needs "
            f"no such permission."
        )
    return True, ""


def _current():
    proc = _gsettings("get", _SCHEME, _KEY)
    if proc is None or proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "set"):
        return f"Action must be status or set, not {action!r}."

    desktop = desktop_session.kind()
    if desktop == "unknown":
        return ("The desktop session could not be identified, so this does not "
                "know which mechanism to use.")
    if desktop == "kde":
        if shutil.which("kscreen-doctor") is None:
            return ("This is a KDE session and kscreen-doctor is not installed, "
                    "so display scaling cannot be changed or read from here. "
                    "KDE's own text size lives in System Settings, and its output "
                    "scaling is a per-output compositor property rather than a "
                    "setting.")
        return ("This is a KDE session. Only text scaling is offered by this "
                "skill, and KDE's text size is a font configuration setting rather "
                "than a single factor, so there is no equivalent value to set "
                "here - System Settings is the supported way.")

    if shutil.which("gsettings") is None:
        return ("gsettings is not installed, so the text scaling factor is "
                "UNKNOWN and cannot be changed.")

    if action == "status":
        current = _current()
        if current is None:
            return "The text scaling factor could not be read, so it is UNKNOWN."
        percent = round(current * 100)
        return (f"Text scaling factor: {current} ({percent}% of normal text size). "
                f"This is text only; the display's own resolution scaling is a "
                f"separate thing this does not change.")

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change text scaling: {reason}"

    raw = arguments.get("factor")
    if raw is None or raw == "":
        return "No scaling factor was given, so there is nothing to set."
    try:
        factor = float(raw)
    except (TypeError, ValueError):
        return f"{raw!r} is not a number, so it cannot be a scaling factor."

    if not (_MIN <= factor <= _MAX):
        return (f"Refusing to set {factor}: the accepted range is {_MIN} to "
                f"{_MAX}, and the desktop clamps anything outside it, so the "
                f"result would be a different number than the one asked for.")

    proc = _gsettings("set", _SCHEME, _KEY, repr(factor))
    if proc is None or proc.returncode != 0:
        detail = (proc.stderr or "").strip() if proc else "gsettings is not installed"
        return f"Could not set the text scaling factor: {detail or 'no detail'}."

    after = _current()
    if after == factor:
        return (f"Text scaling is now {after} ({round(after * 100)}%), verified by "
                f"reading it back.")
    if after is None:
        return "gsettings accepted the change but the value could not be read back."
    return (f"Asked for {factor} and the desktop reports {after}, so it clamped or "
            f"rounded the value rather than applying what was requested.")


SKILLS = [Skill(name="set_scaling", schema=SCHEMA, run=_run)]
