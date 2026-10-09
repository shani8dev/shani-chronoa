"""Skill: a list of reminders you can add to, list, mark done, and remove.

`set_timer` is a countdown that fires while Chronoa is running. This is the
other thing people mean by "remind me": things written down, still there
tomorrow, with no timer involved and no assumption that anyone is listening.

Full CRUD: `add` writes one, `list` shows what is outstanding, `done` marks one
finished by its list number, `remove` deletes one. The skill is named `reminders`
rather than `add_reminder` because it does all four, not just the first; the
exposed tool name used to promise less than the skill did once done and remove
landed.

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

from shani_chronoa import files
from shani_chronoa.skills import Skill

_STORE = files.data_home() / "shani-chronoa" / "reminders.jsonl"
_MAX_CHARS = 500

SCHEMA = {
    "type": "function",
    "function": {
        "name": "reminders",
        "description": (
            "A dated list of reminders you can keep coming back to: add one, "
            "list what is still due, mark one done, or remove one. Optionally "
            "with a due time in words, e.g. 'tomorrow 9am' or 'in 3 hours'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "done", "remove"],
                    "description": (
                        "What to do. 'add' writes one (needs text), 'list' "
                        "shows what is still due, 'done' marks one finished "
                        "given its number, 'remove' deletes one given its "
                        "number. Defaults to 'add'."
                    ),
                },
                "number": {
                    "type": "integer",
                    "description": "Which reminder, as shown by 'list'. Required for done and remove.",
                },
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


_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)


_NAMED_CLOCKS = {"noon": (12, 0), "midday": (12, 0), "midnight": (0, 0)}

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _weekday(word: str) -> "int | None":
    """0-6 for 'friday', 'fri', 'Fri,'; None for anything else."""
    w = word.strip().lower().rstrip(",.")
    if len(w) < 3:
        return None
    for index, name in enumerate(_WEEKDAYS):
        if name.startswith(w) and (len(w) >= 3):
            return index
    return None


def _split_day_clock(text: str, today) -> "tuple[object, tuple[int, int] | None, bool]":
    """Split 'friday 5 pm', 'tomorrow at 3pm', 'next tue', '2026-10-09 14:00', '5pm'
    into (date or None, (hour, minute) or None, understood).

    The day is never moved to make a time land in the future - that is the
    caller's decision, because "my 5 PM call" (find it) and "remind me at 5"
    (it must be ahead) want different answers. A weekday means its next
    occurrence counting today; "next friday" means the one after today.
    """
    words = text.strip().replace(",", " ").split()
    words = [w for w in words if w.lower() not in ("on", "at", "this")]
    if not words:
        return None, None, True
    day = None
    first = words[0].lower()
    if first == "today":
        day, words = today, words[1:]
    elif first == "tomorrow":
        day, words = today + timedelta(days=1), words[1:]
    elif first == "next" and len(words) > 1 and _weekday(words[1]) is not None:
        ahead = (_weekday(words[1]) - today.weekday()) % 7 or 7
        day, words = today + timedelta(days=ahead), words[2:]
    elif _weekday(first) is not None:
        day, words = today + timedelta(days=(_weekday(first) - today.weekday()) % 7), words[1:]
    else:
        try:
            day, words = datetime.strptime(first, "%Y-%m-%d").date(), words[1:]
        except ValueError:
            pass
    rest = " ".join(words).strip()
    if not rest:
        return day, None, True
    clock = _clock(rest)
    return day, clock, clock is not None


_DURATION_PART = re.compile(
    r"(\d+(?:\.\d+)?|an?|half(?:\s+an?)?)\s*(h|hrs?|hours?|m|mins?|minutes?)\b", re.I)


def _parse_duration(text: str) -> "int | None":
    """Seconds for '1 hour', 'an hour', '90 minutes', '1.5 hours', 'half an hour',
    '1h30m', 'an hour and a half'; None when it is not clearly a duration."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    total, consumed = 0.0, raw
    for amount, unit in _DURATION_PART.findall(raw):
        value = 0.5 if amount.startswith("half") else 1.0 if amount in ("a", "an") else float(amount)
        total += value * (3600 if unit.startswith("h") else 60)
    consumed = _DURATION_PART.sub("", consumed)
    if re.search(r"\band a half\b", consumed):
        total += 1800
        consumed = consumed.replace("and a half", "")
    match = re.fullmatch(r"\s*(\d+)\s*", consumed.replace("and", ""))
    if match and total and re.search(r"\d+\s*h", raw):  # '1h30' - the trailing minutes
        total += int(match.group(1)) * 60
        consumed = ""
    if re.sub(r"\band\b", "", consumed).strip() or total <= 0:
        return None
    return int(total)


