"""The user's calendar, read through the code that already reads it.

There is one calendar reader in Chronoa - `eds_calendar.py`, over the GNOME
image's Evolution Data Server bindings - and two callers: the `calendar_events`
skill (what a person asks out loud) and the `calendar` event trigger (what a rule
watches). This panel is a third caller of that same reader, not a new one: it
calls `eds_calendar.events_between()` for the window `skills/calendar_events.
span()` already defines, and it formats each row from the same `Event` namedtuple
the skill's `describe()` reads. A panel with its own reader would be a second
answer to "what is on", free to disagree with the first.

**Five states, and a panel that shows one of them always.**

- **Consent off.** `calendar-read-enabled` is off, so nothing was read - not a
  timed-out call, not an empty list. Who you meet, and where, is the user's; the
  skill refuses the same way for the same key. The key being *unreadable* (no
  `config` on the object handed in, a settings read that raised) is worded
  apart from "off": both refuse to read, and only one of them is a decision the
  user made.
- **No calendar backend on this machine.** `eds_calendar` raises
  `CalendarUnavailable(UNAVAILABLE_NO_BINDINGS)`. The GNOME image ships EDS; the
  Plasma image ships Akonadi, which this reader does not speak, so there the
  answer is "no calendar service here", said in those words and never drawn as
  "nothing on".
- **The backend is here and could not be read.** The registry did not answer, an
  account's client refused, a `GLib.Error` came back. Same refusal to call it
  empty, with the backend's own words attached.
- **Nothing on.** Every enabled calendar was read across the whole window and
  holds no occurrence in it. Only reachable after a read that succeeded.
- **The rows.** One per occurrence, grouped by day: today, then tomorrow, then
  the days after, each row carrying the clock time, the summary, how long until
  it starts, the location if there is one, and the calendar it came from.

Recurring events arrive already expanded (`generate_instances` inside
`events_between`), so "the standup every weekday" is a row on the day it happens.
An occurrence already under way is kept and said to be under way rather than
filtered out, and an occurrence whose timestamps cannot be read keeps its row
with the time marked unreadable - dropping it would turn a parse problem into a
missing meeting.

**One seam, deliberately.** `build()` reads `app.config` for the consent key and
the wall clock from `time.time()`, and reads events through the module attribute
`eds_calendar.events_between` - so a test patches the name production reads, in
the module that owns it, rather than a copy this module happens to hold.

The page is `surfaces/common.py`'s: `common.surface()` for the page and toolbar,
`common.group()`/`common.row()` for the groups and rows, `common.empty_state()`
for each honest non-list state, all inside a `common.scrolled()`. Event titles
are untrusted text out of someone else's calendar, so they are escaped before
they reach an `Adw.ActionRow` - whose `use-markup` defaults to true, where an
unescaped `<` renders an *empty* label rather than a wrong one.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import eds_calendar, markdown_lite  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.skills import calendar_events as skill_calendar  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Calendar"
ICON = "office-calendar-symbolic"
SECTION = "This machine"

SUBTITLE = (
    "What is on your calendars, read from the same Evolution Data Server "
    "sources the calendar_events skill and the calendar trigger read. "
    "Read-only: nothing here changes an event, and nothing is read while the "
    "consent key below is off."
)

#: The skill's own consent key, repeated because a panel must not fail open if
#: the skill is renamed. `tests/test_surface_calendar.py` asserts this equals
#: `skills.calendar_events._CONSENT_KEY` and that the key is in the gschema, so
#: a drift here is a failing test rather than a panel quietly reading (or
#: refusing) on a key nothing else uses.
CONSENT_KEY = "calendar-read-enabled"

#: A *separate* key, and the reason this page says so: `calendar-sense-enabled`
#: governs whether a `calendar` event rule may act, which is not what a
#: read-only list does. Naming one key and implying it covers both would be the
#: confident wrong answer.
TRIGGER_KEY = "calendar-sense-enabled"

#: The window, named in the skill's own vocabulary so the panel and the spoken
#: answer describe the same span. `span()` resolves it to (now, midnight+7d).
WINDOW = "week"

#: Markers a test can find in the built tree, so no assertion has to read a
#: count back out of the object being counted.
ROW_CSS = "calendar-event-row"
GROUP_CSS = "calendar-day-group"
STATE_CSS = "calendar-state"

STATE_EVENTS = "events"
STATE_EMPTY = "empty"
STATE_NO_BACKEND = "no-backend"
STATE_UNREADABLE = "unreadable"
STATE_CONSENT = "consent-off"

CONSENT_TITLE = "Chronoa is not reading your calendar"
CONSENT_DETAIL = (
    f"'{CONSENT_KEY}' is off, so nothing was read here. Who you meet, and "
    "where, is yours to keep or share; turn the key on in Settings and this "
    "page fills itself."
)
CONSENT_UNKNOWN_DETAIL = (
    f"The '{CONSENT_KEY}' switch could not be read, so nothing was read here. "
    "That is not the same as it being off - the panel refuses rather than "
    "guessing which it was."
)

NO_BACKEND_TITLE = "This desktop has no calendar service for Chronoa to read"
NO_BACKEND_DETAIL = (
    "Chronoa reads calendars through Evolution Data Server, which the GNOME "
    "image ships. Plasma has none: it keeps calendars in Akonadi, which this "
    "reader does not speak, so on a Plasma install there is no backend here to "
    "ask. That is not the same as having nothing on - nothing was asked."
)

UNREADABLE_TITLE = "The calendar service is here but could not be read"
UNREADABLE_DETAIL = (
    "The calendar service answered with an error, so this page cannot tell you "
    "whether you have anything on. An unreadable calendar is never drawn as an "
    "empty one. It said: {reason}"
)

#: "Nothing on in the next 7 days" - the span's own label already carries the
#: "in the ", so the title must not add a second one.
EMPTY_TITLE = "Nothing on {label}"
EMPTY_DETAIL = (
    "Every enabled calendar was read across the whole window and none of them "
    "holds an occurrence in it. A calendar that could not be read is reported "
    "as unreadable, never as this."
)

FOOTER = (
    f"Read-only. The 'calendar' trigger is a separate permission: a rule that "
    f"acts on an event starting needs '{TRIGGER_KEY}' as well as "
    f"'{CONSENT_KEY}'."
)

UNDATED = "Undated"


# ---------------------------------------------------------------------------
# Reading, without raising
# ---------------------------------------------------------------------------


def _number(value: Any) -> Optional[float]:
    """A timestamp as a float, or None when it is not one."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now(app: Any) -> float:
    """The wall clock, or `app.calendar_now` when a caller supplies one.

    `now` is a parameter everywhere else in this package's read paths
    (`events_between(start, end)`, `describe(event, now)`); `build(app)` has a
    fixed signature, so the override lives on the app object rather than being
    reached for by patching `time`.
    """
    given = getattr(app, "calendar_now", None)
    if isinstance(given, (int, float)) and not isinstance(given, bool):
        return float(given)
    return time.time()


