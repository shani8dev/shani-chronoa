"""A meeting just started: an app began using the microphone.

When a browser or a meeting app (Zoom, Teams, Meet in a browser, Slack,
Discord, Webex) starts capturing audio, Chronoa offers - once, as a
notification - to take notes, and the button opens the Meetings page. It never
records on its own: the offer is all it does, and only when the person has
already turned on `meeting-enabled` ("record meetings").

Read from PipeWire (`pw-dump`: every `Stream/Input/Audio` node and the app
behind it), every `POLL_SECONDS` while the switch is on. Chronoa's own capture
is never counted.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Iterable, Set, Tuple

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
CONSENT_KEY = "meeting-enabled"

#: Apps whose microphone use is most likely a call or a meeting.
MEETING_APPS = re.compile(r"firefox|chrom|brave|edge|vivaldi|opera|epiphany|zoom|teams|slack|"
                          r"discord|webex|skype|jitsi|signal|telegram|whatsapp|element", re.I)
#: Capture that is Chronoa's own (whisper, its recorder, the app itself).
OWN = re.compile(r"chronoa|whisper|pw-record|python3?$|parakeet", re.I)

_STATE = {"seen": set(), "timer": 0, "app": None, "nid": 0}


def capturing() -> Set[Tuple[str, str]]:
    """(app name, binary) of everything capturing audio now; empty when unknown."""
    if not shutil.which("pw-dump"):
        return set()
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
        nodes = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()
    found = set()
    for node in nodes:
        props = (node.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Stream/Input/Audio":
            found.add((str(props.get("application.name") or ""),
                       str(props.get("application.process.binary") or "")))
    return found


def meetings_among(streams: Iterable[Tuple[str, str]]) -> Set[str]:
    """The meeting-like apps in `streams`, by the name to show; Chronoa's own left out."""
    out = set()
    for name, binary in streams:
        if OWN.search(name) or OWN.search(binary):
            continue
        if MEETING_APPS.search(name) or MEETING_APPS.search(binary):
            out.add(name or binary)
    return out


def _tick() -> bool:
    app = _STATE["app"]
    config = getattr(app, "config", None)
    try:
        allowed = bool(config.get_bool(CONSENT_KEY, False)) if config is not None else False
    except Exception:  # noqa: BLE001
        allowed = False
    if not allowed:
        _STATE["seen"] = set()
        return True
    now = meetings_among(capturing())
    new = now - _STATE["seen"]
    _STATE["seen"] = now
    if new:
        _offer(sorted(new)[0])
    return True


def _offer(who: str) -> None:
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", (
                "Shani Chronoa", 0, "shani-chronoa", f"{who} is using the microphone",
                "A meeting? Chronoa can take notes - nothing is recorded unless you start it.",
                ["notes", "Take notes", "dismiss", "Not now"],
                {"desktop-entry": GLib.Variant("s", "shani-chronoa")}, -1)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        _STATE["nid"] = int(reply.unpack()[0])
    except Exception:  # noqa: BLE001
        logger.debug("could not offer meeting notes", exc_info=True)


def _on_action(_conn, _sender, _path, _iface, _signal, params) -> None:
    from gi.repository import GLib
    nid, key = params.unpack()
    if nid == _STATE["nid"] and key == "notes" and _STATE["app"] is not None:
        _STATE["app"].activate_action("show-page", GLib.Variant("s", "main:meetings"))


def start(app) -> bool:
    """Begin watching (a cheap poll that does nothing while the switch is off)."""
    if _STATE["timer"]:
        return True
    try:
        from gi.repository import Gio, GLib
        _STATE["app"] = app
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.signal_subscribe("org.freedesktop.Notifications", "org.freedesktop.Notifications",
                             "ActionInvoked", "/org/freedesktop/Notifications", None,
                             Gio.DBusSignalFlags.NONE, _on_action)
        _STATE["timer"] = GLib.timeout_add_seconds(POLL_SECONDS, _tick)
        return True
    except Exception:  # noqa: BLE001
        return False
