"""This computer's music, controllable from Bluetooth remotes (AVRCP target).

bluez's `Media1.RegisterPlayer` (man org.bluez.Media(5)) takes an object that
implements `org.mpris.MediaPlayer2.Player` and offers it to every connected
AVRCP controller - a headset's buttons, a watch's music screen, a phone or car
that controls a source. This registers one such player that stands for whatever
is playing here: its methods run `media_control` (directly, never through the
tool dispatch - polling that every few seconds tripped the repeat guard and then
refused media_control to everyone, measured with the watch companion), and its
PlaybackStatus/Metadata follow the real player.

Registered on its own connection and thread; bluez unregisters it when the
connection goes away.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

PATH = "/dev/shani/chronoa/avrcp_player"
IFACE = "org.mpris.MediaPlayer2.Player"
XML = f"""<node><interface name="{IFACE}">
  <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/>
  <method name="Stop"/><method name="Play"/>
  <property name="PlaybackStatus" type="s" access="read"/><property name="Metadata" type="a{{sv}}" access="read"/>
  <property name="CanGoNext" type="b" access="read"/><property name="CanGoPrevious" type="b" access="read"/>
  <property name="CanPlay" type="b" access="read"/><property name="CanPause" type="b" access="read"/>
  <property name="CanControl" type="b" access="read"/><property name="CanSeek" type="b" access="read"/>
  <property name="Rate" type="d" access="read"/><property name="Position" type="x" access="read"/>
  </interface></node>"""
ACTIONS = {"Next": "next", "Previous": "previous", "Pause": "pause", "PlayPause": "toggle",
           "Stop": "stop", "Play": "play"}
POLL_SECONDS = 3.0


def parse_status(text: str) -> "tuple[str, str]":
    """('Playing'|'Paused'|'Stopped', 'Title by Artist') from media_control's own status sentence."""
    t = text.split(" (unverified")[0].strip()
    state = "Playing" if " is playing" in t else "Paused" if " is paused" in t else "Stopped"
    title = t.split(": ", 1)[1].rstrip(".") if ": " in t else ""
    return state, title


class AvrcpTarget:
    def __init__(self, run_action=None):
        self.run_action = run_action or _media_control
        self.state, self.title = "Stopped", ""
        self.bus = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self.error = ""
        self.pressed: "list[str]" = []

    def _props(self) -> dict:
        from gi.repository import GLib
        title, artist = (self.title.split(" by ", 1) + [""])[:2] if self.title else ("", "")
        meta = {"mpris:trackid": GLib.Variant("o", "/dev/shani/chronoa/track"),
                "xesam:title": GLib.Variant("s", title or "Shani Chronoa")}
        if artist:
            meta["xesam:artist"] = GLib.Variant("as", [artist])
        return {"PlaybackStatus": GLib.Variant("s", self.state), "Metadata": GLib.Variant("a{sv}", meta),
                "CanGoNext": GLib.Variant("b", True), "CanGoPrevious": GLib.Variant("b", True),
                "CanPlay": GLib.Variant("b", True), "CanPause": GLib.Variant("b", True),
                "CanControl": GLib.Variant("b", True), "CanSeek": GLib.Variant("b", False),
                "Rate": GLib.Variant("d", 1.0), "Position": GLib.Variant("x", 0)}

    def on_method(self, method: str) -> None:
        """A remote pressed a button: run it here, then report the new state."""
        action = ACTIONS.get(method)
        if action is None:
            return
        self.pressed.append(method)
        logger.info("AVRCP remote pressed %s", method)
        threading.Thread(target=lambda: (self.run_action(action), self.refresh()), daemon=True).start()

    def refresh(self) -> bool:
        """Re-read the real player; True when something changed."""
        try:
            state, title = parse_status(self.run_action("status"))
        except Exception:  # noqa: BLE001
            return False
        if (state, title) == (self.state, self.title):
            return False
        self.state, self.title = state, title
        if self._loop is not None and self.bus is not None:
            from gi.repository import GLib

            def emit():
                p = self._props()
                self.bus.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                     GLib.Variant("(sa{sv}as)", (IFACE, {"PlaybackStatus": p["PlaybackStatus"],
                                                                       "Metadata": p["Metadata"]}, [])))
                return False
            self._loop.get_context().invoke_full(0, emit)
        return True

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="avrcp-target", daemon=True)
            self._thread.start()
            self._ready.wait(10)
            threading.Thread(target=self._poll, name="avrcp-target-poll", daemon=True).start()

    def stop(self) -> None:
        loop, self._loop = self._loop, None
        if loop is not None:
            loop.get_context().invoke_full(0, lambda: (loop.quit(), False)[1])
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    def _poll(self) -> None:
        while self._thread is not None:
            self.refresh()
            time.sleep(POLL_SECONDS)

    def _run(self) -> None:
        from gi.repository import Gio, GLib
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self.bus = Gio.DBusConnection.new_for_address_sync(
                Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
            info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]

            def call(_c, _s, _p, _i, method, _params, inv):
                self.on_method(method)
                inv.return_value(None)
            reg = self.bus.register_object(PATH, info, call, lambda c, s, p, i, prop: self._props()[prop], None)
            self._loop = GLib.MainLoop.new(ctx, False)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)[:200]
            ctx.pop_thread_default()
            self._ready.set()
            return

        def registered(conn, res):
            try:
                conn.call_finish(res)
                logger.info("AVRCP target registered")
            except GLib.Error as exc:
                self.error = f"bluez refused the player: {exc.message}"
                logger.warning(self.error)
            self._ready.set()
        self.bus.call("org.bluez", "/org/bluez/hci0", "org.bluez.Media1", "RegisterPlayer",
                      GLib.Variant("(oa{sv})", (PATH, self._props())), None, 0, 10000, None, registered)
        try:
            self._loop.run()
        finally:
            try:
                self.bus.call_sync("org.bluez", "/org/bluez/hci0", "org.bluez.Media1", "UnregisterPlayer",
                                   GLib.Variant("(o)", (PATH,)), None, 0, 3000, None)
            except Exception:  # noqa: BLE001
                pass
            self.bus.unregister_object(reg)
            ctx.pop_thread_default()


def _media_control(action: str) -> str:
    from shani_chronoa.skills import media_control
    return media_control._run({"action": action})
