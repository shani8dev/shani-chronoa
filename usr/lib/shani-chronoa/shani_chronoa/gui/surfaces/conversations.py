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

import datetime
import logging
import time
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

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


def _count_sentence(rows: "list", listed: "list") -> "tuple[str, str]":
    """`(title, detail)` for the store summary.

    A module-level function, not two f-strings inline in `build`, because **the
    test has to be able to reach it.** The first version of
    `tests/test_the_conversation_count_says_one_thing.py` reimplemented this
    arithmetic inside the test file, which meant it would still pass after the
    panel changed - the same tautology found and fixed in
    `tests/test_listening_gate_offers_a_way.py`.

    **Pluralised the way this same file already did it thirty lines below** -
    `f"{count} message{'s' if count != 1 else ''}"` - rather than with a `(s)`
    nobody should see on screen. The measured panel said:

        1 conversation(s) recorded - 0 empty, 1 with messages

    Three numbers where one was wanted. The detail is a *count that only ever
    restates the count* when every conversation has messages in it, so it is
    empty then; naming the empty ones is the case that earns it, because that is
    how a person tells a deleted conversation from one opened and left.
    """
    title = f"{len(rows)} conversation{'' if len(rows) == 1 else 's'} recorded"
    empties = len(listed) - len(rows)
    detail = f"{empties} of them have no messages" if empties else ""
    return title, detail


def _days_between(stamp: float, now: float) -> int:
    """Whole days from `stamp` to `now`, by calendar date rather than by seconds.

    By calendar, because "yesterday" is a day on the wall and not a 24-hour span:
    a conversation at 23:50 and one at 00:10 the next morning are ten minutes
    apart and on different sides of the word.
    """
    then, today = time.localtime(stamp), time.localtime(now)
    return (datetime.date(today.tm_year, today.tm_mon, today.tm_mday)
            - datetime.date(then.tm_year, then.tm_mon, then.tm_mday)).days


def _day_label(stamp: float, now: "Optional[float]" = None) -> str:
    """`Today`, `Yesterday`, `Tuesday`, `02 Oct`, `02 Oct 2025` - the heading.

    The same vocabulary `_when` always used, with the clock taken off, because the
    two were one string and a grouped list needs them apart. **One implementation
    of the vocabulary**, so the heading and the row cannot drift into saying
    "Yesterday" and "Tuesday" about the same day.
    """
    now = time.time() if now is None else now
    days = _days_between(stamp, now)
    then, today = time.localtime(stamp), time.localtime(now)
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return time.strftime("%A", then)
    if then.tm_year == today.tm_year:
        return time.strftime("%d %b", then)
    return time.strftime("%d %b %Y", then)


#: A clock is shown only while the day word could still mean more than one
#: conversation. "Today 14:02" and "Tuesday 08:05" identify one; "02 Sep 08:00"
#: does not - it is a date and a time with no way to tell which matters - which
#: is why this module has always dropped the clock past a week rather than
#: printing both. Bounded here rather than re-derived in three callers.
_CLOCK_WITHIN_DAYS = 7


def _clock(stamp: float, now: "Optional[float]" = None) -> str:
    """`14:02` - or `""` for anything older than a week.

    **The row's half of the date, not the day word's.** With the day in a
    heading above it, a clock is redundant the moment the heading is unambiguous:
    every conversation under "02 Oct" is on 2 October whoever made it. It is kept
    while the heading is "Today" or "Tuesday", where a day can hold a dozen
    conversations and the order is the only thing that separates them.

    Returning `""` rather than a date is the module's own pre-existing behaviour -
    `_when` said `02 Sep`, not `02 Sep 08:00` - so this is the one rule behind
    both the row and `_when` rather than two ladders that can disagree.
    """
    now = time.time() if now is None else now
    if _days_between(stamp, now) >= _CLOCK_WITHIN_DAYS:
        return ""
    return time.strftime("%H:%M", time.localtime(stamp))


