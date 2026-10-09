"""Alarm clock: set, list, change, snooze, and delete alarms at a time of day.

A timer counts down a duration; an alarm rings at a clock time, optionally every
day, on weekdays, at weekends, or on chosen days. "Set an alarm for 7",
"change my 7am alarm to 7:30", "when is my next alarm?", "snooze", "delete the
7am alarm".

**Each alarm is a `systemd --user` transient timer with `OnCalendar=`**, the
same mechanism `set_timer` uses with `OnActive=`, so it rings whether or not
Chronoa is running. When it fires it records that it rang (so `snooze` knows
which alarm is ringing), sends a notification - under the same
`notification-enabled` switch `set_timer` honours - and plays the freedesktop
theme's alarm sound with pw-play or paplay when either is installed. The sound
is not behind the notification switch: an alarm the user set on purpose that
rings silently because toasts are off has not done its one job.

State is a JSON list under the user's state directory, next to the timers. A
one-shot alarm drops out of that list once its time has passed (its transient
unit is gone by then too); a repeating alarm stays until deleted.

**A transient unit does not survive a reboot or logout.** That is stated in the
schema rather than hidden, and listing alarms checks every one against systemd
and re-arms the ones that were lost, saying so. An alarm listed as set that
will not ring is the lie this module is built to avoid.

Times are read with `reminders._clock`/`reminders._parse_due` (one parser for
"7am", "7:30 pm", "tomorrow 6:45", "in 2 hours"), and days with the trigger
engine's `parse_schedule` ("weekdays", "mon,wed,fri"). Nothing here guesses:
an unreadable time, or a delete/change that matches several alarms, is refused
with the candidates listed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from shani_chronoa import files
from shani_chronoa.skills import Skill
from shani_chronoa.skills.reminders import _clock, _parse_due
from shani_chronoa.skills.timer import _systemd_available

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_PREFIX = "shani-chronoa-alarm"
_DATA_NAME = "alarms.json"
_MAX_ALARMS = 50
_SNOOZE_DEFAULT = 10
_SNOOZE_MAX = 120
#: How long after ringing an alarm still counts as "the ringing one" for snooze.
_SNOOZE_WINDOW = 2 * 3600
_SOUND = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_ID = re.compile(r"[0-9a-f]{4,32}")


# --- store ----------------------------------------------------------------

def _state_dir() -> Path:
    """Resolved per call, never at import (see `timer._data_path` for why)."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa"


def _data_path() -> Path:
    return _state_dir() / _DATA_NAME


def _fired_path() -> Path:
    return _state_dir() / "alarms-fired.log"


def _expired(alarm: dict, now: float) -> bool:
    if alarm.get("days"):
        return False
    try:
        return float(alarm.get("at") or 0) <= now
    except (TypeError, ValueError):
        return True


