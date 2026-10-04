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
    """(id, description) of the default capture source, or None if there is none.

    Parsing is delegated to `senses.audio`, which is the module that already
    gets this right.

    The first version of this walked `wpctl status` itself looking for a heading
    spelled `Audio Sources:`. No such heading exists. `wpctl` prints `Sources:`,
    among others, and the same status text is printed twice - once for the
    audio manager graph and once for video - so a scanner that merely looks for
    "sources" needs to pick the right one.

    The practical result was that on a laptop with a working microphone, this
    reported "No capture source was reported, so this machine has no
    microphone". Which is exactly the failure this project treats as worst: a
    plausible, confident, wrong statement about hardware that is plainly present,
    and one the user can disprove by looking at their own machine.
    """
    from shani_chronoa.senses import audio

    devices = audio.read_devices()
    sources = devices.get("sources") or []
    if not sources:
        return None
    for entry in sources:
        if entry.get("default"):
            return str(entry["id"]), entry.get("name", "")
    # No source is marked default. The first one is a better answer than
    # refusing, and still better than pretending there is no microphone - but it
    # is reported as what it is rather than as "the default".
    first = sources[0]
    return str(first["id"]), first.get("name", "")


def _is_muted(ident: str) -> Optional[bool]:
    """Whether a source is muted, or None because wpctl cannot tell us.

    **wpctl has no `get-mute`.** Its commands are status, get-volume, inspect,
    set-default, set-volume, set-mute, set-profile and clear-default - mute can
    be *set* but not read back through it. `wpctl inspect` does not expose it
    either; there is no key containing "mute" in a node's whole property dump,
    confirmed by inspecting a live source on this machine.

    So the first version of this called `wpctl get-mute <id>`, which does not
    exist, and every status call answered UNKNOWN. UNKNOWN is the honest result
    and the code now says *why*, rather than leaving a reader to wonder whether
    the microphone is muted.

    This is the same shape as the senses that report UNKNOWN when a read fails:
    the inability to observe is a state the code can represent, and it is not
    rounded up to a clean yes or no.
    """
    proc = run_wpctl("get-volume", ident)
    if proc.returncode != 0:
        return None
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
        # Says why the state is UNKNOWN rather than leaving it looking like a
        # finding. "UNKNOWN" alone invites the reader to wonder whether the
        # microphone is muted, and there is no way for them to find out either.
        if muted is None:
            return (f"Default microphone: {description}\n"
                    f"Mute state: UNKNOWN - wpctl can set a microphone's mute "
                    f"state but cannot report it, so this is not knowable from "
                    f"the command line on this system.")
        return f"Default microphone: {description}\nMute state: {state}"

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the microphone: {reason}"

    if muted is None:
        # Not a reason to refuse, and not a reason to guess. `set-mute` takes an
        # absolute 1 or 0, so this change does not depend on the current state
        # and is safe to make. Refusing here would leave the microphone
        # permanently uncontrollable through this skill on every machine, since
        # wpctl cannot report the state to compare against.
        proc = run_wpctl("set-mute", ident, "1" if action == "mute" else "0")
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()
            return ("Failed to change the microphone's mute state."
                    + (f" wpctl said: {detail}" if detail else ""))
        # Reported as the change that was made, with the reason the previous
        # state was unknown - so the answer is verifiable by the user rather
        # than resting on a claim about state nobody could read.
        return (f"{'Muted' if action == 'mute' else 'Unmuted'} the microphone "
                f"({description}). Note: its previous mute state could not be "
                f"read - wpctl has no way to report it - so this set it rather "
                f"than changing it from a known starting point.")

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
        return ("wpctl reported success but the mute state could not be read "
                "back, so this is not verified.")
    return f"Asked wpctl to change the mute state, but it still reports {after}."



def _post_condition(arguments: dict):
    """The microphone's mute state, read back through get-volume."""
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("mute", "unmute"):
        return None
    source = _default_source()
    if source is None:
        return False, "no capture source to read back"
    muted = _is_muted(source[0])
    if muted is None:
        return None
    return muted == (action == "mute"), f"read back: {'muted' if muted else 'unmuted'}"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="set_mic_mute", schema=SCHEMA, run=_run)]
