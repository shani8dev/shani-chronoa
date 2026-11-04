"""Skill: read and write NFC tags - tap a sticker, find out what it says.

The skill: the assistant's consent and its tool schema. The engine is
shani-link's (shani_link/nfc.py) - device plumbing, used by people directly
through the Shani Link app with no consent step - and every name it has is
reachable here, so the assistant's code and tests see one module's worth.
"""
from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_engine = _link.module("nfc")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})


_CONSENT_KEY = "nfc-enabled"


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature.

    It takes the config and returns `(allowed, reason)`, which is what
    `tests/test_question_presenter.py` sweeps every gated tool through.
    """
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Using NFC is turned off. Nothing was read or written. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "nfc",
        "description": (
            "Read and write NFC tags. 'scan': is there a reader here (most laptops "
            "have none, and that is a true answer). 'read': tap a tag and be told what "
            "is written on it. 'write': put a link on an Ultralight/NTAG sticker - a "
            "bank card, transit pass or hotel key is refused, not rewritten. "
            "'emulate': present a tag to a phone. Needs the 'nfc-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "scan: is there an NFC reader here. read: tap a tag "
                                   "and be told what it says. write: put a link on a "
                                   "sticker. emulate: present a tag to a phone.",
                },
                "url": {
                    "type": "string",
                    "description": "For read: nothing. For write and emulate: the link to "
                                   "put on the tag, e.g. 'https://example.org'.",
                },
                "text": {
                    "type": "string",
                    "description": "Alternative to url, for a note rather than a link.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For read only: how long to wait for a tap, 1-30, "
                                   "default 6. Hold the tag flat against the reader.",
                },
                "target": {
                    "type": "string",
                    "description": "For write: which memory sector/page of the sticker, "
                                   "default 1.",
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
    return _engine._run(arguments)


SKILLS = [Skill(name="nfc", schema=SCHEMA, run=_run)]
