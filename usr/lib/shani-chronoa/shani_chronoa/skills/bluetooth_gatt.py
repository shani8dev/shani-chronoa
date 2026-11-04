"""Skill: read a paired Bluetooth Low Energy device over GATT - a watch's

The skill: the assistant's consent and its tool schema. The engine is
shani-link's (shani_link/gatt.py) - device plumbing, used by people directly
through the Shani Link app with no consent step - and every name it has is
reachable here, so the assistant's code and tests see one module's worth.
"""
from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_engine = _link.module("gatt")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})


COMPONENT = "skill:bluetooth-gatt"


_CONSENT_KEY = "bluetooth-gatt-enabled"


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) for reading a paired device's own attributes.

    **The signature is the house convention**, not a preference: it takes the
    config and returns `(allowed, reason)`, like `toggle_wifi._consent` and the
    rest. `tests/test_question_presenter.py` sweeps every gated tool by calling
    exactly this shape with one config, which is how it proves that each tool
    refuses *by name* when nobody can be asked. A version taking no argument and
    returning a bare string passed the skill's own tests and failed that sweep
    with `TypeError: _consent() takes 0 positional arguments but 1 was given` -
    which is the failure mode the sweep exists to catch, caught by the sweep.

    A named helper rather than an inline `config.get_bool(...)` because
    `test_skill_gates_are_enforced.py` decides whether a module consults a gate
    by reading its source, and `get_bool` is deliberately excluded from its
    accepted names - it appears inside this function, so accepting it would let a
    module that defines a gate and never calls one pass on the strength of a
    function it merely contains.
    """
    if not config.get_bool(_CONSENT_KEY, False):
        # **The switch by the name the Settings row shows**, not by its key.
        from shani_chronoa.capabilities import gate_label
        return False, (f"Reading a Bluetooth device is turned off. Nothing was read. "
                       f"Turn on \u201c{gate_label(_CONSENT_KEY)}\u201d in Settings "
                       f"to allow it.")
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "bluetooth_gatt",
        "description": (
            "Read what a paired Bluetooth Low Energy device says about itself - a watch's or "
            "headset's battery, a heart-rate strap's live reading, which services a band "
            "offers. 'list': every paired device and whether it speaks this protocol. "
            "'services': what one exposes. 'read': one stored value. 'listen': a live "
            "reading of something the device only pushes, e.g. heart rate - 'nothing "
            "arrived' is the truthful answer when the device is not measuring. Speakers, "
            "headphones and car stereios are older Bluetooth and expose nothing here, "
            "which is a true answer, not a failure; wearables, bands, straps, smart tags "
            "and thermometers answer. Needs 'bluetooth-gatt-enabled' and the device must "
            "be connected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "services", "read", "listen"],
                    "description": "list: every paired device and whether it exposes GATT. "
                                   "services: what one device exposes. read: one stored value. "
                                   "listen: a live reading of something the device pushes.",
                },
                "device": {
                    "type": "string",
                    "description": "Part of the device's name, for services, read and listen. "
                                   "Ambiguous names are refused rather than guessed at.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For listen only: how long to collect for, 1-60, default 10. "
                                   "The device has to be worn and in use for it to push anything.",
                },
                "characteristic": {
                    "type": "string",
                    "description": "For read or listen: which value, e.g. 'Battery Level', 'Heart Rate "
                                   "Measurement', 'Firmware Revision'. Only characteristics that "
                                   "are actually on the device can be read. Heart Rate Measurement "
                                   "must be listened to, not read - the device has no stored value "
                                   "for it.",
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


def _run_find(arguments: dict) -> str:
    """The assistant's way in: its consent first, then shani-link's engine."""
    if isinstance(arguments, dict):
        allowed, refusal = _consent(ChronoaConfig())
        if not allowed:
            return refusal.replace("Reading", "Reaching").replace("Nothing was read", "Nothing was sent")
    return _engine._run_find(arguments)


SKILLS = [Skill(name="bluetooth_gatt", schema=SCHEMA, run=_run),
          Skill(name="find_device", schema=FIND_SCHEMA, run=_run_find)]
