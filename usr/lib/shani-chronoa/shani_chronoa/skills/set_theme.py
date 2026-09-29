"""Skill: read or change the desktop's light/dark appearance.

Nothing in this project could change how the desktop looks, and light/dark is
the single most toggled preference on a machine - it tracks the time of day, the
room, and the projector you just plugged into. An assistant that can read the
screen but cannot follow it into a dark room is only half an assistant.

Handles GNOME and KDE separately, because they do not share a mechanism at all.
GNOME keeps the preference in GSettings and it is a one-line write. KDE keeps the
*look* in a `.krc` file and the colour scheme in `kdeglobals`, and the supported
way to change either is `plasma-lookandfeeltool`. Writing KDE's config files
directly is what breaks a Plasma session, so that is not attempted.

Gated, and separately from anything that reads. Restyling someone's desktop
unasked, mid-document, is disruptive in a way that muting a microphone is not.
Reporting what is set needs no permission.

Honesty rules:

- **The setting is read back after writing it.** A `gsettings set` exiting 0 and
  a desktop that is actually dark are different claims, and a theme provider
  that is installed but not selected produces a write that silently does nothing
  visible.
- "Preferred" and "applied" are reported separately. Setting the preference
  `prefer-dark` does not make a GTK application that ignores the portal dark, and
  saying "dark mode is on" in that state would be a claim about the whole desktop
  that one preference cannot support.
- An unsupported desktop says so rather than reporting the current setting as if
  it were the whole picture.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "appearance-control-enabled"
_TIMEOUT = 25

_GNOME_SCHEME = "org.gnome.desktop.interface"
_GNOME_KEY = "color-scheme"
#: KDE's own light/dark preference names, mapped to the two states.
_DARK_LOOKS = ("Breeze-Dark", "dark", "adwaita-dark")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_theme",
        "description": (
            "Report or change whether the desktop uses a light or dark "
            "appearance, on GNOME or KDE. Reports the applied theme separately "
            "from the preference, because setting the preference does not make "
            "every application follow it. Requires the "
            "'appearance-control-enabled' consent key; reporting needs no such "
            "permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status', 'dark' or 'light'. Defaults to status.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing the desktop appearance is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting what is currently set "
            f"needs no such permission - only changing it does, because "
            f"restyling a desktop unasked is disruptive in a way that reading it "
            f"is not."
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


def _desktop() -> str:
    """'gnome', 'kde' or 'unknown' from the session itself, not from a guess."""
    for var in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_DESKTOP"):
        value = (os.environ.get(var) or "").lower()
        if "kde" in value or "plasma" in value:
            return "kde"
        if "gnome" in value or "unity" in value or "cinnamon" in value:
            return "gnome"
    return "unknown"


def _gnome_state() -> "tuple[str | None, str]":
    """(state, raw) where state is 'dark'/'light', or None if unreadable."""
    proc = _run_cmd(["gsettings", "get", _GNOME_SCHEME, _GNOME_KEY])
    if proc is None or proc.returncode != 0:
        return (None, "")
    raw = proc.stdout.strip().strip("'\"")
    if raw == "prefer-dark":
        return ("dark", raw)
    if raw in ("default", "prefer-light"):
        return ("light", raw)
    return (None, raw)


def _kde_state() -> "tuple[str | None, str]":
    """(state, look) from kdeglobals' colour scheme, or (None, '') if unreadable."""
    proc = _run_cmd(["kreadconfig6", "--file", "kdeglobals", "--group", "KDE",
                     "--key", "colorScheme"])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        scheme = proc.stdout.strip()
        return (("dark" if "dark" in scheme.lower() else "light"), scheme)
    proc = _run_cmd(["kreadconfig5", "--file", "kdeglobals", "--group", "KDE",
                     "--key", "colorScheme"])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        scheme = proc.stdout.strip()
        return (("dark" if "dark" in scheme.lower() else "light"), scheme)
    return (None, "")


