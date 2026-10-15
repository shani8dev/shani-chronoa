"""Place and answer calls on the JioFiber landline, over Wi-Fi through the router.

The landline is the broadband line's own number, the one JioJoin uses. This
computer is added to it once from the Phone page (a one-time code the owner
gets by text); after that, with `landline-enabled` on, Chronoa registers with
the router and can:

- `status`  - whether the line is connected and what call, if any, is on;
- `dial`    - place a call (`dial number="+911234567890"`), after confirming,
              because it rings someone;
- `answer`  - pick up a ringing call;
- `hangup`  - end, cancel or decline whatever call there is.

Nothing is recorded, and Chronoa never registers directly to Jio's network -
only through the router, so the landline handset keeps working. The voice is on
this computer's speaker and microphone.
"""
from __future__ import annotations

from shani_chronoa import approvals
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

CONSENT_KEY = "landline-enabled"
_ALLOWED = set("0123456789+*#, ")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, refusal). The one place the landline gate is read."""
    if config.get_bool(CONSENT_KEY, False):
        return True, ""
    return False, ("Using the JioFiber landline is off. Turn on 'landline-enabled' and set the "
                   "line up on the Phone page first. Nothing was done.")


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "Pass an action (status, dial, answer or hangup). Nothing was done."
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal

    from shani_chronoa import landline

    action = str(arguments.get("action") or "status").strip().lower()
    if not landline.is_set_up():
        return ("The landline is not set up on this computer yet. Open the Phone page and add "
                "this computer to the line - you will need the code Jio texts you. Nothing was done.")

    if action == "status":
        return landline.status()

    if action == "dial":
        number = str(arguments.get("number") or "").strip()
        if not number:
            return "Dial what? Pass number, e.g. number='+911234567890'. Nothing was dialled."
        if not number or any(ch not in _ALLOWED for ch in number):
            return ("That is not a dialable number - only digits, spaces and + * # , are allowed. "
                    "Nothing was dialled.")
        if not landline.ready():
            return ("The landline is not connected to the router right now, so there is nothing to "
                    "call through. Nothing was dialled.")
        from shani_chronoa.landline.ua import dial_user
        who = dial_user(number)
        ok, why = approvals.confirm(
            "Call on your landline?",
            f"Calling {who} on your JioFiber landline. You will both be heard on this "
            "computer's speaker and microphone.")
        if not ok:
            return f"Not dialled: {why or 'nobody confirmed'}. Nothing was done."
        try:
            landline.dial(number)
        except (RuntimeError, ValueError) as exc:
            return f"Could not dial {who}: {exc}. Nothing was dialled."
        return f"Calling {who} on the landline. Hang up here when you are done."

    if action == "answer":
        try:
            landline.answer()
        except RuntimeError as exc:
            return f"Could not answer: {exc}."
        return "Answered on the landline."

    if action == "hangup":
        try:
            landline.hangup()
        except RuntimeError as exc:
            return f"Could not hang up: {exc}."
        return "Hung up the landline call."

    return f"action must be status, dial, answer or hangup, not {action!r}."


SCHEMA = {
    "type": "function",
    "function": {
        "name": "landline_call",
        "description": (
            "Place and answer calls on the JioFiber broadband landline over Wi-Fi, through the "
            "router - the same line the JioJoin app uses, the house's own landline number. "
            "'status' says whether the line is connected and what call is on. 'dial' places a "
            "call - 'dial number=\"+911234567890\"' - asking you to confirm first, because it "
            "rings someone. 'answer' picks up a ringing call; 'hangup' ends, cancels or declines "
            "the current call. The voice is on this computer's speaker and microphone, nothing is "
            "recorded, and the landline handset keeps working. Needs this computer added to the "
            "line on the Phone page and the 'landline-enabled' consent key. This is the landline, "
            "not a mobile over Bluetooth - that is 'bluetooth_call'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "dial", "answer", "hangup"],
                    "description": "status: whether the line is connected and what call is on. "
                                   "dial: place a call. answer: pick up a ringing call. hangup: "
                                   "end, cancel or decline the current call.",
                },
                "number": {
                    "type": "string",
                    "description": "For dial: the number to call, e.g. '+911234567890' or a local "
                                   "number. Only digits, spaces and + * # , are accepted.",
                },
            },
            "required": ["action"],
        },
    },
}

SKILLS = [Skill(name="landline_call", schema=SCHEMA, run=_run)]
