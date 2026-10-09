"""Tell bluez a device's battery, so the desktop's own Bluetooth panel shows it.

bluez's `BatteryProviderManager1` (on the adapter, man org.bluez.BatteryProviderManager(5))
accepts batteries that an application reads by itself - here, a MoYoung watch's
level, read over its own protocol by `watch_companion`. bluez then exposes it as
`org.bluez.Battery1` on the device, which GNOME and Plasma already display.

The provider is one ObjectManager root with one `org.bluez.BatteryProvider1`
child per device (Percentage, Source, Device). bluez reads it back with
GetManagedObjects *during* RegisterBatteryProvider, so registration is
asynchronous with the loop running - a synchronous call deadlocked the GATT
server the same way (ble_peripheral.py).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = "/dev/shani/chronoa/battery"
IFACE = "org.bluez.BatteryProvider1"
_XML = {
    "om": """<node><interface name="org.freedesktop.DBus.ObjectManager">
      <method name="GetManagedObjects"><arg type="a{oa{sa{sv}}}" direction="out"/></method>
      <signal name="InterfacesAdded"><arg type="o"/><arg type="a{sa{sv}}"/></signal>
      <signal name="InterfacesRemoved"><arg type="o"/><arg type="as"/></signal>
      </interface></node>""",
    "bat": f"""<node><interface name="{IFACE}">
      <property name="Percentage" type="y" access="read"/><property name="Source" type="s" access="read"/>
      <property name="Device" type="o" access="read"/></interface></node>""",
}


def device_path(mac: str) -> str:
    return "/org/bluez/hci0/dev_" + mac.replace(":", "_")


class BatteryPublisher:
    def __init__(self, source: str = "Shani Chronoa"):
        self.source = source
        self.levels: "dict[str, int]" = {}          # mac -> percent
        self._regs: "dict[str, int]" = {}
        self.bus = None
        self._loop = None
        self._ready = threading.Event()
        self.error = ""
        self._thread: Optional[threading.Thread] = None

    def _child(self, mac: str) -> str:
        return f"{ROOT}/dev_{mac.replace(':', '_')}"

    def _props(self, mac: str) -> dict:
        from gi.repository import GLib
        return {"Percentage": GLib.Variant("y", self.levels[mac]), "Source": GLib.Variant("s", self.source),
                "Device": GLib.Variant("o", device_path(mac))}

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="bluez-battery", daemon=True)
            self._thread.start()
            self._ready.wait(10)

    def stop(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.get_context().invoke_full(0, lambda: (loop.quit(), False)[1])
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    def _run(self) -> None:
        from gi.repository import Gio, GLib
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self.bus = Gio.DBusConnection.new_for_address_sync(
                Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
            self._bat_info = Gio.DBusNodeInfo.new_for_xml(_XML["bat"]).interfaces[0]
            om = Gio.DBusNodeInfo.new_for_xml(_XML["om"]).interfaces[0]

            def om_call(_c, _s, _p, _i, method, _params, inv):
                inv.return_value(GLib.Variant("(a{oa{sa{sv}}})", (
                    {self._child(m): {IFACE: self._props(m)} for m in self.levels},)))
            self._om_reg = self.bus.register_object(ROOT, om, om_call, None, None)
            self._loop = GLib.MainLoop.new(ctx, False)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)[:200]
            ctx.pop_thread_default()
            self._ready.set()
            return

        def registered(conn, res):
            try:
                conn.call_finish(res)
                logger.info("battery provider registered")
            except GLib.Error as exc:
                self.error = f"bluez refused the battery provider: {exc.message}"
                logger.warning(self.error)
            self._ready.set()
        self.bus.call("org.bluez", "/org/bluez/hci0", "org.bluez.BatteryProviderManager1",
                      "RegisterBatteryProvider", GLib.Variant("(o)", (ROOT,)), None, 0, 10000, None, registered)
        try:
            self._loop.run()
        finally:
            try:
                self.bus.call_sync("org.bluez", "/org/bluez/hci0", "org.bluez.BatteryProviderManager1",
                                   "UnregisterBatteryProvider", GLib.Variant("(o)", (ROOT,)), None, 0, 3000, None)
            except Exception:  # noqa: BLE001
                pass
            for reg in list(self._regs.values()) + [self._om_reg]:
                self.bus.unregister_object(reg)
            ctx.pop_thread_default()

    def set(self, mac: str, percent: int) -> None:
        """Publish (or update) one device's battery; safe from any thread."""
        percent = max(0, min(100, int(percent)))
        if self._loop is None:
            return

        def apply():
            from gi.repository import GLib
            new = mac not in self.levels
            self.levels[mac] = percent
            path = self._child(mac)
            if new:
                self._regs[mac] = self.bus.register_object(
                    path, self._bat_info, None, lambda c, s, p, i, prop, m=mac: self._props(m)[prop], None)
                self.bus.emit_signal(None, ROOT, "org.freedesktop.DBus.ObjectManager", "InterfacesAdded",
                                     GLib.Variant("(oa{sa{sv}})", (path, {IFACE: self._props(mac)})))
            else:
                self.bus.emit_signal(None, path, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                     GLib.Variant("(sa{sv}as)", (IFACE, {"Percentage": GLib.Variant("y", percent)}, [])))
            return False
        self._loop.get_context().invoke_full(0, apply)
