"""Skill: report or change the system timezone.

`timebase` (a sense) reports whether the clock is *synchronised* and which zone
it is in. Nothing could change it, which is the gap: timezone is one of the few
system settings people genuinely need to change by hand - after a flight, after
a DST boundary, on a machine that was assembled in the wrong zone, or when a
dual-booted install disagrees with the desktop about where it is.

Needs root, because `/etc/localtime` and the zone database are system-owned. That
is stated plainly rather than discovered as a permission error, and the refusal
to guess the zone name is deliberate: `timedatectl` accepts an arbitrary string
and a wrong one produces a machine whose clock is confidently in a city the user
does not live in.

Gated. It is a system-wide, immediately-visible change that affects every
timestamp on the machine, including anything that signs or timestamps files.

Honesty rules:

- **The zone is read back after setting it.** `timedatectl set-timezone`
  succeeding and the machine actually being in that zone are different claims.
- A zone name is validated against the system's own zone list before being
  passed on, so a typo is refused rather than applied.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "timezone-control-enabled"
_TIMEOUT = 25
_MAX_LISTED = 40

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_timezone",
        "description": (
            "Report the current system timezone, list the ones available, or "
            "change it. Changing it needs root and requires the "
            "'timezone-control-enabled' consent key; reporting needs no such "
            "permission. Validates the zone against the system's own list first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status', 'list' or 'set'. Defaults to status.",
                },
                "timezone": {
                    "type": "string",
                    "description": (
                        "The IANA zone to switch to, e.g. Europe/Berlin. "
                        "Required for 'set'."
                    ),
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing the system timezone is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). It is system-wide and moves every "
            f"timestamp on the machine at once, so it is not something to "
            f"discover after the fact."
        )
    return True, ""


def _timedatectl(*args: str):
    if shutil.which("timedatectl") is None:
        return None
    try:
        return subprocess.run(["timedatectl", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def current_timezone() -> str:
    proc = _timedatectl("show", "-p", "Timezone")
    if proc is None or proc.returncode != 0:
        return "UNKNOWN"
    for line in proc.stdout.splitlines():
        if line.startswith("Timezone="):
            return line.split("=", 1)[1].strip()
    return "UNKNOWN"


def known_timezones() -> "list | None":
    proc = _timedatectl("list-timezones")
    if proc is None or proc.returncode != 0:
        return None
    return [l.strip() for l in proc.stdout.splitlines() if l.strip()]


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "list", "set"):
        return f"Action must be status, list or set, not {action!r}."

    if shutil.which("timedatectl") is None:
        return ("timedatectl is not available, so the system timezone is "
                "UNKNOWN. That is not the same as the machine being in the "
                "right zone.")

    if action == "status":
        zone = current_timezone()
        out = f"System timezone: {zone}"
        if zone == "UNKNOWN":
            out += ("\nThe zone could not be read, so this machine's zone is "
                    "not established rather than confirmed.")
        return out

    if action == "list":
        zones = known_timezones()
        if not zones:
            return ("The system's zone list could not be read, so the available "
                    "timezones are UNKNOWN rather than an empty list.")
        shown = zones[:_MAX_LISTED]
        out = [f"{len(zones)} timezone(s) the system knows about:"]
        out += [f"  {z}" for z in shown]
        if len(zones) > len(shown):
            out.append(f"  and {len(zones) - len(shown)} more")
        out.append(f"In use: {current_timezone()}")
        return "\n".join(out)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the timezone: {reason}"

    zone = (arguments.get("timezone") or "").strip()
    if not zone:
        return "No timezone was named, so there is nothing to switch to."

    zones = known_timezones()
    if zones is not None and zone not in zones:
        near = [z for z in zones if zone.split("/")[-1].lower() in z.lower()][:5]
        hint = (f" Similar names the system does know: {', '.join(near)}."
                if near else "")
        return (f"Refusing to set timezone {zone!r}: it is not among the "
                f"{len(zones)} zones this system knows about.{hint} Use "
                f"action='list' to see them. timedatectl would have accepted an "
                f"arbitrary string and left the machine confidently in the "
                f"wrong city.")

    before = current_timezone()
    if zone == before:
        return f"The timezone is already {zone}, so nothing was changed."

    proc = _timedatectl("set-timezone", zone)
    if proc is None:
        return f"timedatectl did not answer within {_TIMEOUT}s, so the timezone is UNKNOWN."
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        lowered = detail.lower()
        if "permission" in lowered or "access" in lowered or "denied" in lowered:
            return (f"Could not set the timezone: {detail}. Changing it is a "
                    f"system-wide operation on /etc/localtime and needs root; "
                    f"it is still {before}.")
        return (f"Could not set the timezone: {detail or 'no detail given'}. "
                f"It is still {before}.")

    after = current_timezone()
    if after == zone:
        return (f"Timezone is now {after} (verified by reading it back). Every "
                f"timestamp on this machine moves accordingly.")
    return (f"timedatectl reported success but the zone reads back as {after}, "
            f"so this is not verified.")



def _post_condition(arguments: dict):
    """The zone timedatectl reports now, compared with the one asked for."""
    if (arguments.get("action") or "status").strip().lower() != "set":
        return None
    zone = (arguments.get("timezone") or "").strip()
    if not zone:
        return None
    proc = _timedatectl("show", "--property=Timezone", "--value")
    now = (proc.stdout or "").strip() if proc is not None and proc.returncode == 0 else ""
    return now == zone, f"read back: {now or 'nothing'}"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="set_timezone", schema=SCHEMA, run=_run)]
