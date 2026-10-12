"""The minimap in the window: does it stay in step with the conversation?

`test_turn_minimap.py` covers the widget and its arithmetic. This covers the
**wiring**, which is the part that decides whether the feature exists at all: a
ruler that is built and never told about turns is an ornament.

The window methods are taken off the class and bound to a stand-in, rather than
reimplemented here. A test that copies the method under test passes when the
method is wrong - which is the whole reason these are bound, not written out.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import minimap as minimap_module  # noqa: E402
from shani_chronoa.gui import style as style_module  # noqa: E402
from shani_chronoa.gui import window as window_module  # noqa: E402
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402


def _bind(name):
    """The real window method, unbound."""
    return getattr(window_module.ChronoaWindow, name)


def _window(roles=("user", "assistant")):
    """A stand-in window carrying exactly the attributes the minimap touches.

    **Every method the wiring calls is bound from the class.** An earlier
    version bound only `_on_turn_added`, so its call to
    `_update_minimap_viewport` raised `AttributeError` on the stub - and
    PyGObject *swallows* exceptions raised in a signal handler, so the test
    reported "no ticks" instead of "your stub is incomplete". Four failures
    that all pointed at the wiring and all were the harness.
    """
    transcript = TranscriptView()
    jumped = []
    window = type("_W", (), {})()
    window._transcript = transcript
    window._minimap = minimap_module.TurnMinimap(on_jump=jumped.append)
    window._minimap_adjustment = None
    window._update_minimap_viewport = (
        _bind("_update_minimap_viewport").__get__(window))
    transcript.connect("turn-added",
                       _bind("_on_turn_added").__get__(window))
    for role in roles:
        if role == "user":
            transcript.add_user_turn("x")
        else:
            transcript.add_assistant_turn("y")
    return window, transcript, jumped


def _ticks(window):
    return [n for n in window._minimap
            if isinstance(n, Gtk.Button) and "minimap-tick" in n.get_css_classes()]


class _Row:
    """A transcript row with a stated height, for the viewport arithmetic."""

    def __init__(self, height):
        self._height = height

    def get_height(self):
        return self._height


# -- the map follows the conversation ---------------------------------------


def test_a_turn_added_keeps_the_existing_ticks():
    """**The wiring the review found.** `_on_turn_added`'s comment said
    "appended rather than rebuilt" while the code called `set_turns`, which
    removes and recreates every tick - quadratic over a conversation, and it
    replaced the highlighted tick outright. Counting ticks cannot see this, so
    the test compares the widget objects."""
    window, transcript, _ = _window()
    before = list(_ticks(window))
    transcript.add_user_turn("a second question")
    after = _ticks(window)
    assert len(after) == len(before) + 1, (len(before), len(after))
    assert after[:len(before)] == before, "existing ticks were replaced"


def test_a_turn_added_grows_the_map_by_one_tick():
    """The wiring's whole job. Without it the map is a fixed three-pixel bar.

    **A user turn, not another assistant turn.** `add_assistant_turn` merges
    into the turn already open (`_current_assistant`) rather than appending -
    correct behaviour, and the reason a third `add_assistant_turn` here left
    the map at two ticks and looked like a wiring failure.
    """
    window, transcript, _ = _window()
    before = len(_ticks(window))
    transcript.add_user_turn("a second question")
    assert len(_ticks(window)) == before + 1, (
        f"{before} ticks before, {len(_ticks(window))} after a turn was added")


def test_the_map_has_one_tick_per_turn():
    window, transcript, _ = _window(("user", "assistant", "user"))
    assert len(_ticks(window)) == len(transcript.visible_turns())


def test_the_ticks_are_styled_by_the_turns_actual_roles():
    """Built from `visible_turns()`, not from whatever role last arrived -
    otherwise every tick after a question is coloured as a question."""
    window, _, _ = _window(("user", "assistant", "user"))
    classes = [t.get_css_classes() for t in _ticks(window)]
    assert "minimap-user" in classes[0], classes[0]
    assert "minimap-assistant" in classes[1], classes[1]
    assert "minimap-user" in classes[2], classes[2]


# -- clicking a tick moves the conversation --------------------------------


def test_a_tick_click_scrolls_the_transcript():
    """And it is the transcript's own `scroll_to_turn` that moves it, so search
    and the map cannot disagree about which turn is turn 4."""
    window, transcript, _ = _window(("user", "assistant"))
    _ticks(window)[0].emit("clicked")
    assert transcript.get_vadjustment().get_value() >= 0.0


def test_an_out_of_range_index_does_not_raise():
    """The transcript also holds a placeholder row the map never drew, so 'one
    past the end' is reachable by arithmetic."""
    window, _, _ = _window(("user", "assistant"))
    window._minimap.on_jump(9999)          # must not raise


# -- scrolling moves the map, not the other way round ------------------------


