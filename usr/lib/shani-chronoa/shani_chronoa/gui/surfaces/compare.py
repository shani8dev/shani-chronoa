"""Compare two conversations by the questions they asked.

**Why a panel, and not a skill.** A model can be asked "what did I ask about
last week", and will answer from the conversation it currently holds - which is
not the same question. Asking across conversations needs both sets of questions
in hand at once, and that is a thing a panel does rather than a thing a model
does.

**Why questions and not text.** Assistant text is not comparable: the same
question answered twice gives two different answers, so a diff over that half
reports every turn as new on every comparison. `conversation_diff` holds the
comparison, and this file holds the widgets.

**The two drop-downs are both optional and neither is preselected to a
surprising pair.** Defaulting to the two most recent is the useful choice for
"what's new since last time", which is the common case - but the order is
stated in the labels ("Earlier"/"Later") so a pair picked the other way round is
still readable rather than being silently reversed.
"""

import logging
from typing import Any, List

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import conversation_diff as diff_module  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.words import plural

logger = logging.getLogger(__name__)

TITLE = "Compare"
#: `document-compare-symbolic` and `edit-compare-symbolic` are both absent from
#: Adwaita - checked with `rglob` over the theme directory, because an icon name
#: the theme lacks renders as an empty box that reads as a bug rather than as a
#: missing glyph.
#: **`view-dual`, not `view-continuous`, because `activity` already had it.**
#: The first version of this line used `view-continuous-symbolic` and its own
#: comment called it "the theme's own 'side by side'" - which it is, and which
#: is why `activity` was already using it for exactly that idea.
#: `test_no_two_panels_share_an_icon` caught the clash, and it is the right
#: test: a sidebar row's icon is the fastest thing to match when scanning, so
#: two of the same make two different questions look like one. `view-dual` is
#: also "two panes side by side" and is used by nothing else in the tree.
ICON = "view-dual-symbolic"
SECTION = "Remembering"

SUBTITLE = ("What changed between two conversations, by the questions you asked. "
            "Replies are not compared: the same question answered twice gives two "
            "different answers.")

NEED_TWO = "Pick two conversations to compare."
ONLY_ONE = "There is only one conversation to compare."
UNREADABLE = "Could not read your conversations"
NO_QUESTIONS = "One of these asked nothing."

#: Read when either side was cut short by the store's message window.
WINDOW_NOTE = "oldest questions are not counted here"


def _store_root(app: Any):
    """Where conversations live.

    **`conversation_store.session_dir()`, the way the Conversations panel asks.**
    The first version guessed at application attributes - `_conversation_root`,
    `conversation_root`, `_sessions_root`, `session_dir` - and the
    application has **none of them**; every caller in `app/` and in
    `gui/surfaces/conversations.py` asks the module function instead. So the
    panel would have found no root and said "Could not read your conversations"
    on every machine, forever.

    **The tests could not catch it**, because the fixture was a stub built to
    match the guess. A test that supplies the thing the code asks for proves
    only that the code can read the thing the test gave it - the real question
    is what it asks for, and `test_the_app_has_the_root_this_asks_for` now asks
    the application itself.

    An explicitly supplied root still wins, so a test can point the panel at a
    fixture store - but it is named `_test_conversation_root` rather than
    `_conversation_root` so a stub cannot accidentally stand in for the real
    answer.
    """
    override = getattr(app, "_test_conversation_root", None)
    if override is not None:
        return override
    from shani_chronoa import conversation_store

    try:
        return conversation_store.session_dir()
    except Exception as exc:  # noqa: BLE001
        logger.debug("compare: the conversation directory is unknown: %s", exc)
        return None


