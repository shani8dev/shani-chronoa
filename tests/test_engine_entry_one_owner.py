"""The headless entry starts on a private bus, and the Agent has one owner.

Phase 5 gave the engine its own launcher, a D-Bus-activated systemd user unit
and a bus service file. The GTK app and the engine export the same
`dev.shani.chronoa.Agent` name, so the engine asks first: when a desktop app
already owns it, the engine stays a client instead of REPLACE-ing the name
back and forth with it. Both orders are driven here on a real private bus.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_BIN = _REPO / "usr/bin/shani-chronoa-engine"
_UNIT = _REPO / "usr/lib/systemd/user/shani-chronoa-engine.service"
_DBUS = _REPO / "usr/share/dbus-1/services/dev.shani.chronoa.Agent.service"


def _run_private_bus(
    script: str,
    timeout: int = 90,
    args: tuple = (),
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    if not shutil.which("dbus-run-session"):
        pytest.skip("dbus-run-session is not installed")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(extra_env or {})}
    return subprocess.run(
        ["dbus-run-session", "--", sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(_REPO),
        env=env,
    )


def _result(proc: subprocess.CompletedProcess) -> dict:
    line = next(
        (line for line in proc.stdout.splitlines() if line.startswith("RESULT ")), None
    )
    assert line is not None, f"nothing measured:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(line[len("RESULT ") :])


class TestTheEntryFiles:
    def test_launcher_runs_the_engine_not_the_gui(self):
        source = _BIN.read_text()
        assert "from shani_chronoa.engine import main" in source
        assert "ui_gtk" not in source
        assert os.access(_BIN, os.X_OK)

    def test_unit_is_dbus_activated_and_part_of_the_session(self):
        unit = _UNIT.read_text()
        assert "Type=dbus" in unit
        assert "BusName=dev.shani.chronoa.Agent" in unit
        assert "ExecStart=/usr/bin/shani-chronoa-engine" in unit
        assert "PartOf=graphical-session.target" in unit
        # A GTK desktop is the Agent there; starting this at login too would
        # make the two fight over the name before anyone called either.
        assert "WantedBy=" not in unit

    def test_dbus_service_points_at_the_unit(self):
        service = _DBUS.read_text()
        assert "Name=dev.shani.chronoa.Agent" in service
        assert "Exec=/usr/bin/shani-chronoa-engine" in service
        assert "SystemdService=shani-chronoa-engine.service" in service


def test_engine_owns_the_agent_and_answers_a_turn(tmp_path):
    """The real launcher owns the name; a stand-in model answers one Ask."""
    driver = tmp_path / "engine_with_stand_in.py"
    driver.write_text(
        f"""\
import sys
sys.path.insert(0, {str(_REPO / "usr/lib/shani-chronoa")!r})
sys.path.insert(0, {str(_REPO / "tests")!r})
import shani_chronoa.engine as engine
from test_tool_loop_continuity import _MockOllama, _final_reply
engine.OllamaLLM = lambda **kw: _MockOllama(
    [_final_reply("hello from a headless engine")]).build()
raise SystemExit(engine.main())
""",
        encoding="utf-8",
    )

    script = """
import json, os, subprocess, sys, time
from gi.repository import Gio, GLib

