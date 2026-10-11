"""Wayland input and the global shortcut, through xdg-desktop-portal - against a fake portal on a private bus.

The real portal shows a permission dialog a person must answer, and the test
slots have no compositor, so the protocol is proven here and the first real
run on a desktop is a person's. What this pins down: the Request/Response
flow, the restore token that keeps the dialog to once, a declined dialog
reported as declined, the exact keysyms and buttons sent, and a shortcut press
reaching the callback.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="needs dbus-daemon")

HERE = Path(__file__).resolve().parent


@pytest.fixture
def bus(tmp_path, monkeypatch):
    """A private session bus (never the user's) with the fake portal on it."""
    # Its own config, with no <servicedir>. `--session` loads the standard
    # session config, whose service directories let the bus *activate* the
    # host's real xdg-desktop-portal on first contact - so on a GNOME machine
    # "no portal" was answered by the real one ("ended CreateSession without
    # an answer (code 2)") and `test_no_portal_is_said_plainly` failed. A bus
    # that can only reach what this file starts is what "private" means.
    config = tmp_path / "bus.conf"
    config.write_text(
        "<!DOCTYPE busconfig PUBLIC '-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN' "
        "'http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd'>\n"
        "<busconfig><type>session</type>"
        f"<listen>unix:tmpdir={tmp_path}</listen>"
        "<policy context='default'><allow send_destination='*' eavesdrop='true'/>"
        "<allow eavesdrop='true'/><allow own='*'/></policy></busconfig>\n")
    daemon = subprocess.Popen(["dbus-daemon", f"--config-file={config}", "--print-address", "--nofork"],
                              stdout=subprocess.PIPE, text=True)
    address = daemon.stdout.readline().strip()
    log = tmp_path / "portal.log"
    procs = [daemon]

    def start(decline=""):
        env = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": address, "FAKE_PORTAL_LOG": str(log),
               "FAKE_PORTAL_DECLINE": decline}
        p = subprocess.Popen([sys.executable, str(HERE / "fake_portal.py")], env=env, stdout=subprocess.PIPE, text=True)
        assert p.stdout.readline().strip() == "READY"
        procs.append(p)

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    yield start, (lambda: [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else [])
    for p in reversed(procs):
        p.terminate()
        p.wait(timeout=5)


def _client():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    from shani_chronoa import portal
    conn = Gio.DBusConnection.new_for_address_sync(
        os.environ["DBUS_SESSION_BUS_ADDRESS"],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    return portal.PortalClient(connection=conn, timeout=5)


def test_typing_goes_through_the_request_flow_and_keeps_the_token(bus, tmp_path):
    from shani_chronoa import portal
    start, calls = bus
    start()
    with portal.RemoteInput(portal.KEYBOARD, client=_client()) as ri:
        ri.type_text("Hi!")
    methods = [c["method"] for c in calls()]
    assert methods[:3] == ["CreateSession", "SelectDevices", "Start"]
    keys = [c["args"][2:] for c in calls() if c["method"] == "NotifyKeyboardKeysym"]
    assert keys == [[ord("H"), 1], [ord("H"), 0], [ord("i"), 1], [ord("i"), 0], [ord("!"), 1], [ord("!"), 0]]
    token = tmp_path / "state/shani-chronoa/remote-desktop.token"
    assert token.read_text() == "tok-1" and oct(token.stat().st_mode)[-3:] == "600"
    # the second session offers the saved token, so the portal can skip the dialog
    with portal.RemoteInput(portal.KEYBOARD, client=_client()) as ri:
        ri.tap(portal.KEYSYMS["return"])
    selects = [c for c in calls() if c["method"] == "SelectDevices"]
    assert "restore_token" not in selects[0]["args"][1] and "restore_token" in selects[1]["args"][1]


def test_a_declined_dialog_is_reported_as_declined(bus):
    from shani_chronoa import portal
    start, calls = bus
    start(decline="Start")
    with pytest.raises(portal.PortalError, match="declined"):
        with portal.RemoteInput(portal.KEYBOARD, client=_client()):
            pass
    assert not [c for c in calls() if c["method"].startswith("Notify")], "nothing was typed after a no"


def test_chords_and_clicks(bus):
    from shani_chronoa import portal
    start, calls = bus
    start()
    with portal.RemoteInput(client=_client()) as ri:
        ri.chord([portal.keysym_for("ctrl"), portal.keysym_for("shift"), portal.keysym_for("t")])
        ri.click("right")
    keys = [c["args"][2:] for c in calls() if c["method"] == "NotifyKeyboardKeysym"]
    assert keys == [[0xFFE3, 1], [0xFFE1, 1], [ord("t"), 1], [ord("t"), 0], [0xFFE1, 0], [0xFFE3, 0]]
    assert [c["args"][2:] for c in calls() if c["method"] == "NotifyPointerButton"] == [[0x111, 1], [0x111, 0]]


def test_no_portal_is_said_plainly(bus):
    from shani_chronoa import portal
    with pytest.raises(portal.PortalError, match="does not offer RemoteDesktop"):
        with portal.RemoteInput(client=_client()):
            pass


def test_a_global_shortcut_press_reaches_the_callback(bus):
    from shani_chronoa import portal
    start, calls = bus
    start()
    pressed = []
    c = portal.bind_global_shortcut("talk", "Talk to Chronoa", "CTRL+ALT+space", lambda: pressed.append(1),
                                    client=_client())
    assert [x["method"] for x in calls()][:2] == ["CreateSession", "BindShortcuts"]
    from gi.repository import GLib
    c.bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                    "dev.shani.test.FakePortal", "TriggerShortcut", GLib.Variant("(s)", ("talk",)), None, 0, 2000, None)
    deadline = time.time() + 3
    while not pressed and time.time() < deadline:
        c.context.iteration(False)
        time.sleep(0.02)
    assert pressed == [1]


def test_keysyms():
    from shani_chronoa import portal
    assert portal.keysym_for("a") == 0x61 and portal.keysym_for("é") == 0xE9
    assert portal.keysym_for("€") == 0x01000000 + 0x20AC and portal.keysym_for("F5") == 0xFFC2
    with pytest.raises(ValueError):
        portal.keysym_for("notakey")


def test_the_app_binds_the_shortcut_and_a_press_toggles_listening(bus):
    """The real ChronoaApplication method, on a stand-in app object, against the fake portal."""
    from types import SimpleNamespace
    from gi.repository import GLib
    from shani_chronoa.app import ChronoaApplication
    start, calls = bus
    start()
    toggled = []

    class Config:
        def get_bool(self, key, default=False):
            return key == "global-shortcut-enabled"

        def get(self, key, default=""):
            return "CTRL+ALT+space"

    app = SimpleNamespace(config=Config(), activate_action=lambda name, arg: toggled.append(name))
    from gi.repository import Gio
    private = Gio.DBusConnection.new_for_address_sync(
        os.environ["DBUS_SESSION_BUS_ADDRESS"],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    # Always the private bus: never the session-bus singleton, which may be the user's real one.
    ChronoaApplication._start_global_shortcut(app, connection=private)
    deadline = time.time() + 5
    while not any(c["method"] == "BindShortcuts" for c in calls()) and time.time() < deadline:
        time.sleep(0.05)
    bind = next(c for c in calls() if c["method"] == "BindShortcuts")
    assert "talk" in str(bind["args"])
    time.sleep(0.3)
    subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop", "--object-path",
                    "/org/freedesktop/portal/desktop", "--method", "dev.shani.test.FakePortal.TriggerShortcut", "talk"],
                   check=True, capture_output=True)
    ctx = GLib.MainContext.default()
    while not toggled and time.time() < deadline + 3:
        ctx.iteration(False)
        time.sleep(0.02)
    assert toggled == ["toggle-listening"]
    ChronoaApplication._stop_global_shortcut(app)
    assert not app._shortcut_thread.is_alive(), "the shortcut thread must stop when the app quits"


