"""Skill: a USB/Wi-Fi-debugging Android phone over adb - info, battery, screenshot, apps, files, open a link.

The skill: the assistant's consent and its tool schema. The engine is
shani-link's (shani_link/android.py) - device plumbing, used by people directly
through the Shani Link app's Android tab with no consent step - and every name
it has is reachable here.

The phone itself is the first consent: adb only reaches a device whose owner
turned on USB debugging and accepted this computer's key. Chronoa's own gate is
`phone-control-enabled`, the same one the KDE Connect / GSConnect `phone` skill
uses.
"""

from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_engine = _link.module("android")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})

_CONSENT_KEY = "phone-control-enabled"
_ACTIONS = _engine.ACTIONS

SCHEMA = {
    "type": "function",
    "function": {
        "name": "android_device",
        "description": (
            "An Android phone connected with USB debugging (adb). devices: which are connected; info: model, "
            "Android version, battery, storage; screenshot: save the phone's screen as a PNG; apps: installed "
            "apps (query filters); push: copy a file to the phone (to, default /sdcard/Download); pull: copy a "
            "file from the phone; open_url: open a web link on the phone. Requires 'phone-control-enabled'."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "serial": {"type": "string", "description": "Which device, when more than one is connected."},
            "path": {"type": "string"}, "to": {"type": "string"}, "url": {"type": "string"},
            "query": {"type": "string"}, "output": {"type": "string"},
        }, "required": ["action"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        # **The switch by the name the Settings row shows**, not by its key.
        from shani_chronoa.capabilities import gate_label
        return False, (f"using your phone is turned off - turn on "
                       f"\u201c{gate_label(_CONSENT_KEY)}\u201d in Settings")
    return True, ""


def _run(arguments: dict) -> str:
    """The assistant's way in: its consent first, then shani-link's engine."""
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing: {reason}."
    return _engine._run(arguments if isinstance(arguments, dict) else {})


SKILLS = [Skill(name="android_device", schema=SCHEMA, run=_run)]
