"""The sidebar searches what a panel is for, not what it is called.

The filter matched `needle in title or needle in icon or needle in key or
needle in section.lower()` - substring against names. So "meeting" found
Meetings and "what time is my appointment" found nothing at all, while
`listening.py` says in its own first line that it asks whether Chronoa can
"transcribe what an application is playing", and nobody would ever guess that
word from the word *Listening*.

**A newcomer searching a box they have just installed types the thing they
want, not the name the app happened to give it.** So the filter now also reads
each panel's own description - which already exists, written next to the code it
describes by whoever wrote that code, and which is therefore a second thing that
cannot drift.
"""

import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SIDEBAR = REPO / "usr/lib/shani-chronoa/shani_chronoa/gui/sidebar.py"


def _source() -> str:
    return SIDEBAR.read_text(encoding="utf-8")


class TestSearchFindsWhatAPanelIsFor:
    def test_descriptions_are_indexed(self):
        """The positive case, run against the real registry.

        **Run, not asserted about the code.** A test that only reads the source
        can be satisfied by a filter nobody calls, which is the dead-control
        shape this repository records for `Registry.register` and
        `refused_tools`.
        """
        from shani_chronoa.gui.sidebar import _words_in

        words = _words_in("listening")
        assert len(words) > 100, (
            f"the listening panel indexed {len(words)} characters, so the search "
            f"index is empty and the filter has nothing to match on")
        assert "transcribe" in words, (
            "the listening panel's own description does not reach the index, "
            "so searching for what it does still finds nothing")

    def test_a_panel_is_found_by_a_word_only_its_description_has(self):
        """**And the words a newcomer would type are not the words in the
        title.** This is the whole claim: `Listening` and `transcribe` share
        nothing, and neither does `Driving` and `window on this desktop`.
        """
        from shani_chronoa.gui.sidebar import _words_in

        # **The words are read out of the panels' own docstrings rather than
        # assumed.** The first version asserted `driving` was findable by
        # "keyboard" - a word taken from a comment in `surfaces/__init__.py`,
        # not from `driving.py`, which says "Can Chronoa drive an app on this
        # desktop?" and never says keyboard. The test was wrong about the code
        # it was checking, which is the one failure mode a test written from
        # the same reading as the change cannot catch.
        for name, distinctive in (("listening", "transcribe"),
                                  ("driving", "click")):
            words = _words_in(name)
            assert distinctive in words, (
                f"{name!r} is not findable by {distinctive!r}, so a newcomer "
                f"searching for what it does finds nothing")

    def test_an_unknown_panel_indexes_nothing_rather_than_raising(self):
        """**A search index that fails must not take the sidebar down.**

        The first version of `_words_in` took the panel's builder as an
        argument, and the row-building loop carries `(name, title, icon)` with
        no builder in scope - so it raised `NameError` on the first row and the
        whole sidebar failed to build. Losing a search index is survivable;
        losing the list of panels is not.
        """
        from shani_chronoa.gui.sidebar import _words_in

        assert _words_in("no_such_panel_xyz") == ""

    def test_the_filter_reads_the_index(self):
        """**The wiring, because a correct index nothing consults is a dead
        control** - the shape this repository records more than once.
        """
        body = _source()
        assert 'self._surface_words.get(key, "")' in body, (
            "_filter does not consult the description index, so the function "
            "above is correct and changes nothing")
        assert "_surface_words" in body.split("def _filter")[1][:2000], (
            "the index is built but the filter never reads it")