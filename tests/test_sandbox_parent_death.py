"""A backgrounded skill command outlived the app that started it.

`start_new_session=True` is what makes the timeout fix work: it gives each child its own
process group so `os.killpg` can reach a whole `sh -c` tree instead of hitting Chronoa's
own group. It also detaches the child from that group, so killing Chronoa does not reach
it either.

A backgrounded command is the reachable case. The executor returns at once, reports
"Command started and continues running in the background (PID: ...)", and keeps no record
of the pid beyond that string. A crash, a `kill -9`, or a force-quit from the window
manager left the process running with nothing to reap it and no way to find it again.

Measured on this machine, with the real executor: a backgrounded `sleep 6; touch MARKER`
still wrote its marker after the parent was `SIGKILL`ed - `rc=-9`, no cleanup, no atexit.
The same test with `preexec_fn` removed writes the marker; with it, the marker never
appears. That pair is the whole of this file's evidence.

`assistd`'s `child_server` sets the equivalent itself - the supervisor is documented as
setting "stdio, the process group and the parent-death signal".

Two details are load-bearing and easy to lose:

  - **The race.** If the parent dies between `fork` and the `prctl`, the signal is never
    armed and nothing would ever notice. `_die_with_parent` therefore compares `getppid()`
    against the pid recorded immediately before the spawn and exits if they differ, which
    is the only moment the loss is observable.
  - **It must not break ordinary commands.** The first version of the helper called
    `dict.get()` with no key, which raised `TypeError` inside `preexec_fn`; every command
    then failed with "Exception occurred in preexec_fn". The first verification of this fix
    reported "no orphan" - a false pass produced by the command never running at all.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

#: The package directory as a *quoted* literal for the generated child scripts. Built
#: with `repr` rather than interpolated bare, which produced `sys.path.insert(0, /home/...)`
#: - a SyntaxError, so every child process died before running a line.
_PKG_DIR_REPR = repr(str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.sandbox import executor as executor_mod  # noqa: E402
from shani_chronoa.sandbox.executor import SandboxExecutor  # noqa: E402

#: Long enough that a surviving orphan is unambiguous, short enough to run in a suite.
_CHILD_SLEEP = 4
_SETTLE = _CHILD_SLEEP + 3


def _run_in_a_child_that_is_killed(command: str, marker: str) -> "tuple[str, int]":
    """Spawn the real executor in a child, SIGKILL that child, return its output.

    The kill is `SIGKILL` on purpose: no atexit handler, no `__del__`, no chance for
    anything to clean up. A fix that only helps on an orderly shutdown is not a fix.
    """
    code = f"""
import os, sys, signal
sys.path.insert(0, {_PKG_DIR_REPR})
from shani_chronoa.sandbox.executor import SandboxExecutor
exit_code, out, _ = SandboxExecutor()._run_host({command!r}, 10, __import__('time').monotonic())
print(out.strip()[:70], flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""
    child = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.PIPE, text=True, cwd=os.path.dirname(_REPO))
    line = child.stdout.readline().strip()
    child.wait()
    return line, child.returncode


@pytest.mark.skipif(sys.platform != "linux",
                    reason="PR_SET_PDEATHSIG is Linux-specific")
class TestAChildCannotOutliveTheApp:
    def test_a_backgrounded_command_does_not_survive_a_kill(self):
        marker = tempfile.mktemp(suffix=".marker")
        try:
            reported, returncode = _run_in_a_child_that_is_killed(
                f"sleep {_CHILD_SLEEP}; touch {marker} &", marker)
            assert returncode == -signal.SIGKILL, (
                f"the simulated app exited {returncode}, not by SIGKILL; the test "
                f"did not reproduce a force-quit")
            assert "background" in reported, (
                f"the executor did not take the background path: {reported!r}")
            time.sleep(_SETTLE)
            assert not os.path.exists(marker), (
                "a backgrounded command outlived the app that started it; the "
                "parent-death signal is not taking effect")
        finally:
            os.path.exists(marker) and os.unlink(marker)

    def test_the_command_actually_reported_itself_as_still_running(self):
        # Without this, the test above would also pass if the executor had simply
        # failed - which it did, once, while the assertion still held.
        marker = tempfile.mktemp(suffix=".marker")
        try:
            reported, _ = _run_in_a_child_that_is_killed(
                f"sleep {_CHILD_SLEEP}; touch {marker} &", marker)
            assert "continues running in the background" in reported, (
                f"expected the background-running report, got {reported!r}")
        finally:
            os.path.exists(marker) and os.unlink(marker)


class TestOrdinaryCommandsAreUnaffected:
    """A `preexec_fn` that raises takes every command down with it.

    The first version of `_die_with_parent` called `dict.get()` with no key, so it raised
    `TypeError` inside the forked child. Every command then failed with "Exception
    occurred in preexec_fn", and the orphan test reported success because nothing had
    actually run.
    """

    def test_a_plain_command_still_works(self):
        exit_code, out, _ = SandboxExecutor()._run_host(
            "echo hello-from-sandbox", 10, time.monotonic())
        assert exit_code == 0, f"exit {exit_code}: {out!r}"
        assert "hello-from-sandbox" in out

    def test_the_helper_runs_without_raising_in_a_fresh_interpreter(self):
        # Called the way the forked child calls it, with the expected parent recorded.
        code = f"""
import os, sys
sys.path.insert(0, {_PKG_DIR_REPR})
from shani_chronoa.sandbox import executor as E
E._EXPECTED_PARENT["pid"] = os.getppid()
E._die_with_parent()
print("clean")
"""
        result = subprocess.run([sys.executable, "-c", code],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:300]
        assert "clean" in result.stdout

    def test_a_failing_prctl_does_not_fail_the_command(self):
        # An unsupported kernel, or a seccomp policy that blocks prctl, must leave the
        # command runnable rather than erroring out of preexec_fn.
        original = executor_mod._PR_SET_PDEATHSIG
        try:
            executor_mod._PR_SET_PDEATHSIG = -1  # EINVAL
            exit_code, out, _ = SandboxExecutor()._run_host(
                "echo still-works", 10, time.monotonic())
            assert exit_code == 0, f"exit {exit_code}: {out!r}"
            assert "still-works" in out
        finally:
            executor_mod._PR_SET_PDEATHSIG = original


class TestTheExpectedParentIsRecordedAndCleared:
    def test_it_is_empty_after_a_spawn(self):
        # A stale entry would make a later, unrelated child compare against the wrong
        # pid and exit for no reason.
        SandboxExecutor()._run_host("echo x", 10, time.monotonic())
        assert not executor_mod._EXPECTED_PARENT, (
            f"the expected-parent record survived the spawn: "
            f"{executor_mod._EXPECTED_PARENT}")

    def test_the_record_is_consulted_rather_than_assumed(self):
        # The race guard. With no record, the helper cannot tell "parent alive" from
        # "parent already gone", so it arms the signal and carries on - which is the
        # right default, and is why the record is cleared rather than left set.
        assert "_EXPECTED_PARENT.get" in (
            Path(executor_mod.__file__).read_text())
