"""Skill: who would the kernel kill, and has it killed anyone already?

"Firefox vanished" and "the machine just froze" have two different causes, and
neither is answered anywhere in this tree. Measured, not assumed:

    grep -rn "oom_score"      usr/lib/...  -> 0 files
    grep -rn "oom_kill"       usr/lib/...  -> 0 files
    grep -rn "pressure/memory" usr/lib/... -> 0 files

`resources` reports *what is in use* - zombie processes, swap, file descriptors
- and `kernellog` reports what the journal said after the fact. Neither answers
the two questions a person actually has:

1. **Is memory pressure happening right now?** `/proc/pressure/memory` is the
   kernel's own measure of it, and the only one that is not a proxy. Free
   memory below a threshold is not pressure; **time spent stalled waiting on
   memory** is pressure, and on a machine with a page cache doing its job those
   are different facts.
2. **Who would go first if it got worse?** `/proc/<pid>/oom_score` is the
   kernel's ranking, and `oom_score_adj` is whether somebody *deliberately*
   moved a process. Measured on this box: every chrome child carries `adj 300`
   - a policy set by whatever launched them, saying "kill these before the
   rest". Nothing here would have told a person that.

**Four things that make this more than a wrapper, all measured here:**

- **`oom_score` is a ranking, not a countdown.** A process at 870 is "the one
  the OOM killer would pick", not "870% dead" and not "about to be killed". The
  module says so in its own output rather than letting a bare number be read as
  a proximity to death, which is the obvious wrong reading and the one a person
  would act on.
- **`oom_kill: 0` is a real answer, and it is not "unknown".** `0` means the
  kernel has never OOM-killed on this boot; *absent* means the vmstat field
  could not be read. Those are different claims and the difference matters - a
  machine whose honest state is "0 kills" must not read as one that could not
  ask.
- **`total=` in PSI is cumulative microseconds since boot**, so it is stall
  *time*, not a percentage of a window. It is reported as seconds rather than
  folded into a figure nobody asked about.
- **`some` and `full` are separate PSI measurements.** `some` is how often *any*
  task waited, `full` how often *every* task did. Quoting one as the other
  would overstate an ordinary machine, so the reader takes `some` deliberately
  and the docstring says why.

Everything here reads `/proc` through the standard library. No binary is
consulted, which is the case this package prefers: `choom` is the util-linux
tool for the adjustment and `vmstat` for the counter, and neither gives more
than the file does.

**Nothing is adjusted.** `oom_score_adj` is read and reported, never written -
an adjustment changes who the kernel kills, and this skill does not get to
decide that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

#: `/proc/pressure/memory` with PSI disabled, or absent from the image, is a
#: missing file rather than a zero - and zero is a measurement.
_PSI = Path("/proc/pressure/memory")
_VMSTAT = Path("/proc/vmstat")
#: Named so a test can point the walk at a fixture tree. **A stubbed
# `iterdir` is not an option**: patching `pathlib.Path.iterdir` changes it for
# every file in the process, and the first version of this test did exactly
# that and then failed for reasons that had nothing to do with the parse.
_PROC = Path("/proc")

#: PSI's three averaged windows, in seconds. The first is noisy and the third
#: is the machine's own recent history, so one alone says less than the set.
_WINDOWS = (("avg10", 10), ("avg60", 60), ("avg300", 300))

#: The kernel's `oom_score` is scaled so the kernel picks the highest, and a
# process in the upper band is genuinely a candidate. **The list is not "the
# top N"**: the first version printed the top 10 whatever they scored, so on a
# quiet box it listed a process at 2 of 1000 under "who the OOM killer would
# pick first" - the tail of a short list reading as a candidate. Below this
# line the honest answer is that nothing is.
_AT_RISK = 700.0


class Pressure(NamedTuple):
    """PSI's memory numbers, read as published."""

    windows: Dict[str, float]
    total_us: Optional[int]


