"""The window carries the timers, and the tick keeps them current.

**The rail's Timers section was the only place a running timer could be
seen, and the rail is deleted below 1040px** - the app opens at 460, so
a kettle that was boiling was invisible on the window a person actually
uses.

The timers are a strip above the composer now (`gui/timers.py`), and
this file is about the *wiring*: that the strip is really in the window,
that it is really above the composer, and that the tick that made the
countdown a countdown still refreshes it.

**The tick is the part that would rot quietly.** The rail's refresh and
the strip's refresh are two calls in one tick, and the rail is hidden on
the narrow window where the timers matter most - so a tick that only
refreshed the rail would leave the timers stale exactly where they are
the only thing on screen.
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

from shani_chronoa.gui import timers as timers_module  # noqa: E402
from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


class _App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.shani.chronoa.timerstrip")


@pytest.fixture
def window():
    return ChronoaWindow(_App())


@pytest.fixture
def a_timer(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from shani_chronoa.skills import timer

    monkeypatch.setattr(timer, "_schedule", lambda *a, **k: (True, ""))
    monkeypatch.setattr(timer, "_unschedule", lambda *a, **k: (True, ""))
    monkeypatch.setattr(timer, "_systemd_available", lambda: True)
    return timer


def _children(widget):
    """The direct children of a box, in order.

    **GTK4 has no `get_children()`** - it is a GTK3 name, and the class that
    had it removed raised `AttributeError` on every call.
    """
    out = []
    child = widget.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _contains(outer, inner) -> bool:
    """Whether `inner` is `outer` or somewhere inside it."""
    if outer is inner:
        return True
    queue = deque([outer])
    while queue:
        node = queue.popleft()
        if node is inner:
            return True
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return False


def _labels(widget):
    queue, found = deque([widget]), []
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Label):
            found.append(node.get_label())
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found


class TestTheWindowCarriesTheTimers:
    def test_the_strip_is_in_the_window(self, window):
        """**A widget built and not put anywhere is a widget nothing
        sees** - the shape this file's subject has been in all along."""
        assert isinstance(getattr(window, "_timer_strip", None),
                          timers_module.TimerStrip), (
            "the window has no timer strip")
        assert window._timer_strip in _children(window._chat_column), (
            "the timer strip is not in the chat column, so it is not on "
            "screen")

    def test_the_strip_is_above_the_composer(self, window):
        """**Above the composer is the point**: a fact that scrolls away
        with the conversation is a fact the person has to hunt for."""
        column = window._chat_column
        children = _children(column)
        assert window._timer_strip in children
        # **The composer is wrapped** in the reading-width box, so it is
        # found by containment rather than by identity.
        composer_at = next((i for i, child in enumerate(children)
                             if _contains(child, window._composer_card)), None)
        assert composer_at is not None, "the composer is not in the chat column"
        assert children.index(window._timer_strip) < composer_at, (
            "the timer strip sits below the composer, so it is under the "
            "thing a person is typing into")

    def test_the_tick_refreshes_the_strip(self, window, a_timer):
        """**The tick that made the countdown a countdown** - and the rail
        it used to belong to is hidden on the narrow window, so a tick that
        only refreshed the rail would leave the timers stale exactly where
        they are the only thing on screen."""
        a_timer.set_timer(95, "kettle")
        assert "kettle" not in "\n".join(_labels(window._timer_strip)), (
            "the strip already shows a timer before anything refreshed it")
        window._on_rail_tick()
        assert "kettle" in "\n".join(_labels(window._timer_strip)), (
            "the tick did not put the running timer on screen")
