"""A question asked while the window is hidden arrives as a notification too.

Before 2026-10-15 "may I run pytest in ~/shop?" showed only in the window, so a
turn sat blocked until someone happened to look. Then the first version used
`Gio.Notification`, and GNOME never showed it - it needs a desktop file named
after the app id, and Chronoa's is `shani-chronoa.desktop` (measured in the
demo recorder: posted, never on screen). So it is `org.freedesktop.Notifications`,
the route `incoming_call.py` already uses.

Driven against a fake notification server in its own process on a private bus:
what Chronoa posted, what pressing a button does, and that it is closed after.
"""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa.app.application import ChronoaApplication as App  # noqa: E402

_SERVER = r'''
import sys, json
from gi.repository import Gio, GLib
XML = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s"/><arg type="u"/><arg type="s"/><arg type="s"/><arg type="s"/>
<arg type="as"/><arg type="a{sv}"/><arg type="i"/><arg type="u" direction="out"/></method>
<method name="CloseNotification"><arg type="u"/></method>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal></interface></node>"""
node = Gio.DBusNodeInfo.new_for_xml(XML)
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def call(conn, sender, path, iface, method, params, inv):
    if method == "Notify":
        a = params.unpack()
        print(json.dumps({"notify": {"summary": a[3], "body": a[4], "actions": a[5]}}), flush=True)
        inv.return_value(GLib.Variant("(u)", (7,)))
    else:
        print(json.dumps({"close": params.unpack()[0]}), flush=True)
        inv.return_value(None)
bus.register_object("/org/freedesktop/Notifications", node.interfaces[0], call)
bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
              "RequestName", GLib.Variant("(su)", ("org.freedesktop.Notifications", 0)),
              None, 0, -1, None)
def press(ch, _c):
    key = sys.stdin.readline().strip()
    if key:
        bus.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                        "ActionInvoked", GLib.Variant("(us)", (7, key)))
        bus.flush_sync(None)
        return True
    loop.quit(); return False
GLib.io_add_watch(GLib.IOChannel.unix_new(0), GLib.IO_IN | GLib.IO_HUP, press)
print(json.dumps({"ready": True}), flush=True)
loop = GLib.MainLoop(); loop.run()
'''


class _Window:
    def __init__(self, active):
        self.active = active
        self.answered = []

    def is_active(self):
        return self.active

    def answer_pending_question(self, text):
        self.answered.append(text)
        return True


class _Stub:
    QUESTION_NOTIFICATION = App.QUESTION_NOTIFICATION
    _notify_question = App._notify_question
    _on_answer_question = App._on_answer_question
    _close_question_note = App._close_question_note

    #: A private connection per test: Gio's shared session connection never
    #: closes, and Gio.TestDBus.down() waits for every connection to go.
    connection = None

    def __init__(self, active):
        self.window = _Window(active)

    def get_dbus_connection(self):
        return _Stub.connection

    def activate_action(self, name, value):
        assert name == "answer-question"
        App._on_answer_question(self, None, value)


@pytest.fixture
def server():
    bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    bus.up()
    # The address passed explicitly: TestDBus sets it in the C environment,
    # which os.environ does not see - the first version's server joined the
    # person's real session bus instead (where GNOME Shell holds the name).
    proc = subprocess.Popen([sys.executable, "-c", _SERVER], text=True,
                            env={**os.environ, "DBUS_SESSION_BUS_ADDRESS": bus.get_bus_address()},
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    seen = []
    threading.Thread(target=lambda: [seen.append(json.loads(l)) for l in proc.stdout],
                     daemon=True).start()
    end = time.monotonic() + 10
    while not seen and time.monotonic() < end:
        time.sleep(0.05)
    _Stub.connection = Gio.DBusConnection.new_for_address_sync(
        bus.get_bus_address(),
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    yield proc, seen
    proc.stdin.close()
    proc.wait(timeout=10)
    _Stub.connection.close_sync(None)
    _Stub.connection = None
    bus.down()


def _drain(until=lambda: False, seconds=3.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)


def _ask(app, options):
    done = threading.Event()
    wrapped = App._notifying_presenter(app, lambda q, o: done)
    assert wrapped("Run pytest in ~/shop?\n\nDetails.", options) is done
    return done


def test_a_hidden_window_is_asked_through_a_notification_whose_buttons_answer(server):
    proc, seen = server
    app = _Stub(active=False)
    done = _ask(app, ["Allow this once", "Deny"])
    _drain(lambda: any("notify" in s for s in seen))
    note = next(s["notify"] for s in seen if "notify" in s)
    assert note["summary"] == "Chronoa is asking" and note["body"] == "Run pytest in ~/shop?"
    assert note["actions"] == ["0", "Allow this once", "1", "Deny"]
    proc.stdin.write("0\n")
    proc.stdin.flush()
    _drain(lambda: app.window.answered)
    assert app.window.answered == ["Allow this once"], "the button did not answer the question"
    _drain(lambda: any("close" in s for s in seen))
    assert {"close": 7} in seen, "the notification was left up after it was answered"


def test_answering_in_the_window_closes_the_notification(server):
    _proc, seen = server
    app = _Stub(active=False)
    done = _ask(app, ["Allow this once", "Deny"])
    _drain(lambda: any("notify" in s for s in seen))
    done.set()
    _drain(lambda: any("close" in s for s in seen))
    assert {"close": 7} in seen


def test_a_window_in_front_gets_no_notification(server):
    _proc, seen = server
    _ask(_Stub(active=True), ["Allow this once", "Deny"])
    _drain(seconds=1.0)
    assert not any("notify" in s for s in seen)
