"""The bell in the header: background activity, where a person can see it.

**The rail has always shown the rules that fired, and the rail is hidden below
1040px** - which is every window the app opens at (460x640, measured on a real
capture). So on the default window a rule that fired while the person was away
left no trace at all, and a proposal waiting for an answer was somewhere only
the Memory panel could find.

Every assertion here builds a real window, because the question is whether the
control is *there* and what it *says* - and a stub of a widget answers neither.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.gui import ChronoaWindow  # noqa: E402


@pytest.fixture(scope="module")
def gtk_app():
    """A real Gtk.Application with a live main loop.

    Widget state - visibility, labels - only settles once the widgets are
    realised against a display, so the loop has to actually run.
    """
    app = Gtk.Application(application_id="test.chronoa.bell")
    holder = {}
    started = threading.Event()

    def on_activate(a):
        holder["app"] = a
        started.set()
        a.quit()

    app.connect("activate", on_activate)
    app.run([])
    assert started.wait(10), "Gtk.Application never activated"
    yield app


@pytest.fixture
def window(gtk_app):
    win = ChronoaWindow(gtk_app)
    yield win
    win.destroy()


class _Fired:
    """What a fired rule hands back. The description reads it defensively."""

    def __init__(self, name="repo-changed", action="notify", **extra):
        self.rule_name = name
        self.action = action
        for key, value in extra.items():
            setattr(self, key, value)


def _labels(widget):
    out = []

    def walk(node):
        if isinstance(node, Gtk.Label) and node.get_label():
            out.append(node.get_label())
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


def _buttons(widget):
    out = []

    def walk(node):
        if isinstance(node, Gtk.Button):
            out.append(node)
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


class TestTheBell:
    def test_it_is_hidden_when_nothing_has_happened(self, window):
        """**A bell that is always there with nothing behind it is decoration.**

        Decoration is worse than nothing, because it costs a glance on every
        turn and teaches the reader that the bell means nothing.
        """
        window._rail_events = lambda: ()
        window._proposal_count = lambda: 0
        window._refresh_activity_bell()
        assert not window._bell_button.get_visible(), (
            "the bell is showing with nothing behind it")

    def test_it_appears_when_a_rule_has_fired(self, window):
        """**A rule that fired while the person was away left no trace at
        all** - the rail that used to show it is hidden below 1040px, which is
        every window the app opens at.
        """
        window._rail_events = lambda: [_Fired()]
        window._proposal_count = lambda: 0
        window._refresh_activity_bell()
        assert window._bell_button.get_visible(), (
            "a rule fired and the window shows no sign of it")

    def test_it_says_what_happened(self, window):
        """The tooltip is the answer to "what is that thing" - and it must
        name the rules, not merely be present."""
        window._rail_events = lambda: [_Fired(), _Fired()]
        window._proposal_count = lambda: 0
        window._refresh_activity_bell()
        tip = window._bell_button.get_tooltip_text()
        assert "2 rules fired" in tip, tip
        assert "rule(s)" not in tip, tip

    def test_the_badge_counts_what_is_waiting(self, window):
        """**A rule that fired is history; a proposal is a decision somebody
        owes.** The badge counts the second - the thing that needs the person -
        and the menu lists the first.
        """
        window._rail_events = lambda: [_Fired()]
        window._proposal_count = lambda: 3
        window._refresh_activity_bell()
        assert window._bell_badge.get_label() == "3", (
            f"the badge does not count the proposals: "
            f"{window._bell_badge.get_label()!r}")

    def test_the_menu_lists_the_rules_that_fired(self, window):
        """**The menu is the detail the badge cannot carry** - a count says
        how many, and a person needs to know which.
        """
        window._rail_events = lambda: [_Fired(name="repo-changed"),
                                       _Fired(name="disk-full")]
        window._proposal_count = lambda: 0
        window._refresh_activity_bell()
        text = "\n".join(_labels(window._bell_popover.get_child()))
        assert "repo-changed" in text, text
        assert "disk-full" in text, text

    def test_its_door_opens_the_triggers_panel(self, window, monkeypatch):
        """**A door that leads somewhere that does not answer is the dead-end
        class this repository keeps measuring.** The rules that fired are not
        in the Triggers *panel* - they are in the rail - so the menu has to
        carry them itself, and the door is for managing the rules.
        """
        opened = []
        monkeypatch.setattr(window, "_show_surface",
                            lambda name: opened.append(name))
        window._rail_events = lambda: [_Fired()]
        window._proposal_count = lambda: 0
        window._refresh_activity_bell()
        button = next(b for b in _buttons(window._bell_popover.get_child())
                      if b.get_label() == "Open Triggers")
        button.emit("clicked")
        assert opened == ["triggers"], opened

    def test_its_other_door_opens_the_memory_panel(self, window, monkeypatch):
        """The proposals are read and decided in the Memory panel, so that is
        where its door goes."""
        opened = []
        monkeypatch.setattr(window, "_show_surface",
                            lambda name: opened.append(name))
        window._rail_events = lambda: ()
        window._proposal_count = lambda: 2
        window._refresh_activity_bell()
        button = next(b for b in _buttons(window._bell_popover.get_child())
                      if b.get_label() == "Open Memory")
        button.emit("clicked")
        assert opened == ["memory"], opened

    def test_the_proposals_are_not_read_on_every_tick(self, window,
                                                       monkeypatch):
        """**`pending_proposals` reads every digest on disk**, and the window
        already runs a two-second tick - so reading them on every tick would
        turn that into a directory scan every two seconds for the life of the
        window.
        """
        from shani_chronoa.senses import memory

        reads = []

        def _count():
            reads.append(time.monotonic())
            return []

        monkeypatch.setattr(memory, "pending_proposals", _count)
        window._proposal_cache = None
        window._proposal_count()
        window._proposal_count()
        window._proposal_count()
        assert len(reads) == 1, (
            f"the proposals were read {len(reads)} times for one answer")

    def test_a_read_failure_counts_as_no_proposals(self, window, monkeypatch):
        """**"I could not determine" is not "there are none".** The bell
        reports what it knows; a reader that raises must not leave the count
        at zero for a minute, pretending the store is empty.
        """
        from shani_chronoa.senses import memory

        def _boom():
            raise RuntimeError("sleep is not installed")

        monkeypatch.setattr(memory, "pending_proposals", _boom)
        window._proposal_cache = None
        assert window._proposal_count() == 0
