"""The empty state answers "what can I ask?", and cannot go out of date.

Thirty-four panels across seven groups is a wall, and the composer's placeholder
said only "Type a message…". So the person a newcomer most is - one who has just
finished setup and wants to know what the thing can do - had to guess a question
and then read a list to find out whether they had guessed right. That is the
wrong order: the list is the answer and it was behind the guessing.

**The property worth protecting is that the row is built from the registry.**
A hand-written list of example questions would be wrong the day a panel was
added or renamed, and the wrongness would be invisible: an example that no
longer works still reads as a sentence. So these tests are mostly about *where
the words come from* rather than about how the row looks - which is the one
thing nobody can check without a picture, and this session could not get one.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WINDOW = REPO / "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"
SURFACES = REPO / "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/__init__.py"


def _source() -> str:
    return WINDOW.read_text(encoding="utf-8")


def _starter_count() -> int:
    """The chip count, read out of the module rather than restated here.

    **Read, not copied.** A second copy of a number is `skills_total`,
    `42/87/70` and "105 characters" - three recorded incidents in this
    repository of a value drifting from the code it describes.
    """
    m = re.search(r"^_STARTER_CHIPS = (\d+)", _source(), re.M)
    assert m, "the module declares no _STARTER_CHIPS"
    return int(m.group(1))


class TestTheStarterRowCannotDrift:
    def test_the_row_is_few_not_thirty_four(self):
        """**And the point is that it is few.** Thirty-four chips on the main
        screen is the wall this row was added to dissolve, so a count that grows
        towards the registry size defeats it while every individual chip stays
        perfectly correct.
        """
        count = _starter_count()
        assert count <= 8, (
            f"the empty state offers {count} chips. The row exists to show that "
            f"asking is something you can do here; at the size of the sidebar it "
            f"is the sidebar, and the newcomer is back to the wall")

    def test_the_words_come_from_the_registry(self):
        """**The anti-drift guard, and the reason the row can be trusted.**

        `_build_starter_row` must read `available_surfaces()` and nothing else.
        If a title is ever typed into the method instead, the row silently
        becomes a hand-written list: it would still render, still read as a
        sentence, and be wrong about whatever it names.
        """
        body = _source()
        start = body.index("def _build_starter_row(")
        chunk = body[start:body.index("\n    def ", start)]
        assert "available_surfaces()" in chunk, (
            "_build_starter_row does not read the registry, so its words are "
            "hand-written and will disagree with the sidebar")
        # A quoted sentence inside the method is the shape a hand-written list
        # takes. The docstring is exempt, so only the code after it is read.
        code = chunk[chunk.index('"""', chunk.index('"""') + 3) + 3:]
        assert not re.search(r'button\(\s*label=', code), (
            "_build_starter_row passes a literal label to a button, which is a "
            "hardcoded example that no registry will ever update")

    def test_the_chips_come_from_the_panels_that_exist(self):
        """The row and the sidebar must not be able to disagree.

        Read the same registry the row reads: if the first few panels are not
        what the row would show, this fails before anybody looks at a picture.
        """
        from shani_chronoa.gui.surfaces import SURFACE_IDS, available_surfaces

        assert len(SURFACE_IDS) > _starter_count(), (
            "the registry is no larger than the row, so the row is the whole "
            "sidebar and the distinction has gone")
        surfaces = available_surfaces()
        for name in SURFACE_IDS[:_starter_count()]:
            assert name in surfaces, (
                f"{name!r} is in SURFACE_IDS but not in available_surfaces(), so "
                f"the row would show a panel the sidebar cannot open")


class TestTheRowIsOnlyForAnEmptyConversation:
    def test_it_asks_emptiness_rather_than_guessing(self):
        """`len()` on a `Gtk.ScrolledWindow` raises, and the first version put
        that inside a `try` whose `except` said "empty" - so the row could never
        hide and every transcript carried five chips under it. **A check that
        cannot run, caught, becomes a check that always passes**, and it passed
        in the direction that made the new feature look working.

        So the question has to be asked of something that answers it.
        """
        widgets = (REPO / "usr/lib/shani-chronoa/shani_chronoa/gui/widgets.py")
        body = widgets.read_text(encoding="utf-8")
        assert "def is_empty(self)" in body, (
            "TranscriptView has no is_empty(), so the row's own emptiness "
            "check is back to guessing")
        assert "len(self._transcript)" not in _source(), (
            "the row asks len() of a widget, which raises TypeError and is "
            "caught into a permanent 'empty'")

    def test_a_failed_emptiness_check_hides_rather_than_shows(self):
        """**Fail closed.** If the question cannot be asked, the safe wrong
        answer is the one that adds nothing to a transcript that already has
        turns in it - not five chips under every answer the app has ever given.
        """
        body = _source()
        start = body.index("def _refresh_starter_row(")
        chunk = body[start:body.index("\n    def ", start)]
        tail = chunk[chunk.index("except Exception"):]
        assert "empty = False" in tail, (
            "an unreadable transcript leaves the row visible, so a full "
            "transcript grows five example questions out of its bottom")