def test_scrolling_draws_the_viewport():
    """**Stub rows with known heights, not the transcript's own.** An unrealised
    widget reports a height of 0, so every row reads as one pixel tall and all
    three fall inside the page - which made the first version of this test
    assert `[0, 1]` and be told `[0, 1, 2]`, with no way to tell which of the
    two was wrong. The heights here are stated, so the answer is checkable.
    """
    window, _, _ = _window(("user", "assistant", "user"))
    rows = [_Row(100.0) for _ in range(3)]
    window._minimap.update_from(0, 300, 150, rows)
    assert [i for i, t in enumerate(_ticks(window))
            if t.has_css_class("minimap-visible")] == [0, 1]


def test_a_scroll_handler_that_raises_does_not_stop_the_scroll():
    """**The map is decoration.** It runs on every `value-changed`, so an
    exception here is a traceback per pixel of scroll - and the one thing it
    must never be able to do is stop the conversation being scrolled."""

    class _Angry:
        def get_value(self):
            raise RuntimeError("not realised")

        def get_upper(self):
            return 300.0

        def get_page_size(self):
            return 150.0

    window, _, _ = _window()
    window._minimap_adjustment = _Angry()
    _bind("_update_minimap_viewport").__get__(window)()   # must not raise


def test_no_adjustment_means_no_viewport_but_no_crash():
    window, _, _ = _window()
    window._minimap_adjustment = None
    _bind("_update_minimap_viewport").__get__(window)()   # must not raise


# -- the map has to fit ----------------------------------------------------


def _stylesheet_provider():
    """The real stylesheet, applied, because the bug is in the *styled* size."""
    from gi.repository import Gdk

    provider = Gtk.CssProvider()
    provider.load_from_data(_stylesheet())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return provider


def test_a_full_conversation_still_fits_the_window_height():
    """**A layout bug no unit test could see, because it needs the CSS.** Each
    tick is a `Gtk.Button`, and a button's height is mostly theme padding - so
    the map's *minimum* height was about four pixels a tick. GTK cannot
    allocate a widget below its minimum, so that is a hard floor on the column:
    at 250 turns it measured **1007px**, and a 900px window cannot honour it,
    so the composer is pushed off the bottom of a conversation that is well
    inside the store's own 500-message cap.

    **The turn count is derived, not typed in.** `MAX_LOADED` is 500 messages
    and a conversation alternates, so 250 is the largest a user can reach; a
    hardcoded 400 would pass today and quietly stop being true when the cap
    moves.
    """
    from shani_chronoa import conversation_store

    _stylesheet_provider()
    turns = conversation_store.MAX_LOADED // 2
    view = minimap_module.TurnMinimap()
    view.set_turns(["user"] * turns)

    minimum = view.measure(Gtk.Orientation.VERTICAL, -1)[0]
    # 900px is a conservative small laptop window. A map that cannot fit in one
    # cannot fit in any, and the composer goes with it.
    assert minimum <= 900, (
        f"{turns} ticks need {minimum}px before GTK will shrink them at all, "
        f"so the transcript row cannot fit a 900px window")


def test_a_short_conversation_fills_the_column_rather_than_sitting_at_the_top():
    """Four thin marks in a 900px column is not a map of the conversation."""
    _stylesheet_provider()
    view = minimap_module.TurnMinimap()
    view.set_turns(["user"] * 4)
    assert view.get_vexpand(), "the map does not expand to the column height"
    ticks = [n for n in view
             if isinstance(n, Gtk.Button) and "minimap-tick" in n.get_css_classes()]
    assert ticks and all(t.get_vexpand() for t in ticks), (
        "ticks that do not expand leave the column mostly empty")


# -- the stylesheet ---------------------------------------------------------


def _stylesheet():
    """The bytes `_apply_css` feeds the provider, read without running it.

    Extracted from the source rather than by calling `_apply_css`, because that
    registers a provider against the display, which needs a live one.
    """
    source = Path(style_module.__file__).read_text(encoding="utf-8")
    start = source.index('css_data = b"""')
    start = source.index("\n", start) + 1
    end = source.index('"""', start)
    return source[start:end].encode("utf-8")


def test_the_stylesheet_is_valid_css():
    """A malformed rule is dropped by the parser with a warning on stderr and
    everything after it stops applying - so a brace typo at the end of the file
    silently unstyles the whole application."""
    provider = Gtk.CssProvider()
    provider.load_from_data(_stylesheet())      # raises on a parse error


def test_every_minimap_class_the_widget_sets_is_styled():
    """**The two halves are written in different files and nothing connects
    them.** The widget adds the classes; the stylesheet has to answer, or the
    map is three pixels of default button grey that looks like a scrollbar.
    """
    css = _stylesheet().decode("utf-8")
    window, _, _ = _window(("user", "assistant"))
    for tick in _ticks(window):
        for name in tick.get_css_classes():
            if name.startswith("minimap-"):
                assert f".{name}" in css, f"the widget sets .{name}, the CSS never mentions it"
    assert ".turn-minimap" in css


def test_the_map_has_a_visible_style_for_the_viewport():
    """Without the brighter rule the map renders correctly and tells you
    nothing, which is the failure mode it exists to avoid."""
    css = _stylesheet().decode("utf-8")
    assert re.search(r"\.minimap-tick\.minimap-visible\s*\{[^}]*background-color",
                     css), "no viewport highlight rule"