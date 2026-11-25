"""Skill: which THREAD is the problem?

`process_detail` answers "what is this process waiting on" from
`/proc/<pid>/wchan`, and `list_processes` shows CPU per **process**. Between
them is the question a person asks when something is stuck, spinning, or
eating a core:

> **"This program is at 300% CPU - which thread?"**

Nothing in this package goes below the process. `grep -rn "/task" usr/` finds
no such reader, and the gap matters most for exactly the programs on this
machine: measured here, the busiest threads by `/proc/*/task/*/stat` belong to
**`llama-server` (39,145 jiffies)**, `gnome-terminal-`, `gnome-shell` and
`opencode` - all of them multi-threaded, and all of them opaque to a per-process
reading. A process at 400% CPU with one thread at 380% is a very different
diagnosis from four threads sharing it evenly, and the process-level number
cannot tell those apart.

**Four traps, all measured on a live machine, and every one of them produces a
confident wrong answer rather than an error.**

1. **`comm` can contain spaces and is truncated to 15 characters.** Measured
   real values on this box: `"Bun Pool 5"`, `"kworker/R-ext4-rsv-conversion"`
   (27 chars), `"irq/160-iwlwifi:queue_8"`. So the classic
   `line.split()[1]` for the command name is **wrong on this machine** - it
   returns `Pool` for `"Bun Pool 5"` - and it is the name people identify a
   program by. The field is taken between the **first `(` and the last `)`**,
   which is where the kernel brackets it, and a name containing a paren cannot
   break that.

2. **`utime`/`stime` are per-thread, in jiffies, and are cumulative.** They
   are read from field positions *after* the closing paren, so every field is
   shifted by however many words the command name happened to contain. Reading
   position 11 of the whole line is a different field on `Bun Pool 5` than on
   `systemd`. And a cumulative counter needs **two readings** to be a rate at
   all - reporting the total as a percentage is the classic version of this bug.

3. **`wchan` reads `0` for a thread that is running**, not for one that is
   stuck. `process_detail`'s own docstring records this for processes and it
   holds per thread, so "waiting in `0`" and "running" are separated by the
   state letter rather than reported as a null wait.

4. **A thread exits between listing `/proc/<pid>/task` and reading it.** This
   is the ordinary case under load rather than an edge case, so every read is
   individually guarded and a thread that vanished is counted as gone rather
   than crashing the walk.

**What it deliberately does not do.** It reads `/proc` and nothing else - no
`top -H`, no `ps -L`, no `htop`. `strace`, `perf`, `btop` and `nvtop` are on
the image and unused, and every one of them is a *sampling* tool whose answer
depends on a window and a sort order; this is the unprivileged kernel data
behind them, which needs no binary and cannot refuse.
"""

from __future__ import annotations

import os
import time
from typing import List, NamedTuple, Optional, Tuple

from shani_chronoa.skills import Skill

#: How long to wait between the two readings that turn cumulative jiffies into
#: a rate. Long enough that one tick is a visible fraction, short enough that a
#: person does not notice they are waiting.
_SAMPLE_SECONDS = 1.0

#: `wchan` values that mean "not blocked in a named function" rather than a
#: real answer - the kernel's own comment calls these `0`. `process_detail`
#: already carries this table; the same shapes recur per thread.
_UNNAMED_WCHAN = ("0", "")


class Thread(NamedTuple):
    pid: str
    tid: str
    comm: str
    state: str
    utime: int
    stime: int
    wchan: str


#: The `/proc` root, as a module attribute rather than a literal at each call
#: site. **This exists so the tests can redirect the tree**, and it was added
#: because `monkeypatch.setattr(module.os, "listdir", ...)` patches the
#: *stdlib* `os` module - `skills.thread_activity.os is os` - so the fixture
#: broke pytest's own directory walking and six tests failed with "No readable
#: threads were found" while the skill under test was fine. A seam that is the
#: thing being replaced, rather than a patch that reaches through it.
_PROC = "/proc"


def _list_dir(path: str) -> List[str]:
    """The entries of a directory, or `[]` when it cannot be read.

    **A seam for the same reason as `_read_text`**, and the second half of the
    same lesson: patching `os.listdir` through the module reaches the *stdlib*
    function, because `skills.thread_activity.os is os`, and pytest's own
    directory walking then reads the fake `/proc` and fails. The value being
    replaced is the thing the framework also uses, so the replacement has to be
    a name the skill owns.
    """
    try:
        return os.listdir(path)
    except OSError:
        return []


