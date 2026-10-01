"""Skill: leave a reminder for later.

`set_timer` is a countdown that fires while Chronoa is running. This is the
other thing people mean by "remind me": something written down, still there
tomorrow, with no timer involved and no assumption that anyone is listening.

Stored in a plain JSON file under the per-user data directory, one entry per
line, so a person can read it without Chronoa running at all. That is
deliberate: a reminder nobody can inspect is a reminder nobody can trust.

Honesty rules: the absolute due time is printed back, because "tomorrow at 9"
means different things on different days and the whole value of a reminder is
knowing which morning it is for. An unparseable relative time is refused rather
than guessed at.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_STORE = files.data_home() / "shani-chronoa" / "reminders.jsonl"
_MAX_CHARS = 500

SCHEMA = {
    "type": "function",
    "function": {
        "name": "add_reminder",
        "description": (
            "Write a reminder to be read later - something still there "
            "tomorrow, with no countdown involved. Optionally with a due time "
            "in words, e.g. 'tomorrow 9am' or 'in 3 hours'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to be reminded about."},
                "due": {
                    "type": "string",
                    "description": (
                        "When it applies, in words: 'in 2 hours', 'tomorrow 9am', "
                        "'2026-10-01 14:00'. Omit for no particular time."
                    ),
                },
            },
        },
    },
}

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
_RELATIVE = re.compile(
    r"^in\s+(\d+)\s+(second|minute|hour|day|week)s?$", re.IGNORECASE)


def _parse_due(raw: str) -> tuple[datetime | None, str]:
    """Return (due, problem). Never guesses: an unclear time is a refusal."""
    text = raw.strip()
    if not text:
        return None, ""
    now = datetime.now()
    match = _RELATIVE.match(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        return now + timedelta(seconds=amount * _UNITS[unit]), ""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%H:%M", "%H:%M:%S", "%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt.startswith("%H"):
            parsed = parsed.replace(year=now.year, month=now.month, day=now.day)
            if parsed <= now:
                parsed += timedelta(days=1)
        return parsed, ""
    if text.lower().startswith("tomorrow"):
        rest = text[len("tomorrow"):].strip() or "09:00"
        try:
            when = datetime.strptime(now.strftime("%Y-%m-%d") + " " + rest, "%Y-%m-%d %H:%M")
        except ValueError:
            return None, (
                f"Could not read {raw!r} as a time. Use 'in 2 hours', "
                f"'tomorrow 9am', or 'YYYY-MM-DD HH:MM'."
            )
        return when, ""
    return None, (
        f"Could not read {raw!r} as a time, so the reminder was written with no "
        f"due date rather than a guessed one. Use 'in 2 hours', 'tomorrow 9am', "
        f"or 'YYYY-MM-DD HH:MM'."
    )


def _run(arguments: dict) -> str:
    text = (arguments.get("text") or "").strip()
    if not text:
        return "No reminder text was given, so there is nothing to write down."
    if len(text) > _MAX_CHARS:
        return f"That reminder is {len(text)} characters, over the {_MAX_CHARS} limit. Shorten it."

    due, problem = _parse_due((arguments.get("due") or "").strip())
    entry = {
        "text": text,
        "written_at": time.time(),
        "due": due.isoformat() if due else None,
    }
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        with open(_STORE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        return files.describe(exc, _STORE, "write the reminder to")

    if not _STORE.exists():
        return f"Reported writing the reminder but {_STORE} is not there."
    when = f" Due {due.strftime('%Y-%m-%d %H:%M')}" if due else " No due time was given."
    note = f" {problem}" if problem else ""
    return f"Reminder written to {_STORE}: {text!r}.{when}{note}"


SKILLS = [Skill(name="add_reminder", schema=SCHEMA, run=_run)]
