"""A ruler for conversations too long to scroll.

The interesting part is `visible_turn_range`: it decides which ticks are lit,
and it is the part that is wrong at the edges - the last turn not lit at the
very bottom, a half-visible turn counted as visible, an empty conversation
dividing by zero. Each of those is a case with a hand-worked answer below, not
a case checked against whatever the function happened to return.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from shani_chronoa.gui import minimap as minimap_module  # noqa: E402


# -- a helper so the arithmetic can be read without a widget ----------------


def _spans(*heights):
    """Cumulative spans for turns of the given heights."""
    out, offset = [], 0.0
    for height in heights:
        out.append((offset, float(height)))
        offset += height
    return out


# -- visible_turn_range -----------------------------------------------------


def test_an_empty_conversation_reports_nothing_visible():
    assert minimap_module.visible_turn_range(0, 0, 100, []) == (-1, -1)


def test_a_conversation_at_the_top_shows_the_first_turns():
    # Three turns of 100px, a 150px viewport at the very top.
    got = minimap_module.visible_turn_range(0, 300, 150, _spans(100, 100, 100))
    assert got == (0, 1), got


def test_a_turn_only_half_on_screen_is_visible():
    """A turn you are halfway through reading is a turn you are reading."""
    # viewport 50..250 over three 100px turns: turn 0 ends at 100, turn 2
    # starts at 200, so both are on screen even though neither is whole.
    got = minimap_module.visible_turn_range(50, 300, 200, _spans(100, 100, 100))
    assert got == (0, 2), got


def test_the_last_turn_is_visible_at_the_very_bottom():
    """**The case that made this function.** GTK hands back a `value` that can
    sit a pixel or two past `upper - page` during a fling. Unclamped, that
    reports the last turn as not visible - at exactly the point where someone
    scrolling to the end notices that the ruler does not reach the end."""
    spans = _spans(100, 100, 100)
    got = minimap_module.visible_turn_range(152, 300, 150, spans)
    assert got == (1, 2), got


def test_a_value_past_the_end_still_reports_the_last_turn():
    got = minimap_module.visible_turn_range(9999, 300, 150, _spans(100, 100, 100))
    assert got[1] == 2, got


def test_a_negative_value_still_reports_the_first_turn():
    got = minimap_module.visible_turn_range(-5, 300, 150, _spans(100, 100, 100))
    assert got[0] == 0, got


def test_one_turn_taller_than_the_viewport_is_visible_at_both_ends():
    spans = _spans(1000)
    assert minimap_module.visible_turn_range(0, 1000, 200, spans) == (0, 0)
    assert minimap_module.visible_turn_range(900, 1000, 200, spans) == (0, 0)


def test_a_zero_height_turn_at_the_top_is_still_visible():
    """A turn added this frame has no allocation yet. Treating it as invisible
    makes it blink out of the map every time one is added."""
    got = minimap_module.visible_turn_range(0, 100, 100, _spans(0, 100))
    assert got[0] == 0, got


def test_a_zero_height_turn_far_away_is_not_visible():
    got = minimap_module.visible_turn_range(0, 1000, 100, _spans(100, 0, 100,
                                                                  100, 100, 100,
                                                                  100, 100, 100))
    assert got == (0, 0), got


def test_a_viewport_larger_than_the_content_shows_everything():
    got = minimap_module.visible_turn_range(0, 100, 500, _spans(50, 50))
    assert got == (0, 1), got


# -- spans_for --------------------------------------------------------------


def test_spans_are_measured_not_assumed():
    """Turns vary from one line to a screen. An even split is what a ruler is
    not."""
    rows = [SimpleNamespace(get_height=lambda h=h: h) for h in (10, 40, 50)]
    assert minimap_module.spans_for(rows) == [(0.0, 10.0), (10.0, 40.0),
                                              (50.0, 50.0)]


def test_a_row_that_cannot_be_measured_does_not_raise():
    class _Broken:
        def get_height(self):
            raise RuntimeError("not realised")

    assert minimap_module.spans_for([_Broken()]) == [(0.0, 0.0)]


# -- the widget -------------------------------------------------------------


def _minimap(roles=("user", "assistant", "user", "assistant")):
    jumped = []
    widget = minimap_module.TurnMinimap(on_jump=jumped.append)
    widget.set_turns(roles)
    return widget, jumped


def _ticks(widget):
    return [n for n in widget
            if isinstance(n, Gtk.Button) and "minimap-tick" in n.get_css_classes()]


def test_one_tick_per_turn():
    widget, _ = _minimap()
    assert len(_ticks(widget)) == 4


def test_rebuilding_replaces_the_ticks_rather_than_appending():
    """**The path that had the bug.** `set_turns` clears before it fills, so a
    second call is the first time that clearing code runs at all - and it used
    a GTK3 method name that does not exist in GTK4. Every test above builds the
    map exactly once, so only this one and the window's `turn-added` handler
    ever executed it."""
    widget, _ = _minimap()
    widget.set_turns(("user", "assistant"))
    assert len(_ticks(widget)) == 2, len(_ticks(widget))


def test_rebuilding_from_several_turns_down_to_none_leaves_nothing():
    widget, _ = _minimap()
    widget.set_turns(())
    assert not _ticks(widget)


def test_ticks_are_styled_by_role():
    """The map is only a map if you can tell a question from an answer without
    reading it."""
    widget, _ = _minimap(("user", "assistant"))
    ticks = _ticks(widget)
    assert "minimap-user" in ticks[0].get_css_classes(), ticks[0].get_css_classes()
    assert "minimap-assistant" in ticks[1].get_css_classes()


def test_an_empty_conversation_says_so_rather_than_showing_nothing():
    """An invisible minimap and one that failed to build look identical, and the
    second is the one that loses an afternoon."""
    widget, _ = _minimap(())
    assert not _ticks(widget)
    tooltips = [n.get_tooltip_text() for n in widget if n.get_tooltip_text()]
    assert tooltips == ["No turns yet"], tooltips


def test_clicking_a_tick_jumps_to_that_turn():
    widget, jumped = _minimap()
    _ticks(widget)[2].emit("clicked")
    assert jumped == [2], jumped


def test_the_viewport_highlights_the_covered_ticks():
    widget, _ = _minimap()
    widget.set_viewport(1, 2)
    lit = [i for i, t in enumerate(_ticks(widget))
           if t.has_css_class("minimap-visible")]
    assert lit == [1, 2], lit


def test_an_unknown_viewport_clears_every_highlight():
    """Leaving the last highlight in place claims to know where you are."""
    widget, _ = _minimap()
    widget.set_viewport(1, 2)
    widget.set_viewport(-1, -1)
    assert not [t for t in _ticks(widget)
                if t.has_css_class("minimap-visible")]


def test_setting_the_viewport_twice_does_not_stack_classes():
    widget, _ = _minimap()
    widget.set_viewport(1, 2)
    widget.set_viewport(1, 2)
    tick = _ticks(widget)[1]
    assert tick.get_css_classes().count("minimap-visible") == 1, (
        tick.get_css_classes())


def test_update_from_measures_the_rows_and_draws_the_result():
    widget, _ = _minimap(("user", "assistant", "user"))
    rows = [SimpleNamespace(get_height=lambda h=h: h) for h in (100, 100, 100)]
    widget.update_from(0, 300, 150, rows)
    assert [i for i, t in enumerate(_ticks(widget))
            if t.has_css_class("minimap-visible")] == [0, 1]


def test_update_from_does_nothing_when_there_are_no_ticks():
    widget, _ = _minimap(())
    widget.update_from(0, 300, 150, [])          # must not raise


# -- appending, which is what a running conversation actually does ---------


def test_appending_leaves_the_existing_ticks_alone():
    """**The thing no test checked.** Every earlier test counts ticks, so a map
    that removed and recreated all of them on each turn agreed with one that
    only added one - and the rebuilding version is O(turns) per turn, which is
    quadratic over a conversation.

    Tick *identity* is what separates them, so that is what this asserts.
    """
    widget, _ = _minimap()
    first = _ticks(widget)[0]
    widget.append_turn("user")
    assert len(_ticks(widget)) == 5, len(_ticks(widget))
    assert _ticks(widget)[0] is first, "the first tick was replaced"


def test_appending_does_not_disturb_the_viewport_highlight():
    """Rebuilding every tick replaced the highlighted widget outright, so the
    viewport highlight silently moved onto a fresh tick that did not have the
    class until the next `set_viewport`."""
    widget, _ = _minimap()
    widget.set_viewport(0, 1)
    widget.append_turn("user")
    lit = [i for i, t in enumerate(_ticks(widget))
           if t.has_css_class("minimap-visible")]
    assert lit == [0, 1], lit


def test_the_first_append_to_an_empty_map_removes_the_placeholder():
    """A map plus a leftover "No turns yet" label is two states at once."""
    widget = minimap_module.TurnMinimap()
    widget.append_turn("user")
    assert len(_ticks(widget)) == 1, len(_ticks(widget))
    # **The leftover placeholder**, identified by the tooltip it carries rather
    # than by its type, so the check fails if a different empty state appears.
    leftover = [n.get_tooltip_text() for n in widget
                if n.get_tooltip_text() == "No turns yet"]
    assert not leftover, "the 'No turns yet' label survived the first turn"


def test_appending_twice_to_an_empty_map_leaves_two_ticks():
    """**The bug this caught.** `_built` was set next to the tick loop, which
    the empty case returns before - so it stayed False, every `append_turn`
    re-ran `set_turns(())`, and the tick the previous call had just added was
    cleared. Measured: two appends left one tick."""
    widget = minimap_module.TurnMinimap()
    widget.set_turns(())
    widget.append_turn("user")
    widget.append_turn("assistant")
    assert len(_ticks(widget)) == 2, len(_ticks(widget))


def test_a_map_never_built_can_be_appended_to():
    widget = minimap_module.TurnMinimap()
    widget.append_turn("user")
    widget.append_turn("assistant")
    assert len(_ticks(widget)) == 2, len(_ticks(widget))


def test_set_turns_still_replaces_wholesale():
    """`append_turn` is the incremental path; a reload is a different job and
    must still be able to replace the whole map."""
    widget, _ = _minimap()
    widget.set_turns(("user",))
    assert len(_ticks(widget)) == 1, len(_ticks(widget))


def test_a_freshly_appended_tick_still_jumps_to_its_own_index():
    """The index is baked in when the tick is built, so a tick made by
    `append_turn` must carry the *current* count, not a stale or default one."""
    jumped = []
    widget = minimap_module.TurnMinimap(on_jump=jumped.append)
    widget.set_turns(("user",))
    widget.append_turn("assistant")
    _ticks(widget)[1].emit("clicked")
    assert jumped == [1], jumped


# -- keyboard ---------------------------------------------------------------


def _press(widget, name):
    widget._on_key(None, Gdk.keyval_from_name(name), 0, 0)


def test_down_then_down_moves_two_turns_on():
    widget, jumped = _minimap()
    _press(widget, "Down")
    _press(widget, "Down")
    assert jumped == [0, 1], jumped


def test_home_jumps_to_the_first_turn():
    widget, jumped = _minimap()
    _press(widget, "End")
    _press(widget, "Home")
    # End first, so the two jumps are distinguishable - an assertion of `[0]`
    # alone would pass even if `End` did nothing.
    assert jumped == [3, 0], jumped


def test_end_jumps_to_the_last_turn():
    widget, jumped = _minimap()
    _press(widget, "End")
    assert jumped == [3], jumped


def test_moving_past_the_end_stops_at_the_last_turn():
    widget, jumped = _minimap()
    for _ in range(10):
        _press(widget, "Down")
    assert jumped[-1] == 3, jumped


def test_an_unhandled_key_is_not_consumed():
    """Swallowing a key the focus is not meant to have is how typing stops
    working in a dialog that is not even open."""
    widget, jumped = _minimap()
    handled = widget._on_key(None, Gdk.keyval_from_name("a"), 0, 0)
    assert handled is False
    assert jumped == []


def test_keys_do_nothing_without_turns():
    widget, jumped = _minimap(())
    assert widget._on_key(None, Gdk.keyval_from_name("Down"), 0, 0) is False
    assert jumped == []


def test_ticks_are_not_individual_tab_stops():
    """Two hundred turns is two hundred tab stops between the conversation and
    the composer, which makes the minimap the hardest thing in the window to
    get past."""
    widget, _ = _minimap(tuple("user" for _ in range(200)))
    assert all(not t.get_focusable() for t in _ticks(widget))


def test_the_minimap_itself_is_one_focus_stop_with_a_label():
    """**The label is asserted by recording `update_property`, because this GTK
    has no getter for it** - `get_accessible_property` does not exist on
    `Gtk.Box` here (`dir()` shows only `get_accessible_role` and friends), so a
    test that reads the property back would fail on the API, not on the label.
    Reading the stored value is not possible, so the call is what gets checked.
    """
    recorded = {}
    original = minimap_module.TurnMinimap.update_property

    def recorder(self, properties, values):
        if Gtk.AccessibleProperty.LABEL in list(properties):
            recorded["label"] = list(values)
        return original(self, properties, values)

    minimap_module.TurnMinimap.update_property = recorder
    try:
        widget, _ = _minimap()
    finally:
        minimap_module.TurnMinimap.update_property = original

    assert widget.get_focusable()
    assert recorded.get("label"), "the minimap announced nothing to a screen reader"
    assert "Down" in recorded["label"][0], recorded["label"]