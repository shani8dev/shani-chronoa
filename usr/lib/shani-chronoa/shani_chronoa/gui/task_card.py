"""The task card: the plan, in the chat, where it cannot scroll away.

OpenHands renders `TaskItem {title, notes, status: todo | in_progress | done}`
as a card inside the conversation (`conversation-events/chat/task-tracking/`)
and cline has its own todo row. Chronoa had a `todo_list` skill and a rail
section - both of which are one scroll - and nothing in the conversation, which
is where a person is actually looking.

Three states and a blocked mark, because a list that cannot say "this one is
waiting on something else" is a list that gets silently wrong. Read-only
*here*: the card shows what the store holds and the assistant changes it through
the skill. A card with its own edit buttons would be a second writer for one
store, and two writers is how a plan starts disagreeing with itself.

Nothing is shown when the task list is off or empty. An empty card is a widget
that costs a person's attention every turn to say nothing - the rail's Tasks
section already answers "is there anything outstanding".
"""

from __future__ import annotations

import logging
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402 - after require_version

logger = logging.getLogger(__name__)

#: `(status, icon, label)` per state. The icons are the same three-state shapes
#: OpenHands uses (`u-circle`, `u-check-circle-half`, `u-check-circle`); the
#: names here are the GTK equivalents, each checked with
#: `Gtk.IconTheme.has_icon` before it is used, because a missing glyph renders
#: as an empty box and reads as a rendering bug.
_STATES = {
    #: A plan plan mode has made and you have not approved. **Its own state,
    #: not `pending`** - a plan waiting for a person and a todo waiting to be
    #: done are different things, and drawing them the same way is how a card
    #: says "in hand" about something nobody has agreed to.
    "awaiting": ("dialog-question-symbolic", "waiting for you"),
    "pending": ("radio-symbolic", ""),
    "in_progress": ("content-loading-symbolic", "doing"),
    "blocked": ("dialog-warning-symbolic", "blocked"),
    "completed": ("object-select-symbolic", "done"),
}

_FALLBACK_ICON = "dialog-warning-symbolic"


def _icon_name(name: str) -> str:
    """The icon, or a fallback when this theme does not have it."""
    try:
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is not None and Gtk.IconTheme.get_for_display(display).has_icon(name):
            return name
    except Exception:  # noqa: BLE001 - a missing icon must not break the card
        pass
    return _FALLBACK_ICON


