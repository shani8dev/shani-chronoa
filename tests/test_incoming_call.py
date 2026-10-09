"""An incoming call is shown with Answer / Decline, and closed when it ends.

Driven over a private `dbus-daemon`, never the session bus: a stand-in
notification server and a stand-in PipeWire call object, with the watcher
seeing exactly the signals PipeWire emits (`CallAdded` on
org.ofono.VoiceCallManager, `PropertiesChanged` on the call).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa import incoming_call as ic  # noqa: E402

CALL = "/org/pipewire/Telephony/ag1/call1"
GATEWAY = "/org/pipewire/Telephony/ag1"
XML = """<node>
<interface name="org.freedesktop.Notifications">
  <method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
    <arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
    <arg type="u" direction="out"/></method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method>
</interface>
<interface name="org.pipewire.Telephony.Call1">
  <method name="Answer"/><method name="Hangup"/>
</interface>
</node>"""


@pytest.fixture()
def private_bus():
    proc = subprocess.Popen(["dbus-daemon", "--session", "--fork", "--print-address", "--print-pid"],
                            stdout=subprocess.PIPE, text=True)
    address, pid = proc.stdout.readline().strip(), proc.stdout.readline().strip()
    proc.wait()
    assert address and pid.isdigit()
    yield address
    os.kill(int(pid), 15)


def _connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


class Phone:
    """The notification server and the phone's call object, on one connection."""

    def __init__(self, address):
        self.bus = _connect(address)
        self.log = []
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.bus.register_object(ic.NOTIFY_PATH, node.lookup_interface(ic.NOTIFY_BUS), self.on_call)
        self.bus.register_object(CALL, node.lookup_interface(ic.CALL_IFACE), self.on_call)
        for name in (ic.NOTIFY_BUS, ic.TELEPHONY):
            self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                               "RequestName", GLib.Variant("(su)", (name, 4)), None, 0, 2000, None)

    def on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        self.log.append((method, params.unpack()))
        invocation.return_value(GLib.Variant("(u)", (7,)) if method == "Notify" else None)

    def emit(self, path, iface, signal, value):
        self.bus.emit_signal(None, path, iface, signal, value)
        self.bus.flush_sync(None)

    def ring(self, number="+919800000001"):
        self.emit(GATEWAY, "org.ofono.VoiceCallManager", "CallAdded", GLib.Variant(
            "(oa{sv})", (CALL, {"State": GLib.Variant("s", "incoming"),
                                "LineIdentification": GLib.Variant("s", number)})))

    def wait_for(self, method, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            hit = [p for m, p in self.log if m == method]
            if hit:
                return hit
            time.sleep(0.02)
        return []


class Config:
    def __init__(self, **keys):
        self.keys = keys

    def get_bool(self, key, default=False):
        return self.keys.get(key, default)


@pytest.fixture()
def phone_and_watcher(private_bus):
    phone = Phone(private_bus)
    watcher = ic.IncomingCallWatcher(Config(**{ic.CALL_KEY: True}), bus_factory=lambda: _connect(private_bus))
    watcher.start()
    yield phone, watcher
    watcher.stop()


def test_a_ringing_call_posts_answer_and_decline(phone_and_watcher):
    phone, _watcher = phone_and_watcher
    phone.ring()
    notify = phone.wait_for("Notify")
    assert notify, "no notification was posted for a ringing phone"
    _app, _replaces, _icon, title, _body, actions, hints, _timeout = notify[0]
    assert "+919800000001" in title and title.startswith("Incoming call")
    assert actions == ["answer", "Answer", "decline", "Decline"]
    assert hints["category"] == "call.incoming" and hints["urgency"] == 2


def test_pressing_answer_answers_that_call_and_closes_the_notification(phone_and_watcher):
    phone, watcher = phone_and_watcher
    phone.ring()
    assert phone.wait_for("Notify")
    phone.emit(ic.NOTIFY_PATH, ic.NOTIFY_BUS, "ActionInvoked", GLib.Variant("(us)", (7, "answer")))
    assert phone.wait_for("Answer"), phone.log
    assert phone.wait_for("CloseNotification"), phone.log
    assert not phone.wait_for("Hangup", 0.3)


def test_decline_hangs_up(phone_and_watcher):
    phone, _watcher = phone_and_watcher
    phone.ring()
    assert phone.wait_for("Notify")
    phone.emit(ic.NOTIFY_PATH, ic.NOTIFY_BUS, "ActionInvoked", GLib.Variant("(us)", (7, "decline")))
    assert phone.wait_for("Hangup"), phone.log
    assert not [m for m, _p in phone.log if m == "Answer"]


def test_a_call_answered_on_the_phone_closes_the_notification(phone_and_watcher):
    """The phone's own answer button must not leave a ringing card on the desktop."""
    phone, _watcher = phone_and_watcher
    phone.ring()
    assert phone.wait_for("Notify")
    phone.emit(CALL, "org.freedesktop.DBus.Properties", "PropertiesChanged", GLib.Variant(
        "(sa{sv}as)", (ic.CALL_IFACE, {"State": GLib.Variant("s", "active")}, [])))
    assert phone.wait_for("CloseNotification"), phone.log


def test_nothing_is_shown_while_the_switch_is_off(private_bus):
    phone = Phone(private_bus)
    watcher = ic.IncomingCallWatcher(Config(), bus_factory=lambda: _connect(private_bus))
    watcher.start()
    try:
        phone.ring()
        assert not phone.wait_for("Notify", 1.0), phone.log
    finally:
        watcher.stop()


def test_the_caller_is_named_from_the_contacts():
    title, body = ic.caller_label({"State": "incoming", "LineIdentification": "09800000001"},
                                  [("Asha Rao", ("+91 98000 00001",))])
    assert title == "Incoming call: Asha Rao" and body == "09800000001"
