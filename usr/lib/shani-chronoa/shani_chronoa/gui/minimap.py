"""A one-tick-per-turn map of a conversation, for the conversations too long to
scroll.

`visible_turns()` and `scroll_to_turn()` already exist on the transcript, so the
data was there and nothing was drawing it. What was missing was any answer to
*"how far down am I?"* - which is the question you ask when you have scrolled
past the point you meant to stop at and cannot tell a long turn from a short
one.

**A tick per turn, coloured by role, with the viewport drawn on top.** Clicking
a tick jumps there; it is the same jump search already makes, not a second
mechanism that could disagree with it.

The viewport arithmetic is a module-level function on purpose. It is the part
that would be wrong - off-by-one at the end of a conversation, a half-visible
turn reported as fully visible, an empty conversation dividing by zero - and a
function that takes five numbers can be checked against numbers whose answer you
worked out by hand.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

logger = logging.getLogger(__name__)

#: Ticks are 3px wide: this is a ruler, not a second transcript.
TICK_WIDTH = 3
#: Visible gap between ticks, so a hundred turns still read as a bar.
TICK_GAP = 1


def visible_turn_range(value: float, upper: float, page_size: float,
                       spans: "list[tuple[float, float]]") -> "tuple[int, int]":
    """Which turns intersect the viewport, as inclusive `(first, last)` indexes.

    `spans` is `(offset, height)` per turn in content coordinates, so `spans[i]`
    covers `[offset, offset + height]`.

    `(-1, -1)` means nothing is visible - an empty conversation, or an
    adjustment that has not been realised yet, which is the state the widget is
    in for the frame between being built and being shown.

    **Clamped, deliberately.** GTK hands back a `value` that can sit a pixel or
    two outside `[0, upper - page]` during a fling, and an unclamped comparison
    reports the last turn as not visible at the very bottom of the
    conversation - which is precisely where someone is looking when they notice.
    """
    if not spans:
        return (-1, -1)
    top = max(0.0, min(float(value), max(0.0, float(upper) - float(page_size))))
    bottom = top + max(1.0, float(page_size))

    first = -1
    last = -1
    for index, (offset, height) in enumerate(spans):
        # A zero-height row (a turn added but not yet allocated) still counts as
        # visible when the viewport is exactly on it, or it flickers out of the
        # map for a frame each time one is added.
        end = offset + max(float(height), 1.0)
        if end > top and offset < bottom:
            if first < 0:
                first = index
            last = index
    return (first, last)


def spans_for(rows) -> "list[tuple[float, float]]":
    """Cumulative `(offset, height)` for each row, from its own measured size.

    Measured rather than assumed because turns vary from one line to a screen:
    an even split is what a ruler is not.
    """
    spans: "list[tuple[float, float]]" = []
    offset = 0.0
    for row in rows:
        try:
            height = float(row.get_height())
        except Exception:  # noqa: BLE001
            height = 0.0
        spans.append((offset, height))
        offset += height
    return spans


class TurnMinimap(Gtk.Box):
    """A vertical ruler of turns, with the viewport drawn on it."""

    def __init__(self, on_jump=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=TICK_GAP)
        self.add_css_class("turn-minimap")
        self.set_size_request(TICK_WIDTH + 2, -1)
        #: Called with a turn index. A callback rather than a held reference to
        #: the transcript, for the reason the rail does the same: this widget is
        #: then testable without a scrolled window and a real conversation.
        self.on_jump = on_jump or (lambda _index: None)

        self._ticks: "list[Gtk.Widget]" = []
        self._cursor = -1

        # **Keyboard, and deliberately not one tab stop per turn.** Two hundred
        # turns is two hundred tab stops between the conversation and the
        # composer, which makes the minimap the hardest thing in the window to
        # get past. One focusable box that answers Up/Down instead.
        self.set_focusable(True)
        self.set_can_focus(True)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Turn minimap. Up and Down move between turns."])

    # -- contents ------------------------------------------------------------

    def set_turns(self, roles) -> None:
        """One tick per turn, rebuilt from scratch.

        Rebuilt rather than diffed: a turn's height changes as it streams, so a
        tick that kept its identity would keep a stale size. The turn count is
        the number of turns, and there is no case where updating a tick in place
        is worth the state to track.
        """
        while self.get_first_child() is not None:
            # **`remove`, not `_remove`.** GTK4 dropped the private helper the
            # GTK3 idiom used. Called here on every rebuild, so it ran on the
            # second turn and raised - and PyGObject swallows exceptions raised
            # inside a signal handler, so the window's `turn-added` handler
            # died quietly and the map stayed one tick wide forever.
            self.remove(self.get_first_child())
        self._ticks = []

        roles = list(roles or ())
        if not roles:
            # **A visible empty state, not an absent widget.** An invisible
            # minimap and a minimap that failed to build look identical, and the
            # second is the one that loses an afternoon.
            empty = Gtk.Label(label="")
            empty.set_size_request(TICK_WIDTH + 2, -1)
            empty.set_tooltip_text("No turns yet")
            empty.add_css_class("dim-label")
            self.append(empty)
            return

        for index, role in enumerate(roles):
            tick = Gtk.Button()
            tick.add_css_class("minimap-tick")
            tick.add_css_class("minimap-" + str(role))
            # Not focusable: see the class docstring on why.
            tick.set_focusable(False)
            tick.set_size_request(TICK_WIDTH, -1)
            tick.set_tooltip_text(f"Turn {index + 1}: {role}")
            tick.connect("clicked", lambda _b, i=index: self.on_jump(i))
            self.append(tick)
            self._ticks.append(tick)

    # -- viewport ------------------------------------------------------------

    def set_viewport(self, first: int, last: int) -> None:
        """Highlight the ticks the viewport covers.

        `(-1, -1)` clears every highlight, which is the honest rendering of
        "I do not know where you are" - and better than leaving the last known
        highlight in place, which would claim to know.
        """
        for index, tick in enumerate(self._ticks):
            inside = first <= index <= last
            if inside and not tick.has_css_class("minimap-visible"):
                tick.add_css_class("minimap-visible")
            elif not inside and tick.has_css_class("minimap-visible"):
                tick.remove_css_class("minimap-visible")

    def update_from(self, value: float, upper: float, page_size: float,
                    rows) -> None:
        """Measure the rows, work out the range, draw it."""
        if not self._ticks:
            return
        self.set_viewport(*visible_turn_range(value, upper, page_size,
                                              spans_for(rows)))

    # -- keyboard ------------------------------------------------------------

    def _on_key(self, _controller, keyval, _code, _state) -> bool:
        count = len(self._ticks)
        if not count:
            return False
        name = keyval_name(keyval)
        # **Start at whichever end is nearer**, not always at zero: a minimap
        # whose Down key jumps to turn 1 when you are at turn 200 feels broken
        # even though it is arithmetically correct.
        cursor = self._cursor if self._cursor >= 0 else -1
        if name == "Down" or name == "Page_Down":
            target = cursor + 5 if name == "Page_Down" else cursor + 1
        elif name == "Up" or name == "Page_Up":
            target = cursor - 5 if name == "Page_Up" else cursor - 1
        elif name == "Home":
            target = 0
        elif name == "End":
            target = count - 1
        else:
            return False
        # -1 from an unset cursor would clamp to 0, so an unset Up jumps to the
        # *last* turn's neighbour rather than the last turn; handled explicitly.
        if cursor < 0 and name in ("Up", "Page_Up"):
            target = count - 1
        self._cursor = max(0, min(count - 1, target))
        self.on_jump(self._cursor)
        return True


def keyval_name(keyval: int) -> str:
    """`Gdk.KEY_Down` -> `"Down"`, and unknown keys -> `""`.

    Named rather than compared against raw keyvals at every call site so a test
    can name the key it means, and so a keyval that does not resolve is an
    unhandled key rather than an `AttributeError` on the comparison.
    """
    from gi.repository import Gdk

    return Gdk.keyval_name(keyval) or ""