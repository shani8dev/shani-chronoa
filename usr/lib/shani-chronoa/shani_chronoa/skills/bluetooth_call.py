"""Skill: use a paired phone as a Bluetooth speakerphone - see who's calling,

The skill: the assistant's consent and its tool schema. The engine is
shani-link's (shani_link/bt_telephony.py) - device plumbing, used by people directly
through the Shani Link app with no consent step - and every name it has is
reachable here, so the assistant's code and tests see one module's worth.
"""
from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa import approvals

_engine = _link.module("bt_telephony")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})


COMPONENT = "skill:bluetooth-call"


_CONSENT_KEY = "bluetooth-call-enabled"


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) for Bluetooth call control.

    The house shape, for the reason `tests/test_question_presenter.py` documents:
    it sweeps every gated tool through one `_consent(config) -> (allowed, reason)`
    contract, which is how it proves each one refuses by naming its own switch
    when nobody can be asked.
    """
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Using your phone as a Bluetooth speakerphone is turned off. Nothing was "
                       f"done. Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "bluetooth_call",
        "description": (
            "Use a paired Android phone as a Bluetooth speakerphone, with no cable and no "
            "app on the phone. 'status' says whether a phone is connected as a hands-free unit, "
            "whether a call is in progress, and which audio profile its call audio arrived on. "
            "'dial' places a call - 'dial number=\"+441234567890\"' - asking you to confirm first, "
            "because it rings someone. 'answer' and 'hangup' work on a call already in progress. "
            "'audio' reports where the call's voice is going and names anything missing from that "
            "path, and 'profile' switches the card between wide-band hfp_hf and narrow-band "
            "hsp_hs. "
            "The phone stays the radio; this routes the voice to this computer's speakers and "
            "microphone. Needs the phone connected over Bluetooth, not merely paired, and the "
            "'bluetooth-call-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "dial", "answer", "hangup", "audio", "profile"],
                    "description": "status: who is connected and what is happening. dial: place a "
                                   "call. answer: pick up a ringing call. hangup: end the call. "
                                   "audio: report where the call audio is going and what is "
                                   "missing from that path. profile: switch the Bluetooth audio "
                                   "card to a profile such as hfp_hf or hsp_hs.",
                },
                "card": {"type": "string",
                         "description": "For profile: which audio card, by number, from the "
                                        "audio report. Only needed when more than one is present."},
                "profile": {"type": "string",
                            "description": "For profile: the profile to switch to, e.g. 'hfp_hf' "
                                           "(wide-band) or 'hsp_hs' (narrow-band)."},
                "to": {"type": "string", "enum": ["pc", "phone"],
                       "description": "For audio: move the call's audio to this computer's "
                                      "speaker and microphone ('pc') or back to the phone."},
                "number": {
                    "type": "string",
                    "description": "For dial: the number to call, e.g. '+441234567890'. Only "
                                   "digits, spaces and + * # , are accepted.",
                },
            },
            "required": ["action"],
        },
    },
}


def _run(arguments: dict) -> str:
    """The assistant's way in: its consent first, then shani-link's engine."""
    if isinstance(arguments, dict):
        allowed, refusal = _consent(ChronoaConfig())
        if not allowed:
            return refusal
    return _engine._run(arguments, confirm=approvals.confirm)


SKILLS = [Skill(name="bluetooth_call", schema=SCHEMA, run=_run)]
