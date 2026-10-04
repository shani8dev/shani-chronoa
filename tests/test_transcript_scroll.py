"""Following the newest turn - and not stealing the view from a reader.

The transcript used to scroll to the bottom on every single update. A reply
arrives over seconds, as a stream of chunks, so that meant a person who scrolled
up to re-read an earlier answer was dragged back to the bottom by every chunk
that followed - the one moment the transcript most needs to hold still.

These drive a real `TranscriptView` in a real window with real layout, because
the property under test is about scroll offsets, and an unlaid-out widget has an
adjustment of zero and would agree with any implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402

LONG = ("Here is a long answer. " * 400) + "\n\nEnd of it."
SHORT = "A short one."


def _shown(view) -> Gtk.Widget:
    """Show the transcript at a size where scrolling is possible.

    The size request on the *view* is what makes the fixture real: left to the
    window, a `ScrolledWindow` takes all the height it is given and its
    adjustment has an upper bound equal to its page size - never scrollable, so
    every assertion about following the newest turn would pass against an
    implementation that never moves.
    """
    view.set_size_request(400, 220)
    window = Gtk.Window()
    window.set_default_size(420, 260)
    window.set_child(view)
    window.present()
    return window


def _flush(seconds: float = 0.35) -> None:
    """Let layout happen, then the deferred follow, then measure.

    Time-based, not "until nothing is pending". GTK4 allocates on a frame-clock
    tick and there is no pending source to wait for at that moment, so a
    pending-only loop returns before the widget has been given a size - and a
    widget with no size has an adjustment whose upper bound equals its page size,
    which is not scrollable at all and would agree with any implementation.
    """
    import time

    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def _distance_from_bottom(view) -> float:
    adjustment = view.get_vadjustment()
    return adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value()


def _jump_button(view):
    stack, found = [view], None
    while stack:
        node = stack.pop()
        if getattr(node, "get_tooltip_text", None) and \
                node.get_tooltip_text() == "Jump to the newest message":
            found = node
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


@pytest.fixture
def laid_out():
    view = TranscriptView()
    window = _shown(view)
    _flush()
    yield view, window
    window.destroy()


class TestFollowingTheNewestTurn:
    def test_a_new_turn_is_followed_even_from_the_top(self, laid_out):
        """The user asked it; they want to watch it being answered."""
        view, _window = laid_out
        view.add_user_turn("first question")
        view.add_assistant_turn(LONG)
        _flush()
        view.get_vadjustment().set_value(0)          # scrolled away, reading history
        view.add_user_turn("second question")
        _flush()
        assert _distance_from_bottom(view) < 2

    def test_streaming_does_not_steal_the_view(self, laid_out):
        """The defect: every streamed chunk scrolled to the bottom."""
        view, _window = laid_out
        view.add_user_turn("a question")
        view.add_assistant_turn(LONG)
        _flush()
        view.get_vadjustment().set_value(0)
        # Settle first: a scrollbar move and a pending layout in the same tick is
        # the race the generation counter exists for, and this test is about the
        # steady state, not about winning that race deliberately.
        _flush()
        away = _distance_from_bottom(view)
        assert away > TranscriptView.STICKY_PX, "the fixture is not actually scrolled away"

        for chunk in range(6):
            view.add_assistant_turn(LONG + f" {'more'} " * chunk)
            _flush()
        assert _distance_from_bottom(view) == pytest.approx(away, abs=2), (
            "the transcript followed the stream and moved the reader's view")

    def test_a_reader_who_is_already_at_the_bottom_is_followed(self, laid_out):
        """The sticky range is the point: staying at the bottom must still follow
        new text, or the newest reply would arrive off-screen for everyone."""
        view, _window = laid_out
        view.add_assistant_turn(LONG)
        _flush()
        view.get_vadjustment().set_value(
            view.get_vadjustment().get_upper() - view.get_vadjustment().get_page_size())
        view.add_assistant_turn(LONG + "\n\nAnd one more line.")
        _flush()
        assert _distance_from_bottom(view) < 2


class TestTheWayBack:
    def test_the_button_appears_only_when_the_view_has_left_the_newest_turn(self, laid_out):
        view, _window = laid_out
        button = _jump_button(view)
        assert button is not None, "there is no way back to the newest turn"
        view.add_assistant_turn(LONG)
        _flush()
        assert button.get_visible() is False

        view.get_vadjustment().set_value(0)
        view.add_assistant_turn(LONG + "\n\nAnother paragraph, further down.")
        _flush()
        assert button.get_visible() is True

    def test_pressing_it_returns_to_the_newest_turn(self, laid_out):
        view, _window = laid_out
        view.add_assistant_turn(LONG)
        _flush()
        view.get_vadjustment().set_value(0)
        view.add_assistant_turn(LONG + "\n\nOne more.")
        _flush()
        button = _jump_button(view)
        assert button.get_visible()
        button.emit("clicked")
        _flush()
        assert _distance_from_bottom(view) < 2
        assert button.get_visible() is False, "the button stayed up after it was used"

    def test_it_is_not_a_toggle_a_person_can_leave_stuck_on(self, laid_out):
        """`Gtk.ToggleButton` because of the look, but a toggle that stays active
        would promise a second press does something when it does not."""
        view, _window = laid_out
        button = _jump_button(view)
        assert isinstance(button, Gtk.ToggleButton)
        view.add_assistant_turn(LONG)
        _flush()
        view.get_vadjustment().set_value(0)
        view.add_assistant_turn(LONG + "\n\nmore")
        _flush()
        button.emit("clicked")
        _flush()
        assert button.get_active() is False


class TestTheThreshold:
    def test_it_is_a_distance_and_not_a_flag(self):
        """A named constant, because 150 is a measured compromise between
        "the view jumped while I was reading" and "the new reply arrived
        off-screen" and both are bugs."""
        assert isinstance(TranscriptView.STICKY_PX, (int, float))
        assert 0 < TranscriptView.STICKY_PX <= 400

    def test_forcing_ignores_the_threshold(self, laid_out):
        view, _window = laid_out
        view.add_assistant_turn(LONG)
        _flush()
        view.get_vadjustment().set_value(0)
        assert _distance_from_bottom(view) > TranscriptView.STICKY_PX
        view._scroll_to_end(force=True)
        _flush()
        assert _distance_from_bottom(view) < 2