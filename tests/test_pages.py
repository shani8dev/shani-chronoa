"""Every page has an id, and any of them can be named from outside the window.

These pages were reachable only by holding the mouse: the settings sections by
typing into a search box, the wizard by pressing Next, the main window by
whatever happened to be open. So a notification could not say "open Settings on
Privacy", a keybinding could not, and a test could not - it had to guess at
selectors, which is how one run produced four screenshots of four "different"
sections that were byte-identical while reporting every step green.

The shape is shani-cassini's (`notebook.py`'s `PAGES`/`page_ids()`/`select`,
`--section=`, and a `show-section` action), generalised to three windows.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import pages  # noqa: E402


class _Window:
    """A window that records what it was asked to show, and can refuse."""

    def __init__(self, refuses=()):
        self.shown = []
        self.refuses = set(refuses)
        self.presented = 0

    def show_page(self, page_id):
        if page_id in self.refuses:
            return False
        self.shown.append(page_id)
        return True

    def present(self):
        self.presented += 1


@pytest.fixture(autouse=True)
def clean_registry():
    """The registry is module state; a test that leaks into it breaks the next."""
    saved_pages, saved_aliases = dict(pages._PAGES), dict(pages._ALIASES)
    saved_windows, saved_factories = dict(pages._WINDOWS), dict(pages._FACTORIES)
    pages._WINDOWS.clear()
    pages._FACTORIES.clear()
    yield
    pages._PAGES.clear(); pages._PAGES.update(saved_pages)
    pages._ALIASES.clear(); pages._ALIASES.update(saved_aliases)
    pages._WINDOWS.clear(); pages._WINDOWS.update(saved_windows)
    pages._FACTORIES.clear(); pages._FACTORIES.update(saved_factories)


def test_a_slug_is_derived_from_the_title():
    assert pages.slug("Tool activity") == "tool-activity"
    assert pages.slug("Who said what") == "who-said-what"
    assert pages.slug("") == ""


def test_an_explicit_id_wins_over_the_title():
    pages.register("settings", [("tool-activity", "Activity log")])
    assert pages.page_ids("settings") == ["tool-activity"]
    assert pages.page_titles("settings") == ["Activity log"]


def test_a_retired_id_still_opens_what_absorbed_it():
    """A stored id that opens nothing is worse than one that opens somewhere
    honest - the app looks broken in a way nobody can diagnose from the id."""
    pages.register("settings", [("models", "Models")], aliases={"model": "models"})
    assert pages.resolve("settings", "model") == "models"
    assert pages.resolve("settings", "models") == "models"


def test_aliases_are_per_window_so_ids_cannot_collide():
    pages.register("settings", [("privacy", "Privacy")], aliases={"model": "privacy"})
    pages.register("setup", [("models", "Model")])
    assert pages.resolve("settings", "model") == "privacy"
    assert pages.resolve("setup", "model") is None


def test_an_alias_cycle_is_refused_rather_than_hanging():
    """A click that loops forever is the worst outcome for a rename gone wrong.

    Neither id here is a live page, so the walk genuinely goes round: an earlier
    version of this test registered `a` as a page *and* aliased it to `b`, which
    is an ordinary alias and resolves correctly - a test that passed for the
    wrong reason.
    """
    pages.register("x", [("c", "C")], aliases={"a": "b", "b": "a"})
    assert pages.resolve("x", "a") is None
    assert pages.resolve("x", "c") == "c"


def test_an_unknown_page_opens_nothing():
    pages.register("settings", [("privacy", "Privacy")])
    window = _Window()
    pages.note_window("settings", window)
    assert pages.show("settings:nosuchpage") is False
    assert window.shown == [], "an unknown id opened something anyway"
    assert window.presented == 0, "and presented the window regardless"


def test_an_unknown_window_is_refused():
    assert pages.show("nosuchwindow:privacy") is False


def test_a_bare_id_resolves_only_when_it_is_unambiguous():
    pages.register("settings", [("privacy", "Privacy")])
    pages.register("setup", [("privacy", "Privacy")])
    assert pages.show("privacy") is False, "an ambiguous bare id must not guess"
    pages._PAGES.pop("setup")
    window = _Window()
    pages.note_window("settings", window)
    assert pages.show("privacy") is True
    assert window.shown == ["privacy"]


def test_a_window_that_refuses_is_reported_not_hidden():
    """The registry must not report success for a page the window would not show."""
    pages.register("settings", [("privacy", "Privacy")])
    pages.note_window("settings", _Window(refuses={"privacy"}))
    assert pages.show("settings:privacy") is False


def test_a_closed_window_is_built_through_its_factory():
    """`--show-page=settings:privacy` on a cold start has to open the window."""
    built = []

    def factory(application, config=None):
        built.append((application, config))
        window = _Window()
        pages.note_window("settings", window)
        return window

    pages.register("settings", [("privacy", "Privacy")], factory=factory)
    assert pages.show("settings:privacy", application="APP", config="CFG")
    assert built == [("APP", "CFG")]
    assert pages.show("settings:privacy") is True, "and the built window is remembered"


def test_describe_names_every_window_and_its_aliases():
    pages.register("settings", [("privacy", "Privacy")], aliases={"model": "privacy"})
    text = pages.describe()
    assert "settings: privacy" in text
    assert "model -> privacy" in text


# ---------------------------------------------------------------------------
# the three real windows register themselves
# ---------------------------------------------------------------------------


def _source(module_name: str) -> str:
    from _source import package_source
    return package_source(module_name)


def test_the_settings_window_registers_its_sections():
    """The ids a caller may use are a promise, so they are declared rather than
    scraped from whatever widgets happened to be built."""
    source = _source("settings_window")
    assert "register(" in source and '"settings"' in source
    for section in ("senses", "privacy", "approvals", "tool-activity",
                    "voice", "models", "system"):
        assert f'"{section}"' in source, section


def test_the_wizard_registers_its_pages():
    source = _source("setup_wizard")
    assert '"setup"' in source
    for page in ("welcome", "mode", "cloud-keys", "brain", "model-picker",
                 "ears", "voice", "review", "extras", "done"):
        assert f'"{page}"' in source, page


def test_all_three_windows_answer_to_show_page():
    """One name, three windows - so a caller does not learn three spellings."""
    from shani_chronoa.gui.window import ChronoaWindow
    assert hasattr(ChronoaWindow, "show_page"), "the main window cannot be addressed"
    for module in ("setup_wizard", "settings_window"):
        assert "def show_page" in _source(module), module


def test_the_application_exposes_the_doorway():
    source = _source("app.application")
    assert '"show-page"' in source, "no action"
    assert "--show-page=" in source, "no flag"