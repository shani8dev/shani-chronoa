"""The UI-driving skills, against a real window, in a private headless GNOME Shell.

Measured 2026-10-15 before the fixes, in this shell: type_text, move_pointer and
click_pointer reached nothing (wtype was given `-M hello`, a modifier named
"hello"; it has no pointer at all), press_key needed a portal the session did
not have, and ui_elements wanted an element id and an app's program name.

Every assertion is on what the *window* received - the target app prints each
change - never on what a skill said about itself.

A pointer move goes across, then down: diagonally out of the hot corner at
(0,0) it pushed the corner's pressure barrier and opened the Activities
overview, and every click after that landed on the overview.
"""
import importlib.util
import json
import shutil
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("live", _REPO / "tests" / "test_windows_gnome_live.py")
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

pytestmark = live.pytestmark

_TARGET = """
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
app = Gtk.Application(application_id="org.demo.UiTarget")
def go(a):
    w = Gtk.ApplicationWindow(application=a, title="UI target")
    w.set_default_size(400, 240)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=20,
                  margin_start=20, margin_end=20)
    entry = Gtk.Entry(placeholder_text="Name")
    entry.connect("changed", lambda e: print("TEXT", e.get_text(), flush=True))
    button = Gtk.Button(label="Save")
    button.connect("clicked", lambda b: print("CLICKED Save", flush=True))
    box.append(entry); box.append(button); w.set_child(box); w.present()
    entry.grab_focus()
app.connect("activate", go)
app.run([])
"""

_RUN = r'''
import sys, json, logging
sys.path.insert(0, "usr/lib/shani-chronoa"); logging.disable(50)
from shani_chronoa import tools
name, args = json.loads(sys.argv[1])
print(str(tools.execute_tool(name, args)))
'''


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    root = tmp_path_factory.mktemp("uiskills")
    shell = live._Shell(root, monitor="1280x720")
    seen: list = []
    try:
        shell.extension(True)
        gui = {**shell.env, "WAYLAND_DISPLAY": "wl-0", "GDK_BACKEND": "wayland"}
        (root / "target.py").write_text(_TARGET)
        app = subprocess.Popen([sys.executable, str(root / "target.py")], env=gui,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=lambda: [seen.append(l.strip()) for l in app.stdout],
                         daemon=True).start()
        live._Shell._wait(lambda: "UI target" in shell.windows(), 20, "the target window did not open")
        time.sleep(4)  # past the shell's startup animation, which re-opens the overview
        cfg = shell.home / ".config" / "glib-2.0" / "settings"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "keyfile").write_text("[org/shani/chronoa]\ninput-control-enabled=true\n")
        schemas = root / "schemas"
        schemas.mkdir()
        for xml in (_REPO / "usr/share/glib-2.0/schemas").glob("*.gschema.xml"):
            (schemas / xml.name).write_text(xml.read_text())
        subprocess.run(["glib-compile-schemas", str(schemas)], check=True)
        env = {**gui, "XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME",
               "GSETTINGS_BACKEND": "keyfile", "GSETTINGS_SCHEMA_DIR": str(schemas),
               "PATH": os.environ["PATH"]}

        def skill(_tool, **args):
            before = len(seen)
            out = subprocess.run([sys.executable, "-c", _RUN, json.dumps([_tool, args])], env=env,
                                 cwd=_REPO, capture_output=True, text=True, timeout=120).stdout
            time.sleep(1.0)
            return out, seen[before:]
        skill.env = env
        yield skill
        app.terminate()
    finally:
        shell.stop()


def test_focus_then_typing_and_keys_reach_the_window(desk):
    said, _ = desk("focus_window", title_contains="UI target")
    assert "Focused" in said, said
    said, got = desk("type_text", text="hello, World! 42")
    assert got and got[-1] == "TEXT hello, World! 42", (said, got)
    # Read back from the field, not taken from the skill's word.
    # (A verified result carries no suffix; it used to read "unverified".)
    assert "unverified" not in said and "VERIFICATION FAILED" not in said, said
    _, got = desk("press_key", key="a")
    assert got and got[-1] == "TEXT hello, World! 42a", got
    desk("press_key", key="Tab")
    _, got = desk("press_key", key="Return")
    assert "CLICKED Save" in got, got


def test_ui_elements_by_window_title_and_by_name(desk):
    said, got = desk("ui_elements", action="press", app="UI target", query="Save")
    assert "CLICKED Save" in got, said
    said, got = desk("ui_elements", action="set_text", app="UI target", text="by name")
    assert got and got[-1] == "TEXT by name", said
    said, _ = desk("ui_elements", action="press", app="UI target", query="Nope")
    assert "nothing is named 'nope'" in said, said


def test_a_click_at_coordinates_presses_the_button(desk):
    """Pointer to the Save button (window 400x240 at 440,256), then a click."""
    desk("focus_window", title_contains="UI target")
    desk("move_pointer", x=640, y=381)
    said, got = desk("click_pointer", button="left")
    assert "CLICKED Save" in got, (said, got)


