"""A command palette: one place to go, and one keystroke away.

244 skills, seven rail sections, every panel, every slash command, and the mode
switches on the strip. The sidebar search already filters panels, but it filters
*only* panels and it is somewhere you have to be.

**Every entry carries its own callback**, so the palette never has to know what a
destination is called or how to reach it - the same rule the rail's `on_open`
follows, and the reason both are testable without a window.

**Nothing is shown that cannot be chosen.** An entry with no callback is dropped
rather than drawn: a palette row that reports "nothing happened" when chosen is
the same defect as a button wired to nothing, and it is much easier to hit here
because the entries are gathered from four places that can each come up empty.

**Built for the keyboard first.** Up/Down move, Enter chooses, Escape closes,
because a palette opened with Ctrl+K and then driven with the mouse has saved
nobody any time.
"""

from __future__ import annotations

from typing import Callable, List, NamedTuple, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402 - after require


#: How tall the result list may grow before it starts scrolling.
#:
#: Measured on this widget, `CommandPalette` rows: **6 entries ask for 252px,
#: 20 for 840px, 50 for 2100px** - and `min == natural` at every one, with
#: `issubclass(Gtk.ListBox, Gtk.Scrollable)` **False**. A bare list therefore
#: cannot shrink, so on the 640px-tall window the app opens at, two thirds of
#: the palette was unreachable.
#:
#: 420px is a **ceiling, not a height**: below it the palette is exactly as tall
#: as its rows need, which is how the six-row case read before. It is not a
#: tuned number - it is about six rows, the last count that fits whole.
#:
#: The widget's own `measure()` in an unpresented probe reports 98px whatever
#: the entry count, because nothing is allocated; **the list's 252/840/2100 is
#: the figure that matters**, and this is why it is quoted rather than the
#: palette's.
_MAX_HEIGHT_PX = 420


class Entry(NamedTuple):
    """One destination. `run` is what Enter does."""

    title: str
    subtitle: str = ""
    icon: str = "go-next-symbolic"
    run: Optional[Callable[[], None]] = None
    #: Lower sorts first within a group; groups themselves keep the order they
    #: were given, so "Panels" stays above "Commands" however the text matches.
    group: str = ""
    weight: int = 0


def collect(candidates: List[Entry]) -> List[Entry]:
    """Keep only the entries that can actually be chosen.

    Deduplicated on title+group, because the four sources overlap: a panel can
    also be a slash command, and two identical rows in a search result reads as
    a bug rather than as two ways of saying the same thing.
    """
    seen, out = set(), []
    for entry in candidates:
        if entry.run is None or not entry.title.strip():
            continue
        key = (entry.title.strip().lower(), entry.group)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def filter_entries(entries: List[Entry], needle: str) -> List[Entry]:
    """Rank entries for a search string.

    **Empty query returns everything in its original order**, which is the order
    the caller chose - so opening the palette shows the common things first
    rather than an alphabetical list of 244 skills.

    Matching is on title, subtitle and group, case-folded. A non-matching query
    returns nothing rather than a fallback, so a typo shows an empty palette
    instead of silently showing something else.
    """
    needle = (needle or "").strip().lower()
    if not needle:
        return list(entries)
    hits = [e for e in entries
            if needle in e.title.lower() or needle in e.subtitle.lower()
            or needle in e.group.lower()]
    # Title matches first, then prefix matches, then the rest - stable within
    # each so the caller's ordering survives.
    def rank(entry: Entry):
        title = entry.title.lower()
        if title.startswith(needle):
            return (0, entry.weight)
        if needle in title:
            return (1, entry.weight)
        return (2, entry.weight)
    return sorted(hits, key=rank)


