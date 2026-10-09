"""Skill: a MoYoung ("Da Fit") smartwatch - health data, measurements, settings, messages.

Everything goes through `shani_chronoa/moyoung.py` (Gadgetbridge's protocol,
measured on an FB BGS002). Gated on `bluetooth-gatt-enabled`, the switch for
reaching a paired watch at all. Weight is a profile value the watch uses for
calories; it cannot measure it, and `profile` says so rather than pretending.
"""

from __future__ import annotations

import shutil
from datetime import datetime

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "bluetooth-gatt-enabled"
ACTIONS = ("summary", "measure", "settings", "set", "sync_time", "notify", "profile")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Reaching your watch is turned off. Nothing was asked or sent. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


def _pick(device: str) -> "tuple[str, str] | str":
    """(mac, name) of the watch, or why none could be picked."""
    from shani_chronoa.skills import bluetooth_gatt as bg
    from shani_chronoa import phone_bluez
    phones = {a for a, _n, _c in phone_bluez.phones()}
    found = [(m, n) for m, n in bg.paired() if bg.looks_like_low_energy(m) and m not in phones]
    if device:
        found = [(m, n) for m, n in found if device.lower() in n.lower()]
    if not found:
        return "No paired watch or band was found." + (f" Nothing matches {device!r}." if device else "")
    if len(found) > 1:
        return f"{len(found)} devices could be the watch ({', '.join(n for _, n in found)}); pass device."
    return found[0]


def _hm(minutes) -> str:
    return "unknown" if minutes is None else f"{minutes // 60} h {minutes % 60} min"


def _summary(mac: str, name: str) -> str:
    from shani_chronoa import moyoung as m
    s = m.health_summary(mac)
    lines = [f"{name}:"]
    for key, label in (("steps_today", "Today"), ("steps_yesterday", "Yesterday"),
                       ("steps_2_days_ago", "Two days ago")):
        v = s.get(key)
        lines.append(f"- {label}: " + ("no reply" if v is None else
                                        f"{v['steps']} steps, {v['distance_m']} m, {v['calories']} kcal"))
    sleep = s.get("sleep_summary")
    lines.append("- Sleep: " + ("none recorded" if not sleep else
                                f"{_hm(sleep['asleep_minutes'])} asleep ({_hm(sleep['deep_minutes'])} deep, "
                                f"{_hm(sleep['light_minutes'])} light, {_hm(sleep['awake_minutes'])} awake)"))
    stress = s.get("stress_today") or []
    lines.append("- Stress today: " + ("no readings" if not stress else
                                       ", ".join(f"{w:%H:%M} {v}" for w, v in stress[-6:])))
    training = s.get("training") or []
    lines.append("- Workouts: " + ("none recorded" if not training else "; ".join(
        f"{t['type']} {t['start']:%d %b %H:%M}, {t['steps']} steps, {t['calories']} kcal" for t in training)))
    lines.append("All zeros usually means the watch was not worn, or a phone app already synced the data off it.")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "watch needs its arguments as an object, e.g. {\"action\": \"summary\"}."
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal
    if not shutil.which("gatttool") or not shutil.which("bluetoothctl"):
        return "bluez's gatttool and bluetoothctl are needed to reach the watch, and are not installed."
    action = str(arguments.get("action") or "summary").strip().lower()
    if action not in ACTIONS:
        return f"action must be one of {', '.join(ACTIONS)}, not {action!r}."
    picked = _pick(str(arguments.get("device") or "").strip())
    if isinstance(picked, str):
        return picked
    mac, name = picked
    from shani_chronoa import moyoung as m
    try:
        if action == "summary":
            return _summary(mac, name)
        if action == "measure":
            what = str(arguments.get("what") or "heart_rate").strip().lower().replace(" ", "_")
            what = {"hr": "heart_rate", "spo2": "blood_oxygen", "oxygen": "blood_oxygen",
                    "bp": "blood_pressure"}.get(what, what)
            if what not in m.MEASUREMENTS:
                return f"what must be heart_rate, blood_oxygen or blood_pressure, not {what!r}."
            got = m.measure(mac, what)
            if got is None:
                return (f"{name} reported no {what.replace('_', ' ')} - it has to be worn snugly and "
                        "kept still for the minute it measures. Nothing is wrong with asking again.")
            if what == "blood_pressure":
                return f"{name} measured blood pressure {got[0]}/{got[1]} mmHg (a wrist estimate, not a medical reading)."
            unit = "bpm" if what == "heart_rate" else "%"
            return f"{name} measured {what.replace('_', ' ')} {got[0]} {unit}."
        with m.connect(mac) as w:
            if action == "settings":
                st = m.read_settings(w)
                return f"{name} settings:\n" + "\n".join(
                    f"- {k.replace('_', ' ')}: {'not supported' if v is None else v}" for k, v in st.items())
            if action == "set":
                setting = str(arguments.get("setting") or "").strip().lower().replace(" ", "_")
                value = arguments.get("value")
                if setting == "do_not_disturb" and isinstance(value, str) and "-" in value:
                    value = tuple(x.strip() for x in value.split("-", 1))
                ok, now = m.change_setting(w, setting, value)
                return (f"{name}: {setting.replace('_', ' ')} is now {now}." if ok else
                        f"{name} did not take it: {setting.replace('_', ' ')} still reads {now}.")
            if action == "sync_time":
                w.request(m.CMD_SYNC_TIME, m.time_payload(), seconds=0.5)
                return f"Set {name}'s clock to {datetime.now():%H:%M}."
            if action == "notify":
                text = str(arguments.get("text") or "").strip()
                if not text:
                    return "notify needs text. Nothing was sent."
                w.request(m.CMD_SEND_MESSAGE, m.message_payload(
                    str(arguments.get("kind") or "other"), str(arguments.get("sender") or "Chronoa"), text),
                    seconds=0.5)
                return f"Sent to {name}: {text[:80]}. The watch does not confirm it was shown."
            if action == "profile":
                need = [k for k in ("height_cm", "weight_kg", "age", "gender") if arguments.get(k) in (None, "")]
                if need:
                    return ("profile needs height_cm, weight_kg, age and gender (missing: " + ", ".join(need)
                            + "). The watch cannot measure weight; it uses this one for calories.")
                w.request(m.CMD_SET_USER_INFO, m.user_info_payload(
                    arguments["height_cm"], arguments["weight_kg"], arguments["age"], arguments["gender"]),
                    seconds=m.SET_SETTLE)
                return (f"Gave {name} your profile ({arguments['height_cm']} cm, {arguments['weight_kg']} kg). "
                        "The watch has no way to read it back, so this is unconfirmed.")
    except m.WatchError as exc:
        return f"Could not reach {name}: {exc}."
    return "Nothing was done."


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
            "'22:00-07:00'), 'sync_time', 'notify' (show a message on the watch), or set the 'profile' "
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

SKILLS = [Skill(name="watch", schema=SCHEMA, run=_run)]
