"""Receiving a file over Bluetooth: asked first, saved safely, declined by default.

A private dbus-daemon stands in for obexd and the notification server.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))
pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa import obex_receive as rx  # noqa: E402

XML = """<node>
<interface name="org.bluez.obex.AgentManager1">
  <method name="RegisterAgent"><arg type="o" direction="in"/></method>
  <method name="UnregisterAgent"><arg type="o" direction="in"/></method></interface>
<interface name="org.freedesktop.DBus.Properties">
  <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method></interface>
<interface name="org.freedesktop.Notifications">
  <method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
    <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/></method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method></interface>
</node>"""
TRANSFER = "/org/bluez/obex/server/session1/transfer1"


@pytest.fixture()
def bus_address():
    proc = subprocess.Popen(["dbus-daemon", "--session", "--fork", "--print-address", "--print-pid"],
                            stdout=subprocess.PIPE, text=True)
    address, pid = proc.stdout.readline().strip(), proc.stdout.readline().strip()
    proc.wait()
    yield address
    os.kill(int(pid), 15)


def _connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)


class Obexd:
    def __init__(self, address, name):
        self.bus, self.log, self.name = _connect(address), [], name
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.bus.register_object("/org/bluez/obex", node.lookup_interface("org.bluez.obex.AgentManager1"), self.on)
        self.bus.register_object(TRANSFER, node.lookup_interface("org.freedesktop.DBus.Properties"), self.on)
        self.bus.register_object(rx.NOTIFY_PATH, node.lookup_interface(rx.NOTIFY), self.on)
        for n in (rx.OBEX, rx.NOTIFY):
            self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                               "RequestName", GLib.Variant("(su)", (n, 4)), None, 0, 2000, None)

    def on(self, _c, _s, _path, _iface, method, params, inv):
        self.log.append((method, params.unpack()))
        if method == "GetAll":
            inv.return_value(GLib.Variant("(a{sv})", ({"Name": GLib.Variant("s", self.name),
                                                       "Size": GLib.Variant("t", 2048)},)))
        elif method == "Notify":
            inv.return_value(GLib.Variant("(u)", (5,)))
        else:
            inv.return_value(None)

    def pump(self, until, seconds=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            GLib.MainContext.default().iteration(False)
            if until():
                return True
            time.sleep(0.02)
        return False

    def push(self):
        """obexd's AuthorizePush, answered later; returns a box with the reply."""
        box = {}

        def done(conn, res):
            try:
                box["ok"] = conn.call_finish(res).unpack()[0]
            except GLib.Error as exc:
                box["err"] = exc.message
        self.bus.call(self.owner, rx.AGENT_PATH, rx.AGENT_IFACE, "AuthorizePush",
                      GLib.Variant("(o)", (TRANSFER,)), None, 0, 10000, None, done)
        return box

    def press(self, action):
        self.bus.emit_signal(None, rx.NOTIFY_PATH, rx.NOTIFY, "ActionInvoked", GLib.Variant("(us)", (5, action)))
        self.bus.flush_sync(None)


class Config:
    def __init__(self, on):
        self.on = on

    def get_bool(self, key, default=False):
        return self.on and key == rx.KEY


def _run(bus_address, tmp_path, monkeypatch, name, action, on=True):
    (tmp_path / "stage").mkdir(exist_ok=True)
    (tmp_path / "dl").mkdir(exist_ok=True)
    monkeypatch.setattr(rx, "obex_root", lambda: str(tmp_path / "stage"))
    monkeypatch.setattr(rx, "downloads", lambda: str(tmp_path / "dl"))
    obexd = Obexd(bus_address, name)
    receiver = rx.ObexReceiver(Config(on), bus_factory=lambda: _connect(bus_address))
    receiver.start()
    try:
        assert obexd.pump(lambda: any(m == "RegisterAgent" for m, _ in obexd.log)), obexd.log
        obexd.owner = receiver.bus.get_unique_name()   # obexd calls back whoever registered
        box = obexd.push()
        if on:
            assert obexd.pump(lambda: any(m == "Notify" for m, _ in obexd.log)), obexd.log
            obexd.press(action)
        obexd.pump(lambda: box)
        return box, obexd
    finally:
        receiver.stop()


