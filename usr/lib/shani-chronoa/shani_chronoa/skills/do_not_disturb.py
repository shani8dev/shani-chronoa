"""Skill: Do Not Disturb - silence notification banners, or let them through
again. GNOME keeps it in org.gnome.desktop.notifications show-banners, the same
switch as its own Do Not Disturb toggle, so the desktop shows the same state."""

import subprocess

from shani_chronoa.skills import Skill

SCHEMA_ID, KEY = "org.gnome.desktop.notifications", "show-banners"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "do_not_disturb",
        "description": "Turn Do Not Disturb on (silence notification pop-ups) or off, or say whether it is on.",
        "parameters": {"type": "object", "properties": {
            "enabled": {"type": "boolean", "description": "true = Do Not Disturb on; omit to ask."}}},
    },
}


def _run(arguments: dict) -> str:
    try:
        cur = subprocess.run(["gsettings", "get", SCHEMA_ID, KEY], capture_output=True, text=True, timeout=5)
    except OSError:
        return "Do Not Disturb is set in this desktop's own settings; I can only switch GNOME's."
    if cur.returncode != 0:
        return "This desktop does not keep GNOME's notification settings; use its own Do Not Disturb switch."
    on = cur.stdout.strip() == "false"
    want = arguments.get("enabled")
    if want is None:
        return f"Do Not Disturb is {'on' if on else 'off'}."
    r = subprocess.run(["gsettings", "set", SCHEMA_ID, KEY, "false" if want else "true"],
                       capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        return f"Could not change Do Not Disturb: {r.stderr.strip()[:150]}"
    return f"Do Not Disturb is now {'on' if want else 'off'}."


SKILLS = [Skill(name="do_not_disturb", schema=_SCHEMA, run=_run)]