def _permitted(app: Any) -> Tuple[bool, str]:
    """`(may read, detail)` for the consent key.

    Fails toward *not* reading: an app object with no readable config is
    treated as refusing, which is the only direction that cannot leak someone's
    calendar into a window that was never given permission.
    """
    getter = getattr(getattr(app, "config", None), "get_bool", None)
    if not callable(getter):
        return False, CONSENT_UNKNOWN_DETAIL
    try:
        on = bool(getter(CONSENT_KEY, False))
    except Exception:  # noqa: BLE001 - an unreadable key is unknown, not off
        logger.debug("could not read %s", CONSENT_KEY, exc_info=True)
        return False, CONSENT_UNKNOWN_DETAIL
    return (True, "") if on else (False, CONSENT_DETAIL)


def _window(now: float) -> Tuple[float, float, str]:
    """The span to read, from the skill's own `span()`.

    Delegating is the point: the skill answers "what's on this week" with this
    arithmetic, so a panel that computed its own midnight would be free to show
    a different week than the spoken answer for the same words.
    """
    return skill_calendar.span(WINDOW, 0, now)


def _read(now: float) -> Tuple[Optional[List[Any]], str, str]:
    """`(events, state, detail)` - exactly one of the two last fields is set.

    `detail` is the honest message for a state; for `STATE_EVENTS` it is empty
    and the events are the answer.
    """
    try:
        start, end, label = _window(now)
    except Exception as exc:  # noqa: BLE001 - a window that cannot be worked out is unreadable
        logger.debug("could not work out the calendar window", exc_info=True)
        return None, STATE_UNREADABLE, UNREADABLE_DETAIL.format(reason=str(exc) or repr(exc))
    try:
        found = eds_calendar.events_between(start, end)
    except eds_calendar.CalendarUnavailable as exc:
        reason = str(exc) or "no reason given"
        # The module raises its own constant for "the bindings are not here" and
        # a formatted sentence for every other failure, so equality against that
        # constant is what separates "no backend on this desktop" from "the
        # backend here would not answer". Collapsing the two would tell a Plasma
        # user their calendar is empty.
        if reason == eds_calendar.UNAVAILABLE_NO_BINDINGS:
            return None, STATE_NO_BACKEND, NO_BACKEND_DETAIL
        return None, STATE_UNREADABLE, UNREADABLE_DETAIL.format(reason=reason)
    except Exception as exc:  # noqa: BLE001 - any other failure is unreadable, never empty
        logger.debug("reading the calendar raised", exc_info=True)
        return None, STATE_UNREADABLE, UNREADABLE_DETAIL.format(reason=str(exc) or repr(exc))

    try:
        occurrences = list(found)
    except TypeError:
        return None, STATE_UNREADABLE, UNREADABLE_DETAIL.format(
            reason=f"the reader returned {type(found).__name__} rather than a list of events")
    # An occurrence already finished is not upcoming; one under way is kept and
    # says so. An occurrence with no readable end is kept too - a row is lost
    # information, and losing it silently would be a missing meeting.
    upcoming = [event for event in occurrences
                if _number(getattr(event, "end", None)) is None
                or float(event.end) > now]
    if not upcoming:
        return None, STATE_EMPTY, EMPTY_DETAIL.format(label=label)
    return upcoming, STATE_EVENTS, ""


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def _plain(text: Any) -> str:
    """`text` as an Adw row's title or subtitle, escaped if that row is one.

    See the module docstring: `Adw.PreferencesRow.use-markup` defaults to True
    (measured on libadwaita 1.5), so an unescaped `&` or `<` from a calendar
    leaves the label empty and silent. The plain-GTK answer from `common.row()`
    is a `Gtk.Label`, which takes no markup and would print the entities
    themselves, so the escape follows whichever branch built the row.
    """
    flat = str(text)
    return markdown_lite.escape(flat) if common.adw_ready() else flat


