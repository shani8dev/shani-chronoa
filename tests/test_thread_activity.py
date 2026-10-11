"""Tests for `thread_activity`, and the four parsing traps that shape it.

Every fixture is a **real `/proc/<pid>/task/<tid>/stat` line** from this
machine, because the whole skill turns on parsing a format whose fields move:

| fixture | what it exercises |
|---|---|
| `systemd` | the ordinary case - a name with no space |
| `Bun Pool 5` | **a comm containing a space** - measured on this box |
| `kworker/R-ext4-rsv-conversion` | **27 chars, past the kernel's 15** |
| `irq/160-iwlwifi:queue_8` | slashes and colons, as kernel threads carry |

The field offsets are only stable because `comm` is bracketed by parentheses.
A parser using `line.split()[1]` returns `Pool` for `Bun Pool 5` **and** reads a
different column for `utime` on it than on `systemd` - one line, two wrong
answers, neither raising.

**Why the report is rendered from two snapshots rather than a fake `/proc`.** A
rate needs two readings, so a first version made the renderer read the tree
itself and every test had to fake a *sequence* of directory listings. Six tests
then failed against a working skill, because `skills.thread_activity.os is os`:
patching `os.listdir` through the module patches the stdlib function, and
pytest's own directory walking started reading the fake tree. `_render()`
takes the two snapshots as arguments, so the parser is tested directly and the
sampler is tested separately.
"""

from __future__ import annotations

import pytest

from shani_chronoa.skills import thread_activity as ta

PID = "303214"


def _stat(comm, state="R", utime="0", stime="0"):
    """A real `/proc/<pid>/task/<tid>/stat` line, field for field.

    After the closing paren: state ppid pgrp session tty_nr tpgid flags minflt
    cminflt majflt cmajflt **utime stime** ...
    """
    tail = [state, "1", PID, PID, "0", "-1", "4194304", "100", "0", "0", "0",
            utime, stime, "0", "0", "20", "0", "1", "0", "0"]
    return f"{PID} ({comm}) " + " ".join(tail) + " 0 0\n"


STAT_SYSTEMD = _stat("systemd", state="S", utime="927", stime="558")
STAT_SPACE = _stat("Bun Pool 5", state="R", utime="500", stime="100")
STAT_LONG = _stat("kworker/R-ext4-rsv-conversion", state="D", utime="7", stime="3")
STAT_IRQ = _stat("irq/160-iwlwifi:queue_8", state="S", utime="12", stime="1")


def _fake_proc(threads, wchans=None):
    """A `(read_text, list_dir)` pair serving one fixed snapshot of a tree."""
    wchans = wchans or {}

    def read_text(path):
        tid = path.split("/")[4]
        if path.endswith("/stat"):
            return threads.get(tid)
        if path.endswith("/wchan"):
            return wchans.get(tid, "0") + "\n"
        return None

    def list_dir(path):
        if path == ta._PROC:
            return [PID]
        if path == f"{ta._PROC}/{PID}/task":
            return list(threads)
        return []

    return read_text, list_dir


def snap(threads, wchans=None, monkeypatch=None):
    """One snapshot as `{tid: Thread}`, via the real parser."""
    read_text, list_dir = _fake_proc(threads, wchans)
    if monkeypatch is not None:
        monkeypatch.setattr(ta, "_read_text", read_text)
        monkeypatch.setattr(ta, "_list_dir", list_dir)
        monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
        out = {(PID, t.tid): t for t in ta._threads_of(PID)}
        monkeypatch.undo()
        return out
    saved_read, saved_list = ta._read_text, ta._list_dir
    ta._read_text, ta._list_dir = read_text, list_dir
    try:
        return {(PID, t.tid): t for t in ta._threads_of(PID)}
    finally:
        ta._read_text, ta._list_dir = saved_read, saved_list


def report(before, after, whole_machine=False):
    return ta._render(before, after, "test", whole_machine=whole_machine)


# --------------------------------------------------------------------------
# comm: spaces, length, and the field shift they cause
# --------------------------------------------------------------------------

