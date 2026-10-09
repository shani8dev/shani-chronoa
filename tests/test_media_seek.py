"""media_control restart/seek against a fake MPRIS player on a *private* bus.

The real `gdbus` is driven, so the argument text the skill hands it
(`int64 -10000000`, `objectpath '...'`) is parsed by the real tool - the part a
fake gdbus would have taken on faith. The player is a small Gio program that
implements the MPRIS Player methods and logs each call, on a dbus-daemon this
test starts and stops by its own recorded PID. DBUS_SESSION_BUS_ADDRESS points
at that daemon for the whole test, so the user's real players are never seen,
let alone moved.
"""

import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

from shani_chronoa.skills import media_control as mc

pytestmark = pytest.mark.skipif(not (shutil.which("dbus-daemon") and shutil.which("gdbus")),
                                reason="needs dbus-daemon and gdbus")

PLAYER = textwrap.dedent('''
    import os, sys
    from gi.repository import Gio, GLib
    LOG = sys.argv[1]
    CAN_SEEK = os.environ.get("FAKE_CAN_SEEK", "1") == "1"
    OBEY = os.environ.get("FAKE_OBEY", "1") == "1"
    TRACK = os.environ.get("FAKE_TRACK", "/org/fake/track/7")
    pos = [int(os.environ.get("FAKE_POS", "60000000"))]
    XML = """<node>
      <interface name="org.mpris.MediaPlayer2"><property name="Identity" type="s" access="read"/></interface>
      <interface name="org.mpris.MediaPlayer2.Player">
        <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
        <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/>
                                   <arg name="Position" type="x" direction="in"/></method>
        <method name="Next"/><method name="Previous"/><method name="Play"/><method name="Pause"/>
        <method name="PlayPause"/><method name="Stop"/>
        <property name="PlaybackStatus" type="s" access="read"/>
        <property name="Metadata" type="a{sv}" access="read"/>
        <property name="Position" type="x" access="read"/>
        <property name="CanSeek" type="b" access="read"/>
      </interface></node>"""
    def log(line):
        with open(LOG, "a") as f:
            f.write(line + "\\n")
    def call(conn, sender, path, iface, method, params, inv):
        log(method + " " + params.print_(True))
        if method == "Seek" and OBEY:
            pos[0] = max(0, pos[0] + params.unpack()[0])
        elif method == "SetPosition" and OBEY and params.unpack()[0] == TRACK:
            pos[0] = params.unpack()[1]
        inv.return_value(None)
    def get(conn, sender, path, iface, prop):
        if prop == "Identity": return GLib.Variant("s", "Fake Player")
        if prop == "PlaybackStatus": return GLib.Variant("s", "Playing")
        if prop == "CanSeek": return GLib.Variant("b", CAN_SEEK)
        if prop == "Position": return GLib.Variant("x", pos[0])
        meta = {"xesam:title": GLib.Variant("s", "Song")}
        if TRACK:
            meta["mpris:trackid"] = GLib.Variant("o", TRACK)
        return GLib.Variant("a{sv}", meta)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    info = Gio.DBusNodeInfo.new_for_xml(XML)
    for iface in info.interfaces:
        bus.register_object("/org/mpris/MediaPlayer2", iface, call, get, None)
    Gio.bus_own_name_on_connection(bus, "org.mpris.MediaPlayer2.fakeplayer", 0, None, None)
    GLib.MainLoop().run()
''')


@pytest.fixture
def private_bus(tmp_path, monkeypatch):
    real = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1",
                               f"--address=unix:path={tmp_path}/bus"],
                              stdout=subprocess.PIPE, text=True)
    address = daemon.stdout.readline().strip()
    assert address and address != real
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    started = []

    def start_player(**env):
        script = tmp_path / "player.py"
        script.write_text(PLAYER)
        log = tmp_path / "calls.log"
        full = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address, **env)
        proc = subprocess.Popen([sys.executable, str(script), str(log)], env=full)
        started.append(proc)
        for _ in range(100):
            if mc.players():
                break
            time.sleep(0.05)
        assert mc.players() == ["org.mpris.MediaPlayer2.fakeplayer"]
        return lambda: log.read_text().splitlines() if log.exists() else []

    yield start_player
    for proc in started + [daemon]:   # recorded PIDs only
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    daemon.stdout.close()


def test_seek_ahead_moves_and_reports_the_new_position(private_bus):
    calls = private_bus()
    assert mc._run({"action": "seek", "seconds": 30}) == "Moved 30 s ahead in Fake Player (now at 1:30)."
    assert calls() == ["Seek (int64 30000000,)"]


def test_seek_back_takes_a_negative_number(private_bus):
    calls = private_bus()
    assert mc._run({"action": "seek", "seconds": "-10"}) == "Moved 10 s back in Fake Player (now at 0:50)."
    assert calls() == ["Seek (int64 -10000000,)"]


def test_restart_sets_position_zero_on_the_current_track(private_bus):
    calls = private_bus()
    assert mc._run({"action": "restart"}) == "Restarted the track in Fake Player (now at 0:00)."
    assert calls() == ["SetPosition (objectpath '/org/fake/track/7', int64 0)"]


def test_restart_without_a_track_id_seeks_back_past_the_start(private_bus):
    calls = private_bus(FAKE_TRACK="")
    assert mc._run({"action": "restart"}).startswith("Restarted the track")
    assert calls() == ["Seek (int64 -61000000,)"]


def test_control_a_player_that_ignores_the_seek_is_not_called_moved(private_bus):
    private_bus(FAKE_OBEY="0")
    out = mc._run({"action": "seek", "seconds": 30})
    assert "still reads 1:00" in out and not out.startswith("Moved")
    assert "did not go back to the start" in mc._run({"action": "restart"})


def test_a_player_that_cannot_seek_is_not_asked(private_bus):
    calls = private_bus(FAKE_CAN_SEEK="0")
    assert "does not allow seeking" in mc._run({"action": "seek", "seconds": 5})
    assert "does not allow seeking" in mc._run({"action": "restart"})
    assert calls() == []


def test_bad_seconds_are_refused_before_anything_is_sent(private_bus):
    calls = private_bus()
    assert "needs 'seconds'" in mc._run({"action": "seek"})
    assert "Could not use 'soon'" in mc._run({"action": "seek", "seconds": "soon"})
    assert "would not move" in mc._run({"action": "seek", "seconds": 0})
    assert "not seeking that far" in mc._run({"action": "seek", "seconds": 10 ** 9})
    assert "needs 'seconds'" in mc._run({"action": "seek", "seconds": True})
    assert calls() == []


def test_existing_actions_still_work_and_previous_is_previous(private_bus):
    calls = private_bus()
    assert mc._run({"action": "previous"}) == "Done: previous in Fake Player."
    assert mc._run({"action": "status"}) == "Fake Player is playing: Song."
    assert calls() == ["Previous ()"]


def test_unknown_and_malformed_actions():
    assert "restart, seek" in mc._run({"action": "rewind"})
    assert mc._run({"action": 4}).startswith("Unknown media action")
    assert mc._run(None) == "media_control needs an action."  # type: ignore[arg-type]
    enum = mc._SCHEMA["function"]["parameters"]["properties"]["action"]["enum"]
    assert {"restart", "seek", "previous", "status"} <= set(enum)
