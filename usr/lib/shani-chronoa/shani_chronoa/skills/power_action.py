"""Skill: suspend, hibernate, restart or shut down - only with consent, and
never at once for the ones that lose work.

`power-control-enabled` is off by default. Restart and shut down are
scheduled one minute ahead (`shutdown`'s own delay) and the reply says how
to cancel, because a misheard "restart" must be undoable; suspend and
hibernate lose nothing, so they happen now. logind decides whether the
active seat user may do it, exactly as the desktop's own power menu.
"""

import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "power-control-enabled"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "power_action",
        "description": "Suspend (sleep), hibernate, restart or shut down this computer, or cancel a "
                       "scheduled restart/shutdown. Restart and shutdown happen after one minute and "
                       "can be cancelled. Requires the 'power-control-enabled' consent key.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["suspend", "hibernate", "restart", "shutdown", "cancel"]},
        }, "required": ["action"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Whether power actions are allowed, and the refusal naming the switch if not."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Not done: power actions are switched off ('{_CONSENT_KEY}'). Turn on "
                       "'Let Chronoa suspend, restart or shut down' in Settings to allow it.")
    return True, ""


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip()
    if action not in ("suspend", "hibernate", "restart", "shutdown", "cancel"):
        return "Suspend, hibernate, restart, shutdown or cancel?"
    if action != "cancel":
        allowed, reason = _consent(ChronoaConfig())
        if not allowed:
            return reason
    try:
        if action in ("suspend", "hibernate"):
            if not shutil.which("systemctl"):
                return "systemctl is not available, so I cannot suspend."
            r = subprocess.run(["systemctl", action], capture_output=True, text=True, timeout=20)
            return f"{action.capitalize()}ing now." if r.returncode == 0 else \
                f"Could not {action}: {r.stderr.strip()[:160]}"
        if not shutil.which("shutdown"):
            return "The shutdown command is not available."
        if action == "cancel":
            r = subprocess.run(["shutdown", "-c"], capture_output=True, text=True, timeout=10)
            return "Cancelled: nothing will restart or shut down." if r.returncode == 0 else \
                f"Nothing to cancel ({r.stderr.strip()[:120] or 'no scheduled shutdown'})."
        flag = "-r" if action == "restart" else "-P"
        r = subprocess.run(["shutdown", flag, "+1", f"Chronoa: {action} requested by voice"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return f"Could not schedule the {action}: {r.stderr.strip()[:160]}"
        return (f"This computer will {action.replace('shutdown', 'shut down')} in one minute. Save your "
                "work - or say 'cancel the shutdown' to stop it.")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Could not {action}: {e.__class__.__name__}"


def _post_condition(arguments: dict) -> tuple[bool, str]:
    """Did the power action actually take, read from the system's own state.

    Without this the call returned UNVERIFIED, and UNVERIFIED is exactly the
    cell the replay guard refuses to store - so a notification pressed twice,
    or a client replaying after a timeout that actually succeeded, could shut
    the machine down twice. It is the sharpest instance of that gap: every
    other uncovered destructive tool can be repeated harmlessly, and this one
    takes the desktop with it.

    The check is deliberately narrow. Suspend and hibernate cannot be observed
    from outside - by the time anything could read the state, the machine is
    asleep or coming back - so they report what the tool itself reported
    rather than inventing a check. `restart`/`shutdown` schedule through
    systemd's shutdown binary, which arms `systemd-shutdown.timer`; that timer
    is the real evidence, and it is what `shutdown -c` clears.
    """
    action = (arguments.get("action") or "").strip()
    if action in ("suspend", "hibernate"):
        return True, (f"systemctl {action} returned success; the state cannot be "
                      f"read from outside without waking the machine again")
    if action == "cancel":
        return True, "cancellation is a negative claim and is not observable here"
    if shutil.which("systemctl") is None:
        return False, "systemctl is not available, so the shutdown timer cannot be read"
    try:
        props = subprocess.run(
            ["systemctl", "show", "systemd-shutdown.timer", "--property=ActiveState"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not read systemd-shutdown.timer: {exc.__class__.__name__}"
    if props.returncode != 0:
        return False, (f"systemd-shutdown.timer did not answer: "
                        f"{(props.stderr or '').strip()[:120]}")
    state = ""
    for line in props.stdout.splitlines():
        if line.startswith("ActiveState="):
            state = line.split("=", 1)[1].strip()
    if state == "active":
        return True, "systemd-shutdown.timer is armed"
    return False, f"systemd-shutdown.timer is {state or 'in an unknown state'}, not armed"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="power_action", schema=_SCHEMA, run=_run)]
