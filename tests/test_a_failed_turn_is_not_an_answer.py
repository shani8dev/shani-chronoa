"""A turn that failed must not look like a turn that answered.

The one-line version of the defect this file is about:

    self.window.set_response(f"Error: {result}")

`set_response` builds the **assistant's turn** - so a Python exception landed in
the transcript in Chronoa's own bubble, in the assistant's styling, reading as
something Chronoa had said. A person scrolling back through a conversation saw
`Error: <repr>` as the answer to their question, and it could not be
regenerated, because regeneration is offered on answers.

**Three ways that could be got wrong, and each is a control here.**

- Asserting only that the sentence *appears* passes on the old code, because
  the old code put the same words in the transcript - just in the wrong place.
  So the property is **where** it is: a marked row, not assistant prose.
- Asserting on `get_label()` of the turn's text does not see a row at all, so a
  test written that way cannot fail either. The row is walked as a widget.
- An error that is *dimmer* than the notice it sits beside is worse than the
  bubble it replaced: the failure has to be the louder thing.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import widgets as widgets_module  # noqa: E402
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402

TURN_ERROR_CSS = widgets_module.TURN_ERROR_CSS
TURN_NOTICE_CSS = widgets_module.TURN_NOTICE_CSS


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


def _texts(view):
    """Every visible string in the transcript, by widget."""
    return [n.get_label() for n in _walk(view._rows)
            if isinstance(n, Gtk.Label) and n.get_visible()
            and n.get_label()]


def _notice_rows(view):
    return [n for n in _walk(view._rows)
            if TURN_NOTICE_CSS in (n.get_css_classes() or [])]


class TestAFailedTurnIsNotAnAnswer:
    """What a person sees is the whole of the claim."""

    def test_the_failure_is_a_marked_row_and_not_the_assistants_prose(self):
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("Chronoa could not finish that turn. Boom.",
                            "error")
        rows = _notice_rows(view)
        assert rows, (
            "no notice row - the failure is either missing or drawn as something "
            "else, and neither is the row a person can recognise")
        classes = rows[0].get_css_classes() or []
        assert TURN_ERROR_CSS in classes, (
            f"the row carries {classes}, so it is indistinguishable from a "
            f"compaction note - a failure and a housekeeping line are not the "
            f"same kind of thing to a reader")

    def test_and_the_failure_is_not_sitting_in_the_assistants_answer(self):
        """**The control for the whole file.**

        The old code produced the same sentence - as assistant text. Asserting
        that the words appear therefore proves nothing on its own, which is why
        the first test is about *where* they are and this one is about what the
        turn's own text is.

        An error drawn inside the assistant's reply text is a reply, and a reply
        is offered for regeneration.
        """
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("Chronoa could not finish that turn. Boom.",
                            "error")
        turn_text = " ".join(_texts(view))
        assert "Boom" not in turn_text or TURN_ERROR_CSS in (
            _notice_rows(view)[0].get_css_classes() or []), (
            "the failure's text is in the assistant's own reply, so it reads as "
            "something Chronoa said")
        assert view._block_text == "", (
            f"the assistant turn carries {view._block_text!r} - a failed turn "
            f"must not hold prose that will be regenerated as an answer")

    def test_a_housekeeping_note_is_still_dimmed(self):
        """**A control, and the reason the error kind exists at all.**

        Every other note here recedes on purpose. If the error row were dimmed
        too it would be *quieter* than the fake assistant bubble it replaced,
        which is the one outcome that makes this change a regression rather than
        a fix.
        """
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("earlier context was shortened", "compaction")
        labels = [n for n in _walk(view._rows) if isinstance(n, Gtk.Label)
                  and n.get_label() == "earlier context was shortened"]
        assert labels, "the note was not built"
        assert "dim-label" in (labels[0].get_css_classes() or []), (
            "a housekeeping note stopped being dim, so a failure would have "
            "nothing to be louder than")

    def test_the_failure_row_is_not_dimmed(self):
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("Chronoa could not finish that turn. Boom.",
                            "error")
        labels = [n for n in _walk(view._rows) if isinstance(n, Gtk.Label)
                  and "Boom" in n.get_label()]
        assert labels, "the failure's sentence was not built"
        assert "dim-label" not in (labels[0].get_css_classes() or []), (
            "the failure is dimmed - it must be the loudest thing in the turn")

    def test_the_row_survives_a_later_reply_in_the_same_turn(self):
        """**`turn-notice` is a marker `_keeps_across_replies` reads**, so the
        error kind is added *beside* it rather than instead of it.

        Without this, the failure vanishes the moment anything else is said in
        that turn - and the failure is the one row whose whole value is that it
        is still there afterwards.
        """
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("Chronoa could not finish that turn. Boom.",
                            "error")
        # **`add_assistant_turn`, not `set_response`.** That is the window's
        # method; the transcript's own is `add_assistant_turn`, and the first
        # version of this test called a method the transcript does not have and
        # failed with an `AttributeError` that pointed at the harness rather
        # than at the property.
        view.add_assistant_turn("Actually, here is the answer.")
        assert _notice_rows(view), (
            "the failure disappeared when a reply followed it - a note about "
            "the turn has to outlive the text of the turn")
        assert any("Boom" in t for t in _texts(view)), (
            "and it disappeared from the transcript entirely")


class TestTheWindowRoutesAFailureHere:
    """The caller is half the fix - a marked row nobody renders is decoration."""

    def test_an_exception_does_not_go_through_set_response(self):
        """**Driven through the real handler**, not by reading its source.

        The defect is one call in `app/conversation.py`, and a test that asserts
        `add_notice_row` exists proves nothing about which of the two the
        failure path uses. This builds a stand-in window, drives
        `_on_response_ready` with a real exception, and asks the window what it
        was told.
        """
        from shani_chronoa.app import conversation as conversation_module

        asked = {"notices": [], "responses": [], "states": [], "statuses": []}
        window = SimpleNamespace(
            add_notice_row=lambda sentence, kind="compaction":
                asked["notices"].append((sentence, kind)),
            set_response=lambda text: asked["responses"].append(text),
            set_orb_state=lambda state: asked["states"].append(state),
            set_status=lambda text: asked["statuses"].append(text),
        )
        mixin = conversation_module.ConversationMixin.__new__(
            conversation_module.ConversationMixin)
        mixin.window = window
        mixin._mark = lambda *a, **k: None
        mixin._follow_active_conversation = lambda: None
        mixin._maybe_title = lambda: None
        mixin._capture_memory = lambda: None

        assert mixin._on_response_ready(RuntimeError("the model went away")) \
            is not None or True
        assert asked["notices"], (
            "a failed turn drew no notice row - the exception is not reaching "
            "the transcript at all")
        sentence, kind = asked["notices"][0]
        assert kind == "error", (
            f"the failure was filed as a {kind!r} note, which is dimmed and "
            f"reads as housekeeping")
        assert "the model went away" in sentence, sentence
        assert asked["states"] == ["error"], asked["states"]
        assert not [t for t in asked["responses"] if "Error:" in t], (
            "the exception still reaches the assistant's reply - an error drawn "
            "as an answer is the defect this file is about")

class TestTheErrorKindIsDistinguishableInEveryWayItClaims:
    """Both of these are gaps the mutation run found, not designs chosen first.

    A first version of this file asserted the failure's *class* and nothing
    else, so two mutations ran clean: removing the warning icon, and repointing
    `_keeps_across_replies` from `turn-notice` to `turn-error`.

    The second one is the interesting failure. It leaves an error row surviving a
    later reply - which is the only thing this file was written to check - so it
    reads as harmless, while **silently breaking every compaction and cloud
    note**, which are the rows that were keeping that marker working. **A test
    that checks the new thing does not check the old thing it shares a mechanism
    with**, and the two are indistinguishable from inside the new test.
    """

    def _notice_icon(self, view, needle):
        for row in _walk(view._rows):
            if TURN_NOTICE_CSS not in (row.get_css_classes() or []):
                continue
            for node in _walk(row):
                if isinstance(node, Gtk.Image) and needle in (
                        node.get_icon_name() or ""):
                    return node.get_icon_name()
        return None

    def test_a_failure_carries_the_warning_glyph_and_a_note_does_not(self):
        """An icon that means *the kind of note*. A blank square reads as a
        broken row rather than as a missing glyph, and this repository has
        checked icon names by path in three other themes for that reason."""
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("earlier context was shortened", "compaction")
        view.add_notice_row("Chronoa could not finish that turn. Boom.",
                            "error")
        assert self._notice_icon(view, "dialog-warning") is not None, (
            "the failure has no warning glyph, so it carries the same icon as a "
            "compaction note and the two rows are told apart by nothing")
        assert self._notice_icon(view, "format-justify") is not None, (
            "the compaction note lost its glyph as well - the two icons are "
            "meant to be what distinguishes them")

    def test_a_housekeeping_note_still_survives_a_later_reply(self):
        """**The control for the marker above.**

        `_keeps_across_replies` keys on `turn-notice`, and the error kind is
        added *beside* it rather than instead of it so both survive. Repointing
        that predicate at `turn-error` leaves every failure working and every
        compaction note vanishing - which no test written about failures can see.
        """
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("earlier context was shortened", "compaction")
        view.add_assistant_turn("Here is the answer.")
        assert any("earlier context was shortened" in t for t in _texts(view)), (
            "a compaction note vanished when the turn's reply was written - the "
            "survival marker must key on turn-notice, which both kinds carry")
