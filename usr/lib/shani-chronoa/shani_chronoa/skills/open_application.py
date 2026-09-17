"""Skill: launch an installed application by name."""

from typing import Any

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib  # type: ignore

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_application",
        "description": "Open or launch an installed application by name, e.g. 'firefox', 'files', 'settings'.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The application name to launch."},
            },
            "required": ["name"],
        },
    },
}


def _run(arguments: dict) -> str:
    name = (arguments.get("name") or "").strip()
    if not name:
        return "No application name given."

    needle = name.lower()
    best: Any = None
    for app_info in Gio.AppInfo.get_all():
        if not app_info.should_show():
            continue
        display = (app_info.get_display_name() or "").lower()
        app_id = (app_info.get_id() or "").lower().removesuffix(".desktop")
        if needle == display or needle == app_id:
            best = app_info
            break
        if best is None and (needle in display or needle in app_id):
            best = app_info

    if best is None:
        return f"Could not find an installed application matching '{name}'."

    try:
        launched = best.launch([], None)
    except GLib.Error as e:
        return f"Failed to launch {best.get_display_name()}: {e.message}"

    if not launched:
        return f"Failed to launch {best.get_display_name()}."
    return f"Opened {best.get_display_name()}."


SKILLS = [Skill(name="open_application", schema=_SCHEMA, run=_run)]