def _conversations(root) -> "List[tuple[str, str]]":
    """`(id, label)` newest first, labelled so two can be told apart.

    **The label carries the message count**, because two conversations
    generated from the same first question have the same title - and a pair of
    drop-downs reading "Fix the build" and "Fix the build" makes it impossible
    to say which two are being compared. `list_sessions` already counts the
    messages, so this adds no formatting of its own and needs no date library:
    a date would mean a fourth `_when()` in this package (there are already
    three, each for a different type of thing), and the Conversations panel
    beside it is where dates belong.

    **The index, not the files.** Enumerating `*.jsonl` would list a
    conversation whose index entry never got written - an interrupted session -
    and then fail to load it, which is a panel reporting a broken file for a
    conversation that is merely unlisted.
    """
    from shani_chronoa import conversation_store

    try:
        listed = conversation_store.list_sessions(root)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is a message
        logger.debug("compare: the conversation store is unreadable: %s", exc)
        return []
    # **`list_sessions`, not a hand-rolled read of the index.** It already
    # sorts by `updated` descending and already resolves an untitled
    # conversation's name; the first version reimplemented both from the raw
    # index, which is a second answer to "what is this conversation called" and
    # a second one that drifts.
    out = []
    for item in listed:
        title = str(item.get("title") or "Untitled")
        try:
            count = int(item.get("messages") or 0)
        except (TypeError, ValueError):
            count = 0
        label = f"{title} - {count} message" + ("" if count == 1 else "s")
        out.append((str(item.get("id") or ""), label))
    return out


