"""Countdown timers that survive the assistant, and can be listed and cancelled.

**This replaces an in-process `threading.Timer`, which had two defects that only
appeared when you tried to use it.** A timer set through the old skill lived in
the assistant's own process: closing the window, or the assistant being
restarted, silently killed every pending timer with no error, and there was no
way to ask what was pending or to call one off. A user who said "set a timer for
the pasta" and then found the app had been restarted had no way to know the
timer was gone, and no way to re-set it without stacking duplicates.

State is a JSON file under the user's own state directory, and the countdown is
owned by a `systemd --user` transient timer, so the notification fires whether or
not the assistant is running. The JSON is the list/cancel surface; systemd is
what makes the timer real.

**Cancelling is idempotent and reports exactly what it did.** Cancelling an id
that already fired, or was never set, is a normal outcome rather than an error -
a user tidying up after a timer went off should not be told they made a mistake.
What matters is that the reply never claims to have cancelled something it did
not.

**A timer that cannot be scheduled is reported as unscheduled, not as set.** If
`systemd --user` is unavailable, nothing is written and the skill says so. A
timer recorded as pending that will never fire is the same class of lie as a
camera reported as disabled when it is not.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from shani_chronoa import files
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_TIMEOUT = 20
#: Kept only as a *name* for messages; the path itself is resolved per call by
#: `_data_path()` (see its docstring for why it is not a module constant).
_DATA_NAME = "timers.json"
_PREFIX = "shani-chronoa-timer"
_MAX_SECONDS = 86400 * 7
# The payload is a template rather than a fixed string for two reasons the
# in-process version got right and the first systemd version dropped: the
# notification has to say which timer went off, and it must not appear at all
# when `notification-enabled` is off. A user who turned notifications off still
# got one, and one that says "A timer finished" instead of "pasta" is useless.
_NOTIFY_TEMPLATE = (
    "gsettings get org.shani.chronoa notification-enabled 2>/dev/null "
    "| grep -q true && command -v notify-send >/dev/null 2>&1 && "
    "notify-send 'Chronoa' -- 'Timer: {label}' || true"
)


def _escape(text: str) -> str:
    """Make a label safe to embed in a single-quoted shell word."""
    return str(text).replace("'", "'\\''")[:60]


def active_timers() -> "list[tuple[str, str, float]]":
    """Pending timers as `(id, label, seconds_left)`, soonest first.

    Exists so a reader other than this skill's own text output can ask the
    store a question without reaching into `_load` and re-deriving the "is
    this one still in the future" filter - which is the part that is easy to get
    subtly wrong (a timer whose moment has passed is not a pending timer, and
    listing it would be a promise nobody keeps).

    An unreadable store is an empty list, not an exception: this is a glance,
    and a glance that raises takes the window with it.

    A paused timer is stored with `due` 0 and is not counting down, so it is
    deliberately absent here: a rail row showing a frozen number as a live
    countdown would be wrong. `list_timers` shows it, marked paused.
    """
    now = time.time()
    pending = [(str(t.get("id") or ""), str(t.get("label") or "timer"),
                float(t.get("due", 0)) - now)
               for t in _load() if float(t.get("due", 0) or 0) > now]
    return sorted(pending, key=lambda row: row[2])


def _load() -> List[dict]:
    try:
        parsed = json.loads(_data_path().read_text())
    except (OSError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _data_path() -> Path:
    """Where the timer store lives, resolved per call and never at import time.

    The module used to hold this as a module-level `_DATA`, captured when the
    module was first imported. That is the repo's recorded contamination lesson
    - a path resolved from `$HOME`/`$XDG_STATE_HOME` at import has already
    written into a real user's state directory in every process that imported it
    before `XDG_STATE_HOME` was pointed elsewhere, and `conftest.py` carries an
    autouse fixture per store for exactly this reason. Resolving late is also
    what lets a test point `XDG_STATE_HOME` at a temp dir at all.

    `_DATA` is kept as a name because other modules and older callers refer to
    it in messages, but nothing resolves through it any more.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / "timers.json"


