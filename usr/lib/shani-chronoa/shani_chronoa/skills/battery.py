"""Skill: report battery charge percentage and charging state via upower."""

import subprocess

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_battery_status",
        "description": "Get the current battery charge percentage and charging state.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run(_arguments: dict) -> str:
    try:
        devices = subprocess.run(
            ["upower", "-e"], capture_output=True, text=True, timeout=5
        ).stdout.splitlines()
    except Exception as e:
        return f"Could not query battery: {e}"

    battery_path = next((d for d in devices if "battery" in d.lower()), None)
    if not battery_path:
        return "No battery detected on this system."

    try:
        info = subprocess.run(
            ["upower", "-i", battery_path], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception as e:
        return f"Could not query battery: {e}"

    percentage = next(
        (l.split(":", 1)[1].strip() for l in info.splitlines() if "percentage" in l),
        "unknown",
    )
    state = next(
        (l.split(":", 1)[1].strip() for l in info.splitlines() if l.strip().startswith("state:")),
        "unknown",
    )
    return f"Battery is at {percentage} and {state}."


SKILLS = [Skill(name="get_battery_status", schema=_SCHEMA, run=_run)]
