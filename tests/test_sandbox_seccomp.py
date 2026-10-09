"""The seccomp filter is real, one-way, narrow, and off by default.

Every test here runs a real child through the real `SandboxExecutor` and reads
the answer out of that child, because the three ways a hand-assembled BPF
filter goes wrong are all invisible to a test that does not execute it:

- An inverted arch guard (`jt=0/jf=1` where `jt=1/jf=0` was needed) **installs
  cleanly**, makes `/proc/self/status` report `Seccomp: 2`, and then answers
  `ENOSYS` to every syscall including `write(2)`. The process dies before it
  prints anything, so a "did the filter install?" assertion passes against a
  filter that denies everything.
- A filter that returns `ALLOW` for a syscall it should deny passes every
  "is it installed" check and every "does it install" check, and denies
  nothing. Only asking the blocked syscall itself catches it.
- A filter that denies one syscall too many also installs cleanly and reports
  `Seccomp: 2`, and the symptom is a skill that broke somewhere else entirely.
  Hence the over-filtering tests below run the same `sh -c 'a; b &'`, `dd` and
  `env` shapes that `test_sandbox_parent_death.py` and
  `test_sandbox_argv_policy.py` depend on, plus a thread, a subprocess and a
  loopback socket, because those are what the real skill path uses.

The headline test is `test_the_memfd_exec_which_escapes_landlock...`. It runs
the *same* program twice through the *same* confined path, with the gate off and
then on: the first run escapes this repo's own Landlock ruleset, the second is
refused. That pairing is the point - the baseline run is the control, so a
refusal afterwards is attributable to the filter and not to the program being
incapable of running in the first place.

The refusals are proven by their *side effect*, not by their exit code: each
fail-closed test asks the child to create a marker file and then asserts the
marker does not exist. A 126 with the command still having run is the failure
mode that matters, and only the marker distinguishes it.
"""

from __future__ import annotations

import errno
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.sandbox import executor as executor_mod  # noqa: E402
from shani_chronoa.sandbox import seccomp as seccomp_mod  # noqa: E402
from shani_chronoa.sandbox.executor import SandboxExecutor  # noqa: E402
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel  # noqa: E402
from shani_chronoa.sandbox.seccomp import SECCOMP_SETTING, SeccompError  # noqa: E402

EXIT_SECURITY_ERROR = 126


def _memfd_exec_is_allowed_by_this_kernel() -> bool:
    """Can this kernel run a program out of a file with no pathname?

    **The premise of the headline test, established rather than assumed.** That
    test's control is: with the gate off, `memfd_create` + `execve` on the
    descriptor escapes this repo's own Landlock ruleset, so a refusal with the
    gate on is attributable to the filter and not to the program being unable to
    run at all. That is the correct shape - and it means the test can only run
    where the premise holds.

    **Measured on this machine (Ubuntu, kernel 7.0.0-38): the premise does not
    hold, and the reason is an LSM, not the syscall.** `memfd_create`, `write`
    and `fchmod` all succeed and `execve(fd)` is then killed with

        Security violation: Requested utility `3` does not match executable name:
          /memfd:chronoa-probe (deleted)

    which is **AppArmor's `exec` profile refusing a file with no pathname**, and
    it arrives on stderr with the *process already gone* - so a probe that
    catches `OSError` in Python sees nothing at all. That is why the first
    version of this check was wrong twice: it looked for an errno, found none,
    and would have skipped on any machine for any reason.

    So the probe runs the **real payload** - `/bin/echo` copied into the
    descriptor and executed with `MEMFD-EXECUTED` as its argument - in a
    separate process, and asks the only question that matters: did it print?
    That is the control's own assertion, so the guard and the test cannot
    disagree, and a probe that always returns False (a test that always skips)
    is not possible to confuse with one that always passes.
    """
    probe = textwrap.dedent("""
        import os
        fd = os.memfd_create("chronoa-premise-probe", 0)
        with open("/bin/echo", "rb") as handle:
            os.write(fd, handle.read())
        os.fchmod(fd, 0o700)
        os.execve(fd, ["/bin/echo", "PREMISE-OK"], {})
    """)
    try:
        done = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                              text=True, timeout=30)
    except Exception:  # noqa: BLE001 - cannot run a program, cannot have the premise
        return False
    return done.returncode == 0 and "PREMISE-OK" in done.stdout


