"""The settings window's section chips: one click lands on a section.

Seven sections lived in one ~9,000px scroll and the only way to land on one was
to know its title and type it. `show_section()` already filtered to a section
for the page registry; nothing a person could see called it. These tests press
the chips and count what is left on screen.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.config import ChronoaConfig  # noqa: E402


class _App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="test.chronoa.chips",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = ChronoaConfig()
        self.window = None
        self._wake_word_active = False

    def activate_action(self, name, arg=None):
        pass


@pytest.fixture(scope="module")
def app():
    # One registration per module: a second one exports the same object path.
    application = _App()
    application.register(None)
    return application


@pytest.fixture()
def window(app):
    from shani_chronoa.settings_window import SettingsWindow
    return SettingsWindow(app)


def _shown(window):
    return [g for g, _h in window._searchable if g.get_visible()]


def _families(groups):
    return {getattr(g, "_section_family", "") for g in groups}


def test_there_is_a_chip_for_all_and_for_every_section(window):
    from shani_chronoa import pages
    assert set(window._chips) == {""} | set(pages.page_ids("settings"))
    assert window._chips[""].get_active(), "the window does not open on All"


def test_a_chip_shows_only_its_section(window):
    total = len(window._searchable)
    window._chips["privacy"].set_active(True)
    shown = _shown(window)
    assert shown and len(shown) < total, (len(shown), total)
    assert _families(shown) == {"privacy"}, _families(shown)


def test_all_puts_every_section_back(window):
    total = len(window._searchable)
    window._chips["voice"].set_active(True)
    window._chips[""].set_active(True)
    assert len(_shown(window)) == total


def test_typing_then_clearing_after_a_chip_restores_the_window(window):
    """`search-changed` is delayed for typed text and immediate for empty text,
    so a type-and-clear can deliver only the empty one. That must still mean
    "everything", not "the chip chosen before the typing"."""
    total = len(window._searchable)
    window._chips["system"].set_active(True)
    assert len(_shown(window)) < total, "control: the chip did not filter"
    window._search.set_text("camera")
    assert window._chips[""].get_active(), "typing did not move the chips to All"
    window._search.set_text("")
    assert len(_shown(window)) == total


def test_show_section_moves_the_chip(window):
    window.show_section("models")
    assert window._chips["models"].get_active()
    assert _families(_shown(window)) == {"models"}
