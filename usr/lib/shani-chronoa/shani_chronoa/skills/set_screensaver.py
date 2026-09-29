"""Skill: report or change when the screen blanks and whether it locks.

`lock_screen` can lock the session on demand. Nothing could change the *policy* -
how long the machine sits idle before doing it - which is the setting people
actually want to change, and the one they change most often: longer during a
presentation, never while a video plays, five minutes at a desk, immediately on a
laptop that leaves the bag.

Two separate settings, and this skill treats them as two. GNOME's `idle-delay` is
how long until the screen blanks; `lock-enabled` is whether the screensaver then
demands a password. A machine can blank without locking - the screen goes dark and
anyone who walks up can move the mouse and be in - which is the state most people
do not realise they are in.

Gated by `idle-timeout-enabled`, its own key rather than sharing
`screen-lock-enabled`. Whether the *machine locks itself* is a security policy
decision, and lumping it in with "may Chronoa lock the screen right now" would
mean one switch silently controlling both.

KDE is not supported for the timeout and says so; its screensaver settings live
in kscreensaver's own config, with no documented command-line interface.

Honesty rules:

- **Read back after writing.** `idle-delay` is in seconds, and a value the
  desktop rejects leaves the previous timeout silently in place.
- The value read back is converted to minutes and shown, because a raw `300` does
  not answer "how long is that".
- A blank-without-lock machine is reported as exactly that, not as protected.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "idle-timeout-enabled"
_TIMEOUT = 20

_IDLE_SCHEME = "org.gnome.desktop.session"
_IDLE_KEY = "idle-delay"
_LOCK_SCHEME = "org.gnome.desktop.screensaver"
_LOCK_KEY = "lock-enabled"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_screensaver",
        "description": (
            "Report or change how long the machine waits before the screen "
            "blanks, and whether it then demands a password. The two are "
            "reported separately, because blanking without locking leaves anyone "
            "who walks up to the machine able to use it. Requires the "
            "'idle-timeout-enabled' consent key; reporting needs no such "
            "permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status' or 'set'. Defaults to status.",
                },
                "blank_after_minutes": {
                    "type": "integer",
                    "description": (
                        "Minutes of inactivity before the screen blanks. 0 means "
                        "never. Required for 'set'."
                    ),
                },
                "lock": {
                    "type": "boolean",
                    "description": (
                        "Whether the screen demands a password when it blanks. "
                        "Optional."
                    ),
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            "changing the idle timeout is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting the current timeout needs "
            f"no such permission. This has its own key rather than sharing the "
            f"lock skill's, because whether the machine locks itself is a "
            f"standing policy decision, not a one-off action."
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


#: `gsettings get` prefixes numeric types with their GVariant type name:
#: `org.gnome.desktop.session idle-delay` comes back as `uint32 300`, not `300`.
#: Booleans, doubles and strings come back bare (`true`, `1.0`, `'default'`), so
#: this only bites integer keys - and it bites them silently, because `int()` on
#: the prefixed form raises and the read-back comparison against the value just
#: written never matches, so a successful write reports itself as unverified.
_TYPED_PREFIX = re.compile(r"^(?:u?int(?:8|16|32|64)|byte|int)\s+")


def _read(scheme: str, key: str):
    """The key's value in GVariant form, or None if it could not be read."""
    proc = _gs("get", scheme, key)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _read_int(scheme: str, key: str) -> "int | None":
    """An integer key's value, or None - including when it is non-numeric."""
    raw = _read(scheme, key)
    if raw is None:
        return None
    try:
        return int(_TYPED_PREFIX.sub("", raw).strip())
    except ValueError:
        return None


def _minutes(seconds: "int | None") -> str:
    if seconds is None:
        return "UNKNOWN"
    if seconds == 0:
        return "never"
    if seconds < 60:
        return f"{seconds} second(s)"
    return f"{seconds // 60} minute(s)"


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "set"):
        return f"Action must be status or set, not {action!r}."

    desktop = _desktop()
    if desktop == "kde":
        return ("This is a KDE session. The screensaver's settings live in "
                "kscreensaver's own configuration with no documented command-line "
                "interface, so the timeout is not changed from here. "
                "'lock_screen' can still lock the session on demand.")
    if desktop == "unknown":
        return ("The desktop session could not be identified, so this does not "
                "know which mechanism to use.")

    if shutil.which("gsettings") is None:
        return "gsettings is not installed, so the idle timeout is UNKNOWN."

    if action == "status":
        seconds = _read_int(_IDLE_SCHEME, _IDLE_KEY)
        lock = _read(_LOCK_SCHEME, _LOCK_KEY)
        lines = [f"Screen blanks after: {_minutes(seconds)} of inactivity."]
        if lock is None:
            lines.append("Whether it then demands a password: UNKNOWN.")
        elif lock == "true":
            lines.append("It then demands a password.")
        else:
            lines.append("It does NOT demand a password - the screen goes dark and "
                         "anyone who walks up can move the mouse and be straight "
                         "in. If that is not what you want, this is the setting.")
        return "\n".join(lines)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the idle timeout: {reason}"

    changed = []

    raw_minutes = arguments.get("blank_after_minutes")
    if raw_minutes is not None and raw_minutes != "":
        try:
            minutes = int(raw_minutes)
        except (TypeError, ValueError):
            return f"{raw_minutes!r} is not a whole number of minutes."
        if minutes < 0:
            return f"Refusing {minutes} minutes: the timeout cannot be negative. " \
                   f"Use 0 for never."
        seconds = minutes * 60
        proc = _gs("set", _IDLE_SCHEME, _IDLE_KEY, str(seconds))
        if proc is None or proc.returncode != 0:
            detail = (proc.stderr or "").strip() if proc else "gsettings is not installed"
            return f"Could not set the blank timeout: {detail or 'no detail'}"
        after = _read_int(_IDLE_SCHEME, _IDLE_KEY)
        if after is None:
            return ("gsettings accepted the change but the value could not be read "
                    "back, so it is not verified.")
        if after != seconds:
            return (f"Asked for {minutes} minute(s) but the desktop reports "
                    f"{_minutes(after)}. Read the current timeout before assuming "
                    f"the change stuck.")
        changed.append(f"blank timeout is now {_minutes(seconds)} (verified)")

    if "lock" in arguments and arguments.get("lock") is not None:
        want = "true" if arguments.get("lock") else "false"
        proc = _gs("set", _LOCK_SCHEME, _LOCK_KEY, want)
        if proc is None or proc.returncode != 0:
            detail = (proc.stderr or "").strip() if proc else "gsettings is not installed"
            return f"Could not change whether it locks: {detail or 'no detail'}"
        after = _read(_LOCK_SCHEME, _LOCK_KEY)
        if after != want:
            return (f"gsettings accepted the lock change but it reads back as "
                    f"{after}, so this is not verified.")
        changed.append(f"it {'now demands' if want == 'true' else 'no longer demands'} "
                       f"a password (verified)")

    if not changed:
        return ("Nothing to change: no blank_after_minutes and no lock value were "
                "given.")
    return "; ".join(changed) + "."


SKILLS = [Skill(name="set_screensaver", schema=SCHEMA, run=_run)]
