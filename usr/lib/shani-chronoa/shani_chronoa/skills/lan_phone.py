"""Skill: a phone on the **same Wi-Fi**, without KDE Connect or GSConnect installed.

The skill: the assistant's consent (`lan-phone-enabled`) and its tool schema.
The protocol (`lan_link`) and the actions - find phones, make this computer
findable, pair, send a file, the one remembered address - are shani-link's
(shani_link/lan_phone.py), which the Shani Link app's Phone link tab also uses
with no consent step; every name it has is reachable here.
"""

from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.skills import Skill

_engine = _link.module("lan_phone")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})

_CONSENT_KEY = "lan-phone-enabled"
_ACTIONS = _engine.ACTIONS


def _consent(config) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature every gated tool returns."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Finding phones on this network is turned off. Nothing "
                       f"was sent. Enable '{_CONSENT_KEY}' in Settings to allow "
                       f"it.")
    return True, ""


def _run(arguments: dict):
    """The assistant's way in: its consent first, then shani-link's actions."""
    config = arguments.get("_config")
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    allowed, refusal = _consent(config)
    if not allowed:
        return refusal
    return _engine._run(arguments)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "lan_phone",
        "description": (
            "Find phones on the SAME Wi-Fi network, using the KDE Connect "
            "protocol directly - so it works without KDE Connect or GSConnect "
            "installed. 'devices': what announced itself on this network in the "
            "last few seconds (phones, tablets, other computers). 'announce': "
            "make this computer findable by a phone's KDE Connect app. 'status': "
            "what this computer can and cannot do on the LAN. 'pair': pair with "
            "the device 'devices' found (the phone asks its owner). Needs the "
            "'lan-phone-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "devices: list what answers. announce: make "
                                   "this computer findable. status: what works "
                                   "here. pair: pair with the found device. "
                                   "Defaults to status.",
                    "default": "status",
                },
                "seconds": {
                    "type": "number",
                    "description": "For devices: how long to listen, 1-30 "
                                   "(default 5). Devices announce on a timer, "
                                   "so a short listen can miss one.",
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="lan_phone", schema=SCHEMA, run=_run)]
