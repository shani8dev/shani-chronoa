"""Today: everything with a date or a due time, on one surface.

The five stores were chat-only. A reminder, an alarm, a timer, a note and a
to-do each lived behind a phrase you had to remember ("set a timer", "remind
me", "add a note", "add a todo", "set an alarm"), and the only other place a
timer appeared was the strip above the composer. All five are a question about
*today* - what is due, what will ring, what is still counting down, what I wrote
down, what I still have to do - so they belong on one page, read together.

**Read-only apart from the two timer buttons.** Cancel and +5 minutes are the
two controls the rail already offers (`gui/timers.py`), and they call the
timer skill's own functions rather than a private copy of the store. The other
four stores are shown as Chronoa recorded them; changing them is a chat turn,
because each write has its own consent gate and a row here must not be a side
door around it. A row that needs a change says what to ask, and a "Try it"
button fills the composer rather than sending - the same shape the capability
page uses for a skill that writes.

**An empty store is an answer, an unreadable store is not.** The two are kept
apart per group: "nothing pending" is `STATUS_OK`, a store that could not be
read is `STATUS_UNKNOWN` with the reason. A page that said "nothing" for a
store it never opened would be the confident wrong answer this repository keeps
filing.

**Paths stay out of the prose.** Each group names its store by what it holds,
not where the JSON lives; the full path is the row's tooltip.
"""

from __future__ import annotations
from shani_chronoa.gui.locale import _

import logging
from typing import Any, Callable, List, Tuple

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.gui import timers as timer_ui  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = _('Today')

ICON = "document-open-recent-symbolic"
SECTION = "Remembering"
SUBTITLE = _('Reminders, alarms, timers, notes and to-dos on one page - what is due or waiting, read from the same stores the assistant uses. Only a timer can be changed here; the rest are started by asking.')


#: group title -> (one-line description, "ask" prompt to fill the composer)
_GROUPS = (
    (
        "Timers",
        "Counting down now, soonest first; cancel or add five minutes.",
        "Set a timer",
    ),
    (
        "Reminders",
        "Things Chronoa was asked to remember, with their due times.",
        "Remind me",
    ),
    ("Alarms", "Alarms still to ring, in the order they will fire.", "Set an alarm"),
    ("Notes", "Short notes kept for later, oldest first.", "Take a note"),
    ("To-dos", "The task list, outstanding items first.", "Add a to-do"),
)


def _clipped(value: Any, limit: int = 90) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _timers() -> Tuple[List[timer_ui.Pending], str]:
    try:
        return timer_ui.pending_timers(), ""
    except Exception as exc:  # noqa: BLE001 - a glance must not take the page down
        return [], f"the timer store could not be read ({exc})"


def _reminders() -> Tuple[List[Tuple[str, str]], str]:
    """Pending reminders as (text, due), newest first."""
    try:
        from shani_chronoa.skills import reminders

        entries = reminders._entries()
    except Exception as exc:  # noqa: BLE001
        return [], f"the reminder store could not be read ({exc})"
    out = []
    for entry in reversed(entries):
        if not isinstance(entry, dict) or entry.get("done"):
            continue
        text = _clipped(entry.get("text") or entry.get("body"))
        due = _clipped(entry.get("due")) if entry.get("due") else "no due time"
        if text:
            out.append((text, due))
    return out, ""


def _alarms() -> Tuple[List[str], str]:
    try:
        from shani_chronoa.skills import alarm

        found = alarm._load()
    except Exception as exc:  # noqa: BLE001
        return [], f"the alarm store could not be read ({exc})"
    return [_clipped(alarm._summary(a, __import__("time").time())) for a in found], ""


def _notes() -> Tuple[List[str], str]:
    try:
        from shani_chronoa.skills import notes

        found = notes._load()
    except Exception as exc:  # noqa: BLE001
        return [], f"the note store could not be read ({exc})"
    return [_clipped(n.get("text")) for n in found if isinstance(n, dict)], ""


def _todos() -> Tuple[List[str], str]:
    try:
        from shani_chronoa.skills import todo_list

        found, problem = todo_list.open_tasks()
    except Exception as exc:  # noqa: BLE001
        return [], f"the task list could not be read ({exc})"
    if found is None:
        return [], problem or "the task list could not be read"
    pending = [i for i in found if isinstance(i, dict) and not i.get("completed")]
    return [_clipped(i.get("text") or i.get("title") or i) for i in pending], ""


