"""Skill: what was this machine doing earlier today?

`disk_activity` (iostat) and `cpu_per_core` (mpstat) both answer **right
now**. There is no way to ask either "was this thing busy at lunchtime", and
that is the question people actually have about a machine that misbehaved
overnight - the fan noise, the laptop that got hot, the sync that ran at
some hour nobody was watching.

`sysstat` - which ships `iostat` and `mpstat`, already in use - also ships
`sar`, which reads the per-day accounting files the collector writes under
`/var/log/sa`. Nothing in Chronoa read them.

**Measured on a real `@blue` slot (sar 12.x), the report verbatim:**

    Linux 7.2.6-arch2-1 (shanios) 	10/10/26 	_x86_64_	(8 CPU)

    06:40:39        CPU     %user     %nice   %system   %iowait    %steal     %idle
    06:40:40        all     20.46      0.00      7.12      0.00      0.00     72.43
    06:40:41        all     20.72      0.00      6.44      0.00      0.00     72.84
    Average:        all     20.59      0.00      6.78      0.00      0.00     72.63

**Three things about that shape that a parser gets wrong by assumption:**

- **The header is not the first line.** sar prints a machine banner
  (`Linux <kernel> (<host>) <date> <arch> (<n> CPU)`) and then a **blank
  line** before the column header. Splitting the first line into fields
  yields the kernel name and nothing else.
- **The last line is `Average:`** and it is the one worth quoting - it is
  the whole measurement, where the rows above it are samples. A reader that
  takes the *last* row works by accident; one that takes the first row
  reports a single second as the day's load.
- **`CPU` is a column of its own and `all` is one of its values.** `sar -u`
  can print one row per core, so the column decides how many rows there
  are, and `all` may or may not be present among them.

**And the precondition, which is the honest part of this skill.** The
accounting files only exist if the collector has run. Where it has not,
`sar` exits non-zero or prints an empty report, and *that* is not "the
machine was idle" - it is "nobody was watching". Those are opposite claims
and the skill keeps them apart.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30

#: `Linux 7.2.6-arch2-1 (shanios) 10/10/26 _x86_64_ (8 CPU)` - the banner
#: above the table, and the reason the first line is not the header.
#:
#: **Redundant on this banner, and kept deliberately.** Measured: it contains
#: no `%`, does not begin with a time, and does not match `_AVERAGE`, so all
#: three of the other branches reject it on their own and removing this guard
#: leaves the suite green. It is defence in depth against a banner that grows
#: a `%` or a leading time, not a load-bearing rule - which is why the test
#: that covers it asserts on the parse, not on the guard existing.
_BANNER = re.compile(r"^Linux\s+\S+\s+\([^)]*\)\s+\S+\s+\S+\s+\(\d+\s+CPU\)$")

#: `Average:        all     20.59      0.00      6.78      0.00      0.00     72.63`
#: `Linux ... (8 CPU)` also ends in a run of numbers, so the label word is
#: required - without it the banner parses as an average.
_AVERAGE = re.compile(r"^Average:\s+(\S+)\s+(.*)$")

#: `06:40:40        all     20.46 ...`
_SAMPLE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(\S+)\s+(.*)$")

#: `%user`, `%idle`, `%iowait` - a percent column, so the header names them
#: and the row's values are positional against it.
_PERCENT = re.compile(r"^-?\d+(\.\d+)?$")


#: `06:40:39        CPU     %user     %nice   %system ...` - the header. Its
#: **first token is a time-format label, not a column**, because that cell
#: holds `06:40:40` on the rows below. So the header line is the same shape as
#: a data row, which is what makes it easy to mistake for one - and getting
#: it wrong shifts every value onto its neighbour's name:
#:
#:     columns[1:] = ['CPU', '%user', '%nice', ...]
#:     values      = ['20.59', '0.00',  '0.00',  ...]
#:     -> "CPU 20.6%, user 0.0%"      # %user's figure, labelled "CPU"
#:
#: Every number is right and every label is off by one.
_TIME_LABEL = re.compile(r"^\d{2}:\d{2}:\d{2}$")


def _run(arguments: "list[str]") -> "tuple[str, str, int]":
    try:
        proc = subprocess.run(arguments, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"sar did not answer within {_TIMEOUT}s", 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def _history_days() -> "list[str]":
    """The accounting files present, newest first.

    Read from the directory rather than inferred from sar's exit code,
    because "no file" and "an old file" are different states and only the
    first of them means nobody was watching.
    """
    sa = Path("/var/log/sa")
    try:
        days = sorted((p.name for p in sa.glob("sa[0-9][0-9]")),
                      reverse=True)
    except OSError:
        return []
    return days


def _parse(text: str) -> "tuple[dict, list]":
    """(columns, rows). Rows carry their time or the word `Average`."""
    columns: list = []
    rows: list = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _BANNER.match(stripped):
            # The banner is not the header, and it is not a row.
            continue
        match = _AVERAGE.match(stripped)
        if match:
            rows.append({"when": "Average", "cpu": match.group(1),
                         "values": match.group(2).split()})
            continue
        if "%" in stripped and not _PERCENT.match(stripped.split()[0]
                                                  if stripped.split() else ""):
            columns = stripped.split()
            # **Drop the leading time-format token.** Without this, `CPU`
            # becomes the name of %user's figure - and the resulting sentence
            # reads perfectly, which is what made it worth catching.
            if columns and _TIME_LABEL.match(columns[0]):
                columns = columns[1:]
            continue
        match = _SAMPLE.match(stripped)
        if match:
            rows.append({"when": match.group(1), "cpu": match.group(2),
                         "values": match.group(3).split()})
    return columns, rows


def _explain(columns: "list[str]", values: "list[str]") -> str:
    pairs = []
    for name, raw in zip(columns[1:], values):
        try:
            number = float(raw)
        except ValueError:
            continue
        pairs.append((name.strip("%"), number))
    return ", ".join(f"{name} {value:.1f}%" for name, value in pairs)


def _run_skill(arguments: dict) -> str:
    if shutil.which("sar") is None:
        return files.tool_missing("sar", "see what this machine was doing earlier")

    raw_topic = str(arguments.get("topic") or "").strip().lower()
    topic = raw_topic or "u"
    if topic not in ("u", "d", "r", "b"):
        return (f"{raw_topic!r} is not a report this knows how to read. "
                "`u` is CPU, `d` is disk, `r` is memory, `b` is paging.")

    flags = {"u": ["-u"], "d": ["-d"], "r": ["-r"], "b": ["-b"]}[topic]
    names = {"u": "CPU use", "d": "disk activity", "r": "memory use",
             "b": "paging"}

    days = _history_days()
    out, err, rc = _run(["sar", *flags])

    if rc == 124:
        return f"sar did not answer within {_TIMEOUT}s."
    if rc != 0 and not out.strip():
        detail = [line for line in err.splitlines() if line.strip()]
        # **This is the common case on a machine whose collector has never
        # run**, and it is the opposite of "the machine was idle".
        if not days:
            return (
                f"There is **no accounting history** on this machine - sar has "
                f"nothing recorded to read, so I cannot say what {names[topic]} "
                f"looked like earlier"
                + (f".\n\nsar said: {detail[-1]}" if detail else ".")
                + "\n\nThis is not the same as the machine having been idle. "
                  "`sysstat`'s collector has to run to write those files, and "
                  "on this one it has not. What it is doing *now* is a different "
                  "question, which I can answer.")
        return (f"sar could not read the {names[topic]} history: "
                f"{detail[-1] if detail else f'exit {rc}'}")

    columns, rows = _parse(out)
    if not rows:
        return (
            f"sar reported no {names[topic]} samples. "
            + (f"The accounting files present are {', '.join(days)} - that is "
               f"history, and it is not empty for other topics, so this is a "
               f"gap in the record rather than an idle machine."
               if days else
               "There are no accounting files under /var/log/sa at all, so "
               "nobody has been recording this - which is not the same as the "
               "machine having been idle."))

    # The average is the measurement. A sample is one second of it.
    average = next((row for row in rows if row["when"] == "Average"), None)
    chosen = average or rows[-1]
    when = "the recorded average" if average else f"the last sample at {chosen['when']}"

    lines = [f"{names[topic].capitalize()} over {when}: "
             f"{_explain(columns, chosen['values']) or 'no readable values'}."]

    if average is None and len(rows) > 1:
        busiest = max(rows, key=lambda r: _busy_value(columns, r))
        if busiest["when"] != chosen["when"]:
            lines.append(f"The busiest recorded moment was {busiest['when']}: "
                         f"{_explain(columns, busiest['values'])}.")

    lines.append("")
    if days:
        lines.append(f"Accounting files present: {', '.join(days[:7])}"
                     + (f" (and {len(days) - 7} more)" if len(days) > 7 else "")
                     + ". How long ago is a question I can narrow down - ask "
                       "again with a day in mind if you want a specific one.")
    else:
        lines.append("No accounting files are present, so this is the "
                     "collection since sar was asked rather than history.")
    return "\n".join(lines)


def _busy_value(columns: "list[str]", row: dict) -> float:
    """Highest non-idle percentage - what "busiest" means per topic."""
    try:
        idle_at = [i for i, name in enumerate(columns) if name == "%idle"]
    except ValueError:
        idle_at = []
    values = row["values"]
    if idle_at and idle_at[0] < len(values):
        try:
            return 100.0 - float(values[idle_at[0]])
        except ValueError:
            pass
    numbers = []
    for raw in values:
        try:
            numbers.append(float(raw))
        except ValueError:
            continue
    return max(numbers) if numbers else 0.0


SCHEMA = {
    "type": "function",
    "function": {
        "name": "system_history",
        "description": (
            "What this machine's CPU, disk, memory or paging was doing earlier, "
            "from the sysstat accounting records rather than a live reading. "
            "Use for 'was this busy last night', 'what was using the disk at "
            "lunchtime', 'why was the fan loud earlier'. Says plainly when no "
            "history has been recorded, because that is not the same as the "
            "machine having been idle. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["u", "d", "r", "b"],
                    "description": ("u CPU (default), d disk, r memory, "
                                    "b paging."),
                },
            },
        },
    },
}

SKILLS = [Skill(name="system_history", schema=SCHEMA, run=_run_skill)]