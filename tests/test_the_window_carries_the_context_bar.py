"""The window carries the context bar, and the tick keeps it current.

**The rail's Context card was the only place that drew "how full the
model's window is", and the rail is deleted below 1040px** - the app
opens at 460. The card's own docstring calls the ambiguity it drew
"the single most alarming thing that can happen to a local assistant":
without it, "it forgot what I said" is indistinguishable from "the window
is full".

So the bar is above the composer, at every width. This file is about the
*wiring*: that the bar is really in the window, and that the tick that
refreshes the rail refreshes it too.

**The tick is the part that would rot quietly.** The rail is hidden on
the narrow window where the bar matters most, so a tick that only
refreshed the rail would leave the bar stale exactly where it is the only
thing on screen.
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

from shani_chronoa import context_meter  # noqa: E402
from shani_chronoa.gui import context_bar as context_bar_module  # noqa: E402
from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


class _App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.shani.chronoa.contextbar")


@pytest.fixture
def window():
    return ChronoaWindow(_App())


def _children(widget):
    """The direct children of a box, in order.

    **GTK4 has no `get_children()`** - it is a GTK3 name.
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


class TestTheWindowCarriesTheContextBar:
    def test_the_bar_is_in_the_window(self, window):
        """**A widget built and not put anywhere is a widget nothing
        sees**."""
        assert isinstance(getattr(window, "_context_bar", None),
                          context_bar_module.ContextBar), (
            "the window has no context bar")
        assert window._context_bar in _children(window._chat_column), (
            "the context bar is not in the chat column, so it is not on screen")

    def test_the_bar_reads_the_same_report_the_rail_reads(self, window, monkeypatch):
        """**One answer to one question**: the rail's Context card and this
        bar must not be able to disagree about how full the window is."""
        report = context_meter.Report(limit=8192, total_tokens=7000,
                                      total_chars=28000, reply_reserve=1024)
        monkeypatch.setattr(type(window), "_rail_context", lambda self: report)
        window._on_rail_tick()
        assert window._context_bar.get_visible(), (
            "the bar did not show a window that is 87% used")
        # **The report's own number**, rounded as the report rounds it -
        # the bar shows what the meter says, not a second calculation.
        assert window._context_bar._bar.get_value() == pytest.approx(
            report.usable_percent / 100.0), (
            "the bar is not showing the report the rail reads")

    def test_a_quiet_window_draws_no_bar(self, window, monkeypatch):
        """**A bar for a window at 15% is chrome on every window** - and
        the window's minimum height is a promise."""
        report = context_meter.Report(limit=8192, total_tokens=1000,
                                      total_chars=4000, reply_reserve=1024)
        monkeypatch.setattr(type(window), "_rail_context", lambda self: report)
        window._on_rail_tick()
        assert not window._context_bar.get_visible()