_MEMFD_EXEC_ALLOWED = _memfd_exec_is_allowed_by_this_kernel()

#: A real program, run exactly the way `tools.py` runs every skill: a fresh
#: interpreter with `-c`. The ctypes dance is the smallest thing that can ask
#: the kernel a question about a specific syscall number, which is the only
#: way to observe a filter that works by denying particular syscalls.
_PROBE = """
import ctypes, errno, sys
libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.syscall.restype = ctypes.c_long
ctypes.set_errno(0)
rc = libc.syscall({number}, 0, 0, 0)
err = ctypes.get_errno()
print("rc=%d errno=%d name=%s" % (rc, err, errno.errorcode.get(err, "none")))
"""


def _probe(number: int) -> str:
    return _PROBE.replace("{number}", str(number))


@pytest.fixture
def executor(tmp_path):
    return SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))


@pytest.fixture
def gate(chronoa_config):
    """Turn the filter on through the real gsetting, and prove it took.

    The assertion is not decoration. This repo's schema has silently discarded
    itself whole on a malformed key, taking every other key with it, so a test
    that sets a key and assumes it landed would go on to "pass" against a
    filter that was never requested.
    """
    from shani_chronoa.config import ChronoaConfig

    chronoa_config.set(SECCOMP_SETTING, "true")
    assert ChronoaConfig().get_bool(SECCOMP_SETTING, False) is True, (
        f"{SECCOMP_SETTING} did not read back as true; the schema may have "
        f"been discarded by glib-compile-schemas"
    )
    return chronoa_config


def _level_3(timeout: int = 30) -> SandboxConfig:
    return SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=timeout)


def _python(program: str) -> "list[str]":
    return [sys.executable, "-c", textwrap.dedent(program)]


def _marker_program(marker: Path) -> str:
    """A program that proves it ran by creating `marker`."""
    return f"""
        from pathlib import Path
        Path({str(marker)!r}).write_text("ran")
        print("RAN")
    """


# ---------------------------------------------------------------------------
# The gate: off by default, and off means inert
# ---------------------------------------------------------------------------


