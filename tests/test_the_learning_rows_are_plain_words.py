"""The Learning panel's rows say what a person would say.

**The panel used to be titled in the learning layer's own vocabulary** -
"A distilled router", "The outcome model", "Does the bandit beat chance"
- and a row title in a panel anyone can open is not the place for a
module's name. A person reading "What it has learned" was asked to know
what a *distilled router* is.

So the titles are plain words, and **the internal name is on the hover**,
so a developer can still map a row to the function it came from.

**The plain name and the internal name are one table**, not two, so
they cannot drift apart - which is the shape this repository keeps
paying for wherever a second hand-kept list appears.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")
Adw = pytest.importorskip("gi.repository.Adw", reason="libadwaita is not installed")

Adw.init()

from shani_chronoa.gui.surfaces import learning  # noqa: E402

#: The words that are the learning layer's vocabulary rather than a
#: person's. **Checked as words, not as phrases**, so a title that says
#: "the router" is caught as well as one that says "A distilled router".
JARGON = ("router", "outcome model", "bandit", "stand-in", "routing pairs",
          "distilled", "arms", "skill pairs")


class _App:
    application_id = "test.chronoa.learningrows"

    def activate(self):
        pass


def _rows():
    """The panel's rows, by title.

    **Walked from the page**, not read off a list: the panel publishes no
    row list, and the rows are what the page is made of.
    """
    page = learning.build(_App())
    out = {}
    stack = [page]
    while stack:
        node = stack.pop()
        if type(node).__name__ == "ActionRow":
            out[node.get_title()] = node
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return out


class TestTheTitlesArePlain:
    def test_no_row_title_carries_an_internal_name(self):
        """**A row title in a panel anyone can open is not the place for a
        module's name.**"""
        for title in learning.ROW_TITLES:
            lowered = title.lower()
            for word in JARGON:
                assert word not in lowered, (
                    f"the row title {title!r} still says {word!r}")

    def test_the_titles_are_still_distinct(self):
        """**Two rows with the same title are two answers a reader cannot
        tell apart** - and the old table was checked for this."""
        assert len(set(learning.ROW_TITLES)) == len(learning.ROW_TITLES)

    def test_the_plain_name_and_the_internal_name_are_one_table(self):
        """**Two lists drift.** The titles are *derived* from the table, so
        there is no second copy to fall behind."""
        assert learning.ROW_TITLES == tuple(title for title, _ in learning.ROWS)
        assert len(learning.ROWS) == len(learning.ROW_TITLES)


class TestTheInternalNameIsOnTheHover:
    def test_every_row_carries_what_it_is_called_in_the_code(self):
        """**The plain title is for the person and the internal name is for
        the developer**, and both have to be there."""
        rows = _rows()
        for title, internal in learning.ROWS:
            row = rows.get(title)
            assert row is not None, f"the row {title!r} is not on the panel"
            tooltip = row.get_tooltip_text() or ""
            # **The hover has to be there at all.** An empty internal name
            # would otherwise satisfy "the name is in the tooltip", because
            # every string contains the empty one.
            assert tooltip, f"the row {title!r} has no hover at all"
            assert internal in tooltip, (
                f"the row {title!r} does not say what it is called in the "
                f"code - the hover is where a developer maps it")

    def test_the_internal_name_names_something(self):
        """**A tooltip that says nothing is not a mapping.** Every row's
        internal name says what it is, in the code's own words."""
        for title, internal in learning.ROWS:
            assert internal.strip(), title
            assert len(internal) > 5, (title, internal)