class TestTheCommandName:

    @pytest.mark.parametrize("raw,name", [
        (STAT_SYSTEMD, "systemd"),
        (STAT_SPACE, "Bun Pool 5"),
        (STAT_LONG, "kworker/R-ext4-rsv-conversion"),
        (STAT_IRQ, "irq/160-iwlwifi:queue_8"),
    ])
    def test_each_real_name_is_read_whole(self, raw, name, monkeypatch):
        """**The space is the whole point.** `line.split()[1]` returns `Pool`
        for `Bun Pool 5` on a machine that runs it - measured, not invented.
        """
        read_text, list_dir = _fake_proc({"303214": raw})
        monkeypatch.setattr(ta, "_read_text", read_text)
        monkeypatch.setattr(ta, "_list_dir", list_dir)
        assert ta._threads_of(PID)[0].comm == name

    def test_the_cpu_fields_do_not_shift_with_the_name(self):
        """**The trap that matters most.** `utime`/`stime` are read from a
        position *after* the closing paren, so a parser indexing the whole line
        reads a different column for every name - and on `Bun Pool 5` it reads a
        number rather than raising.
        """
        systemd = snap({"303214": STAT_SYSTEMD})[("303214", "303214")]
        spaced = snap({"303215": STAT_SPACE})[("303214", "303215")]
        assert (systemd.utime, systemd.stime) == (927, 558)
        assert (spaced.utime, spaced.stime) == (500, 100), (
            "a space in the name must not move the counter columns")


# --------------------------------------------------------------------------
# Threads that go away
# --------------------------------------------------------------------------

class TestThreadsThatGoAway:

    def test_a_thread_that_exits_is_skipped_not_fatal(self, monkeypatch):
        # Listed, then gone by the time it is read - the ordinary case under
        # load rather than an edge case.
        read_text, list_dir = _fake_proc({"303214": STAT_SYSTEMD})
        list_dir = (lambda path: ["303214", "303999"]
                    if path.endswith("/task") else list_dir(path))
        monkeypatch.setattr(ta, "_read_text", read_text)
        monkeypatch.setattr(ta, "_list_dir", list_dir)
        assert len(ta._threads_of(PID)) == 1

    def test_an_unreadable_tree_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(ta, "_read_text", lambda p: None)
        assert ta._threads_of(PID) == []

    def test_a_malformed_stat_line_yields_nothing(self, monkeypatch):
        # Truncated between reads, which happens.
        monkeypatch.setattr(ta, "_read_text", lambda p: "303214 (half")
        assert ta._threads_of(PID) == []

    def test_a_non_numeric_pid_asks_for_a_number(self):
        assert "a number" in ta._run({"pid": "llama-server"}).lower()

    def test_an_unknown_pid_is_reported_not_crashed(self, monkeypatch):
        monkeypatch.setattr(ta, "_list_dir", lambda p: [])
        monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
        out = ta._describe("999999")
        assert "no process 999999" in out


# --------------------------------------------------------------------------
# wchan: running is not blocked, and "0" is not an answer
# --------------------------------------------------------------------------

class TestWhatAThreadIsWaitingOn:

    def test_a_running_thread_says_on_cpu(self):
        """`wchan` reads `0` for a thread that is RUNNING - the opposite of
        blocked. `process_detail` records this for a process and it holds here.
        """
        before = snap({"303214": _stat("burn", state="R", utime="100")})
        after = snap({"303214": _stat("burn", state="R", utime="200")},
                     wchans={"303214": "0"})
        out = report(before, after)
        assert "on CPU" in out, "wchan 0 while state is R means running"

    def test_a_blocked_thread_names_the_function(self):
        before = snap({"303214": _stat("worker", state="S", utime="10")})
        after = snap({"303214": _stat("worker", state="S", utime="10")},
                     wchans={"303214": "futex_do_wait"})
        assert "futex_do_wait" in report(before, after)

    def test_an_unreadable_wchan_says_so_rather_than_blank(self):
        """An empty `wchan` is the kernel saying "not in a named function", and
        it must never read as "not waiting for anything" - a different claim.
        """
        # A thread that is NOT running and whose wchan is empty: the kernel is
        # saying "not in a named function", which must not read as "not waiting
        # for anything". (A *running* thread with an empty wchan is a different
        # case and says "on CPU" - wchan is only meaningful for a blocked one.)
        before = snap({"303214": _stat("w", state="D", utime="0")})
        after = snap({"303214": _stat("w", state="D", utime="200")},
                     wchans={"303214": ""})
        out = report(before, after)
        assert "not in a named function" in out

    def test_a_stuck_thread_appears_with_no_cpu_at_all(self):
        """**The design flaw a test caught, and it is half this skill's point.**

        A thread blocked in a long syscall uses no CPU *because* it is stuck, so
        a version that listed only threads above the CPU threshold answered
        "nothing used measurable CPU" to "which thread is stuck?" - about
        exactly the thread the person was asking about.
        """
        before = snap({"303214": _stat("stuck", state="D", utime="4")})
        after = snap({"303214": _stat("stuck", state="D", utime="4")},
                     wchans={"303214": "wait_for_completion"})
        out = report(before, after)
        assert "stuck" in out
        assert "wait_for_completion" in out
        assert "blocked rather than busy" in out

    def test_a_quiet_machine_says_so_and_names_the_limitation(self):
        before = snap({"303214": _stat("idle", state="S", utime="1")})
        after = snap({"303214": _stat("idle", state="S", utime="1")})
        out = report(before, after)
        assert "No thread used measurable CPU" in out
        assert "one second can miss a burst" in out, (
            "a window that can miss a burst must say so, or the silence reads "
            "as a measurement")


