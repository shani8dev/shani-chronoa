"""Skill: a MoYoung ("Da Fit") smartwatch - health data, measurements, settings, messages.

The skill: the assistant's consent and its tool schema. The engine is
shani-link's (shani_link/watch.py) - device plumbing, used by people directly
through the Shani Link app with no consent step - and every name it has is
reachable here, so the assistant's code and tests see one module's worth.
"""
from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_engine = _link.module("watch")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})


_CONSENT_KEY = "bluetooth-gatt-enabled"


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Reaching your watch is turned off. Nothing was asked or sent. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "watch",
        "description": (
            "Your MoYoung/Da Fit smartwatch: 'summary' (steps, distance, calories for 3 days, last "
            "night's sleep stages, today's stress, workouts), 'measure' a heart rate, blood oxygen or "
            "blood pressure on the wrist (takes up to a minute), read 'settings', 'set' one (step_goal, "
            "time_format 12h/24h, units metric/imperial, raise_to_wake, sedentary_reminder, "
            "other_app_notifications, power_saving, heart_rate_auto_interval, do_not_disturb "
            "'22:00-07:00'), 'sync_time', 'find' (make the watch ring), 'notify' (show a message on the watch), or set the 'profile' "
            "(height, weight, age, gender - weight is never measured by the watch). "
            "Requires the 'bluetooth-gatt-enabled' consent key."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "device": {"type": "string", "description": "Part of the watch's name; omit if there is one."},
            "what": {"type": "string", "enum": ["heart_rate", "blood_oxygen", "blood_pressure"],
                     "description": "For measure."},
            "setting": {"type": "string", "description": "For set: which setting."},
            "value": {"description": "For set: the new value (number, true/false, '24h', 'metric', "
                                     "or '22:00-07:00' for do_not_disturb)."},
            "sender": {"type": "string", "description": "For notify: who it is from."},
            "text": {"type": "string", "description": "For notify: the message."},
            "kind": {"type": "string", "enum": ["other", "sms", "call", "whatsapp"],
                     "description": "For notify: which icon the watch shows."},
            "height_cm": {"type": "integer"}, "weight_kg": {"type": "integer"},
            "age": {"type": "integer"}, "gender": {"type": "string", "enum": ["male", "female"]},
        }, "required": ["action"]},
    },
}


def _run(arguments: dict) -> str:
    """The assistant's way in: its consent first, then shani-link's engine."""
    if isinstance(arguments, dict):
        allowed, refusal = _consent(ChronoaConfig())
        if not allowed:
            return refusal
    return _engine._run(arguments)


SKILLS = [Skill(name="watch", schema=SCHEMA, run=_run)]
