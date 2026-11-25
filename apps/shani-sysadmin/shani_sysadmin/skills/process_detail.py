"""Skill: what is this stuck program waiting on?

"Chronoa is frozen" and "that window has stopped responding" both end in the
same question: *what is the process actually blocked on?* Nothing answered it.
`list_processes` reports CPU and memory, which is what a process that is
**busy** looks like - and a hung process is the opposite: it is using no CPU at
all, because it is waiting.

`/proc/<pid>/wchan` is the kernel's own name for the function the task is
sleeping in, and `/proc/<pid>/status` its state letter. Both are world-readable
for the user's own processes and need no root, no ptrace and no binary at all.

**`strace` is deliberately not used here**, and the reason is worth keeping
because it is the shape this repo keeps meeting. `strace -p PID` is the obvious
tool, but tracing an arbitrary process is *attaching to it*: on this machine
`strace -p <pid>` fails with `ptrace(PTRACE_SEIZE, ...): Operation not permitted`
under `kernel.yama.ptrace_scope = 1`, and its exit status is 1 for a pid that
does not exist and for a pid it may not touch. More to the point, `strace
<command>` runs an arbitrary command, which is exactly the fixed-whitelist
boundary AGENTS.md marks **never** to cross. `wchan` answers the same question
about a process that is *already running*, without executing anything.

**Measured here, and the shapes that matter:**

    Name:  sleep                          kernel threads: cmdline is EMPTY
    State: S (sleeping)                   wchan is "0" and means "on CPU"
    wchan: hrtimer_nanosleep              State letter S/D/T/Z each mean
    cmdline: sleep 300                    something different

`wchan` reads `0` for a process that is *running*, not for one that is stuck -
so "0" must never be printed as a place, or a busy process is reported as
blocked in function zero. A kernel thread has an empty `cmdline` and would
otherwise be named by nothing at all.
"""

from __future__ import annotations

import os

from shani_chronoa.skills import Skill

_TIMEOUT = 20

#: The kernel's own state letters, from `proc(5)`. These are the five a user
#: will actually meet, and saying what each means is the whole value of the
#: answer - a letter on its own is a code, not an explanation.
_STATES = {
    "R": "running or using CPU right now",
    "S": "sleeping - waiting for something (a timer, a disk, the network)",
    "D": "uninterruptible sleep - almost always waiting on a disk or a drive",
    "T": "stopped - paused, usually by a debugger or by Ctrl-Z",
    "Z": "a zombie - finished, but nobody has reaped it yet",
    "t": "tracing stop (being debugged)",
    "X": "dead",
    "I": "idle kernel thread",
}

#: `wchan` values that mean "not blocked in a named function" rather than a
#: place. `0` is what a running task reports, and it is the one that matters:
#: reading it as a function name says a busy process is stuck in "0".
_NOT_A_PLACE = {"0", "-", ""}


def _proc_field(pid: int, name: str) -> str:
    """One field out of a `/proc/<pid>/...` file, or "" if unreadable."""
    try:
        with open(f"/proc/{pid}/{name}", encoding="utf-8",
                  errors="replace") as handle:
            return handle.read()
    except (OSError, ValueError):
        # Unreadable is NOT "empty". A pid owned by another user, or one that
        # exited between the list and this read, must not be reported as a
        # process with nothing in it.
        return ""


def _describe(pid: int) -> "tuple[dict, str]":
    """(facts, reason). `reason` is empty only when the pid was really read."""
    status = _proc_field(pid, "status")
    if not status:
        # Distinguish "gone" from "not yours": /proc/<pid> existing at all is
        # the test, and reading it is the one that can be refused.
        if not os.path.isdir(f"/proc/{pid}"):
            return {}, f"there is no process {pid} any more"
        return {}, (f"process {pid} belongs to another user, so this account "
                    "cannot read what it is waiting on")

    fields = {}
    for line in status.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.strip(), value.strip())

    cmdline = _proc_field(pid, "cmdline").replace("\0", " ").strip()
    wchan = _proc_field(pid, "wchan").strip()
    # `wchan` is a bare function name with no newline on most kernels; on some
    # it is padded. `0` and `-` are the "not in a named function" answers.
    if wchan and wchan.isdigit():
        wchan = ""

    return {
        "name": fields.get("Name", ""),
        "state": fields.get("State", ""),
        "cmdline": cmdline,
        "wchan": wchan,
        "threads": fields.get("Threads", ""),
        "rss": fields.get("VmRSS", ""),
    }, ""