def _save(timers: List[dict]) -> None:
    # mkdir then chmod, not mkdir(mode=...): the mode argument is masked by the
    # process umask and lands permissive without error. This directory was only
    # ever incidentally private, because `triggers.py` happened to chmod the same
    # parent - which stops being true the moment that runs first, or not at all.
    path = _data_path()
    files.ensure_private_dir(path.parent)
    path.write_text(json.dumps(timers, indent=1))
    files.restrict_file(path)


def _systemd_available() -> bool:
    if shutil.which("systemctl") is None:
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("systemd --user unavailable: %s", exc)
        return False
    if proc.returncode != 0:
        # A failed probe prints nothing, and "" is not "offline" - so the
        # stdout check alone called a broken systemd "available".
        logger.debug("systemd --user probe failed: %s", (proc.stderr or "").strip())
        return False
    # `running` and `degraded` are both usable; `offline` is not.
    return "offline" not in (proc.stdout or "")


def _schedule(identifier: str, seconds: int, label: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [
                "systemd-run", "--user", "--unit", f"{_PREFIX}-{identifier}",
                "--on-active", f"{seconds}s", "--timer-property=AccuracySec=1s",
                "--description", "Shani Chronoa countdown timer",
                "/bin/sh", "-c", _NOTIFY_TEMPLATE.format(label=_escape(label)),
            ],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"systemd-run could not be launched ({exc})."
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip() or "unknown error"
    return True, ""


def _unschedule(identifier: str) -> "tuple[bool, str]":
    """Stop the transient timer unit `<prefix>-<identifier>.timer`; (stopped, why not).

    Used to return nothing, which was enough for cancel but not for pause: a
    timer recorded as paused whose unit is still armed goes off anyway - the
    same lie as a timer recorded as set that never fires. A unit that is not
    loaded has nothing left to fire (it already went off, or the session was
    restarted), so that counts as stopped.
    """
    if shutil.which("systemctl") is None:
        return False, "systemctl is not installed"
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "stop", f"{_PREFIX}-{identifier}.timer"],
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


