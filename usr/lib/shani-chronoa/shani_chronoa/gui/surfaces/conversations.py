"""Every saved conversation as a full page, mirroring the Conversations popover.

Rows come from `conversation_store` and are filtered by the search entry
against title and message text, newest first. Buttons call the application's
own methods - no new store API is added here.

**The page is `surfaces/common.py`'s; what is under the toolbar is this
module's.** `common.surface()` builds the `Adw.NavigationPage`, its
`ToolbarView` and its `HeaderBar`, `common.search_entry()` builds the filter and
`common.group()`/`common.row()` build the rows, so this panel reads as one of
the sidebar's panels rather than as a hand-built box that happens to sit beside
them. The filter stays pinned above the list and only the list scrolls, which is
why the scroller is inside the view rather than around it: a filter that scrolls
out of reach above a list of nothing cannot be cleared by the person looking at
it.

**The filter is driven the way a person drives it, and that changes what a
caller may assume.** `Gtk.SearchEntry` fires `search-changed` 150 ms after the
last keystroke, and `set_text()` emits neither signal, so a filter that reads
the entry at the moment it is set sees the *previous* query - which is how a
test comes to pass for the wrong reason. Everything here therefore reads the
text the signal hands over, and `_refresh` asks the widget directly only because
it is the code that just filled the widget.

**Three answers, one slot, and none of them is a blank rectangle.** The store
had nothing (the first run of the app, or a store that was cleared), the store
could not be read (its index is a file another process can be writing while
this panel is built, so `list_sessions` is allowed to fail), and the filter
excluded everything (the rows are all still there, hidden by the filter). Each
gets its own sentence, and a panel that showed "no conversations" for a corrupt
index would be the confident wrong answer this repo keeps paying for. The "New
conversation" button stays on the page in all three, because "start a new one"
is advice the page has to be able to act on.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from shani_chronoa import conversation_store, history_repair, markdown_lite
from shani_chronoa.gui.surfaces import common

logger = logging.getLogger(__name__)

TITLE = "Conversations"
ICON = "view-list-symbolic"

#: The page's own line above its header bar, saying which way the list runs: a
#: list with a date on every row and no order stated is one people re-sort by
#: hand, in the store, forever.
SUBTITLE = "Every saved conversation, newest first."

NO_MATCHES = "No conversation matches the filter."
NOTHING_YET = "No conversations yet"
NOTHING_YET_DESC = "Start a new one and it will show up here."
UNREADABLE = "Could not read conversations"

#: The list gets its own height cap rather than growing with the store: this
#: panel sits beside a window that also has a transcript and a composer, and a
#: store with four hundred conversations should not push the filter off the top
#: of the window to show the oldest of them.
ROWS_MAX_HEIGHT = 520


def _matches(haystack: str, query: str) -> bool:
    tokens = [token for token in query.lower().split() if token]
    low = haystack.lower()
    return all(token in low for token in tokens)


def _access(widget: Gtk.Widget, label: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])


def _label(text: str) -> Gtk.Label:
    label = Gtk.Label()
    label.set_markup(markdown_lite.escape(text))
    return label


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


class _ConversationsView(Gtk.Box):
    """The filter, the "New conversation" button and the list: everything under
    the page's toolbar.

    Rows are read once, in `_refresh`, and the filter narrows a list already in
    hand rather than re-reading the store on every keystroke - so an index that
    cannot be read is one empty state at open time instead of one per keystroke.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._app = app
        self._row_widgets: List[tuple] = []
        self._group = common.group()
        self._rows_area: Optional[Gtk.Widget] = None
        self.empty_state: Gtk.Widget

        self.append(self._filter_row())
        # The panel's own health, above the list: how many
        # conversations the store holds, and whether the store
        # could be read at all. One row, one dot, one word -
        # the question the panel is opened for, before the rows
        # that hold the conversations.
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.append(self._status_slot)
        #: The same health in two places - the row above the list, and the dot
        #: on the sidebar's row for this panel. One value, so the two cannot
        #: disagree about the same store.
        self.status_recorder = common.StatusRecorder()
        # One slot, three answers: the rows, the reason there are none, or the
        # reason they are hidden. Stacking them instead would say "no
        # conversations" under a list of nothing, or put a status page above the
        # filter that caused it.
        self._list_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._list_area.set_vexpand(True)
        self.append(self._list_area)
        self.no_matches = common.empty_state("dialog-information-symbolic", NO_MATCHES)
        self.no_matches.set_visible(False)

        # Something is in the slot before `_refresh` reads the store, so that a
        # read that raises halfway cannot leave the page with no answer in it.
        self._present_empty("dialog-information-symbolic", NOTHING_YET, NOTHING_YET_DESC)
        self._refresh()

    # -- the filter and the button -------------------------------------------

    def _filter_row(self) -> Gtk.Widget:
        """The filter, and the button that starts the next conversation.

        `common.search_entry()` rather than a `Gtk.Entry`: the search icon and
        the clear button come with it, and it fires on a pause instead of on
        every keystroke, so a long list is not refiltered once per character as
        someone types a phrase.
        """
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = common.search_entry("Filter by title or text", self._on_search_changed)
        self.search.set_tooltip_text(
            "Filter conversations by title or message text")
        header.append(self.search)
        self.new_button = Gtk.Button(label="New conversation")
        self.new_button.set_tooltip_text("Start a new conversation")
        _access(self.new_button, "Start a new conversation")
        self.new_button.connect("clicked", self._on_new)
        header.append(self.new_button)
        return header

    # -- the list -------------------------------------------------------------

    def _refresh(self) -> None:
        try:
            listed = conversation_store.list_sessions(conversation_store.session_dir())
            error = None
        except Exception as exc:  # a missing or stale index is an empty state, not a crash
            listed = []
            error = exc

        # The panel's own health, before the list: is the store
        # readable, is it holding conversations, or could it not
        # be told? One row, one dot, one word - the question the
        # panel is opened for, before the rows that hold the
        # conversations.
        common.clear(self._status_slot)
        if error is not None:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                "The conversation store could not be read",
                str(error)))
        elif not listed:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_UNKNOWN,
                "No conversations recorded",
                "the store is empty, which is not the same as "
                "every conversation having been deleted"))
        else:
            rows = [item for item in listed if item["messages"] or item["active"]]
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK,
                f"{len(rows)} conversation(s) recorded",
                f"{len(listed) - len(rows)} empty, {len(rows)} "
                "with messages"))

        # Removed by reference. `Adw.PreferencesGroup` is a `Gtk.ListBox` with
        # libadwaita's own boxes inside it, so walking it for children finds
        # boxes that cannot be removed from it - measured:
        # `tried to remove non-child ... of type 'GtkBox' from
        # 'AdwPreferencesGroup'`, once per pass, which over a `while` loop is a
        # child that never goes away.
        for row, _open_btn, _delete, _haystack in self._row_widgets:
            self._group.remove(row)
        self._row_widgets = []

        if error is not None:
            self._present_empty("dialog-error-symbolic", UNREADABLE, str(error))
            return

        rows = [item for item in listed if item["messages"] or item["active"]]
        if not rows:
            self._present_empty("dialog-information-symbolic", NOTHING_YET, NOTHING_YET_DESC)
            return

        self._present_rows()
        root = conversation_store.session_dir()
        for item in rows:
            haystack = item["title"]
            notice = ""
            try:
                messages = conversation_store.load(root / f"{item['id']}.jsonl")
                text = " ".join(_text_of(m) for m in messages)
                # The transcript is already in hand for the search haystack, so
                # noticing a turn that never finished costs nothing here.
                notice = history_repair.unfinished_notice(messages)
            except OSError:
                text = ""
            haystack = haystack + " " + text
            self._add_row(item, haystack, notice)
        self._apply_filter(self.search.get_text())

    def _add_row(self, item: dict, haystack: str, notice: str = "") -> None:
        """Add one conversation's row, and remember the widgets it is made of.

        Both controls are `Gtk.Button`s inside an `Adw.ActionRow`, rather than a
        row whose own title is the conversation's name and whose suffix is a
        chevron. The button carries the name - so what a screen reader reads is
        "Open conversation titled <name>" rather than "Open" - and the row
        brings the focus ring, the hover state and the spacing, instead of those
        being written again here.
        """
        when = time.strftime("%d %b %H:%M", time.localtime(item["updated"]))
        open_btn = Gtk.Button()
        open_label = _label(f"{'• ' if item['active'] else ''}{item['title']}  ({when})")
        open_label.set_halign(Gtk.Align.START)
        open_label.set_ellipsize(Pango.EllipsizeMode.END)
        open_label.set_max_width_chars(40)
        open_btn.set_child(open_label)
        open_btn.add_css_class("flat")
        open_btn.set_hexpand(True)
        # A tooltip rather than a subtitle on purpose. `Adw.ActionRow` does not
        # wrap its own subtitle on libadwaita 1.5 - a 130-character one measured
        # 1,227px - and this surface is under the layout contract that fails on a
        # horizontal scrollbar. A tooltip is text with no layout cost at all.
        open_btn.set_tooltip_text(
            f"{notice}\n\nOpen this conversation" if notice else "Open this conversation")
        _access(open_btn, f"Open conversation titled {item['title']}")
        open_btn.connect("clicked", lambda _b: self._call("_open_conversation", item["id"]))
        delete = Gtk.Button(icon_name="user-trash-symbolic")
        delete.add_css_class("flat")
        delete.set_tooltip_text("Delete this conversation")
        _access(delete, f"Delete conversation titled {item['title']}")
        delete.connect("clicked", lambda _b: self._call("_delete_conversation", item["id"]))
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        actions.set_hexpand(True)
        actions.append(open_btn)
        actions.append(delete)
        row = common.row("", "", suffix=actions)
        self._group.add(row)
        self._row_widgets.append((row, open_btn, delete, haystack))

    # -- the three answers ---------------------------------------------------

    def _present(self, widget: Gtk.Widget) -> None:
        """One widget in the list slot; whatever was there is removed."""
        child = self._list_area.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._list_area.remove(child)
            child = following
        self._list_area.append(widget)

    def _present_rows(self) -> None:
        if self._rows_area is None:
            self._rows_area = common.scrolled(self._group, ROWS_MAX_HEIGHT)
        # Hidden, not merely unparented: `empty()` and `no_matches` read their
        # own visibility, and a widget nobody hid still says yes.
        self.empty_state.set_visible(False)
        self.no_matches.set_visible(False)
        self._present(self._rows_area)

    def _present_empty(self, icon_name: str, title: str, description: str) -> None:
        self.empty_state = common.empty_state(icon_name, title, description)
        self.empty_state.set_vexpand(True)
        self.no_matches.set_visible(False)
        self._present(self.empty_state)

    def _present_no_matches(self) -> None:
        self.empty_state.set_visible(False)
        self.no_matches.set_vexpand(True)
        self.no_matches.set_visible(True)
        self._present(self.no_matches)

    # -- wiring --------------------------------------------------------------

    def _call(self, name: str, *args) -> None:
        method = getattr(self._app, name, None)
        if callable(method):
            method(*args)
        else:
            logger.warning("app has no %s; the button cannot act", name)

    def _on_new(self, _button: Gtk.Button) -> None:
        method = getattr(self._app, "_reset_conversation", None)
        if callable(method):
            method(None, None)
        else:
            logger.warning("app has no _reset_conversation; new button cannot act")

    def _on_search_changed(self, query: str) -> None:
        """`common.search_entry()` hands over the query it just settled on."""
        self._apply_filter(query)

    def _apply_filter(self, query: str) -> None:
        visible = 0
        for row, _open_btn, _delete, haystack in self._row_widgets:
            show = _matches(haystack, query)
            row.set_visible(show)
            if show:
                visible += 1
        if not self._row_widgets:
            # Nothing to filter: whatever the store said is still the answer.
            return
        if visible == 0:
            self._present_no_matches()
        else:
            self._present_rows()

    # Public for tests: the widget holding each row, its open and delete
    # buttons, and the text used for filtering.
    def rows(self) -> List[tuple]:
        return list(self._row_widgets)

    def visible_row_count(self) -> int:
        return sum(1 for row, _o, _d, _h in self._row_widgets if row.get_visible())

    def empty(self) -> bool:
        return self.empty_state.get_visible()


def build(app: Any) -> Gtk.Widget:
    """The conversations page for `app`: an `Adw.NavigationPage`.

    `build()` hands back the page, so the things a caller needs from this surface
    are reachable on it - the filter to narrow by, and the answers to "is there
    anything here, and how much of it is showing". Set as attributes rather than
    wrapped, because the page is a libadwaita widget and this is the one shape
    that works on it.
    """
    view = _ConversationsView(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(view)
    # `_apply_filter` rides out too: re-applying a query is how a caller checks
    # that a narrowing filter came back after something else replaced it, and
    # typing the same words again would not change the entry's text, so nothing
    # would arrive to be observed.
    for name in ("search", "new_button", "no_matches", "rows",
                 "visible_row_count", "empty", "_apply_filter"):
        setattr(page, name, getattr(view, name))
    # What this panel says about itself, for the sidebar's health dot - read
    # from the same recorder that built the row at the top of the list, so the
    # dot and the row cannot say different things about the same store.
    page.status = view.status_recorder.status
    return page


# Kept for tests and other surfaces that want the same filter semantics.
__all__ = ["TITLE", "ICON", "build", "SUBTITLE", "NO_MATCHES"]
