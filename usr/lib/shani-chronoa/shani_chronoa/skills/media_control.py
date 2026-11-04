"""Skill: play, pause, skip, or say what is playing - in any media player.

Every Linux media player that shows up in the desktop's media controls
(Spotify, Firefox, Rhythmbox, VLC, mpv with mpris, ...) speaks MPRIS over the
session bus. Talking to it is shani-link's (shani_link/mpris.py - the same
module the Shani Link app, the watch's music buttons and Bluetooth remotes
use); this skill is the wording. With several players, the one playing is
preferred. With none, a phone's player over Bluetooth (AVRCP, `phone_bluez`).

`restart` and `seek` use the Player interface's own SetPosition and Seek
methods, after checking the player's CanSeek property: a live stream or a
player that does not implement seeking says so rather than "done". The
position is read back afterwards and reported, so a seek the player ignored
is not described as one that happened.
"""

from shani_chronoa import _link
from shani_chronoa.skills import Skill

_engine = _link.module("mpris")

ACTIONS = dict(_engine.METHODS)

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


def _now_playing(name: str) -> str:
    title, artist = _engine.title_artist(name)
    return (title + (f" by {artist}" if artist else "")) or "something untitled"


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
    # `sysfs.finite`, the one implementation, rather than this check written out
    # again: `float("nan")` and `float("inf")` both succeed, and an infinite
    # seek offset would move the player somewhere no track has ever been.
    from shani_chronoa import sysfs

    if not sysfs.finite(value):
        return None, f"Could not use {raw!r} as a number of seconds."
    if value == 0:
        return None, "Seeking by 0 seconds would not move anything."
    if abs(value) > MAX_SEEK_SECONDS:
        return None, f"{value:g} seconds is more than {MAX_SEEK_SECONDS // 3600} hours; not seeking that far."
    return int(value * 1_000_000), ""


def _move(target: str, app: str, action: str, offset: int) -> str:
    if not _engine.can_seek(target):
        return f"{app} does not allow seeking in what it is playing, so nothing was moved."
    before = _engine.position(target)
    done, why = _engine.restart(target) if action == "restart" else _engine.seek(target, offset)
    if not done:
        return f"{app} refused '{action}': {why[:160]}"
    after = _engine.position(target)
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


def _phone(action: str) -> str:
    """A phone's player over Bluetooth, straight from bluez (AVRCP). The route
    through `mpris-proxy` was measured crashing (SIGSEGV 40 s after start), and
    then the phone's music simply vanished from here."""
    from shani_chronoa import phone_bluez
    phone_players = phone_bluez.avrcp_players()
    if not phone_players:
        return "No media player is running."
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
    target = _engine.current()
    if target is None:
        return _phone(action)
    app = _engine.identity(target)
    if action == "status":
        state = _engine.status(target)
        return f"{app} is {state.lower()}" + (f": {_now_playing(target)}." if state != "Stopped" else ".")
    if action in SEEK_ACTIONS:
        return _move(target, app, action, offset)
    done, why = _engine.call(target, ACTIONS[action])
    if not done:
        return f"{app} refused '{action}': {why[:160]}"
    return f"Done: {action} in {app}."


SKILLS = [Skill(name="media_control", schema=_SCHEMA, run=_run)]