def test_the_shortcut_stays_off_by_default():
    from types import SimpleNamespace
    from shani_chronoa.app import ChronoaApplication

    class Off:
        def get_bool(self, key, default=False):
            return False
    app = SimpleNamespace(config=Off())
    ChronoaApplication._start_global_shortcut(app)
    assert getattr(app, "_shortcut_thread", None) is None


def test_hold_to_talk_release_reaches_its_own_callback(bus):
    from shani_chronoa import portal
    start, calls = bus
    start()
    pressed, released = [], []
    c = portal.bind_global_shortcut("talk", "Talk", "CTRL+ALT+space", lambda: pressed.append(1),
                                    client=_client(), on_deactivated=lambda: released.append(1))
    from gi.repository import GLib
    for method in ("TriggerShortcut", "ReleaseShortcut"):
        c.bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                        "dev.shani.test.FakePortal", method, GLib.Variant("(s)", ("talk",)), None, 0, 2000, None)
    deadline = time.time() + 3
    while not (pressed and released) and time.time() < deadline:
        c.context.iteration(False)
        time.sleep(0.02)
    assert pressed == [1] and released == [1]


def test_only_a_held_shortcut_ends_listening_on_release():
    from types import SimpleNamespace
    from shani_chronoa.app import ChronoaApplication
    ended = []
    app = SimpleNamespace(_listening=True, recorder=SimpleNamespace(cancel_auto_stop=lambda: ended.append(1)))
    ChronoaApplication._end_listening_now(app)
    assert ended == [1]
    app._listening = False
    ChronoaApplication._end_listening_now(app)
    assert ended == [1], "nothing to end when not listening"


