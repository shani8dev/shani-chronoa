"""Skills: read/set system output volume and mute state via wpctl."""

import subprocess

from shani_chronoa.skills import Skill


def _run_wpctl(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        ["wpctl", *args], capture_output=True, text=True, timeout=5
    )


def _run_get_volume(_arguments: dict) -> str:
    try:
        result = _run_wpctl("get-volume", "@DEFAULT_AUDIO_SINK@")
    except Exception as e:
        return f"Could not read volume: {e}"
    if result.returncode != 0:
        return "Could not read volume."
    return result.stdout.strip()


def _run_set_volume(arguments: dict) -> str:
    percent = arguments.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, int):
        return "Invalid volume percentage: expected an integer between 0 and 100."
    percent = max(0, min(percent, 100))
    try:
        result = _run_wpctl("set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%")
    except Exception as e:
        return f"Failed to set volume: {e}"
    if result.returncode != 0:
        return f"Failed to set volume: {result.stderr.strip()}"
    return f"Volume set to {percent}%."


def _run_set_mute(arguments: dict) -> str:
    mute = arguments.get("mute")
    if not isinstance(mute, bool):
        return "Invalid mute argument: expected true or false."
    try:
        result = _run_wpctl("set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0")
    except Exception as e:
        return f"Failed to change mute state: {e}"
    if result.returncode != 0:
        return f"Failed to change mute state: {result.stderr.strip()}"
    return "Muted." if mute else "Unmuted."


SKILLS = [
    Skill(
        name="get_volume",
        schema={
            "type": "function",
            "function": {
                "name": "get_volume",
                "description": "Get the current system output volume and mute state.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        run=_run_get_volume,
    ),
    Skill(
        name="set_volume",
        schema={
            "type": "function",
            "function": {
                "name": "set_volume",
                "description": "Set the system output volume to an exact percentage.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "percent": {"type": "integer", "description": "Target volume percentage, 0-100."},
                    },
                    "required": ["percent"],
                },
            },
        },
        run=_run_set_volume,
    ),
    Skill(
        name="set_mute",
        schema={
            "type": "function",
            "function": {
                "name": "set_mute",
                "description": "Mute or unmute the system output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mute": {"type": "boolean", "description": "true to mute, false to unmute."},
                    },
                    "required": ["mute"],
                },
            },
        },
        run=_run_set_mute,
    ),
]