_READERS = {
    "Timers": _timers,
    "Reminders": _reminders,
    "Alarms": _alarms,
    "Notes": _notes,
    "To-dos": _todos,
}


def _timer_button(icon: str, tooltip: str, run: Callable[[], None]) -> Gtk.Button:
    button = Gtk.Button(icon_name=icon)
    button.add_css_class("flat")
    button.add_css_class("circular")
    button.set_valign(Gtk.Align.CENTER)
    button.set_tooltip_text(tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    button.connect("clicked", lambda _b, r=run: r())
    return button


def _try_button(window: Any, prompt: str) -> Gtk.Button:
    """Fill the composer; never send. A write still needs a chat turn."""
    button = Gtk.Button(label="Try it")
    button.add_css_class("flat")
    button.set_valign(Gtk.Align.CENTER)
    tip = f"Put “{prompt}” in the composer to ask Chronoa"
    button.set_tooltip_text(tip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [tip])

    def clicked(_button: Gtk.Button) -> None:
        try:
            window.set_input_text(prompt)
        except Exception:  # noqa: BLE001 - a click must not take the window down
            logger.debug("today: could not fill the composer", exc_info=True)

    button.connect("clicked", clicked)
    return button


def _change_timer(identifier: str, label: str, seconds: int) -> None:
    """The timer skill's own writer - the same one the strip beside the composer uses."""
    from shani_chronoa.skills import timer as timer_skill

    try:
        if seconds:
            timer_skill.add_to_timer(seconds, identifier, label)
        else:
            timer_skill.cancel_timer(identifier)
    except Exception:  # noqa: BLE001
        logger.debug("today: timer change failed", exc_info=True)


def _fill(content: Gtk.Box, recorder: common.StatusRecorder, window: Any) -> None:
    child = content.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        content.remove(child)
        child = following

    # Read every store before drawing anything, so the page's one health row is
    # decided by the same reads the groups show - not by a second count. GTK4's
    # Box has no `insert`, so the order is fixed here rather than patched later.
    readings = []
    problems: List[str] = []
    for title, description, prompt in _GROUPS:
        try:
            items, problem = _READERS[title]()
        except Exception as exc:  # noqa: BLE001 - one store cannot take the page down
            items, problem = [], f"{title.lower()} could not be read ({exc})"
        if problem:
            problems.append(problem)
        readings.append((title, description, prompt, items, problem))

    status = common.STATUS_UNKNOWN if problems else common.STATUS_OK
    detail = (
        "  ·  ".join(problems) if problems else "All five stores were read just now."
    )
    content.append(recorder.row(status, "Read just now", detail))

    for title, description, prompt, items, problem in readings:
        group = common.group(title, description)
        if problem:
            adder = getattr(group, "add", None) or group.append
            adder(common.row("Could not read this store", problem))
        elif not items:
            adder = getattr(group, "add", None) or group.append
            adder(common.row("Nothing here yet", f"Ask Chronoa: {prompt}"))

        for item in items:
            row = None
            if title == "Timers":
                identifier, label, left = item
                row = common.row(
                    _clipped(label) or "Timer",
                    f"{timer_ui.human_seconds(left)} left",
                    escape=True,
                )
                for icon, tip, seconds in (
                    ("list-add-symbolic", "Five more minutes", 300),
                    ("window-close-symbolic", "Cancel this timer", 0),
                ):
                    row.add_suffix(
                        _timer_button(
                            icon,
                            tip,
                            lambda i=str(identifier), l=str(label), s=seconds: (
                                _change_timer(i, l, s)
                            ),
                        )
                    )
            elif title == "Reminders":
                text, due = item
                row = common.row(text, f"due: {due}")
            else:
                row = common.row(_clipped(item), "")
            adder = getattr(group, "add", None) or group.append
            adder(row)

        adder = getattr(group, "add", None) or group.append
        adder(
            common.row(
                "",
                "Ask Chronoa to change anything in this group.",
                suffix=_try_button(window, prompt),
            )
        )
        content.append(group)


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    recorder = common.StatusRecorder()
    page.status = recorder.status
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    set_content(common.scrolled(content))
    window = getattr(app, "window", None)
    _fill(content, recorder, window)
    if common.adw_ready() and hasattr(page, "connect"):
        page.connect("showing", lambda *_a: _fill(content, recorder, window))
    return page