# --- every method named here has to exist on a real portal ------------------------
#
# Two method names in this file were invented against a real interface and cost a
# whole session of work: `NotifyPointerScroll`, which the portal does not have at
# all (it has `NotifyPointerAxis` / `NotifyPointerAxisDiscrete`), and
# `NotifyPointerMotion` used as if it were absolute when it is the *relative*
# method. Nothing about either is checkable by reading - the call is well formed
# and the code is correct-looking. Only the interface itself knows.

#: `gdbus introspect --session --dest org.freedesktop.portal.Desktop
#: --object-path /org/freedesktop/portal/desktop --recurse`, restricted to
#: `org.freedesktop.portal.RemoteDesktop`, on this machine (2026-10-11).
#: Recorded rather than introspected live, so the suite needs no session bus and
#: no portal to hold the one method name that was wrong.
REMOTE_DESKTOP_METHODS = {
    "ConnectToEIS", "CreateSession", "NotifyKeyboardKeycode",
    "NotifyKeyboardKeysym", "NotifyPointerAxis", "NotifyPointerAxisDiscrete",
    "NotifyPointerButton", "NotifyPointerMotion", "NotifyPointerMotionAbsolute",
    "NotifyTouchDown", "NotifyTouchMotion", "NotifyTouchUp", "SelectDevices",
    "Start",
}


def test_every_remote_desktop_method_called_actually_exists() -> None:
    import re
    from shani_chronoa import portal

    source = Path(portal.__file__).read_text()
    called = set(re.findall(r'RD_IFACE,\s*"([A-Za-z]+)"', source))

    assert called, "the scan found nothing, so it would pass whatever the code did"
    unknown = called - REMOTE_DESKTOP_METHODS
    assert not unknown, (
        "%s %s not on org.freedesktop.portal.RemoteDesktop - a well-formed call "
        "to a method the portal does not have fails at run time, on the one "
        "route that is supposed to make failures visible"
        % (sorted(unknown), "is" if len(unknown) == 1 else "are"))


def test_move_sends_the_difference_from_where_the_pointer_is(monkeypatch) -> None:
    """**The relative method is the only drivable one, so `move` sends a delta.**

    Measured on this desktop, `NotifyPointerMotion(214, 874)` with the pointer
    at (688, 1079) left it at (902, 1079) - and y never moved at all, because it
    was already clamped at the bottom edge of the screen. So every "click on a
    sidebar row" was a click *at the bottom of the screen*, reported delivered.
    """
    from shani_chronoa import portal

    class _Client:
        GLib = None

        def call(self, _iface, method, _sig, args):
            self.calls.append((method, args))

    client = _Client()
    client.calls = []
    monkeypatch.setattr(portal, "pointer_position", lambda: (688, 1079))

    ri = portal.RemoteInput(client=client)
    ri.session = "/session/1"
    ri.move(214.0, 874.0)

    assert client.calls == [("NotifyPointerMotion",
                             ("/session/1", {}, -474.0, -205.0))], client.calls


def test_move_refuses_rather_than_nudging_when_the_pointer_cannot_be_read(
        monkeypatch) -> None:
    """Sending the coordinates as if they were a delta is the original bug."""
    from shani_chronoa import portal

    class _Client:
        GLib = None

        def call(self, *_a):
            raise AssertionError("a motion was sent with nowhere to move from")

    monkeypatch.setattr(portal, "pointer_position", lambda: None)

    ri = portal.RemoteInput(client=_Client())
    ri.session = "/session/1"

    with pytest.raises(portal.PortalError, match="relatively"):
        ri.move(214.0, 874.0)


def test_scroll_uses_the_axis_method_the_portal_actually_has(monkeypatch) -> None:
    """`NotifyPointerScroll` is not a method. It was called anyway, and the call
    was well formed, which is why only running it could have told."""
    from shani_chronoa import portal

    class _Client:
        GLib = None

        def call(self, _iface, method, _sig, args):
            self.calls.append((method, args))

    client = _Client()
    client.calls = []
    ri = portal.RemoteInput(client=client)
    ri.session = "/session/1"

    ri.scroll(0.0, -4.0)

    assert client.calls == [("NotifyPointerAxis", ("/session/1", {}, 0.0, -4.0))]