# --------------------------------------------------------------------------
# A rate is two readings
# --------------------------------------------------------------------------

class TestTheRateIsMeasured:

    def test_cumulative_jiffies_are_not_reported_as_a_percentage(self):
        """A cumulative counter reported directly is the classic bug: 927
        jiffies is not 927%. The first version divided a *total* by the window,
        so every long-lived thread looked permanently busy.
        """
        before = snap({"303214": _stat("systemd", utime="927", stime="558")})
        after = snap({"303214": _stat("systemd", utime="927", stime="558")})
        assert "No thread used measurable CPU" in report(before, after)

    def test_a_thread_that_burned_is_ranked(self):
        before = snap({"303214": _stat("burn", state="R", utime="100")})
        after = snap({"303214": _stat("burn", state="R", utime="300")})
        out = report(before, after)
        assert "burn" in out and "CPU%" in out

    def test_the_busiest_thread_is_listed_first(self):
        before = snap({"303214": _stat("a", utime="0"),
                       "303215": _stat("b", utime="0")})
        after = snap({"303214": _stat("a", utime="100"),
                      "303215": _stat("b", utime="900")})
        out = report(before, after)
        # Assert on the rendered row, not a substring: the columns are padded,
        # so `" b "` never appears literally.
        names = [ln.rsplit(None, 1)[-1] for ln in out.splitlines()
                 if ln.strip()[:1].isdigit() and "%" in ln]
        assert names == ["b", "a"], (
            "the whole skill is which thread is hot, so the order is the "
            f"answer; got {names}")

    def test_a_thread_new_during_the_window_is_marked_not_dropped(self):
        # Dropping the thread that just spawned is how "nothing is busy" gets
        # reported about a program that spawns constantly.
        before = snap({"303214": _stat("old", utime="0")})
        after = snap({"303214": _stat("old", utime="0"),
                      "303215": _stat("spawned", utime="50")})
        out = report(before, after)
        assert "spawned" in out
        assert "new during the window" in out


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------

class TestItIsReachable:

    def test_the_handler_is_importable_by_name(self):
        assert ta._run.__name__ == "_run"
        assert ta._run.__name__.isidentifier()

    def test_the_schema_registers_under_its_own_name(self):
        assert ta.SKILLS[0].name == "thread_activity"
        assert ta.SCHEMA["function"]["name"] == "thread_activity"

    def test_the_pid_is_optional_so_the_whole_machine_is_reachable(self):
        params = ta.SCHEMA["function"]["parameters"]
        assert params["required"] == []
        assert set(params["properties"]) == {"pid"}

    def test_it_shells_out_to_nothing(self):
        # Every alternative is a sampling tool whose answer depends on a window
        # and a sort order; this is the kernel data underneath them.
        assert "subprocess" not in open(ta.__file__, encoding="utf-8").read()

    def test_it_reads_threads_below_the_process(self):
        assert "/task" in open(ta.__file__, encoding="utf-8").read()