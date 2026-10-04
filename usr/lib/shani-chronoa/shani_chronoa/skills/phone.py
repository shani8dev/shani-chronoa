"""Skill: the user's paired phone - status (reachable, battery), ring it, ping it, send it a file or link.

Through GSConnect (GNOME) or KDE Connect (Plasma); see shani_chronoa/phone.py
for what is deliberately left out (SMS, reading notifications, remote input).
Gated by `phone-control-enabled`, off by default.
"""

from __future__ import annotations

import os

from shani_chronoa import files
from shani_chronoa import phone as ph
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "phone-control-enabled"
_ACTIONS = ("status", "ring", "ping", "share")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "phone",
        "description": ("The user's paired phone via GSConnect/KDE Connect: status (reachable, battery), ring it "
                        "to find it, ping it, or share a file or link to it. Requires the 'phone-control-enabled' "
                        "consent key."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "device": {"type": "string", "description": "Which phone, by name; omit if there is one."},
            "target": {"type": "string", "description": "For share: a file path or an http(s) link."},
        }, "required": ["action"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, f"using your phone is turned off (enable '{_CONSENT_KEY}' in Settings)."
    return True, ""


def _pick(devs: "list[ph.Device]", wanted: str) -> "tuple[ph.Device | None, str]":
    paired = [d for d in devs if d.paired]
    if not paired:
        return None, "No phone is paired. Pair one in GSConnect or KDE Connect first."
    if wanted:
        match = [d for d in paired if wanted.lower() in d.name.lower() or wanted == d.id]
        if not match:
            return None, f"No paired phone matches {wanted!r}; paired: {', '.join(d.name for d in paired)}."
        return match[0], ""
    if len(paired) > 1:
        return None, f"Which phone? Paired: {', '.join(d.name for d in paired)}."
    return paired[0], ""


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to use your phone: {reason}"
    action = (arguments.get("action") or "status").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}, not {action!r}."
    try:
        devs = ph.devices()
    except ph.PhoneUnavailable as exc:
        return f"I cannot reach a phone link: {exc}."
    if action == "status":
        if not devs:
            return "No phones are known to the phone link - none has been paired yet."
        lines = []
        for d in devs:
            bat = ph.battery(d) if d.reachable else None
            lines.append(f"- {d.name}: {'reachable' if d.reachable else 'not reachable'}"
                         + ("" if d.paired else ", not paired")
                         + (f", battery {bat[0]}%{' charging' if bat[1] else ''}" if bat else ""))
        return "Phones:\n" + "\n".join(lines)
    dev, problem = _pick(devs, (arguments.get("device") or "").strip())
    if dev is None:
        return problem
    if not dev.reachable:
        return f"{dev.name} is paired but not reachable right now (off, out of range, or on another network)."
    payload = ""
    if action == "share":
        target = (arguments.get("target") or "").strip()
        if not target:
            return "Share what? Give a file path or a link."
        if target.startswith(("http://", "https://")):
            payload = target
        else:
            try:
                path = files.resolve(target)
            except files.PathProblem as exc:
                return str(exc)
            if not path.is_file():
                return f"{path} is not a file."
            payload = os.fspath(path)
    ok, detail = ph.act(dev, action, payload)
    verb = {"ring": "Ringing", "ping": "Pinged", "share": "Sent"}[action]
    if not ok:
        return f"The phone link could not {action} {dev.name}: {detail or 'no detail'}."
    return f"{verb} {dev.name}" + (f": {os.path.basename(payload) or payload}" if payload else ".")


SKILLS = [Skill(name="phone", schema=SCHEMA, run=_run)]
