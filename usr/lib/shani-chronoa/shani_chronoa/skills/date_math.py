"""Skill: date arithmetic - days until a date, a date N days/weeks/months away,
days between two dates, the weekday of a date. Local, exact, no network."""

import calendar
import re
from datetime import date, datetime, timedelta

from shani_chronoa.skills import Skill

_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d/%m/%Y",
            "%d %B", "%d %b", "%B %d", "%b %d")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "date_math",
        "description": "Date arithmetic: days until a date ('how many days until 25 December'), the "
                       "date N days/weeks/months from a date ('what date is 45 days from now'), days "
                       "between two dates, or the weekday of a date.",
        "parameters": {"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["days_until", "add", "between", "weekday"]},
            "date": {"type": "string", "description": "e.g. '25 December', '2026-12-25', 'today', 'tomorrow'."},
            "other_date": {"type": "string", "description": "For between: the second date."},
            "amount": {"type": "integer", "description": "For add: how many (negative goes back)."},
            "unit": {"type": "string", "enum": ["days", "weeks", "months", "years"]},
        }, "required": ["operation"]},
    },
}


def parse_date(text: str, today: date) -> date:
    t = " ".join((text or "today").strip().lower().replace(",", " ").split())
    t = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", t)
    if t in ("today", "now"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t == "yesterday":
        return today - timedelta(days=1)
    for fmt in _FORMATS:
        try:
            d = datetime.strptime(t, fmt.lower() if "%" not in fmt else fmt).date()
        except ValueError:
            try:
                d = datetime.strptime(t.title(), fmt).date()
            except ValueError:
                continue
        if "%Y" not in fmt:  # no year said: the next time it comes round
            d = d.replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
        return d
    raise ValueError(f"I can't read '{text}' as a date")


def add(d: date, amount: int, unit: str) -> date:
    if unit == "days":
        return d + timedelta(days=amount)
    if unit == "weeks":
        return d + timedelta(weeks=amount)
    months = amount * (12 if unit == "years" else 1)
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _say(d: date) -> str:
    return d.strftime("%A, %d %B %Y")


def _run(arguments: dict, today: "date | None" = None) -> str:
    today = today or date.today()
    op = arguments.get("operation")
    try:
        d = parse_date(arguments.get("date") or "today", today)
        if op == "days_until":
            n = (d - today).days
            return f"{_say(d)} is {'today' if n == 0 else f'in {n} days' if n > 0 else f'{-n} days ago'}."
        if op == "add":
            amount, unit = int(arguments.get("amount") or 0), arguments.get("unit") or "days"
            return f"{amount} {unit} from {_say(d)} is {_say(add(d, amount, unit))}."
        if op == "between":
            e = parse_date(arguments.get("other_date") or "today", today)
            return f"There are {abs((e - d).days)} days between {_say(d)} and {_say(e)}."
        if op == "weekday":
            return f"{d.strftime('%d %B %Y')} is a {d.strftime('%A')}."
    except (ValueError, OverflowError) as e:
        return f"{e}."
    return "Unknown date operation."


SKILLS = [Skill(name="date_math", schema=_SCHEMA, run=_run)]
