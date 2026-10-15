"""Find, pair and connect a Bluetooth device through BlueZ's own D-Bus API.

**Why this exists.** Every Bluetooth skill here works on a device that is
*already paired* - `bluetooth_devices` said "Pair one in Settings first", and
the phone, watch and headset panels all start from a paired device. Pairing was
the one step Chronoa sent a person somewhere else for. This is that step, for
the "Connect a device" window (`gui/connect_device.py`).

**Our own pairing agent, registered on our own connection, never the
default.** BlueZ asks *the agent of the connection that called `Pair`* for the
PIN or the code to compare, so a private connection with an agent on it gets
exactly the prompts for the pairing this window started - and GNOME's default
agent keeps every other one (a device pairing *to* the computer, a file push).
`RequestDefaultAgent` is deliberately never called. Closing the connection
unregisters the agent with it.

**Prompts are data, answered by a person.** Every agent method that needs a
decision becomes a `Prompt` handed to `on_prompt`; the D-Bus reply waits until
`Prompt.answer()` is called. Nothing here confirms a code by itself: a
confirmation that answered itself would be pairing with whichever device asked
first. `AuthorizeService` and `RequestAuthorization` are accepted only for the
device this window is pairing.

**Everything is asynchronous on the caller's main context**, because the
window calls it from GTK's: discovery results and prompts arrive as callbacks,
and `Pair` - which can wait a minute for a person at the other device - never
blocks a frame.

The test double is `tools/fake_bluez.py`, a BlueZ that speaks the same D-Bus
on a private bus; no test touches a real adapter.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

BLUEZ = "org.bluez"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
AGENT_MANAGER = "org.bluez.AgentManager1"
AGENT_PATH = "/org/shani/chronoa/pairing_agent"
CAPABILITY = "KeyboardDisplay"
PROPS = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

#: Long enough for a person to walk to the other device and press a button.
PAIR_TIMEOUT_MS = 90_000
CONNECT_TIMEOUT_MS = 30_000

_AGENT_XML = """
<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Cancel"/>
</interface></node>
"""

#: BlueZ errors as sentences a person can act on.
_WHY = {
    "org.bluez.Error.AuthenticationFailed": "the codes did not match, or the device refused",
    "org.bluez.Error.AuthenticationCanceled": "pairing was cancelled",
    "org.bluez.Error.AuthenticationRejected": "pairing was declined",
    "org.bluez.Error.AuthenticationTimeout": "nobody answered on the device in time",
    "org.bluez.Error.ConnectionAttemptFailed": "the device did not answer - is it still in pairing mode and close by?",
    "org.bluez.Error.AlreadyExists": "it is already paired",
    "org.bluez.Error.InProgress": "another pairing is already in progress",
    "org.bluez.Error.NotReady": "Bluetooth is not ready - is it turned on?",
    "org.bluez.Error.Failed": "the device refused",
}


#: GATT primary services (0x1800-0x1FFF), as `skills/bluetooth_gatt` reads them;
#: classic profiles are 0x11xx/0x12xx.
_GATT_UUID = re.compile(r"^0000(1[89a-f][0-9a-f]{2})-0000-1000-8000-00805f9b34fb$")
_CLASSIC_UUID = re.compile(r"^000011[0-9a-f]{2}-|^000012[0-9a-f]{2}-")


def is_low_energy(address_type: str, uuids) -> bool:
    """Whether a device speaks GATT only - a band, a strap, most watches.

    A random address is LE by definition (classic devices only have public
    ones). Otherwise: GATT services advertised and no classic profile. A
    headset advertises both and is treated as classic, which is what pairing
    it needs.
    """
    uuids = [str(u).lower() for u in (uuids or ())]
    if str(address_type or "").lower() == "random":
        return True
    return (any(_GATT_UUID.match(u) for u in uuids)
            and not any(_CLASSIC_UUID.match(u) for u in uuids))


def kind_of(icon: str, appearance: int = 0) -> str:
    """What a device is *for*, from what BlueZ says it is.

    `Icon` is BlueZ's own reading of the class of device (`audio-headset`,
    `phone`, `input-keyboard`); watches have no icon and show only in the
    GAP `Appearance` (0x00C0-0x00C3).
    """
    icon = (icon or "").lower()
    if icon.startswith("audio") or icon in ("multimedia-player",):
        return "audio"
    if icon == "phone":
        return "phone"
    if icon.startswith("input"):
        return "input"
    if icon == "computer":
        return "computer"
    if 0x00C0 <= int(appearance or 0) <= 0x00C3:
        return "watch"
    return "other"


@dataclass
class Device:
    path: str
    address: str
    name: str
    icon: str = ""
    kind: str = "other"
    paired: bool = False
    connected: bool = False
    rssi: Optional[int] = None
    #: GATT only (`is_low_energy`). Many such devices refuse to bond and are
    #: used by connecting - see `Pairing.connect_only`.
    le: bool = False
    trusted: bool = False


@dataclass
class Prompt:
    """One question from BlueZ, answered once by `answer()`.

    `kind` is `confirm` (compare `code`, yes or no), `authorize` (yes or no,
    no code), `enter-pin` / `enter-passkey` (type what the device shows),
    `show-pin` / `show-passkey` (type `code` on the device; nothing to answer),
    or `cancel` (BlueZ withdrew the question).
    """
    kind: str
    device: str
    code: str = ""
    _invocation: object = None

    def answer(self, value) -> None:
        """`True`/the typed value to accept; `None`/`False` to refuse."""
        invocation, self._invocation = self._invocation, None
        if invocation is None:
            return
        if value is None or value is False:
            invocation.return_dbus_error("org.bluez.Error.Rejected", "declined")
        elif self.kind == "enter-pin":
            invocation.return_value(GLib.Variant("(s)", (str(value),)))
        elif self.kind == "enter-passkey":
            invocation.return_value(GLib.Variant("(u)", (int(value),)))
        else:
            invocation.return_value(None)

    @property
    def needs_answer(self) -> bool:
        return self._invocation is not None


def _unpack(props) -> dict:
    return {k: (v.unpack() if hasattr(v, "unpack") else v) for k, v in dict(props).items()}


class Pairing:
    """One window's session with BlueZ: discovery, an agent, pairing."""

    def __init__(self, address: Optional[str] = None,
                 on_devices: Optional[Callable[[], None]] = None,
                 on_prompt: Optional[Callable[[Prompt], None]] = None) -> None:
        self._address = address
        self.on_devices = on_devices or (lambda: None)
        self.on_prompt = on_prompt or (lambda p: p.answer(None))
        self.bus: Optional[Gio.DBusConnection] = None
        self.adapter = ""
        self._objects: Dict[str, Dict[str, dict]] = {}
        self._subs: List[int] = []
        self._agent_reg = 0
        self._agent_registered = False
        self._pairing = ""          # the device path this window is pairing
        self._pending: Optional[Prompt] = None

    # -- opening and closing ------------------------------------------------
    def open(self) -> "tuple[bool, str]":
        """Connect, find the adapter, register the agent. `(ok, why-not)`."""
        try:
            address = self._address or Gio.dbus_address_get_for_bus_sync(
                Gio.BusType.SYSTEM, None)
            self.bus = Gio.DBusConnection.new_for_address_sync(
                address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            reply = self.bus.call_sync(BLUEZ, "/", OBJECT_MANAGER, "GetManagedObjects",
                                       None, None, Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as exc:
            return False, _bus_why(exc)
        for path, ifaces in reply.unpack()[0].items():
            self._objects[path] = {i: dict(p) for i, p in ifaces.items()}
        adapters = sorted(p for p, i in self._objects.items() if ADAPTER in i)
        if not adapters:
            return False, "this computer has no Bluetooth adapter"
        self.adapter = adapters[0]
        self._subs = [
            self.bus.signal_subscribe(BLUEZ, OBJECT_MANAGER, "InterfacesAdded", None, None,
                                      Gio.DBusSignalFlags.NONE, self._on_added),
            self.bus.signal_subscribe(BLUEZ, OBJECT_MANAGER, "InterfacesRemoved", None, None,
                                      Gio.DBusSignalFlags.NONE, self._on_removed),
            self.bus.signal_subscribe(BLUEZ, PROPS, "PropertiesChanged", None, None,
                                      Gio.DBusSignalFlags.NONE, self._on_changed),
        ]
        info = Gio.DBusNodeInfo.new_for_xml(_AGENT_XML).interfaces[0]
        try:
            self._agent_reg = self.bus.register_object(AGENT_PATH, info, self._on_agent_call,
                                                       None, None)
            self.bus.call_sync(BLUEZ, "/org/bluez", AGENT_MANAGER, "RegisterAgent",
                               GLib.Variant("(os)", (AGENT_PATH, CAPABILITY)),
                               None, Gio.DBusCallFlags.NONE, 5000, None)
            self._agent_registered = True
        except GLib.Error as exc:
            # Without an agent BlueZ can still pair "just works" devices; a
            # device that needs a code then fails with a reason, said then.
            logger.info("could not register the pairing agent: %s", exc.message)
        return True, ""

    def close(self) -> None:
        if self.bus is None:
            return
        if self._pending is not None:
            self._pending.answer(None)
        self.stop_discovery()
        if self._agent_registered:
            try:
                self.bus.call_sync(BLUEZ, "/org/bluez", AGENT_MANAGER, "UnregisterAgent",
                                   GLib.Variant("(o)", (AGENT_PATH,)), None,
                                   Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass
        if self._agent_reg:
            self.bus.unregister_object(self._agent_reg)
        for sub in self._subs:
            self.bus.signal_unsubscribe(sub)
        try:
            self.bus.close_sync(None)
        except GLib.Error:
            pass
        self.bus = None

    # -- the adapter --------------------------------------------------------
    def _adapter_prop(self, name: str, default=None):
        return _unpack(self._objects.get(self.adapter, {}).get(ADAPTER, {})).get(name, default)

    @property
    def powered(self) -> bool:
        return bool(self._adapter_prop("Powered", False))

    @property
    def discovering(self) -> bool:
        return bool(self._adapter_prop("Discovering", False))

    def power_on(self, done: Callable[[bool, str], None]) -> None:
        self._call(self.adapter, PROPS, "Set",
                   GLib.Variant("(ssv)", (ADAPTER, "Powered", GLib.Variant("b", True))),
                   done, 10_000)

    def start_discovery(self, done: Optional[Callable[[bool, str], None]] = None) -> None:
        try:
            self.bus.call_sync(BLUEZ, self.adapter, ADAPTER, "SetDiscoveryFilter",
                               GLib.Variant("(a{sv})", ({"Transport": GLib.Variant("s", "auto")},)),
                               None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass                  # an older BlueZ; plain discovery still works
        self._call(self.adapter, ADAPTER, "StartDiscovery", None,
                   done or (lambda ok, why: None), 10_000)

    def stop_discovery(self) -> None:
        if self.bus is None or not self.adapter:
            return
        try:
            self.bus.call_sync(BLUEZ, self.adapter, ADAPTER, "StopDiscovery", None, None,
                               Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass                  # not discovering, or not ours to stop

    # -- devices ------------------------------------------------------------
    def device(self, path: str) -> Optional[Device]:
        props = _unpack(self._objects.get(path, {}).get(DEVICE, {}))
        if not props:
            return None
        name = str(props.get("Alias") or props.get("Name") or "")
        icon = str(props.get("Icon") or "")
        rssi = props.get("RSSI")
        return Device(path=path, address=str(props.get("Address") or ""),
                      name=name, icon=icon,
                      kind=kind_of(icon, int(props.get("Appearance") or 0)),
                      paired=bool(props.get("Paired")),
                      connected=bool(props.get("Connected")),
                      rssi=int(rssi) if rssi is not None else None,
                      le=is_low_energy(props.get("AddressType", ""), props.get("UUIDs")),
                      trusted=bool(props.get("Trusted")))

    def nearby(self) -> List[Device]:
        """Devices on this adapter that could be paired: named, not paired.

        A device with no name is left out: BlueZ's alias for it is its address
        with dashes, which nobody can match to the thing in their hand. The
        strongest signal comes first - the device being held next to the
        computer is the one being paired.
        """
        out = []
        for path in self._objects:
            if not path.startswith(self.adapter + "/"):
                continue
            d = self.device(path)
            if d is None or d.paired or (d.le and d.trusted):
                continue
            props = _unpack(self._objects[path][DEVICE])
            if not props.get("Name"):
                continue
            out.append(d)
        return sorted(out, key=lambda d: (-(d.rssi if d.rssi is not None else -999), d.name.lower()))

    def pair(self, path: str, done: Callable[[bool, str], None]) -> None:
        """Pair, trust, connect. `done(ok, sentence)`; ok means paired.

        Trusted is set so the device reconnects without a prompt next time,
        which is what pairing means to a person. A device that pairs but will
        not connect yet is still paired, and the sentence says the rest.
        """
        self._pairing = path

        def paired(ok: bool, why: str) -> None:
            if not ok and why != _WHY["org.bluez.Error.AlreadyExists"]:
                self._pairing = ""
                done(False, why)
                return
            self._call(path, PROPS, "Set",
                       GLib.Variant("(ssv)", (DEVICE, "Trusted", GLib.Variant("b", True))),
                       lambda _ok, _why: None, 5000)
            self._call(path, DEVICE, "Connect", None, connected, CONNECT_TIMEOUT_MS)

        def connected(ok: bool, why: str) -> None:
            self._pairing = ""
            done(True, "paired and connected" if ok else f"paired, but not connected yet: {why}")

        self.stop_discovery()      # discovery slows pairing on most adapters
        self._call(path, DEVICE, "Pair", None, paired, PAIR_TIMEOUT_MS)

    def connect_only(self, path: str, done: Callable[[bool, str], None]) -> None:
        """Connect over GATT without bonding, and remember it. `done(ok, why)`.

        **Why there is a second way in.** A Bluetooth LE band or watch often
        refuses to bond - the vendor app never bonds either, it connects and
        talks GATT - so `Pair` fails on a device that works perfectly. Trusted
        is set after the connection, which keeps the device in BlueZ's list and
        lets it reconnect; `skills/bluetooth_gatt.paired()` lists trusted
        devices as well as paired ones for exactly this.
        """
        self.stop_discovery()

        def connected(ok: bool, why: str) -> None:
            if not ok:
                done(False, why)
                return
            self._call(path, PROPS, "Set",
                       GLib.Variant("(ssv)", (DEVICE, "Trusted", GLib.Variant("b", True))),
                       lambda t_ok, t_why: done(True, "connected" if t_ok else
                                                f"connected, but not remembered: {t_why}"),
                       5000)

        self._call(path, DEVICE, "Connect", None, connected, CONNECT_TIMEOUT_MS)

    def cancel_pairing(self) -> None:
        if self._pending is not None:
            self._pending.answer(None)
        if self._pairing:
            self._call(self._pairing, DEVICE, "CancelPairing", None,
                       lambda ok, why: None, 5000)

    # -- plumbing -----------------------------------------------------------
    def _call(self, path, iface, method, params, done, timeout) -> None:
        def finished(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error as exc:
                done(False, _bus_why(exc))
                return
            done(True, "")
        self.bus.call(BLUEZ, path, iface, method, params, None,
                      Gio.DBusCallFlags.NONE, timeout, None, finished)

    def _on_added(self, _c, _s, _p, _i, _sig, params) -> None:
        path, ifaces = params.unpack()
        entry = self._objects.setdefault(path, {})
        for iface, props in ifaces.items():
            entry.setdefault(iface, {}).update(props)
        if DEVICE in ifaces:
            self.on_devices()

    def _on_removed(self, _c, _s, _p, _i, _sig, params) -> None:
        path, ifaces = params.unpack()
        entry = self._objects.get(path, {})
        for iface in ifaces:
            entry.pop(iface, None)
        if not entry:
            self._objects.pop(path, None)
        if DEVICE in ifaces:
            self.on_devices()

    def _on_changed(self, _c, _s, path, _i, _sig, params) -> None:
        iface, changed, _invalidated = params.unpack()
        if path not in self._objects:
            return
        self._objects[path].setdefault(iface, {}).update(changed)
        if iface in (DEVICE, ADAPTER):
            self.on_devices()

    def _name(self, path: str) -> str:
        d = self.device(path)
        return d.name if d and d.name else "the device"

    def _on_agent_call(self, _c, _sender, _p, _i, method, params, invocation) -> None:
        args = params.unpack()
        device = args[0] if args else ""
        if method in ("Release",):
            invocation.return_value(None)
            return
        if method == "Cancel":
            invocation.return_value(None)
            if self._pending is not None:
                self._pending._invocation = None
                self._pending = None
            self.on_prompt(Prompt("cancel", ""))
            return
        if method in ("AuthorizeService", "RequestAuthorization") and device != self._pairing:
            # Not the device this window is pairing: not ours to allow.
            invocation.return_dbus_error("org.bluez.Error.Rejected", "not this pairing")
            return
        if method == "AuthorizeService":
            invocation.return_value(None)
            return
        name = self._name(device)
        if method == "DisplayPasskey":
            invocation.return_value(None)
            self.on_prompt(Prompt("show-passkey", name, f"{int(args[1]):06d}"))
            return
        if method == "DisplayPinCode":
            invocation.return_value(None)
            self.on_prompt(Prompt("show-pin", name, str(args[1])))
            return
        kind, code = {
            "RequestConfirmation": ("confirm", f"{int(args[1]):06d}" if len(args) > 1 else ""),
            "RequestAuthorization": ("authorize", ""),
            "RequestPinCode": ("enter-pin", ""),
            "RequestPasskey": ("enter-passkey", ""),
        }.get(method, ("", ""))
        if not kind:
            invocation.return_dbus_error("org.bluez.Error.Rejected", f"{method} is not handled")
            return
        self._pending = Prompt(kind, name, code, invocation)
        self.on_prompt(self._pending)


def _bus_why(exc: "GLib.Error") -> str:
    remote = Gio.DBusError.get_remote_error(exc) or ""
    if remote in _WHY:
        return _WHY[remote]
    if remote in ("org.freedesktop.DBus.Error.ServiceUnknown",
                  "org.freedesktop.DBus.Error.NameHasNoOwner"):
        return "the Bluetooth service (bluetoothd) is not running"
    message = exc.message or ""
    if message.startswith("GDBus.Error:"):
        message = message.split(" ", 1)[-1] if " " in message else ""
    return message.strip() or "Bluetooth did not answer"
