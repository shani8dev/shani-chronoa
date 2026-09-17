"""Skill: set a countdown timer that fires a desktop notification."""

import subprocess
import threading

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_timer",
        "description": "Set a countdown timer that sends a desktop notification when it finishes.",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "Duration of the timer in seconds."},
                "label": {"type": "string", "description": "What the timer is for, e.g. 'pasta'."},
            },
            "required": ["seconds"],
        },
    },
}


_MAX_SECONDS = 24 * 3600


def _run(arguments: dict) -> str:
    seconds = arguments.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return "Invalid timer duration: expected an integer number of seconds."
    seconds = max(1, min(seconds, _MAX_SECONDS))
    label = (arguments.get("label") or "Timer").strip()

    def _fire() -> None:
        if not ChronoaConfig().notification_enabled:
            return
        try:
            subprocess.run(
                ["notify-send", "Shani Chronoa", f"⏰ {label}"], check=False
            )
        except OSError:
            # notify-send may be absent on headless systems; the timer itself
            # already ran, so a missing notifier must not crash the thread.
            return

    timer = threading.Timer(seconds, _fire)
    timer.daemon = True
    timer.start()
    return f"Timer set for {seconds} seconds: '{label}'."


SKILLS = [Skill(name="set_timer", schema=_SCHEMA, run=_run)]
