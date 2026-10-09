"""Skill: press keys on the paired phone - camera shutter, media, volume, typing.

This computer is "Chronoa Remote", a Bluetooth keyboard the phone paired with
(`ble_peripheral.py`, HID over GATT - no app on the phone). The peripheral runs
in the app; this skill asks it through its socket. Gated on
`phone-remote-enabled`, the same switch that makes this computer advertise.
"""

from __future__ import annotations

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "phone-remote-enabled"
KEYS = ("shutter", "play_pause", "next", "previous", "stop", "volume_up", "volume_down", "mute")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"The phone remote is turned off. Nothing was pressed. Enable "
                       f"'{_CONSENT_KEY}' in Settings, then pair 'Chronoa Remote' from the phone.")
    return True, ""


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "phone_remote needs an action, e.g. {\"action\": \"press\", \"key\": \"shutter\"}."
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal
    from shani_chronoa import ble_peripheral as bp
    action = str(arguments.get("action") or "status").strip().lower()
    try:
        status = bp.ask("status")
    except OSError:
        return "The phone remote is not running here - it starts with the Chronoa app."
    if status.get("error"):
        return f"The phone remote could not start: {status['error']}."
    if action == "status":
        return ("The phone is connected to Chronoa Remote and listening for keys." if status.get("subscribed")
                else "Chronoa Remote is advertising, but no phone is connected to it. Pair 'Chronoa Remote' "
                     "from the phone's Bluetooth settings.")
    if not status.get("subscribed"):
        return "No phone is connected to Chronoa Remote, so nothing was pressed."
    if action == "press":
        key = str(arguments.get("key") or "").strip().lower().replace(" ", "_")
        if key not in KEYS:
            return f"key must be one of {', '.join(KEYS)}, not {key!r}."
        r = bp.ask("press", key=key)
        if r.get("error"):
            return f"Not pressed: {r['error']}."
        hint = " (in the phone's camera app this takes the photo)" if key == "shutter" else ""
        return f"Pressed {key.replace('_', ' ')} on the phone{hint}."
    if action == "type":
        text = str(arguments.get("text") or "")
        if not text:
            return "type needs text. Nothing was typed."
        r = bp.ask("type", text=text)
        if r.get("error"):
            return f"Not typed: {r['error']}."
        missing = r.get("missing") or ""
        return (f"Typed {len(text) - len(missing)} characters into the phone's focused field."
                + (f" Could not type {missing!r}: a US keyboard has no keys for them." if missing else ""))
    return f"action must be status, press or type, not {action!r}."


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