def test_the_pointer_lands_where_it_was_sent(desk):
    """`PointerMove` re-reads the pointer and fails when it is not there, so a
    "done" without "failed" is a landing that was checked, not assumed."""
    said, _ = desk("move_pointer", x=300, y=200)
    assert "done" in said and "failed" not in said, said


_IBUS_HOST = r'''
import sys, logging
sys.path.insert(0, "usr/lib/shani-chronoa"); logging.disable(50)
from gi.repository import GLib
from shani_chronoa import ibus_input
print("STARTED", ibus_input.start(), flush=True)
GLib.MainLoop().run()
'''


def test_text_no_key_can_type_arrives_through_ibus(desk):
    """Devanagari has no key to press: it is committed through Chronoa's IBus
    engine (ibus_input.py), the way dictation tools insert text."""
    env = desk.env
    probe = subprocess.run([sys.executable, "-c",
                            "import gi; gi.require_version('IBus','1.0'); from gi.repository import IBus; "
                            "print(IBus.get_address() or '')"], env=env, capture_output=True, text=True)
    address = probe.stdout.strip()
    if not address or os.path.expanduser("~/.cache/ibus") in address:
        pytest.skip("no IBus in the private session (never the person's own)")
    host = subprocess.Popen([sys.executable, "-c", _IBUS_HOST], env=env, cwd=_REPO,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert host.stdout.readline().strip() == "STARTED True"
        desk("focus_window", title_contains="UI target")
        desk("ui_elements", action="set_text", app="UI target", text="")
        # An earlier test pressed Tab: focus is on Save, and a commit goes to
        # whatever holds focus - so back to the field first.
        desk("press_key", key="shift+Tab")
        said, got = desk("type_text", text="नमस्ते café")
        assert "IBus" in said, said
        assert got and got[-1] == "TEXT नमस्ते café", (said, got)
    finally:
        host.terminate()


def test_open_application_waits_for_the_window(desk):
    """Opened means its window is on screen - not that a launch was requested."""
    if not shutil.which("gnome-calculator"):
        pytest.skip("gnome-calculator is not installed")
    said, _ = desk("open_application", name="Calculator")
    assert said.startswith("Opened Calculator: its window"), said
    assert "Bank of Canada" not in said and "Fontconfig" not in said, "the app's own log leaked in"


_FAKE_CHRONOA_WINDOW = """
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
app = Gtk.Application(application_id="dev.shani.chronoa")
def go(a):
    w = Gtk.ApplicationWindow(application=a, title="Shani Chronoa")
    e = Gtk.Entry(); e.connect("changed", lambda x: print("TEXT", x.get_text(), flush=True))
    w.set_child(e); w.present(); e.grab_focus()
app.connect("activate", go)
app.run([])
"""


def test_typing_into_chronoas_own_window_is_refused(desk, tmp_path):
    script = tmp_path / "fake_chronoa.py"
    script.write_text(_FAKE_CHRONOA_WINDOW)
    proc = subprocess.Popen([sys.executable, str(script)], env=desk.env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        time.sleep(3)
        desk("focus_window", title_contains="Shani Chronoa")
        said, _ = desk("type_text", text="should not land")
        assert said.startswith("Nothing was typed: Chronoa's own window has focus"), said
        said, _ = desk("press_key", key="Return")
        assert "was not pressed" in said, said
    finally:
        proc.terminate()
    out = proc.communicate(timeout=10)[0]
    assert "should not land" not in out


def _cue(desk):
    out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.shani.Chronoa.Windows",
                          "--object-path", "/org/shani/Chronoa/Windows", "--method",
                          "org.shani.Chronoa.Windows.Indicator"],
                         env=desk.env, capture_output=True, text=True, timeout=10).stdout.strip()
    return json.loads(out[out.index("'") + 1:out.rindex("'")].encode().decode("unicode_escape"))["cue"]


def test_every_action_says_on_screen_that_chronoa_did_it(desk):
    """Input from the extension looks like the person's own, so each action
    puts what Chronoa is doing on screen (a pill; a ripple where it clicks)."""
    desk("focus_window", title_contains="UI target")
    assert _cue(desk) == "Chronoa switched to UI target"
    desk("type_text", text="x")
    assert _cue(desk) == "Chronoa is typing"
    desk("press_key", key="BackSpace")
    assert _cue(desk) == "Chronoa pressed BackSpace"
    desk("move_pointer", x=500, y=300)
    desk("click_pointer", button="left")
    assert _cue(desk) == "Chronoa is clicking"


def test_any_tool_call_can_be_cued(desk):
    """desktop_cue.show is what the app sends for every tool call."""
    subprocess.run([sys.executable, "-c",
                    "import sys; sys.path.insert(0, 'usr/lib/shani-chronoa'); "
                    "from shani_chronoa import desktop_cue as d; "
                    "d.show(d.for_tool('edit_file', {'path': '/x/pricing.py'}))"],
                   env=desk.env, cwd=_REPO, timeout=30, check=True)
    time.sleep(0.5)
    assert _cue(desk) == "Chronoa: Changed pricing.py"

