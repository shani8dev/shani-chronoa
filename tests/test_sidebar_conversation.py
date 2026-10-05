"""The sidebar says one thing once.

Two rows both called "Conversation" is the defect this file exists for, and it
was found by looking at the sidebar rather than by reading it: the sidebar opens
with an untitled "Conversation" row for the live chat, and a "Conversation"
section heading sat directly above a "Conversations" panel for the saved list.
The word appeared three times and named two different things, and a person
scanning the list had no way to tell which was the chat they were already in.

Everything here reads the built widget tree rather than the module constants,
because the constants were right and the tree was wrong. `CHAT_TITLE` has
always said "Conversation"; for a long time the row it named was never built at
all, and when it was built it was appended below every section - the comment
above it said "first and above every section" and the code said the opposite.
A test that asserted on the constants would have passed through both.

The negative control matters for the ordering assertion: `select()` on a row
that is not in the tree raises rather than silently passing, so the ordering
check compares positions in the built tree instead of trusting a comment.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path
from typing import List

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.gui.sidebar import CHAT_TITLE, SidebarPage  # noqa: E402


def _groups(widget: Gtk.Widget) -> List[Adw.PreferencesGroup]:
    found: List[Adw.PreferencesGroup] = []
    if isinstance(widget, Adw.PreferencesGroup):
        found.append(widget)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_groups(child))
        child = child.get_next_sibling()
    return found


def _rows(widget: Gtk.Widget) -> List[Adw.ActionRow]:
    found: List[Adw.ActionRow] = []
    if isinstance(widget, Adw.ActionRow):
        found.append(widget)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_rows(child))
        child = child.get_next_sibling()
    return found


@pytest.fixture(scope="module")
def sidebar() -> SidebarPage:
    return SidebarPage(None, None, None)


class TestNoDuplicateNames:
    def test_no_two_rows_share_a_title(self, sidebar):
        """The defect, stated as the property that must hold.

        A duplicate title is not a cosmetic repetition - it is two destinations
        behind one label, so the reader cannot know which one they are choosing.
        """
        titles = [row.get_title() for row in _rows(sidebar)]
        repeats = {
            title: count
            for title, count in collections.Counter(titles).items()
            if count > 1
        }
        assert not repeats, f"sidebar rows sharing a title: {repeats}"

    def test_the_word_conversation_names_exactly_one_row(self, sidebar):
        titles = [row.get_title() for row in _rows(sidebar)]
        assert titles.count(CHAT_TITLE) == 1, (
            f"{CHAT_TITLE!r} should name the live chat and nothing else; got {titles}"
        )

    def test_no_row_is_titled_the_same_as_the_section_it_sits_in(
        self, sidebar):
        """A section heading that repeats a row inside it reads as two of a thing.

        This is the shape the sidebar had: a heading "Conversation" above a
        panel, and a separate row "Conversation" above the heading.
        """
        clashes = []
        for group in _groups(sidebar):
            heading = group.get_title()
            if not heading:
                continue
            rows = _rows(group)
            if any(row.get_title() == heading for row in rows):
                clashes.append(heading)
        assert not clashes, f"section heading repeated by a row inside it: {clashes}"

    def test_the_saved_list_is_not_called_conversation(self, sidebar):
        """Singular is the live chat; plural is what is on disk.

        The two were distinguished by one letter in the source and by nothing at
        all on screen, which is not a distinction a person can use.
        """
        titles = [row.get_title() for row in _rows(sidebar)]
        assert "Conversations" in titles, (
            f"the saved-conversation panel should still be reachable; got {titles}"
        )


class TestOrdering:
    def test_the_conversation_row_is_the_first_row_in_the_sidebar(
        self, sidebar):
        """The comment said "first and above every section" and the code said
        otherwise, for as long as both existed.

        The chat is the state the window opens in. If the way back to it is the
        last of twenty rows, it is not the way back to anything.
        """
        rows = _rows(sidebar)
        chat_rows = [row for row in rows if row.get_title() == CHAT_TITLE]
        assert len(chat_rows) == 1, f"expected one chat row, got {len(chat_rows)}"
        assert rows[0] is chat_rows[0], (
            "the conversation row is not first; the order is "
            f"{[r.get_title() for r in rows][:4]}..."
        )

    def test_the_chat_row_is_in_an_unheaded_group(self, sidebar):
        """It sits above the sections, so it carries no heading of its own.

        A heading here would reintroduce the exact collision this file was
        written for.
        """
        owning = [
            group for group in _groups(sidebar)
            if any(row.get_title() == CHAT_TITLE for row in _rows(group))
        ]
        assert len(owning) == 1, f"chat row is in {len(owning)} groups"
        assert owning[0].get_title() in (None, ""), (
            f"the chat row's group is headed {owning[0].get_title()!r}"
        )


class TestSelectionStillMarksSomething:
    def test_selecting_the_chat_marks_the_chat_row(self, sidebar):
        """Opening the chat is the default state; it must be marked.

        `.sidebar-row.selected` has a real rule, so the marker is visible - but
        only if something sets it, and the chat row was `None` in this sidebar
        for long enough that `select(None)` had nothing to mark.
        """
        sidebar.select(None)
        chat_rows = [row for row in _rows(sidebar) if row.get_title() == CHAT_TITLE]
        assert chat_rows and chat_rows[0].has_css_class("selected"), (
            "opening the conversation marked nothing in the sidebar"
        )

    def test_selecting_a_panel_marks_it_and_clears_the_chat(self, sidebar):
        sidebar.select("diagnostics")
        marked = [
            row.get_title() for row in _rows(sidebar) if row.has_css_class("selected")
        ]
        assert marked == ["Diagnostics"], (
            f"expected only Diagnostics marked, got {marked}"
        )
        sidebar.select(None)

    def test_the_control_actually_marks_something(self, sidebar):
        """A negative control: if `select` marked nothing at all, the two
        assertions above would pass for the wrong reason on the first and fail
        on the second for an unrelated one.

        This pins that the class really does land on a row when the panel is a
        real one, so the assertions are about *which* row, not *whether*.
        """
        sidebar.select("diagnostics")
        try:
            marked = [row for row in _rows(sidebar) if row.has_css_class("selected")]
            assert len(marked) == 1, f"expected exactly one marked row, got {marked}"
        finally:
            sidebar.select(None)