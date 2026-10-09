"""Files sent to this computer over Bluetooth (a phone's Share > Bluetooth).

bluez's obexd receives Object Push, and asks a registered agent
(`org.bluez.obex.Agent1.AuthorizePush`) what to do with each file. This module
is that agent: it shows who is sending what with **Accept** / **Decline**, and
only an Accept stores the file - in ~/Downloads, under a name with no path in it
and never over an existing file. Silence, a closed notification and the
notification timing out all decline. A notification follows when the file has
arrived (or failed).

Gated on `phone-control-enabled`: the agent is registered with obexd only while
that switch is on (re-checked every few seconds), and the switch is checked
again per file. obexd has **one** agent slot, so holding it while switched off
rejected every incoming file and kept the desktop's own receiver out. Runs on
its own thread, main context and D-Bus connection, like `incoming_call.py`.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

OBEX = "org.bluez.obex"
AGENT_PATH = "/dev/shani/chronoa/obex_agent"
AGENT_IFACE = "org.bluez.obex.Agent1"
NOTIFY = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
KEY = "phone-control-enabled"
#: How often the switch is re-read to take or release obexd's agent slot.
SWITCH_POLL_SECONDS = 5
DECIDE_SECONDS = 60
XML = f"""<node><interface name="{AGENT_IFACE}">
  <method name="Release"/>
  <method name="AuthorizePush"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Cancel"/>
</interface></node>"""


def safe_name(name: str) -> str:
    """The file's own name with any path, control or direction character removed."""
    from shani_chronoa.phone import clean_text
    base = os.path.basename(clean_text(name or "").replace("\\", "/")).strip().lstrip(".")
    base = re.sub(r"[\x00-\x1f/]", "", base)
    return base[:180] or "received-file"


def free_path(folder: str, name: str) -> str:
    """`folder/name`, or `name (2)` and so on - never an existing file."""
    stem, ext = os.path.splitext(name)
    candidate, n = os.path.join(folder, name), 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return candidate