def _load() -> List[dict]:
    """Alarms still to ring. A one-shot alarm whose moment has passed is gone."""
    try:
        parsed = json.loads(_data_path().read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    now = time.time()
    return [a for a in parsed if isinstance(a, dict) and a.get("id") and not _expired(a, now)]


def _save(alarms: List[dict]) -> None:
    path = _data_path()
    files.ensure_private_dir(path.parent)
    path.write_text(json.dumps(alarms, indent=1))
    files.restrict_file(path)


def _fired() -> List[tuple]:
    """(id, epoch, label) for each time an alarm rang, oldest first."""
    try:
        lines = _fired_path().read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        parts = line.split(" ", 2)
        if len(parts) >= 2 and parts[1].isdigit():
            out.append((parts[0], int(parts[1]), parts[2] if len(parts) > 2 else "alarm"))
    return out


# --- time and days ----------------------------------------------------------

def _parse_days(raw) -> "tuple[Optional[list], str]":
    """[] for a one-shot alarm, sorted weekday numbers (Mon=0) for a repeat, or (None, why)."""
    if raw is None or raw == "" or raw is False:
        return [], ""
    if isinstance(raw, list):
        raw = ",".join(str(part) for part in raw)
    if not isinstance(raw, str):
        return None, "repeat must be words like 'daily', 'weekdays', 'weekends' or 'mon,wed,fri'."
    text = " ".join(raw.strip().lower().split())
    if text in ("", "once", "never", "none", "no", "one-shot", "one time", "false"):
        return [], ""
    if text in ("everyday", "every day", "each day", "daily"):
        text = "daily"
    else:
        text = re.sub(r"^(every|on)\s+", "", text)
        text = re.sub(r"\s+and\s+|\s*&\s*", ",", text)
    from shani_chronoa.triggers.desktop_sources import parse_schedule

    spec, why = parse_schedule(f"{text} 00:00")
    if spec is None or spec[0] != "days" or not spec[1]:
        return None, (f"Could not read {raw!r} as days. Use 'daily', 'weekdays', "
                      f"'weekends' or day names like 'mon,wed,fri'.")
    return sorted(spec[1]), ""


def _describe_days(days: list) -> str:
    if not days:
        return "once"
    if len(days) == 7:
        return "every day"
    if days == [0, 1, 2, 3, 4]:
        return "weekdays"
    if days == [5, 6]:
        return "weekends"
    return ", ".join(_DAY_NAMES[d] for d in days)


def _clock_of(text: str) -> "tuple[int, int] | None":
    text = text.strip()
    text = text[3:] if text.lower().startswith("at ") else text
    return _clock(text)


def _next_fire(alarm: dict, now: float) -> float:
    days = alarm.get("days") or []
    if not days:
        return float(alarm.get("at") or 0)
    base = datetime.fromtimestamp(now)
    for ahead in range(0, 8):
        day = base.date() + timedelta(days=ahead)
        when = datetime(day.year, day.month, day.day, int(alarm["hour"]), int(alarm["minute"]))
        if when.weekday() in days and when.timestamp() > now:
            return when.timestamp()
    return 0.0


def _calendar(alarm: dict) -> str:
    """The OnCalendar= expression systemd is given."""
    days = alarm.get("days") or []
    if not days:
        return datetime.fromtimestamp(float(alarm["at"])).strftime("%Y-%m-%d %H:%M:%S")
    clock = f"*-*-* {int(alarm['hour']):02d}:{int(alarm['minute']):02d}:00"
    if len(days) == 7:
        return clock
    return ",".join(_DAY_NAMES[d] for d in days) + " " + clock


def _when(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%a %-d %b %Y at %H:%M")


def _hhmm(alarm: dict) -> str:
    return f"{int(alarm['hour']):02d}:{int(alarm['minute']):02d}"


def _clean_label(raw) -> str:
    if not isinstance(raw, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", raw).strip()[:60]


# --- systemd ----------------------------------------------------------------

def _command(alarm: dict) -> str:
    """What runs when the alarm rings: record it, notify, ring."""
    label = alarm.get("label") or "alarm"
    q = shlex.quote
    sound = q(_SOUND)
    return (
        f"printf '%s %s %s\\n' {q(alarm['id'])} \"$(date +%s)\" {q(label)} "
        f">> {q(str(_fired_path()))}; "
        "gsettings get org.shani.chronoa notification-enabled 2>/dev/null | grep -q true "
        "&& command -v notify-send >/dev/null 2>&1 && "
        f"notify-send -u critical -i alarm-symbolic 'Chronoa' -- "
        f"{q(f'Alarm: {label} ({_hhmm(alarm)})')}; "
        f"if [ -f {sound} ]; then "
        "if command -v pw-play >/dev/null 2>&1; then P=pw-play; "
        "elif command -v paplay >/dev/null 2>&1; then P=paplay; else P=; fi; "
        f"if [ -n \"$P\" ]; then for i in 1 2 3; do \"$P\" {sound}; done; fi; "
        "fi; true"
    )


def _schedule(alarm: dict, unit: str) -> "tuple[bool, str]":
    try:
        files.ensure_private_dir(_state_dir())
        proc = subprocess.run(
            [
                "systemd-run", "--user", "--unit", f"{_PREFIX}-{unit}",
                f"--on-calendar={_calendar(alarm)}",
                "--timer-property=AccuracySec=1s",
                "--description", "Shani Chronoa alarm",
                "/bin/sh", "-c", _command(alarm),
            ],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"systemd-run could not be launched ({exc})"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip() or "unknown error"
    return True, ""


def _unschedule(unit: str) -> "tuple[bool, str]":
    if shutil.which("systemctl") is None:
        return False, "systemctl is not installed"
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "stop", f"{_PREFIX}-{unit}.timer"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"systemctl could not be launched ({exc})"
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout or "").strip()
        if "not loaded" in why.lower():
            return True, ""
        return False, why or f"systemctl exited {proc.returncode}"
    return True, ""


def _armed(units: List[str]) -> "Optional[set]":
    """Which of `units` systemd has an active timer for; None when it cannot be asked."""
    if not units or shutil.which("systemctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", *[f"{_PREFIX}-{u}.timer" for u in units]],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    states = (proc.stdout or "").split()
    if len(states) != len(units):
        return None
    return {u for u, s in zip(units, states) if s == "active"}


def _new_unit(identifier: str) -> str:
    return f"{identifier}-{uuid.uuid4().hex[:4]}"


def _arm(alarms: list, alarm: dict) -> str:
    """Schedule `alarm` on a fresh unit, then stop its old one; '' or why not.

    New first, old second: a failure leaves exactly one unit armed, never none.
    """
    unit = _new_unit(alarm["id"])
    ok, why = _schedule(alarm, unit)
    if not ok:
        return f"systemd refused to schedule it ({why})"
    old = alarm.get("unit")
    if old:
        stopped, reason = _unschedule(old)
        if not stopped:
            _unschedule(unit)
            return f"systemd could not stop the old schedule ({reason})"
    alarm["unit"] = unit
    return ""


# --- actions ----------------------------------------------------------------

def _summary(alarm: dict, now: float) -> str:
    label = f" '{alarm['label']}'" if alarm.get("label") else ""
    if alarm.get("days"):
        return (f"{_hhmm(alarm)} {_describe_days(alarm['days'])}{label} (id {alarm['id']}), "
                f"next {_when(_next_fire(alarm, now))}")
    return f"{_when(_next_fire(alarm, now))}{label} (id {alarm['id']}), once"


def _listing(alarms: list, now: float) -> str:
    lines = []
    for alarm in sorted(alarms, key=lambda a: _next_fire(a, now)):
        lines.append(f"  {_summary(alarm, now)}")
    return "\n".join(lines)


def _when_from(text: str, days: list) -> "tuple[Optional[dict], str]":
    """{hour, minute[, at]} for an alarm time, or (None, why)."""
    if days:
        clock = _clock_of(text)
        if clock is None:
            return None, (f"Could not read {text!r} as a time of day. A repeating "
                          f"alarm needs a clock time like '7am' or '07:30'.")
        return {"hour": clock[0], "minute": clock[1]}, ""
    due, problem = _parse_due(text[3:] if text.lower().startswith("at ") else text)
    if due is None:
        return None, (problem or f"Could not read {text!r} as a time.").replace(
            "the reminder was written with no due date rather than a guessed one",
            "no alarm was set").replace("no reminder time was set", "no alarm was set")
    if due.timestamp() <= time.time():
        return None, f"{text!r} is in the past, so no alarm was set."
    return {"hour": due.hour, "minute": due.minute, "at": due.timestamp()}, ""


def set_alarm(when: str, label: str = "", repeat=None) -> str:
    days, problem = _parse_days(repeat)
    if days is None:
        return problem + " No alarm was set."
    parsed, problem = _when_from(when, days)
    if parsed is None:
        return problem
    alarms = _load()
    if len(alarms) >= _MAX_ALARMS:
        return f"No alarm was set: {_MAX_ALARMS} alarms are already set. Delete one first."
    if not _systemd_available():
        return ("The alarm was NOT set: systemd --user is not available in this "
                "session, so nothing could be scheduled to ring.")
    alarm = {"id": uuid.uuid4().hex[:8], "label": label, "days": days,
             "created": time.time(), **parsed}
    problem = _arm(alarms, alarm)
    if problem:
        return f"The alarm was NOT set: {problem}. Nothing was recorded."
    alarms.append(alarm)
    _save(alarms)
    return "Alarm set: " + _summary(alarm, time.time()) + "."


def list_alarms(only_next: bool = False) -> str:
    alarms = _load()
    if not alarms:
        return "No alarms are set."
    now = time.time()
    note = _rearm_lost(alarms)
    alarms = _load()
    if not alarms:
        return "No alarms are set." + note
    if only_next:
        first = min(alarms, key=lambda a: _next_fire(a, now))
        return "Your next alarm: " + _summary(first, now) + "." + note
    return "Alarms, soonest first:\n" + _listing(alarms, now) + note


def _rearm_lost(alarms: list) -> str:
    """Re-arm alarms systemd has no timer for (a reboot or logout drops them)."""
    armed = _armed([a.get("unit") or "" for a in alarms])
    if armed is None:
        return ""
    lost = [a for a in alarms if (a.get("unit") or "") not in armed]
    if not lost:
        return ""
    restored, failed = [], []
    for alarm in lost:
        alarm.pop("unit", None)  # nothing to stop: systemd has no such timer
        (failed if _arm(alarms, alarm) else restored).append(alarm)
    _save(alarms)
    note = ""
    if restored:
        note += (f"\n{len(restored)} alarm(s) had been dropped by systemd (e.g. by a "
                 f"reboot or logout) and were re-armed: "
                 + ", ".join(a["id"] for a in restored) + ".")
    if failed:
        note += ("\nNOT armed - these will not ring until set again: "
                 + ", ".join(a["id"] for a in failed) + ".")
    return note


def _select(alarms: list, identifier: str, when: str, label: str) -> "tuple[list, str, str]":
    """(matching alarms, what was asked for, problem); problem is '' when the selectors were usable."""
    if identifier:
        if not _ID.fullmatch(identifier):
            return [], "", f"{identifier!r} is not an alarm id. Use action=list to see them."
        return [a for a in alarms if a["id"] == identifier], f"id {identifier}", ""
    found = list(alarms)
    described = []
    if when:
        clock = _clock_of(when)
        if clock is None:
            due, _ = _parse_due(when)
            clock = (due.hour, due.minute) if due else None
        if clock is None:
            return [], "", f"Could not read {when!r} as a time, so no alarm was picked."
        found = [a for a in found if (int(a["hour"]), int(a["minute"])) == clock]
        described.append(f"{clock[0]:02d}:{clock[1]:02d}")
    if label:
        found = [a for a in found if str(a.get("label") or "").casefold() == label.casefold()]
        described.append(f"label {label!r}")
    return found, " and ".join(described), ""


def _one(identifier: str, when: str, label: str, verb: str) -> "tuple[Optional[dict], list, str]":
    """The single alarm the selectors name, or (None, alarms, why) - never a guess."""
    alarms = _load()
    if not alarms:
        return None, alarms, "No alarms are set."
    if not (identifier or when or label):
        if len(alarms) == 1:
            return alarms[0], alarms, ""
        return None, alarms, (f"Several alarms are set; say which one to {verb} "
                              f"(by id, time or label):\n" + _listing(alarms, time.time()))
    found, described, problem = _select(alarms, identifier, when, label)
    if problem:
        return None, alarms, problem
    if not found:
        return None, alarms, (f"No alarm matches {described}; nothing was {verb}d.\n"
                              f"Alarms set:\n" + _listing(alarms, time.time()))
    if len(found) > 1:
        return None, alarms, (f"{len(found)} alarms match {described}; nothing was "
                              f"{verb}d. Say which by id:\n" + _listing(found, time.time()))
    return found[0], alarms, ""


def delete_alarm(identifier: str = "", when: str = "", label: str = "") -> str:
    alarm, alarms, why = _one(identifier, when, label, "delete")
    if alarm is None:
        return why
    if alarm.get("unit"):
        stopped, reason = _unschedule(alarm["unit"])
        if not stopped:
            return (f"The alarm was NOT deleted: systemd could not stop it ({reason}), "
                    f"so it would still ring. Alarm: {_summary(alarm, time.time())}.")
    _save([a for a in alarms if a["id"] != alarm["id"]])
    return f"Deleted alarm {_summary(alarm, time.time())}."


def change_alarm(identifier: str, when: str, label: str, new_time: str,
                 new_label: Optional[str], repeat) -> str:
    if not new_time and new_label is None and repeat is None:
        return ("Nothing to change: pass new_time, new_label or repeat, e.g. "
                "new_time='7:30'.")
    alarm, alarms, why = _one(identifier, when, label, "change")
    if alarm is None:
        return why
    before = _summary(alarm, time.time())
    updated = dict(alarm)
    if repeat is not None:
        days, problem = _parse_days(repeat)
        if days is None:
            return problem + " The alarm was not changed."
        updated["days"] = days
    if new_label is not None:
        updated["label"] = new_label
    if new_time or (repeat is not None and updated["days"] != (alarm.get("days") or [])):
        text = new_time or _hhmm(alarm)
        parsed, problem = _when_from(text, updated["days"])
        if parsed is None:
            return problem.replace("no alarm was set", "the alarm was not changed")
        updated.pop("at", None)
        updated.update(parsed)
    if not _systemd_available():
        return ("The alarm was NOT changed: systemd --user is not available in this "
                "session. It is still: " + before + ".")
    problem = _arm(alarms, updated)
    if problem:
        return f"The alarm was NOT changed: {problem}. It is still: {before}."
    _save([updated if a["id"] == alarm["id"] else a for a in alarms])
    return f"Changed alarm {alarm['id']}: now {_summary(updated, time.time())} (was {before})."


def snooze_alarm(minutes: int, identifier: str = "", label: str = "") -> str:
    now = time.time()
    rang = [f for f in _fired() if now - f[1] <= _SNOOZE_WINDOW]
    if identifier:
        rang = [f for f in rang if f[0] == identifier]
    if label:
        rang = [f for f in rang if f[2].casefold() == label.casefold()]
    if not rang:
        return ("No alarm has rung in the last two hours, so there is nothing to "
                "snooze. To move an upcoming alarm, use action=change.")
    rang_id, _, rang_label = rang[-1]
    if not _systemd_available():
        return "The alarm was NOT snoozed: systemd --user is not available in this session."
    at = now + minutes * 60
    moment = datetime.fromtimestamp(at)
    alarms = _load()
    alarm = {"id": uuid.uuid4().hex[:8], "label": rang_label if rang_label != "alarm" else "",
             "days": [], "hour": moment.hour, "minute": moment.minute, "at": at,
             "created": now, "snoozed_from": rang_id}
    problem = _arm(alarms, alarm)
    if problem:
        return f"The alarm was NOT snoozed: {problem}."
    alarms.append(alarm)
    _save(alarms)
    _trim_fired()
    name = f"'{rang_label}'" if rang_label and rang_label != "alarm" else "the alarm"
    return (f"Snoozed {name} for {minutes} minutes: it rings again at "
            f"{moment.strftime('%H:%M:%S')} (id {alarm['id']}).")


def _trim_fired(keep: int = 100) -> None:
    rows = _fired()
    if len(rows) > keep:
        try:
            _fired_path().write_text("".join(f"{i} {t} {l}\n" for i, t, l in rows[-keep:]))
        except OSError:
            pass


SCHEMA = {
    "type": "function",
    "function": {
        "name": "alarm",
        "description": (
            "Alarm clock: set an alarm at a time of day (once, or repeating "
            "daily/weekdays/weekends/chosen days), list alarms, say when the next "
            "one is, change one's time or label, snooze the one that just rang, "
            "or delete one. Examples: 'set an alarm for 7am', 'alarm for 6:30 on "
            "weekdays', 'when is my next alarm?', 'change my 7am alarm to 7:30', "
            "'snooze', 'delete the 7am alarm'. Times may be words ('7am', "
            "'7:30 pm', 'tomorrow 6:45'). It rings with a notification and the "
            "alarm sound even if the assistant is closed; it is held by "
            "systemd --user, so a reboot drops it until alarms are next listed, "
            "which re-arms it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set", "list", "next", "change", "snooze", "delete"],
                    "description": (
                        "set a new alarm (needs time), list all, next (when the "
                        "next alarm rings), change one (new_time/new_label/"
                        "repeat), snooze the alarm that just rang, delete one."
                    ),
                    "default": "set",
                },
                "time": {
                    "type": "string",
                    "description": (
                        "For set: when it rings, e.g. '7am', '6:45', 'tomorrow "
                        "7:30', '21:00'. For change/delete: which alarm, by its "
                        "time, e.g. '7am'."
                    ),
                },
                "label": {
                    "type": "string",
                    "description": "For set: what it is for, e.g. 'gym'. For change/delete/snooze: which alarm, by label.",
                },
                "repeat": {
                    "type": "string",
                    "description": (
                        "Repeat days: 'daily', 'weekdays', 'weekends', or day "
                        "names like 'mon,wed,fri'. Omit for a one-time alarm; "
                        "'once' makes a repeating alarm one-time on change."
                    ),
                },
                "id": {"type": "string", "description": "Alarm id, from the list."},
                "new_time": {"type": "string", "description": "For change: the new time, e.g. '7:30'."},
                "new_label": {"type": "string", "description": "For change: the new label."},
                "minutes": {
                    "type": "integer",
                    "description": f"For snooze: minutes to snooze, default {_SNOOZE_DEFAULT}.",
                },
            },
        },
    },
}