def _when(stamp: float, now: "Optional[float]" = None) -> str:
    """`Today 14:02`, `Yesterday 09:10`, `Tuesday 18:30`, `02 Oct`, `02 Oct 2025`.

    **The day and the clock in one string, which is what this file used to put on
    every row**, and the exact ladder it always walked - a clock inside the week, a
    bare date past it. It is now built from the same two functions the panel's own
    headings and rows use, so there is one vocabulary and one rule about when a
    clock is worth printing.

    The trailing shape matters: `_clock` returning `""` is what keeps a September
    conversation reading `02 Sep` rather than `02 Sep 08:00`, and
    `tests/test_surface_conversations.py::test_row_dates_are_relative` pins it.
    """
    now = time.time() if now is None else now
    day = _day_label(stamp, now)
    clock = _clock(stamp, now)
    return f"{day} {clock}" if clock else day


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
        #: `(heading, rows under it)` per day, in the order the days appear. Kept
        #: beside `_row_widgets` rather than inside it because the two answer
        #: different questions: a row is "does the query match this", a heading is
        #: "does the query match **any** of these" - and only the second one can be
        #: computed from the first.
        self._day_rows: List[tuple] = []
        self._group = common.group()
        # Same 12px inset as the filter row, so the list and the controls
        # above it share one left edge.
        self._group.set_margin_start(12)
        self._group.set_margin_end(12)
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
        # Inset like the rows under it. Rendered without margins, the filter ran
        # into the window's left edge and the button into its right.
        for side in ("start", "end", "top", "bottom"):
            getattr(header, f"set_margin_{side}")(12 if side in ("start", "end") else 6)
        self.search = common.search_entry("Filter by title or text", self._on_search_changed)
        self.search.set_tooltip_text(
            "Filter conversations by title or message text")
        header.append(self.search)
        self.new_button = Gtk.Button(label="New conversation")
        self.new_button.add_css_class("suggested-action")
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
                common.STATUS_OK,
                "No conversations recorded",
                "the store is empty, which is not the same as "
                "every conversation having been deleted"))
        else:
            rows = [item for item in listed if item["messages"] or item["active"]]
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK,
                *_count_sentence(rows, listed)))

        # Removed by reference. `Adw.PreferencesGroup` is a `Gtk.ListBox` with
        # libadwaita's own boxes inside it, so walking it for children finds
        # boxes that cannot be removed from it - measured:
        # `tried to remove non-child ... of type 'GtkBox' from
        # 'AdwPreferencesGroup'`, once per pass, which over a `while` loop is a
        # child that never goes away.
        for row, _open_btn, _delete, _haystack in self._row_widgets:
            self._group.remove(row)
        self._row_widgets = []
        # **The headings have to be removed too, or a second refresh stacks a
        # second copy of every day above the first.** They go after the rows
        # because they sit above them, and `PreferencesGroup.remove` is by
        # reference - the same trap the row loop above documents.
        for heading, _rows_under in self._day_rows:
            self._group.remove(heading)
        self._day_rows = []

        if error is not None:
            self._present_empty("dialog-error-symbolic", UNREADABLE, str(error))
            return

        rows = [item for item in listed if item["messages"] or item["active"]]
        if not rows:
            self._present_empty("dialog-information-symbolic", NOTHING_YET, NOTHING_YET_DESC)
            return

        self._present_rows()
        root = conversation_store.session_dir()
        # One `now` for the whole pass: reading the clock per row means a refresh
        # that straddles midnight puts one conversation under "Today" and the
        # next under "Yesterday" with both stamped the same way, and the heading
        # is then a claim about a time this loop invented.
        now = time.time()
        last_day = None
        for item in rows:
            day = _day_label(item["updated"], now)
            if day != last_day:
                self._add_day_heading(day)
                last_day = day
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
            self._add_row(item, haystack, notice, now)
        self._apply_filter(self.search.get_text())

    def _add_row(self, item: dict, haystack: str, notice: str = "",
                 now: "Optional[float]" = None) -> None:
        """Add one conversation's row, and remember the widgets it is made of.

        `now` is **passed in rather than read here**, because `_refresh` already
        reads it once for the day headings: two reads straddling midnight would
        put a conversation under "Today" with a row stamped "Yesterday".

        Both controls are `Gtk.Button`s inside an `Adw.ActionRow`, rather than a
        row whose own title is the conversation's name and whose suffix is a
        chevron. The button carries the name - so what a screen reader reads is
        "Open conversation titled <name>" rather than "Open" - and the row
        brings the focus ring, the hover state and the spacing, instead of those
        being written again here.
        """
        # A list row, not a centred button label. It was `• title  (02 Oct
        # 00:42)` in bold, centred in the row: a typed bullet for "current", the
        # date in brackets, and nothing about how long the conversation was.
        # Now: the title on the left, and one short line under it - when, how
        # many messages, whether it is the open one. Short on purpose:
        # libadwaita 1.5 does not wrap an `Adw.ActionRow` subtitle, so this is a
        # plain label that ellipsises rather than one that can widen the page.
        open_btn = Gtk.Button()
        open_btn.add_css_class("flat")
        open_btn.add_css_class("conversation-open")
        open_btn.set_hexpand(True)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=item["title"], xalign=0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.add_css_class("conversation-title")
        text.append(title)
        # **The clock, not `Today 14:02`.** The day is now a heading above the
        # group, and repeating it on every row is what made a returning reader
        # read down past three dates to learn whether anything had happened today.
        # **Appended, not prepended, and only when there is one.** `_clock` is
        # empty for anything past a week, and a list starting with `""` joins to
        # a leading `" · "` - a row that opens with a separator and says nothing
        # before it.
        bits = []
        clock = _clock(item["updated"], now)
        if clock:
            bits.append(clock)
        count = int(item.get("messages") or 0)
        bits.append(f"{count} message{'s' if count != 1 else ''}")
        if item["active"]:
            bits.append("Current")
        meta = Gtk.Label(label=" \u00b7 ".join(bits), xalign=0.0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.add_css_class("dim-label")
        meta.add_css_class("caption")
        text.append(meta)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if notice:
            # An unfinished last turn used to be a tooltip only - nothing on
            # the row said the conversation ended mid-answer.
            mark = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            mark.set_tooltip_text(notice)
            mark.update_property([Gtk.AccessibleProperty.LABEL], [notice])
            line.append(mark)
        line.append(text)
        open_btn.set_child(line)
        open_btn._title = title
        open_btn._meta = meta
        open_btn.set_tooltip_text(
            f"{notice}\n\nOpen this conversation" if notice else "Open this conversation")
        _access(open_btn, f"Open conversation titled {item['title']}")
        open_btn.connect("clicked", lambda _b: self._call("_open_conversation", item["id"]))
        delete = Gtk.Button(icon_name="user-trash-symbolic")
        delete.add_css_class("flat")
        delete.set_tooltip_text("Delete this conversation")
        _access(delete, f"Delete conversation titled {item['title']}")
        delete.connect("clicked", lambda _b: self._call("_delete_conversation", item["id"]))
        delete.set_valign(Gtk.Align.CENTER)
        rename = self._rename_button(item)
        branch = self._branch_button(item)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        actions.set_hexpand(True)
        actions.set_margin_end(6)
        actions.append(open_btn)
        actions.append(branch)
        actions.append(self._checkpoint_button(item))
        actions.append(rename)
        actions.append(delete)
        # `PreferencesRow` holding the line directly: an `ActionRow` suffix
        # centres what it is given, which is what centred the title before.
        if common.adw_ready():
            row = Adw.PreferencesRow()
            row.set_child(actions)
        else:
            row = common.row("", "", suffix=actions)
        self._group.add(row)
        self._row_widgets.append((row, open_btn, delete, haystack))
        if self._day_rows:
            self._day_rows[-1][1].append(row)

    def _add_day_heading(self, day: str) -> None:
        """One heading for a run of rows from the same day.

        **There is no `common.heading()`** - `heading` is a CSS class name used
        inside `common.group()` and one other builder, and the first version of
        this called it and would have raised `AttributeError` on the first store
        with two days in it. An `Adw.PreferencesRow` holding the label is what the
        rows below already use, so it is what this uses.

        **And it is not activatable, on purpose.** A heading that is a focus stop
        is a Tab that goes nowhere: `compare.py` had exactly this, because
        `_row()` made headings, counts and instructions selectable and every
        heading in that panel became a Tab stop.
        """
        label = _label(day)
        label.set_xalign(0.0)
        label.add_css_class("dim-label")
        if common.adw_ready():
            row = Adw.PreferencesRow()
            row.set_child(label)
        else:
            row = common.row("", "", suffix=label)
        row.set_activatable(False)
        self._group.add(row)
        self._day_rows.append((row, []))

    def _branch_button(self, item: dict) -> Gtk.Button:
        """Continue from a copy, keeping the original as it was.

        `conversation_store.fork()` was reachable only by asking in chat (the
        `conversations` skill): there was no way to try a different direction
        from an earlier conversation without losing the one you had.
        """
        button = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text("Branch: continue in a copy, keep this one as it is")
        _access(button, f"Branch conversation titled {item['title']}")

        def branch(_b: Gtk.Button) -> None:
            try:
                new = conversation_store.fork(conversation_store.session_dir(), item["id"])
            except Exception as exc:  # noqa: BLE001 - a branch must not take the panel
                logger.warning("could not branch %s: %s", item["id"], exc)
                return
            if new:
                self._call("_open_conversation", new)
            GLib.idle_add(lambda: (self._refresh(), False)[1])

        button.connect("clicked", branch)
        return button

    def _checkpoint_button(self, item: dict) -> Gtk.MenuButton:
        """Set a point to come back to, and return to one.

        **`conversation_store.checkpoint()`, `revert()` and `list_checkpoints()`
        all exist, and nothing in the whole codebase called any of them.** Not
        the GUI, not a skill, not `app/brain.py` - verified by grepping the
        package and `tests/` for `checkpoint(` and `revert(`. The capability
        explains itself:

            A checkpoint is a copy of the conversation at a point the user
            wants to come back to - "before I edited that file" - rather than
            a reset.

        So a person could neither save that point nor return to it, from
        anywhere. Wired here the way `_branch_button` wires `fork()`, which was
        in the same position before it: reachable only by asking a model in chat.

        The distinction kept is the module's own: a checkpoint is **not** a
        reset. `revert()` rewrites the current transcript and leaves the
        checkpoint in place, so the same point can be returned to again - which
        is why the menu offers every numbered checkpoint rather than one "undo",
        and why the tooltip says so.
        """
        button = Gtk.MenuButton(icon_name="document-save-symbolic",
                                valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        _access(button, f"Checkpoints for {item['title']}")
        button.set_tooltip_text("Set a point to come back to, or return to one")

        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(6)

        save = Gtk.Button(label="Set a checkpoint here")
        save.add_css_class("flat")

        def set_it(_b: Gtk.Button) -> None:
            try:
                conversation_store.checkpoint(conversation_store.session_dir(),
                                              item["id"])
            except Exception as exc:  # noqa: BLE001 - a failed save must not take the panel
                logger.warning("could not checkpoint %s: %s", item["id"], exc)
                return
            GLib.idle_add(lambda: (self._refresh(), False)[1])

        save.connect("clicked", set_it)
        box.append(save)

        try:
            have = conversation_store.list_checkpoints(
                conversation_store.session_dir(), item["id"])
        except Exception:  # noqa: BLE001 - an unreadable list is not a dead button
            have = []
        for n in have:
            back = Gtk.Button(label=f"Return to checkpoint #{n}")
            back.add_css_class("flat")
            back.set_tooltip_text(
                "Return the transcript to that point. The checkpoint stays, so "
                "you can return to it again - this is not a reset.")

            def back_to(_b: Gtk.Button, n: int = n) -> None:
                try:
                    conversation_store.revert(conversation_store.session_dir(),
                                              item["id"], n)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("could not revert %s to %s: %s",
                                   item["id"], n, exc)
                    return
                GLib.idle_add(lambda: (self._refresh(), False)[1])

            back.connect("clicked", back_to)
            box.append(back)

        if not have:
            note = Gtk.Label(label="No checkpoints saved yet.", xalign=0.0)
            note.add_css_class("dim-label")
            box.append(note)

        pop.set_child(box)
        button.set_popover(pop)
        return button

    def _rename_button(self, item: dict) -> Gtk.MenuButton:
        """Rename a conversation in place.

        `conversation_store.rename()` existed - and marks the title as chosen,
        so the model never overwrites it - with no control anywhere: a title was
        whatever the first message or the model made of it, for good.
        """
        button = Gtk.MenuButton(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text("Rename this conversation")
        _access(button, f"Rename conversation titled {item['title']}")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(6)
        entry = Gtk.Entry(text=item["title"], hexpand=True)
        entry.set_width_chars(28)
        _access(entry, "New title")
        save = Gtk.Button(label="Rename")
        save.add_css_class("suggested-action")
        box.append(entry)
        box.append(save)
        popover = Gtk.Popover()
        popover.set_child(box)
        button.set_popover(popover)

        def commit(*_a) -> None:
            title = entry.get_text().strip()
            if title and title != item["title"]:
                try:
                    conversation_store.rename(conversation_store.session_dir(), item["id"], title)
                except Exception as exc:  # noqa: BLE001 - a rename must not take the panel
                    logger.warning("could not rename %s: %s", item["id"], exc)
            popover.popdown()
            # After the popover has closed: refreshing rebuilds the rows, and
            # this button with them.
            GLib.idle_add(lambda: (self._refresh(), False)[1])

        save.connect("clicked", commit)
        entry.connect("activate", commit)
        button._rename_entry, button._rename_save = entry, save
        return button

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
        # **A heading with nothing under it is a lie** - it names a day the filter
        # found nothing from, so typing one word leaves "Tuesday" above an empty
        # gap. This is the half that makes grouping safe to add to a list with a
        # search box on it, and it is derived from the rows rather than tracked
        # separately, so a row the filter cannot reach cannot leave one behind.
        for heading, rows_under in self._day_rows:
            heading.set_visible(any(r.get_visible() for r in rows_under))
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