proc = subprocess.Popen([sys.executable, sys.argv[1]], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True,
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
proxy = Gio.DBusProxy.new_for_bus_sync(
    Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
    "dev.shani.chronoa.Agent", "/dev/shani/chronoa/Agent",
    "dev.shani.chronoa.Agent", None)
inventory = ""
for _ in range(80):
    time.sleep(0.25)
    try:
        inventory = proxy.call_sync(
            "Inventory", GLib.Variant("(s)", ("status",)),
            Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        break
    except Exception:
        pass
if not inventory:
    print("RESULT " + json.dumps({"error": "no Agent", "stderr": proc.stderr.read()[-800:]}))
    raise SystemExit(0)

events = []
loop = GLib.MainLoop()
def on_signal(_proxy, _sender, _signal, params):
    event = json.loads(params.unpack()[0])
    events.append(event)
    if event.get("event") == "done" and loop.is_running():
        loop.quit()
proxy.connect("g-signal", on_signal)
turn = proxy.call_sync("Turn", GLib.Variant("(s)", ("say hello",)),
                       Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
GLib.timeout_add_seconds(25, loop.quit)
loop.run()
proc.terminate()
try:
    proc.communicate(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.communicate()
print("RESULT " + json.dumps({
    "inventory": json.loads(inventory), "turn": turn,
    "events": [e.get("event") for e in events],
    "turns": sorted({e.get("turn") for e in events}),
}))
"""
    proc = _run_private_bus(f"{script}\n", timeout=120, args=(str(driver),))
    result = _result(proc)
    assert "error" not in result, result
    assert result["inventory"]["section"] == "status"
    assert result["turn"].startswith("turn-")
    assert "done" in result["events"]
    assert result["turns"] == [result["turn"]], "events from another turn"


def test_one_owner_in_both_orders():
    """desktop first then engine, and engine first then desktop."""
    script = """
import json, os
from gi.repository import Gio, GLib

from shani_chronoa.agent_service import AgentService, BUS_NAME

class FakeApp:
    pass

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def owner():
    return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "GetNameOwner",
        GLib.Variant("(s)", (BUS_NAME,)), None,
        Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

flags = Gio.BusNameOwnerFlags.REPLACE | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT
# The desktop side is a separate process in real life: give it a separate
# connection, or both owners carry the same unique name and the test cannot
# tell who won even when the name really changed hands.
desktop_bus = Gio.DBusConnection.new_for_address_sync(
    os.environ["DBUS_SESSION_BUS_ADDRESS"],
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
desktop = Gio.bus_own_name_on_connection(desktop_bus, BUS_NAME, flags, None, None)
first = owner()
engine = AgentService(FakeApp())
yielded = engine.export(yield_to_owner=True)
still = owner()
Gio.bus_unown_name(desktop)

headless = AgentService(FakeApp())
assert headless.export(yield_to_owner=True) is True
second = owner()
desktop_again = Gio.bus_own_name_on_connection(desktop_bus, BUS_NAME, flags, None, None)
third = owner()
print("RESULT " + json.dumps({
    "desktop_first": yielded is False and still == first,
    "engine_next": second != first,
    "desktop_takes_over": third != second,
}))
"""
    # The child needs the package on sys.path; dbus-run-session inherits it.
    script = (
        f"import sys\nsys.path.insert(0, {str(_REPO / 'usr/lib/shani-chronoa')!r})\n"
        + script
    )
    proc = _run_private_bus(script)
    result = _result(proc)
    assert all(result.values()), result


def test_calling_the_agent_name_activates_the_engine(tmp_path):
    """A client that only knows the bus name starts the headless entry.

    The installed file points at /usr/bin, which is not present on a dev
    box; the service is copied with this checkout's launcher as `Exec` so
    dbus-daemon can actually run it. The rest of the file is unchanged.
    """
    services = tmp_path / "dbus-1" / "services"
    services.mkdir(parents=True)
    service = services / "dev.shani.chronoa.Agent.service"
    service.write_text(
        _DBUS.read_text().replace("Exec=/usr/bin/shani-chronoa-engine", f"Exec={_BIN}"),
        encoding="utf-8",
    )
    script = """
import json, time
from gi.repository import Gio, GLib
proxy = Gio.DBusProxy.new_for_bus_sync(
    Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
    "dev.shani.chronoa.Agent", "/dev/shani/chronoa/Agent",
    "dev.shani.chronoa.Agent", None)
out = ""
for _ in range(80):
    time.sleep(0.25)
    try:
        out = proxy.call_sync("Inventory", GLib.Variant("(s)", ("status",)),
            Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        break
    except Exception:
        pass
print("RESULT " + json.dumps({"activated": bool(out),
                             "section": json.loads(out).get("section") if out else ""}))
"""
    proc = _run_private_bus(
        script,
        extra_env={
            "XDG_DATA_DIRS": f"{tmp_path}:{os.environ.get('XDG_DATA_DIRS', '/usr/share')}"
        },
    )
    result = _result(proc)
    assert result == {"activated": True, "section": "status"}, result