def test_accept_stages_in_obexds_folder_then_delivers_without_overwriting(bus_address, tmp_path, monkeypatch):
    """obexd refuses any path outside its root (measured: EPERM, then Forbidden)."""
    box, obexd = _run(bus_address, tmp_path, monkeypatch, "photo.jpg", "accept")
    staged = tmp_path / "stage" / "photo.jpg"
    assert box.get("ok") == str(staged), box
    staged.write_text("the bytes obexd wrote")
    (tmp_path / "dl" / "photo.jpg").write_text("already here")
    final = rx.ObexReceiver.deliver(str(staged))
    assert final == str(tmp_path / "dl" / "photo (2).jpg") and not staged.exists()
    assert (tmp_path / "dl" / "photo.jpg").read_text() == "already here"
    title, body = [p for m, p in obexd.log if m == "Notify"][0][3:5]
    assert title == "Receive a file?" and "photo.jpg" in body


def test_decline_refuses_the_file(bus_address, tmp_path, monkeypatch):
    box, _ = _run(bus_address, tmp_path, monkeypatch, "photo.jpg", "decline")
    assert "declined" in box.get("err", ""), box


def test_a_name_with_a_path_cannot_leave_downloads(bus_address, tmp_path, monkeypatch):
    box, _ = _run(bus_address, tmp_path, monkeypatch, "../../.bashrc", "accept")
    assert box.get("ok") == str(tmp_path / "stage" / "bashrc"), box


def test_the_agent_slot_is_left_alone_while_the_switch_is_off(bus_address, tmp_path, monkeypatch):
    """obexd has one agent slot. Holding it while switched off rejected every
    incoming file and kept the desktop's own receiver out."""
    obexd = Obexd(bus_address, "photo.jpg")
    receiver = rx.ObexReceiver(Config(False), bus_factory=lambda: _connect(bus_address))
    receiver.start()
    try:
        assert not obexd.pump(lambda: any(m == "RegisterAgent" for m, _ in obexd.log), seconds=1.5)
        assert not receiver.registered
    finally:
        receiver.stop()


def test_the_switch_takes_and_releases_the_slot(bus_address, tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "SWITCH_POLL_SECONDS", 1)
    config = Config(False)
    obexd = Obexd(bus_address, "photo.jpg")
    receiver = rx.ObexReceiver(config, bus_factory=lambda: _connect(bus_address))
    receiver.start()
    try:
        config.on = True
        assert obexd.pump(lambda: any(m == "RegisterAgent" for m, _ in obexd.log), seconds=4), obexd.log
        config.on = False
        assert obexd.pump(lambda: any(m == "UnregisterAgent" for m, _ in obexd.log), seconds=4), obexd.log
    finally:
        receiver.stop()


def test_a_file_offered_after_switching_off_is_refused(bus_address, tmp_path, monkeypatch):
    """Between switching off and the next poll the agent is still registered;
    the per-file check refuses in that window."""
    config = Config(True)
    obexd = Obexd(bus_address, "photo.jpg")
    receiver = rx.ObexReceiver(config, bus_factory=lambda: _connect(bus_address))
    receiver.start()
    try:
        assert obexd.pump(lambda: any(m == "RegisterAgent" for m, _ in obexd.log)), obexd.log
        obexd.owner = receiver.bus.get_unique_name()
        config.on = False
        box = obexd.push()
        obexd.pump(lambda: box)
        assert "is off" in box.get("err", ""), box
        assert not [m for m, _ in obexd.log if m == "Notify"]
    finally:
        receiver.stop()