class CommandPalette(Gtk.Box):
    """The palette itself: a search entry over a list of destinations."""

    def __init__(self, on_close: Optional[Callable[[], None]] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        # **`CSS = "command-palette"` was applied to this box and styled by
        # nothing** - not this package, not GTK, not libadwaita. The constant
        # existed only to be applied once, and the one caller that read it was
        # the line below. A class that styles nothing is an unfulfilled claim,
        # not documentation; `CommandPalette`'s own measurements live at the top
        # of this file, which is where a reader will actually look.
        self._on_close = on_close or (lambda: None)
        self._entries: List[Entry] = []
        self._visible: List[Entry] = []

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Go to a panel, a command, a skill...")
        self._search.set_hexpand(True)
        self._search.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Search panels, commands and skills"])
        self.append(self._search)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        # **A scroller, because the list cannot shrink.** Measured on this
        # palette: the entries ask for **252px (6), 840px (20) and 2100px
        # (50)**, `min == natural` at every count, and
        # `issubclass(Gtk.ListBox, Gtk.Scrollable)` is **False** - so with the
        # bare list the palette was 2140px tall on a 640px window and
        # everything below the fold was unreachable. `Gtk.ListBox` is not
        # scrollable and cannot be made so; the scroller is the whole of the
        # answer.
        #
        # **`self._list` stays the ListBox**, because `_refilter`, `_row`,
        # `_move`, `_selected_index` and `_select` all address it, and a
        # reader that re-pointed them at the scroller would be testing
        # `Gtk.Scrollable`, which is the API that does not exist.
        scroller = Gtk.ScrolledWindow()
        # A cap rather than a demand: fewer than about six rows and the palette
        # is exactly as tall as its rows need, which is how it read before.
        scroller.set_max_content_height(_MAX_HEIGHT_PX)
        # **And the cap needs this half to be a cap rather than a crush.** Set
        # on its own, `max-content-height` does not make the scroller *grow*
        # toward the cap - it only refuses to be told it may be taller, and a
        # `ScrolledWindow` asks for very little by default. Measured in a
        # presented 320x900 window, identical widgets, only this flag differing:
        #
        #   50 entries, flag off -> 58px, upper 2100, scrolls  (58px is one row)
        #   50 entries, flag on  -> 420px, upper 2100, scrolls
        #    3 entries, flag off -> 58px, upper 126, scrolls   <- false scrollbar
        #    3 entries, flag on  -> 126px, upper 126, no scroll
        #
        # So without it the palette would open a single row tall and would draw
        # a scrollbar over three entries that fit. This is the measured reason
        # the flag is here and not merely plausible.
        scroller.set_propagate_natural_height(True)
        scroller.set_propagate_natural_width(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self._list)
        self._scroller = scroller
        self.append(scroller)

        self._empty = Gtk.Label(label="Nothing matches that.")
        self._empty.add_css_class("dim-label")
        self._empty.set_visible(False)
        self.append(self._empty)

        self._search.connect("search-changed", self._on_search_changed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # -- contents ---------------------------------------------------------

    def focus_search(self) -> None:
        """Put the caret in the search box, so typing works without a click."""
        self._search.grab_focus()

    def set_entries(self, entries: List[Entry]) -> None:
        self._entries = collect(list(entries))
        self._refilter(self._search.get_text())

    def visible_entries(self) -> List[Entry]:
        """What the palette is currently offering. For tests, and for callers
        that need to know whether a search found anything."""
        return list(self._visible)

    def _on_search_changed(self, entry) -> None:
        self._refilter(entry.get_text())

    def _refilter(self, text: str) -> None:
        child = self._list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._list.remove(child)
            child = following
        self._visible = filter_entries(self._entries, text)
        for entry in self._visible:
            self._list.append(self._row(entry))
        if self._visible:
            # **`get_row_at_index`, not the child widget.** `ListBox.append`
            # wraps each child in a `ListBoxRow`, and `select_row` takes *that* -
            # passing the child raises `TypeError: argument row: Expected
            # Gtk.ListBoxRow, but got gi.repository.Gtk.Box`. Measured while
            # building the window, before any test existed.
            self._list.select_row(self._list.get_row_at_index(0))
        self._empty.set_visible(not self._visible)

    def _row(self, entry: Entry) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(entry.icon)
        row.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_hexpand(True)
        title = Gtk.Label(label=entry.title)
        title.set_xalign(0.0)
        # **Both labels ellipsise, and this is a defect the layout contract
        # could not see until the palette was in the window at all.** These two
        # labels were built with neither `set_wrap` nor `set_ellipsize` - and
        # because `_build_palette` added the palette to *nothing*, no test ever
        # walked them. Parenting it (`window.py`) put fourteen of them inside
        # the main window, and `test_the_main_window_wraps_or_ellipsises_its_
        # long_text` immediately named all fourteen: three pre-existing mode
        # subtitles ("It answers anything, using whatever tools fit the ...")
        # and ten new cloud ones.
        #
        # **A hidden control's labels are still a defect the moment it is
        # shown**, and this is the clearest instance in the tree of a test that
        # could not fail - not because it was weak, but because its subject
        # was not in the tree.
        title.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(title)
        if entry.subtitle:
            subtitle = Gtk.Label(label=entry.subtitle)
            subtitle.add_css_class("dim-label")
            subtitle.set_xalign(0.0)
            subtitle.set_ellipsize(Pango.EllipsizeMode.END)
            text.append(subtitle)
        row.append(text)
        row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [entry.title if not entry.subtitle
             else f"{entry.title}, {entry.subtitle}"])
        return row

    # -- keyboard ---------------------------------------------------------

    def _on_key(self, _controller, keyval, _keycode, state) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._move(-1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            return self.activate_selected()
        if keyval == Gdk.KEY_Escape:
            self._on_close()
            return True
        return False

    def _move(self, delta: int) -> None:
        """Move the selection, wrapping - and staying put when empty.

        Wrapping is what a list does everywhere else; clamping stops at the ends
        and makes holding Down feel like hitting a wall.
        """
        if not self._visible:
            return
        index = self._selected_index()
        if index < 0:
            index = 0 if delta > 0 else len(self._visible) - 1
        else:
            index = (index + delta) % len(self._visible)
        self._select(index)

    def _selected_index(self) -> int:
        """Which entry is highlighted, or -1.

        **Walked by index against `get_row_at_index`, not by walking children
        and comparing parents.** In GTK 4 the child's parent is *not* the
        `ListBoxRow` that `get_selected_row()` returns - the row is an internal
        wrapper - so the parent comparison never matched, `_selected_index()`
        answered -1 for a visibly selected row, and Enter did nothing at all.
        Measured with `first_child.get_parent() is get_selected_row()`: False.
        """
        row = self._list.get_selected_row()
        if row is None:
            return -1
        for index in range(len(self._visible)):
            if self._list.get_row_at_index(index) is row:
                return index
        return -1

    def _select(self, index: int) -> None:
        if not 0 <= index < len(self._visible):
            return
        row = self._list.get_row_at_index(index)
        if row is None:
            return
        self._list.select_row(row)
        # **`Gtk.Widget.scroll_to`, not `ListBox.scroll_to_child`** - the latter
        # does not exist on GTK 4's ListBox (`AttributeError: 'ListBox' object
        # has no attribute 'scroll_to_child'`), and the palette could not move
        # its own selection without crashing.
        child = row.get_child()
        if child is not None:
            try:
                child.scroll_to(Gtk.WidgetAlign.CENTER, False, 0.0, 0.0, 0.0, 0.0)
            except Exception:                                   # noqa: BLE001
                pass

    def activate_selected(self) -> bool:
        """Run the highlighted entry. Returns whether anything ran.

        False is the honest answer for an empty palette, and it is what lets the
        window fall through to whatever else wanted that key.
        """
        index = self._selected_index()
        if index < 0 or index >= len(self._visible):
            return False
        entry = self._visible[index]
        if entry.run is None:
            return False
        entry.run()
        return True