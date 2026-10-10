"""Skill: is one CPU core busy while the machine looks idle?

`system_info` says how many CPUs there are and `cpu_frequency` says what the
governor is doing, but neither says **which** cores are carrying the load.
That is a different question and it has a different answer: on an eight-core
machine, one process pinning one core looks like an idle machine in every
system-wide average, and "Chronoa feels slow but nothing looks busy" is
exactly what it looks like from here.

`mpstat` from `sysstat` reports per-CPU percentages, and `sysstat` is in
`shani-tools-extra`, so it is on the images by design - and `disk_activity`
already reads `iostat` from the same package.

**Measured on this machine**, and two shapes matter:

    10:47:11 AM  CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
    10:47:12 AM  0     10.69    0.00    1.13    0.00    0.00    0.00    0.00    0.00    0.00   88.18
    10:47:12 AM  all    9.31    0.00    1.01    0.38    0.00    0.13    0.00    0.00    0.00   89.18

- **`mpstat 1 2` prints two reports and the LAST is the current one** - the
  same since-boot-vs-now trap as `iostat -x`, and the same reason the first
  report is history rather than a measurement.
- **`all` is a row, not a core.** `all` aggregates the machine, so a parser
  that treats it as core 0 reports the average as one core's load and, worse,
  can miss that the *other* seven are idle while one is pegged - which is the
  entire question this skill exists to answer.
- **An `Average:` row appears last**, and it is a third thing again: neither
  the first report nor the last. Three reports' worth of rows in one output,
  and only one of them is the now.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30


def _sample() -> "tuple[list, tuple, int, str]":
    """(rows, header_cells, label_offset, reason).

    The column layout is returned rather than kept private because `_run` has
    to index the rows by the header's own names - a fixed index assumes
    sysstat printed its default column set, and when it prints a different
    one a hardcoded index reads a neighbouring percentage: a number of the
    right shape and the wrong meaning.

    `reason` is empty only when the sample is real; every other path is a
    refusal, because a CPU table with a hole in it reads as a quiet machine.
    """
    try:
        # **`-P ALL` is required, not optional.** Measured on this machine:
        # plain `mpstat 1 2` prints **only** an `all` row - one line for the
        # whole machine - and exits 0, so a parser reading it finds no cores
        # and the skill refuses on every 8-core machine. With `-P ALL` the
        # same binary prints one row per core. Both forms exit 0, so the exit
        # status distinguishes nothing here.
        proc = subprocess.run(["mpstat", "-P", "ALL", "1", "2"],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return [], [], 0, f"mpstat did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return [], [], 0, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return [], [], 0, (detail[-1] if detail else f"mpstat exited {proc.returncode}")

    reports, current = [], []
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                reports.append(current)
                current = []
            continue
        current.append(line)
    if current:
        reports.append(current)
    if not reports:
        return [], [], 0, "mpstat printed nothing"

    # The LAST report, and only that one: the first is since boot, and the
    # trailing `Average:` line belongs to neither.
    #
    # **The timestamp is the first cell, not the CPU label.** Measured: every
    # row is `10:49:14 AM  CPU  %usr ...` / `10:49:15 AM  all  10.69 ...`,
    # so `cells[0]` is a clock and `cells[1]` is the label. A first version
    # read `cells[0]` as the label, found `10:49:14` was not `all`, treated it
    # as a core, and then failed to read its percentages - so the skill
    # reported "no per-CPU rows" on every machine, which is what running it
    # showed. The row is identified by *position*: the header tells us how
    # many columns precede the numbers.
    # Find the header once, anywhere in the output: it names the columns, and
    # the row shape follows from it.
    #
    # **Identified by `CPU` plus any `%` column, not by `%idle`.** Requiring
    # `%idle` was wrong in two directions at once: a sysstat build that prints
    # a narrower set has no `%idle` at all and the whole read failed with
    # "no header naming its columns" - refusing on a machine that had given a
    # perfectly readable table.
    header_cells = None
    for report in reports:
        for line in report:
            cells = line.split()
            if "CPU" in cells and any(c.startswith("%") for c in cells):
                header_cells = cells
                break
        if header_cells:
            break
    if not header_cells:
        return [], [], 0, "mpstat printed no header naming its columns"
    # **The label sits where `CPU` sits in the header, and the offset is not
    # fixed.** Measured on this box the clock is two cells - `10:49:39` and
    # `AM` - so `CPU` is at index 2; under a 24-hour locale it is one cell and
    # `CPU` is at index 1. Deriving the offset from the first `%` minus one
    # works only for the first shape and produced a core labelled `10.37`
    # when the locale was the other. The header is the authority on its own
    # layout, so it is asked.
    label_offset = header_cells.index("CPU")
    if label_offset < 1:
        return [], "mpstat's header did not name a per-CPU label column"

    # The live report is **the one before the `Average:` block**, and it is
    # neither the first report (since boot) nor the last (an average of
    # everything sampled). Measured here, `-P ALL` produces four
    # blank-line-separated reports: the banner, the first interval, the
    # second interval, and the `Average:` block. Taking the last gives a
    # report whose label column reads `Average:`; taking the first reports a
    # machine's history.
    live = None
    for report in reports:
        # **A report is the Average block only when its FIRST line says so.**
        # Measured: when mpstat prints one interval, that interval and its
        # `Average:` rows share a single blank-line-separated report - so
        # testing every line for `Average:` skipped the only real report and
        # the skill answered "no interval report (only a banner)" on a
        # machine that had given a complete table.
        first = report[0].split() if report else []
        if first and first[0] == "Average:":
            break
        if any("CPU" in line.split() for line in report):
            live = report
    if live is None:
        return [], [], 0, "mpstat printed no interval report (only a banner)"

    rows = []
    # **The label offset is re-derived per row**, by finding the one cell that
    # is a CPU number (`all`, or `0`-`N`) immediately before the values.
    #
    # Measured: `mpstat -P ALL` and plain `mpstat` use *different* offsets for
    # the label - with `-P ALL` the label is at the header's `CPU` index, and
    # without it the `all` row puts its label one cell earlier still. Taking
    # the header's index for both produced a "core" named `9.61`, which is a
    # percentage of the `all` row. So the label is found by shape: the last
    # cell before the run of numbers, and it must be `all` or digits.
    for line in live:
        cells = line.split()
        numbers = [i for i, c in enumerate(cells) if _is_number(c)]
        if len(numbers) < 2:
            continue
        # **The clock is a number too** (`10:50:28` parses as a float), so the
        # first numeric cell is the *time*, not the first value. The label is
        # the cell before the *value run* - found by walking back from the end
        # to the last cell that is `all` or a bare integer.
        label_index = None
        for i in range(len(cells) - 1, -1, -1):
            if cells[i] == "all" or cells[i].isdigit():
                label_index = i
            elif label_index is not None:
                break
        if label_index is None:
            continue
        # Everything after the label must be values, or this is a header or a
        # banner rather than a reading.
        if not all(_is_number(c) for c in cells[label_index + 1:]):
            continue
        label = cells[label_index]
        if label == "all":
            # **An aggregate, not a core.** Counted here rather than in `_run`
            # so the "no per-CPU rows" refusal below fires when *every* row is
            # `all` - exactly what plain `mpstat` produces, and the honest
            # answer there is that no core was measured.
            continue
        values = [float(c) for c in cells[label_index + 1:]]
        rows.append((label, values))
    if not rows:
        return [], [], 0, ("mpstat reported no per-CPU rows - only an "
                           "aggregate, which is not a measurement of any core")
    return rows, header_cells, label_offset, ""


def _is_number(cell: str) -> bool:
    try:
        float(cell)
    except ValueError:
        return False
    return True


def _run(_arguments: dict) -> str:
    if shutil.which("mpstat") is None:
        return files.tool_missing("mpstat", "see which processor cores are busy")

    rows, header_cells, label_offset, problem = _sample()
    if problem:
        return f"I could not read the per-core CPU usage: {problem}. Nothing was guessed."

    # **Columns are read from the header, not counted.** A fixed index
    # assumes mpstat's default column set, and sysstat can print a different
    # one - at which point a hardcoded index silently reads the neighbouring
    # percentage, which is a number of the right shape and the wrong meaning.
    # `_sample()` returns rows already trimmed to the label, so the header's
    # own names index them directly.
    def value(row, name):
        """Read a column **by the header's own name**.

        The header's columns are counted from `CPU`, and `values` begins at
        the first percentage, so the two line up by position - which is what
        makes a *different* sysstat column set read correctly instead of
        shifting every value left by however many columns it omits.
        """
        try:
            index = header_cells.index(name) - (label_offset + 1)
        except ValueError:
            return None
        values = row[1]
        return values[index] if 0 <= index < len(values) else None

    user = "%usr"
    iowait = "%iowait"
    idle = "%idle"
    lines = []
    busy = []
    for row in rows:
        label = row[0]
        pct_idle = value(row, idle)
        pct_user = value(row, user)
        pct_iowait = value(row, iowait)
        if pct_idle is None or pct_user is None:
            lines.append(f"  core {label}: this mpstat did not print the "
                         "columns this reads")
            continue
        used = 100.0 - pct_idle
        detail = f"core {label}: {used:5.1f}% in use ({pct_user:5.1f}% user)"
        if pct_iowait is not None and pct_iowait > 0.5:
            detail += f", {pct_iowait:.1f}% waiting on disk"
            busy.append((label, "waiting on disk", pct_iowait))
        elif used >= 80.0:
            detail += ", pegged"
            busy.append((label, "pegged", used))
        lines.append(detail)

    if not any("did not print" in line for line in lines):
        header = f"{len(rows)} core(s), over the last second:"
        lines.insert(0, header)

    if busy:
        lines.append("")
        if any(why == "waiting on disk" for _, why, _ in busy):
            lines.append("A core with time in %iowait is waiting on a disk, "
                         "not computing - that is a slow-storage answer, not "
                         "a busy-CPU one. `disk_activity` says which disk.")
        if any(why == "pegged" for _, why, _ in busy):
            pegged = [label for label, why, _ in busy if why == "pegged"]
            lines.append(f"Busy right now: {', '.join(pegged)} at 80% or "
                         "more. A machine can look idle in every average and "
                         "still be pinned on one core.")
    else:
        lines.append("")
        lines.append("No single core is above 80%, so nothing here is "
                     "saturated.")

    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "cpu_per_core",
        "description": (
            "Which individual processor cores are busy right now, and how "
            "much of each is user time versus waiting on disk. Use when the "
            "machine feels slow but nothing looks busy - a single process "
            "pinning one core looks idle in every system-wide average. "
            "Read-only; starts nothing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="cpu_per_core", schema=SCHEMA, run=_run)]