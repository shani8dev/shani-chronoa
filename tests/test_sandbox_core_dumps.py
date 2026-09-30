"""Core dumps are a way to write a process's secrets to disk without meaning to.

Every child this module spawns inherits a full copy of what Chronoa was holding:
`secrets_manager.inject_environment()` puts cloud provider API keys into the
environment it hands over, and a captured screenshot or a transcript is in the
address space. A core dump writes that out in cleartext when the process dies -
which is precisely the moment nobody is watching - into a directory the user
does not think of as holding credentials.

Two separate facts are under test, and only one of them survives `execve`:

- `RLIMIT_CORE = 0` does survive it. Rlimits are per-process and inherited, so
  the limit a child starts under is the one the wrapped command runs with.
  Measured: set to (0, 0) in a parent, read back as (0, 0) after `execve`.
- `PR_SET_DUMPABLE = 0` does not. `setup_new_exec()` resets it to
  `SUID_DUMP_USER` for any binary that is not setuid. Measured: `PR_GET_DUMPABLE`
  reads 0 immediately before `execve` and 1 in the exec'd program. It therefore
  covers the fork-to-exec window - which is still where the Python interpreter
  holding the injected keys actually lives.

Every assertion below raises the *parent's* `RLIMIT_CORE` first. Ubuntu's shell
default is already `ulimit -c 0`, so a test that merely observed `(0, 0)` in the
child would pass against a version that hardens nothing at all. A control that
cannot fail is not a control, and that is the whole reason for the fixture.
"""

import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from shani_chronoa.sandbox import executor as executor_mod
from shani_chronoa.sandbox.executor import SandboxExecutor
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel

PKG_PARENT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr",
    "lib",
    "shani-chronoa",
)

#: Reports what the kernel thinks of the process it is running in.
REPORT = (
    "import ctypes, os, resource;"
    "l = ctypes.CDLL('libc.so.6', use_errno=True);"
    "l.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4;"
    "core = [x.split(':', 1)[1].strip() for x in "
    "open('/proc/self/status') if x.startswith(('CoreDumping', 'Dumpable'))];"
    "print('CORE', resource.getrlimit(resource.RLIMIT_CORE)[0],"
    " 'DUMPABLE', l.prctl(3, 0, 0, 0, 0), 'KERNEL', core[0] if core else 'n/a')"
)

#: A limit that is unmistakably not zero, so "0 in the child" means something.
NONZERO_CORE = 1 << 24


@pytest.fixture
def nonzero_parent_core_limit():
    """Give this process a core limit worth removing, then put it back.

    Without this the suite proves nothing on any host whose shell default is
    already `ulimit -c 0` - which is most of them, and is this one.
    """
    original = resource.getrlimit(resource.RLIMIT_CORE)
    ceiling = original[1]
    raise_to = NONZERO_CORE if ceiling == resource.RLIM_INFINITY else min(
        NONZERO_CORE, ceiling
    )
    resource.setrlimit(resource.RLIMIT_CORE, (raise_to, original[1]))
    try:
        assert resource.getrlimit(resource.RLIMIT_CORE)[0] == raise_to, (
            "the fixture could not raise RLIMIT_CORE, so a child reporting 0 "
            "would prove nothing"
        )
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_CORE, original)


def _fields(report):
    parts = report.split()
    return int(parts[parts.index("CORE") + 1]), parts[parts.index("DUMPABLE") + 1]