def _run(arguments: dict) -> str:
    # **Not `arguments.get("pid") or ""`.** `pid=0` is falsy, so that idiom
    # turns "you gave me an invalid pid" into "you gave me no pid at all" -
    # two different questions, and the second asks the person to supply
    # something they had already supplied. Caught by running it.
    given = arguments.get("pid", arguments.get("process"))
    raw = "" if given is None else str(given).strip()
    if not raw:
        return ("I need a process id to look at. `list_processes` will show you "
                "the pids, and you can ask me about one of them.")
    try:
        pid = int(raw)
    except ValueError:
        return f"{raw!r} is not a process id. A pid is a plain number."
    if pid <= 0:
        return ("That is not a process id - pids start at 1. Ask me to list "
                "processes if you are not sure which one you mean.")

    facts, problem = _describe(pid)
    if problem:
        return problem

    name = facts["name"] or "(unnamed)"
    letter = facts["state"].split("(")[0].strip()
    meaning = _STATES.get(letter, "a state this kernel does not document here")
    title = f"pid {pid}, {name}"
    if facts["cmdline"]:
        title += f" - {facts['cmdline']}"

    lines = [title, f"  state: {letter} = {meaning}"]

    wchan = facts["wchan"]
    if wchan:
        lines.append(f"  waiting in: {wchan} (the kernel's own name for the "
                     "function it is asleep in)")
    elif letter == "R":
        lines.append("  waiting in: nowhere - it is on the CPU, so it is busy "
                     "rather than stuck")
    elif not facts["cmdline"]:
        # **Measured: a kernel thread sleeps with `wchan` reading `0` and an
        # empty `cmdline`.** `/proc/<pid>/wchan` only ever names *userspace*
        # functions, so for kthreadd it has nothing to report - and "I could
        # not find out" would be the wrong sentence for a process that is
        # working exactly as intended.
        lines.append("  waiting in: not applicable - this is a kernel thread, "
                     "and the kernel does not report a userspace function for "
                     "one")
    else:
        # Sleeping but with no name: real, and different from every case above.
        # On this machine `kernel.yama.ptrace_scope` is 1, which is enough to
        # blank wchan for *other users'* processes - so this branch is the
        # normal answer for pid 1, not an edge case.
        lines.append("  waiting in: the kernel did not name a function for "
                     "this one (it only names userspace functions, and it "
                     "reports nothing for a process owned by another user), "
                     "so I cannot say what it is blocked on")

    if facts["rss"]:
        lines.append(f"  memory: {facts['rss']}")
    if letter in ("Z", "D"):
        lines.append("")
        if letter == "Z":
            lines.append("A zombie is a process that has finished and is "
                         "waiting for its parent to notice. It is not doing "
                         "anything, and it holds no memory beyond its own.")
        else:
            lines.append("Uninterruptible sleep usually means the kernel is "
                         "waiting on a disk that has stopped answering. This "
                         "is the state a failing drive shows.")

    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "process_detail",
        "description": (
            "What one running process is doing right now: its state (running, "
            "sleeping, stopped, zombie), and the kernel function it is waiting "
            "on if it is blocked. Answers 'why is that window frozen', 'what "
            "is this program stuck on', 'is this process hung'. Read-only: it "
            "attaches to nothing, runs nothing, and never needs root for your "
            "own processes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "The process id to look at, from list_processes.",
                },
            },
            "required": ["pid"],
        },
    },
}

SKILLS = [Skill(name="process_detail", schema=SCHEMA, run=_run)]