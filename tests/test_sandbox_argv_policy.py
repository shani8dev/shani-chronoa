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

This file also exists to keep a *second* round of the same bypass closed. The first
round was fixed by resolving the program out of the argv; the resolver then read
only enough of `env` to get past an assignment, which is not the same as getting
past `env`. `env` has options, and they are not assignments:

    env VAR=x dd   -> _program='dd'    refused, 126
    env -i dd      -> _program='-i'    the real system dd RAN and wrote its output
    env -- dd      -> _program='--'    ditto
    env -u PATH dd -> _program='-u'    ditto
    env env dd     -> _program='env'   ditto

All four guards read `_program()`, so all four fell at once - including the
privilege-escalation guard, where `env -i sudo` and `env -i pkexec` ran the real
binary. Every case below is measured against the real `SandboxExecutor` at
`LEVEL_3_HOST_USER`, and the `dd` cases are asserted by the *marker file they
would have written*: a refusal that returns 126 while the binary still ran is the
"proof it ran, not proof the filter declined" failure, so the marker is the
assertion that matters.

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


#: `env` prefixes each of which was run against the real `env` on this machine
#: (GNU coreutils 9.4) and observed to exec the `dd` that follows it. `-0` is
#: deliberately absent: `env` refuses `--null` together with a command (exit
#: 125), so it would prove nothing.
_ENV_PREFIXES_THAT_RUN_DD = [
    pytest.param(["env", "-i"], id="ignore-environment"),
    pytest.param(["env", "--ignore-environment"], id="ignore-environment-long"),
    pytest.param(["env", "--"], id="terminator"),
    pytest.param(["env", "-u", "PATH"], id="unset-detached"),
    pytest.param(["env", "--unset=PATH"], id="unset-attached"),
    pytest.param(["env", "-C", "/tmp"], id="chdir-detached"),
    pytest.param(["env", "--chdir=/tmp"], id="chdir-attached"),
    pytest.param(["env", "-v"], id="debug-flag"),
    pytest.param(["env", "env"], id="repeated-env"),
    pytest.param(["env", "--", "env"], id="repeated-env-after-terminator"),
    pytest.param(["env", "-i", "--"], id="option-then-terminator"),
    pytest.param(["env", "-iu", "PATH"], id="clustered-short-options"),
    pytest.param(["env", "-i", "env"], id="flag-then-repeated-env"),
]


def _dd_writes(marker: str) -> "list[str]":
    """A real `dd` argv that creates `marker` - so running it is observable."""
    return ["if=/dev/zero", f"of={marker}", "bs=1", "count=1"]


