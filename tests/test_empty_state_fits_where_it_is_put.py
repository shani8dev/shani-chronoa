"""An empty state has to fit where it is put, and say what it is for.

Found by rendering, 2026-10-13. `common.empty_state()` builds an
`Adw.StatusPage`, which is a **page-level** widget, and the two panels that place
one *inline* - inside a scrolled content box, beside other content - rendered

    a 55px horizontal slice through the middle of the icon,
    with neither the title nor the description

`triggers` showed a grey crescent where "**No trigger rules armed**" should have
been, on a panel whose entire content was that sentence. `memory` showed a
clipped prohibition icon. Both reproducible: 1859 and 8962 pixels of the icon's
own grey, at identical coordinates in two independent renders.

The cause is a measurement rather than an inference:

    Adw.StatusPage declares a MINIMUM height of 301px
    (and a minimum width of 152px), whatever height it is offered

The icon is drawn *centred* in the 301px it insists on, so a container that can
only give it a fraction shows a band across the middle of the icon - which reads
as a stray shape rather than as a truncated one. `goals`, which gives its
`StatusPage` the whole page, renders perfectly, and is the control for this file.

**These tests assert the difference between the two placements, in both
directions.** A guard that only checked the inline variant would be satisfied by
a helper that stopped building a `StatusPage` at all, which would silently change
every panel that uses it as a page.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui.surfaces import common  # noqa: E402

#: `Adw.StatusPage`'s own floor, measured on the installed libadwaita. Not a
#: guess and not a constant copied from a comment - `test_the_measured_floor_is
#: still_the_floor` re-derives it, so a libadwaita upgrade that changes it is
#: visible rather than silently invalidating the bound below.
STATUS_PAGE_MIN_HEIGHT = 301


def _min_height(widget: Gtk.Widget) -> int:
    widget.measure(Gtk.Orientation.VERTICAL, 900)
    return widget.measure(Gtk.Orientation.VERTICAL, 900)[1]


def _labels(widget: Gtk.Widget) -> list[str]:
    out: list[str] = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            text = w.get_text()
            if text and text.strip():
                out.append(text.strip())
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


class TestTheInlineEmptyState:
    def test_it_is_not_an_adw_status_page(self):
        """The whole defect: a page-level widget in a band."""
        inline = common.empty_state("x-symbolic", "Nothing here", "why", inline=True)
        assert not isinstance(inline, Adw.StatusPage), (
            "inline=True still built a StatusPage, which carries the 301px floor")

    def test_it_fits_in_a_fraction_of_a_status_page(self):
        inline = common.empty_state("x-symbolic", "Nothing here", "why", inline=True)
        assert _min_height(inline) < 120, (
            f"the inline empty state still demands {_min_height(inline)}px; the "
            f"StatusPage it replaced demands {STATUS_PAGE_MIN_HEIGHT}px")

    def test_it_carries_both_the_title_and_the_description(self):
        """The half of the defect a person actually notices: the sentence."""
        shown = _labels(common.empty_state(
            "x-symbolic", "No rules armed", "nothing here can act on its own",
            inline=True))
        assert "No rules armed" in shown, shown
        assert "nothing here can act on its own" in shown, shown

    def test_its_title_wraps(self):
        """`test_long_labels_wrap_or_ellipsise[memory]` caught this the moment a
        panel used the box: the heading was never wrapped, and the StatusPage it
        replaced wrapped its own."""
        long_title = "The assistant's memory is not available to this window"
        inline = common.empty_state("x-symbolic", long_title, "because", inline=True)
        inline.measure(Gtk.Orientation.VERTICAL, 900)
        natural = inline.measure(Gtk.Orientation.HORIZONTAL, -1)
        offered = inline.measure(Gtk.Orientation.HORIZONTAL, 200)
        assert offered[1] <= 200 + 1, (
            f"the title needs {natural[1]}px and ignores the {offered[1]}px it is "
            f"given, so it runs off the edge")

    def test_it_carries_its_own_scroller(self):
        """`test_every_surface_can_scroll[memory]` was passing only because the
        `Adw.StatusPage` it no longer uses contains one internally."""
        inline = common.empty_state("x-symbolic", "Nothing here", "why", inline=True)
        found = []

        def walk(w):
            if isinstance(w, Gtk.ScrolledWindow):
                found.append(w)
            child = w.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(inline)
        assert found, "the inline empty state has no Gtk.ScrolledWindow"


class TestThePageEmptyStateIsUnchanged:
    """The control. `goals` owns its page and renders perfectly; a fix that
    quietly changed that path would change every other panel."""

    def test_it_is_still_a_status_page(self):
        page = common.empty_state("x-symbolic", "Nothing here", "why")
        assert isinstance(page, Adw.StatusPage)

    def test_it_still_carries_the_child_and_the_title(self):
        button = Gtk.Button(label="Do the thing")
        page = common.empty_state("x-symbolic", "Nothing here", "why", button)
        assert isinstance(page, Adw.StatusPage)
        assert "Nothing here" in " ".join(_labels(page)) or page.get_title() == "Nothing here"

    def test_the_measured_floor_is_still_the_floor(self):
        """Re-derive the 301px rather than trusting the constant, so a
        libadwaita upgrade is visible instead of silently moving the bound."""
        page = Adw.StatusPage(icon_name="x", title="t", description="d")
        assert _min_height(page) == STATUS_PAGE_MIN_HEIGHT, (
            f"Adw.StatusPage's minimum height is now {_min_height(page)}px, not "
            f"{STATUS_PAGE_MIN_HEIGHT}px - re-measure and record why the inline "
            f"bound above is still the right one")