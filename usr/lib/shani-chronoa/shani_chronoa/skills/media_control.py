"""Skill: play, pause, skip, or say what is playing - in any media player.

Every Linux media player that shows up in the desktop's media controls
(Spotify, Firefox, Rhythmbox, VLC, mpv with mpris, ...) speaks MPRIS over the
session bus, and `gdbus` - part of glib2, so already on every Shanios
install - is enough to drive it. No new dependency, nothing leaves the
machine. With several players, the one playing is preferred.

`restart` and `seek` use the Player interface's own SetPosition and Seek
methods, after checking the player's CanSeek property: a live stream or a
player that does not implement seeking says so rather than "done". The
position is read back afterwards and reported, so a seek the player ignored
is not described as one that happened.
"""

import re
import shutil
import subprocess

from shani_chronoa.skills import Skill

_PATH, _PLAYER = "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player"
ACTIONS = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "next": "Next",
           "previous": "Previous", "stop": "Stop"}

#: actions that move the play position rather than call a no-argument method
SEEK_ACTIONS = ("restart", "seek")
#: a seek longer than this is almost certainly a misheard number
MAX_SEEK_SECONDS = 6 * 3600

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "media_control",
        "description": "Control music and video players: play, pause, toggle, next track, previous "
                       "track, stop, restart (current track from the beginning), seek (skip ahead or "
                       "go back by 'seconds'; negative goes back), or 'status' to say what is playing.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": sorted(ACTIONS) + ["restart", "seek", "status"],
                       "description": "What to do."},
            "seconds": {"type": "number",
                        "description": "seek only: how far to move, in seconds; positive skips ahead, "
                                       "negative goes back (e.g. 30 or -10)."},
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


def _position(name: str):
    """The play position in microseconds, or None when the player does not say."""
    m = re.search(r"<(?:int64 )?(-?\d+)>", _prop(name, "Position"))
    return int(m.group(1)) if m else None


def _clock(us: int) -> str:
    total = max(0, int(round(us / 1_000_000)))
    h, rem = divmod(total, 3600)
    return (f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}")


def _seconds(arguments: dict):
    """(offset in microseconds, problem)."""
    raw = arguments.get("seconds")
    if raw is None or isinstance(raw, bool):
        return None, "seek needs 'seconds': how far to move (positive skips ahead, negative goes back)."
    try:
        value = float(str(raw).strip().removesuffix("s"))
    except ValueError:
        return None, f"Could not use {raw!r} as a number of seconds."
    if value != value or value in (float("inf"), float("-inf")):
        return None, f"Could not use {raw!r} as a number of seconds."
    if value == 0:
        return None, "Seeking by 0 seconds would not move anything."
    if abs(value) > MAX_SEEK_SECONDS:
        return None, f"{value:g} seconds is more than {MAX_SEEK_SECONDS // 3600} hours; not seeking that far."
    return int(value * 1_000_000), ""


def _move(target: str, app: str, action: str, offset: int) -> str:
    if not re.search(r"<true>", _prop(target, "CanSeek")):
        return f"{app} does not allow seeking in what it is playing, so nothing was moved."
    before = _position(target)
    if action == "restart":
        track = re.search(r"'mpris:trackid': <(?:objectpath )?'([^']+)'>", _prop(target, "Metadata"))
        if track:
            r = _gdbus("--dest", target, "--object-path", _PATH, "--method", f"{_PLAYER}.SetPosition",
                       f"objectpath '{track.group(1)}'", "int64 0")
        else:
            # Without a track id SetPosition cannot be called; the spec says a
            # Seek back past the start lands on the start.
            r = _gdbus("--dest", target, "--object-path", _PATH, "--method", f"{_PLAYER}.Seek",
                       f"int64 {-(before or 0) - 1_000_000}")
    else:
        r = _gdbus("--dest", target, "--object-path", _PATH, "--method", f"{_PLAYER}.Seek",
                   f"int64 {offset}")
    if r.returncode != 0:
        return f"{app} refused '{action}': {r.stderr.strip()[:160]}"
    after = _position(target)
    if after is None:
        return f"Asked {app} to {action}; it does not report its position, so where it is now is not verified."
    if action == "restart":
        if after <= 3_000_000:
            return f"Restarted the track in {app} (now at {_clock(after)})."
        return f"Asked {app} to restart the track, but it reads {_clock(after)}, so it did not go back to the start."
    if before is not None and after == before:
        return f"Asked {app} to move {offset / 1e6:+g} s, but the position still reads {_clock(after)}."
    direction = "ahead" if offset > 0 else "back"
    return f"Moved {abs(offset) / 1e6:g} s {direction} in {app} (now at {_clock(after)})."


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "media_control needs an action."
    raw = arguments.get("action")
    action = raw.strip().lower() if isinstance(raw, str) else ""
    if action not in ACTIONS and action not in SEEK_ACTIONS and action != "status":
        return (f"Unknown media action '{action or raw}'. Use one of: "
                f"{', '.join(sorted(ACTIONS) + list(SEEK_ACTIONS))}, status.")
    offset = 0
    if action == "seek":
        offset, problem = _seconds(arguments)
        if problem:
            return problem
    if not shutil.which("gdbus"):
        return "gdbus (glib2) is not available, so no media player can be reached."
    found = players()
    if not found:
        # A phone's player over Bluetooth, straight from bluez (AVRCP). The
        # route through `mpris-proxy` was measured crashing (SIGSEGV 40 s after
        # start), and then the phone's music simply vanished from here.
        from shani_chronoa import phone_bluez
        phone_players = phone_bluez.avrcp_players()
        if phone_players:
            path, name = phone_players[0]
            if action == "status":
                state, now = phone_bluez.avrcp_status(path)
                return f"{name} is {state.lower()}" + (f": {now}." if now else ".")
            if action in SEEK_ACTIONS:
                return f"Seeking is not offered for {name}'s music over Bluetooth."
            ok, why = phone_bluez.avrcp(path, action)
            if ok:
                return f"Done: {action} on {name}." + (f" ({why})" if why else "")
            return f"Not done on {name}: {why}."
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
    if action in SEEK_ACTIONS:
        return _move(target, app, action, offset)
    r = _gdbus("--dest", target, "--object-path", _PATH, "--method", f"{_PLAYER}.{ACTIONS[action]}")
    if r.returncode != 0:
        return f"{app} refused '{action}': {r.stderr.strip()[:160]}"
    return f"Done: {action} in {app}."


SKILLS = [Skill(name="media_control", schema=_SCHEMA, run=_run)]