def _countdown(seconds: float) -> str:
    """How long until something, in the largest unit that still reads."""
    value = max(0.0, seconds)
    if value < 60:
        return f"in {int(value)} sec"
    if value < 3600:
        minutes = int(value // 60)
        return "in 1 min" if minutes == 1 else f"in {minutes} min"
    if value < 86400:
        hours = int(round(value / 3600))
        return "in 1 hour" if hours == 1 else f"in {hours} hours"
    days = int(value // 86400)
    return "in 1 day" if days == 1 else f"in {days} days"


def _when(event: Any, now: float) -> str:
    """The clock time and the countdown, the two things every row answers."""
    start = _number(getattr(event, "start", None))
    if start is None:
        return "start time unreadable"
    if start <= now:
        return "under way now"
    if getattr(event, "all_day", False):
        return "all day · " + _countdown(start - now)
    try:
        clock = time.strftime("%H:%M", time.localtime(start))
    except (ValueError, OverflowError, OSError):
        clock = "time unreadable"
    return f"{clock} · {_countdown(start - now)}"


def _detail(event: Any, now: float) -> str:
    """The subtitle: when, where, and which calendar it came from.

    "all day" is said once, by `_when()`, next to the countdown that goes with
    it - repeating it at the end of the line is the kind of small thing that
    makes a row look machine-written.
    """
    bits = [_when(event, now)]
    location = str(getattr(event, "location", "") or "").strip()
    if location:
        bits.append(f"@ {location}")
    calendar = str(getattr(event, "calendar", "") or "").strip()
    if calendar:
        bits.append(calendar)
    return " · ".join(bits)


def _tooltip(event: Any, now: float) -> str:
    """The whole occurrence, in the reader's own words.

    `eds_calendar.describe()` is what `calendar_events` answers with, so the
    tooltip is the same sentence a person would get if they asked out loud -
    including the calendar's name and location, which the row may not have room
    for.
    """
    try:
        described = eds_calendar.describe(event, now)
    except Exception:  # noqa: BLE001 - a tooltip must never be the thing that raises
        described = str(getattr(event, "summary", "") or "(no title)")
    return described


def _local_date(timestamp: float) -> Optional[date]:
    """The local calendar date of a timestamp, or None if it has none."""
    try:
        return datetime.fromtimestamp(timestamp).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _day_label(day: Optional[date], now: float) -> str:
    """`Today`, `Tomorrow`, or the date - relative to the day it is read."""
    if day is None:
        return UNDATED
    today = _local_date(now)
    if today is not None:
        if day == today:
            return "Today"
        try:
            if day == today + timedelta(days=1):
                return "Tomorrow"
        except (OverflowError, ValueError):  # pragma: no cover - only at the date range's edge
            pass
    try:
        return day.strftime("%a %d %b")
    except ValueError:  # pragma: no cover - a real date always formats
        return str(day)


def _grouped(events: List[Any], now: float) -> "List[Tuple[str, List[Any]]]":
    """The events by day, days in order, today's first.

    Keyed on the local *date* rather than on `start // 86400`, because that
    division puts 23:00 on the wrong day twice a year and "today" would then be
    a lie on the day it is read.
    """
    buckets: "dict[Optional[date], List[Any]]" = {}
    for event in events:
        start = _number(getattr(event, "start", None))
        buckets.setdefault(_local_date(start) if start is not None else None, []).append(event)
    out: "List[Tuple[str, List[Any]]]" = []
    for day in sorted(buckets, key=lambda d: (d is None, d or date.min)):
        out.append((_day_label(day, now), buckets[day]))
    return out


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    """Put a row in a group, whichever kind `common.group()` built.

    `Adw.PreferencesGroup` takes rows through `add()`; the plain-GTK answer is a
    `Gtk.Box`, whose rows are appended. Duck-typed, so this module does not
    require libadwaita itself - `common` treats it as optional and so must
    everything built on it.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(child)
    else:
        group.append(child)


def _note(text: str) -> Gtk.Label:
    """A dim line of plain text. `set_text` takes no markup at all, which is the
    one call that cannot be half-done for the messages this panel writes."""
    label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
    label.set_text(text)
    label.add_css_class("dim-label")
    return label


def _margined(widget: Gtk.Widget) -> Gtk.Widget:
    """The margins every panel in this package puts around its content."""
    widget.set_margin_top(12)
    widget.set_margin_bottom(12)
    widget.set_margin_start(12)
    widget.set_margin_end(12)
    return widget


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


class _CalendarSurface:
    """The page's logic, and the page it fills.

    Reads once, in `refresh()`, and publishes its state: which of the five
    answers this page is showing, the rows it built, and the events behind them.
    A panel that raises takes the window with it, so every read is wrapped and a
    failure becomes the unreadable state rather than a traceback on open.
    """

    def __init__(self, app: Any, set_content: Any) -> None:
        self._app = app
        self._rows: List[Gtk.Widget] = []
        self._groups: List[Gtk.Widget] = []
        self._events: List[Any] = []
        self._state = ""
        self._message = ""
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        set_content(self._content)
        # The panel's own health, before any state it may show:
        # is the calendar readable, is it granted, or could it
        # not be told? One row, one dot, one word - the question
        # the panel is opened for, before the rows that hold the
        # events.
        #: The panel's health, written once into the row above and read back
        #: for the dot on its sidebar row. Same value, so the dot cannot
        #: disagree with the sentence directly above it.
        self.status_recorder = common.StatusRecorder()
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._content.append(self._status_slot)
        self.refresh()

    # -- content ------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the calendar and rebuild the page."""
        # Everything below the status row is rebuilt; the status row
        # itself is replaced, not appended to, so a refresh cannot
        # stack a second one.
        child = self._content.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._content.remove(child)
            child = following
        self._content.append(self._status_slot)
        common.clear(self._status_slot)
        self._rows = []
        self._groups = []
        self._events = []
        self._state = ""
        self._message = ""

        now = _now(self._app)
        allowed, detail = _permitted(self._app)
        if not allowed:
            self._state = STATE_CONSENT
            self._message = detail
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                "Calendar is not granted",
                detail))
            self._show_state(CONSENT_TITLE, detail)
            return

        try:
            events, state, detail = _read(now)
        except Exception as exc:  # noqa: BLE001 - the panel's own last line of defence
            logger.warning("the calendar panel could not read the calendar", exc_info=True)
            events, state, detail = None, STATE_UNREADABLE, UNREADABLE_DETAIL.format(
                reason=str(exc) or repr(exc))
        self._state = state
        self._message = detail

        if state == STATE_NO_BACKEND:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                "No calendar backend",
                detail))
            self._show_state(NO_BACKEND_TITLE, detail)
            return
        if state == STATE_UNREADABLE:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                "Calendar could not be read",
                detail))
            self._show_state(UNREADABLE_TITLE, detail)
            return
        if state == STATE_EMPTY:
            try:
                label = _window(now)[2]
            except Exception:  # noqa: BLE001 - already reported, do not raise here
                label = "window"
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                "No events in range",
                detail))
            self._show_state(EMPTY_TITLE.format(label=label), detail)
            return

        self._events = list(events or [])
        self._status_slot.append(self.status_recorder.row(
            common.STATUS_OK,
            f"{len(self._events)} event(s) in range",
            detail))
        self._show_events(self._events, now)

    def _show_events(self, events: List[Any], now: float) -> None:
        body = _margined(common.page_body())
        for label, day_events in _grouped(events, now):
            group = common.group(label, f"{len(day_events)} event(s)")
            group.add_css_class(GROUP_CSS)
            for event in day_events:
                row = self._event_row(event, now)
                _add(group, row)
                self._rows.append(row)
            self._groups.append(group)
            body.append(group)
        body.append(_note(FOOTER))
        self._content.append(common.scrolled(body))

    def _event_row(self, event: Any, now: float) -> Gtk.Widget:
        summary = str(getattr(event, "summary", "") or "(no title)")
        row = common.row(_plain(summary), _plain(_detail(event, now)))
        row.add_css_class(ROW_CSS)
        row.set_tooltip_text(_plain(_tooltip(event, now)))
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"Calendar event {summary}"])
        return row

    def _show_state(self, title: str, detail: str) -> None:
        """One of the four honest non-list states, plus the footer.

        A status page rather than an empty group: an empty list with no
        explanation reads as a bug, and each of these has a reason that can be
        said exactly.

        **The consent state carries a button, because it is the one state a
        person can undo.** Its detail used to end "turn the key on in Settings" -
        naming the switch and providing no way to reach it, which is the dead end
        this panel is the answer to. The other three need a package installed, a
        service running or permission from the desktop, and none of those is a
        page in this app, so they keep their prose rather than growing a button
        that would open Settings and change nothing.
        """
        if self._state == STATE_CONSENT:
            self._content.append(common.banner(
                "Reading the calendar is a switch on this machine.",
                "Open Senses settings",
                lambda: common.open_page(self._app, "settings:senses")))
        widget = common.empty_state(ICON if self._state != STATE_UNREADABLE
                                    else "dialog-warning-symbolic",
                                    _plain(title), _plain(detail))
        widget.add_css_class(STATE_CSS)
        widget.set_vexpand(True)
        self._content.append(widget)
        self._content.append(_margined(_note(FOOTER)))

    @staticmethod
    def _empty(container: Gtk.Widget) -> None:
        child = container.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            container.remove(child)
            child = following

    # -- for tests, and for any surface that wants the same reading ----------

    def rows(self) -> List[Gtk.Widget]:
        return list(self._rows)

    def row_count(self) -> int:
        return len(self._rows)

    def groups(self) -> List[Gtk.Widget]:
        return list(self._groups)

    def events(self) -> List[Any]:
        return list(self._events)

    def state(self) -> str:
        return self._state

    def message(self) -> str:
        return self._message


def build(app: Any) -> Gtk.Widget:
    """Build the calendar page for `app`.

    Returns the `Adw.NavigationPage` a window pushes into its navigation view,
    with this surface's read-only API attached. Reads the calendar, writes
    nothing.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _CalendarSurface(app, set_content)
    page.surface = surface
    page.rows = surface.rows
    page.row_count = surface.row_count
    page.groups = surface.groups
    page.events = surface.events
    page.state = surface.state
    page.message = surface.message
    # What this panel says about itself, for the sidebar's health dot. Read from
    # the same recorder the row at the top of the panel was written through, so
    # the dot and the row are one statement rather than two that can drift.
    page.status = surface.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "SECTION", "CONSENT_KEY", "build"]