def _messages(root, sid: str) -> "tuple[list, bool]":
    """`(messages, was_truncated)`.

    **`load()` keeps only the last `MAX_LOADED` messages and says nothing.**
    That is the right behaviour for opening a transcript, and the wrong one
    here: this panel exists to answer "have I asked this before?", and a
    question asked forty turns before the window opens is reported under
    **"Only in the later"** - a confident *you have not asked this before*
    built out of a message the reader simply never had.

    So the truncation is detected rather than inherited. Counting the file's
    own non-blank lines against what came back is one pass over a file this
    panel has already read, and it does not need `load()` to change.
    """
    from shani_chronoa import conversation_store

    try:
        path = conversation_store.session_path(root, sid)
        messages = conversation_store.load(path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("compare: conversation %s is unreadable: %s", sid, exc)
        return [], False

    truncated = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = sum(1 for line in handle if line.strip())
        truncated = lines > len(messages)
    except Exception as exc:  # noqa: BLE001 - a count that cannot be had is not fatal
        logger.debug("compare: could not size conversation %s: %s", sid, exc)
    return messages, truncated


def _row(text: str, dim: bool = False, selectable: bool = False) -> Gtk.Label:
    """One line of the panel. `selectable` is opt-in, and only content gets it.

    **This used to be `set_selectable(True)` unconditionally**, and a selectable
    GTK4 label is a focus stop that paints its own accent-highlighted background
    once it holds focus. Measured in a render: *"Pick two conversations to
    compare."* came back inside an orange box, which reads as a text selection
    rather than as an instruction - and it was the *first* focusable widget in
    the panel, so whichever row the window focused is the one that looked
    selected.

    It was never about that one line. `_group` built its heading, its empty
    "None" and its "and 12 more" count through here too, so **every heading in
    the panel was a Tab stop** - this file's own Diff-panel finding, which was
    *content* reachable by pointer only, is the same trade the other way round:
    here the non-content is focusable and nothing says what pressing Tab will
    land on.

    Selectable is for text somebody might want to copy, so only the questions
    themselves ask for it. A heading, a count and an instruction have nothing to
    copy.
    """
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    label.set_wrap(True)
    if selectable:
        label.set_selectable(True)
    if dim:
        label.add_css_class("dim-label")
    return label


def _group(title: str, items: "tuple[str, ...]", dim: bool = False,
           limit: int = 25) -> Gtk.Widget:
    """One heading and its rows, or a single line saying there are none.

    **The empty case says so.** A heading with nothing under it is
    indistinguishable from a section that failed to build, and in a comparison
    panel "no questions only in the later one" is a real and useful answer.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_margin_top(10)
    heading = _row(f"{title} ({len(items)})")
    heading.add_css_class("heading")
    box.append(heading)
    if not items:
        box.append(_row("None", dim=True))
        return box
    for text in items[:limit]:
        box.append(_row("• " + diff_module.for_display(text), dim,
                        selectable=True))
    if len(items) > limit:
        # Counted, not hidden: a capped list that does not say it was capped
        # reads as the whole answer.
        box.append(_row(f"and {len(items) - limit} more", dim=True))
    return box


class _CompareView(Gtk.Box):
    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self._app = app
        self._root = _store_root(app)
        self._rows: "List[tuple[str, str]]" = []
        #: What this panel says about itself, for the sidebar's health dot and
        #: for the row it draws. Built once and read on the way past, so there is
        #: no second count to fall out of date.
        #:
        #: **This panel shipped without one, and the test named it.**
        #: `memory.py`'s build ends with `surface.status =
        #: surface.status_recorder.status`; this one attached `page.view` and
        #: `page.reload` and stopped. The result was the only sidebar row in the
        # app with no dot at all, which
        #: `test_every_panel_says_something_about_itself` reads as *"a panel that
        #: cannot say is not a broken panel, but twenty of them is a flat list."*
        self.status_recorder = common.StatusRecorder()
        #: **The status slot, and the row is drawn through the recorder.** The
        #: first version of this fix called `status_recorder.set(...)`, which
        #: records the word and draws nothing - and
        #: `test_the_dot_agrees_with_the_row_the_panel_shows` said exactly that:
        #: *"status() says 'ok' so the sidebar dot would be 'status-ok', but the
        #: panel renders no such row. Either the row was not written through the
        #: recorder, or the recorder recorded a word nothing was drawn for."*
        #: `recorder.row()` does both in one call, which is the whole reason it
        #: exists instead of twenty hand-written closures.
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                    spacing=0)
        self.append(self._status_slot)
        #: **Two boxes, not one.** The first version put the drop-downs and the
        #: results in the same box and cleared the results by index - removing
        #: `children[2:]` on the assumption that the two selectors are always
        #: first. Reordering the layout by one row would then have deleted a
        #: drop-down and left one that does nothing.
        self._selectors = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=4)
        self._result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=0)
        self.append(self._selectors)
        self.append(self._result_box)
        self._earlier = None
        self._later = None
        self.reload()

    # -- data ---------------------------------------------------------------

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        while box.get_first_child() is not None:
            box.remove(box.get_first_child())

    def reload(self) -> None:
        self._rows = _conversations(self._root) if self._root else []
        self._clear(self._selectors)
        self._clear(self._result_box)
        self._earlier = self._later = None

        # **The row and the dot, made in one call so they cannot disagree.**
        # `_conversations` returns `[]` both for a store with nothing in it and
        # for one that could not be read, so the rows alone cannot tell them
        # apart - and "I could not ask" is not "there is nothing to ask".
        common.clear(self._status_slot)
        if not self._root:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION, "The conversation store could not be "
                "asked", "the panel does not know where Chronoa's conversations "
                "are kept on this machine"))
            self._result_box.append(_row(UNREADABLE))
            return
        # **An empty store that was read is an answer, not a fault.** Amber is
        # for a panel that could not find out; this one could, and the answer is
        # that there is nothing to compare - which is the same convention the
        # other empty stores use. This row was `STATUS_ATTENTION`, so the render
        # showed **red "Needs attention"** for a machine that was working
        # perfectly and had simply not been asked anything yet.
        if not self._rows:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK, "Nothing to compare yet",
                "two conversations are needed before this panel can say "
                "anything; ask something twice and there will be two to "
                "choose from"))
            self._result_box.append(_row(NEED_TWO))
            return
        self._status_slot.append(self.status_recorder.row(
            common.STATUS_OK,
            f"{plural(len(self._rows), 'conversation')} to compare between"))
        if len(self._rows) < 2:
            # One conversation is not a comparison, and saying "pick two" when
            # there is one to pick is an instruction nobody can follow.
            # **Not `ONLY_ONE if self._rows else NEED_TWO`.** The empty case
            # returned above, so `self._rows` is non-empty by the time this line
            # runs and the `else` could never be taken - a branch that reads as
            # a decision and is not one.
            self._result_box.append(_row(ONLY_ONE))
            return

        titles = [title for _sid, title in self._rows]
        earlier = Gtk.DropDown.new_from_strings(titles)
        later = Gtk.DropDown.new_from_strings(titles)
        # **The two most recent, in the useful order.** Index 0 is newest, so
        # `later` is 0 and `earlier` is 1 - which answers "what is new since
        # last time", the reason this panel exists.
        later.set_selected(0)
        earlier.set_selected(1)
        for drop, name in ((earlier, "Earlier conversation"),
                           (later, "Later conversation")):
            drop.update_property([Gtk.AccessibleProperty.LABEL], [name])
            drop.connect("notify::selected", lambda *_a: self._compare())
            self._selectors.append(drop)
        self._earlier, self._later = earlier, later
        self._compare()

    def _compare(self) -> None:
        self._clear(self._result_box)
        if self._earlier is None or self._later is None:
            return
        old_id = self._selected(self._earlier)
        new_id = self._selected(self._later)
        old_messages, old_cut = _messages(self._root, old_id)
        new_messages, new_cut = _messages(self._root, new_id)
        result = diff_module.diff_conversations(old_messages, new_messages)

        self._result_box.append(_row(
            f"Earlier asked {plural(result.old_count, 'question')}; "
            f"later asked {result.new_count}. "
            f"{len(result.shared)} in both."))
        # **Either side, not both.** The sentence says "One of these asked
        # nothing", and the first version fired only when *both* counts were
        # zero - so a conversation consisting of nothing but a system prompt,
        # compared against a real one, reported "Earlier asked 1 question(s);
        # later asked 0" and three groups of "None", with nothing saying that
        # one of them is empty. The count line is true and useless.
        # **`or`, not `and`.** This regressed while the truncation note was
        # being added: the empty-side message was widened from "both" to "either"
        # once already, and the same line was rewritten with `and` in a later
        # patch - putting the original defect back. The sentence says "One of
        # these", so it fires when *one* of them is empty.
        if not result.old_count or not result.new_count:
            self._result_box.append(_row(NO_QUESTIONS))
        if old_cut or new_cut:
            # **Said, not inferred.** "Only in the later" is a claim about what
            # the person has and has not asked, and it is only as good as the
            # window behind it.
            which = "Both" if (old_cut and new_cut) else (
                "The earlier one" if old_cut else "The later one")
            self._result_box.append(_row(
                f"{which} is longer than the window this reads, so its oldest "
                f"questions are not counted here - a question listed as new may "
                f"have been asked earlier and fallen outside the window.",
                dim=True))
        self._result_box.append(_group("Only in the later", result.only_new))
        self._result_box.append(_group("Only in the earlier", result.only_old))
        self._result_box.append(_group("Asked more than once, earlier",
                                       result.old_repeats, dim=True))
        self._result_box.append(_group("Asked more than once, later",
                                       result.new_repeats, dim=True))

    def _selected(self, drop: Gtk.DropDown) -> str:
        """The id at the drop-down's index, clamped.

        Clamped because `set_selected` and the notification can disagree for a
        frame during a relayout, and an out-of-range index would be a
        `IndexError` from inside a `notify` handler.
        """
        index = max(0, min(len(self._rows) - 1, drop.get_selected()))
        return self._rows[index][0]


def build(app: Any) -> Gtk.Widget:
    """Build the compare page for `app`."""
    page, set_content = common.surface(TITLE, SUBTITLE)
    view = _CompareView(app)
    # **In a scroller, like every other panel.** `common.surface` hands back an
    # `Adw.ToolbarView` when libadwaita is there, and a caller that puts its view
    # straight into the content slot gets a panel whose bottom is unreachable at
    # any window height. `test_every_surface_can_scroll` exists for exactly this
    # and was written against `inventory`, which had the same defect; three of
    # the four panels without `common.scrolled` build their own `Gtk
    # .ScrolledWindow` instead, and this was the one with none at all.
    #
    # It was pre-existing, not introduced by the status row - the sweep that
    # found it ran against the commit before the status slot existed.
    set_content(common.scrolled(view))
    # Re-read on every showing: the window builds a panel once and keeps it, so
    # a load at build time alone froze the store listing at the moment the
    # panel was first opened.
    if hasattr(page, "connect") and common.adw_ready():
        page.connect("showing", lambda *_a: view.reload())
    page.view = view
    page.reload = view.reload
    # **And what it says about itself.** Missing this is what left the sidebar
    # row with no dot. `memory.py`'s build ends with the same line; this is it.
    page.status = view.status_recorder.status
    return page