def _read_oom_kills() -> Optional[int]:
    """How many processes the kernel has OOM-killed, or None if unasked.

    **An absent field is not a zero counter.** `oom_kill` is a line of
    `/proc/vmstat`, and a kernel or a namespace that does not carry it must
    report "unknown" - a `0` there would claim the kernel has never killed
    anyone on a machine where nobody has looked.
    """
    try:
        raw = _VMSTAT.read_text()
    except OSError:
        return None
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "oom_kill":
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def _read_pressure() -> "Tuple[Optional[Pressure], str]":
    """`(PSI reading, why there is none)`."""
    try:
        raw = _PSI.read_text()
    except FileNotFoundError:
        return None, (f"{_PSI} is absent: this kernel has no PSI, so memory "
                      "pressure cannot be measured here")
    except OSError as exc:
        return None, f"{_PSI} could not be read ({exc})"

    windows: Dict[str, float] = {}
    total_us: Optional[int] = None
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        # **Only the `some` block.** `full` is a different claim - how often
        # *every* task waited rather than *some* task - and quoting the first
        # block found would be an accident of file order.
        if parts[0].rstrip(":") != "some":
            continue
        for item in parts[1:]:
            if "=" not in item:
                continue
            name, _, value = item.partition("=")
            if name in dict(_WINDOWS):
                try:
                    windows[name] = float(value)
                except ValueError:
                    return None, f"{_PSI} carries {name}={value!r}"
            elif name == "total":
                try:
                    total_us = int(value)
                except ValueError:
                    return None, f"{_PSI} carries total={value!r}"
    if not windows:
        return None, f"{_PSI} parsed, but no averaged window was in it"
    return Pressure(windows, total_us), ""


def _owner(pid: int) -> str:
    """The user behind a pid, from `/proc/<pid>/status`'s `Uid:` line.

    **The number, not a name.** The uid is the kernel's own record and is
    always present; a name needs a passwd lookup that can fail on a machine
    with no matching entry, and the number is the more useful half anyway.
    """
    try:
        for line in (_PROC / str(pid) / "status").read_text().splitlines():
            if line.startswith("Uid:"):
                fields = line.split()
                if len(fields) >= 2:
                    return f"uid {fields[1]}"
                break
    except OSError:
        pass
    return ""


class Target(NamedTuple):
    """One process, with the kernel's own ranking of it."""

    pid: int
    name: str
    owner: str
    score: float
    adj: Optional[int]
    rss_kb: Optional[int]


