"""While you were away: what Chronoa did while the screen was locked.

Rules, triggers, background mode and long jobs keep running when the person
steps away - and coming back to a desktop that changed with nothing saying how
is exactly the "who did this?" a person cannot answer. So on unlock, one
notification sums up the actions in Chronoa's own log since the lock: how many,
which, and which failed. Nothing happened, nothing is said.

Lock state comes from the session's `org.gnome.ScreenSaver.ActiveChanged`
(GNOME, and the freedesktop ScreenSaver on Plasma); the actions from the same
log the Activity panel reads (`tool_tracking`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

_STATE = {"locked_at": None, "subscription": 0, "bus": None}


def summary(records: List[dict], since: datetime) -> str:
    """One sentence for the actions in `records` at or after `since`; "" if none."""
    from shani_chronoa.desktop_cue import for_tool
    recent = []
    for record in records:
        try:
            when = datetime.fromisoformat(str(record.get("timestamp")))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= since:
            recent.append(record)
    if not recent:
        return ""
    failed = [r for r in recent if r.get("verdict") == "failed"
              or str(r.get("result") or "").startswith(("ERROR", "Not run"))]
    names = []
    for record in recent:
        words = for_tool(str(record.get("tool_name") or ""), record.get("args") or {})
        words = words.removeprefix("Chronoa: ")
        if words not in names:
            names.append(words)
    listed = "; ".join(names[:4]) + (f"; and {len(names) - 4} more" if len(names) > 4 else "")
    head = f"While you were away, Chronoa did {len(recent)} thing{'s' if len(recent) != 1 else ''}"
    tail = f" - {len(failed)} did not work." if failed else "."
    return f"{head}: {listed}{tail}"


def _records() -> List[dict]:
    try:
        from shani_chronoa import tool_tracking
        from shani_chronoa.gui.surfaces import activity
        return activity._records(tool_tracking.ToolTracker())  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return []


def _notify(text: str) -> None:
    from gi.repository import Gio, GLib
    bus = _STATE["bus"]
    try:
        bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications", "Notify",
                 GLib.Variant("(susssasa{sv}i)", ("Shani Chronoa", 0, "shani-chronoa",
                                                   "While you were away", text, [],
                                                   {"desktop-entry": GLib.Variant("s", "shani-chronoa")},
                                                   -1)),
                 None, Gio.DBusCallFlags.NONE, 5000, None, None, None)
    except Exception:  # noqa: BLE001
        logger.debug("could not post the away summary", exc_info=True)


def _on_active_changed(_conn, _sender, _path, _iface, _signal, params) -> None:
    active = bool(params.unpack()[0])
    if active:
        _STATE["locked_at"] = datetime.now(timezone.utc)
        return
    since: Optional[datetime] = _STATE["locked_at"]
    _STATE["locked_at"] = None
    if since is None:
        return
    text = summary(_records(), since)
    if text:
        _notify(text)


def start() -> bool:
    """Watch the screen lock on the session bus; False when there is no bus."""
    if _STATE["subscription"]:
        return True
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        _STATE["bus"] = bus
        _STATE["subscription"] = bus.signal_subscribe(
            None, "org.gnome.ScreenSaver", "ActiveChanged", "/org/gnome/ScreenSaver",
            None, Gio.DBusSignalFlags.NONE, _on_active_changed)
        return True
    except Exception:  # noqa: BLE001 - no session bus: nothing to watch
        return False