def obex_root() -> str:
    """obexd's own folder. It refuses to write anywhere else - measured:
    `open(~/Downloads/x): Operation not permitted`, then OBEX Forbidden to the
    phone - so a file is received here and moved once it is complete."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, "obexd")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def downloads() -> str:
    try:
        from gi.repository import GLib
        path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    except Exception:  # noqa: BLE001
        path = None
    path = path or os.path.expanduser("~/Downloads")
    os.makedirs(path, exist_ok=True)
    return path


class ObexReceiver:
    def __init__(self, config: Any, bus_factory=None):
        self.config = config
        self._bus_factory = bus_factory
        self._thread: Optional[threading.Thread] = None
        self._loop = None
        self.bus = None
        self.waiting: "dict[int, tuple[Any, str, str]]" = {}   # notification id -> (invocation, path, transfer)
        self.transfers: "dict[str, str]" = {}                    # transfer path -> saved file
        #: Whether obexd currently has this agent - only while the switch is on.
        self.registered = False

    def start(self) -> None:
        if self._thread is None:
            ready = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(ready,), name="obex-receive", daemon=True)
            self._thread.start()
            ready.wait(5)

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
        self._ctx = ctx
        ctx.push_thread_default()
        try:
            self.bus = (self._bus_factory() if self._bus_factory else Gio.DBusConnection.new_for_address_sync(
                Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None))
            node = Gio.DBusNodeInfo.new_for_xml(XML)
            self._reg = self.bus.register_object(AGENT_PATH, node.interfaces[0], self._on_method)
            self._sync_agent()
            self._subs = [
                self.bus.signal_subscribe(NOTIFY, NOTIFY, "ActionInvoked", NOTIFY_PATH, None, 0, self._on_action),
                self.bus.signal_subscribe(NOTIFY, NOTIFY, "NotificationClosed", NOTIFY_PATH, None, 0,
                                          self._on_closed),
                self.bus.signal_subscribe(OBEX, "org.freedesktop.DBus.Properties", "PropertiesChanged", None,
                                          "org.bluez.obex.Transfer1", 0, self._on_transfer),
            ]
            self._loop = GLib.MainLoop.new(ctx, False)
            self._every(SWITCH_POLL_SECONDS, lambda: (self._sync_agent(), True)[1])
        except Exception:  # noqa: BLE001 - no obexd: nothing to receive, not a crash
            logger.warning("Bluetooth file receiving not started", exc_info=True)
            ready.set()
            ctx.pop_thread_default()
            return
        ready.set()
        try:
            self._loop.run()
        finally:
            self._set_agent(False)
            for inv, _p, _t in self.waiting.values():
                inv.return_dbus_error("org.bluez.obex.Error.Rejected", "Chronoa stopped")
            ctx.pop_thread_default()

    def _every(self, seconds: int, callback) -> None:
        """A timer on THIS thread's context. `GLib.timeout_add_seconds` attaches
        to the global default context - the GTK thread in the app, and nothing at
        all in a test - so the switch poll never ran (caught by a test) and the
        decline timeout ran on the wrong thread."""
        from gi.repository import GLib
        source = GLib.timeout_source_new_seconds(seconds)
        source.set_callback(lambda *_a: callback())
        source.attach(self._ctx)

    def _allowed(self) -> bool:
        try:
            return bool(self.config.get_bool(KEY, False))
        except Exception:  # noqa: BLE001
            return False

    def _sync_agent(self) -> None:
        """Hold obexd's agent slot exactly while the switch is on."""
        self._set_agent(self._allowed())

    def _set_agent(self, want: bool) -> None:
        if want == self.registered:
            return
        from gi.repository import GLib
        method = "RegisterAgent" if want else "UnregisterAgent"
        try:
            self.bus.call_sync(OBEX, "/org/bluez/obex", "org.bluez.obex.AgentManager1", method,
                               GLib.Variant("(o)", (AGENT_PATH,)), None, 0, 5000, None)
            self.registered = want
            logger.info("Bluetooth file receiving %s", "on" if want else "off")
        except Exception as exc:  # noqa: BLE001 - obexd absent or slot taken: try again next poll
            logger.debug("obex agent %s failed: %s", method, exc)
            if not want:
                self.registered = False

    # --- obexd asks ------------------------------------------------------------

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method != "AuthorizePush":
            invocation.return_value(None)
            return
        transfer = params.unpack()[0]
        if not self._allowed():
            invocation.return_dbus_error("org.bluez.obex.Error.Rejected", f"'{KEY}' is off")
            return
        props = self._props(transfer, "org.bluez.obex.Transfer1")
        name = safe_name(str(props.get("Name") or ""))
        size = int(props.get("Size") or 0)
        sender = self._sender_name(str(props.get("Session") or ""))
        target = free_path(obex_root(), name)      # staged; moved to Downloads when complete
        nid = self._notify("Receive a file?", f"{sender} wants to send {name}"
                           + (f" ({size / 1024:.0f} KB)" if size else ""),
                           ["accept", "Accept", "decline", "Decline"])
        if not nid:
            invocation.return_dbus_error("org.bluez.obex.Error.Rejected", "nobody could be asked")
            return
        self.waiting[nid] = (invocation, target, transfer)
        self._every(DECIDE_SECONDS, lambda n=nid: (self._answer(n, False), False)[1])

    def _answer(self, nid: int, accept: bool) -> None:
        item = self.waiting.pop(nid, None)
        if item is None:
            return
        invocation, target, transfer = item
        if accept:
            self.transfers[transfer] = target
            from gi.repository import GLib
            invocation.return_value(GLib.Variant("(s)", (target,)))
        else:
            invocation.return_dbus_error("org.bluez.obex.Error.Rejected", "declined")
        self._close(nid)

    def _on_action(self, _c, _s, _p, _i, _sig, params) -> None:
        nid, action = params.unpack()
        if nid in self.waiting:
            self._answer(nid, action == "accept")

    def _on_closed(self, _c, _s, _p, _i, _sig, params) -> None:
        nid = params.unpack()[0]
        if nid in self.waiting:
            self._answer(nid, False)

    def _on_transfer(self, _c, _s, path, _i, _sig, params) -> None:
        _iface, changed, _gone = params.unpack()
        status = str(dict(changed).get("Status") or "")
        target = self.transfers.get(str(path))
        if target and status in ("complete", "error"):
            self.transfers.pop(str(path), None)
            if status == "complete":
                saved = self.deliver(target)
                self._notify("File received", f"Saved {os.path.basename(saved)} in {os.path.dirname(saved)}", [])
            else:
                try:
                    os.unlink(target)                  # a partial file is not kept
                except OSError:
                    pass
                self._notify("File not received", f"{os.path.basename(target)} did not arrive completely", [])

    @staticmethod
    def deliver(staged: str) -> str:
        """Move a completed file from obexd's folder into Downloads; returns where it went."""
        import shutil
        final = free_path(downloads(), os.path.basename(staged))
        shutil.move(staged, final)
        return final

    # --- helpers ---------------------------------------------------------------

    def _props(self, path: str, iface: str) -> dict:
        from gi.repository import GLib
        try:
            return dict(self.bus.call_sync(OBEX, path, "org.freedesktop.DBus.Properties", "GetAll",
                                           GLib.Variant("(s)", (iface,)), None, 0, 3000, None).unpack()[0])
        except Exception:  # noqa: BLE001
            return {}

    def _sender_name(self, session: str) -> str:
        from shani_chronoa.phone import clean_text
        address = str(self._props(session, "org.bluez.obex.Session1").get("Destination") or "") if session else ""
        if address:
            try:
                from shani_chronoa.phone_bluez import _run
                info = _run(["bluetoothctl", "info", address]).stdout or ""
                m = re.search(r"^\s*Alias:\s*(.+)$", info, re.M)
                if m:
                    return clean_text(m.group(1).strip())
            except Exception:  # noqa: BLE001
                pass
        return address or "A Bluetooth device"

    def _notify(self, title: str, body: str, actions: list) -> int:
        from gi.repository import GLib
        hints = {"urgency": GLib.Variant("y", 2 if actions else 1), "category": GLib.Variant("s", "transfer"),
                 "desktop-entry": GLib.Variant("s", "dev.shani.chronoa")}
        if actions:
            hints["resident"] = GLib.Variant("b", True)
        try:
            return int(self.bus.call_sync(
                NOTIFY, NOTIFY_PATH, NOTIFY, "Notify",
                GLib.Variant("(susssasa{sv}i)", ("Shani Chronoa", 0, "document-send-symbolic", title, body,
                                                  actions, hints, 0 if actions else 8000)),
                GLib.VariantType.new("(u)"), 0, 5000, None).unpack()[0])
        except Exception:  # noqa: BLE001
            logger.warning("file receive: could not notify", exc_info=True)
            return 0

    def _close(self, nid: int) -> None:
        from gi.repository import GLib
        try:
            self.bus.call_sync(NOTIFY, NOTIFY_PATH, NOTIFY, "CloseNotification", GLib.Variant("(u)", (nid,)),
                               None, 0, 2000, None)
        except Exception:  # noqa: BLE001
            pass
