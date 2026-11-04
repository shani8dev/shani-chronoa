"""Skill: send and receive infrared codes - a remote, made of arithmetic.

The skill: the assistant's consent and its tool schema. The engine is
shani-link's (shani_link/ir_remote.py) - device plumbing, used by people directly
through the Shani Link app with no consent step - and every name it has is
reachable here, so the assistant's code and tests see one module's worth.
"""
from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_engine = _link.module("ir_remote")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})


_CONSENT_KEY = "ir-remote-enabled"


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature every gated tool returns."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Infrared is turned off. Nothing was sent or received. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "ir_remote",
        "description": (
            "Send and receive infrared remote-control codes, and build or read the "
            "pulse-space timings themselves. 'devices': is there IR hardware here "
            "(most machines have none, and that is a true answer). 'encode': turn an "
            "address and command into the NEC or RC-6 timings that would carry them. "
            "'decode': turn timings into a code. 'send': transmit a code through an IR "
            "device. 'receive': capture a button press and decode it. Needs the "
            "'ir-remote-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "devices: what IR hardware and tools are here. "
                                   "encode: build the timings for a code. decode: read "
                                   "timings and name the code. send: transmit. "
                                   "receive: capture a button press.",
                },
                "protocol": {
                    "type": "string",
                    "enum": list(PROTOCOLS),
                    "description": "Which protocol: " + " or ".join(PROTOCOLS) +
                                   ". Every cheap remote speaks NEC; RC-6 is the "
                                   "Philips one and uses a 36 kHz carrier.",
                },
                "address": {
                    "type": "integer",
                    "description": "The address (NEC) or control field (RC-6). NEC "
                                   "commands are scoped to the device they address, so "
                                   "the wrong address is silence.",
                },
                "command": {
                    "type": "integer",
                    "description": "NEC: the command, 0-255. RC-6: the information "
                                   "field, 0-255.",
                },
                "timings": {
                    "type": "string",
                    "description": "For decode: the timings as `pulse 9000 space 4500 "
                                   "...`, the way ir-ctl prints them.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For receive: how long to wait for a button press, "
                                   "default 10.",
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


SKILLS = [Skill(name="ir_remote", schema=SCHEMA, run=_run)]
