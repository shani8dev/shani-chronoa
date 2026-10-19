"""Skill: launch an installed application by name."""

from typing import Any

import os

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


#: How long a launch is given to put a window on screen.
WINDOW_WAIT_SECONDS = 10.0


def _launch_outside(desktop_id: str) -> str:
    """Have the desktop start the app, not this skill; who did it, or "".

    **Not as our child.** A skill runs under resource limits (an
    address-space cap among them), and a process it starts inherits them:
    measured 2026-10-15, GNOME Calculator launched from here died at once.
    GNOME Shell (through Chronoa's extension) launches it as the app grid
    would; elsewhere the user's systemd starts it with `gtk-launch`.
    """
    if not desktop_id:
        return ""
    try:
        from shani_chronoa.windows import gnome
        shell = gnome.launch_backend()
        if shell is not None:
            shell.launch_app(desktop_id)
            return "GNOME Shell"
    except Exception:  # noqa: BLE001 - try the next way
        pass
    import shutil
    import subprocess
    if shutil.which("systemd-run") and shutil.which("gtk-launch"):
        done = subprocess.run(["systemd-run", "--user", "--collect", "--quiet", "gtk-launch",
                               desktop_id.removesuffix(".desktop")],
                              capture_output=True, text=True, timeout=20)
        if done.returncode == 0:
            return "the session (systemd)"
    return ""


def _window_ids():
    """Ids of the windows on screen now, or None when they cannot be listed."""
    try:
        from shani_chronoa.windows import detect
        backend, _why = detect()
        if backend is None:
            return None
        return {str(w.id) for w in backend.list()}
    except Exception:  # noqa: BLE001 - no window list is "unknown", not "none"
        return None


def _wait_for_window(before, info, label: str) -> str:
    """The title of a new window of this app, once one appears; "" if none does."""
    import time
    if before is None:
        return ""
    try:
        from shani_chronoa.windows import detect
        backend, _why = detect()
    except Exception:  # noqa: BLE001
        return ""
    app_id = (info.get_id() or "").removesuffix(".desktop").lower() if hasattr(info, "get_id") else ""
    wanted = {app_id, label.lower()}
    end = time.monotonic() + WINDOW_WAIT_SECONDS
    while time.monotonic() < end:
        try:
            windows = backend.list()
        except Exception:  # noqa: BLE001
            return ""
        for w in windows:
            fields = {str(getattr(w, k, "") or "").lower() for k in ("app_id", "wm_class", "title")}
            if str(w.id) not in before and (fields & wanted or any(
                    label.lower() in f or (app_id and app_id in f) for f in fields)):
                return str(getattr(w, "title", "") or label)
        time.sleep(0.4)
    return ""


def _run(arguments: dict) -> str:
    name = (arguments.get("name") or "").strip()
    if not name:
        return "No application name given."
    best = find_app(name)
    if best is None:
        return f"Could not find an installed application matching '{name}'."
    label = best.get_display_name()
    before = _window_ids()
    pids: list = []
    desktop_id = best.get_id() if hasattr(best, "get_id") else ""
    how = _launch_outside(desktop_id)
    if how:
        window = _wait_for_window(before, best, label)
        if window:
            return f"Opened {label}: its window '{window}' is on screen and has focus."
        return (f"Asked {how} to open {label}, but no window of it appeared within "
                f"{WINDOW_WAIT_SECONDS:.0f}s - do not type into it until list_windows shows it.")
    try:
        if isinstance(best, Gio.DesktopAppInfo):
            # **Its output to /dev/null, not into this answer.** Launched as
            # our child, the app inherited the skill's stdout: measured
            # 2026-10-15, open_application's reply was GNOME Calculator's
            # currency-rate log and Fontconfig warnings.
            null = os.open(os.devnull, os.O_RDWR)
            try:
                launched = best.launch_uris_as_manager_with_fds(
                    [], None, GLib.SpawnFlags.SEARCH_PATH, None, None,
                    lambda _info, pid, *_: pids.append(pid), None, null, null, null)
            finally:
                os.close(null)
        else:
            launched = best.launch([], None)
    except GLib.Error as e:
        return f"Failed to launch {label}: {e.message}"
    if not launched:
        return f"Failed to launch {label}."
    # **The window, not the launch, is what "opened" means.** The demo's
    # model was told "Opened text_editor", typed straight away, and the text
    # went into Chronoa's own message box: no window had appeared.
    window = _wait_for_window(before, best, label)
    if window:
        return f"Opened {label}: its window '{window}' is on screen and has focus."
    if pids and not _alive(pids[0]):
        return f"{label} started but exited straight away - it may be broken or need something it lacks."
    if before is None:
        return (f"Asked the desktop to open {label}; this desktop cannot list windows, so "
                f"whether it appeared is unknown.")
    return (f"Asked the desktop to open {label}, but no window of it appeared within "
            f"{WINDOW_WAIT_SECONDS:.0f}s - do not type into it until list_windows shows it.")


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
