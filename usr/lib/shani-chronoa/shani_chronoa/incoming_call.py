"""An incoming call on the paired phone, shown the way the phone shows it.

When a phone connected over Bluetooth hands-free rings, PipeWire's telephony
service (`org.pipewire.Telephony`, see `skills/bluetooth_call.py`) announces a
new call object. This watches for that and posts a desktop notification with the
caller - named from the phone's contacts when Chronoa may read them - and
**Answer** / **Decline** buttons, closing it again when the call is answered
anywhere, declined, or the caller gives up. That last part is why it talks to
`org.freedesktop.Notifications` directly instead of `notify-send --wait` (the
route `approvals.py` takes): a notification for a call that has already ended
must disappear, and only the D-Bus API can close one.

`category=call.incoming` and critical urgency are the freedesktop hints GNOME
Shell and Plasma both use to keep a call notification on screen until it is
dealt with, the desktop's version of the phone's full-screen call card.

Gated on `bluetooth-call-enabled`: answering and declining act on the phone, the
same switch `bluetooth_call` refuses behind. Checked at every call, so turning it
off takes effect at once. Contacts are looked up only with
`phone-messages-read-enabled` on; otherwise the number is shown as the phone sent
it.

Runs on its own thread with its own `GLib.MainContext` and D-Bus connection, so
it works with every window closed and never waits on the GTK loop.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

TELEPHONY = "org.pipewire.Telephony"
TELEPHONY_ROOT = "/org/pipewire/Telephony"
CALL_IFACE = "org.pipewire.Telephony.Call1"
NOTIFY_BUS = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
CALL_KEY = "bluetooth-call-enabled"
WATCH_KEY = "bluetooth-gatt-enabled"
READ_KEY = "phone-messages-read-enabled"
RINGING = ("incoming", "waiting")
#: How long a cached contact book is trusted before it is pulled again.
BOOK_SECONDS = 3600


def caller_label(props: dict, book: "list[tuple[str, tuple[str, ...]]]") -> "tuple[str, str]":
    """(title, body) for the notification: the contact's name when known, else the number."""
    from shani_chronoa import phone as ph
    number = str(props.get("LineIdentification") or props.get("IncomingLine") or "").strip()
    named = str(props.get("Name") or "").strip()
    name = ph.name_for(number, book) if number and book else ""
    who = name or named or number or "Unknown caller"
    detail = number if (name or named) and number else ""
    waiting = str(props.get("State") or "") == "waiting"
    return (("Call waiting" if waiting else "Incoming call") + f": {who}", detail)


