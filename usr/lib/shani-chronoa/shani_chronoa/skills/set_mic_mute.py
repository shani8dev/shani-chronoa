"""Skill: mute or unmute the microphone, and report its level.

The audio side of this project is output-only. `get_volume`, `set_volume` and
`set_mute` all act on the *sink* - what the speakers are playing - so a user
asking "how loud am I" gets an answer and a user asking "is my mic muted" gets
nothing, despite muting a microphone being one of the most privacy-relevant
controls a machine has and one people reach for constantly.

Pipes and per-application streams are not touched. Muting the default source is
what a person means by "mute the mic"; muting individual streams is a different
and much larger action, and silently widening this to cover them would be the
kind of helpful overreach that makes an assistant unsafe to leave running.

Honesty rules:

- **Mute state is read back from the source's own reported volume**, because a
  `wpctl` call exiting 0 and a microphone that is genuinely muted are different
  claims. `wpctl set-mute` does not change the volume, so the volume alone is not
  proof either way, and the mute property is what is read.
- A machine with no capture source is reported as having no microphone, not as a
  muted one.
"""

from __future__ import annotations

import shutil
from typing import Optional, Tuple

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.pipewire import run_wpctl
from shani_chronoa.skills import Skill

_CONSENT_KEY = "mic-control-enabled"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_mic_mute",
        "description": (
            "Mute or unmute the default microphone input, or report whether it "
            "is currently muted. Output volume is a separate skill. Requires "
            "the 'mic-control-enabled' consent key; reporting needs no such "
            "permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'mute', 'unmute' or 'status'. Defaults to status.",
                }
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing the microphone is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting whether it is muted "
            f"needs no such permission - only changing it does."
        )
    return True, ""


def _default_source() -> Optional[Tuple[str, str]]:
    """(id, description) of the default capture source, or None if there is none."""
    proc = run_wpctl("status")
    if proc.returncode != 0:
        return None
    capture = False
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Audio Sources:"):
            capture = True
            continue
        if stripped.endswith(":") and not stripped.startswith("Audio") and capture:
            break
        if capture and stripped:
            ident = stripped.split(" ", 1)[0]
            if ident.isdigit() or ident == "__default__":
                return (ident, stripped)
    return None


def _is_muted(ident: str) -> Optional[bool]:
    proc = run_wpctl("get-mute", ident)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip().lower()
    if value == "muted":
        return True
    if value == "unmuted":
        return False
    return None


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("mute", "unmute", "status"):
        return f"Action must be mute, unmute or status, not {action!r}."

    if shutil.which("wpctl") is None:
        return files.tool_missing("wpctl", "report or change the microphone state")

    source = _default_source()
    if source is None:
        return (
            "No capture source was reported, so this machine has no microphone "
            "PipeWire knows about. That is different from a microphone that is "
            "muted - nothing exists to mute."
        )
    ident, description = source
    muted = _is_muted(ident)
    state = "UNKNOWN" if muted is None else ("muted" if muted else "unmuted")

    if action == "status":
        return (f"Default microphone: {description}\n"
                f"Mute state: {state}")

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the microphone: {reason}"

    if muted is None:
        return ("The microphone's current mute state could not be read, so it "
                "is not known whether this would change anything. Nothing was "
                "changed on an unknown starting state.")

    want = action == "mute"
    if muted == want:
        return f"The microphone is already {state}, so nothing was changed."

    proc = run_wpctl("set-mute", ident, "1" if want else "0")
    if proc.returncode != 0:
        return (f"wpctl refused to {'mute' if want else 'unmute'} the "
                f"microphone (exit {proc.returncode}): "
                f"{(proc.stderr or '').strip() or 'no detail given'}")

    after = _is_muted(ident)
    if after == want:
        return f"Microphone is now {state} (verified by reading it back)."
    if after is None:
        return (f"wpctl reported success but the mute state could not be read "
                f"back, so this is not verified.")
    return f"Asked wpctl to change the mute state, but it still reports {after}."


SKILLS = [Skill(name="set_mic_mute", schema=SCHEMA, run=_run)]
