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
        "description": ("Open or launch an installed application by name or by kind, e.g. 'firefox', "
                        "'files', 'settings', 'music player', 'text editor'. Not for websites: "
                        "use browse to open a site and work on it."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The application name to launch."},
            },
            "required": ["name"],
        },
    },
}


#: How long a launched app must stay alive to count as started (sayri's
#: executor watches a GUI launch for 2.5 s for the same reason: a program that
#: dies at once has not "opened").
ALIVE_SECONDS = 1.5


def find_app(name: str):
    """The best installed app for `name`: exact name/id, then a substring, then the desktop's own search."""
    needle = name.lower()
    best: Any = None
    for app_info in Gio.AppInfo.get_all():
        if not app_info.should_show():
            continue
        display = (app_info.get_display_name() or "").lower()
        app_id = (app_info.get_id() or "").lower().removesuffix(".desktop")
        if needle == display or needle == app_id:
            return app_info
        if best is None and (needle in display or needle in app_id):
            best = app_info
    if best is not None:
        return best
    # "music player", "browser", "text editor": the desktop's own ranked search
    # over names, GenericName, Keywords and Categories - what the app grid uses.
    for group in Gio.DesktopAppInfo.search(name) or []:
        for desktop_id in group:
            info = Gio.DesktopAppInfo.new(desktop_id)
            if info is not None and info.should_show():
                return info
    return None


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
            return handle.read().split(") ", 1)[1][0] != "Z"
    except (OSError, IndexError):
        return False


def _run(arguments: dict) -> str:
    import time
    name = (arguments.get("name") or "").strip()
    if not name:
        return "No application name given."
    best = find_app(name)
    if best is None:
        return f"Could not find an installed application matching '{name}'."
    label = best.get_display_name()
    pids: list = []
    try:
        if isinstance(best, Gio.DesktopAppInfo):
            launched = best.launch_uris_as_manager([], None, GLib.SpawnFlags.SEARCH_PATH, None, None,
                                                   lambda _info, pid, *_: pids.append(pid), None)
        else:
            launched = best.launch([], None)
    except GLib.Error as e:
        return f"Failed to launch {label}: {e.message}"
    if not launched:
        return f"Failed to launch {label}."
    if not pids:
        # D-Bus-activated apps are started by the session, not as our child
        return f"Asked the desktop to open {label}."
    time.sleep(ALIVE_SECONDS)
    if _alive(pids[0]):
        return f"Opened {label} (running)."
    return f"{label} started but exited straight away - it may be broken or need something it lacks."


def _post_condition(arguments: dict):
    """Re-read the world: is a process of that app's executable running now?

    None (unverified) for launchers whose own name says nothing about the app
    (flatpak, env, a shell wrapper) and for D-Bus-activated apps.
    """
    import os
    info = find_app((arguments.get("name") or "").strip()) if arguments.get("name") else None
    exe = os.path.basename(info.get_executable() or "") if info is not None else ""
    if not exe or exe in ("flatpak", "env", "sh", "bash", "gapplication", "gtk-launch", "snap"):
        return None
    comm = exe[:15]  # the kernel truncates comm to 15 characters
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/comm", encoding="utf-8", errors="replace") as handle:
                    if handle.read().strip() == comm:
                        return True, f"{exe} is running"
            except OSError:
                continue
    return False, f"no {exe} process is running"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="open_application", schema=_SCHEMA, run=_run)]