class IncomingCallWatcher:
    """Start with `start()`, stop with `stop()`; everything else is internal."""

    def __init__(self, config: Any, bus_factory: Optional[Callable[[], Any]] = None):
        self.config = config
        self._bus_factory = bus_factory
        self._thread: Optional[threading.Thread] = None
        self._loop = None
        self._bus = None
        self._subs: list = []
        self.shown: "dict[str, int]" = {}     # call path -> notification id
        self._book: "list[tuple[str, tuple[str, ...]]]" = []
        self._book_at = 0.0

    # --- life cycle ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="incoming-call",
                                        daemon=True)
        self._thread.start()
        ready.wait(5)
        self.refresh_contacts()

    def stop(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.get_context().invoke_full(0, lambda: (loop.quit(), False)[1])
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self, ready: threading.Event) -> None:
        from gi.repository import Gio, GLib
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self._bus = (self._bus_factory() if self._bus_factory else
                         Gio.DBusConnection.new_for_address_sync(
                             Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
                             Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None))
            sub = self._bus.signal_subscribe
            flags = Gio.DBusSignalFlags.NONE
            self._subs = [
                sub(None, "org.ofono.VoiceCallManager", "CallAdded", None, None, flags, self._on_added),
                sub(None, "org.ofono.VoiceCallManager", "CallRemoved", None, None, flags, self._on_removed),
                sub(None, "org.freedesktop.DBus.Properties", "PropertiesChanged", None, CALL_IFACE,
                    flags, self._on_changed),
                sub(NOTIFY_BUS, NOTIFY_BUS, "ActionInvoked", NOTIFY_PATH, None, flags, self._on_action),
                sub(NOTIFY_BUS, NOTIFY_BUS, "NotificationClosed", NOTIFY_PATH, None, flags,
                    self._on_closed),
            ]
            self._loop = GLib.MainLoop.new(ctx, False)
        except Exception:  # noqa: BLE001 - no session bus is no watcher, not a crash
            logger.warning("incoming-call watcher could not start", exc_info=True)
            ready.set()
            ctx.pop_thread_default()
            return
        ready.set()
        try:
            self._loop.run()
        finally:
            for s in self._subs:
                self._bus.signal_unsubscribe(s)
            for nid in list(self.shown.values()):
                self._close(nid)
            ctx.pop_thread_default()

    # --- what the phone says ---------------------------------------------------

    def _allowed(self, key: str) -> bool:
        try:
            return bool(self.config.get_bool(key, False))
        except Exception:  # noqa: BLE001 - fails closed
            return False

    def _on_added(self, _conn, _sender, path, _iface, _signal, params) -> None:
        if not str(path).startswith(TELEPHONY_ROOT):
            return
        call, props = params.unpack()
        self.ringing(str(call), dict(props))

    def _on_changed(self, _conn, _sender, path, _iface, _signal, params) -> None:
        if not str(path).startswith(TELEPHONY_ROOT):
            return
        _iface_name, changed, _gone = params.unpack()
        state = str(dict(changed).get("State") or "")
        if state and state not in RINGING:
            self.ended(str(path))       # answered (here or on the phone) or hung up

    def _on_removed(self, _conn, _sender, path, _iface, _signal, params) -> None:
        if str(path).startswith(TELEPHONY_ROOT):
            self.ended(str(params.unpack()[0]))

    def ringing(self, call: str, props: dict) -> None:
        if str(props.get("State") or "") not in RINGING or call in self.shown:
            return
        if not self._allowed(CALL_KEY):
            logger.info("incoming call not shown: '%s' is off", CALL_KEY)
            return
        # Only the cached book: pulling contacts over Bluetooth takes ~20 s, and a
        # call notification that late is no notification. A stale book is
        # refreshed behind the call for the next one.
        title, body = caller_label(props, self._book)
        self.refresh_contacts()
        nid = self._notify(title, body)
        if nid:
            self.shown[call] = nid
        self._to_watch(lambda m: m.call_payload(title.split(": ", 1)[-1]))

    def ended(self, call: str) -> None:
        nid = self.shown.pop(call, None)
        if nid:
            self._close(nid)
            self._to_watch(lambda m: m.CALL_OFF_HOOK)

    def _to_watch(self, payload_for) -> None:
        """Ring (or stop ringing) a paired MoYoung watch too, off this thread.

        A watch takes seconds to connect, and a call notification must not wait
        for it. Needs the watch's own switch; any failure is only logged - the
        desktop card is the one that has to work.
        """
        if not self._allowed(WATCH_KEY):
            return

        def send():
            try:
                from shani_chronoa import moyoung
                from shani_chronoa.skills import watch as watch_skill
                picked = watch_skill._pick("")
                if isinstance(picked, tuple):
                    moyoung.tell_watch(picked[0], payload_for(moyoung))
            except Exception:  # noqa: BLE001
                logger.info("incoming call: the watch was not told", exc_info=True)
        threading.Thread(target=send, name="incoming-call-watch", daemon=True).start()

    def refresh_contacts(self) -> None:
        """Pull the book in the background when it is missing or stale."""
        if not self._allowed(READ_KEY) or (self._book and time.monotonic() - self._book_at < BOOK_SECONDS):
            return
        threading.Thread(target=self._contacts, name="incoming-call-contacts", daemon=True).start()

    def _contacts(self) -> "list[tuple[str, tuple[str, ...]]]":
        try:
            from shani_chronoa import phone as ph
            dev = next((d for d in ph.devices() if d.reachable), None)
            self._book = ph.contacts(dev) if dev is not None else []
            self._book_at = time.monotonic()
        except Exception:  # noqa: BLE001 - a number with no name still rings
            logger.info("incoming call: contacts not read", exc_info=True)
        return self._book

    # --- the notification --------------------------------------------------------

    def _notify(self, title: str, body: str) -> int:
        from gi.repository import GLib
        hints = {"urgency": GLib.Variant("y", 2), "category": GLib.Variant("s", "call.incoming"),
                 "resident": GLib.Variant("b", True),
                 "desktop-entry": GLib.Variant("s", "dev.shani.chronoa")}
        try:
            reply = self._bus.call_sync(
                NOTIFY_BUS, NOTIFY_PATH, NOTIFY_BUS, "Notify",
                GLib.Variant("(susssasa{sv}i)", ("Shani Chronoa", 0, "call-start-symbolic", title, body,
                                                  ["answer", "Answer", "decline", "Decline"], hints, 0)),
                GLib.VariantType.new("(u)"), 0, 5000, None)
            return int(reply.unpack()[0])
        except Exception:  # noqa: BLE001 - no notification server
            logger.warning("incoming call: could not post the notification", exc_info=True)
            return 0

    def _close(self, nid: int) -> None:
        from gi.repository import GLib
        try:
            self._bus.call_sync(NOTIFY_BUS, NOTIFY_PATH, NOTIFY_BUS, "CloseNotification",
                                GLib.Variant("(u)", (nid,)), None, 0, 2000, None)
        except Exception:  # noqa: BLE001 - already gone
            pass

    def _on_action(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        nid, action = params.unpack()
        call = next((c for c, n in self.shown.items() if n == nid), None)
        if call is None or action not in ("answer", "decline"):
            return
        self.act(call, action)

    def act(self, call: str, action: str) -> None:
        """Answer or decline `call` on the phone - the notification's two buttons."""
        if not self._allowed(CALL_KEY):
            return
        method = "Answer" if action == "answer" else "Hangup"
        try:
            self._bus.call_sync(TELEPHONY, call, CALL_IFACE, method, None, None, 0, 5000, None)
        except Exception:  # noqa: BLE001 - the caller may have just hung up
            logger.info("incoming call: %s failed", method, exc_info=True)
        self.ended(call)

    def _on_closed(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        nid = params.unpack()[0]
        for call, n in list(self.shown.items()):
            if n == nid:
                self.shown.pop(call, None)
