"""A sidebar row title that does not fit must ellipsise, not hyphenate.

Measured in a render at 760px: **"Meetings" came out as `Meetin-` over `gs`** -
one word broken across two lines with a hyphen - while "My files" in the row
above it, which can break at its space, looked perfectly normal. One panel, one
row, and the only one on screen that broke.

**It survived because every render in this project's history was 460 wide**, where
the app's own breakpoints collapse the sidebar entirely, so the rows whose titles
are long enough to break were never in a picture at all. The defect was not
subtle; it was unreachable by the only instrument available. That is also why
this file is worth having: `CHRONOA_FORCE_SIZE` is what made the width reachable,
and the fix is only this small because the render narrowed it to one row.

**One line, not a wider sidebar.** The sidebar's width is the window's to decide,
and at 760px there is no width to give back that would not come out of the
conversation. A title that cannot fit says so with an ellipsis and stays one
line; one that wraps changes the height of a single row in a list of twenty,
which is what makes the list look broken rather than narrow.

**`set_title_lines(1)`, not `set_title_widget`.** Measured on this libadwaita:
`Adw.ActionRow` has no `set_title_widget` - `AttributeError` - and the first
version of the fix used it and broke every window that built a sidebar. The
clamp is the only lever the row offers, and it keeps the title a *string*, so
`get_title()` still answers the panel name; a title widget would have made it
answer `""`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa.gui.sidebar import SidebarPage  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    if node is not None:
        out.append(node)
    if hasattr(node, "get_first_child"):
        child = node.get_first_child()
        while child is not None:
            _walk(child, out)
            child = child.get_next_sibling()
    return out


def _rows():
    """Every `Adw.ActionRow` the real sidebar builds."""
    page = SidebarPage(None, lambda: None, lambda _key: None)
    return [n for n in _walk(page) if isinstance(n, Adw.ActionRow)]


class TestASidebarTitleStaysOnOneLine:

    def test_every_panel_row_is_clamped_to_one_line(self):
        """**The witness.** `get_title_lines()` is 0 by default, which means
        unlimited, which is the state the hyphenated render came from."""
        rows = _rows()
        assert rows, "the sidebar built no rows at all"
        unlimited = [r.get_title() for r in rows if r.get_title_lines() == 0]
        assert not unlimited, (
            f"{len(unlimited)} sidebar rows can wrap - {unlimited[:5]} - and at "
            f"760px a one-word title that cannot fit is broken across two lines "
            f"with a hyphen rather than ending in an ellipsis")

    def test_and_the_panel_still_has_a_readable_title(self):
        """**The control, and what `set_title_widget` would have cost.**

        A title *widget* replaces the string, so `get_title()` answers `""` and
        anything that names a panel by reading it back - a test, a tooltip, a
        search index - loses the name. The clamp keeps it.
        """
        rows = _rows()
        titles = [r.get_title() for r in rows]
        assert all(titles), "a sidebar row has no title any more"
        assert "Meetings" in titles, (
            f"the row that hyphenated is gone rather than fixed: {titles}")

    def test_the_conversation_row_is_clamped_too(self):
        """The conversation row is built separately from the panel rows, so it
        is exactly the kind of thing a fix applied to one loop misses."""
        page = SidebarPage(None, lambda: None, lambda _key: None)
        chat = [n for n in _walk(page)
                if isinstance(n, Adw.ActionRow) and n.get_title() == "Conversation"]
        assert chat, "the conversation row is missing"
        assert chat[0].get_title_lines() == 1, (
            "the conversation row can still wrap while every panel row cannot")


class TestTheLeverExists:
    """One measurement, because the first version of the fix used an API that
    does not exist on this libadwaita and broke the whole window."""

    def test_action_row_has_no_set_title_widget_here(self):
        """Why the fix is a clamp and not a title widget."""
        assert not hasattr(Adw.ActionRow(), "set_title_widget"), (
            "this libadwaita now has set_title_widget - the clamp may no longer "
            "be the only lever, and this comment is what should send someone back")

    def test_the_clamp_is_readable_back(self):
        row = Adw.ActionRow(title="Meetings")
        row.set_title_lines(1)
        assert row.get_title_lines() == 1
        assert row.get_title() == "Meetings", (
            "clamping the title also cleared it")
        assert isinstance(row, Gtk.Widget)