def _human(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _is_pending(timer: dict, now: float) -> bool:
    """Paused timers are pending (they have time left); running ones until due."""
    if timer.get("paused"):
        return True
    try:
        return float(timer.get("due") or 0) > now
    except (TypeError, ValueError):
        return False


def _left(timer: dict, now: float) -> int:
    if timer.get("paused"):
        try:
            return max(0, int(round(float(timer.get("remaining") or 0))))
        except (TypeError, ValueError):
            return 0
    return max(0, int(round(float(timer.get("due") or 0) - now)))


def _clock_at(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


def _name(timer: dict) -> str:
    return f"{timer.get('id')} ('{timer.get('label') or 'timer'}')"


def _new_unit(identifier: str) -> str:
    # A fresh unit name per countdown: re-arming under the old name can collide
    # with the old unit while systemd is still unloading it, and scheduling the
    # new one *before* stopping the old one is what keeps a failed re-arm from
    # losing the timer altogether.
    return f"{identifier}-{uuid.uuid4().hex[:4]}"


def set_timer(seconds: int, label: str) -> str:
    if seconds <= 0:
        return "Invalid timer duration: expected a positive number of seconds."
    if seconds > _MAX_SECONDS:
        return (
            f"Refusing a {seconds / 86400:.1f} day timer. A countdown that "
            f"long is a reminder, not a timer, and it would sit pending for a "
            f"week."
        )
    if not _systemd_available():
        return (
            "The timer was NOT set: systemd --user is not available in this "
            "session, so nothing could be scheduled to fire. A timer is only "
            "reported as set when something will actually fire it."
        )
    identifier = uuid.uuid4().hex[:8]
    scheduled, reason = _schedule(identifier, seconds, label or "timer")
    if not scheduled:
        return (
            f"The timer was NOT set: systemd refused to schedule it ({reason}). "
            f"Nothing was recorded, so there is no pending timer to list or "
            f"cancel."
        )
    now = time.time()
    timers = [t for t in _load() if _is_pending(t, now)]
    timers.append({
        "id": identifier,
        "label": label or "timer",
        "due": now + seconds,
        # The original length is what `reset` restarts from; a timer that has
        # been extended or paused no longer carries it anywhere else.
        "length": seconds,
    })
    _save(timers)
    return (
        f"Timer set for {_human(seconds)} ({seconds}s), id {identifier}"
        + (f", labelled '{label}'." if label else ".")
    )


def list_timers() -> str:
    now = time.time()
    pending = [t for t in _load() if _is_pending(t, now)]
    if not pending:
        return "No timers are pending."
    lines = ["Pending timers:"]
    for timer in sorted(pending, key=lambda t: _left(t, now)):
        state = "paused, " if timer.get("paused") else ""
        lines.append(
            f"  {timer['id']}  {timer.get('label', 'timer')}  "
            f"{state}{_human(_left(timer, now))} left"
        )
    return "\n".join(lines)


def cancel_timer(identifier: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{4,32}", identifier or ""):
        return (
            f"'{identifier}' is not a timer id. Use action=list to see the ids "
            f"of the timers that are pending."
        )
    timers = _load()
    match = [t for t in timers if t.get("id") == identifier]
    remaining = [t for t in timers if t.get("id") != identifier]
    if not match:
        return (
            f"No pending timer has the id {identifier} - it has either already "
            f"fired or was never set. Nothing was cancelled."
        )
    if not match[0].get("paused"):
        stopped, why = _unschedule(match[0].get("unit") or identifier)
        if not stopped:
            return (
                f"Timer {identifier} was NOT cancelled: systemd could not stop "
                f"it ({why}), so it is still pending and will go off."
            )
    _save(remaining)
    return f"Cancelled timer {identifier}."


def _pick(identifier: str, label: str) -> "tuple[dict | None, str]":
    """The one timer an id/label/"my timer" refers to, or (None, why not).

    "Pause my timer" with one timer pending means that timer; with several it
    means nothing in particular, and guessing would pause the wrong one.
    """
    now = time.time()
    pending = [t for t in _load() if _is_pending(t, now)]
    if not pending:
        return None, "No timers are pending."
    if identifier:
        found = [t for t in pending if t.get("id") == identifier]
        if not found:
            return None, (
                f"No pending timer has the id {identifier!r} - it has either "
                f"already fired or was never set."
            )
        return found[0], ""
    if label:
        found = [t for t in pending
                 if str(t.get("label") or "").casefold() == label.casefold()]
        if not found:
            return None, f"No pending timer is labelled {label!r}.\n" + list_timers()
        pending = found
    if len(pending) > 1:
        return None, (
            "Several timers are pending, so it is not clear which one you mean. "
            "Say which by id or label.\n" + list_timers()
        )
    return pending[0], ""


def _rearm(timers: list, entry: dict, seconds: int) -> str:
    """Run `entry` for `seconds` from now on a new unit, stopping the old one; '' or why not.

    The new countdown is scheduled first and the old one stopped second, so a
    failure at either step leaves exactly one countdown armed - never none
    (the timer silently lost) and never two (it goes off twice).
    """
    if not _systemd_available():
        return "systemd --user is not available in this session"
    unit = _new_unit(str(entry["id"]))
    scheduled, reason = _schedule(unit, seconds, entry.get("label") or "timer")
    if not scheduled:
        return f"systemd refused to schedule it ({reason})"
    if not entry.get("paused"):
        stopped, why = _unschedule(entry.get("unit") or entry["id"])
        if not stopped:
            _unschedule(unit)
            return f"systemd could not stop the old countdown ({why})"
    entry["unit"] = unit
    entry["due"] = time.time() + seconds
    entry["paused"] = False
    entry.pop("remaining", None)
    _save(timers)
    return ""


def _find(timers: list, chosen: dict) -> dict:
    return next(t for t in timers if t.get("id") == chosen.get("id"))


def pause_timer(identifier: str = "", label: str = "") -> str:
    chosen, why = _pick(identifier, label)
    if chosen is None:
        return why
    now = time.time()
    if chosen.get("paused"):
        return f"Timer {_name(chosen)} is already paused with {_human(_left(chosen, now))} left."
    timers = _load()
    entry = _find(timers, chosen)
    left = _left(entry, now)
    stopped, reason = _unschedule(entry.get("unit") or entry["id"])
    if not stopped:
        return (
            f"Timer {_name(entry)} was NOT paused: systemd could not stop it "
            f"({reason}). It is still running and goes off in {_human(left)}."
        )
    entry.update({"paused": True, "remaining": left, "due": 0})
    _save(timers)
    return (
        f"Paused timer {_name(entry)} with {_human(left)} left. It will not go "
        f"off until it is resumed."
    )


def resume_timer(identifier: str = "", label: str = "") -> str:
    chosen, why = _pick(identifier, label)
    if chosen is None:
        return why
    now = time.time()
    if not chosen.get("paused"):
        return (f"Timer {_name(chosen)} is not paused; it goes off in "
                f"{_human(_left(chosen, now))}.")
    timers = _load()
    entry = _find(timers, chosen)
    left = max(1, _left(entry, now))
    problem = _rearm(timers, entry, left)
    if problem:
        return (f"Timer {_name(entry)} was NOT resumed: {problem}. It is still "
                f"paused with {_human(left)} left.")
    return (f"Resumed timer {_name(entry)}: {_human(left)} left, it goes off at "
            f"{_clock_at(entry['due'])}.")


def add_to_timer(delta: int, identifier: str = "", label: str = "") -> str:
    if delta == 0:
        return "Adding 0 seconds changes nothing; pass seconds, e.g. seconds=300 to add five minutes."
    chosen, why = _pick(identifier, label)
    if chosen is None:
        return why
    now = time.time()
    timers = _load()
    entry = _find(timers, chosen)
    left = _left(entry, now)
    new = left + delta
    verb = "Added" if delta > 0 else "Took"
    amount = _human(abs(delta))
    if new <= 0:
        return (f"Timer {_name(entry)} has {_human(left)} left, so taking off "
                f"{amount} would leave nothing. It was not changed; use "
                f"action=cancel to end it.")
    if new > _MAX_SECONDS:
        return (f"Timer {_name(entry)} was not changed: {_human(new)} is longer "
                f"than a timer can run (7 days).")
    if entry.get("paused"):
        entry["remaining"] = new
        _save(timers)
        return (f"{verb} {amount} {'to' if delta > 0 else 'off'} paused timer "
                f"{_name(entry)}: it now has {_human(new)} left and is still paused.")
    problem = _rearm(timers, entry, new)
    if problem:
        return (f"Timer {_name(entry)} was NOT changed: {problem}. It still goes "
                f"off in {_human(left)}.")
    return (f"{verb} {amount} {'to' if delta > 0 else 'off'} timer {_name(entry)}: "
            f"{_human(new)} left, it now goes off at {_clock_at(entry['due'])}.")


def reset_timer(identifier: str = "", label: str = "") -> str:
    chosen, why = _pick(identifier, label)
    if chosen is None:
        return why
    try:
        length = int(chosen.get("length") or 0)
    except (TypeError, ValueError):
        length = 0
    if length <= 0:
        return (f"Timer {_name(chosen)} was set before its original length was "
                f"recorded, so it cannot be reset; it was not changed.")
    timers = _load()
    entry = _find(timers, chosen)
    left = _left(entry, time.time())
    problem = _rearm(timers, entry, length)
    if problem:
        state = "still paused" if entry.get("paused") else "still running"
        return (f"Timer {_name(entry)} was NOT reset: {problem}. It is {state} "
                f"with {_human(left)} left.")
    return (f"Reset timer {_name(entry)} to its original {_human(length)}: it "
            f"goes off at {_clock_at(entry['due'])}.")


def time_left(identifier: str = "", label: str = "") -> str:
    now = time.time()
    pending = [t for t in _load() if _is_pending(t, now)]
    if not identifier and not label and len(pending) > 1:
        return list_timers()
    chosen, why = _pick(identifier, label)
    if chosen is None:
        return why
    left = _left(chosen, now)
    if chosen.get("paused"):
        return f"Timer {_name(chosen)} is paused with {_human(left)} left."
    return (f"Timer {_name(chosen)} has {_human(left)} left; it goes off at "
            f"{_clock_at(float(chosen['due']))}.")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_timer",
        "description": (
            "Set a countdown timer that sends a desktop notification when it "
            "finishes; list pending timers, say how much time is left, add or "
            "take off time, pause, resume, reset to the original length, and "
            "cancel. With one timer pending, 'my timer' needs no id. The "
            "countdown is owned by systemd, so it fires even if the assistant "
            "is closed. It is refused rather than silently accepted when "
            "systemd is unavailable, because a timer recorded as pending that "
            "will never fire is the same lie as a camera reported as disabled "
            "when it is not."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set", "list", "cancel", "add", "pause", "resume",
                             "remaining", "reset"],
                    "description": (
                        "set a timer, list what is pending, cancel one, add "
                        "time to one (seconds; negative takes time off), "
                        "pause, resume, remaining (time left), or reset "
                        "(restart from its original length)."
                    ),
                    "default": "set",
                },
                "seconds": {
                    "type": "integer",
                    "description": (
                        "Duration in seconds. Required for action=set; for "
                        "action=add, the seconds to add (negative to subtract)."
                    ),
                },
                "label": {
                    "type": "string",
                    "description": (
                        "What the timer is for, e.g. 'pasta'. For the other "
                        "actions, picks the timer by its label."
                    ),
                },
                "id": {
                    "type": "string",
                    "description": (
                        "Timer id, from the list. Needed for cancel, add, "
                        "pause, resume, remaining and reset only when more "
                        "than one timer is pending."
                    ),
                },
            },
        },
    },
}

