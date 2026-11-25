"""Skill: read what a service or the kernel has been saying.

Log reading is a person's most common diagnostic step after "this stopped
working", and it is the step the assistant previously could not take at all.

Bounded on purpose: a journal with no limit is an unbounded amount of context
for a reply nobody can read, so this defaults to the last 20 lines and says when
it cut the output.

Honesty rules:

- **An empty journal is reported as empty, not as "no problems".** Those are
  different, and the first is a fact while the second is a conclusion this
  cannot reach.
- Permission denied reading the system journal is reported as such, because
  `/var/log/journal` is root-only on many distributions and a user asking "what
  did it say" would otherwise conclude the service is silent.
- Line counts are the journal's own, so a truncated read says how much of the
  line was actually delivered.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 25
_MAX_LINES = 400
_MAX_CHARS = 6000

SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_logs",
        "description": (
            "Read recent log output from a named system service, or from the "
            "kernel when no service is named. Returns the most recent lines "
            "first-context-last, and says how much it did not show."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "unit": {
                    "type": "string",
                    "description": (
                        "Service name, e.g. 'sshd' or 'sshd.service'. Omit to "
                        "read the kernel journal."
                    ),
                },
                "lines": {"type": "integer", "description": f"How many lines. Defaults to 20, maximum {_MAX_LINES}."},
                "priority": {
                    "type": "string",
                    "description": (
                        "Only entries at or above this level: emerg, alert, "
                        "crit, err, warning, notice, info, debug."
                    ),
                },
                "since": {
                    "type": "string",
                    "description": "Only entries newer than this, e.g. '-30min', '-2h', 'today'.",
                },
            },
        },
    },
}

_PRIORITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


def _run(arguments: dict) -> str:
    if shutil.which("journalctl") is None:
        return files.tool_missing("journalctl", "read the system log")

    try:
        want = max(1, min(int(arguments.get("lines") or 20), _MAX_LINES))
    except (TypeError, ValueError):
        want = 20
    unit = (arguments.get("unit") or "").strip()
    priority = (arguments.get("priority") or "").strip().lower()
    since = (arguments.get("since") or "").strip()

    if priority and priority not in _PRIORITIES:
        return f"Priority must be one of {', '.join(_PRIORITIES)}, not {priority!r}."

    target = f"unit {unit}" if unit else "the kernel journal"
    if unit and not unit.endswith(".service") and not unit.endswith(".socket") \
            and not unit.endswith(".target") and "." not in unit:
        unit += ".service"
        target = f"unit {unit}"

    args = ["journalctl", "--no-pager", "--lines", str(want), "--output=short-iso"]
    if unit:
        args += ["--unit", unit]
    else:
        args += ["-k"]   # not "--kernel": journalctl rejects that outright
    if priority:
        args += ["--priority", priority]
    if since:
        # Passed through as one argv element, never interpolated into a shell
        # string, so a value like '; rm -rf /' is just an invalid date.
        args += ["--since", since]

    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"journalctl did not answer within {_TIMEOUT}s, so {target} was not read."
    except OSError as exc:
        return f"Could not read {target}: {exc}"

    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        joined = " ".join(err).lower()
        if "permission denied" in joined or "not readable" in joined:
            return (
                f"Could not read {target}: permission denied. The system journal "
                f"is root-only on many distributions, so this is not evidence "
                f"that the service is silent - it is evidence that this user "
                f"cannot read it. Try 'sudo journalctl' yourself."
            )
        return f"journalctl could not read {target} (exit {proc.returncode})" + (
            f": {err[-1]}" if err else ".")

    raw_lines = [l for l in proc.stdout.splitlines() if l.strip()]
    # journalctl exits 0 and prints this one placeholder for a journal with
    # nothing in it. Treating it as a log line would show the user a literal
    # "-- No entries --" as though it were the kernel's own output.
    if raw_lines == ["-- No entries --"] or raw_lines == ["-- Journal begins at ..."]:
        raw_lines = []
    lines = [l for l in raw_lines if not l.startswith("-- Journal ")]
    if not lines:
        scope = f" for {unit}" if unit else ""
        extra = f" at priority {priority} or above" if priority else ""
        if since:
            extra += f" since {since}"
        return (
            f"The journal{scope} has no entries{extra}. That is an empty "
            f"journal, not a claim that nothing is wrong - the unit may not "
            f"have logged, or may not exist."
        )

    truncated = len(lines) >= want
    body = "\n".join(lines)
    cut = ""
    if len(body) > _MAX_CHARS:
        body = body[:_MAX_CHARS]
        cut = f"\n  ... cut at {_MAX_CHARS} characters; ask for fewer lines."
    head = f"{target}: {len(lines)} line(s) shown"
    if truncated:
        head += f" (asked for {want}, so there are older entries not shown)"
    return f"{head}\n{body}{cut}"


SKILLS = [Skill(name="read_logs", schema=SCHEMA, run=_run)]