class TaskCard(Gtk.Box):
    """The outstanding tasks, newest state first, with a heading.

    `refresh()` re-reads the store and hides itself entirely when there is
    nothing to say - see the module docstring for why "empty but present" is
    worse than absent.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("task-card")
        self.set_margin_top(8)
        self._title = Gtk.Label(label="Tasks")
        self._title.add_css_class("heading")
        self._title.set_xalign(0.0)
        self.append(self._title)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.append(self._list)
        self._empty = Gtk.Label(label="Nothing outstanding")
        self._empty.add_css_class("dim-label")
        self._empty.set_xalign(0.0)
        self.append(self._empty)
        self.refresh()

    def refresh(self) -> bool:
        """Re-read the store. Returns whether anything is outstanding."""
        for child in list(self._rows()):
            self._list.remove(child)
        try:
            items = _outstanding()
        except Exception as exc:  # noqa: BLE001 - a card must not break the chat
            logger.debug("task card could not read the store: %s", exc)
            self.set_visible(False)
            return False
        if not items:
            self.set_visible(False)
            return False
        for item in items[:8]:
            self._list.append(self._row(item))
        if len(items) > 8:
            more = Gtk.Label(label=f"and {len(items) - 8} more")
            more.add_css_class("dim-label")
            more.set_xalign(0.0)
            self._list.append(more)
        self._empty.set_visible(False)
        self.set_visible(True)
        return True

    def _rows(self) -> "list[Gtk.Widget]":
        out, child = [], self._list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            out.append(child)
            child = following
        return out

    def _row(self, item: dict) -> Gtk.Widget:
        status = str(item.get("status") or "pending")
        icon_name, prefix = _STATES.get(status, _STATES["pending"])
        icon = Gtk.Image.new_from_icon_name(_icon_name(icon_name))
        icon.set_pixel_size(14)
        label = Gtk.Label(label=f"{prefix + ' ' if prefix else ''}"
                                 f"{item.get('content', '')}".strip())
        label.set_xalign(0.0)
        label.set_wrap(True)
        label.set_hexpand(True)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        head.append(icon)
        head.append(label)
        blocked = item.get("blocked_by") or []
        if not blocked:
            return head
        label.set_tooltip_text("waiting on " + ", ".join(str(b) for b in blocked))
        label.add_css_class("dim-label")
        # **The reason is on the row, not only in the tooltip.** `blocked_by`
        # exists to say *what* is in the way, and a card rendering only
        # "blocked check the backup" throws that away: a tooltip needs a hover
        # (or a keyboard focus), never appears on touch, and is absent from every
        # screenshot. Found by rendering the card — the earlier version looked
        # correct while saying nothing about the disk being full.
        #
        # **The icon stays in the head box, and that is not cosmetic.** A first
        # attempt returned a fresh vertical box holding the two labels and left
        # the icon behind, which the next render showed immediately: "blocked"
        # had lost the one marker that distinguishes it from "doing" without
        # colour. The head goes *inside* the row rather than being replaced by it.
        reason = Gtk.Label(label="waiting on " + ", ".join(str(b) for b in blocked))
        reason.add_css_class("dim-label")
        reason.set_xalign(0.0)
        reason.set_wrap(True)
        reason.set_hexpand(True)
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        row.append(head)
        row.append(reason)
        return row


def _outstanding() -> "list[dict]":
    """What is outstanding, or `[]` when the list is off or unreadable.

    The consent gate is the *skill's own*, consulted here rather than assumed:
    a list the user switched off is not shown in the chat either, and "the
    store is unreadable" is not shown as "you have no tasks".
    """
    # **Plans are read first, and are not gated by the todo-list setting.**
    # They used to be read *after* it, which meant a plan plan mode had just
    # stored was invisible in every chat where the person had the task list
    # switched off - its default. Measured by rendering the card: `refresh()`
    # reported "Nothing outstanding" with a plan in the store.
    #
    # The gate is right for todos and wrong for plans: nobody opts into
    # `todo-list-enabled` by *asking the assistant to work out what it would
    # do*. A plan is the answer to a question you asked, so hiding it behind an
    # unrelated setting is hiding the answer.
    plans = _plans_waiting()

    from shani_chronoa import config as config_mod
    if not config_mod.ChronoaConfig().get_bool("todo-list-enabled", False):
        return plans
    from shani_chronoa.skills import todo_list
    items, problem = todo_list.open_tasks()
    if problem or items is None:
        logger.info("task card: %s", problem or "the task list could not be read")
        return plans
    outstanding = [item for item in items if item.get("status") != "completed"]
    outstanding.extend(plans)
    return outstanding


def _what_it_would_do(steps) -> str:
    """The plan's tools in order, duplicates collapsed - beside the count.

    **A count is not a plan.** "3 steps, nothing has been done" is the sentence
    for a run whose shape you cannot see, and approving it means saying yes to
    three things nobody was shown. A model that asks for the same write five
    times has still described **one** write, so the names collapse - while the
    count stays the number of *steps*, so a plan that writes five files says
    five and never five copies of one line.

    **The dedup used to live in `planmode.refused_tools()`**, and that function
    read the wrong thing: the **live** refusal list, which is this turn's, while
    a card is handed a stored run whose steps may be from any earlier turn. Two
    answers to "what would this do" is one too many, and the card is holding the
    run - so the helper sits here and reads what the card actually has.
    """
    steps = list(steps or ())
    tools: "list[str]" = []
    for step in steps:
        name = getattr(step, "tool_name", "") or ""
        if name and name not in tools:
            tools.append(name)
    if not tools:
        return f"{len(steps)} step" + ("" if len(steps) == 1 else "s")
    shown = ", ".join(tools[:3])
    if len(tools) > 3:
        shown += f" and {len(tools) - 3} more"
    return (f"{len(steps)} step" + ("" if len(steps) == 1 else "s")
            + ": " + shown)


def _plans_waiting() -> "list[dict]":
    """Plans plan mode made, in the shape `_row` already draws.

    **Without this the plan is invisible.** Plan mode asks, you say "keep
    planning", and the refusals become a run in the goals store - which the
    Goals panel and the rail can show, and neither of which is where a person
    is looking while the assistant is still talking. A plan you cannot see is
    a plan you cannot correct before it runs.

    **The same dict shape as a todo**, so one `_row` draws both, and the state
    is `awaiting` so it reads as *waiting for you* rather than *to do*.
    """
    try:
        from shani_chronoa import goals as goals_module

        store = goals_module.GoalStore(goals_module.store_root())
        runs = store.list()
    except Exception as exc:  # noqa: BLE001 - a plan that cannot be read is not
        logger.debug("task card: the goal store is unreadable: %s", exc)
        return []

    out = []
    for run in runs:
        # **The phase decides, and the reason only identifies which kind of
        # waiting it is.** The first version checked the reason alone, and
        # `awaiting_reason` is a *label* that stays put when the run moves on -
        # so a plan already approved and running still had it, and the card
        # went on asking you to approve work that was already happening.
        if run.phase is not goals_module.Phase.AWAITING:
            continue
        if run.awaiting_reason != goals_module.PLAN_AWAITING_REASON:
            continue
        out.append({
            "status": "awaiting",
            "content": f"{run.goal} - {_what_it_would_do(run.steps)}"
                       + ", nothing has been done",
            "blocked_by": [],
        })
    return out
