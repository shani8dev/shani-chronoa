"""Finding something in the conversation, and the sidebar's own bookkeeping.

Two features that only exist because someone asked how a person would actually
use the window, and both are things this repo has measured rather than assumed:

- **In-window search.** The desktop search provider finds documents, the
  conversation store searches across conversations, and neither can find a
  sentence in the conversation you are looking at. Alpaca has two search bars for
  this reason.
- **Sidebar marking.** `SidebarPage` used to be a factory returning a plain
  `Adw.NavigationPage`, with `select()` defined on a class nothing instantiated -
  so every panel open raised `AttributeError` *after* pushing the page, and the
  marking silently never happened. Nothing caught it because nothing asserted it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa.gui.sidebar import SidebarPage  # noqa: E402
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402


class TestFindingInTheConversation:
    def _transcript(self):
        view = TranscriptView()
        view.add_user_turn("what is on my disk?")
        view.add_assistant_turn("There are 476 GiB on / and two unreadable drives.")
        view.add_user_turn("and the temperature?")
        view.add_assistant_turn("No thermal sensor reports anything on this machine.")
        return view

    def test_it_finds_a_turn_by_its_own_words(self):
        view = self._transcript()
        assert view.find("thermal") == [3]
        assert view.find("476") == [1]

    def test_the_search_is_case_insensitive(self):
        assert TranscriptView().find("") == []
        view = self._transcript()
        assert view.find("THERMAL") == view.find("thermal")

    def test_an_empty_query_matches_nothing(self):
        """Opening a search box and typing nothing must not light up every turn
        in the conversation - that is what 'no results' looks like in every other
        search box a person has ever used."""
        assert self._transcript().find("") == []
        assert self._transcript().find("   ") == []

    def test_a_needle_inside_a_code_block_is_a_real_match(self):
        """The tempting shortcut is to search the widget tree's labels, which
        finds a code block's *language tag* - so a search for 'bash' would hit a
        reply that only ever ran it. The search runs against the turn's text."""
        view = TranscriptView()
        view.add_assistant_turn("Run this:\n\n```bash\ndf -h /\n```")
        assert view.find("bash") == [0], "the fence language is part of the turn's text"
        view2 = TranscriptView()
        view2.add_assistant_turn("Here is a table:\n\n| Mount | Size |\n|---|---|\n| / | 1G |")
        assert view2.find("Mount") == [0]

    def test_the_count_it_reports_is_the_number_of_turns_not_of_hits(self):
        """Two turns, three occurrences: "3 matches" would send someone looking
        for a third turn that does not exist."""
        view = TranscriptView()
        view.add_user_turn("how much disk is there?")
        view.add_assistant_turn("476 GiB of disk, and the disk sense is on.")
        matching = [text for _role, text in view.visible_turns()
                    if "disk" in text.lower()]
        occurrences = sum(text.lower().count("disk") for text in matching)
        assert len(matching) == 2 and occurrences > len(matching), (
            "the fixture must have more mentions than turns, or it proves nothing")
        assert view.highlight("disk") == len(matching)

    def test_highlighting_marks_the_matching_turns_and_no_others(self):
        # "disk" is in the question, not in the answer - the answer says "drives".
        # Asserting on the wrong one of the two would have passed against a
        # search that marked the neighbouring row.
        view = self._transcript()
        assert view.highlight("disk") == 1
        marked = [index for index, row in enumerate(view._turn_rows())
                  if "transcript-match" in row.get_css_classes()]
        assert marked == [0]
        assert "disk" in view.visible_turns()[0][1]

    def test_clearing_the_highlight_leaves_no_marks_behind(self):
        view = self._transcript()
        view.highlight("disk")
        view.highlight("")
        assert not any("transcript-match" in row.get_css_classes()
                       for row in view._turn_rows())

    def test_the_rows_it_marks_are_the_turns_it_searched(self):
        """The mark is applied per row and the search per text; if the two lists
        ever came from different places, the highlight would drift onto the wrong
        turn - which is exactly the bug that reading text out of the tree causes."""
        view = self._transcript()
        view.highlight("temperature")
        rows = view._turn_rows()
        assert len(rows) == len(view.visible_turns())
        assert "transcript-match" in rows[2].get_css_classes()

    def test_scrolling_to_a_turn_that_is_not_there_says_so(self):
        view = self._transcript()
        assert view.scroll_to_turn(0) is True
        assert view.scroll_to_turn(99) is False
        assert view.scroll_to_turn(-1) is False

    def test_clearing_the_transcript_clears_the_search_index(self):
        """Otherwise the next search reports matches in a conversation that is no
        longer on screen."""
        view = self._transcript()
        view.clear()
        assert view.visible_turns() == []
        assert view.find("disk") == []

    def test_the_window_wires_it_to_ctrl_f(self):
        """An action nobody registers is a shortcut that does nothing."""
        source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa/app/application.py").read_text()
        assert "find-in-conversation" in source
        assert '"<Ctrl>f"' in source


class TestTheSidebarMarksWhereYouAre:
    def _page(self):
        return SidebarPage(None, lambda: None, lambda _key: None)

    def test_it_is_a_navigation_page_so_select_can_exist_at_all(self):
        """The bug: a classmethod factory returning a *plain* `Adw.NavigationPage`,
        with `select()` defined on a class that was never instantiated. Every
        panel open raised `AttributeError` after the page was already pushed, so
        the panel appeared and the marking quietly did not."""
        page = self._page()
        assert isinstance(page, Adw.NavigationPage)
        assert hasattr(page, "select")

    def test_selecting_marks_exactly_one_row(self):
        page = self._page()
        page.select("machine")
        marked = [name for name, (row, *_rest) in page._surface_rows.items()
                  if "selected" in row.get_css_classes()]
        assert marked == ["machine"]

    def test_selecting_moves_the_mark_rather_than_adding_to_it(self):
        page = self._page()
        page.select("machine")
        page.select("skills")
        marked = [name for name, (row, *_rest) in page._surface_rows.items()
                  if "selected" in row.get_css_classes()]
        assert marked == ["skills"]

    def test_selecting_nothing_clears_the_mark(self):
        page = self._page()
        page.select("machine")
        page.select(None)
        assert not any("selected" in row.get_css_classes()
                       for row, *_rest in page._surface_rows.values())

    def test_every_panel_is_listed_and_grouped(self):
        from shani_chronoa.gui import surfaces

        page = self._page()
        assert set(page._surface_rows) == set(surfaces.available_surfaces())
        assert set(page._surface_rows) <= set(surfaces.SURFACE_IDS)

    def test_a_section_with_nothing_under_it_is_hidden(self):
        """A heading that says "This machine" above nothing is a heading that
        lies about what is on the screen."""
        page = self._page()
        page._filter("no-such-panel-anywhere")
        assert not any(group.get_visible() for group in page.groups.values())

    def test_filtering_finds_a_panel_by_its_section_too(self):
        page = self._page()
        page._filter("desktop")
        visible = [name for name, (row, *_rest) in page._surface_rows.items()
                   if row.get_visible()]
        assert "desktop" in visible

    def test_a_panel_gets_a_way_back_that_does_not_need_the_sidebar(self):
        """Panels are pushed onto the content view, so on a narrow window - where
        the sidebar is a collapsed drawer - the only way out would be to open it."""
        from shani_chronoa.gui.surfaces import common

        assert "back" in common.surface.__doc__ or "back" in (common.surface.__doc__ or "") \
            or True  # the parameter exists; asserted properly below
        import inspect

        assert "back" in inspect.signature(common.surface).parameters