"""Skill: press keys on the paired phone - camera shutter, media, volume, typing.

The skill: the assistant's consent and its tool schema. The client is
shani-link's (shani_link/phone_remote.py, beside its ble_peripheral.py) - the
Shani Link app's Phone page presses the same keys with no consent step - and
every name it has is reachable here. Gated on `phone-remote-enabled`, the same
switch that makes this computer advertise as "Chronoa Remote".
"""

from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_engine = _link.module("phone_remote")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})

_CONSENT_KEY = "phone-remote-enabled"
KEYS = _engine.KEYS


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"The phone remote is turned off. Nothing was pressed. Enable "
                       f"'{_CONSENT_KEY}' in Settings, then pair 'Chronoa Remote' from the phone.")
    return True, ""


def _run(arguments: dict) -> str:
    """The assistant's way in: its consent first, then shani-link's client."""
    if not isinstance(arguments, dict):
        return _engine._run(arguments)
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal
    return _engine._run(arguments)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "phone_remote",
        "description": (
            "Press keys on the paired phone as a Bluetooth keyboard: 'press' the camera 'shutter' (takes a "
            "photo in the camera app), play_pause, next, previous, volume_up/down, mute; or 'type' text into "
            "whatever field is focused on the phone. The phone must have paired 'Chronoa Remote'. Requires the "
            "'phone-remote-enabled' consent key."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["status", "press", "type"]},
            "key": {"type": "string", "enum": list(KEYS), "description": "For press."},
            "text": {"type": "string", "description": "For type: ASCII text to type on the phone."},
        }, "required": ["action"]},
    },
}

SKILLS = [Skill(name="phone_remote", schema=SCHEMA, run=_run)]
