"""The settings window is the tree's only libadwaita user, so the application
has to initialise libadwaita.

`Adw.init()` was not called anywhere in the repo, and the settings window is
built entirely from Adw.PreferencesPage / SwitchRow / EntryRow.

Scope note, because it is the part worth being careful about: an earlier
version of this test asserted on `Adw.StyleManager.get_default()`, and it
passed with `Adw.init()` deleted. `get_default()` constructs the singleton
lazily through `g_object_new`, so it answers with a valid object whether or not
the library was initialised - the assertion could not fail. A second version
asserted the row count, which is also independent of init. Both were theatre.
Deleting `Adw.init()` is a contract violation, but on libadwaita 1.5 it was not
demonstrably a *user-visible* failure, and this test does not pretend
otherwise.

What it pins is the wiring: the application initialises libadwaita during
startup. That is the property whose absence let the contract lapse, and unlike
the two above it is observable - the call either happens or it does not.
"""

import os
import subprocess
import sys
import textwrap

import pytest

# Runs in a child process: GTK holds a display connection and libadwaita holds
# global state, neither of which can be set up and torn down per-test inside a
# single interpreter.
_HARNESS = textwrap.dedent(
    """
    import json, sys
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib

    import shani_chronoa.app.application as app_mod  # do_startup lives here

    calls = []
    real_init = Adw.init

    class _Spy:
        def __getattr__(self, name):
            return getattr(Adw, name)

        @staticmethod
        def init():
            calls.append("Adw.init")
            return real_init()

    # Recorded before the substitution below, because assigning `Adw` onto the
    # module would *create* the name and mask a missing import - and a missing
    # import is a NameError raised inside a vfunc, which GLib swallows rather
    # than propagating, so nothing else would notice.
    out_imported = hasattr(app_mod, "Adw")
    app_mod.Adw = _Spy

    app = app_mod.ChronoaApplication()
    out = {}

    def on_startup(a):
        def rows(node):
            c = node.get_first_child()
            while c:
                if isinstance(c, Adw.PreferencesRow):
                    yield c
                yield from rows(c)
                c = c.get_next_sibling()
        from shani_chronoa.settings_window import SettingsWindow
        out["rows"] = sum(1 for _ in rows(SettingsWindow(a)))
        a.quit()

    app.connect("startup", on_startup)
    GLib.timeout_add(25000, lambda: (app.quit(), False)[1])
    app.run([])
    out["init_calls"] = calls
    out["adw_imported"] = out_imported
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def real_app(tmp_path_factory):
    import json
    import pathlib

    work = tmp_path_factory.mktemp("adw")
    runtime = work / "runtime"
    runtime.mkdir()
    os.chmod(runtime, 0o700)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(pathlib.Path("usr/lib/shani-chronoa").resolve())
    env["XDG_RUNTIME_DIR"] = str(runtime)
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(work),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, (
        f"the real app produced no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    )
    return payload


def test_the_application_initialises_libadwaita(real_app):
    """`Adw.init()` is a documented prerequisite for any Adw widget, and this
    tree's settings window is nothing but Adw widgets.

    Driving the real ChronoaApplication matters: a test that called Adw.init()
    itself would pass with the call removed from app.py, which is exactly the
    mistake the first two versions of this file made.
    """
    assert real_app["adw_imported"], (
        "app.py does not import Adw, so the call inside do_startup is a "
        "NameError that GLib swallows - the settings window would silently "
        "fall back to unstyled GTK"
    )
    assert real_app["init_calls"], (
        "ChronoaApplication.do_startup never called Adw.init(); libadwaita is "
        "a prerequisite for the settings window's Adw widgets"
    )


def test_the_settings_window_builds_its_rows_under_the_real_app(real_app):
    assert real_app["rows"] > 0