class TestEnvOptionsCannotHideTheProgram:
    """`env`'s options are not assignments, and every guard reads `_program()`.

    The resolver's job is to name the program the kernel will exec. `env` execs
    whatever follows its own options, so a resolver that stops at the first token
    after `env` names the *option* - and `-i`, `--`, `-u` and `-C` match no
    blocklist, so guards 2 through 5 all wave the real binary through. Measured on
    this machine against the real executor at `LEVEL_3_HOST_USER`, before the fix:
    `env -i dd`, `env -- dd`, `env -u PATH dd` and `env env dd` each exited 0 and
    each wrote the file they were pointed at, while `env -i sudo` and
    `env -i pkexec` ran the real ones.

    The `dd` cases assert the marker file, not just the exit code. 126 with the
    marker present would be a guard that declined on paper and executed in fact,
    which is the one failure this file was written to make impossible.
    """

    @pytest.mark.parametrize("argv", [
        pytest.param(["env", "-S", "dd"], id="split-string-detached"),
        pytest.param(["env", "--split-string", "dd"], id="split-string-long"),
        pytest.param(["env", "-Sdd"], id="split-string-attached-short"),
        pytest.param(["env", "--split-string=dd"], id="split-string-attached-long"),
    ])
    def test_dd_hidden_inside_env_split_string_is_refused_and_never_runs(
        self, host_executor, tmp_path, argv
    ):
        # `env -S "dd of=..."` word-splits its own argument, so the program is
        # not a token in this argv. Read as a value-taking option it consumed
        # `dd` and returned "", which no blocklist holds - the real dd ran.
        marker = tmp_path / "dd-ran"
        code, out, _ = host_executor.execute([*argv, *_dd_writes(str(marker))], _level_3())
        assert code == EXIT_SECURITY_ERROR, f"{argv!r} ran the real dd: {out!r}"
        assert not marker.exists(), (
            f"{argv!r} exited {code} *and* wrote {marker}; a refusal that "
            "executes is worse than no refusal")

    @pytest.mark.parametrize("prefix", _ENV_PREFIXES_THAT_RUN_DD)
    def test_dd_behind_an_env_option_is_refused_and_never_runs(
        self, host_executor, tmp_path, prefix
    ):
        marker = tmp_path / "dd-ran"
        argv = [*prefix, "dd", *_dd_writes(str(marker))]
        code, out, _ = host_executor.execute(argv, _level_3())
        assert code == EXIT_SECURITY_ERROR, (
            f"{prefix!r} ran the real dd: {out!r}")
        assert not marker.exists(), (
            f"{prefix!r} exited {code} *and* wrote {marker}; a refusal that "
            "executes is worse than no refusal")

    @pytest.mark.parametrize("prefix", [
        pytest.param(["env", "-i"], id="ignore-environment"),
        pytest.param(["env", "--"], id="terminator"),
        pytest.param(["env", "-u", "PATH"], id="unset-detached"),
        pytest.param(["env", "env"], id="repeated-env"),
    ])
    def test_privilege_escalation_behind_an_env_option_is_refused(
        self, host_executor, prefix
    ):
        for elevated in (["sudo", "-n", "true"], ["pkexec", "--version"]):
            argv = [*prefix, *elevated]
            code, out, _ = host_executor.execute(argv, _level_3())
            assert code == EXIT_SECURITY_ERROR, (
                f"{argv!r} was not refused: {out!r}")
            assert "Privilege escalation" in out, (
                f"{argv!r} was refused by the wrong guard, which means the "
                f"escalation guard itself missed it: {out!r}")

    @pytest.mark.parametrize("prefix", [
        pytest.param(["env", "-i"], id="ignore-environment"),
        pytest.param(["env", "--"], id="terminator"),
        pytest.param(["env", "-u", "PATH"], id="unset-detached"),
        pytest.param(["env", "env"], id="repeated-env"),
    ])
    def test_an_internal_tool_behind_an_env_option_is_refused_in_isolated_levels(
        self, host_executor, prefix
    ):
        # Guard 3, which applies to the internal management binaries and only at
        # an isolated level - so it has to be checked separately or a fix that
        # only moves guards 2, 4 and 5 would look complete. `--version` rather
        # than a pid, because a `pkill` that *did* run has to be a probe and not
        # a signal: the whole point is to prove it ran, not to signal something.
        code, out, _ = host_executor.execute([*prefix, "pkill", "--version"], _level_1())
        assert code == EXIT_SECURITY_ERROR, out
        assert "pkill" in out, out

    def test_a_config_blocked_binary_behind_an_env_option_is_refused(self, host_executor):
        # Guard 4 reads `_program()` too, through the same `_blocked_binary`.
        code, out, _ = host_executor.execute(
            ["env", "-i", "frobnicate", "--now"],
            _level_3(blocked_binaries=["frobnicate"]),
        )
        assert code == EXIT_SECURITY_ERROR, out
        assert "frobnicate" in out, out

    @pytest.mark.parametrize("argv", [
        ["env", "-i", "echo", "clean"],
        ["env", "--ignore-environment", "echo", "clean"],
        ["env", "--", "echo", "terminated"],
        ["env", "-u", "HOME", "echo", "unset-ok"],
        ["env", "-C", "/tmp", "pwd"],
        ["env", "FOO=bar", "echo", "assigned"],
        ["env", "-i", "FOO=bar", "echo", "flag-then-assignment"],
        ["env", "env", "FOO=bar", "echo", "nested-assignment"],
    ])
    def test_a_legitimate_env_invocation_still_runs(self, host_executor, argv):
        # The guard is not allowed to become "refuse anything that mentions env".
        code, out, _ = host_executor.execute(argv, _level_3())
        assert code == 0, f"{argv!r} was refused: {out!r}"
        assert out.strip(), f"{argv!r} produced no output at all: {out!r}"

    def test_env_with_no_command_at_all_is_not_a_refusal(self, host_executor):
        # `env -i` prints its (empty) environment and exits 0 (measured). That is
        # a real, harmless program, and a resolver that reported it as
        # undeterminable would be refusing working code - the fail-closed answer
        # has to stop at *malformed*, not at *empty*.
        code, out, _ = host_executor.execute(["env", "-i"], _level_3())
        assert code == 0, out