_ACTIONS = ("set", "list", "next", "change", "snooze", "delete")
_ALIASES = {"remove": "delete", "cancel": "delete", "edit": "change", "update": "change",
            "move": "change", "add": "set", "create": "set", "when": "next"}


def _text(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return value.strip() if isinstance(value, str) else ""


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "The alarm arguments were not an object; nothing was done."
    action = _text(arguments, "action").lower() or "set"
    action = _ALIASES.get(action, action)
    identifier = _text(arguments, "id").lower()
    when = _text(arguments, "time")
    label = _clean_label(arguments.get("label"))
    if action == "list":
        return list_alarms()
    if action == "next":
        return list_alarms(only_next=True)
    if action == "delete":
        return delete_alarm(identifier, when, label)
    if action == "snooze":
        raw = arguments.get("minutes")
        if raw is None or raw == "":
            minutes = _SNOOZE_DEFAULT
        elif isinstance(raw, bool) or not re.fullmatch(r"\d+", str(raw).strip()):
            return "Invalid snooze length: expected a whole number of minutes, e.g. minutes=10."
        else:
            minutes = int(str(raw).strip())
        if not 1 <= minutes <= _SNOOZE_MAX:
            return f"Invalid snooze length: expected 1 to {_SNOOZE_MAX} minutes."
        return snooze_alarm(minutes, identifier, label)
    if action == "change":
        new_time = _text(arguments, "new_time")
        new_label = (_clean_label(arguments["new_label"])
                     if isinstance(arguments.get("new_label"), str) else None)
        repeat = arguments.get("repeat")
        if repeat is not None and not isinstance(repeat, (str, list)):
            return "repeat must be words like 'daily', 'weekdays' or 'mon,wed,fri'. The alarm was not changed."
        # "Change alarm ab12 to 7:30" often arrives as id + time with no new_time:
        # once id or label already names the alarm, `time` can only be the new time.
        if not new_time and when and (identifier or label):
            new_time, when = when, ""
        return change_alarm(identifier, when, label, new_time, new_label, repeat)
    if action != "set":
        return f"Unknown action {action!r}. Valid actions are: {', '.join(_ACTIONS)}."
    if not when:
        return ("No time given. Pass time, e.g. time='7am' or time='tomorrow 6:30', "
                "or use action=list to see the alarms already set.")
    repeat = arguments.get("repeat")
    if repeat is not None and not isinstance(repeat, (str, list)):
        return "repeat must be words like 'daily', 'weekdays' or 'mon,wed,fri'. No alarm was set."
    return set_alarm(when, label, repeat)


def _verify_alarm(arguments: dict, tool=None):
    """Post-condition: does the alarm store hold what this call claimed?

    `set` must leave a newly created alarm (last minute) with the label asked
    for; `delete` by id must leave that id gone. Everything else is None -
    nothing checkable, not a manufactured pass.
    """
    if not isinstance(arguments, dict):
        return None
    action = _text(arguments, "action").lower() or "set"
    action = _ALIASES.get(action, action)
    alarms = _load()
    if action == "set":
        if not _text(arguments, "time"):
            return None
        fresh = [a for a in alarms if time.time() - float(a.get("created") or 0) < 60]
        if not fresh:
            return (False, f"{_DATA_NAME} holds no alarm created by this call")
        newest = max(fresh, key=lambda a: float(a.get("created") or 0))
        label = _clean_label(arguments.get("label"))
        if label and newest.get("label") != label:
            return (False, f"the newest alarm is labelled {newest.get('label')!r}, not {label!r}")
        if not newest.get("unit"):
            return (False, "the newest alarm has no systemd unit, so it cannot ring")
        return (True, f"alarm {newest['id']} in {_DATA_NAME} is armed as "
                      f"{_PREFIX}-{newest['unit']} for {_calendar(newest)!r}")
    identifier = _text(arguments, "id").lower()
    if action == "delete" and identifier:
        gone = all(a["id"] != identifier for a in alarms)
        return (gone, f"alarm {identifier} is {'absent from' if gone else 'still in'} {_DATA_NAME}")
    return None


POST_CONDITION = _verify_alarm

SKILLS = [Skill(name="alarm", schema=SCHEMA, run=_run)]