def _targets() -> "List[Target]":
    """Every readable process, with its OOM ranking.

    **Read whole; skip what cannot be read.** A process whose `oom_score` is
    unreadable is left out rather than counted as zero - a `0` in this column
    reads as "the kernel would never pick this", which for an unreadable
    process is a claim nobody has measured. The same rule `list_processes`
    already follows for a process that exits mid-walk.
    """
    out: "List[Target]" = []
    try:
        entries = list(_PROC.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            score = float((entry / "oom_score").read_text().strip())
        except (OSError, ValueError):
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            comm = ""
        adj: Optional[int] = None
        try:
            text = (entry / "oom_score_adj").read_text().strip()
            if text.lstrip("-").isdigit():
                adj = int(text)
        except (OSError, ValueError):
            adj = None
        rss: Optional[int] = None
        try:
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    fields = line.split()
                    if len(fields) >= 2 and fields[1].isdigit():
                        rss = int(fields[1])
                    break
        except (OSError, ValueError):
            rss = None
        out.append(Target(pid, comm or f"pid {pid}", _owner(pid), score, adj,
                          rss))
    return out


def _adjusted(targets: "List[Target]") -> "List[Target]":
    """Processes somebody deliberately re-ranked in the kernel's queue.

    **`0` is not an adjustment.** The overwhelming majority of processes carry
    the default, so reporting them would bury the few that were moved - and the
    moved ones are the whole point, because they are a statement about
    expendability made by whoever launched the process rather than by the
    kernel's own scaling.
    """
    return [t for t in targets if t.adj not in (None, 0)]


def _run(_arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("memory"):
        return sense_reading.refusal(config, "memory")

    problems: List[str] = []
    pressure, why = _read_pressure()
    if why:
        problems.append(why)
    kills = _read_oom_kills()
    if kills is None:
        problems.append("/proc/vmstat carries no oom_kill counter, so the "
                        "number of memory kills is unknown")

    targets = _targets()
    if not targets:
        return ("OOM state is UNKNOWN: no process's oom_score could be read "
                "from /proc. Nothing was guessed.")
    # **A threshold, not a top-N.** A list of "the first ten" on a quiet box
    # ends with processes the kernel would never consider, and every row above
    # them inherits the doubt. Nothing above the line is a real answer.
    at_risk = sorted((t for t in targets if t.score >= _AT_RISK),
                     key=lambda t: -t.score)[:10]
    moved = _adjusted(targets)

    lines: List[str] = []
    if pressure:
        shown = " and ".join(
            f"{pressure.windows[name]:.2f}% over the last {span}s"
            for name, span in _WINDOWS if name in pressure.windows)
        lines.append(f"Memory pressure right now: {shown}.")
        if pressure.total_us is not None:
            lines.append(
                "  cumulative time something waited on memory: "
                f"{pressure.total_us / 1_000_000:.1f}s since boot")
        lines.append(
            "  that is the share of time a task was stalled waiting on a "
            "memory page - not how much memory is free, which is a different "
            "question")

    # **`0` and "could not ask" are different sentences.** "The kernel has not
    # killed anyone" is a fact about this machine; a missing counter is a fact
    # about /proc and was appended to `problems` above instead of merged here.
    if kills:
        lines.append(
            f"The kernel has OOM-killed {kills} process(es) since boot.")
    elif kills == 0:
        lines.append("The kernel has not OOM-killed anything on this boot.")

    if at_risk:
        lines.append("")
        lines.append(f"Who the OOM killer would pick first (the kernel's own "
                     f"ranking, above {_AT_RISK:.0f}, highest first):")
        for target in at_risk:
            adj = f", adj {target.adj}" if target.adj else ""
            where = f", {target.owner}" if target.owner else ""
            mem = (f" holding {target.rss_kb // 1024} MB"
                   if target.rss_kb is not None else "")
            lines.append(
                f"  {target.name} (pid {target.pid}{where}): "
                f"score {target.score:.0f}{adj}{mem}")
        lines.append(
            "  the score is where the kernel would start looking, not a "
            "proximity to death: nothing on a machine with free memory is "
            "close to being killed")
    else:
        lines.append("")
        lines.append(f"No process scores {_AT_RISK:.0f} or above, so nothing "
                     "on this machine is a candidate the kernel would pick "
                     "first - which is the same as saying memory is not the "
                     "thing this machine is short of right now.")

    if moved:
        lines.append("")
        lines.append("Deliberately re-ranked by whoever launched them "
                     "(oom_score_adj, not the kernel's scaling):")
        grouped: Dict[str, Dict[int, int]] = {}
        for target in moved:
            # **Every distinct adjustment, not the first one read.** Measured:
            # chrome carries adj 300 on 15 processes, 200 on 4 and 100 on 4,
            # so quoting `group[0].adj` named one process's policy as though it
            # were all of them - three different statements collapsed into one
            # confident number. The kernel applies the per-process value, so
            # the distribution is the fact.
            counts = grouped.setdefault(target.name, {})
            counts[target.adj] = counts.get(target.adj, 0) + 1
        for name, counts in sorted(grouped.items(),
                                   key=lambda kv: -sum(kv[1].values()))[:6]:
            spread = ", ".join(
                f"adj {adj} on {count}" for adj, count in
                sorted(counts.items(), reverse=True))
            lines.append(f"  {name}: {spread}")
        lines.append(
            "  a raised number asks for these to be killed before the rest; a "
            "lowered one asks for them to be kept")

    if problems:
        lines.append("")
        lines.append(f"Partly unreadable: {'; '.join(problems)}. What is above "
                     "is real; the missing half is named rather than shown as "
                     "zero.")
    elif not lines:
        lines.append(
            "Nothing about memory pressure could be measured, and no process "
            "carries an OOM ranking above zero - so the honest answer is that "
            "nothing here would be picked first.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "oom_score",
        "description": (
            "Which process the kernel would kill if memory ran out, and "
            "whether it has killed anything already. Reports how much time "
            "this machine has spent stalled waiting on memory, the kernel's "
            "own ranking of who would go first, any process somebody "
            "deliberately marked as more expendable, and how many processes "
            "the kernel has OOM-killed since boot. Answers 'who would the "
            "kernel kill', 'why did my program disappear', 'is my machine "
            "short on memory', 'has anything been killed', 'what would happen "
            "if I opened more'. Use this when something vanished or the "
            "machine stalled; use list_processes to see what is running and "
            "the memory sense to see how much is used. Read-only; nothing is "
            "adjusted and nothing is killed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="oom_score", schema=SCHEMA, run=_run)]
