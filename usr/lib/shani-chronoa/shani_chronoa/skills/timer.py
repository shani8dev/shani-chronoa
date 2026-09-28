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
from pathlib import Path
from typing import List

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_DATA = (
    Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    / "shani-chronoa" / "timers.json"
)
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


def _load() -> List[dict]:
    try:
        parsed = json.loads(_DATA.read_text())
    except (OSError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _save(timers: List[dict]) -> None:
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    _DATA.write_text(json.dumps(timers, indent=1))


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


def _unschedule(identifier: str) -> None:
    if shutil.which("systemctl") is None:
        return
    subprocess.run(
        ["systemctl", "--user", "stop", f"{_PREFIX}-{identifier}.timer"],
        capture_output=True, text=True, timeout=_TIMEOUT, check=False,
    )


def _human(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


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
    timers = [t for t in _load() if t.get("due", 0) > time.time()]
    timers.append({
        "id": identifier,
        "label": label or "timer",
        "due": time.time() + seconds,
    })
    _save(timers)
    return (
        f"Timer set for {_human(seconds)} ({seconds}s), id {identifier}"
        + (f", labelled '{label}'." if label else ".")
    )


def list_timers() -> str:
    pending = [t for t in _load() if t.get("due", 0) > time.time()]
    if not pending:
        return "No timers are pending."
    now = time.time()
    lines = ["Pending timers:"]
    for timer in sorted(pending, key=lambda t: t["due"]):
        lines.append(
            f"  {timer['id']}  {timer.get('label', 'timer')}  "
            f"{_human(timer['due'] - now)} left"
        )
    return "\n".join(lines)


def cancel_timer(identifier: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{4,32}", identifier or ""):
        return (
            f"'{identifier}' is not a timer id. Use action=list to see the ids "
            f"of the timers that are pending."
        )
    timers = _load()
    remaining = [t for t in timers if t.get("id") != identifier]
    if len(remaining) == len(timers):
        return (
            f"No pending timer has the id {identifier} - it has either already "
            f"fired or was never set. Nothing was cancelled."
        )
    _unschedule(identifier)
    _save(remaining)
    return f"Cancelled timer {identifier}."


SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_timer",
        "description": (
            "Set a countdown timer that sends a desktop notification when it "
            "finishes; also list pending timers and cancel one by id, because a "
            "timer you cannot call off is not much of a timer. The countdown is "
            "owned by systemd, so it fires even if the assistant is closed. It "
            "is refused rather than silently accepted when systemd is "
            "unavailable, because a timer recorded as pending that will never "
            "fire is the same lie as a camera reported as disabled when it is "
            "not."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["set", "list", "cancel"],
                    "description": "Set a timer, list what is pending, or cancel one.",
                    "default": "set",
                },
                "seconds": {
                    "type": "integer",
                    "description": "Duration in seconds. Required for action=set.",
                },
                "label": {
                    "type": "string",
                    "description": "What the timer is for, e.g. 'pasta'.",
                },
                "id": {
                    "type": "string",
                    "description": "Timer id, from the list. Required for action=cancel.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    action = str(arguments.get("action") or "set").strip().lower()
    if action == "list":
        return list_timers()
    if action == "cancel":
        return cancel_timer(str(arguments.get("id") or ""))
    if action != "set":
        return f"Unknown action {action!r}. Valid actions are: set, list, cancel."
    raw = arguments.get("seconds")
    if raw is None:
        return (
            "No duration given. Pass seconds, e.g. seconds=600 for ten "
            "minutes, or use action=list to see what is already pending."
        )
    if isinstance(raw, bool):
        # isinstance(True, int) is True, so an unguarded int() turns a JSON
        # `true` into a 1-second timer. One second is never what was meant.
        return "Invalid timer duration: true/false is not a duration."
    if isinstance(raw, int):
        seconds = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        seconds = int(raw.strip())
    else:
        return (
            "Invalid timer duration: expected a whole number of seconds, or a "
            "number of minutes, e.g. seconds=600."
        )
    return set_timer(seconds, str(arguments.get("label") or ""))


SKILLS = [Skill(name="set_timer", schema=SCHEMA, run=_run)]
