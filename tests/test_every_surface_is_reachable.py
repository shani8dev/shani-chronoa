"""Every surface is reachable from the real window, not merely registered.

The claim this file exists for is the one this repo has paid for more than any
other: **a module that imports is not a widget that works.** The orb is the
standing example - a `Gtk.Button` with nothing connected, which read as complete
and did nothing, and its own AGENTS.md entry records that reading `gui.py` beside
`app.py` looked complete. The surfaces registry is one level up from the orb, and
the same gap is available there: `surfaces.all_surfaces()` answering an id proves
the *module* imports, and says nothing about whether the sidebar row, the push, or
the built page does anything.

So this drives the real `ChronoaWindow`, calls each panel's own `_show_surface`,
and reads the **visible page's title**. A `Gtk.WidgetPaintable` of the content is
deliberately not used: every `common.surface()` returns an `Adw.NavigationPage`
holding an `Adw.ToolbarView`, so seeing one proves the call happened and nothing
about *which* panel arrived. The title is the panel's own `TITLE`, set by its
module, and it is the only thing on the page that names it.

Built in a subprocess on a private broadway display, because a GDK backend is
chosen when GTK initialises and no fixture can change it afterwards - the repo's
own measured trap, which is why `test_now_rail.py` picks its display before
`gi.repository.Gtk` is imported rather than in a fixture.
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"

HARNESS = r'''
import json, sys
sys.path.insert(0, PKG_PATH)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
Adw.init()

from shani_chronoa.gui import surfaces

holder = {}


def say(**kw):
    print("RESULT " + json.dumps(kw), flush=True)


def on_activate(a):
    from shani_chronoa.gui.window import ChronoaWindow
    holder["w"] = ChronoaWindow(a)
    holder["w"].present()


app = Gtk.Application(application_id="org.shani.surfaces.reachable")
app.connect("activate", on_activate)

from gi.repository import GLib


def drive():
    window = holder["w"]
    wanted = sys.argv[1] if len(sys.argv) > 1 else ""
    names = [wanted] if wanted else sorted(surfaces.all_surfaces())
    shown = {}
    for name in names:
        try:
            window._show_surface(name)
        except Exception as exc:            # a build failure is not a crash
            shown[name] = "raised " + type(exc).__name__
            continue
        page = window._content_view.get_visible_page()
        shown[name] = page.get_title() if page is not None else "nothing pushed"
    say(shown=shown, registered=sorted(surfaces.all_surfaces()))
    GLib.timeout_add(100, lambda: (app.quit(), False)[1])
    return False


GLib.timeout_add(900, drive)
GLib.timeout_add(24000, lambda: (app.quit(), False)[1])
raise SystemExit(app.run([]))
'''.replace("PKG_PATH", repr(str(PKG)))


@pytest.fixture(scope="module")
def broadway():
    """A private Broadway display, so no window appears on anybody's desktop."""
    daemon = shutil.which("gtk4-broadwayd")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if daemon is None or not runtime:
        pytest.skip("no gtk4-broadwayd or no XDG_RUNTIME_DIR, so no headless display")
    base = pathlib.Path(runtime)
    # A free display is a missing socket *and* a free port: tests/_broadway.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _broadway import free_display
    number = free_display(base, 300, 400)
    if number is None:
        pytest.skip("no free Broadway display number")
    proc = subprocess.Popen([daemon, f":{number}"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline \
            and not (base / f"broadway{number + 1}.socket").exists():
        time.sleep(0.1)
    yield number
    proc.terminate()
    proc.wait(timeout=10)


def _drive(broadway, tmp_path, name=None):
    work = tmp_path / "drive"
    work.mkdir(parents=True, exist_ok=True)
    script = work / "drive.py"
    script.write_text(HARNESS)
    env = dict(os.environ)
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{broadway}",
               PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               XDG_STATE_HOME=str(work / "state"),
               XDG_DATA_HOME=str(work / "data"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    argv = [sys.executable, str(script)] + ([name] if name else [])
    done = subprocess.run(argv, capture_output=True, text=True, timeout=300,
                          env=env)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    pytest.fail(f"the harness produced nothing:\n{done.stdout[-800:]}\n"
                f"{done.stderr[-800:]}")


def _title_of(name):
    """A panel's own `TITLE`, read from its module rather than copied here."""
    try:
        module = importlib.import_module(f"shani_chronoa.gui.surfaces.{name}")
    except Exception:
        return None
    return getattr(module, "TITLE", None)


def test_every_surface_arrives_at_itself(broadway, tmp_path):
    """Each panel's own `TITLE` is what lands on the page.

    A panel whose push succeeds but leaves the previous page on screen reports the
    *previous* title here - which is how this catches "the call returned and
    nothing happened" rather than only "the call raised".
    """
    out = _drive(broadway, tmp_path)
    assert out["registered"], "no surfaces at all"
    wrong = {}
    for name, title in out["shown"].items():
        expected = _title_of(name)
        if expected is not None and title != expected:
            wrong[name] = f"{title!r}, expected {expected!r}"
    assert not wrong, f"these panels did not land on themselves: {wrong}"


def test_the_four_panels_added_this_session_are_among_them(broadway, tmp_path):
    """The four added here, by name, because the loop above passes on an
    empty set and a panel nobody drives is invisible to it."""
    out = _drive(broadway, tmp_path)
    for name in ("my_files", "meetings", "phone_link", "driving"):
        assert name in out["shown"], f"{name} was not driven at all"
        assert out["shown"][name] == _title_of(name), out["shown"][name]
