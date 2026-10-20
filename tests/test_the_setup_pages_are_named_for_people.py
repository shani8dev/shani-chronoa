"""The setup wizard's pages are named in words a person would use.

**"Brain", "Ears", "Eyes", "Imagine"** are the inside of this app - the
organ names in `organism.py`, which the Inventory panel is *about*. The setup
wizard is the first thing a new person reads, and it called its pages by
them.

So the **titles** are plain words - "Assistant", "Hearing", "Seeing",
"Pictures", "Search" - and the **keys stay**, because a stored page id has
to keep opening the same page.

**"Voice" is not on the banned list even though it is an organ's name.** The
voice page installs the voice, so "Voice" is the plain word for it as well,
and a guard that forbade it would have had to be wrong about the one title
that is right.

This is a guard on several halves at once: a rename that changed a key
would break a stored id, a rename that left a key in a title would put the
jargon back, and a title that carries its own article reads as "Open the The
assistant page" because the wizard says "Open the {title} page".
"""

from __future__ import annotations

import inspect
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa import setup_wizard  # noqa: E402

#: The organ names that are **not** plain words. "Voice" is deliberately
#: absent - see the module docstring.
ORGANS = ("Brain", "Ears", "Eyes", "Imagine")


def _titles():
    return {tag: title for tag, title in setup_wizard._SETUP_PAGES}


class TestThePagesAreNamedForPeople:
    def test_no_page_is_called_an_organ(self):
        """**The wizard is the first thing a new person reads**, and it is
        not the place for the inside of the app."""
        said = " ".join(_titles().values())
        found = [organ for organ in ORGANS if organ in said]
        assert not found, f"the setup pages are called {found}"

    def test_the_keys_are_unchanged(self):
        """**A stored page id has to keep opening the same page** - so the
        rename is in the title, never in the key."""
        titles = _titles()
        for key in ("brain", "ears", "eyes", "imagine", "memory"):
            assert key in titles, f"the page {key!r} is gone"

    def test_the_pages_say_what_they_are_for(self):
        """**A title has to say which thing it is**, since the page's own
        subtitle is what explains it."""
        titles = _titles()
        assert titles["brain"] == "Assistant"
        assert titles["ears"] == "Hearing"
        assert titles["eyes"] == "Seeing"
        assert titles["imagine"] == "Pictures"
        assert titles["memory"] == "Search"

    def test_no_title_carries_its_own_article(self):
        """**The wizard says "Open the {title} page"**, so a title called
        "The assistant" reads as "Open the The assistant page".

        This is the defect the rename introduced and reading it back caught:
        the title was fine on its own and wrong in the sentence it is put
        into."""
        for tag, title in setup_wizard._SETUP_PAGES:
            assert not title.startswith("The "), (
                f"the page {tag!r} is called {title!r}, and the wizard says "
                "'the {title} page' around it")


class TestTheForwardButtonReadsOneTable:
    """**The forward button names the page it goes to**, so it has to use the
    same words the page is called.

    The two used to be two hand-kept maps that happened to agree. A rename
    that changed one and not the other would have produced a button saying
    "Next: Brain" on a page called "Assistant" - which is exactly what
    happened here.
    """

    def test_the_titles_come_from_the_page_list(self):
        """**One table**, so the two cannot drift apart."""
        assert setup_wizard._TITLES == dict(setup_wizard._SETUP_PAGES)

    def test_the_builder_holds_no_second_table(self):
        """**A source check, and the reason is that the builder's titles
        were a closure over a local**: the labels are built inside
        `build_window`, so a test cannot read them without building a whole
        window.

        So the check is on the shape rather than the output - and it is
        aimed at the defect, which was a second literal table of page
        titles living inside the builder."""
        source = inspect.getsource(setup_wizard.build_window)
        assert "_TITLES" in source, (
            "the builder no longer reads the one table of page titles")
        assert '{"welcome"' not in source, (
            "the builder holds a second copy of the page titles, which is "
            "the copy that drifts")


class TestTheHelpGroupsAgree:
    def test_no_group_is_called_an_organ(self):
        """**The Help window's headings are read by a person too**, and the
        groups are the same words."""
        said = " ".join(capabilities.GROUP_ORDER)
        found = [organ for organ in ORGANS if organ in said]
        assert not found, f"the help groups are called {found}"

    def test_every_group_a_tool_names_is_in_the_order(self):
        """**The heading a tool is filed under has to be one the Help
        shows**, or the tool appears under a heading nobody can find."""
        from shani_chronoa.skills import discover_skills

        tools, _ = discover_skills()
        for capability in capabilities.find_capabilities(tools):
            assert capability.group in (capabilities.GROUP_ORDER
                                        + (capabilities.OTHER,)), (
                f"{capability.tool} is filed under {capability.group!r}, which "
                "the Help does not show")
