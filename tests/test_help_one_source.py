"""Help, /help, the Workbench and the capabilities page read one source.

The four surfaces used to call ``discover_skills()`` each and keep their own
copy of the failure handling, and the help control was named by three literals.
A skill that failed to load could therefore make one surface disagree with
another, and a renamed command could go stale in one place only.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PKG = REPO / "usr/lib/shani-chronoa/shani_chronoa"
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

from shani_chronoa import capabilities, skills  # noqa: E402
from shani_chronoa.gui import commands  # noqa: E402


def _source(relative: str) -> str:
    from _source import source_path
    return source_path(relative).read_text(encoding="utf-8")


def test_all_four_surfaces_read_the_shared_registry():
    assert "capabilities.live_capabilities()" in _source("gui/window.py")
    assert "capabilities.live_registry(discover_skills)" in _source(
        "gui/surfaces/skills.py"
    )
    assert "capabilities.live_registry()" in _source("gui/surfaces/workbench.py")
    assert "capabilities.help_label()" in _source("gui/commands.py")
    assert "capabilities.help_label()" in _source("gui/window.py")


def test_slash_help_uses_the_shared_prompt_and_the_skills_surface():
    source = _source("gui/commands.py")
    assert "caps.help_prompt()" in source
    assert 'window._show_surface("skills")' in source
    assert "window.open_help()" not in source, (
        "/help must not open the older HelpWindow: Skills is the one data source"
    )
    assert commands.commands()["help"].summary == capabilities.help_label()


def test_help_window_still_has_its_own_ui_route():
    """Removing the command's HelpWindow call must not orphan HelpWindow."""
    window = _source("gui/window.py")
    assert "def open_help(" in window
    assert "handler=lambda _b: self.open_help()" in window, (
        "the header Help button is the remaining route to the capability list"
    )


def test_slash_help_opens_skills_and_an_argument_only_fills_the_composer():
    class _Window:
        def __init__(self):
            self.surfaces = []
            self.input = ""

        def _show_surface(self, name):
            self.surfaces.append(name)

        def set_input_text(self, text):
            self.input = text

    help_command = commands.commands()["help"]
    window = _Window()
    assert help_command.run(window, "") is None
    assert window.surfaces == ["skills"]
    assert window.input == ""

    window = _Window()
    assert help_command.run(window, "what can you do?") is None
    assert window.surfaces == [], "an argument must not also switch panels"
    assert window.input == capabilities.help_prompt()


def test_live_capabilities_matches_the_registry():
    schemas, _handlers = skills.discover_skills()
    expected = capabilities.find_capabilities(schemas)
    actual, error = capabilities.live_capabilities()
    assert error == ""
    assert [c.tool for c in actual] == [c.tool for c in expected]


def test_an_unreadable_registry_is_an_error_not_an_empty_fact(monkeypatch):
    def broken():
        raise RuntimeError("registry is down")

    monkeypatch.setattr(skills, "discover_skills", broken)
    caps, error = capabilities.live_capabilities()
    assert caps == []
    assert "registry is down" in error