_ACTIONS = ("set", "list", "cancel", "add", "pause", "resume", "remaining", "reset")
#: What people say that the enum spells differently. Not advertised: the schema
#: stays the one vocabulary, this only stops a near-miss from being an error.
_ALIASES = {"delete": "cancel", "stop": "cancel", "remove": "cancel",
            "left": "remaining", "status": "remaining", "restart": "reset",
            "extend": "add", "continue": "resume", "unpause": "resume"}


def _seconds_arg(raw, allow_negative: bool) -> "tuple[int | None, str]":
    if isinstance(raw, bool):
        # isinstance(True, int) is True, so an unguarded int() turns a JSON
        # `true` into a 1-second timer. One second is never what was meant.
        return None, "Invalid timer duration: true/false is not a duration."
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    elif isinstance(raw, str) and re.fullmatch(
            r"[-+]?\d+" if allow_negative else r"\+?\d+", raw.strip()):
        value = int(raw.strip())
    else:
        return None, (
            "Invalid timer duration: expected a whole number of seconds, or a "
            "number of minutes, e.g. seconds=600."
        )
    if value < 0 and not allow_negative:
        return None, "Invalid timer duration: expected a positive number of seconds."
    return value, ""


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "The timer arguments were not an object; nothing was done."
    action = str(arguments.get("action") or "set").strip().lower()
    action = _ALIASES.get(action, action)
    raw_id = arguments.get("id")
    identifier = raw_id.strip() if isinstance(raw_id, str) else ("" if raw_id is None else str(raw_id))
    raw_label = arguments.get("label")
    label = raw_label.strip() if isinstance(raw_label, str) else ""
    if action == "list":
        return list_timers()
    if action == "cancel":
        if not identifier:
            chosen, why = _pick("", label)
            if chosen is None:
                return why
            identifier = str(chosen.get("id"))
        return cancel_timer(identifier)
    if action == "pause":
        return pause_timer(identifier, label)
    if action == "resume":
        return resume_timer(identifier, label)
    if action == "remaining":
        return time_left(identifier, label)
    if action == "reset":
        return reset_timer(identifier, label)
    if action == "add":
        raw = arguments.get("seconds")
        if raw is None:
            return ("No amount given. Pass seconds to add, e.g. seconds=300 for "
                    "five more minutes, or a negative number to take time off.")
        delta, problem = _seconds_arg(raw, allow_negative=True)
        if delta is None:
            return problem
        return add_to_timer(delta, identifier, label)
    if action != "set":
        return f"Unknown action {action!r}. Valid actions are: {', '.join(_ACTIONS)}."
    raw = arguments.get("seconds")
    if raw is None:
        return (
            "No duration given. Pass seconds, e.g. seconds=600 for ten "
            "minutes, or use action=list to see what is already pending."
        )
    seconds, problem = _seconds_arg(raw, allow_negative=False)
    if seconds is None:
        return problem
    return set_timer(seconds, str(arguments.get("label") or ""))