class TestConfinedChildrenCannotWriteThemselvesToDisk:
    def test_the_host_path_child_runs_with_no_core_limit(self, nonzero_parent_core_limit):
        # Stated as a contrast, so the control is visible in the test itself:
        # this process had a limit worth removing, the child does not.
        assert resource.getrlimit(resource.RLIMIT_CORE)[0] == NONZERO_CORE

        rc, out, _ = SandboxExecutor()._run_host(
            ["python3", "-c", REPORT], 20, time.monotonic()
        )

        assert rc == 0, out
        soft, _ = _fields(out)
        assert soft == 0, (
            f"the child inherited RLIMIT_CORE {soft} from a parent holding "
            f"{NONZERO_CORE}; a core dump of it would contain the injected API "
            f"keys. Report: {out!r}"
        )

    def test_the_landlock_path_child_runs_with_no_core_limit(self, nonzero_parent_core_limit):
        """The path that had no `preexec_fn` at all.

        `_run_host` already had one, so a change there is invisible to half the
        spawn paths; this is the one that spawns a Python interpreter holding
        the injected secrets and then execs the skill.
        """
        assert resource.getrlimit(resource.RLIMIT_CORE)[0] == NONZERO_CORE

        workspace = tempfile.mkdtemp()
        try:
            rc, out, _ = SandboxExecutor(
                sandboxes_root=tempfile.mkdtemp()
            ).execute(
                ["python3", "-c", REPORT],
                SandboxConfig(
                    level=SandboxLevel.LEVEL_1_READONLY, isolated_dir=workspace
                ),
                agent_id="core-probe",
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

        assert rc == 0, out
        soft, _ = _fields(out)
        assert soft == 0, (
            f"the confined child inherited RLIMIT_CORE {soft}; report: {out!r}"
        )

    def test_the_kernel_reports_the_child_as_not_core_dumping(
        self, nonzero_parent_core_limit
    ):
        rc, out, _ = SandboxExecutor()._run_host(
            ["python3", "-c", REPORT], 20, time.monotonic()
        )

        assert rc == 0, out
        assert "KERNEL 0" in out, (
            f"the kernel still reports the child as core-dumping capable; {out!r}"
        )

    def test_chronoa_itself_is_not_hardened(self, nonzero_parent_core_limit):
        """A preexec that leaks into the parent is worse than no preexec.

        `preexec_fn` runs in the forked child, and that is the only reason it is
        the right place. Anything done in the parent would take away Chronoa's
        own ability to produce a core dump - which is a debugging tool - over a
        threat that only applies to the confined child.
        """
        SandboxExecutor()._run_host(["python3", "-c", REPORT], 20, time.monotonic())

        assert resource.getrlimit(resource.RLIMIT_CORE)[0] == NONZERO_CORE, (
            "running a command changed this process's own core limit"
        )


class TestTheHardeningItselfBetweenForkAndExec:
    def test_dumpable_is_cleared_before_the_exec(self):
        """The half that does not survive execve, tested where it does hold.

        There is no way to observe this from outside the child - which is exactly
        why it needs a direct test rather than an inference from the rlimit.
        """
        code = f"""
import ctypes, os, resource, sys
sys.path.insert(0, {PKG_PARENT!r})
from shani_chronoa.sandbox import executor as E
l = ctypes.CDLL('libc.so.6', use_errno=True)
l.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
print('before', l.prctl(3, 0, 0, 0, 0), resource.getrlimit(resource.RLIMIT_CORE))
E._harden_child()
print('after', l.prctl(3, 0, 0, 0, 0), resource.getrlimit(resource.RLIMIT_CORE))
"""
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )

        assert result.returncode == 0, result.stderr
        before, after = result.stdout.splitlines()
        assert before.startswith("before 1"), f"premise broken: {before!r}"
        assert after.startswith("after 0 (0, 0)"), (
            f"_harden_child() did not clear the dumpable flag and the core limit: "
            f"{after!r}"
        )

    def test_a_failing_prctl_does_not_fail_the_command(self):
        """The existing rule, kept for the new call.

        `_die_with_parent` was given this treatment for a good reason: a
        `preexec_fn` that raises takes every command down with an opaque
        "Exception occurred in preexec_fn". A seccomp policy or an old kernel
        refusing prctl must not do the same to the core-dump hardening.
        """
        original = executor_mod._PR_SET_DUMPABLE
        executor_mod._PR_SET_DUMPABLE = -1  # EINVAL
        try:
            rc, out, _ = SandboxExecutor()._run_host(
                ["echo", "still-works"], 10, time.monotonic()
            )
        finally:
            executor_mod._PR_SET_DUMPABLE = original

        assert rc == 0, f"exit {rc}: {out!r}"
        assert "still-works" in out


class TestBothSpawnPathsUseTheSameChildHardening:
    def test_neither_spawn_path_is_left_without_a_hardened_child(self):
        """A missing `preexec_fn` fails open and fails silently.

        Both assertions are about the source rather than about behaviour, because
        the Landlock path's behaviour was invisible from outside until now: the
        wrapper execs the skill, so nothing in the skill's output says whether
        the wrapper that spawned it was hardened.
        """
        source = open(executor_mod.__file__, encoding="utf-8").read()

        assert source.count("preexec_fn=_harden_child") == 2, (
            "expected both the Popen in _run_host and the subprocess.run in "
            f"_run_landlock to harden their child; found "
            f"{source.count('preexec_fn=_harden_child')}"
        )

    def test_the_landlock_wrapper_itself_would_not_need_a_second_measure(self):
        # RLIMIT_CORE is inherited across the wrapper's execvp, so hardening the
        # wrapper's own process is what carries the limit into the skill. Assert
        # the limit is inherited rather than assumed, because a future wrapper
        # that reset its own rlimits would quietly undo this.
        rc, out, _ = SandboxExecutor()._run_host(
            ["sh", "-c", f"exec python3 -c {REPORT!r}"], 20, time.monotonic()
        )

        assert rc == 0, out
        soft, _ = _fields(out)
        assert soft == 0, f"the limit did not survive exec: {out!r}"