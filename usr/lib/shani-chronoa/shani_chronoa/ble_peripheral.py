"""This computer as a Bluetooth LE device: a remote for the phone, and more.

bluez can be a peripheral: `Adapter1.Roles` is ["central", "peripheral"] and the
adapter (`/org/bluez/hci0`, not `/org/bluez` - that wrong path once produced a
confident "central-only") carries `GattManager1` and `LEAdvertisingManager1`
(man org.bluez.GattManager(5), org.bluez.LEAdvertisingManager(5)). This module
registers one GATT application with four standard services and advertises it:

- **HID over GATT (0x1812)** - a keyboard plus media keys. Android pairs with it
  like any Bluetooth keyboard, with no app, and then Chronoa can press the
  phone's **camera shutter** (volume up is the shutter in the camera app),
  play/pause, next, previous, volume, and **type text** on the phone.
- **Immediate Alert (0x1802)** - the Find Me target: a tag or watch that writes
  an alert level makes this computer ring.
- **Battery (0x180F)** - this computer's battery, from /sys.
- **Current Time (0x1805)** - the time, for devices that read it.

One process owns the objects (bluez unregisters an application whose bus
connection goes away), so it runs inside the app and other callers - the
`phone_remote` skill in its sandbox - reach it through a socket, the same shape
as `watch_companion`.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

KEY = "phone-remote-enabled"
ROOT = "/dev/shani/chronoa/gatt"
ADV = "/dev/shani/chronoa/adv_remote"
NAME = "Chronoa Remote"
BASE = "-0000-1000-8000-00805f9b34fb"


def uuid16(short: str) -> str:
    return f"0000{short}{BASE}"


# --- HID: one keyboard report (id 1) and one consumer-control report (id 2) --------

#: USB HID report map. Keyboard: modifiers, reserved, six keys (the boot layout).
#: Consumer: one 16-bit usage. Standard descriptors, as BLE keyboards send them.
REPORT_MAP = bytes([
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, 0x01,             # Generic Desktop, Keyboard, id 1
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
    0x75, 0x01, 0x95, 0x08, 0x81, 0x02,                          # 8 modifier bits
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,                          # reserved byte
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,
    0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,              # six key codes
    0xC0,
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x02,             # Consumer Control, id 2
    0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03,
    0x75, 0x10, 0x95, 0x01, 0x81, 0x00,                          # one 16-bit usage
    0xC0,
])
HID_INFO = bytes([0x11, 0x01, 0x00, 0x02])     # HID 1.11, no country, normally connectable

#: Consumer usages (HID Usage Tables, Consumer page 0x0C).
CONSUMER = {"volume_up": 0x00E9, "volume_down": 0x00EA, "mute": 0x00E2, "play_pause": 0x00CD,
            "next": 0x00B5, "previous": 0x00B6, "stop": 0x00B7,
            # Android's camera app takes a photo on volume up: the remote shutter.
            "shutter": 0x00E9}

_SHIFT = 0x02
_KEYS = {**{chr(ord("a") + i): (0x04 + i, 0) for i in range(26)},
         **{chr(ord("A") + i): (0x04 + i, _SHIFT) for i in range(26)},
         **{str(i): (0x1E + (i - 1) % 10, 0) for i in range(1, 10)}, "0": (0x27, 0),
         "\n": (0x28, 0), "\t": (0x2B, 0), " ": (0x2C, 0), "-": (0x2D, 0), "_": (0x2D, _SHIFT),
         "=": (0x2E, 0), "+": (0x2E, _SHIFT), "[": (0x2F, 0), "{": (0x2F, _SHIFT), "]": (0x30, 0),
         "}": (0x30, _SHIFT), "\\": (0x31, 0), "|": (0x31, _SHIFT), ";": (0x33, 0), ":": (0x33, _SHIFT),
         "'": (0x34, 0), '"': (0x34, _SHIFT), "`": (0x35, 0), "~": (0x35, _SHIFT), ",": (0x36, 0),
         "<": (0x36, _SHIFT), ".": (0x37, 0), ">": (0x37, _SHIFT), "/": (0x38, 0), "?": (0x38, _SHIFT),
         "!": (0x1E, _SHIFT), "@": (0x1F, _SHIFT), "#": (0x20, _SHIFT), "$": (0x21, _SHIFT),
         "%": (0x22, _SHIFT), "^": (0x23, _SHIFT), "&": (0x24, _SHIFT), "*": (0x25, _SHIFT),
         "(": (0x26, _SHIFT), ")": (0x27, _SHIFT)}


def key_reports(text: str) -> "tuple[list[bytes], str]":
    """(press/release report pairs for `text`, the characters that cannot be typed).

    A US-layout keyboard can type ASCII only; anything else is reported, never
    silently dropped or replaced.
    """
    reports, missing = [], ""
    for ch in text:
        code = _KEYS.get(ch)
        if code is None:
            missing += ch
            continue
        key, mods = code
        reports.append(bytes([mods, 0, key, 0, 0, 0, 0, 0]))
        reports.append(bytes(8))
    return reports, missing


def consumer_reports(name: str) -> "list[bytes]":
    usage = CONSUMER[name]
    return [usage.to_bytes(2, "little"), bytes(2)]


def battery_percent() -> Optional[int]:
    base = "/sys/class/power_supply"
    try:
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry)
            try:
                if open(os.path.join(path, "type")).read().strip() == "Battery":
                    return max(0, min(100, int(open(os.path.join(path, "capacity")).read().strip())))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return None


def current_time_value(now: Optional[datetime] = None) -> bytes:
    """Current Time characteristic (0x2A2B): year LE, month, day, h, m, s, weekday (1=Mon), 1/256 s, reason."""
    now = now or datetime.now()
    return (now.year.to_bytes(2, "little")
            + bytes([now.month, now.day, now.hour, now.minute, now.second, now.isoweekday(), 0, 0]))


# --- the GATT objects ---------------------------------------------------------------

_XML = {
    "org.bluez.GattService1": """<interface name="org.bluez.GattService1">
      <property name="UUID" type="s" access="read"/><property name="Primary" type="b" access="read"/>
      </interface>""",
    "org.bluez.GattCharacteristic1": """<interface name="org.bluez.GattCharacteristic1">
      <method name="ReadValue"><arg type="a{sv}" direction="in"/><arg type="ay" direction="out"/></method>
      <method name="WriteValue"><arg type="ay" direction="in"/><arg type="a{sv}" direction="in"/></method>
      <method name="StartNotify"/><method name="StopNotify"/>
      <property name="UUID" type="s" access="read"/><property name="Service" type="o" access="read"/>
      <property name="Flags" type="as" access="read"/><property name="Value" type="ay" access="read"/>
      </interface>""",
    "org.bluez.GattDescriptor1": """<interface name="org.bluez.GattDescriptor1">
      <method name="ReadValue"><arg type="a{sv}" direction="in"/><arg type="ay" direction="out"/></method>
      <property name="UUID" type="s" access="read"/><property name="Characteristic" type="o" access="read"/>
      <property name="Flags" type="as" access="read"/>
      </interface>""",
    "org.freedesktop.DBus.ObjectManager": """<interface name="org.freedesktop.DBus.ObjectManager">
      <method name="GetManagedObjects"><arg type="a{oa{sa{sv}}}" direction="out"/></method>
      </interface>""",
    "org.bluez.LEAdvertisement1": """<interface name="org.bluez.LEAdvertisement1"><method name="Release"/>
      <property name="Type" type="s" access="read"/><property name="LocalName" type="s" access="read"/>
      <property name="ServiceUUIDs" type="as" access="read"/><property name="Appearance" type="q" access="read"/>
      <property name="Discoverable" type="b" access="read"/>
      </interface>""",
}


class _Obj:
    def __init__(self, path: str, iface: str, props: dict, value: bytes = b"",
                 read: Optional[Callable[[], bytes]] = None, write: Optional[Callable[[bytes], None]] = None):
        self.path, self.iface, self.props = path, iface, props
        self.value, self.read, self.write = value, read, write
        self.notifying = False


class Peripheral:
    def __init__(self, config: Any = None, on_alert: Optional[Callable[[int], None]] = None):
        self.config = config
        self.on_alert = on_alert
        self.objects: "list[_Obj]" = []
        self.bus = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self.error = ""
        self.connected_to: "list[str]" = []
        self._build()

    # -- the tree

    def _add(self, obj: _Obj) -> _Obj:
        self.objects.append(obj)
        return obj

    def _service(self, n: int, short: str) -> str:
        path = f"{ROOT}/service{n}"
        self._add(_Obj(path, "org.bluez.GattService1", {"UUID": ("s", uuid16(short)), "Primary": ("b", True)}))
        return path

    def _char(self, service: str, n: int, short: str, flags: list, value: bytes = b"", read=None, write=None):
        path = f"{service}/char{n}"
        return self._add(_Obj(path, "org.bluez.GattCharacteristic1",
                              {"UUID": ("s", uuid16(short)), "Service": ("o", service), "Flags": ("as", flags)},
                              value, read, write))

    def _desc(self, char: _Obj, n: int, short: str, value: bytes, flags: list):
        path = f"{char.path}/desc{n}"
        return self._add(_Obj(path, "org.bluez.GattDescriptor1",
                              {"UUID": ("s", uuid16(short)), "Characteristic": ("o", char.path), "Flags": ("as", flags)},
                              value))

    def _build(self) -> None:
        hid = self._service(0, "1812")
        # Encrypted reads on the HID characteristics are what make the phone bond -
        # a HID host refuses an unencrypted keyboard.
        self._char(hid, 0, "2a4a", ["read"], HID_INFO)
        self._char(hid, 1, "2a4b", ["read", "encrypt-read"], REPORT_MAP)
        self._char(hid, 2, "2a4c", ["write-without-response"], write=lambda v: None)
        self.protocol_mode = self._char(hid, 3, "2a4e", ["read", "write-without-response"], b"\x01",
                                        write=lambda v: None)
        self.keyboard = self._char(hid, 4, "2a4d", ["read", "notify", "encrypt-read"], bytes(8))
        self._desc(self.keyboard, 0, "2908", bytes([1, 1]), ["read"])            # report id 1, input
        self.consumer = self._char(hid, 5, "2a4d", ["read", "notify", "encrypt-read"], bytes(2))
        self._desc(self.consumer, 0, "2908", bytes([2, 1]), ["read"])            # report id 2, input

        ias = self._service(1, "1802")
        self._char(ias, 0, "2a06", ["write-without-response"], write=self._alert)

        bat = self._service(2, "180f")
        self.battery = self._char(bat, 0, "2a19", ["read", "notify"],
                                  read=lambda: bytes([battery_percent() or 0]))

        cts = self._service(3, "1805")
        self._char(cts, 0, "2a2b", ["read"], read=current_time_value)

    def _alert(self, value: bytes) -> None:
        level = value[0] if value else 0
        logger.info("ble peripheral: alert level %d written", level)
        if self.on_alert is not None:
            self.on_alert(level)

    # -- D-Bus

    def _variant(self, sig_value):
        from gi.repository import GLib
        sig, value = sig_value
        return GLib.Variant(sig, value)

    def managed_objects(self) -> dict:
        from gi.repository import GLib
        out = {}
        for o in self.objects:
            props = {k: self._variant(v) for k, v in o.props.items()}
            if o.iface == "org.bluez.GattCharacteristic1":
                props["Value"] = GLib.Variant("ay", list(o.value))
            out[o.path] = {o.iface: props}
        return out

    def _method(self, obj: _Obj):
        def handler(_conn, _sender, _path, _iface, method, params, invocation):
            from gi.repository import GLib
            try:
                if method == "GetManagedObjects":
                    invocation.return_value(GLib.Variant("(a{oa{sa{sv}}})", (self.managed_objects(),)))
                elif method == "ReadValue":
                    value = obj.read() if obj.read else obj.value
                    invocation.return_value(GLib.Variant("(ay)", (list(value),)))
                elif method == "WriteValue":
                    data = bytes(params.unpack()[0])
                    obj.value = data
                    if obj.write:
                        obj.write(data)
                    invocation.return_value(None)
                elif method == "StartNotify":
                    obj.notifying = True
                    invocation.return_value(None)
                elif method == "StopNotify":
                    obj.notifying = False
                    invocation.return_value(None)
                else:
                    invocation.return_value(None)
            except Exception as exc:  # noqa: BLE001 - an error to bluez, never a crash
                invocation.return_dbus_error("org.bluez.Error.Failed", str(exc)[:120])
        return handler

    def _getter(self, obj: _Obj):
        def get(_conn, _sender, _path, _iface, prop):
            from gi.repository import GLib
            if prop == "Value":
                return GLib.Variant("ay", list(obj.value))
            return self._variant(obj.props[prop])
        return get

    def notify(self, obj: _Obj, value: bytes) -> bool:
        """Push a new value; bluez turns the PropertiesChanged into a notification."""
        from gi.repository import GLib
        obj.value = value
        if not obj.notifying or self.bus is None:
            return False
        self.bus.emit_signal(None, obj.path, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                             GLib.Variant("(sa{sv}as)", (obj.iface, {"Value": GLib.Variant("ay", list(value))}, [])))
        return True

    # -- life cycle

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ble-peripheral", daemon=True)
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
        regs = []
        try:
            self.bus = Gio.DBusConnection.new_for_address_sync(
                Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
            infos = {name: Gio.DBusNodeInfo.new_for_xml(f"<node>{xml}</node>").interfaces[0]
                     for name, xml in _XML.items()}
            regs.append(self.bus.register_object(ROOT, infos["org.freedesktop.DBus.ObjectManager"],
                                                 self._method(None), None, None))
            for o in self.objects:
                regs.append(self.bus.register_object(o.path, infos[o.iface], self._method(o), self._getter(o), None))
            adv_props = {"Type": ("s", "peripheral"), "LocalName": ("s", NAME),
                         "ServiceUUIDs": ("as", [uuid16("1812")]), "Appearance": ("q", 0x03C1),
                         "Discoverable": ("b", True)}
            regs.append(self.bus.register_object(
                ADV, infos["org.bluez.LEAdvertisement1"], lambda *a: a[-1].return_value(None),
                lambda c, s, p, i, prop: self._variant(adv_props[prop]), None))
            self._loop = GLib.MainLoop.new(ctx, False)
        except Exception as exc:  # noqa: BLE001 - no bus: said, not raised
            self.error = str(exc)[:200]
            logger.warning("ble peripheral not started: %s", self.error)
            for r in regs:
                self.bus.unregister_object(r)
            ctx.pop_thread_default()
            self._ready.set()
            return

        # Asynchronously, with the loop running: during RegisterApplication bluez
        # calls back into GetManagedObjects on this connection, and a synchronous
        # call here deadlocked until its 10 s timeout (measured).
        def advertised(conn, res):
            try:
                conn.call_finish(res)
                logger.info("ble peripheral: registered and advertising as %r", NAME)
            except GLib.Error as exc:
                self.error = f"advertising refused: {exc.message}"
                logger.warning("ble peripheral: %s", self.error)
            self._ready.set()

        def registered(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error as exc:
                self.error = f"GATT application refused: {exc.message}"
                logger.warning("ble peripheral: %s", self.error)
                self._ready.set()
                self._loop.quit()
                return
            conn.call("org.bluez", "/org/bluez/hci0", "org.bluez.LEAdvertisingManager1", "RegisterAdvertisement",
                      GLib.Variant("(oa{sv})", (ADV, {})), None, 0, 10000, None, advertised)

        self.bus.call("org.bluez", "/org/bluez/hci0", "org.bluez.GattManager1", "RegisterApplication",
                      GLib.Variant("(oa{sv})", (ROOT, {})), None, 0, 10000, None, registered)
        try:
            self._loop.run()
        finally:
            for call in (("org.bluez.LEAdvertisingManager1", "UnregisterAdvertisement", ADV),
                         ("org.bluez.GattManager1", "UnregisterApplication", ROOT)):
                try:
                    self.bus.call_sync("org.bluez", "/org/bluez/hci0", call[0], call[1],
                                       GLib.Variant("(o)", (call[2],)), None, 0, 3000, None)
                except Exception:  # noqa: BLE001
                    pass
            for r in regs:
                self.bus.unregister_object(r)
            ctx.pop_thread_default()

    def _on_loop(self, fn) -> None:
        """Run `fn` on the peripheral's own loop and wait for it."""
        done = threading.Event()
        box = {}

        def run():
            try:
                box["r"] = fn()
            finally:
                done.set()
            return False
        if self._loop is None:
            raise RuntimeError(self.error or "the Bluetooth peripheral is not running")
        self._loop.get_context().invoke_full(0, run)
        done.wait(10)
        return box.get("r")

    # -- what it does

    def subscribed(self) -> bool:
        return self.keyboard.notifying or self.consumer.notifying

    def press(self, name: str) -> bool:
        if name not in CONSUMER:
            raise ValueError(f"unknown key {name!r}; known: {', '.join(CONSUMER)}")
        ok = True
        for report in consumer_reports(name):
            ok = self._on_loop(lambda r=report: self.notify(self.consumer, r)) and ok
            time.sleep(0.05)
        return ok

    def type_text(self, text: str) -> "tuple[bool, str]":
        reports, missing = key_reports(text)
        ok = True
        for report in reports:
            ok = self._on_loop(lambda r=report: self.notify(self.keyboard, r)) and ok
            time.sleep(0.02)
        return ok, missing


