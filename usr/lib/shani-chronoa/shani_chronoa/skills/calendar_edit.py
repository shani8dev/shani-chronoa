"""Skill: add, move or cancel an event on the user's calendar.

A separate skill from `calendar_events`, not more actions on it, because the
two need different answers to "may Chronoa do this?": reading who you meet is
`calendar-read-enabled`, changing it is `calendar-write-enabled`, and
`calendar_events` is listed as read-only (capabilities.READ_ONLY_TOOLS, the MCP
read-only hint). Folding writes into it would make that hint a lie for every
call, including the reads.

Written through the desktop's own calendar service (eds_calendar.py), so the
event lands where GNOME Calendar, Evolution and every GNOME Online Accounts
calendar (Google, Nextcloud, Exchange) see it. Plasma's Akonadi is not shipped;
there the answer is "no calendar service", never a pretend success.

Rules:
- **Fail closed.** `calendar-write-enabled` is off unless the person turned it
  on. Moving or cancelling also reads the calendar to find the event, so it
  needs `calendar-read-enabled` as well.
- **A person confirms every move and cancel**, at the moment it happens
  (approvals.confirm: the in-window question when there is one, otherwise a
  desktop notification with Allow once / Deny). Silence is a no. Adding needs no
  extra question: it destroys nothing, and the reply says exactly what was
  written so it can be cancelled.
- **Ambiguity is refused**, with the candidates listed - two events at 5 PM on
  Friday are not resolved by picking one.
- **Never guess an hour.** "3" alone could be 3 AM or 3 PM: adding refuses it;
  moving takes whichever of the two is nearer the event's current time, because
  "move my 5 PM call to 6" means 6 PM, and the reply prints the absolute time.
- Repeating events are refused: changing one occurrence of a series is a
  question calendar apps ask with a dialog, and guessing "this one" or "all of
  them" is the kind of mistake nobody notices until the meeting is missed.
- Every write is read back (eds_calendar) before it is reported.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from shani_chronoa import approvals
from shani_chronoa import eds_calendar as cal
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.skills.reminders import _clock, _parse_due, _parse_duration, _split_day_clock

_WRITE_KEY = "calendar-write-enabled"
_READ_KEY = "calendar-read-enabled"
_ACTIONS = ("create", "move", "cancel")
_ALIASES = {"add": "create", "new": "create", "schedule": "create", "delete": "cancel",
            "remove": "cancel", "reschedule": "move", "change": "move"}
DEFAULT_DURATION = 3600
MAX_DURATION = 14 * 86400
MAX_CANDIDATES = 6
#: Words that say what kind of thing an event is, not which one. "My 5 PM call"
#: is found by its time; the event may well be titled "Sync with Raj".
_GENERIC = {"my", "the", "a", "an", "call", "meeting", "event", "appointment", "thing", "on",
            "at", "with", "calendar", "slot"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_edit",
        "description": (
            "Change the user's calendar: 'create' adds an event to the default calendar "
            "(title, start, optional duration or end, location); 'move' reschedules an event "
            "found by its current time and/or title to new_start; 'cancel' deletes one. "
            "Times in words: 'tomorrow 3pm', 'friday 5 pm', '2026-10-09 14:00'. Move and cancel "
            "ask the person to confirm. Ambiguous matches are refused with the candidates. "
            "Requires the 'calendar-write-enabled' consent key (move/cancel also "
            "'calendar-read-enabled'). No invitations are sent."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "title": {"type": "string", "description": (
                "create: the event's title, e.g. 'Meeting with John'. move/cancel: words from "
                "the title to find it by (optional when the time is enough).")},
            "start": {"type": "string", "description": (
                "create: when it starts. move/cancel: when the event to change currently "
                "starts, e.g. '5 pm', 'friday 5pm'.")},
            "duration": {"type": "string", "description": "create: how long, e.g. '1 hour', '30 minutes'. Default 1 hour."},
            "end": {"type": "string", "description": "create: end time instead of a duration, e.g. '4pm'."},
            "new_start": {"type": "string", "description": "move: the new start, e.g. '6 pm', '6', 'tomorrow 10am'. Same day unless one is named."},
            "location": {"type": "string", "description": "create: where."},
            "notes": {"type": "string", "description": "create: a description to attach."},
        }, "required": ["action"]},
    },
}


def _config() -> ChronoaConfig:
    return ChronoaConfig()


def _now() -> float:
    return time.time()


def _confirm(title: str, body: str) -> "tuple[bool, str]":
    return approvals.confirm(title, body)


def _text(arguments: dict, key: str, limit: int = 300) -> str:
    value = arguments.get(key)
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()[:limit]


def _fmt(ts: float) -> str:
    return time.strftime("%a %d %b %H:%M", time.localtime(ts))


def _fmt_span(start: float, end: float) -> str:
    same_day = time.localtime(start)[:3] == time.localtime(end)[:3]
    return f"{_fmt(start)}-{time.strftime('%H:%M' if same_day else '%a %d %b %H:%M', time.localtime(end))}"


def _ts(day: date, clock: "tuple[int, int]") -> float:
    return time.mktime((day.year, day.month, day.day, clock[0], clock[1], 0, 0, 0, -1))


def _bare_hour(text: str) -> bool:
    """'3', 'friday 3', 'at 3' - an hour with no am/pm and no minutes, under 12."""
    tail = text.strip().split()[-1] if text.strip() else ""
    return bool(re.fullmatch(r"(?:[1-9]|1[01])", tail))


def _when(text: str, now: float) -> "tuple[float | None, str]":
    """An absolute start for a new event, or a refusal that says why."""
    today = datetime.fromtimestamp(now).date()
    day, clock, understood = _split_day_clock(text, today)
    if understood and clock is not None:
        if _bare_hour(text):
            # The "Nothing was ..." suffix is on every other refusal here and
            # was missing from this one. A refusal that does not say the
            # calendar was left alone reads, to a model relaying it, as a
            # partial success - and this is the one refusal where the guess
            # being avoided is the whole point.
            return None, (f"{text!r} could be AM or PM - say which, e.g. '{text} pm'. "
                          "Nothing was added.")
        if day is None:
            ts = _ts(today, clock)
            if ts <= now:
                ts = _ts(today + timedelta(days=1), clock)
            return ts, ""
        ts = _ts(day, clock)
        if ts <= now:
            return None, f"{_fmt(ts)} has already passed, so nothing was added."
        return ts, ""
    if understood and day is not None:
        return None, f"What time on {day.strftime('%a %d %b')}? Give a start time, e.g. '{text} 3pm'."
    due, problem = _parse_due(text)
    if due is None:
        return None, (problem or f"Could not read {text!r} as a time.").replace(
            "the reminder was written with no due date rather than a guessed one",
            "nothing was added")
    ts = due.timestamp()
    if ts <= now:
        return None, f"{_fmt(ts)} has already passed, so nothing was added."
    return ts, ""


def _find(arguments: dict, now: float) -> "tuple[cal.Event | None, str]":
    """The one event the person means, or a refusal listing what was found."""
    when_text, title = _text(arguments, "start"), _text(arguments, "title")
    words = [w for w in re.findall(r"[\w']+", title.lower()) if w not in _GENERIC]
    if not when_text and not words:
        return None, "Which event? Give its current time (e.g. 'friday 5 pm') or words from its title."
    today = datetime.fromtimestamp(now).date()
    day, clock, understood = _split_day_clock(when_text, today) if when_text else (None, None, True)
    if not understood:
        return None, f"Could not read {when_text!r} as a day and time. Try 'friday 5 pm' or 'tomorrow 9am'."
    if day is not None:
        lo = _ts(day, (0, 0))
        hi = lo + 86400
    else:
        lo, hi = _ts(today, (0, 0)), _ts(today, (0, 0)) + 8 * 86400
    try:
        events = cal.events_between(lo, hi)
    except cal.CalendarUnavailable as exc:
        return None, f"I could not read your calendar to find the event: {exc}. Nothing was changed."
    if day is None:
        events = [e for e in events if e.end > now]  # "my 5 PM call" is the next one, not one that is over
    matched = events
    if clock is not None:
        candidates = {clock}
        if _bare_hour(when_text):  # '5' - either half of the day, the time is the filter
            candidates.add(((clock[0] + 12) % 24, clock[1]))
        matched = [e for e in matched if not e.all_day
                   and (time.localtime(e.start).tm_hour, time.localtime(e.start).tm_min) in candidates]
    if words:
        matched = [e for e in matched if all(w in e.summary.lower() for w in words)]
    if day is None and clock is not None and matched:
        first_day = time.localtime(matched[0].start)[:3]
        matched = [e for e in matched if time.localtime(e.start)[:3] == first_day]
    described = (f"{when_text!r}" if when_text else "that") + (f" titled like {title!r}" if words else "")
    if not matched:
        nearby = "\n".join(f"- {cal.describe(e)}" for e in events[:MAX_CANDIDATES])
        return None, (f"No event matches {described}. Nothing was changed."
                      + (f"\nOn your calendar in that span:\n{nearby}" if nearby else ""))
    if len(matched) > 1:
        listed = "\n".join(f"- {cal.describe(e)}" for e in matched[:MAX_CANDIDATES])
        return None, (f"{len(matched)} events match {described}, so I did not pick one. Nothing was "
                      f"changed. Say which (by time or title):\n{listed}")
    event = matched[0]
    if event.recurring:
        return None, (f"'{event.summary}' ({_fmt(event.start)}) is part of a repeating series. Changing "
                      "one occurrence or the whole series is a choice to make in your calendar app; "
                      "nothing was changed.")
    return event, ""


def _new_start(text: str, event: cal.Event) -> "tuple[float | None, str]":
    """Where a moved event goes. A bare hour takes the AM/PM nearer the old time."""
    old_day = datetime.fromtimestamp(event.start).date()
    day, clock, understood = _split_day_clock(text, datetime.fromtimestamp(_now()).date())
    if not understood or clock is None:
        due, problem = _parse_due(text) if text else (None, "")
        if due is None:
            return None, f"Could not read {text!r} as the new time. Try '6 pm' or 'tomorrow 10am'."
        return due.timestamp(), ""
    day = day or old_day
    ts = _ts(day, clock)
    if _bare_hour(text) and clock[0] < 12:
        other = _ts(day, (clock[0] + 12, clock[1]))
        if abs(other - event.start) < abs(ts - event.start):
            ts = other
    return ts, ""


def _one_key(config, key: str) -> "tuple[bool, str]":
    """(allowed, why-not) for a single consent key."""
    try:
        on = config.get_bool(key, False)
    except Exception:  # noqa: BLE001 - an unreadable setting is a no
        on = False
    if on:
        return True, ""
    what = "changing your calendar" if key == _WRITE_KEY else "reading your calendar"
    return False, (f"Refusing: {what} is turned off (enable '{key}' in Settings). "
                   "Nothing was changed.")


def _consent(config) -> "tuple[bool, str]":
    """(allowed, why-not) for the write switch - the one `GATED` names.

    **The signature is the house convention.** It took `*keys` and returned a
    bare string, which broke `tests/test_question_presenter.py`'s sweep - and
    broke it in the worst way: the sweep calls `_consent(config)` with one
    argument, so a variadic signature checked *no keys at all*, returned an
    empty string, and the unpack of `(allowed, reason)` raised
    `ValueError: not enough values to unpack`. A gate that could not be swept is
    not a gate anyone has checked.
    """
    return _one_key(config, _WRITE_KEY)


def _also_needs_read(config) -> "tuple[bool, str]":
    """The read half, for move and cancel - finding the event to change it."""
    return _one_key(config, _READ_KEY)


def _create(arguments: dict, now: float) -> str:
    title = _text(arguments, "title", 200)
    if not title:
        return "What is the event called? Pass a title, e.g. 'Meeting with John'. Nothing was added."
    start_text = _text(arguments, "start")
    if not start_text:
        return "When does it start? Pass start, e.g. 'tomorrow 3pm'. Nothing was added."
    start, problem = _when(start_text, now)
    if start is None:
        return problem
    end_text, dur_text = _text(arguments, "end"), _text(arguments, "duration")
    if end_text:
        clock = _clock(end_text)
        if clock is None:
            return f"Could not read the end time {end_text!r}. Try '4pm' or give a duration. Nothing was added."
        end = _ts(datetime.fromtimestamp(start).date(), clock)
        if end <= start:
            end += 86400
    elif dur_text:
        seconds = _parse_duration(dur_text)
        if seconds is None:
            return f"Could not read {dur_text!r} as a length of time. Try '1 hour' or '30 minutes'. Nothing was added."
        end = start + seconds
    else:
        end = start + DEFAULT_DURATION
    if end - start > MAX_DURATION:
        return "That is longer than two weeks; refusing rather than writing a mistake. Nothing was added."
    try:
        event = cal.create_event(title, start, end, location=_text(arguments, "location", 200),
                                 description=_text(arguments, "notes", 2000))
    except cal.CalendarUnavailable as exc:
        return f"Could not add it: {exc}. Nothing was added."
    except cal.CalendarWriteFailed as exc:
        return f"Could not add it: {exc}."
    where = f" @ {event.location}" if event.location else ""
    return (f"Added '{event.summary}'{where} to {event.calendar}: {_fmt_span(event.start, event.end)}. "
            "No invitations were sent.")


def _move(arguments: dict, now: float) -> str:
    new_text = _text(arguments, "new_start")
    if not new_text:
        return "Move it to when? Pass new_start, e.g. '6 pm'. Nothing was changed."
    event, problem = _find(arguments, now)
    if event is None:
        return problem
    start, problem = _new_start(new_text, event)
    if start is None:
        return problem + " Nothing was changed."
    end = start + max(0.0, event.end - event.start)
    if abs(start - event.start) < 60:
        return f"'{event.summary}' already starts at {_fmt(event.start)}. Nothing was changed."
    ok, why = _confirm("Move this calendar event?",
                       f"{event.summary} ({event.calendar})\n{_fmt_span(event.start, event.end)}"
                       f"  ->  {_fmt_span(start, end)}")
    if not ok:
        return f"Not moved: {why}. '{event.summary}' is still at {_fmt(event.start)}."
    try:
        moved = cal.move_event(event, start, end)
    except (cal.CalendarUnavailable, cal.CalendarWriteFailed) as exc:
        return f"Could not move '{event.summary}': {exc}."
    return (f"Moved '{moved.summary}' ({moved.calendar}) from {_fmt(event.start)} to "
            f"{_fmt_span(moved.start, moved.end)}.")


def _cancel(arguments: dict, now: float) -> str:
    event, problem = _find(arguments, now)
    if event is None:
        return problem
    ok, why = _confirm("Delete this calendar event?",
                       f"{event.summary} ({event.calendar})\n{_fmt_span(event.start, event.end)}")
    if not ok:
        return f"Not cancelled: {why}. '{event.summary}' at {_fmt(event.start)} is still on your calendar."
    try:
        cal.remove_event(event)
    except (cal.CalendarUnavailable, cal.CalendarWriteFailed) as exc:
        return f"Could not cancel '{event.summary}': {exc}."
    return f"Cancelled '{event.summary}' ({event.calendar}) that was at {_fmt_span(event.start, event.end)}."


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "calendar_edit needs an object of arguments with an action."
    action = _text(arguments, "action").lower()
    action = _ALIASES.get(action, action)
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}, not {action or 'nothing'!r}."
    config = _config()
    allowed, refusal = _consent(config)
    if allowed and action != "create":
        # Moving or cancelling reads the calendar to find the event, so it is
        # two agreements rather than one.
        allowed, refusal = _also_needs_read(config)
    if not allowed:
        return refusal
    now = _now()
    if action == "create":
        return _create(arguments, now)
    if action == "move":
        return _move(arguments, now)
    return _cancel(arguments, now)


SKILLS = [Skill(name="calendar_edit", schema=SCHEMA, run=_run)]
