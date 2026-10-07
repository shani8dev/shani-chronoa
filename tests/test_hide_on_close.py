"""Hide on close with the wake word on; start hidden at login.

sayri's app holds itself open (`app.py:130`) so closing the window never quits
the assistant the wake word still needs. Chronoa's window had no close-request
handler at all: closing it destroyed the app, so the hands-free path the user
turned on died the moment the window was closed. And "start on login" always
popped a window, so there was no honest way to be up at login for the wake word
alone.

These tests build the real `Gtk.Window` for the close behaviour and the real
`ChronoaApplication` for the --hidden flag and the autostart sync - the
environment-dependent bits (a real system bus, a real autostart directory,
libappindicator) are exactly what the insertions above keep optional.
"""

import os
import sys
import threading
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui import AssistantState, ChronoaWindow  # noqa: E402


def _pump(limit=1.5):
    ctx = GLib.MainContext.default()
    deadline = time.time() + limit
    while time.time() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


@pytest.fixture(scope="module")
def gtk_app():
    app = Gtk.Application(application_id="test.chronoa.hide.on.close")
    started = threading.Event()

    def on_activate(a):
        started.set()
        a.quit()

    app.connect("activate", on_activate)
    app.run([])
    assert started.wait(10)
    yield app


class _Config:
    def __init__(self, wake: bool) -> None:
        self.wake_word_enabled = wake


def _window(gtk_app, wake: bool) -> ChronoaWindow:
    return ChronoaWindow(gtk_app, config=_Config(wake))


class TestHideOnClose:
    def test_close_hides_instead_of_quitting_when_wake_word_is_on(self, gtk_app, monkeypatch):
        win = _window(gtk_app, wake=True)
        win.set_state(AssistantState.IDLE)
        holds = []
        real_hold = gtk_app.hold
        monkeypatch.setattr(gtk_app, "hold", lambda: (holds.append(1), real_hold())[1])

        handled = win.emit("close-request")
        _pump(0.5)

        assert handled is True, "the window must eat the close, not destroy itself"
        assert not win.get_visible(), "the window must be hidden, not quit"
        assert win._holding is True
        assert holds == [1], "the app must be held so it does not quit with the window hidden"
        real_hold()  # balance: the real hold() went through our wrapper already
        gtk_app.release()
        win.destroy()

    def test_close_proceeds_normally_when_wake_word_is_off(self, gtk_app, monkeypatch):
        win = _window(gtk_app, wake=False)
        monkeypatch.setattr(gtk_app, "hold", lambda: pytest.fail("must not hold when wake word is off"))
        assert win._on_close_request() is False, (
            "with no hands-free path to preserve, a close must quit as usual"
        )
        assert win._holding is False
        win.destroy()

    def test_show_releases_the_hold(self, gtk_app, monkeypatch):
        win = _window(gtk_app, wake=True)
        real_hold, real_release = gtk_app.hold, gtk_app.release
        holds, releases = [], []
        monkeypatch.setattr(gtk_app, "hold", lambda: (holds.append(1), real_hold())[1])
        monkeypatch.setattr(gtk_app, "release", lambda: (releases.append(1), real_release())[1])

        win.emit("close-request")
        _pump(0.3)
        assert win._holding is True

        win.show()
        _pump(0.5)
        assert win.get_visible() is True
        assert win._holding is False
        assert releases == [1], "showing the window again must release the hold"
        win.destroy()


class TestHiddenFlagParsing:
    def test_hidden_flag_is_parsed(self):
        from shani_chronoa.app.application import ChronoaApplication

        app = ChronoaApplication()
        assert app._start_hidden is False
        app._parse_args(["shani-chronoa", "--hidden"])
        assert app._start_hidden is True

    def test_the_new_actions_are_registered(self):
        from shani_chronoa.app.application import ChronoaApplication

        app = ChronoaApplication()
        app._create_actions()
        # The settings row and the tray menu route through these actions.
        assert app.lookup_action("toggle-start-hidden") is not None
        assert app.lookup_action("show-window") is not None


class TestAutostartHiddenEntry:
    def _app(self, tmp_path, monkeypatch, auto: bool, hidden: bool):
        from shani_chronoa.app.application import ChronoaApplication, DesktopIntegrationMixin

        monkeypatch.setattr(DesktopIntegrationMixin, "_AUTOSTART_DIR", str(tmp_path))
        monkeypatch.setattr(
            DesktopIntegrationMixin, "_AUTOSTART_DESKTOP_FILE", str(tmp_path / "shani-chronoa.desktop")
        )
        monkeypatch.setattr(ChronoaConfig, "auto_start", property(lambda self: auto))
        monkeypatch.setattr(ChronoaConfig, "start_hidden_at_login", property(lambda self: hidden))
        return ChronoaApplication()

    def test_hidden_entry_written_instead_of_symlink(self, tmp_path, monkeypatch):
        app = self._app(tmp_path, monkeypatch, auto=True, hidden=True)
        app._sync_autostart()
        written = tmp_path / "shani-chronoa.desktop"
        assert written.exists() and not written.is_symlink()
        text = written.read_text()
        assert "Exec=shani-chronoa --hidden" in text
        assert "[Desktop Entry]" in text

    def test_switching_off_hidden_replaces_generated_entry(self, tmp_path, monkeypatch):
        app = self._app(tmp_path, monkeypatch, auto=True, hidden=True)
        app._sync_autostart()
        written = tmp_path / "shani-chronoa.desktop"
        assert written.exists()
        # Now the user turns hidden off: the generated entry must go, leaving
        # either the symlink or nothing (the installed file is absent here).
        monkeypatch.setattr(ChronoaConfig, "start_hidden_at_login", property(lambda self: False))
        app._sync_autostart()
        assert not written.exists()

    def test_autostart_off_removes_everything(self, tmp_path, monkeypatch):
        app = self._app(tmp_path, monkeypatch, auto=True, hidden=True)
        app._sync_autostart()
        assert (tmp_path / "shani-chronoa.desktop").exists()
        monkeypatch.setattr(ChronoaConfig, "auto_start", property(lambda self: False))
        app._sync_autostart()
        assert not (tmp_path / "shani-chronoa.desktop").exists()


class TestTrayToggle:
    def _app(self, window) -> object:
        class _App:
            def __init__(self):
                self.window = window
                self.actions = []

            def activate_action(self, name, param):
                self.actions.append(name)

        return _App()

    def test_a_hidden_window_is_brought_back_instead_of_toggling(self):
        from shani_chronoa.app import tray

        class _Window:
            visible = False
            shown = 0

            def get_visible(self):
                return self.visible

            def show(self):
                self.shown += 1
                self.visible = True

        window = _Window()
        app = self._app(window)
        tray._toggle(app)
        assert window.shown == 1
        assert app.actions == [], "a hidden window must come back, not toggle listening"

    def test_a_visible_window_toggles_listening(self):
        from shani_chronoa.app import tray

        class _Window:
            visible = True
            shown = 0

            def get_visible(self):
                return self.visible

            def show(self):
                self.shown += 1

        window = _Window()
        app = self._app(window)
        tray._toggle(app)
        assert window.shown == 0
        assert app.actions == ["toggle-listening"]