class TestTheGate:
    def test_the_key_survives_schema_compilation(self, chronoa_config):
        """The key is in the *compiled* schema, not just the XML.

        `_valid_keys` is populated by looking the key up in the compiled
        schema, so this is the check that would have caught the whole-file
        discard this repo has already been bitten by once.
        """
        assert SECCOMP_SETTING in chronoa_config._valid_keys

    def test_the_key_defaults_to_off(self, chronoa_config):
        assert chronoa_config.get_bool(SECCOMP_SETTING, False) is False

    def test_the_declared_default_is_false_in_the_xml(self):
        import re
        xml = (_REPO / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text()
        match = re.search(
            rf'<key name="{SECCOMP_SETTING}" type="b">\s*<default>(\w+)</default>', xml)
        assert match, f"{SECCOMP_SETTING} is not declared as a boolean in the schema"
        assert match.group(1) == "false", (
            f"the schema default is {match.group(1)!r}; this control ships off"
        )

    def test_with_the_gate_off_the_child_reports_itself_unfiltered(self, executor):
        code, out, _ = executor.execute(
            _python("""
                for line in open("/proc/self/status"):
                    if line.startswith("Seccomp:"):
                        print("SECCOMP", line.split()[1])
            """),
            _level_3())
        assert code == 0, out
        assert "SECCOMP 0" in out, (
            f"a child ran with the gate off and still reported a filter: {out}")

    def test_with_the_gate_off_no_filter_is_ever_requested(self, executor, monkeypatch):
        calls = []
        real = seccomp_mod.apply
        monkeypatch.setattr(seccomp_mod, "apply",
                            lambda: calls.append(1) or real())
        executor.execute(_python("print('hi')"), _level_3())
        assert calls == [], "seccomp.apply() ran with the gate off"

    def test_the_pending_flag_is_cleared_after_a_call(self, executor, gate):
        executor.execute(_python("print('hi')"), _level_3())
        assert executor_mod._PENDING_SECCOMP == {}, (
            "a stale _PENDING_SECCOMP would filter the next command, which never "
            "asked for it, and a filter cannot be removed once installed")


# ---------------------------------------------------------------------------
# The filter is real
# ---------------------------------------------------------------------------


class TestTheFilterIsReal:
    def test_the_child_reports_itself_filtered(self, executor, gate):
        code, out, _ = executor.execute(
            _python("""
                for line in open("/proc/self/status"):
                    if line.startswith("Seccomp:"):
                        print("SECCOMP", line.split()[1])
            """),
            _level_3())
        assert code == 0, out
        assert "SECCOMP 2" in out, f"the child is not under a filter: {out}"

    def test_mount_is_denied_with_eperm_inside_the_sandbox(self, executor, gate):
        """The evidence the task asks for: a blocked syscall, and its errno."""
        code, out, _ = executor.execute(_python(_probe(165)), _level_3())
        assert code == 0, out
        assert "errno=1" in out, f"mount(2) was not denied with EPERM: {out}"
        assert "name=EPERM" in out, out

    @pytest.mark.parametrize("name, number", [
        ("memfd_create", 319), ("bpf", 321), ("unshare", 272),
        ("setns", 308), ("init_module", 175), ("kexec_load", 246),
        ("io_uring_setup", 425), ("process_vm_readv", 310),
        ("pidfd_getfd", 438), ("move_mount", 429),
    ])
    def test_each_of_the_hard_syscalls_is_denied(self, executor, gate, name, number):
        code, out, _ = executor.execute(_python(_probe(number)), _level_3())
        assert code == 0, out
        assert "errno=1" in out, f"{name}({number}) was not denied: {out}"

    def test_every_listed_syscall_is_denied_by_the_running_filter(self, executor, gate):
        """One child checks the whole list, so the list cannot rot unnoticed.

        Driven by `seccomp.BLOCKED` rather than a copy of it, so a syscall
        added to the list without a matching test here is still covered.
        """
        program = f"""
            import ctypes, errno, json
            from shani_chronoa.sandbox import seccomp
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.syscall.restype = ctypes.c_long
            allowed = []
            for entry in seccomp.BLOCKED:
                ctypes.set_errno(0)
                libc.syscall(entry.number, 0, 0, 0)
                if ctypes.get_errno() != errno.EPERM:
                    allowed.append(entry.name)
            print("NOT_DENIED", json.dumps(allowed))
        """
        code, out, _ = executor.execute(_python(program), _level_3())
        assert code == 0, out
        assert "NOT_DENIED []" in out, (
            f"these blocked syscalls were reachable under the running filter: {out}")

    @pytest.mark.skipif(
        not _MEMFD_EXEC_ALLOWED,
        reason="an LSM on this machine refuses to execute a file with no "
               "pathname (memfd_create + execve is killed with 'Security "
               "violation: Requested utility 3 does not match executable name'), "
               "so the control cannot escape and the refusal below would prove "
               "nothing about the filter",
    )
    def test_the_memfd_exec_which_escapes_landlock_is_refused(self, executor,
                                                               chronoa_config,
                                                               tmp_path):
        """The reason this module exists, end to end and with a control.

        A file with no pathname is not reachable by a rule granted on a path,
        so `memfd_create` + `execve` on the descriptor runs a program from
        inside this repo's own Landlock ruleset. The first half of this test
        demonstrates exactly that; the second shows the filter stopping it.
        Without the first half the second proves nothing, because "the program
        did not print" is also what a program that cannot start looks like.
        """
        workspace = tmp_path / "escape"
        workspace.mkdir()
        payload = _python("""
            import os
            fd = os.memfd_create("chronoa-probe", 0)
            with open("/bin/echo", "rb") as handle:
                os.write(fd, handle.read())
            os.fchmod(fd, 0o700)
            os.execve(fd, ["/bin/echo", "MEMFD-EXECUTED"], {})
        """)
        confined = SandboxConfig(level=SandboxLevel.LEVEL_2_ISOLATED_DEV,
                                 isolated_dir=str(workspace), timeout_seconds=30)

        # Control, gate still off: Landlock alone does not stop this.
        assert chronoa_config.get_bool(SECCOMP_SETTING, False) is False
        code, out, _ = executor.execute(payload, confined)
        assert "MEMFD-EXECUTED" in out, (
            f"the control did not escape, so the refusal below would prove "
            f"nothing: exit={code} out={out}")

        # Same executor, same level, same program, gate now on.
        chronoa_config.set(SECCOMP_SETTING, "true")
        code, out, _ = executor.execute(payload, confined)
        assert "MEMFD-EXECUTED" not in out, (
            f"memfd_create exec ran with the filter on: {out}")
        assert "PermissionError" in out or "Operation not permitted" in out, out

    def test_the_filter_is_assembled_with_a_bounded_instruction_count(self):
        _, count = seccomp_mod.program()
        assert 0 < count < 4096, (
            f"the kernel's BPF program limit is 4096 instructions; got {count}")


# ---------------------------------------------------------------------------
# One-way: the child cannot widen or replace the filter
# ---------------------------------------------------------------------------


class TestOneWay:
    def test_the_child_cannot_install_a_permissive_filter(self, executor, gate):
        """A filter the child can widen is not a filter - so prove it cannot.

        The child builds a BPF program that allows everything and tries to
        install it over the top. Linux ANDs filters, so a block cannot be
        *removed* by adding a permissive one; what this asserts is the
        stronger property Chronoa actually wants - the child cannot install a
        filter of its own at all, so the filter it runs under is the only one
        it will ever have.
        """
        program = """
            import ctypes, errno
            # A BPF program that allows everything: arch guard, load nr, allow.
            prog = [
                (0x20, 0, 0, 4), (0x15, 0, 1, 0xC000003E),
                (0x06, 0, 0, 0x7FFF0000), (0x20, 0, 0, 0),
                (0x06, 0, 0, 0x7FFF0000),
            ]
            class F(ctypes.Structure):
                _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                            ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]
            class P(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(F))]
            backing = (F * len(prog))(*[F(*i) for i in prog])
            fp = P(len=len(prog), filter=backing)
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.syscall.restype = ctypes.c_long
            ctypes.set_errno(0)
            rc = libc.syscall(317, 1, 0, ctypes.byref(fp))
            print("WIDEN rc=%d errno=%d" % (rc, ctypes.get_errno()))
        """
        code, out, _ = executor.execute(_python(program), _level_3())
        assert code == 0, out
        assert "errno=%d" % errno.EPERM in out, (
            f"the child installed its own filter: {out}")

    def test_seccomp_itself_is_denied_to_the_child(self, executor, gate):
        code, out, _ = executor.execute(_python(_probe(317)), _level_3())
        assert code == 0, out
        assert "errno=1" in out, f"seccomp(2) was reachable from the child: {out}"

    def test_prctl_set_seccomp_is_denied_to_the_child(self, executor, gate):
        code, out, _ = executor.execute(
            _python("""
                import ctypes, errno
                from shani_chronoa.sandbox import seccomp
                rc, err = seccomp.prctl_set_seccomp_attempt()
                print("PRCTL rc=%d errno=%d" % (rc, err))
            """),
            _level_3())
        assert code == 0, out
        assert f"errno={errno.EPERM}" in out, (
            f"the child installed a filter through prctl(PR_SET_SECCOMP): {out}")

    def test_other_prctl_calls_still_work(self, executor, gate):
        """The over-filtering guard for the argument check.

        `prctl` is denied for `PR_SET_SECCOMP` only, because `PR_SET_NAME` is
        how programs name their own threads. Denying all of `prctl` would pass
        every test above and break threaded programs with no visible cause.
        """
        code, out, _ = executor.execute(
            _python("""
                import ctypes
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                libc.prctl.restype = ctypes.c_int
                libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                       ctypes.c_ulong, ctypes.c_ulong]
                name = ctypes.create_string_buffer(b"chronoa-thread")
                print("PRCTL_NAME", libc.prctl(15, ctypes.addressof(name), 0, 0, 0))
            """),
            _level_3())
        assert code == 0, out
        assert "PRCTL_NAME 0" in out, f"prctl(PR_SET_NAME) was refused: {out}"


# ---------------------------------------------------------------------------
# Not over-broad: real skill shapes still work
# ---------------------------------------------------------------------------


class TestNotOverBroad:
    def test_a_real_skill_call_still_works(self, executor, gate):
        """An actual skill module, imported and called, as `tools.py` does it."""
        code, out, _ = executor.execute(
            _python("""
                from shani_chronoa.skills.clock import _run
                import sys
                result = _run({})
                sys.stdout.write("SKILL " + str(bool(result)))
            """),
            _level_3())
        assert code == 0, out
        assert "SKILL True" in out, f"a real skill call failed under the filter: {out}"

    def test_shell_sequencing_and_backgrounding_survive(self, executor, gate, tmp_path):
        """`test_sandbox_parent_death.py` depends on `;` and `&` working."""
        marker = tmp_path / "shell-marker"
        code, out, _ = executor.execute(
            ["sh", "-c", f"echo first; sleep 0.2; echo second > {marker} & wait; echo done"],
            _level_3())
        assert code == 0, out
        assert "done" in out, out
        assert marker.exists(), (
            f"the backgrounded half of an sh -c never ran under the filter: {out}")

    def test_dd_still_runs(self, executor, gate, tmp_path):
        """`dd` is refused by the argv policy on the default profile, so the
        canary here is that it is refused for the *same* reason, not that it
        succeeds. A filter that blocked `openat` would change the message."""
        code, out, _ = executor.execute(
            ["dd", "if=/dev/zero", f"of={tmp_path}/dd.out", "bs=1k", "count=1"],
            _level_3())
        assert "blocked by the sandbox policy" in out or code == 0, out

    def test_env_still_runs(self, executor, gate):
        code, out, _ = executor.execute(["env"], _level_3())
        assert code == 0, f"env failed under the filter: {out}"
        assert "PATH=" in out

    def test_sleep_still_runs(self, executor, gate):
        code, out, _ = executor.execute(["sleep", "0.1"], _level_3())
        assert code == 0, out

    def test_threads_and_subprocesses_still_work(self, executor, gate):
        """`clone`/`clone3` are the syscalls Python's own threads need.

        This is the test that would catch a future edit adding the clone family
        to the blocklist, which is the most plausible over-filtering mistake
        available here: glibc only falls back from `clone3` to `clone` on
        `ENOSYS`, so denying `clone3` outright kills every Python thread in
        every skill with an error that names neither.
        """
        code, out, _ = executor.execute(
            _python("""
                import subprocess, threading
                thread = threading.Thread(target=lambda: None)
                thread.start(); thread.join()
                result = subprocess.run(["/bin/echo", "hi"], capture_output=True)
                print("THREAD ok SUBPROCESS", result.stdout.decode().strip())
            """),
            _level_3())
        assert code == 0, out
        assert "THREAD ok SUBPROCESS hi" in out, (
            f"threads or subprocesses broke under the filter: {out}")

    def test_sockets_still_work(self, executor, gate):
        """Ollama, web search and the audio stack all need `socket(2)`.

        `profiles.py`'s module docstring records filtering it as a non-goal
        because it would break the session bus `LEVEL_3` exists to provide. If
        this test ever fails, that decision was reversed and the docstring
        needs to change with it - not just the list.
        """
        code, out, _ = executor.execute(
            _python("""
                import socket
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                port = listener.getsockname()[1]
                client = socket.create_connection(("127.0.0.1", port), 5)
                conn, _ = listener.accept()
                client.sendall(b"ping"); conn.recv(4)
                client.close(); conn.close(); listener.close()
                print("SOCKET ok")
            """),
            _level_3())
        assert code == 0, out
        assert "SOCKET ok" in out, f"loopback sockets broke under the filter: {out}"

    def test_the_landlock_wrapper_path_also_survives_the_filter(self, executor, gate,
                                                                 tmp_path):
        """`LEVEL_2` runs a Python wrapper that holds secrets before it execs."""
        code, out, _ = executor.execute(
            _python("print('WRAPPER ok')"),
            SandboxConfig(level=SandboxLevel.LEVEL_2_ISOLATED_DEV,
                          isolated_dir=str(tmp_path / "confined"), timeout_seconds=30))
        assert code == 0, out
        assert "WRAPPER ok" in out, out


# ---------------------------------------------------------------------------
# Fail closed: on but unavailable means refused, not run unfiltered
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_a_refusal_does_not_run_the_command(self, executor, gate, monkeypatch,
                                                tmp_path):
        marker = tmp_path / "must-not-exist"
        monkeypatch.setattr(seccomp_mod, "unavailable_reason",
                            lambda: "measured: this kernel cannot filter")
        code, out, _ = executor.execute(_python(_marker_program(marker)), _level_3())
        assert code == EXIT_SECURITY_ERROR, out
        assert not marker.exists(), (
            "the command ran unfiltered while the setting was on and the filter "
            "was unavailable - the exact defect this control exists to prevent")
        assert SECCOMP_SETTING in out, out
        assert "measured: this kernel cannot filter" in out, out
        # The *pre-flight* refusal specifically, not merely any 126. Two
        # independent mechanisms can refuse here - the parent's availability
        # check and the install failing inside the child - and this test exists
        # to pin the first. Asserting only the exit code let the parent's check
        # be deleted outright and this test still went green, because the child
        # consults the same `unavailable_reason` and refuses too. Measured, not
        # assumed: that is exactly what mutation M7 did.
        assert "one cannot be provided" in out, (
            f"the refusal did not come from the parent's pre-flight check: {out}")

    def test_a_filter_that_fails_inside_the_child_is_refused(self, executor, gate,
                                                             monkeypatch, tmp_path):
        marker = tmp_path / "must-not-exist-either"

        def _boom():
            raise SeccompError("measured: SECCOMP_SET_MODE_FILTER returned EINVAL")

        monkeypatch.setattr(seccomp_mod, "apply", _boom)
        code, out, _ = executor.execute(_python(_marker_program(marker)), _level_3())
        assert code == EXIT_SECURITY_ERROR, (
            f"an in-child install failure was reported as {code}, not a refusal: {out}")
        assert not marker.exists(), "the command ran with no filter installed"
        assert "EINVAL" in out, out
        # The in-child refusal specifically. The parent pre-flight would also
        # produce a 126 here, so the exit code alone cannot tell the two
        # mechanisms apart - see the sibling test above.
        assert "no filter was installed" in out and "EINVAL" in out, out

    def test_the_wrong_architecture_is_refused_rather_than_guessed(self, executor, gate,
                                                                   monkeypatch, tmp_path):
        marker = tmp_path / "must-not-exist-either"
        monkeypatch.setattr(seccomp_mod, "architecture", lambda: "aarch64")
        code, out, _ = executor.execute(_python(_marker_program(marker)), _level_3())
        assert code == EXIT_SECURITY_ERROR, out
        assert not marker.exists(), out
        assert "aarch64" in out, out

    def test_the_elevated_level_is_skipped_loudly_rather_than_broken(self, executor, gate,
                                                                    monkeypatch, caplog):
        """`PR_SET_NO_NEW_PRIVS` is what makes `pkexec`'s setuid stop working.

        So the filter must not be installed there. Applying it anyway would
        leave a level whose whole purpose is elevation silently broken;
        ignoring the situation would be "configured but not enforced". The
        middle path is neither, plus a log line naming the reason.

        Asserted on the decision rather than by running a real `pkexec`: the
        decision is `_PENDING_SECCOMP["enabled"]` at the moment the spawn
        happens, and that is captured here without a privileged child ever
        existing.
        """
        import logging
        seen = {}

        def _capture(argv, timeout, start_time, elevated=False):
            seen["enabled"] = executor_mod._PENDING_SECCOMP.get("enabled")
            seen["elevated"] = elevated
            return (0, "not really elevated", 0.0)

        monkeypatch.setattr(executor, "_run_host", _capture)
        with caplog.at_level(logging.WARNING, logger=executor_mod.__name__):
            code, out, _ = executor.execute(
                _python("print('hi')"),
                SandboxConfig(level=SandboxLevel.LEVEL_4_HOST_ROOT, timeout_seconds=30))
        assert code == 0, out
        assert seen["elevated"] is True, "the test did not reach the elevated path"
        assert seen["enabled"] is False, (
            "the filter was scheduled for a level where PR_SET_NO_NEW_PRIVS "
            "would break pkexec's setuid")
        joined = "\n".join(record.getMessage() for record in caplog.records)
        assert "PR_SET_NO_NEW_PRIVS" in joined, (
            f"the skip was not explained in the log: {joined!r}")
        assert "runs unfiltered" in joined, joined

    def test_the_elevated_level_does_not_refuse_the_call(self, executor, gate, monkeypatch):
        """Skipping the filter must not mean refusing the command.

        A user who turns the filter on and then asks for a privileged action
        gets the action, unfiltered and told so - not a 126, which would read as
        the filter refusing rather than the level being exempt from it.
        """
        monkeypatch.setattr(executor, "_run_host",
                            lambda *a, **k: (0, "elevated ok", 0.0))
        code, out, _ = executor.execute(
            _python("print('hi')"),
            SandboxConfig(level=SandboxLevel.LEVEL_4_HOST_ROOT, timeout_seconds=30))
        assert code == 0 and "elevated ok" in out

    def test_the_gate_defaults_to_off_so_none_of_this_can_happen_by_accident(
            self, chronoa_config):
        assert chronoa_config.get_bool(SECCOMP_SETTING, False) is False


# ---------------------------------------------------------------------------
# The list itself
# ---------------------------------------------------------------------------


class TestTheList:
    def test_no_syscall_is_in_both_lists(self):
        """A syscall in both is denied by the unconditional chain first.

        That is not theoretical: `ptrace` was in both lists in this module's
        first draft, so the `PTRACE_TRACEME` exception below it was unreachable
        and `ptrace(0)` returned `EPERM` anyway. The invariant is invisible in
        the code, so it is asserted here.
        """
        unconditional = {entry.number for entry in seccomp_mod.BLOCKED}
        conditional = {entry.number for entry in seccomp_mod.CONDITIONAL}
        assert not (unconditional & conditional), (
            f"syscalls in both lists, where the argument check can never be "
            f"reached: {sorted(unconditional & conditional)}")

    def test_socket_is_not_filtered(self):
        for entry in seccomp_mod.BLOCKED + seccomp_mod.CONDITIONAL:
            assert entry.name != "socket", (
                "socket(2) is a recorded non-goal; see profiles.py's module "
                "docstring before changing this")

    @pytest.mark.parametrize("name", ["clone", "clone3", "fork", "vfork", "execve",
                                      "execveat", "openat", "socket", "connect"])
    def test_nothing_a_skill_needs_daily_is_filtered(self, name):
        """The deliberate omissions, pinned so a later edit cannot quietly add them.

        `clone3` is the load-bearing one: glibc falls back to `clone` only on
        `ENOSYS`, so a filter denying `clone3` would break every Python thread
        in every skill. `execve`/`openat` are what a command *is*.
        """
        for entry in seccomp_mod.BLOCKED + seccomp_mod.CONDITIONAL:
            assert entry.name != name, f"{name} is filtered; that breaks skills"

    def test_no_blocked_syscall_is_privileged_only(self):
        """Syscalls that already return EPERM without privilege are not here.

        `reboot`, `swapon`, `quotactl`, `acct` and `name_to_handle_at` are all
        refused by the kernel for an unprivileged process already, so blocking
        them would be a list entry that reads as depth with no hole behind it.
        Excluded on purpose, and this test is what keeps the exclusion
        deliberate rather than accidental.
        """
        blocked = {entry.name for entry in seccomp_mod.BLOCKED + seccomp_mod.CONDITIONAL}
        for name in ("reboot", "swapon", "swapoff", "quotactl", "acct",
                     "name_to_handle_at", "open_by_handle_at", "keyctl",
                     "add_key", "request_key", "personality"):
            assert name not in blocked, (
                f"{name} is in the blocklist; it was excluded because it needs "
                f"privilege the child never has")

    def test_every_entry_carries_a_reason(self):
        for entry in seccomp_mod.BLOCKED + seccomp_mod.CONDITIONAL:
            assert entry.why and len(entry.why) >= 15, (
                f"{entry.name} has no justification; an unjustified entry in a "
                f"blocklist is an unjustified way to break a skill")
            assert entry.number > 0


# ---------------------------------------------------------------------------
# The surface: the security sense reports the sandbox's state
# ---------------------------------------------------------------------------


class TestTheSecuritySurface:
    def test_the_sense_reports_the_filter_as_off_by_default(self, chronoa_config):
        from shani_chronoa.senses import security
        state = security.read_sandbox_seccomp()
        assert state["enabled"] is False
        assert state["denied"] > 0
        assert "off by default" in security._run({}).content

    def test_the_sense_reports_the_filter_as_on(self, gate):
        from shani_chronoa.senses import security
        state = security.read_sandbox_seccomp()
        assert state["enabled"] is True
        assert state["available"] is True
        text = security._run({}).content
        assert "sandbox seccomp filter: on" in text
        assert "denying" in text

    def test_the_sense_says_this_process_is_not_the_filtered_one(self, gate):
        """The distinction the surface exists to keep.

        The filter is installed in each command's process, so the assistant
        reads `Seccomp: 0` while every skill call is filtered. Reporting the
        two together without saying so would read as a contradiction.
        """
        from shani_chronoa.senses import security
        text = security._run({}).content
        assert "installed in the command's own process, not here" in text

    def test_an_unreadable_gate_is_reported_as_undetermined_not_off(self, monkeypatch):
        """The third state, which is the one this module's whole docstring is about.

        `enabled` is `False` for a machine that never turned the filter on and
        `None` for one where the setting could not be read. Rounding `None`
        down to `False` is the exact defect `security.py`'s module docstring is
        written against - "X is false" and "I could not determine X" have to be
        different states - so the third state is asserted here, in both the
        record and the rendered percept.
        """
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.senses import security

        def _boom(self, key, default=False):
            if key == SECCOMP_SETTING:
                raise RuntimeError("measured: the settings backend is unavailable")
            return default

        monkeypatch.setattr(ChronoaConfig, "get_bool", _boom)
        state = security.read_sandbox_seccomp()
        assert state["enabled"] is None, (
            f"an unreadable gate was reported as {state['enabled']!r}; the "
            f"security sense's entire reason to exist is not rounding that down")
        assert "could not be read" in state["unavailable_reason"]
        assert "undetermined rather than off" in security._run({}).content

    def test_the_sense_reports_an_unbuildable_filter_rather_than_claiming_on(
            self, gate, monkeypatch):
        from shani_chronoa.senses import security
        monkeypatch.setattr(seccomp_mod, "unavailable_reason",
                            lambda: "measured: no filter here")
        state = security.read_sandbox_seccomp()
        assert state["enabled"] is True
        assert state["available"] is False
        text = security._run({}).content
        assert "NO FILTER CAN BE BUILT" in text
        assert "refused rather than run unfiltered" in text
