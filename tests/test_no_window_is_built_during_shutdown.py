"""A window must not be built while the application is shutting down.

`chunk_ad` of a chunked suite run died with **a segfault**, not the abort
`AGENTS.md` records for `test_setup_button_is_reachable.py`. Its traceback reads,
most-recent-first:

    Gtk.py:575 __init__                 <- Gtk.Window(application=...)
    setup_wizard.py:572 build_window
    app/brain.py:100 _open_setup
    app/application.py:467 <lambda>     <- the "offer setup once" idle
    app/application.py:276 do_shutdown
    Gio.py:138 run

So `do_shutdown` ran while a `GLib.idle_add` queued at *activation* was still
pending. A fresh install gets that idle to offer the setup wizard once; the loop
ends before it fires; the loop is drained inside teardown; and
`Gtk.Window(application=self)` builds against an application that has already
begun shutting down, which segfaults the interpreter. In a chunked run that costs
every test file after the one that hit it.

**The invariant this file pins:** "the application is alive" is a state with a
flag, and building a window requires it. There was no such flag anywhere, so
nothing could refuse - the flag is the fix and this is the check that it stays.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

Adw.init()

from shani_chronoa.app import application as app_mod  # noqa: E402
from shani_chronoa.app import brain as brain_mod  # noqa: E402


def _app_stub(**flags):
    """An application-shaped object: `__new__`-built, as conftest builds one."""
    app = app_mod.ChronoaApplication.__new__(app_mod.ChronoaApplication)
    app._setup_window = None
    app._ollama_available = False
    app.window = None
    app.config = None
    for key, value in flags.items():
        setattr(app, key, value)
    return app


class TestTheFlagExists:
    def test_do_shutdown_sets_it_before_anything_is_torn_down(self):
        """Order matters: set at the top, or a callback firing between the
        release of one resource and the next still sees an alive application."""
        import inspect
        source = inspect.getsource(app_mod.ChronoaApplication.do_shutdown)
        flag_at = source.index("_shutting_down = True")
        first_release = source.index("self._stop_global_shortcut")
        assert flag_at < first_release, (
            "the flag is set after teardown has started, so a window built "
            "between two releases is still built against a dying application")

    def test_do_startup_declares_it(self):
        # declared, so the attribute exists without a call having to have run
        import inspect
        assert "_shutting_down" in inspect.getsource(
            app_mod.ChronoaApplication.do_startup), (
            "the flag is only ever assigned in do_shutdown, so an application "
            "that never shut down has no such attribute")


class TestSetupRefusesDuringShutdown:
    def test_open_setup_does_not_build_a_window(self, monkeypatch):
        """**The patch reaches the module the local import resolves.** `_open_setup`
        does `from shani_chronoa import setup_wizard` inside itself, so there is no
        `brain.setup_wizard` to patch at module scope - patching that name would be
        a no-op in a new module attribute, and the test would pass against the real
        thing. The function raises rather than merely recording, so a window that
        *did* get built fails here."""
        built = []

        def _build(*_a, **_k):
            built.append(True)
            raise AssertionError("a window was built during shutdown")

        monkeypatch.setattr("shani_chronoa.setup_wizard.build_window", _build)
        app = _app_stub(_shutting_down=True)
        # the mixin method is reachable on the application mixin's own function
        brain_mod.BrainMixin._open_setup(app)
        assert built == [], "a window was built while shutting down"

    def test_open_setup_still_builds_when_the_app_is_alive(self, monkeypatch):
        """**The control, in the other direction.** A guard that refuses always
        is not a guard, it is a broken feature - and the setup wizard would never
        appear for anyone."""
        built = []
        presented = []

        class _W:
            def get_visible(self):
                return False

            def present(self):
                presented.append(True)

        def _build(*_a, **_k):
            built.append(True)
            return _W()

        monkeypatch.setattr("shani_chronoa.setup_wizard.build_window", _build)
        app = _app_stub(_shutting_down=False)
        brain_mod.BrainMixin._open_setup(app)
        assert built == [True], "the wizard did not open on a live application"


class TestTheMixinReachesTheApplication:
    def test_the_guard_is_on_the_function_the_idle_calls(self):
        """The idle at `application.py:467` calls `self._open_setup`, so the
        guard has to be inside that method rather than at the call site - a
        second call site appears later and would remember its own way."""
        import inspect
        source = inspect.getsource(brain_mod.BrainMixin._open_setup)
        assert "_shutting_down" in source, (
            "the guard is not in `_open_setup`, so the idle that calls it has "
            "no way to know the application is going away")
