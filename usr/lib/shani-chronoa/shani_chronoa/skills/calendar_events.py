"""Skill: what is on the user's calendar - today, tomorrow, this week, or the next few hours.

Read from the desktop's own calendar service (see shani_chronoa/eds_calendar.py):
every account the user added in GNOME Online Accounts plus the local one.
Gated by `calendar-read-enabled`, off by default: who you meet and where is
yours. A calendar that cannot be read says so; it is never reported as empty.
"""

from __future__ import annotations

import time

from shani_chronoa import eds_calendar as cal
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "calendar-read-enabled"
MAX_EVENTS = 25
_RANGES = ("today", "tomorrow", "week", "next_hours")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_events",
        "description": ("List events on the user's calendars (GNOME calendars and online accounts): today, "
                        "tomorrow, this week, or the next few hours. Requires the 'calendar-read-enabled' "
                        "consent key."),
        "parameters": {"type": "object", "properties": {
            "range": {"type": "string", "enum": list(_RANGES), "description": "Which span. Defaults to today."},
            "hours": {"type": "integer", "description": "For next_hours: how many (1-72)."},
        }},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, f"reading your calendar is turned off (enable '{_CONSENT_KEY}' in Settings)."
    return True, ""


def span(which: str, hours: int, now: float) -> "tuple[float, float, str]":
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if which == "tomorrow":
        return midnight + 86400, midnight + 2 * 86400, "tomorrow"
    if which == "week":
        return now, midnight + 7 * 86400, "in the next 7 days"
    if which == "next_hours":
        h = max(1, min(int(hours or 4), 72))
        return now, now + h * 3600, f"in the next {h} hour(s)"
    return midnight, midnight + 86400, "today"


def _run(arguments: dict, now: "float | None" = None) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to read your calendar: {reason}"
    which = (arguments.get("range") or "today").strip().lower()
    if which not in _RANGES:
        return f"range must be one of {', '.join(_RANGES)}, not {which!r}."
    try:
        hours = int(arguments.get("hours") or 4)
    except (TypeError, ValueError):
        hours = 4
    start, end, label = span(which, hours, time.time() if now is None else now)
    try:
        events = cal.events_between(start, end)
    except cal.CalendarUnavailable as exc:
        return f"I could not read your calendar: {exc}. That is not the same as having nothing on."
    if not events:
        return f"Nothing on your calendars {label}."
    shown = events[:MAX_EVENTS]
    more = f"\n... and {len(events) - MAX_EVENTS} more." if len(events) > MAX_EVENTS else ""
    return f"{len(events)} event(s) {label}:\n" + "\n".join(f"- {cal.describe(e)}" for e in shown) + more


SKILLS = [Skill(name="calendar_events", schema=SCHEMA, run=_run)]
