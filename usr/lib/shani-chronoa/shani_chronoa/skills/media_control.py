"""Skill: play, pause, skip, or say what is playing - in any media player.

Every Linux media player that shows up in the desktop's media controls
(Spotify, Firefox, Rhythmbox, VLC, mpv with mpris, ...) speaks MPRIS over the
session bus, and `gdbus` - part of glib2, so already on every Shanios
install - is enough to drive it. No new dependency, nothing leaves the
machine. With several players, the one playing is preferred.
"""

import re
import shutil
import subprocess

from shani_chronoa.skills import Skill

_PATH, _PLAYER = "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player"
ACTIONS = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "next": "Next",
           "previous": "Previous", "stop": "Stop"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "media_control",
        "description": "Control music and video players: play, pause, toggle, next track, previous "
                       "track, stop, or 'status' to say what is playing.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": sorted(ACTIONS) + ["status"],
                       "description": "What to do."},
        }, "required": ["action"]},
    },
}


def _gdbus(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["gdbus", "call", "--session", *args], capture_output=True, text=True, timeout=5)


def players() -> list:
    r = _gdbus("--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
               "--method", "org.freedesktop.DBus.ListNames")
    return sorted(set(re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", r.stdout)))


def _prop(name: str, prop: str) -> str:
    r = _gdbus("--dest", name, "--object-path", _PATH, "--method",
               "org.freedesktop.DBus.Properties.Get", _PLAYER, prop)
    return r.stdout


def _status(name: str) -> str:
    m = re.search(r"'(Playing|Paused|Stopped)'", _prop(name, "PlaybackStatus"))
    return m.group(1) if m else "Unknown"


def _now_playing(name: str) -> str:
    meta = _prop(name, "Metadata")
    title = re.search(r"'xesam:title': <'((?:[^'\\]|\\.)*)'>", meta)
    artist = re.search(r"'xesam:artist': <\['((?:[^'\\]|\\.)*)'", meta)
    t = title.group(1) if title else ""
    return (t + (f" by {artist.group(1)}" if artist else "")) or "something untitled"


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action not in ACTIONS and action != "status":
        return f"Unknown media action '{action}'. Use one of: {', '.join(sorted(ACTIONS))}, status."
    if not shutil.which("gdbus"):
        return "gdbus (glib2) is not available, so no media player can be reached."
    found = players()
    if not found:
        return "No media player is running."
    playing = [p for p in found if _status(p) == "Playing"]
    target = (playing or found)[0]
    # the player's own name for itself ("Firefox", "Spotify"); the bus name
    # ends in an instance id ("chromium.instance4567")
    ident = re.search(r"<'((?:[^'\\]|\\.)*)'>", _gdbus(
        "--dest", target, "--object-path", _PATH, "--method", "org.freedesktop.DBus.Properties.Get",
        "org.mpris.MediaPlayer2", "Identity").stdout)
    app = ident.group(1) if ident else target.rsplit(".", 1)[-1]
    if action == "status":
        state = _status(target)
        return f"{app} is {state.lower()}" + (f": {_now_playing(target)}." if state != "Stopped" else ".")
    r = _gdbus("--dest", target, "--object-path", _PATH, "--method", f"{_PLAYER}.{ACTIONS[action]}")
    if r.returncode != 0:
        return f"{app} refused '{action}': {r.stderr.strip()[:160]}"
    return f"Done: {action} in {app}."


SKILLS = [Skill(name="media_control", schema=_SCHEMA, run=_run)]
