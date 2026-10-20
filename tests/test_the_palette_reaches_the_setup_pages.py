"""The palette reaches the setup wizard's pages, and the browser window.

**The palette could open the wizard but not a page of it.** The Settings
group lists every Settings page the same way, so a person who wanted the
hearing model had to open the wizard and walk to it - on the one surface
whose whole job is to be the door to everything.

**The in-app browser was a window reachable only by a key.** `Ctrl+Shift+U`
opened it, and nothing on screen said so. It is a *window* rather than a
panel, so it is not in the sidebar and was not in the palette either.

The setup pages are named by the page's own words, so the palette says
"Setup: Hearing" on a page called "Hearing" - the plain title the rename
put there, not the organ name the page used to carry.

**The entries only appear for actions the application really has**, so the
test registers the two actions it needs on its own application and reads
the application's own source to check that the real one has them too.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")
Gio = pytest.importorskip("gi.repository.Gio", reason="GLib is not installed")
GLib = pytest.importorskip("gi.repository.GLib", reason="GLib is not installed")

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


def _registered_actions() -> set:
    """The actions the real application registers, read out of its source.

    **An `ast` read, not a build.** Building the real application needs a
    session bus, and a suite that needs a bus is a suite that cannot run
    beside another one - which is the failure this file's own `AGENTS.md`
    records at length.

    The pattern is the one `tests/test_about.py` already uses for the
    accelerators: the code is the authority on what the app has.
    """
    path = _REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa" / "app" / "application.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "new":
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Attribute) or owner.attr != "SimpleAction":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            names.add(node.args[0].value)
    return names


@pytest.fixture(scope="module")
def app():
    """An application with the two actions the palette needs.

    **The palette's action entries are filtered by what the application
    has** - a row for an action nothing registers would be a row that does
    nothing. So the test registers them, the way the real application does.
    """
    application = Gtk.Application(application_id="test.chronoa.palette.setup")
    browser = Gio.SimpleAction.new("open-browser", None)
    application.add_action(browser)
    setup_page = Gio.SimpleAction.new("setup-page", GLib.VariantType.new("s"))
    application.add_action(setup_page)
    fired = []
    application.fired = fired

    # **The activation is recorded here rather than run.** A bare
    # `Gtk.Application` in a test is not on a bus, and activating an action
    # on one is a GIO critical - "application->priv->is_registered". So the
    # application's own `activate_action` is replaced with a recorder, and
    # everything above it is the product's own path: the palette builds an
    # entry, the entry's callback calls `app.activate_action`, and that is
    # what is being watched.
    def activate_action(name, value=None):
        fired.append((name,
                      value.get_string() if value is not None else None))

    application.activate_action = activate_action
    yield application
    application.quit()


@pytest.fixture
def window(app):
    return ChronoaWindow(app, config=ChronoaConfig())


class TestThePaletteReachesTheBrowser:
    def test_the_browser_is_offered(self, window):
        """**A window is not in the sidebar**, so nothing else on screen
        says the in-app browser exists."""
        titles = {e.title for e in window.palette_entries()}
        assert "In-app browser" in titles, (
            "the palette does not offer the in-app browser, so it is "
            "reachable only by a key nobody is told about")

    def test_pressing_it_opens_the_browser(self, window, app):
        """**The product's own path**: the entry fires the action the
        shortcut fires."""
        app.fired.clear()
        entry = next(e for e in window.palette_entries()
                     if e.title == "In-app browser")
        entry.run()
        assert [name for name, _ in app.fired] == ["open-browser"], (
            f"pressing the browser entry fired {app.fired}, not the browser "
            "action")

    def test_the_real_application_has_the_action(self):
        """**The palette's filter is what makes the entry honest**, so the
        action has to be one the application really registers."""
        assert "open-browser" in _registered_actions(), (
            "the palette offers the browser, but the application does not "
            "register the action - so the row would do nothing")


class TestThePaletteReachesTheSetupPages:
    def test_every_setup_page_is_offered(self, window):
        """**The point of the palette is that everything is one press
        away**, and the wizard's pages were the part that was not."""
        from shani_chronoa import setup_wizard

        wanted = {f"Setup: {title}" for tag, title in setup_wizard._SETUP_PAGES
                  if tag not in ("welcome", "done")}
        offered = {e.title for e in window.palette_entries() if e.group == "Setup"}
        assert wanted == offered, (
            f"the palette's setup pages are {sorted(offered)}, and the "
            f"wizard's are {sorted(wanted)}")

    def test_the_entries_use_the_pages_own_words(self, window):
        """**The palette and the page must be called the same thing**, or
        the palette names a page the wizard does not have."""
        from shani_chronoa import setup_wizard

        titles = {e.title for e in window.palette_entries() if e.group == "Setup"}
        for tag, title in setup_wizard._SETUP_PAGES:
            if tag in ("welcome", "done"):
                continue
            assert f"Setup: {title}" in titles, (
                f"the palette does not offer the {title} page")
        assert not any(t.endswith(": Ears") or t.endswith(": Eyes")
                       for t in titles), (
            "the palette is still offering the setup pages by the organ "
            "names the rename removed")

    def test_pressing_one_asks_for_that_page(self, window, app):
        """**The product's own path**: the page tag reaches the action
        that opens the wizard on it."""
        app.fired.clear()
        entry = next(e for e in window.palette_entries()
                     if e.title == "Setup: Hearing")
        entry.run()
        assert app.fired == [("setup-page", "ears")], (
            f"pressing the hearing page fired {app.fired}, not the setup "
            "action for 'ears'")

    def test_the_real_application_has_the_action(self):
        """**The action the entry fires has to be one the application
        really registers** - the palette's filter is what makes its rows
        honest."""
        assert "setup-page" in _registered_actions(), (
            "the palette offers the setup pages, but the application does "
            "not register the action - so every row would do nothing")
