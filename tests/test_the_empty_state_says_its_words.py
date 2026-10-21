"""The empty state's words must be on screen.

**An inline empty state was clipped to a sliver of its icon.** Measured
from the real app's widget tree, on the Triggers page:

    the page's content box        657px
    the arming form alone         439px
    the empty state's scroller      58px   <- given the leftover
    the empty state's content      154px   <- what it holds

A `Gtk.ScrolledWindow` reports a small minimum height - it can scroll -
so when a page is taller than the window, **the only child that can be
shrunk absorbs the shortfall**, and what got clipped was the empty
state's own title and description. The page then said nothing about
what was absent, and the empty state's title was the fourth copy of
"no rules armed" that never reached the reader.

**So the helper does not put the empty state's content in a scroller of
its own.** The page's own scroller - every page has one - does the
scrolling. A caller that needs a scroller *in its own tree* puts one
there: the memory panel, whose page is short and has no other, and
whose content would otherwise be unreachable in a short window.

The layout contract (`test_every_surface_can_scroll`) is the other
half of the witness: a panel whose empty state is showing must still
hold a scroller.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

Adw = pytest.importorskip("gi.repository.Adw", reason="libadwaita is not installed")
Adw.init()


def test_the_inline_empty_state_is_not_inside_a_scroller_of_its_own():
    """**Nothing may squeeze the empty state's own words away.**

    A scroller of its own looks harmless - it is what the layout
    contract wants - and it is exactly what a crowded page shrinks
    first, because it is the only child that can be. The page's own
    scroller scrolls the page; this one was clipping the sentence
    that says what is missing.
    """
    from shani_chronoa.gui.surfaces import common

    widget = common.empty_state("icon-name", "A title", "A description", inline=True)
    assert not isinstance(widget, Gtk.ScrolledWindow), (
        "the inline empty state's content is inside a scroller of its own, and "
        "a crowded page shrinks that first - the words are clipped away")
    # The content is still there, whatever the caller wraps it in.
    labels = [n for n in _walk(widget) if isinstance(n, Gtk.Label)]
    assert [n.get_text() for n in labels], "the empty state has no words at all"


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out
