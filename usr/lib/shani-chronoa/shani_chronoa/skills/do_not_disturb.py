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


def is_on():
    """The one read of the switch: True, False, or None when there is none.

    Unattended announcement policy asks this rather than running gsettings
    itself, so the reflex layer keeps its single subprocess call (notify-send).
    """
    try:
        cur = subprocess.run(["gsettings", "get", SCHEMA_ID, KEY], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if cur.returncode != 0:
        return None
    return cur.stdout.strip() == "false"


def _run(arguments: dict) -> str:
    on = is_on()
    if on is None:
        return "This desktop does not keep GNOME's notification settings; use its own Do Not Disturb switch."
    want = arguments.get("enabled")
    if want is None:
        return f"Do Not Disturb is {'on' if on else 'off'}."
    r = subprocess.run(["gsettings", "set", SCHEMA_ID, KEY, "false" if want else "true"],
                       capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        return f"Could not change Do Not Disturb: {r.stderr.strip()[:150]}"
    return f"Do Not Disturb is now {'on' if want else 'off'}."



def _post_condition(arguments: dict):
    """show-banners read back; GNOME's own switch reads the same key."""
    want = arguments.get("enabled")
    if not isinstance(want, bool):
        return None
    on = is_on()
    if on is None:
        return None
    return on == want, f"Do Not Disturb reads back {'on' if on else 'off'}"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="do_not_disturb", schema=_SCHEMA, run=_run)]
