"""Skill: which program is actually costing me this machine right now?

`list_processes` reports a **snapshot** - the CPU and memory a process holds at
one instant. `disk_activity` reports the **device** totals. Between them, three
questions had no answer anywhere in the tree, and all three were checked against
it rather than assumed:

    grep -rn "majflt|minflt|major fault|thrashing" usr/lib/... -> nothing
    per-process disk read/write                          -> nothing

1. **Time-weighted CPU, not an instantaneous reading.** A process at 99% for a
   second and a process that touched 99% for one tick look identical in a
   snapshot and are not the same event. `pidstat` samples, so the number is an
   average over a real interval.
2. **User versus system time**, which splits "burning CPU in userspace" from
   "burning CPU in the kernel" - a program stuck in a syscall loop looks like
   the same thing as one computing, and they are fixed differently.
3. **Page faults, and especially *major* faults** - the ones that had to read
   from disk. A process taking thousands of them a second is thrashing, which is
   the answer to "why did everything just stop" that nothing else can give.
4. **Per-process disk read and write rates.** `disk_activity` says *a disk* is
   busy; this says *which program* is the reason.

**`pidstat` is the right tool here even though this package prefers `/proc`,**
and the reason is worth stating because it is a departure. A `/proc` reader can
take one snapshot; time-weighting needs two samples a second apart, and
re-implementing sysstat's sampling (and its `CLOCK_TICK`-derived accounting) is
the kind of machinery that is wrong in a way nobody notices. `sysstat` is on
**both** ShaniOS images, pulled in by `shani-tools-extra` - measured out of
`chronoa-matrix.json`, where `pidstat` carries `pulled_in_by` naming it - which
is the case where a binary-backed skill is the right answer rather than a
liability.

**Measured on this box**, so the parse is against real output and not a man page:

    08:40:01 PM     0        43    0.00    0.98    0.00    0.00    0.98     4  ksoftirqd/4
    08:40:01 PM  1001    4145   11.76    3.92    0.00    0.00   15.69     5  gnome-shell
    08:40:01 PM  1001   41493   99.02    0.00    0.00    0.00   99.02     0  python3

Three things that table is easy to get wrong, all of them measured:

- **The `Command` column is truncated to 15 characters** - the same trap
  `open_files` documents for lsof's 9. `gnome-terminal-` is `gnome-terminal-server`,
  and reading that string as the program name names a program that does not
  exist. The full name is read back from `/proc/<pid>/comm`.
- **`pidstat` prints one table per report and repeats the banner**, and
  `Average:` rows follow. Taking the last line rather than the last *table*
  yields an average row attributed to a program, which is a different claim.
- **A process at 99% can be several processes.** Measured: three `python3` at
  99.02 each, on three different CPUs. The CPU column is *which* core ran it,
  not how many - so a single high row is not the whole story and the skill
  groups by name for exactly that reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

#: Two samples, so the answer is an interval and not since-boot history - the
#: same trap `disk_activity` documents for `iostat`.
_INTERVAL = "1"
_SAMPLES = "2"
_TIMEOUT = 30

#: A process this busy is worth naming at the top; below that the table is
#: noise. 1.0% of one core is roughly what an idle-but-visible process costs.
_BUSY = 1.0
#: A *major* fault had to read from disk. A process taking this many a second
#: is thrashing rather than working - measured at 0 on an idle machine and in
#: the hundreds under memory pressure.
_THRASHING = 10.0


class Cost(NamedTuple):
    """One sampled process, from one `pidstat` table."""

    pid: int
    command: str
    usr: float
    system: float
    cpu: float
    rss_kb: float
    pct_mem: float
    minflt: float
    majflt: float
    kb_read: float
    kb_written: float


def _number(cell: str) -> "Optional[float]":
    """A percentage or a rate, or None when it is not one.

    `pidstat` prints `?` for a field it cannot compute, so a parser that calls
    `float()` on every cell raises on a perfectly healthy reading.
    """
    try:
        return float(cell)
    except (TypeError, ValueError):
        return None


def _full_name(pid: int, reported: str) -> str:
    """`/proc/<pid>/comm` holds all fifteen characters; `pidstat` prints nine.

    Falls back to what `pidstat` said, because a process that exits between the
    two reads has no `/proc` entry left and its own name is all there is.
    """
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return reported
    return comm or reported


def _tables(text: str) -> "List[List[str]]":
    """Every table in the output, each as its lines.

    `pidstat` prints one banner + header + rows per requested report and then
    an `Average:` block. Splitting on blank lines keeps the two apart; taking
    the last *line* instead of the last *table* would hand back an average row,
    which is a different claim from a sample.
    """
    tables, current = [], []
    for line in (text or "").splitlines():
        if not line.strip():
            if current:
                tables.append(current)
                current = []
            continue
        if line.startswith("Linux ") or line.startswith("Average:"):
            # A banner opens a table; an Average row belongs to none of the
            # sampled ones and would otherwise be read as a program.
            if current:
                tables.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        tables.append(current)
    return [t for t in tables if len(t) >= 2]


def _parse_table(lines: "List[str]") -> "List[Cost]":
    """One report's rows, keyed on the header rather than on column position.

    The header names the columns, and **the three tables have different
    columns** - measured:

        -u   UID PID %usr %system %guest %wait %CPU CPU Command
        -r   UID PID minflt/s majflt/s VSZ RSS %MEM Command
        -d   UID PID kB_rd/s kB_wr/s kB_ccwr/s iodelay Command

    A positional reader that assumes one layout reads `RSS` as `kB_rd/s` and
    produces a confident wrong number.

    **The table is recognised by `PID` and `Command`, never by `%CPU`.** The
    first version gated on `%CPU` because it is the column that looks
    identifying - and `%CPU` appears only in the `-u` table, so the `-r` and
    `-d` tables were silently skipped and every process reported **0.0% of
    memory and no disk at all**. That is the absence-shaped wrong answer this
    module exists not to produce, produced by the guard that was meant to make
    the parse safer. `PID` and `Command` are the two columns all three share.
    """
    header = lines[0].split()
    if "PID" not in header or "Command" not in header:
        return []
    index = {name: position for position, name in enumerate(header)}
    out: "List[Cost]" = []
    for line in lines[1:]:
        cells = line.split()
        if len(cells) < len(header):
            continue
        pid_text = cells[index["PID"]]
        if not pid_text.lstrip("-").isdigit():
            continue

        def value(name: str) -> float:
            if name not in index:
                return 0.0
            return _number(cells[index[name]]) or 0.0

        out.append(Cost(
            pid=int(pid_text),
            # **The Command column is the LAST one, so it is read by position
            # and every other by name.** `gnome-terminal-server` holds a space,
            # so the tail is joined rather than taken as a single cell, and
            # `pidstat` truncates it to fifteen characters either way - the
            # full name is read back from the kernel, falling back to the
            # truncated form only for a process that has already exited.
            command=_full_name(int(pid_text),
                               " ".join(cells[index["Command"]:])),
            usr=value("%usr"),
            system=value("%system"),
            cpu=value("%CPU"),
            rss_kb=value("RSS"),
            pct_mem=value("%MEM"),
            minflt=value("minflt/s"),
            majflt=value("majflt/s"),
            kb_read=value("kB_rd/s"),
            kb_written=value("kB_wr/s"),
        ))
    return out


def _sample(flags: str) -> "tuple[List[List[str]], str]":
    """`pidstat <flags> 1 2`: the sampled tables and a reason if there are none."""
    try:
        proc = subprocess.run(["pidstat", *flags.split(), _INTERVAL, _SAMPLES],
                              capture_output=True, text=True, timeout=_TIMEOUT,
                              check=False)
    except subprocess.TimeoutExpired:
        return [], f"pidstat did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return [], str(exc)
    if proc.returncode != 0:
        detail = [line for line in (proc.stderr or "").splitlines() if line.strip()]
        return [], detail[-1] if detail else f"exit {proc.returncode}"
    tables = _tables(proc.stdout or "")
    return tables, "" if tables else "pidstat printed no readable report"


def _gather() -> "tuple[Dict[int, Cost], str]":
    """The three readings merged by pid, which is what makes them comparable.

    Each flag reports a different slice of the same interval; joining on pid is
    what turns three tables into one answer rather than three separate ones.
    """
    merged: "Dict[int, Cost]" = {}
    problem = ""
    for flags, fields in (("-u", ("usr", "system", "cpu")),
                          ("-r", ("rss_kb", "pct_mem", "minflt", "majflt")),
                          ("-d", ("kb_read", "kb_written"))):
        tables, why = _sample(flags)
        if why:
            problem = why
            continue
        # The LAST sampled table is the interval that is actually now; earlier
        # ones are the first partial sample.
        for cost in _parse_table(tables[-1]):
            blank = merged.get(cost.pid) or Cost(cost.pid, cost.command, 0.0,
                                                 0.0, 0.0, 0.0, 0.0, 0.0,
                                                 0.0, 0.0, 0.0)
            merged[cost.pid] = blank._replace(
                **{name: getattr(cost, name) for name in fields})
    return merged, problem


def _by_name(costs: "Dict[int, Cost]") -> "List[tuple]":
    """Group by program, because one program is often several pids.

    Measured: three `python3` at 99% each. Listing three rows reads as three
    problems; one row reading "python3, 3 processes, 297% CPU" is the same fact
    and the one a person can act on.
    """
    grouped: "Dict[str, List[Cost]]" = {}
    for cost in costs.values():
        grouped.setdefault(cost.command, []).append(cost)
    rows = []
    for name, group in grouped.items():
        rows.append((name, group,
                     # **Summed, not maxed.** `%CPU` is per *process*, where
                     # 100% means one core saturated (measured: three `python3`
                     # at 100.50 each on an 8-core box). Taking the largest of a
                     # group reports one of five processes' cost as though it
                     # were the program's, which understates a multi-process
                     # program by a factor of its process count - and that is
                     # the case the grouping exists to explain.
                     sum(item.cpu for item in group),
                     max(item.pct_mem for item in group),
                     sum(item.majflt for item in group),
                     sum(item.kb_read for item in group),
                     sum(item.kb_written for item in group)))
    rows.sort(key=lambda row: -row[2])
    return rows


def _run(_arguments: dict) -> str:
    if shutil.which("pidstat") is None:
        return ("Process cost is UNKNOWN: pidstat (the sysstat package) is not "
                "installed. sysstat is on every ShaniOS image, so a machine "
                "without it is a machine that has dropped something it "
                "installs by design.")
    config = ChronoaConfig()
    if not config.sense_allowed("cpu"):
        return sense_reading.refusal(config, "cpu")
    if not config.sense_allowed("storage"):
        return sense_reading.refusal(config, "storage")

    costs, problem = _gather()
    if problem and not costs:
        return f"Process cost is UNKNOWN: pidstat said {problem!r}. Nothing was guessed."
    rows = _by_name(costs)
    busy = [row for row in rows if row[2] >= _BUSY]

    if not busy:
        return ("No process was using a measurable amount of CPU in the last "
                "second. That is what an idle machine looks like - not a "
                "failure to read.")

    lines = [f"Cost over the last second, {len(busy)} program(s) busy "
             f"(of {len(rows)} seen):"]
    thrashers, writers = [], []
    for name, group, cpu, mem, majflt, kb_read, kb_written in busy[:12]:
        where = ""
        if len(group) > 1:
            where = f" [{len(group)} processes]"
        # `100%` of CPU means **one core saturated**, so a program with several
        # processes legitimately reads over 100% and the number is cores, not
        # a percentage of the machine. Saying so is the difference between
        # "python3 is using 502%" being alarming and being correct.
        unit = ("core%s of CPU" % ("" if cpu < 200.0 else "s")) \
            if cpu > 100.0 else "% of CPU"
        lines.append(f"  {name}{where}: {cpu:.1f}{unit} "
                     f"(user {sum(i.usr for i in group):.1f}, "
                     f"system {sum(i.system for i in group):.1f}), "
                     f"{mem:.1f}% of memory"
                     + (f", {kb_written:.0f} kB/s written"
                        if kb_written >= 1.0 else ""))
        if majflt >= _THRASHING:
            thrashers.append((name, majflt))
        if kb_written >= 1024.0 or kb_read >= 1024.0:
            writers.append((name, kb_read, kb_written))

    if thrashers:
        lines.append("")
        lines.append("Thrashing - these are taking page faults that had to be "
                     "read from disk, which is what 'everything just stopped' "
                     "usually is:")
        for name, majflt in sorted(thrashers, key=lambda row: -row[1])[:5]:
            lines.append(f"  {name}: {majflt:.0f} major fault(s)/s")

    if writers:
        lines.append("")
        lines.append("Reading or writing over 1 MB a second:")
        for name, kb_read, kb_written in writers[:5]:
            lines.append(f"  {name}: {kb_read:.0f} kB/s read, "
                         f"{kb_written:.0f} kB/s written")

    if problem:
        lines.append("")
        lines.append(f"Partly unreadable: {problem}. The rows above are real; "
                     "the missing half is named rather than shown as zero.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "process_cost",
        "description": (
            "What each running program is COSTING this machine right now, "
            "measured over the last second rather than as a snapshot: "
            "percent CPU split into user and system time, how much memory it "
            "holds, page faults (including the major ones that mean it is "
            "thrashing), and how many kB a second it is reading and writing. "
            "Answers 'what is using my CPU', 'what is making my computer slow', "
            "'what is writing to my disk all the time', 'which program is "
            "swapping', 'why did everything freeze'. Use this when the machine "
            "feels slow; use list_processes to see what is running and "
            "disk_activity to see whether the disk itself is busy. Read-only; "
            "nothing is started, stopped or changed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="process_cost", schema=SCHEMA, run=_run)]