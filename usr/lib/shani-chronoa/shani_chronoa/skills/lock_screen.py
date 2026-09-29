"""Skill: lock this session.

Every other actuator here changes something inside the machine. This one ends
the current user's access to it, which means a person mid-sentence loses the
floor and a person who asked for it has to type their password. That is why it is
gated separately rather than folded into the input-control permission: it is not
an action *at* the interface, it is an action on the session.

Tries the session-native mechanisms in order of how much they can go wrong.
`loginctl lock-session` works over any XDG session and is what the desktop's own
"Lock" button ultimately calls. The GNOME and KDE D-Bus interfaces are tried
afterwards for the cases where loginctl cannot reach the session. Nothing is
raised to root, and no fallback pokes at `/proc` or sends keys to the greeter.

Honesty rules:

- **Locking is reported as unverified unless something confirms it.** A session
  that reports as active immediately after a lock request is a real possibility
  on a compositor that took the request and did not act on it, and reporting
  "locked" from an exit code alone would be the kind of answer that lets someone
  walk away from an unlocked machine.
- A failure says which mechanism was tried and why it did not work, because
  "could not lock" with no detail is not actionable.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "screen-lock-enabled"
_TIMEOUT = 20

SCHEMA = {
    "type": "function",
    "function": {
        "name": "lock_screen",
        "description": (
            "Lock this session so a password is needed to come back to it. "
            "Uses the session's own mechanism so the same thing happens as the "
            "desktop's Lock button. Requires the 'screen-lock-enabled' consent "
            "key."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"locking the screen is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). This one needs its own permission "
            f"rather than sharing the input-control key, because it does not act "
            f"on the interface - it ends the current session's access to the "
            f"machine."
        )
    return True, ""


def _run_cmd(argv: list) -> "subprocess.CompletedProcess | None":
    if shutil.which(argv[0]) is None:
        return None
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _session_is_locked() -> "bool | None":
    """True/False from the session's own state, or None if it cannot be read."""
    proc = _run_cmd(["loginctl", "show-session", "self", "-p", "LockedHint"])
    if proc is None or proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("LockedHint="):
            return line.split("=", 1)[1].strip().lower() == "yes"
    return None


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to lock the screen: {reason}"

    if shutil.which("loginctl") is None and shutil.which("gdbus") is None:
        return ("Neither loginctl nor gdbus is available, so there is no "
                "session mechanism to lock through. No fallback is attempted: "
                "raising privileges to poke the greeter is not the same action "
                "as locking a session, and would be harder to undo.")

    attempts = []

    proc = _run_cmd(["loginctl", "lock-session"])
    if proc is None:
        attempts.append("loginctl is not installed")
    elif proc.returncode != 0:
        attempts.append(f"loginctl exited {proc.returncode}: "
                        f"{(proc.stderr or '').strip() or 'no detail'}")
    else:
        state = _session_is_locked()
        if state is True:
            return "The session is locked."
        if state is False:
            return ("loginctl accepted the lock request, but the session still "
                    "reports itself unlocked, so this is NOT verified. Treat the "
                    "machine as unlocked.")
        return ("loginctl accepted the lock request, but the session's own state "
                "could not be read back, so this is unverified rather than "
                "confirmed.")

    for dbus, path, method in (
            ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver",
             "org.gnome.ScreenSaver.Lock"),
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
             "org.freedesktop.ScreenSaver.Lock")):
        proc = _run_cmd(["gdbus", "call", "--session", "--dest", dbus,
                         "--object-path", path, "--method", method])
        if proc is None:
            continue
        if proc.returncode == 0:
            state = _session_is_locked()
            if state is True:
                return f"The session is locked (via {dbus})."
            return (f"{dbus} accepted the lock request, but the session's own "
                    f"state did not confirm it"
                    + (" and still reports unlocked" if state is False else "")
                    + ". Treat the machine as unlocked.")
        attempts.append(f"{dbus} exited {proc.returncode}")

    return ("Could not lock the session. Tried: " + "; ".join(attempts) + ".")


SKILLS = [Skill(name="lock_screen", schema=SCHEMA, run=_run)]
