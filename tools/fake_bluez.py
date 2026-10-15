#!/usr/bin/env python3
"""A BlueZ that speaks the real D-Bus API on a private bus - the pairing harness.

`bt_pair.py` and the "Connect a device" window talk to `org.bluez` over D-Bus;
this owns that name on whatever bus it is given and answers the same methods,
including the part that matters most: on `Device1.Pair` it **calls back into the
agent the caller registered** (`RequestConfirmation`, `DisplayPasskey`,
`RequestPinCode`), exactly as bluetoothd does. So a test drives the real agent
round-trip without a real adapter, and nothing ever touches the machine's
Bluetooth.

Devices, by what they make pairing do:

    Pixel 9        phone     RequestConfirmation(123456); paired if confirmed
    WH-1000XM5     headset   just works (no agent call)
    K380 Keyboard  keyboard  DisplayPasskey(654321), then paired
    Car kit        audio     RequestPinCode; paired if the PIN is "0000"
    Band 8         watch, LE refuses to bond (AuthenticationFailed);
                             Connect works, so connect_only does
    Broken         other     ConnectionAttemptFailed

Use: `FakeBluez(address).start()` in a test (its own thread and main context),
or `python3 tools/fake_bluez.py` to print a private bus address and serve on it
for a live window (`DBUS_SYSTEM_BUS_ADDRESS=<that> tools/live_window.sh ...`).
"""
from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

HCI = "/org/bluez/hci0"

_XML = """
<node>
 <interface name="org.freedesktop.DBus.ObjectManager">
  <method name="GetManagedObjects"><arg type="a{oa{sa{sv}}}" direction="out"/></method>
  <signal name="InterfacesAdded"><arg type="o"/><arg type="a{sa{sv}}"/></signal>
  <signal name="InterfacesRemoved"><arg type="o"/><arg type="as"/></signal>
 </interface>
 <interface name="org.freedesktop.DBus.Properties">
  <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
  <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
  <method name="Set"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/></method>
  <signal name="PropertiesChanged"><arg type="s"/><arg type="a{sv}"/><arg type="as"/></signal>
 </interface>
 <interface name="org.bluez.AgentManager1">
  <method name="RegisterAgent"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="UnregisterAgent"><arg type="o" direction="in"/></method>
  <method name="RequestDefaultAgent"><arg type="o" direction="in"/></method>
 </interface>
 <interface name="org.bluez.Adapter1">
  <method name="StartDiscovery"/>
  <method name="StopDiscovery"/>
  <method name="SetDiscoveryFilter"><arg type="a{sv}" direction="in"/></method>
 </interface>
 <interface name="org.bluez.Device1">
  <method name="Pair"/>
  <method name="CancelPairing"/>
  <method name="Connect"/>
  <method name="Disconnect"/>
 </interface>
</node>
"""

GATT_HR = "0000180d-0000-1000-8000-00805f9b34fb"
CLASSIC_AUDIO = "0000110b-0000-1000-8000-00805f9b34fb"

#: name -> (address, icon, appearance, address type, uuids, behaviour)
DEVICES = {
    "Pixel 9": ("AA:00:00:00:00:01", "phone", 0, "public", [], "confirm"),
    "WH-1000XM5": ("AA:00:00:00:00:02", "audio-headset", 0, "public", [CLASSIC_AUDIO], "justworks"),
    "K380 Keyboard": ("AA:00:00:00:00:03", "input-keyboard", 0, "public", [], "display"),
    "Car kit": ("AA:00:00:00:00:04", "audio-card", 0, "public", [CLASSIC_AUDIO], "pin"),
    "Band 8": ("C1:00:00:00:00:05", "", 0x00C1, "random", [GATT_HR], "nobond"),
    "Broken": ("AA:00:00:00:00:06", "", 0, "public", [], "unreachable"),
}


def _path(address: str) -> str:
    return f"{HCI}/dev_{address.replace(':', '_')}"


