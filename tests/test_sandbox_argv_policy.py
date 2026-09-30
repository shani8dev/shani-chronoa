"""The sandbox's policy guards, now that they read an argv instead of a string.

Four guards used to run their checks over a *shell string* that was then handed to
`/bin/sh -c`, so every one of them could be walked past by writing a blocked name
in a form a shell would expand. Measured against the real `SandboxExecutor` at
`LEVEL_3_HOST_USER`:

    dd status=...          -> refused, 126
    $(echo dd) status=...  -> the real system dd RAN
    d\\d status=...         -> the real system dd RAN

and the privilege-escalation guard, which was worse because it was a plain
substring test, failed the same way: `$(echo sudo) reboot`, `` `echo pkexec` id ``,
`su${IFS}-c id` and `sud\\o reboot` all passed it.

`execute()` now takes an argv list and never builds a shell string, so a guard
reads the program the kernel will exec - there is no spelling of `dd` that reaches
argv[0] without being the thing that runs.

Two things this file exists to keep true, because both are easy to break:

- **The explicit `sh -c` opt-in must not be a hole wearing a feature's clothes.**
  argv[0] for `sh -c "mkfs.ext4 /dev/sda"` is just `sh`, so guard 5 alone would wave
  it through - and did, until the script's own words were put back under the same
  blocklists. That regression was caught here by running the executor, not by
  reading it.
- **Argument data must never be inspected.** The obvious repair for a string-based
  guard is to refuse any command containing `$`, a backtick or `\\`, and that
  false-positives on valid calls: `shlex.quote` wraps a program in single quotes
  rather than stripping those characters, so a skill argument carrying `$(...)`,
  a regex backslash or `$5` reaches the guard as literal text. Confirmed by
  building the real command and looking. A guard that refuses correct input is the
  same class of failure as one that permits wrong input.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.sandbox.executor import (  # noqa: E402
    DANGEROUS_BINARIES,
    SandboxExecutor,
    _blocked_binary,
    _program,
    _shell_script,
)
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel  # noqa: E402

EXIT_SECURITY_ERROR = 126


@pytest.fixture
def host_executor(tmp_path):
    """A real executor at LEVEL_3 - the default level for every skill call."""
    return SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))


def _level_3(**overrides) -> SandboxConfig:
    return SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=10, **overrides)


def _level_1(**overrides) -> SandboxConfig:
    return SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=10, **overrides)


class TestTheProgramIsReadThroughEnvAndAssignments:
    """argv[0] is not always the program.

    `env FOO=bar dd ...` runs `dd`, and `FOO=bar dd ...` does too. A guard reading
    argv[0] literally would see `env`, match nothing, and wave the real binary
    through - reintroducing exactly the bypass the migration removed.
    """

    def test_env_prefix_is_skipped(self):
        assert _program(["env", "FOO=bar", "dd", "status=x"]) == "dd"

    def test_leading_assignment_is_skipped(self):
        assert _program(["FOO=bar", "dd", "status=x"]) == "dd"

    def test_env_with_several_assignments_is_skipped(self):
        assert _program(["env", "A=1", "B=2", "dd"]) == "dd"

    def test_a_program_that_merely_starts_with_env_is_not_skipped(self):
        # `envoy` is a different program; only the bare launcher is transparent.
        assert _program(["envoy", "--version"]) == "envoy"

    def test_a_blocked_binary_behind_env_is_still_blocked(self, host_executor):
        code, out, _ = host_executor.execute(["env", "PATH=/usr/bin", "dd", "status=x"], _level_3())
        assert code == EXIT_SECURITY_ERROR, out
        assert "dd" in out

    def test_a_blocked_binary_behind_an_assignment_is_still_blocked(self, host_executor):
        code, out, _ = host_executor.execute(["LANG=C", "dd", "status=x"], _level_3())
        assert code == EXIT_SECURITY_ERROR, out


class TestACommandStringIsRefusedRatherThanShelled:
    """The old signature is the shape every guard misread, so it is not accepted."""

    def test_a_string_is_refused(self, host_executor):
        code, out, _ = host_executor.execute("echo hi", _level_3())
        assert code == EXIT_SECURITY_ERROR, out
        assert "argv" in out.lower(), (
            f"the refusal must say what to pass instead: {out!r}")

    def test_the_refusal_does_not_execute_the_command(self, host_executor):
        code, out, _ = host_executor.execute("echo SENTINEL-CANARY", _level_3())
        assert code == EXIT_SECURITY_ERROR
        assert "SENTINEL-CANARY" not in out, "a refused command must not run first"

    def test_an_empty_argv_is_refused(self, host_executor):
        code, out, _ = host_executor.execute([], _level_3())
        assert code == EXIT_SECURITY_ERROR, out


class TestTheExplicitShellOptInIsNotAHole:
    """`sh -c` stays reachable, because a blocklist never could police a script.

    What it must not be is a way around the blocklists - and it was, until the
    script's own words were put back under the same checks as a direct run.
    """

    @pytest.mark.parametrize("script", [
        "mkfs.ext4 /dev/sda",
        "/sbin/mkfs.btrfs /dev/sdb",
        "dd if=/dev/zero of=/dev/sda",
        "mount /dev/sda /mnt",
        "reboot",
    ])
    def test_a_blocked_binary_in_a_literal_script_is_refused(self, host_executor, script):
        code, out, _ = host_executor.execute(["sh", "-c", script], _level_3())
        assert code == EXIT_SECURITY_ERROR, (
            f"the shell opt-in ran a blocklisted program: {out!r}")

    def test_privilege_escalation_in_a_script_is_refused(self, host_executor):
        # Check #2 was a plain substring test and this is the case it missed.
        code, out, _ = host_executor.execute(["sh", "-c", "echo NOPE; sudo -n true"], _level_3())
        assert code == EXIT_SECURITY_ERROR, out
        assert "NOPE" not in out, "the script ran before the guard refused it"

    def test_pkexec_in_a_script_is_refused(self, host_executor):
        code, out, _ = host_executor.execute(["sh", "-c", "pkexec --version"], _level_3())
        assert code == EXIT_SECURITY_ERROR, out

    @pytest.mark.parametrize("script", [
        "$(echo dd) status=x",
        "`echo dd` status=x",
        "echo ${HOME}",
    ])
    def test_a_script_whose_program_cannot_be_read_is_refused(self, host_executor, script):
        """Fail closed rather than guess.

        An expansion construct is exactly how the old string guard was defeated:
        the blocked name appears nowhere in the text. Refusing is the honest
        answer, and it costs `sh -c "echo $(date)"` - which is documented rather
        than pretended around.
        """
        code, out, _ = host_executor.execute(["sh", "-c", script], _level_3())
        assert code == EXIT_SECURITY_ERROR, (
            f"a script whose program cannot be determined ran: {out!r}")

    def test_a_legitimate_script_still_runs(self, host_executor):
        """The opt-in has to be usable, or it is just a slower refusal."""
        code, out, _ = host_executor.execute(["sh", "-c", "echo hello-there"], _level_3())
        assert code == 0, out
        assert "hello-there" in out

    def test_a_script_naming_something_unblocked_still_runs(self, host_executor):
        # `ddrescue` and `add` are not `dd`; refusing these would be the false
        # positive this whole design is trying to avoid.
        code, out, _ = host_executor.execute(["sh", "-c", "echo ddrescue add mounting"], _level_3())
        assert code == 0, out

    def test_isolated_levels_still_refuse_internal_tools_in_a_script(self, host_executor):
        code, out, _ = host_executor.execute(
            ["sh", "-c", "shani-settings --anything"], _level_1())
        assert code == EXIT_SECURITY_ERROR, out

    def test_the_script_is_found_only_for_an_explicit_shell(self):
        assert _shell_script(["sh", "-c", "echo x"]) == "echo x"
        assert _shell_script(["bash", "-c", "echo x"]) == "echo x"
        assert _shell_script(["python3", "-c", "print(1)"]) is None, (
            "python3 -c is an argv element, not a shell script, and must not be "
            "treated as one - its program is checked as a python program")

    def test_argv_containing_c_is_not_mistaken_for_a_shell_script(self):
        assert _shell_script(["python3", "-c", "import os"]) is None


class TestArgumentDataIsNeverInspected:
    """The false-positive trap, measured rather than assumed.

    Every one of these is a legitimate program whose *argument* carries a shell
    metacharacter. None may be refused, and none is: the guards read argv[0] and
    stop. This is the case a substring-based repair would break.
    """

    @pytest.mark.parametrize("argument", [
        "$(rm -rf /tmp/x)",
        "`whoami`",
        r"^\d{4}-\d{2}$",
        "cost is $5",
        "${HOME} is not a secret",
        "a && b",
        "x | y",
    ])
    def test_a_python_program_carrying_metacharacters_is_not_refused(
        self, host_executor, argument
    ):
        argv = ["python3", "-c",
                "import sys; sys.stdout.write(sys.argv[1])", argument]
        code, out, _ = host_executor.execute(argv, _level_3())
        assert code == 0, (
            f"a python program was refused for its argument data {argument!r}: {out!r}")
        assert argument in out, "and the argument must arrive intact as data"

    def test_the_blocklist_helper_never_sees_arguments(self):
        assert _blocked_binary(["python3", "-c", "x", "dd"], DANGEROUS_BINARIES) is None, (
            "a blocked name in an argument position is data, not the program")


class TestNoSubstringMatching:
    @pytest.mark.parametrize("argv", [
        ["ddrescue", "/dev/sda"],
        ["echo", "add"],
        ["echo", "mounting"],
        ["get_datetime"],
        ["shutdownctl", "status"],
    ])
    def test_a_name_that_merely_contains_a_blocked_word_runs(self, argv):
        assert _blocked_binary(argv, DANGEROUS_BINARIES) is None

    @pytest.mark.parametrize("argv", [
        ["dd"],
        ["/sbin/dd", "if=x"],
        ["mkfs", "/dev/sda"],
        ["mkfs.ext4", "/dev/sda"],
        ["/usr/sbin/mkfs.btrfs"],
        ["mount"],
        ["umount", "/mnt"],
        ["shutdown", "-h", "now"],
        ["reboot"],
    ])
    def test_the_blocked_names_and_their_dotted_variants_are_caught(self, argv):
        assert _blocked_binary(argv, DANGEROUS_BINARIES) is not None
