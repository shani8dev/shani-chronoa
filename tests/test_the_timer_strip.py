"""The pending timers are above the composer, at every width.

**A running timer is a thing the person asked for, and at the width the
app opens at there was nowhere to see it.** The rail's Timers section held
them, and the rail is deleted below 1040px - the app opens at 460 - so a
kettle that was boiling was invisible on the window a person actually
uses. The only other place a timer appeared was the empty state's home
board, which disappears the moment a conversation starts.

So the timers live in a strip above the composer, where the task card
already lives, and **the rail no longer carries a second copy of them**.

**Cancel and five more minutes are buttons on the row.** They used to be
"a spoken or typed request", so stopping a kettle timer meant asking the
model to call a tool. These are the skill's own functions called
directly: the person's click, about their own timer.
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
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")

from shani_chronoa.gui import timers as timers_module  # noqa: E402


def _labels(widget):
    """Every label's text in the widget."""
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


def _buttons(widget):
    """Every button in the widget, by its tooltip."""
    queue, found = deque([widget]), {}
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Button):
            found[node.get_tooltip_text() or ""] = node
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found


@pytest.fixture
def a_timer(tmp_path, monkeypatch):
    """A real timer, with the scheduling stubbed out."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from shani_chronoa.skills import timer

    monkeypatch.setattr(timer, "_schedule", lambda *a, **k: (True, ""))
    monkeypatch.setattr(timer, "_unschedule", lambda *a, **k: (True, ""))
    monkeypatch.setattr(timer, "_systemd_available", lambda: True)
    return timer


class TestTheTimersAreOnScreen:
    def test_a_running_timer_is_shown_with_the_time_left(self, a_timer):
        """**The fact a person asked for is a fact they can see** - and the
        countdown reads as a countdown."""
        a_timer.set_timer(95, "kettle")
        strip = timers_module.TimerStrip()
        strip.refresh()
        text = "\n".join(_labels(strip))
        assert "kettle" in text, text
        assert "1m 3" in text or "1m 35" in text, text
        assert strip.get_visible(), "the strip is hidden with a timer running"

    def test_nothing_is_shown_when_no_timer_is_running(self, a_timer):
        """**A strip that is always there is chrome**, and the window's
        minimum height is a promise - so the strip hides itself entirely.

        **The transition, not the first refresh.** A fresh strip is hidden
        by its constructor, so a test that only looks at a fresh one cannot
        see the refresh's decision at all - it would pass with the decision
        removed. So the strip is shown a timer first, and the timer goes
        away underneath it.
        """
        a_timer.set_timer(95, "kettle")
        strip = timers_module.TimerStrip()
        strip.refresh()
        assert strip.get_visible(), "the strip did not show a running timer"
        a_timer.cancel_timer(a_timer.active_timers()[0][0])
        strip.refresh()
        assert not strip.get_visible(), (
            "the timer strip is on screen with no timer running")
        assert not _labels(strip), "the strip drew something with nothing to say"

    def test_the_strip_says_what_the_rail_used_to(self, a_timer):
        """**The word a person recognises** - "Timers" - is the heading, so
        a row that says "kettle" and "1m 35s left" is not a mystery."""
        a_timer.set_timer(95, "kettle")
        strip = timers_module.TimerStrip()
        strip.refresh()
        assert "Timers" in "\n".join(_labels(strip))


class TestTheTimersCanBeStopped:
    def test_a_timer_can_be_extended_and_cancelled(self, a_timer):
        """**The person's click, about their own timer** - both used to need
        a chat request."""
        a_timer.set_timer(95, "kettle")
        strip = timers_module.TimerStrip()
        strip.refresh()
        tips = _buttons(strip)
        assert "Five more minutes" in tips and "Cancel this timer" in tips, list(tips)

        tips["Five more minutes"].emit("clicked")
        left = a_timer.active_timers()[0][2]
        assert left > 300, f"+5 min did not extend the timer ({left}s left)"

        tips = _buttons(strip)
        tips["Cancel this timer"].emit("clicked")
        assert a_timer.active_timers() == [], "Cancel left the timer running"
        assert not strip.get_visible(), (
            "the strip is still on screen after its only timer was cancelled")


class TestTheStripSurvivesABrokenTimerStore:
    def test_a_store_that_cannot_be_read_leaves_the_strip_empty(
            self, monkeypatch):
        """**A glance must not take the window down**, and a store that
        cannot be read is no timers - not a crash."""
        monkeypatch.setattr(timers_module, "pending_timers",
                            lambda: (_ for _ in ()).throw(RuntimeError("no store")))
        strip = timers_module.TimerStrip()
        strip.refresh()
        assert not strip.get_visible()

    def test_a_refresh_never_raises(self, monkeypatch):
        """**The window's tick refreshes it**, and a tick that dies takes
        the rest of the window with it."""
        monkeypatch.setattr(timers_module, "pending_timers",
                            lambda: (_ for _ in ()).throw(RuntimeError("no store")))
        strip = timers_module.TimerStrip()
        strip.refresh()          # must not raise
        assert not strip.get_visible()


def test_human_seconds_reads_as_a_countdown():
    """**The same wording the rail used**, moved with the timers."""
    assert timers_module.human_seconds(45) == "45s"
    assert timers_module.human_seconds(192) == "3m 12s"
    assert timers_module.human_seconds(3600 + 240) == "1h 04m"
    assert timers_module.human_seconds(-5) == "0s"