class FakeBluez:
    def __init__(self, address: str, devices=None, powered: bool = True) -> None:
        self.address = address
        self.devices = dict(DEVICES if devices is None else devices)
        self.powered = powered
        self.discovering = False
        self.agent = None               # (sender, path)
        self.calls: "list[str]" = []    # what clients asked, for assertions
        self.objects: "dict[str, dict[str, dict]]" = {}
        self._behaviour: "dict[str, str]" = {}
        self._ready = threading.Event()
        self._loop = None
        self.bus = None

    # -- state ------------------------------------------------------------
    def _adapter_props(self) -> dict:
        return {"Address": GLib.Variant("s", "00:11:22:33:44:55"),
                "Name": GLib.Variant("s", "fake"),
                "Powered": GLib.Variant("b", self.powered),
                "Discovering": GLib.Variant("b", self.discovering)}

    def _tree(self) -> dict:
        tree = {"/org/bluez": {"org.bluez.AgentManager1": {}},
                HCI: {"org.bluez.Adapter1": self._adapter_props()}}
        tree.update({p: {"org.bluez.Device1": dict(v)} for p, v in self.objects.items()})
        return tree

    def _emit(self, path, iface, signal, params) -> None:
        self.bus.emit_signal(None, path, iface, signal, params)

    def _changed(self, path, iface, changed: dict) -> None:
        self._emit(path, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                   GLib.Variant("(sa{sv}as)", (iface, changed, [])))

    def _set_device(self, path, **values) -> None:
        props = self.objects[path]
        changed = {}
        for key, value in values.items():
            variant = GLib.Variant("b", value) if isinstance(value, bool) else value
            props[key] = variant
            changed[key] = variant
        self._changed(path, "org.bluez.Device1", changed)

    def _discover_all(self) -> bool:
        for rssi, (name, (address, icon, appearance, atype, uuids, behaviour)) in enumerate(
                self.devices.items()):
            path = _path(address)
            if path in self.objects:
                continue
            props = {"Address": GLib.Variant("s", address),
                     "AddressType": GLib.Variant("s", atype),
                     "Name": GLib.Variant("s", name), "Alias": GLib.Variant("s", name),
                     "Paired": GLib.Variant("b", False), "Trusted": GLib.Variant("b", False),
                     "Connected": GLib.Variant("b", False),
                     "RSSI": GLib.Variant("n", -40 - 5 * rssi),
                     "UUIDs": GLib.Variant("as", list(uuids)),
                     "Adapter": GLib.Variant("o", HCI)}
            if icon:
                props["Icon"] = GLib.Variant("s", icon)
            if appearance:
                props["Appearance"] = GLib.Variant("q", appearance)
            self.objects[path] = props
            self._behaviour[path] = behaviour
            self._emit("/", "org.freedesktop.DBus.ObjectManager", "InterfacesAdded",
                       GLib.Variant("(oa{sa{sv}})", (path, {"org.bluez.Device1": props})))
        return GLib.SOURCE_REMOVE

    # -- the agent call-back ----------------------------------------------
    def _ask_agent(self, method, params, done) -> None:
        if self.agent is None:
            done(False)
            return
        sender, agent_path = self.agent

        def finished(bus, result):
            try:
                bus.call_finish(result)
                done(True)
            except GLib.Error:
                done(False)
        self.bus.call(sender, agent_path, "org.bluez.Agent1", method, params, None,
                      Gio.DBusCallFlags.NONE, 60_000, None, finished)

    def _pair(self, path, invocation) -> None:
        behaviour = self._behaviour.get(path, "justworks")

        def paired(ok: bool) -> None:
            if not ok:
                invocation.return_dbus_error("org.bluez.Error.AuthenticationRejected",
                                             "Authentication Rejected")
                return
            self._set_device(path, Paired=True)
            invocation.return_value(None)

        if behaviour == "unreachable":
            invocation.return_dbus_error("org.bluez.Error.ConnectionAttemptFailed",
                                         "Page Timeout")
        elif behaviour == "nobond":
            invocation.return_dbus_error("org.bluez.Error.AuthenticationFailed",
                                         "Authentication Failed")
        elif behaviour == "confirm":
            self._ask_agent("RequestConfirmation", GLib.Variant("(ou)", (path, 123456)), paired)
        elif behaviour == "display":
            self._ask_agent("DisplayPasskey", GLib.Variant("(ouq)", (path, 654321, 0)),
                            lambda _ok: GLib.timeout_add(150, lambda: paired(True) and False))
        elif behaviour == "pin":
            def got(bus, result):
                try:
                    pin = bus.call_finish(result).unpack()[0]
                except GLib.Error:
                    pin = None
                paired(pin == "0000")
            sender, agent_path = self.agent or ("", "")
            if not sender:
                paired(False)
                return
            self.bus.call(sender, agent_path, "org.bluez.Agent1", "RequestPinCode",
                          GLib.Variant("(o)", (path,)), GLib.VariantType("(s)"),
                          Gio.DBusCallFlags.NONE, 60_000, None, got)
        else:
            paired(True)

    # -- dispatch ---------------------------------------------------------
    def _call(self, _c, sender, path, iface, method, params, invocation) -> None:
        self.calls.append(f"{iface.rsplit('.', 1)[-1]}.{method} {path}")
        args = params.unpack()
        if method == "GetManagedObjects":
            invocation.return_value(GLib.Variant("(a{oa{sa{sv}}})", (self._tree(),)))
        elif method == "RegisterAgent":
            self.agent = (sender, args[0])
            invocation.return_value(None)
        elif method == "UnregisterAgent":
            self.agent = None
            invocation.return_value(None)
        elif method == "RequestDefaultAgent":
            invocation.return_value(None)
        elif method == "SetDiscoveryFilter":
            invocation.return_value(None)
        elif method == "StartDiscovery":
            if not self.powered:
                invocation.return_dbus_error("org.bluez.Error.NotReady", "Resource Not Ready")
                return
            self.discovering = True
            self._changed(HCI, "org.bluez.Adapter1", {"Discovering": GLib.Variant("b", True)})
            GLib.timeout_add(100, self._discover_all)
            invocation.return_value(None)
        elif method == "StopDiscovery":
            self.discovering = False
            invocation.return_value(None)
        elif method == "Set":
            target, name, value = args
            if target == "org.bluez.Adapter1" and name == "Powered":
                self.powered = bool(value)
                self._changed(HCI, target, {"Powered": GLib.Variant("b", self.powered)})
            elif path in self.objects:
                self._set_device(path, **{name: bool(value)})
            invocation.return_value(None)
        elif method in ("Get", "GetAll"):
            props = self._adapter_props() if path == HCI else self.objects.get(path, {})
            if method == "GetAll":
                invocation.return_value(GLib.Variant("(a{sv})", (props,)))
            else:
                invocation.return_value(GLib.Variant("(v)", (props[args[1]],)))
        elif method == "Pair":
            self._pair(path, invocation)
        elif method == "CancelPairing":
            invocation.return_value(None)
        elif method == "Connect":
            if self._behaviour.get(path) == "unreachable":
                invocation.return_dbus_error("org.bluez.Error.Failed", "Page Timeout")
                return
            self._set_device(path, Connected=True)
            invocation.return_value(None)
        elif method == "Disconnect":
            self._set_device(path, Connected=False)
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.bluez.Error.NotSupported", method)

    # -- running ----------------------------------------------------------
    def _serve(self) -> None:
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        self.bus = Gio.DBusConnection.new_for_address_sync(
            self.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        info = Gio.DBusNodeInfo.new_for_xml(_XML)
        by_name = {i.name: i for i in info.interfaces}
        self.bus.register_object("/", by_name["org.freedesktop.DBus.ObjectManager"], self._call)
        self.bus.register_object("/org/bluez", by_name["org.bluez.AgentManager1"], self._call)
        for iface in ("org.bluez.Adapter1", "org.freedesktop.DBus.Properties"):
            self.bus.register_object(HCI, by_name[iface], self._call)
        self._device_ifaces = (by_name["org.bluez.Device1"],
                               by_name["org.freedesktop.DBus.Properties"])
        # Devices appear under the adapter; one subtree registration answers
        # for every one of them, discovered or not.
        self._register_devices()
        self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                           "org.freedesktop.DBus", "RequestName",
                           GLib.Variant("(su)", ("org.bluez", 4)), None,
                           Gio.DBusCallFlags.NONE, 5000, None)
        self._loop = GLib.MainLoop.new(ctx, False)
        self._ready.set()
        self._loop.run()
        ctx.pop_thread_default()

    def _register_devices(self) -> None:
        for _name, (address, *_rest) in self.devices.items():
            for iface in self._device_ifaces:
                self.bus.register_object(_path(address), iface, self._call)

    def start(self) -> "FakeBluez":
        threading.Thread(target=self._serve, daemon=True).start()
        if not self._ready.wait(10):
            raise RuntimeError("the fake BlueZ did not start")
        return self

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.quit()


def main() -> int:
    """Serve on a fresh private bus and print its address; Ctrl+C stops."""
    test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    test_bus.up()
    address = test_bus.get_bus_address()
    FakeBluez(address).start()
    print(address, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        test_bus.down()
    return 0


if __name__ == "__main__":
    sys.exit(main())
