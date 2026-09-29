"""Skill: start, stop or restart a system service.

The most privileged action in this project short of deleting a disk. It runs a
root-owned unit, it can take down something another person is using, and on a
remote machine stopping the wrong unit locks you out. Gated on its own key.

`restart` is the default rather than `stop` because "make it work again" is
almost always what was meant, and a stop that is immediately followed by a start
briefly takes a working service offline for no reason.

Consent is checked before the unit name is validated, so a refusal does not
confirm or deny that a unit exists.

Honesty rules: a successful `systemctl` exit is reported as *asked*, not as
*working* - systemd will happily start a unit that then immediately fails, and
the post-check reads the real state back so the reply says which happened.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "service-control-enabled"
_TIMEOUT = 30
_ACTIONS = ("start", "stop", "restart", "reload", "enable", "disable")
#: Actions that change what happens at boot rather than right now.
_BOOT = ("enable", "disable")
_SUFFIXES = (".service", ".socket", ".target", ".timer", ".mount", ".path")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing system services is turned off (enable '{_CONSENT_KEY}' "
            f"in Settings). Listing services and reading their logs need no such "
            f"permission - only changing them does, because these are "
            f"root-owned units and the wrong one can take down something "
            f"another person is relying on."
        )
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "control_service",
        "description": (
            "Start, stop, restart, reload, enable or disable a system service, "
            "then read its state back so the reply says whether it actually "
            "came up. Requires the 'service-control-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "unit": {"type": "string", "description": "Service name, e.g. 'sshd' or 'sshd.service'."},
                "action": {
                    "type": "string",
                    "description": f"What to do. One of: {', '.join(_ACTIONS)}. Defaults to restart.",
                },
            },
        },
    },
}


def _state_of(unit: str) -> str:
    if shutil.which("systemctl") is None:
        return "unknown (systemctl is not installed)"
    try:
        proc = subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"unknown ({exc})"
    return (proc.stdout or proc.stderr or "").strip() or "unknown"


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the service: {reason}"

    unit = (arguments.get("unit") or "").strip()
    if not unit:
        return "No service was named. Use list_services to see what is there."
    action = (arguments.get("action") or "restart").strip().lower()
    if action not in _ACTIONS:
        return f"Action must be one of {', '.join(_ACTIONS)}, not {action!r}."

    if "." not in unit and not unit.endswith(_SUFFIXES):
        unit += ".service"
    if len(unit) > 128 or "/" in unit or unit.startswith("-"):
        return f"{unit!r} is not a plausible unit name; refusing to pass it to systemctl."

    if shutil.which("systemctl") is None:
        return files.tool_missing("systemctl", "change a service")

    before = _state_of(unit)
    verb = "enable at boot" if action in _BOOT else action
    try:
        proc = subprocess.run(["systemctl", action, unit], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"Asked systemd to {verb} {unit} but it did not answer within {_TIMEOUT}s; the outcome is unknown."
    except OSError as exc:
        return f"Could not {verb} {unit}: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (
            f"systemctl refused to {verb} {unit} (exit {proc.returncode}). "
            f"It was {before} before the attempt and has not been verified since."
            + (f" systemd said: {detail[-1]}" if detail else "")
        )

    if action in _BOOT:
        # enable/disable does not change the current state, so reading is-active
        # back would report something unrelated to what was just done.
        return f"Asked systemd to {verb} {unit}; it reported success. The current run state is unchanged and still {before}."

    after = _state_of(unit)
    if action in ("start", "restart") and after not in ("active", "activating"):
        return (
            f"systemctl accepted '{action} {unit}' and reported success, but "
            f"the unit is {after}, not active. That is the common case of a "
            f"service that starts and immediately fails - use read_logs on "
            f"{unit} to see why."
        )
    if action == "stop" and after == "active":
        return f"systemctl accepted 'stop {unit}' but it is still {after}. It may be restarting itself."
    return f"{unit} is now {after} (was {before})."


SKILLS = [Skill(name="control_service", schema=SCHEMA, run=_run)]