class TestTheResolverNamesTheProgramInsideEnvExactly:
    """`_program()` is a property every guard rests on, so it is asserted directly.

    Every option spelling below is one that the `env --help` output on this
    machine (GNU coreutils 9.4) documents, and each was run against the real
    `env` to confirm the resolver's reading matches what actually executed - a
    resolver that guesses wrong *is* the bypass, not a cosmetic issue.
    """

    @pytest.mark.parametrize("argv,expected", [
        (["env", "FOO=bar", "dd"], "dd"),
        (["env", "A=1", "B=2", "dd"], "dd"),
        (["FOO=bar", "dd"], "dd"),
        # Flags, plus a mere `-` - env --help: "A mere - implies -i" - which is an
        # option and NOT a terminator.
        (["env", "-i", "dd"], "dd"),
        (["env", "--ignore-environment", "dd"], "dd"),
        (["env", "-0", "env", "dd"], "dd"),
        (["env", "-v", "dd"], "dd"),
        (["env", "--debug", "dd"], "dd"),
        (["env", "--list-signal-handling", "env", "dd"], "dd"),
        (["env", "-", "dd"], "dd"),
        (["env", "-i", "-", "dd"], "dd"),
        # Options taking an argument, detached and attached. `-u=PATH` is absent
        # on purpose: env passes `=PATH` through as the NAME (measured), so the
        # short form takes only the rest of the word.
        (["env", "-u", "PATH", "dd"], "dd"),
        (["env", "-uPATH", "dd"], "dd"),
        (["env", "--unset", "PATH", "dd"], "dd"),
        (["env", "--unset=PATH", "dd"], "dd"),
        (["env", "-C", "/tmp", "dd"], "dd"),
        (["env", "-C/tmp", "dd"], "dd"),
        (["env", "--chdir", "/tmp", "dd"], "dd"),
        (["env", "--chdir=/tmp", "dd"], "dd"),
        (["env", "-S", "dd", "dd"], None),
        (["env", "-Sdd", "dd"], None),
        (["env", "--split-string=dd", "dd"], None),
        (["env", "--split-string", "dd", "dd"], None),
        # Optional-argument long options take their value only after `=`.
        (["env", "--block-signal", "dd"], "dd"),
        (["env", "--block-signal=SIGTERM", "dd"], "dd"),
        (["env", "--default-signal=SIGINT", "dd"], "dd"),
        (["env", "--ignore-signal", "dd"], "dd"),
        # The terminator ends option parsing, so the next token is the program
        # even when it is shaped like one.
        (["env", "--", "dd"], "dd"),
        (["env", "--", "-i"], "-i"),
        (["env", "-i", "--", "dd"], "dd"),
        # A repeated `env` is transparent too, and so is one behind `--`.
        (["env", "env", "dd"], "dd"),
        (["env", "--", "env", "dd"], "dd"),
        (["env", "env", "env", "dd"], "dd"),
        (["env", "-i", "env", "dd"], "dd"),
        (["env", "env", "-i", "dd"], "dd"),
        (["env", "env", "FOO=bar", "dd"], "dd"),
        (["env", "--", "env", "-i", "dd"], "dd"),
        # Short options cluster, and the first letter needing a value takes the
        # rest of the word - so `-iu PATH` unsets PATH and `-ui PATH` unsets `i`.
        (["env", "-iu", "PATH", "dd"], "dd"),
        (["env", "-ui", "PATH", "dd"], "PATH"),
        # An assignment ends option parsing, which is the other measured `env`
        # fact and the one a naive "skip every option you see" walk gets wrong.
        (["env", "-i", "FOO=bar", "dd"], "dd"),
        (["env", "FOO=bar", "-i", "dd"], "-i"),
        (["env", "FOO=bar", "-i", "echo"], "-i"),
        # Nothing to name: `env` with no command prints its environment.
        (["env"], ""),
        (["env", "-i"], ""),
        (["env", "--"], ""),
        (["env", "FOO=bar"], ""),
        # A command is never an option cluster, however its letters fall. A
        # short-option walk that forgets to require a leading `-` reads `sudo`
        # as `-u do` and eats the token after it, naming neither the program nor
        # the escalation it is - which is the bypass, not a rounding error.
        (["env", "-i", "sudo"], "sudo"),
        (["env", "-i", "su"], "su"),
        (["env", "-i", "pkexec"], "pkexec"),
        (["env", "-i", "Sxx"], "Sxx"),
        (["env", "-i", "cut"], "cut"),
        (["env", "-i", "dd", "of=/tmp/x"], "dd"),
        (["env", "-i", "Curl", "https://x"], "Curl"),
        # A token that merely starts with `env` is a different program.
        (["envoy", "--version"], "envoy"),
        (["/usr/bin/envoy", "--version"], "/usr/bin/envoy"),
        (["/usr/bin/env", "dd"], "dd"),
    ])
    def test_the_program_is_resolved_past_every_env_spelling(self, argv, expected):
        assert _program(argv) == expected, f"{argv!r} resolved wrongly"

    @pytest.mark.parametrize("argv", [
        # An option that needs a value and has none. Resolving to `-u` would be
        # the two-ended-token bug: a bare `-u` is malformed, not a program.
        ["env", "-u"],
        ["env", "-C"],
        ["env", "-S"],
        ["env", "--unset"],
        ["env", "--chdir"],
        ["env", "--split-string"],
        ["env", "-u", "PATH", "-C"],
        ["env", "-i", "-u"],
        # A cluster that ends wanting a value: `-0` is a flag, `-u` is not.
        ["env", "-0u"],
        ["env", "-vu"],
        # An option this resolver does not know. `env` itself exits 125 here
        # (measured), but the point is that an *unreadable* option must not be
        # assumed harmless: a future `env` could give it an argument, and
        # over-skipping is the direction that walks straight past the program.
        # The two abbreviation entries are a deliberate, documented false
        # positive: GNU accepts any unambiguous long-option prefix, so
        # `env --un PATH dd` really does run dd and really is refused here.
        ["env", "-h", "dd"],
        ["env", "-Q", "dd"],
        ["env", "--nonsense", "dd"],
        ["env", "--ignore-e", "dd"],
        ["env", "--un", "PATH", "dd"],
        # A flag that was handed an argument it does not take.
        ["env", "--ignore-environment=true", "dd"],
        ["env", "--null=1", "dd"],
    ])
    def test_a_malformed_env_is_reported_as_undeterminable(self, argv):
        assert _program(argv) is None, (
            f"{argv!r} was resolved to {_program(argv)!r}; a malformed env "
            "invocation must not name a program")

    def test_undeterminable_is_distinct_from_nothing_to_run(self):
        # Both are refusals of *different* kinds and collapsing them would either
        # refuse working code or let an unreadable argv through.
        assert _program(["env", "-i"]) == ""
        assert _program(["env", "-i"]) is not None
        assert _program(["env", "-u"]) is None
        assert _program(["env", "-u"]) != ""


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
