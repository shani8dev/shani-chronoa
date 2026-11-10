"""Where the device panels went: one "Shani Link" row, and Connect a device.

Chronoa's Phone, Phone link, Screen share, Devices, Wearables and Android panels
and its Connect a device window moved to shani-link's own app on 2026-11-04.
What stays here is the way to them: a row in the sidebar's "Phone and remote"
group that starts `shani-link-app`, and the `connect-device` action (menu,
palette) starting it on Connect a device. Nothing is actually started here:
`Gio.Subprocess.new` is recorded, never run.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import link_app  # noqa: E402


def test_the_command_carries_the_page_and_the_connect_route():
    assert link_app.argv() == ["shani-link-app"]
    assert link_app.argv(page="watch") == ["shani-link-app", "--page=watch"]
    assert link_app.argv(connect="bluetooth") == ["shani-link-app", "--connect=bluetooth"]
    assert link_app.argv(connect="") == ["shani-link-app", "--connect"], "\"\" is the chooser"


def test_not_installed_is_said_and_nothing_is_started(monkeypatch):
    from gi.repository import Gio
    monkeypatch.setattr(link_app.shutil, "which", lambda name: None)
    monkeypatch.setattr(Gio.Subprocess, "new", lambda *a: pytest.fail("started an app that is not there"))
    ok, why = link_app.open_app(connect="android")
    assert not ok and "not installed" in why and "shani-link" in why


def test_installed_starts_the_app_with_the_route(monkeypatch):
    from gi.repository import Gio
    started = []
    monkeypatch.setattr(link_app.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(Gio.Subprocess, "new", lambda argv, flags: started.append(argv))
    assert link_app.open_app(connect="kdeconnect") == (True, "")
    assert started == [["shani-link-app", "--connect=kdeconnect"]]


def test_the_connect_device_action_opens_shani_link(monkeypatch):
    """The menu's and the palette's "Connect a device" still work: they open the
    window where it lives now, on the route they named."""
    from shani_chronoa.app.application import ChronoaApplication
    asked = []
    monkeypatch.setattr(link_app, "open_app", lambda page="", connect=None: asked.append(connect) or (True, ""))
    app = ChronoaApplication.__new__(ChronoaApplication)
    ChronoaApplication._open_connect_device(app, "bluetooth")
    assert asked == ["bluetooth"]
    said = []
    monkeypatch.setattr(link_app, "open_app", lambda page="", connect=None: (False, "Shani Link is not installed"))
    app._set_status = said.append
    ChronoaApplication._open_connect_device(app, "")
    assert said == ["Shani Link is not installed"]


def test_no_moved_panel_is_left_in_the_registry():
    from shani_chronoa.gui import surfaces
    moved = {"phone", "phone_link", "screen_share", "devices", "wearables", "android"}
    assert not moved & set(surfaces.SURFACE_IDS)
    assert not moved & set(surfaces.SETTINGS_TARGETS)


_HARNESS = r'''
import json, os, sys
sys.path.insert(0, os.environ["CHRONOA_LIB"])
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk
Adw.init()
from shani_chronoa import link_app
link_app.available = lambda: True
started = []
Gio.Subprocess.new = staticmethod(lambda argv, flags: started.append(argv))
from shani_chronoa.gui.sidebar import SidebarPage
from shani_chronoa.gui.surfaces import SECTION_PHONE
page = SidebarPage(None, lambda: None, lambda _key: None)
row = page.shani_link_row
group = page.groups[SECTION_PHONE]
inside = False
w = row.get_parent()
while w is not None:
    inside = inside or w is group
    w = w.get_parent()
row.emit("activated")
print("RESULT " + json.dumps({"title": row.get_title(), "subtitle": row.get_subtitle(),
                              "in_group": inside, "started": started,
                              "sensitive": row.get_sensitive()}))
'''


@pytest.fixture(scope="module")
def broadway():
    """A private Broadway display, so no window appears on anybody's desktop."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        pytest.skip("no XDG_RUNTIME_DIR, so no headless display")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _broadway import start_display
    number, proc = start_display(runtime, 40, 2620)
    # **The display is verified before it is handed back**, and a number that
    # does not work is tried again: a daemon that never came up leaves a
    # fixture yielding a display that does not exist, which reads as broken
    # panels ("the harness produced nothing") rather than as a broken
    # environment. Measured on a CI runner (2026-11-10): 39 panel tests
    # failed that way. See `tests/_broadway.py::start_display`.
    try:
        yield number
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_the_sidebar_row_opens_shani_link_from_phone_and_remote(broadway, tmp_path):
    script = tmp_path / "harness.py"
    script.write_text(_HARNESS)
    env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{broadway}",
               PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               CHRONOA_LIB=str(ROOT / "usr" / "lib" / "shani-chronoa"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          timeout=120, env=env)
    out = next((json.loads(line[7:]) for line in done.stdout.splitlines()
                if line.startswith("RESULT ")), None)
    assert out is not None, f"no result:\n{done.stdout[-1500:]}\n{done.stderr[-3000:]}"
    assert out["title"] == "Shani Link" and out["in_group"], out
    assert out["sensitive"] is True
    assert out["started"] == [["shani-link-app"]], out
