"""The turn footer has a door to the changes it lists.

**The footer said how many files a turn wrote and offered to put them
back, and there was no way to look at them from there.** The door exists
- a tool card carries "Show full diff" - but it is on a *card*, and the
footer is the line a person reads *after* the turn, when the card is
several rows up and the sidebar that holds the panel is collapsed at the
width the app opens at.

**The button uses the same handler the tool card uses** (`on_show_diff`),
so there is one way to open that panel and not two.
"""

from __future__ import annotations

import os
import sys
from collections import deque

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")

from shani_chronoa.gui import widgets as widgets_module  # noqa: E402


def _stats(**over):
    """The turn's statistics, as `assistant.turn_stats()` reports them."""
    base = {
        "model_calls": 1,
        "usage": {"total_tokens": 120},
        "model_seconds": 1.0,
        "wall_seconds": 2.0,
        "budget_seconds": 600,
        "over_budget": False,
        "tools": ["write_text_file"],
        "withheld": 0,
        "cloud": False,
        "changed": ["/tmp/one.txt", "/tmp/two.txt"],
        "turn": "turn-1",
    }
    base.update(over)
    return base


def _buttons(view):
    """Every button in the transcript, by its label."""
    queue, found = deque([view]), []
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Button):
            found.append(node)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found


def _footer(stats, show_diff=None):
    """A transcript with one turn, and the footer that turn drew."""
    view = widgets_module.TranscriptView(on_show_diff=show_diff)
    view.set_turn_footer_handler(lambda: stats)
    view.add_user_turn("a question")
    view.add_assistant_turn("an answer")
    return view


class TestTheFooterHasADoorToTheChanges:
    def test_the_button_is_there_when_the_turn_changed_files(self):
        """**The summary is what a person reads after the turn**, so the
        door belongs on it."""
        view = _footer(_stats(), show_diff=lambda: None)
        labels = [b.get_label() for b in _buttons(view)]
        assert "See the changes" in labels, (
            f"the footer drew {labels} - the files it lists have no way to "
            "be seen from it")

    def test_pressing_it_opens_the_panel(self):
        """**The product's own path**: the button calls the handler the
        tool card's "Show full diff" calls."""
        opened = []
        view = _footer(_stats(), show_diff=lambda: opened.append("diff"))
        button = next(b for b in _buttons(view)
                      if b.get_label() == "See the changes")
        button.emit("clicked")
        assert opened == ["diff"], (
            f"pressing the button opened {opened}, not the panel")

    def test_it_is_the_same_handler_the_tool_card_uses(self):
        """**One way to open that panel, not two.** The transcript already
        had `on_show_diff` for the tool card's "Show full diff"; a second
        handler would be a second route to the same place."""
        view = widgets_module.TranscriptView(on_show_diff=lambda: None)
        assert getattr(view, "_on_show_diff", None) is not None, (
            "the transcript does not carry the handler the tool card uses")

    def test_no_button_when_the_turn_changed_nothing(self):
        """**A button that opens an empty panel is worse than no
        button** - and the control matters, because the footer is drawn
        for every turn.

        **The fixture passes a handler**, so the *changed* half of the
        condition is what decides and not the handler's absence - a
        fixture with no handler makes this test pass whatever the
        condition says.
        """
        view = _footer(_stats(changed=[]), show_diff=lambda: None)
        labels = [b.get_label() for b in _buttons(view)]
        assert "See the changes" not in labels, (
            f"the footer drew {labels} on a turn that changed nothing")

    def test_no_button_when_there_is_no_panel_to_open(self):
        """**A button that cannot work is worse than no button** - the
        same rule the revert button follows."""
        view = _footer(_stats(), show_diff=None)
        labels = [b.get_label() for b in _buttons(view)]
        assert "See the changes" not in labels, (
            f"the footer drew {labels} with nothing to open")