def _verify_timer(arguments: dict, tool=None):
    """Post-condition: does the timer store now hold what this call claimed?

    The store is the timers file, so the check is a read rather than a re-run.

    **This used to verify nothing correctly.** It parsed `timers.json` as JSON
    lines and looked for `at`/`when`/`fire_at`/`timestamp`, while `_save`
    writes one indented JSON *list* whose fire time is `due`. Every successful
    `set_timer` was therefore reported FAILED ("holds no timer at all") -
    measured, not inferred: a store holding one 600 s 'pasta' timer returned
    `(False, 'timers.json holds no timer at all')`.

    Only `set`, `pause` and `cancel` are checked; the other actions either
    change nothing (list, remaining) or re-arm a countdown whose new due time
    the reply already states, and None ("nothing checkable") is the honest
    answer for them rather than a manufactured pass.
    """
    if not isinstance(arguments, dict):
        return None
    action = str(arguments.get("action") or "set").strip().lower()
    action = _ALIASES.get(action, action)
    timers = _load()
    now = time.time()
    identifier = str(arguments.get("id") or "").strip()
    if action == "set":
        seconds = arguments.get("seconds")
        try:
            wanted = float(seconds)
        except (TypeError, ValueError):
            return None
        if wanted <= 0:
            return None
        if not timers:
            return (False, f"{_DATA_NAME} holds no timer at all")
        newest = timers[-1]
        label = str(arguments.get("label") or "")
        if label and str(newest.get("label") or "") != label:
            return (False, f"the newest timer is for {newest.get('label')!r}, not {label!r}")
        left = _left(newest, now)
        if not 0 < left <= wanted + 1:
            return (False, f"the newest timer has {left}s left, against {wanted:.0f}s asked for")
        return (True, f"the newest entry in {_DATA_NAME} goes off in {left}s "
                      f"for label {newest.get('label') or '(none)'!r}, against "
                      f"{wanted:.0f}s asked for")
    if action == "pause" and identifier:
        entry = next((t for t in timers if t.get("id") == identifier), None)
        if entry is None:
            return None
        return (bool(entry.get("paused")),
                f"timer {identifier} is {'paused' if entry.get('paused') else 'still running'} in {_DATA_NAME}")
    if action == "cancel" and identifier:
        gone = all(t.get("id") != identifier for t in timers)
        return (gone, f"timer {identifier} is {'absent from' if gone else 'still in'} {_DATA_NAME}")
    return None


POST_CONDITION = _verify_timer

SKILLS = [Skill(name="set_timer", schema=SCHEMA, run=_run)]
