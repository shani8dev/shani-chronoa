"""A text arriving on the paired phone, shown here the moment it lands.

MAP notifications (the Message Notification Service) over plain Bluetooth, no
app on the phone. obexd's MAP client registers for them by itself when a session
opens (`set_notification_registration(map, true)` in obexd/client/map.c) and
turns each NewMessage event into a new `org.bluez.obex.Message1` object
(`map_handle_new_message` -> `map_msg_create`), which appears as InterfacesAdded
on obexd's ObjectManager. So this holds one MAP session open and listens for
that, then lists the inbox to get who it is from and what it says.

Gated on `phone-control-enabled` and `phone-messages-read-enabled` - the second
because the notification shows other people's words - checked per message.
Text is screened by `phone.clean_text` like every other message display.

The session is held on its own connection and thread (obexd drops a session
whose D-Bus client goes away), and re-opened after the phone leaves and returns.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

OBEX = "org.bluez.obex"
MESSAGE_IFACE = "org.bluez.obex.Message1"
KEYS = ("phone-control-enabled", "phone-messages-read-enabled")
RETRY_SECONDS = 60


class MessageWatcher:
    def __init__(self, config: Any, notify: Optional[Callable[[str, str], None]] = None):
        self.config = config
        self.notify = notify or _desktop_notify
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.seen: set = set()
        self.shown: "list[tuple[str, str]]" = []

    def _allowed(self) -> bool:
        try:
            return all(self.config.get_bool(k, False) for k in KEYS)
        except Exception:  # noqa: BLE001
            return False

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="map-notify", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._allowed():
                try:
                    from shani_chronoa import phone_bluez
                    # Connected or not: opening the MAP session connects to a
                    # phone in range, so requiring a live link first only waited.
                    phones = [a for a, _n, _up in phone_bluez.phones()]
                    if phones:
                        self.hold(phones[0])
                except Exception:  # noqa: BLE001 - out of range, permission off: try later
                    logger.info("message watcher: not listening this time", exc_info=True)
            self._stop.wait(RETRY_SECONDS)

    def hold(self, address: str) -> None:
        """One MAP session, held until the phone goes away or the switches go off."""
        from gi.repository import Gio, GLib
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        bus = Gio.DBusConnection.new_for_address_sync(
            Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        session = ""
        state = {"gone": False, "new": []}

        def on_added(_c, _s, _p, _i, _sig, params):
            path, ifaces = params.unpack()
            if MESSAGE_IFACE in ifaces and str(path).startswith(session + "/"):
                folder = str(ifaces[MESSAGE_IFACE].get("Folder", ""))
                if "inbox" in folder.lower() or not folder:
                    state["new"].append(str(path))

        def on_removed(_c, _s, _p, _i, _sig, params):
            if str(params.unpack()[0]) == session:
                state["gone"] = True                       # the phone ended the session

        subs = [bus.signal_subscribe(OBEX, "org.freedesktop.DBus.ObjectManager", "InterfacesAdded", "/", None,
                                     Gio.DBusSignalFlags.NONE, on_added),
                bus.signal_subscribe(OBEX, "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved", "/", None,
                                     Gio.DBusSignalFlags.NONE, on_removed)]
        try:
            session = bus.call_sync(OBEX, "/org/bluez/obex", "org.bluez.obex.Client1", "CreateSession",
                                    GLib.Variant("(sa{sv})", (address, {"Target": GLib.Variant("s", "map")})),
                                    None, 0, 30000, None).unpack()[0]
            bus.call_sync(OBEX, session, "org.bluez.obex.MessageAccess1", "SetFolder",
                          GLib.Variant("(s)", ("telecom/msg",)), None, 0, 10000, None)
            self.seen |= set(self._inbox(bus, session).keys())       # what is already there is not new
            logger.info("message watcher: listening for new messages on %s", address)
            while not self._stop.is_set() and self._allowed() and not state["gone"]:
                while ctx.iteration(False):
                    pass
                if state["new"]:
                    state["new"].clear()
                    time.sleep(1.0)                                     # let the phone finish storing it
                    self._announce(bus, session)
                time.sleep(0.2)
        finally:
            for s in subs:
                bus.signal_unsubscribe(s)
            if session:
                try:
                    bus.call_sync(OBEX, "/org/bluez/obex", "org.bluez.obex.Client1", "RemoveSession",
                                  GLib.Variant("(o)", (session,)), None, 0, 5000, None)
                except Exception:  # noqa: BLE001
                    pass
            ctx.pop_thread_default()

    def _inbox(self, bus, session: str) -> dict:
        from gi.repository import GLib
        return bus.call_sync(OBEX, session, "org.bluez.obex.MessageAccess1", "ListMessages",
                             GLib.Variant("(sa{sv})", ("inbox", {"MaxCount": GLib.Variant("q", 10)})),
                             None, 0, 30000, None).unpack()[0]

    def _announce(self, bus, session: str) -> None:
        from shani_chronoa import phone as ph
        for path, f in self._inbox(bus, session).items():
            if path in self.seen:
                continue
            self.seen.add(path)
            if not self._allowed():
                continue
            who = ph.clean_text(str(f.get("SenderName") or f.get("Sender") or f.get("SenderAddress") or "Someone"))
            text = ph.clean_text(str(f.get("Subject") or "")).strip() or "(no text)"
            self.shown.append((who, text))
            self.notify(f"Message from {who}", text[:200])


def _desktop_notify(title: str, body: str) -> None:
    import subprocess
    try:
        subprocess.run(["notify-send", "--app-name=Shani Chronoa", "--icon=mail-message-new-symbolic",
                        title, body], capture_output=True, timeout=5, check=False)
    except OSError:
        pass