# --- the socket the skill talks to ------------------------------------------------------

def socket_path() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "chronoa-ble-remote.sock")


def serve(peripheral: Peripheral) -> threading.Thread:
    """Answer the skill's requests: status, press, type."""
    path = socket_path()
    try:
        os.unlink(path)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(4)

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    msg = json.loads(conn.makefile().readline() or "{}")
                    action = msg.get("action")
                    if action == "status":
                        reply = {"running": peripheral._loop is not None, "error": peripheral.error,
                                 "subscribed": peripheral.subscribed(), "name": NAME}
                    elif action == "press":
                        reply = {"sent": peripheral.press(str(msg.get("key")))}
                    elif action == "type":
                        sent, missing = peripheral.type_text(str(msg.get("text") or ""))
                        reply = {"sent": sent, "missing": missing}
                    else:
                        reply = {"error": f"unknown action {action!r}"}
                except Exception as exc:  # noqa: BLE001
                    reply = {"error": str(exc)[:200]}
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    pass
    t = threading.Thread(target=loop, name="ble-remote-socket", daemon=True)
    t.start()
    return t


def ask(action: str, **fields) -> dict:
    """The skill's side of the socket."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(30)
        s.connect(socket_path())
        s.sendall((json.dumps({"action": action, **fields}) + "\n").encode())
        return json.loads(s.makefile().readline() or "{}")


class RemoteService:
    """Starts and stops the peripheral with its switch, checked every 30 s."""

    def __init__(self, config: Any, check_seconds: float = 30.0):
        self.config = config
        self.check_seconds = check_seconds
        self.peripheral: Optional[Peripheral] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        from shani_chronoa.watch_companion import Ringer
        self.ringer = Ringer()

    def _allowed(self) -> bool:
        try:
            return bool(self.config.get_bool(KEY, False))
        except Exception:  # noqa: BLE001
            return False

    def on_alert(self, level: int) -> None:
        """Immediate Alert: 0 none, 1 mild, 2 high - a tag or watch looking for this computer."""
        if level:
            self.ringer.start()
        else:
            self.ringer.stop()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ble-remote-service", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._down()

    def _down(self) -> None:
        if self.peripheral is not None:
            self.peripheral.stop()
            self.peripheral = None
            try:
                os.unlink(socket_path())
            except OSError:
                pass
        self.ringer.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._allowed() and self.peripheral is None:
                self.peripheral = Peripheral(self.config, on_alert=self.on_alert)
                self.peripheral.start()
                if self.peripheral.error:
                    logger.warning("phone remote: %s", self.peripheral.error)
                serve(self.peripheral)
            elif not self._allowed() and self.peripheral is not None:
                self._down()
            self._stop.wait(self.check_seconds)