def _kde_look() -> str:
    proc = _run_cmd(["plasma-lookandfeeltool", "--list"])
    if proc is None or proc.returncode != 0:
        return ""
    current = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("*"):
            current = line.lstrip("* ").strip()
            break
    return current


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "dark", "light"):
        return f"Action must be status, dark or light, not {action!r}."

    desktop = _desktop()
    if desktop == "unknown":
        return ("This desktop's session could not be identified from "
                "XDG_CURRENT_DESKTOP, DESKTOP_SESSION or XDG_SESSION_DESKTOP, so "
                "the appearance is UNKNOWN. GNOME and KDE keep this setting in "
                "completely different places and guessing which one to write "
                "would be the wrong kind of confident.")

    if desktop == "gnome":
        state, raw = _gnome_state()
    else:
        state, raw = _kde_state()
        look = _kde_look()

    if action == "status":
        lines = [f"Desktop session: {desktop}"]
        if state is None:
            lines.append(f"Appearance: UNKNOWN - the preference could not be read"
                         + (f" (raw value {raw!r})" if raw else ""))
        else:
            lines.append(f"Appearance preference: {state}"
                         + (f" (gsettings value {raw!r})" if desktop == "gnome" else
                            f" (colour scheme {raw!r})"))
        if desktop == "kde":
            lines.append(f"Applied look: {look or 'could not be read'}")
        lines.append(
            "This is the preference, not a guarantee: an application that "
            "ignores the desktop's colour-scheme setting will not follow it.")
        return "\n".join(lines)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the appearance: {reason}"

    want_dark = action == "dark"

    if desktop == "gnome":
        target = "prefer-dark" if want_dark else "default"
        proc = _run_cmd(["gsettings", "set", _GNOME_SCHEME, _GNOME_KEY, target])
        if proc is None or proc.returncode != 0:
            return (f"gsettings could not set {_GNOME_KEY}: "
                    f"{(proc.stderr or '').strip() if proc else 'gsettings is not installed'}"
                    f". Nothing was changed.")
        after, raw_after = _gnome_state()
        if after == ("dark" if want_dark else "light"):
            return (f"Desktop appearance preference is now {after} "
                    f"(verified: {raw_after!r}). Applications that ignore the "
                    f"colour-scheme setting will not follow.")
        return (f"gsettings accepted the change but it reads back as {raw_after!r}, "
                f"so this is not verified.")

    # KDE: prefer the supported tool. Editing kdeglobals and the .krc by hand is
    # how a Plasma session ends up with a half-applied theme.
    looks = _run_cmd(["plasma-lookandfeeltool", "--list"])
    available = []
    if looks is not None and looks.returncode == 0:
        available = [l.strip().lstrip("* ").strip()
                     for l in looks.stdout.splitlines() if l.strip()]
    wanted = [n for n in available
              if (any(d in n.lower() for d in _DARK_LOOKS) if want_dark
                  else ("dark" not in n.lower()))]
    if not wanted:
        return (f"No suitable {'dark' if want_dark else 'light'} Plasma look was "
                f"found among {len(available)} available: "
                f"{', '.join(available[:8]) or 'none listed'}. Nothing was changed.")
    proc = _run_cmd(["plasma-lookandfeeltool", "--apply", wanted[0]])
    if proc is None or proc.returncode != 0:
        return (f"plasma-lookandfeeltool could not apply {wanted[0]!r}: "
                f"{(proc.stderr or '').strip() if proc else 'not installed'}. "
                f"Nothing was changed.")
    applied = _kde_look()
    if applied == wanted[0]:
        return (f"Applied Plasma look {applied!r} (verified by reading it back).")
    return (f"plasma-lookandfeeltool reported success but the applied look reads "
            f"back as {applied!r}, so this is not verified.")


SKILLS = [Skill(name="set_theme", schema=SCHEMA, run=_run)]