def _read_text(path: str) -> Optional[str]:
    """The contents of a `/proc` file, or `None` when it cannot be read.

    **A named function, not a bare `open(...)` at each call site**, and the
    reason is what running the tests taught: `monkeypatch.setattr("builtins.
    open", ...)` patches `open` *globally*, so a fixture standing in for the
    `/proc` tree also intercepted pytest's own file reads and six tests failed
    with "No readable threads were found" against a skill that was working. The
    same patch reached through `module.os` before that - `skills.X.os is os` -
    and broke pytest's directory walking. Both are the recorded hazard of
    replacing something a framework depends on; the seam is the fix.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _read_thread(pid: str, tid: str) -> Optional[Thread]:
    """One thread's counters, or `None` if it exited while being read.

    **`comm` is bracketed by the kernel, not space-separated.** The kernel
    writes `pid (comm) state ...` where `comm` may itself contain both spaces
    and parens - measured on this machine: `"Bun Pool 5"`,
    `"kworker/R-writeback"`, and kernel threads named past 15 characters. So
    the name is taken from the **first `(` to the last `)`**, and every
    remaining field is read from *after* that closing paren, where positions
    are stable no matter what the name contained.
    """
    base = f"{_PROC}/{pid}/task/{tid}"
    raw = _read_text(f"{base}/stat")
    if raw is None:
        return None       # exited between the listing and this read
    open_paren = raw.find("(")
    close_paren = raw.rfind(")")
    if open_paren < 0 or close_paren <= open_paren:
        return None
    comm = raw[open_paren + 1:close_paren]
    fields = raw[close_paren + 1:].split()
    # After the paren: state ppid pgrp session tty_nr tpgid flags minflt cminflt
    # majflt cmajflt utime stime ...
    if len(fields) < 13:
        return None
    try:
        return Thread(pid=pid, tid=tid, comm=comm, state=fields[0],
                      utime=int(fields[11]), stime=int(fields[12]),
                      wchan=_read_wchan(base))
    except ValueError:
        return None


def _read_wchan(base: str) -> str:
    """The kernel's name for the function a thread is blocked in.

    Read per-thread, which is what makes the question answerable at all: a
    process's own `wchan` is only one of its threads' states, so a process
    reported as "sleeping in `do_wait`" may have another thread running flat
    out.
    """
    text = _read_text(f"{base}/wchan")
    return text.strip() if text is not None else ""


def _hz() -> float:
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError):
        return 100.0


def _threads_of(pid: str) -> List[Thread]:
    try:
        tids = _list_dir(f"{_PROC}/{pid}/task")
    except OSError:
        return []
    out: List[Thread] = []
    for tid in tids:
        if not tid.isdigit():
            continue
        thread = _read_thread(pid, tid)
        if thread is not None:
            out.append(thread)
    return out


def _snapshot() -> dict:
    """Every readable thread on the machine, keyed by `(pid, tid)`.

    Read directly rather than through `port_owner` or any other module's
    `/proc` walk: those answer a different question, and one walk per question
    is the rule this repo keeps re-learning.
    """
    found: dict = {}
    try:
        pids = [p for p in _list_dir(_PROC) if p.isdigit()]
    except OSError:
        return found
    for pid in pids:
        for thread in _threads_of(pid):
            found[(thread.pid, thread.tid)] = thread
    return found


def _describe(pid: Optional[str] = None) -> str:
    """Take the two readings and render them.

    **Sampling and rendering are separate functions on purpose.** A rate needs
    two readings of the same tree, so the seam a test needs is "these are the
    readings" - and a first version made the renderer read `/proc` itself, so
    every test had to fake a *sequence* of directory listings and interleave
    counters to make the second reading differ from the first. Six tests failed
    against a working skill because `skills.thread_activity.os is os`: patching
    `os.listdir` through the module patched the stdlib function, so pytest's own
    directory walking started reading the fake `/proc` tree. Passing the two
    snapshots in removes the need for any of that.
    """
    if pid:
        before = {(pid, t.tid): t for t in _threads_of(pid)}
        if not before:
            return (f"There is no process {pid} on this machine that this user "
                    f"can read - it may have exited, or it may belong to "
                    f"another user.")
        time.sleep(_SAMPLE_SECONDS)
        after = {(pid, t.tid): t for t in _threads_of(pid)}
        label = f"Process {pid}"
    else:
        before = _snapshot()
        time.sleep(_SAMPLE_SECONDS)
        after = _snapshot()
        label = "This machine"
    return _render(before, after, label, whole_machine=pid is None)


def _render(before: dict, after: dict, label: str,
            whole_machine: bool = False) -> str:
    """The report, from two snapshots. Pure: it reads nothing itself."""
    threads = list(after.values())
    if not threads:
        return "No readable threads were found."

    hz = _hz()
    cores = os.cpu_count() or 1
    window = _SAMPLE_SECONDS

    scored: List[Tuple[float, Thread, Optional[Thread]]] = []
    for key, thread in after.items():
        was = before.get(key)
        jiffies = (thread.utime + thread.stime) - (
            (was.utime + was.stime) if was is not None else 0)
        # A thread that appeared during the window has no earlier reading, so
        # its whole lifetime is counted as the window's delta - which overstates
        # it. It is still ranked, and said to be new, rather than dropped:
        # dropping the thread that just spawned is how "nothing is busy" gets
        # reported about a program that is spawning constantly.
        scored.append((jiffies / hz / window * 100.0, thread, was))
    scored.sort(key=lambda row: -row[0])

    lines: List[str] = []
    if not whole_machine:
        lines.append(f"{label}: {len(threads)} thread(s).")
    else:
        lines.append(f"{label}: {len(threads)} thread(s) across "
                     f"{len({t.pid for t in threads})} process(es); CPU is a "
                     f"share of one core, sampled over {window:g}s on "
                     f"{cores} core(s).")
    lines.append("")

    top = scored[:15]
    busy = [row for row in top if row[0] >= 0.5]

    # **Threads with no CPU are still the answer to half this question.** A
    # first version listed only threads above the CPU threshold and returned
    # early when there were none, so a thread blocked in a long syscall - a read
    # from a dead disk, a futex nobody will wake - **never appeared at all**, and
    # "which thread is stuck?" answered "nothing used measurable CPU". A stuck
    # thread uses no CPU *because* it is stuck. So the blocked ones are gathered
    # first and are a result in their own right, not a footnote to a busy list.
    blocked = [row for row in scored
               if row[0] < 0.5 and row[1].state != "R"
               and row[1].wchan and row[1].wchan not in _UNNAMED_WCHAN]
    if blocked and whole_machine:
        # Whole-machine view: hundreds of threads sleeping in ordinary kernel
        # waits say nothing, so only those belonging to a process that also has
        # a busy thread are listed.
        top_pids = {t.pid for _, t, _ in busy}
        blocked = [row for row in blocked if row[1].pid in top_pids]

    if not busy:
        lines.append("  No thread used measurable CPU in the sampling window.")
        if blocked:
            lines.append("  These are blocked rather than busy - being stuck "
                         "is what using no CPU looks like:")
            for percent, thread, _was in blocked[:8]:
                lines.append(f"  {percent:5.1f}%  {thread.state:<2} "
                             f"{thread.pid}/{thread.tid}  "
                             f"{thread.wchan:<24} {thread.comm}")
        else:
            lines.append("  If you are looking for a busy machine, raise the "
                         "window - one second can miss a burst.")
        return "\n".join(lines)

    lines.append(f"  {'CPU%':>6}  {'state':<2} {'pid/tid':<13}  {'waiting in':<24} name")
    for percent, thread, was in busy:
        waiting = thread.wchan
        if thread.state == "R":
            # Running wins over whatever wchan holds, because wchan is only
            # meaningful for a thread that is *blocked* - it reads 0 or stale
            # values for one on CPU.
            waiting = "on CPU"
        elif not waiting or waiting in _UNNAMED_WCHAN:
            waiting = "(not in a named function)"
        new = "" if was is not None else "  [new during the window]"
        lines.append(f"  {percent:5.1f}%  {thread.state:<2} "
                     f"{thread.pid}/{thread.tid}  {waiting:<24} "
                     f"{thread.comm}{new}")
    if blocked:
        lines.append("")
        lines.append("  Blocked (using no CPU, which is what being stuck looks like):")
        for percent, thread, _was in blocked[:8]:
            lines.append(f"  {percent:5.1f}%  {thread.state:<2} "
                         f"{thread.pid}/{thread.tid}  {thread.wchan:<24} "
                         f"{thread.comm}")

    if len(scored) > len(top):
        lines.append(f"  ... and {len(scored) - len(top)} further thread(s)")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    args = arguments or {}
    raw = args.get("pid", "")
    if raw in (None, ""):
        return _describe(None)
    pid = str(raw).strip()
    if not pid.isdigit():
        return ("A process id is a number, like 1234. To look at every "
                "process on the machine instead, omit it.")
    return _describe(pid)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "thread_activity",
        "description": (
            "Show the individual THREADS of a process - or of the whole "
            "machine - with the CPU each is using right now and the kernel "
            "function it is blocked in. Use for 'which thread is using the "
            "CPU', 'this program is at 300 percent', 'what is this thread "
            "waiting on', 'why is this stuck', 'which of these threads is "
            "busy'. Goes one level below process_detail and list_processes, "
            "which report one figure for the whole process and cannot tell a "
            "program with one hot thread from one using every core evenly. "
            "Read-only; nothing is sampled with a tool, attached to or "
            "changed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "string",
                    "description": ("The process to look inside, e.g. '1234'. "
                                    "Omit for every process on the machine."),
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="thread_activity", schema=SCHEMA, run=_run)]