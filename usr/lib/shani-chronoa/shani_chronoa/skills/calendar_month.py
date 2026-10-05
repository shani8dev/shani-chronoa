"""Skill: show a month's calendar, with today marked. Python's calendar; local."""

import calendar
from datetime import date

from shani_chronoa.skills import Skill

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_month",
        "description": "Show the calendar for a month (default this month), with today marked.",
        "parameters": {"type": "object", "properties": {
            "month": {"type": "string", "description": "e.g. 'March' or '3'."},
            "year": {"type": "integer"}}},
    },
}


def _run(arguments: dict, today: "date | None" = None) -> str:
    today = today or date.today()
    m = str(arguments.get("month") or today.month).strip().lower()
    month = MONTHS.get(m) or (int(m) if m.isdigit() and 1 <= int(m) <= 12 else None)
    if not month:
        return f"'{arguments.get('month')}' is not a month."
    year = int(arguments.get("year") or today.year)
    mark = today.day if (year, month) == (today.year, today.month) else None
    lines = [f"{calendar.month_name[month]} {year}".center(27), " Mo  Tu  We  Th  Fr  Sa  Su"]
    for week in calendar.Calendar(calendar.MONDAY).monthdayscalendar(year, month):
        lines.append("".join(("    " if d == 0 else f"[{d:2d}]" if d == mark else f" {d:2d} ") for d in week).rstrip())
    text = "\n".join(lines) + (f"\nToday: {today.strftime('%A %d %B')}" if mark else "")
    return text


SKILLS = [Skill(name="calendar_month", schema=_SCHEMA, run=_run)]
