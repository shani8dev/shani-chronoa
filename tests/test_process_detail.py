"""`process_detail`: what is this stuck process waiting on?

Nothing answered it. `list_processes` reports CPU and memory, which is what a
**busy** process looks like - and a hung process is the opposite, using no CPU at
all because it is waiting.

`/proc/<pid>/wchan` is the kernel's own name for the function a task sleeps in.
No root, no ptrace, no binary.

**`strace` is deliberately absent**, and the test that says so is the load-
bearing one: `strace -p PID` attaches (measured on this box:
`ptrace(PTRACE_SEQUE, ...): Operation not permitted` under `ptrace_scope = 1`),
and `strace <command>` runs an arbitrary command, which is the fixed-whitelist
boundary AGENTS.md marks **never** to cross. `wchan` answers the same question
about a process that is *already running*, and executes nothing.

**The trap this file exists to pin:** `wchan` reads `0` for a process that is
*running*, not for one that is stuck. Printing that as a place says a busy
process is blocked in function zero.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import process_detail as PD  # noqa: E402

#: Real `/proc/<pid>/status` field shapes, captured on this machine.
STATUS_SLEEPING = """Name:\tsleep
State:\tS (sleeping)
Tgid:\t492537
Pid:\t492537
Threads:\t1
VmRSS:\t    7848 kB
"""

STATUS_RUNNING = """Name:\tpython3
State:\tR (running)
Tgid:\t492590
Pid:\t492590
Threads:\t1
VmRSS:\t    16548 kB
"""

STATUS_ZOMBIE = """Name:\tpython3
State:\tZ (zombie)
Tgid:\t492600
Pid:\t492600
Threads:\t1
VmRSS:\t       0 kB
"""

STATUS_DISK = """Name:\tpostgres
State:\tD (uninterruptible sleep)
Tgid:\t492610
Pid:\t492610
Threads:\t4
VmRSS:\t  221184 kB
"""

#: A kernel thread: real shape, empty cmdline, wchan reads `0`.
STATUS_KERNEL = """Name:\tkthreadd
State:\tS (sleeping)
Tgid:\t2
Pid:\t2
Threads:\t1
VmRSS:\t       0 kB
"""


def _fake_proc(monkeypatch, status, wchan="", cmdline="sleep 300",
               readable=True, exists=True):
    """Stand in for `/proc`, so every state can be driven.

    A *real* hung process cannot be manufactured on demand, and a test that
    only ever sees this machine's current states is a test that cannot fail on
    the states that matter.

    `cmdline` is a **str**, because `_proc_field` opens in text mode. Passing
    bytes here was my first attempt and raised `TypeError: a bytes-like object
    is required, not 'str'` - the fixture disagreed with the code it stood in
    for.
    """
    def fake_field(pid, name):
        if not exists or not readable:
            return ""
        return {"status": status, "wchan": wchan, "cmdline": cmdline}[name]

    monkeypatch.setattr(PD, "_proc_field", fake_field)
    monkeypatch.setattr(os.path, "isdir", lambda path: exists)


def test_wchan_is_named_when_the_kernel_names_it(monkeypatch):
    """The whole point: `hrtimer_nanosleep` on a real `sleep` process is what
    tells a person the process is waiting on a timer rather than wedged.
    """
    _fake_proc(monkeypatch, STATUS_SLEEPING, wchan="hrtimer_nanosleep")
    out = PD._run({"pid": 100})
    assert "hrtimer_nanosleep" in out
    assert "sleeping" in out


def test_a_running_process_is_not_reported_as_stuck(monkeypatch):
    """**`wchan` reads `0` for a process that is on the CPU.** That is not a
    function name and not a place; printing `0` beside "waiting in" says a busy
    process is blocked in function zero.
    """
    _fake_proc(monkeypatch, STATUS_RUNNING, wchan="0")
    out = PD._run({"pid": 100})
    assert "nowhere" in out
    assert "busy" in out
    assert "waiting in: 0" not in out


def test_a_sleeping_process_with_no_name_does_not_claim_unity(monkeypatch):
    """**Measured: `ptrace_scope=1` here blanks `wchan` even for pid 1**, so
    this branch is the normal answer on a multi-user machine, not an edge case.
    The sentence has to admit the limit rather than invent a place.
    """
    _fake_proc(monkeypatch, STATUS_SLEEPING, wchan="0",
               cmdline="/sbin/init")
    out = PD._run({"pid": 100})
    assert "did not name a function" in out
    assert "cannot say what it is blocked on" in out


def test_a_kernel_thread_is_not_an_unknown(monkeypatch):
    """Measured: `kthreadd` sleeps with `wchan` = `0` **and** an empty cmdline,
    because `/proc/<pid>/wchan` only names *userspace* functions. Reporting
    that as a failure would call a correctly working kernel thread broken.
    """
    _fake_proc(monkeypatch, STATUS_KERNEL, wchan="0", cmdline="")
    out = PD._run({"pid": 2})
    assert "kernel thread" in out
    assert "not applicable" in out
    assert "did not name" not in out


def test_a_zombie_is_explained_rather_than_listed(monkeypatch):
    """A `Z` with no explanation is a code. What a zombie *is* - finished,
    waiting to be reaped - is the useful half.
    """
    _fake_proc(monkeypatch, STATUS_ZOMBIE, wchan="0", cmdline="python3")
    out = PD._run({"pid": 100})
    assert "zombie" in out
    assert "has finished" in out
    assert "parent" in out


def test_uninterruptible_sleep_names_the_thing_it_usually_means(monkeypatch):
    """`D` is the state a failing drive shows, and it is the one state where
    "it is waiting" is genuinely alarming rather than normal.
    """
    _fake_proc(monkeypatch, STATUS_DISK, wchan="", cmdline="postgres")
    out = PD._run({"pid": 100})
    assert "disk" in out.lower()
    assert "failing drive" in out


def test_a_gone_process_and_an_unreadable_one_are_different_answers(monkeypatch):
    """Two refusals that read alike are the defect this repo keeps recording:
    "it has finished" and "I was not allowed to look" are opposite claims.
    """
    _fake_proc(monkeypatch, STATUS_SLEEPING, exists=True, readable=False)
    out = PD._run({"pid": 100})
    assert "another user" in out

    _fake_proc(monkeypatch, STATUS_SLEEPING, exists=False, readable=False)
    gone = PD._run({"pid": 100})
    assert "no process" in gone
    assert "another user" not in gone


@pytest.mark.parametrize("bad", ["not-a-number", "", "  "])
def test_nonsense_is_refused_not_guessed(bad):
    out = PD._run({"pid": bad})
    assert "not a process id" in out or "need a process id" in out


def test_no_pid_at_all_is_a_question_not_an_error():
    out = PD._run({})
    assert "need a process id" in out
    assert "list_processes" in out


def test_a_negative_or_zero_pid_is_refused():
    for bad in (0, -1):
        out = PD._run({"pid": bad})
        assert "not a process id" in out


def test_it_never_attaches_or_traces_anything():
    """**The design boundary, asserted.** `strace` attaches to a running
    process and `strace <command>` runs one; both are out. The reason this is a
    test rather than a comment is that the boundary is invisible in the code -
    the module looks like any other reader - and a future "let us just strace
    it" would leave no failing test behind.
    """
    source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa/skills"
              / "process_detail.py").read_text()
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    # Strip the module docstring, which explains at length why strace is absent.
    body = code.split('"""', 2)[-1] if code.count('"""') >= 2 else code
    for forbidden in ("strace", "ptrace", "subprocess", "Popen"):
        assert forbidden not in body, (
            f"{forbidden} must not appear in executable code: this skill "
            f"reads /proc and attaches to nothing")


def test_the_real_proc_is_read_when_this_machine_allows_it():
    """Not a stub: the real `/proc`, on this process's own pid.

    A self-test that could not run would let every stubbed case above pass
    against a module that reads nothing.
    """
    facts, problem = PD._describe(os.getpid())
    assert problem == "", problem
    assert facts["name"], facts
    assert facts["state"], facts
    assert facts["cmdline"], facts
    # wchan is genuinely absent for a running process, and must not have been
    # turned into a function name.
    assert facts["wchan"] == "", facts


def test_a_real_sleeping_process_reports_its_wait(tmp_path):
    """Drive a real `sleep` - a process genuinely blocked in a timer - and
    read it through the real /proc rather than a fixture.
    """
    import subprocess
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        import time
        time.sleep(0.4)
        facts, problem = PD._describe(sleeper.pid)
        assert problem == "", problem
        assert facts["name"] == "sleep", facts
        assert facts["state"].startswith("S"), facts
        # On a kernel with wchan available this is the function name; where it
        # is withheld the state letter is still the honest answer, so the
        # assertion is on what must hold either way.
        out = PD._run({"pid": sleeper.pid})
        assert "sleeping" in out
        assert "waiting in:" in out
    finally:
        sleeper.kill()
        sleeper.wait()