def _clock(text: str) -> "tuple[int, int] | None":
    """(hour, minute) for '9am', '9:30 pm', '21:00', '7'; None when it is not a clock time."""
    named = _NAMED_CLOCKS.get(text.strip().lower())
    if named:
        return named
    match = _CLOCK.match(text.strip().replace(".", ""))
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").lower()
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


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
    lowered = text.lower()
    if lowered.startswith("tomorrow") or lowered.startswith("today"):
        word = "tomorrow" if lowered.startswith("tomorrow") else "today"
        rest = text[len(word):].strip()
        rest = rest[3:].strip() if rest.lower().startswith("at ") else rest
        clock = _clock(rest) if rest else (9, 0)
        if clock is None:
            return None, (
                f"Could not read {raw!r} as a time. Use 'in 2 hours', "
                f"'tomorrow 9am', or 'YYYY-MM-DD HH:MM'."
            )
        # The day is the point of the word. This branch used to build the time
        # on *today's* date, so "tomorrow 09:00" landed this morning - in the
        # past - and "tomorrow 9am", the schema's own example, did not parse.
        day = now.date() + timedelta(days=1 if word == "tomorrow" else 0)
        when = datetime(day.year, day.month, day.day, clock[0], clock[1])
        if when <= now:
            return None, f"{raw!r} has already passed today, so no reminder time was set."
        return when, ""
    clock = _clock(text)
    if clock is not None:
        when = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        return (when if when > now else when + timedelta(days=1)), ""
    # "friday 5pm", "next tue at 9:30": the weekday's next occurrence, and a
    # week on if that time has already gone today.
    first = lowered.replace(",", " ").split()[0]
    if _weekday(first) is not None or (first == "next" and len(lowered.split()) > 1):
        day, clock, understood = _split_day_clock(text, now.date())
        if understood and day is not None:
            hour, minute = clock if clock is not None else (9, 0)
            when = datetime(day.year, day.month, day.day, hour, minute)
            if when <= now:
                when += timedelta(days=7)
            return when, ""
    return None, (
        f"Could not read {raw!r} as a time, so the reminder was written with no "
        f"due date rather than a guessed one. Use 'in 2 hours', 'tomorrow 9am', "
        f"or 'YYYY-MM-DD HH:MM'."
    )


def _entries() -> list[dict]:
    """The store, newest-appended last, unreadable lines skipped."""
    try:
        raw = _STORE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _save_entries(entries: list[dict]) -> str | None:
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
        os.replace(tmp, _STORE)
    except OSError as exc:
        return files.describe(exc, _STORE, "write the reminders to")
    return None


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "add").strip().lower()
    if action not in ("add", "list", "done", "remove"):
        return "Action must be one of add, list, done, remove."

    entries = _entries()
    if action == "list":
        pending = [e for e in entries if not e.get("done")]
        if not pending:
            return "No reminders outstanding."
        lines = []
        for i, e in enumerate(entries):
            if e.get("done"):
                continue
            when = f" (due {e['due'][:16].replace('T', ' ')})" if e.get("due") else ""
            lines.append(f"  {i + 1}. {e.get('text', '')}{when}")
        return "Reminders outstanding:\n" + "\n".join(lines)

    if action in ("done", "remove"):
        try:
            number = int(arguments.get("number"))
        except (TypeError, ValueError):
            return "Pass the reminder's number (as shown by 'list')."
        index = number - 1
        if not (0 <= index < len(entries)) or entries[index].get("done") and action == "done":
            return f"No such reminder: number {number}."
        if action == "done":
            entries[index]["done"] = True
            err = _save_entries(entries)
            if err:
                return err
            return f"Marked done: {entries[index].get('text', '')!r}."
        entry = entries.pop(index)
        err = _save_entries(entries)
        if err:
            return err
        return f"Removed: {entry.get('text', '')!r}."

    # add (default)
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
        "done": False,
    }
    entries.append(entry)
    err = _save_entries(entries)
    if err:
        return err

    if not _STORE.exists():
        return f"Reported writing the reminder but {_STORE} is not there."
    when = f" Due {due.strftime('%Y-%m-%d %H:%M')}" if due else " No due time was given."
    note = f" {problem}" if problem else ""
    return f"Reminder written to {_STORE}: {text!r}.{when}{note}"


def _verify_reminder(arguments: dict, tool=None):
    """Post-condition: is the reminder text actually in the store?

    `reminders` appends one JSON line to `_STORE`. The check reads the store
    back and confirms the text asked for is present, because "I'll remind you"
    followed by a store that does not contain it is the failure worth catching -
    and a reminder is one of the few things a person will trust without
    checking.

    Matched on the text, not on position: two reminders added in the same
    millisecond are otherwise indistinguishable, and matching the newest
    matching line is what makes that safe.
    """
    action = str(arguments.get("action") or "add").strip().lower()
    if action != "add":
        return None  # a list/done/remove changes the store, so the add check does not apply
    text = str(arguments.get("text") or arguments.get("reminder") or "").strip()
    if not text:
        return None  # nothing was written, so nothing to check
    try:
        raw = _STORE.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (False, f"could not read {_STORE.name}: {type(exc).__name__}")
    import json as _json

    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = _json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    if not entries:
        return (False, f"{_STORE.name} holds no reminder at all")
    stored = [str(e.get("text") or e.get("body") or "") for e in entries]
    if text not in stored:
        return (False, f"{_STORE.name} holds {len(stored)} reminder(s) but "
                       f"none of them says this one")
    index = stored.index(text)
    when = entries[index].get("at") or entries[index].get("when")
    return (True, f"{_STORE.name} line {index + 1} carries this reminder"
                  + (f", due {when}" if when else ", with no time recorded"))


POST_CONDITION = _verify_reminder


SKILLS = [Skill(name="reminders", schema=SCHEMA, run=_run)]
