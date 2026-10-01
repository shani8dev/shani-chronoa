"""Skill: find, install or remove apps from Flathub - 'install VLC'.

flatpak, per user (--user: no administrator password, nothing system-wide).
Searching reads Flatpak's own app catalogue and changes nothing. Installing
and removing are behind `app-install-enabled`, off by default, and only ever
for an exact app ID that a search has shown - never a guessed name.
"""

import re
import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "app-install-enabled"
_APP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "install_app",
        "description": "Search Flathub for an app, or install / remove one for this user by its app ID "
                       "(e.g. org.videolan.VLC, from a search). Installing and removing need the "
                       "'app-install-enabled' consent key.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["search", "install", "remove", "list"]},
            "query": {"type": "string", "description": "For search: the app's name or what it does."},
            "app_id": {"type": "string", "description": "For install/remove: the exact app ID."},
        }, "required": ["action"]},
    },
}


def _fp(*args, timeout=60):
    return subprocess.run(["flatpak", *args], capture_output=True, text=True, timeout=timeout)


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Not done: installing and removing apps is switched off ('{_CONSENT_KEY}'). "
                       "Turn on 'Let Chronoa install and remove apps' in Settings to allow it.")
    return True, ""


def _run(arguments: dict) -> str:
    if not shutil.which("flatpak"):
        return "Flatpak is not installed."
    action = arguments.get("action")
    if action == "search":
        q = (arguments.get("query") or "").strip()
        if not q or len(q) > 80:
            return "Search for what?"
        r = _fp("search", "--columns=name,application,description", q)
        rows = [l.split("\t") for l in r.stdout.splitlines() if l.count("\t") >= 2][:6]
        return ("Found: " + "; ".join(f"{n} ({a}) - {d[:60]}" for n, a, d, *_ in rows) + ".") if rows \
            else f"Flathub has nothing for '{q}' (or its catalogue is not downloaded yet)."
    if action == "list":
        r = _fp("list", "--app", "--columns=name,application")
        apps = [l.split("\t")[0] for l in r.stdout.splitlines() if l.strip()]
        return f"{len(apps)} Flatpak apps: {', '.join(apps[:30])}." if apps else "No Flatpak apps are installed."
    app_id = (arguments.get("app_id") or "").strip()
    if not _APP_ID.match(app_id):
        return "That needs the app's exact ID (like org.videolan.VLC) - search for it first."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return reason
    if action == "install":
        r = _fp("install", "--user", "--noninteractive", "-y", "flathub", app_id, timeout=900)
    elif action == "remove":
        r = _fp("uninstall", "--user", "--noninteractive", "-y", app_id, timeout=300)
    else:
        return "Search, list, install or remove?"
    if r.returncode != 0:
        return f"flatpak could not {action} {app_id}: {(r.stderr.strip().splitlines() or ['no message'])[-1][:200]}"
    return f"{'Installed' if action == 'install' else 'Removed'} {app_id}" + \
        (" - it is in your app list now." if action == "install" else ".")


SKILLS = [Skill(name="install_app", schema=_SCHEMA, run=_run)]
