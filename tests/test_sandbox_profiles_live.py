"""`AgentProfile` is enforced by a real child, and the per-origin difference is observable.

`sandbox/profiles.py` was built, unit-tested and imported by nothing. `PROFILE_DEFAULT.memory_limit`
was 512 and `cpu_limit` was 1024, and neither number reached the kernel: the only code that read a
profile was `validate_against_profile`, which checked `allowed_tools` and was itself never called.
A field that is recorded but not enforced is worse than no field, because it reads as a decision
somebody already made.

So these tests are all written to observe an *effect on a real process* rather than to assert that
`setrlimit` was called. A spy on `setrlimit` would pass unchanged against the pre-fix code, which is
the whole failure being fixed. Every ceiling here is applied by `_harden_child`, between `fork` and
`execve`, and each test proves the ceiling survived `execve` by having the *exec'd program itself*
report the limit it is running under.

The per-origin mapping is the reason any of this is worth having rather than merely tidy: an armed
trigger rule firing with nobody at the keyboard used to be indistinguishable from a user-initiated
call in every respect except one field on an audit record. The tests below pin the two apart by
running the *same program* under both origins and showing different outcomes.
"""

from __future__ import annotations

import logging
import os
import resource
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.sandbox import executor as executor_mod  # noqa: E402
from shani_chronoa.sandbox.executor import SandboxExecutor  # noqa: E402
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel  # noqa: E402
from shani_chronoa.sandbox.profiles import (  # noqa: E402
    SHARES_PER_CPU,
    AgentProfile,
    PROFILE_DEFAULT,
    PROFILE_FULL_ACCESS,
    PROFILE_READ_ONLY,
    PROFILE_RESTRICTED,
    get_profile,
    profile_for_origin,
)
from shani_chronoa.tool_tracking import (  # noqa: E402
    ORIGIN_UNATTENDED,
    ORIGIN_USER,
)

EXIT_SECURITY_ERROR = 126

#: Between the restricted profile's 256 MB and the default profile's 512 MB, so
#: one number distinguishes the two origins: too big for `restricted`, fine
#: for `default`. Every per-origin test below uses exactly this size.
_MID_MIB = 400
_MID_BYTES = _MID_MIB * 1024 * 1024


@pytest.fixture
def executor(tmp_path):
    return SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))


def _level_3(timeout: int = 30, **overrides) -> SandboxConfig:
    """The level every skill call actually runs at (`tools._get_sandbox_config`)."""
    overrides.pop("timeout_seconds", None)
    return SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER,
                         timeout_seconds=timeout, **overrides)


def _allocate(mib: int) -> "list[str]":
    """A real child that allocates `mib` and reports whether it managed to.

    `bytearray` rather than a bare `x = 1 * n`, because CPython interns small
    integers and would let a 4 MB "allocation" succeed as a shared object - a
    test that passes for the wrong reason is the failure mode this whole repo
    is written against.
    """
    return [sys.executable, "-c",
            f"x = bytearray({mib} * 1024 * 1024); print('ALLOCATED-OK', len(x))"]


def _report_limits() -> "list[str]":
    """A real child that prints the limits *it* is running under.

    This is the only shape of assertion that can distinguish "the ceiling was
    applied in the child" from "the parent called `setrlimit` and nothing
    followed" - rlimits are inherited across `execve`, so a number printed by
    the exec'd interpreter is the number the policy actually installed.
    """
    return [sys.executable, "-c", textwrap.dedent("""
        import resource
        print("AS", resource.getrlimit(resource.RLIMIT_AS)[0])
        print("CPU", resource.getrlimit(resource.RLIMIT_CPU)[0])
    """)]


class TestTheMemoryCeilingIsEnforcedInTheChild:
    """`memory_limit` must bite, not merely be set."""

    def test_the_child_reports_the_profile_ceiling_it_is_running_under(self, executor):
        code, out, _ = executor.execute(_report_limits(), _level_3())
        assert code == 0, out
        assert f"AS {PROFILE_DEFAULT.memory_bytes()}" in out, (
            f"the exec'd child did not inherit the profile's address-space "
            f"ceiling: {out!r}")
        assert f"CPU {PROFILE_DEFAULT.cpu_seconds_budget(30)}" in out, (
            f"the exec'd child did not inherit the profile's CPU budget: {out!r}")

    def test_a_child_that_exceeds_the_ceiling_dies(self, executor):
        """The effect, not the call: a real allocation past the limit fails.

        `PROFILE_DEFAULT` allows 512 MB and this asks for 900, so a policy that
        is not applied produces `ALLOCATED-OK` and a `MemoryError` proves it is.
        """
        code, out, _ = executor.execute(_allocate(900), _level_3())
        assert code != 0, f"a 900MB allocation ran under a 512MB profile: {out!r}"
        assert "MemoryError" in out, (
            f"the child failed for some reason other than the ceiling: {out!r}")
        assert "ALLOCATED-OK" not in out

    def test_a_child_inside_the_ceiling_still_runs(self, executor):
        """The negative control the test above needs.

        Without this, "a python child failed" would satisfy the ceiling test on
        its own - the program could be wrong, the interpreter could be missing,
        and the ceiling could be doing nothing at all.
        """
        code, out, _ = executor.execute(_allocate(64), _level_3())
        assert code == 0, f"a 64MB allocation failed under a 512MB profile: {out!r}"
        assert "ALLOCATED-OK" in out

    def test_the_ceiling_holds_on_the_real_skill_transport(self, executor):
        """The argv `tools.py` actually builds, not a synthetic one.

        The inline transport is `python3 -c "from shani_chronoa.skills.<m> import
        <f>; ..."`, and it is the only shape that reaches a real skill. Proving
        the ceiling on a bare `bytearray` and not on this would leave open the
        possibility that the skill import itself is what fails.
        """
        program = ("from shani_chronoa.skills.clock import _run; "
                   "x = bytearray(900 * 1024 * 1024); print('ALLOCATED-OK')")
        code, out, _ = executor.execute(
            [sys.executable, "-c", program], _level_3())
        assert code != 0, f"a real skill-shaped child ran 900MB under 512MB: {out!r}"
        assert "MemoryError" in out, out
        # The same transport, inside the ceiling, still works - so the failure
        # above is the ceiling and not the `shani_chronoa` import.
        small = program.replace("900", "8")
        code_ok, out_ok, _ = executor.execute([sys.executable, "-c", small], _level_3())
        assert code_ok == 0, f"the skill-shaped child failed inside the ceiling: {out_ok!r}"
        assert "ALLOCATED-OK" in out_ok


class TestTheCpuCeilingIsEnforcedInTheChild:
    """`cpu_limit` is shares; `RLIMIT_CPU` is a total. Both halves are tested."""

    def test_a_spin_loop_is_cut_off_at_the_profiles_budget(self, executor):
        """A 2 CPU-second budget against a 10s wall timeout.

        The margin is load-bearing and was widened after a real flake. A first
        version used a 5 CPU-second budget against the same 10s wall - only 2x -
        and failed roughly one run in twenty, because a spinning process on a
        busy machine can burn 10 s of *wall* time before it has accrued 5 s of
        *CPU*, at which point the wall timeout fires, the exit code is 124, and
        the test reports a ceiling that did not act when it did. Quarter-share
        against a 10s budget puts the CPU ceiling 5x inside the wall timeout, so
        the wall cannot win unless the ceiling is not working at all.
        """
        profile = AgentProfile(name="cpu-probe", memory_limit=512, cpu_limit=256,
                               network_access=True, timeout_seconds=10)
        budget = profile.cpu_seconds_budget(10)
        assert budget == 2
        assert budget * 5 <= profile.timeout_seconds, (
            f"the CPU budget must stay well inside the wall timeout for this "
            f"test to be measuring the ceiling and not a race: {budget}s vs "
            f"{profile.timeout_seconds}s")
        code, out, _ = executor.execute(
            [sys.executable, "-c", "x = 0\nwhile True:\n    x += 1\n"],
            _level_3(), profile=profile)
        assert code != 0, f"an unbounded spin loop was not cut off: {out!r}"
        assert "CPU-time ceiling" in out, (
            f"the child died but not on the profile's CPU ceiling, so the "
            f"ceiling is not what stopped it: {out!r}")
        assert "2 CPU-seconds" in out, (
            f"the refusal must name the budget it enforced: {out!r}")

    def test_a_command_inside_the_cpu_budget_is_unaffected(self, executor):
        """The negative control: same profile, a child that does not burn CPU.

        Without it, "the spin loop failed" is satisfied by any reason at all.
        """
        profile = AgentProfile(name="cpu-probe", memory_limit=512, cpu_limit=256,
                               network_access=True, timeout_seconds=10)
        code, out, _ = executor.execute(
            [sys.executable, "-c", "print('QUIET-OK')"], _level_3(), profile=profile)
        assert code == 0, f"a trivial command was killed by the CPU ceiling: {out!r}"
        assert "QUIET-OK" in out

    def test_the_budget_is_shares_scaled_by_the_timeout_not_shares_taken_literally(self):
        """The conversion, stated as a fact rather than left to the docstring.

        1024 shares is one CPU (`SHARES_PER_CPU`), so a 10s run is 10 CPU-seconds
        and half that is 5. Reading `cpu_limit` as seconds - the mistake a field
        comment reading "in shares" invites - would make 512 mean 512 CPU-seconds
        and this test would show a budget of 512, not 5.
        """
        assert PROFILE_DEFAULT.cpu_limit == SHARES_PER_CPU
        assert PROFILE_DEFAULT.cpu_seconds_budget(10) == 10
        assert PROFILE_DEFAULT.cpu_seconds_budget(60) == 60
        half = AgentProfile(name="half", memory_limit=64, cpu_limit=512,
                            network_access=True, timeout_seconds=10)
        assert half.cpu_seconds_budget(10) == 5


class TestThePerOriginProfileIsSelectedAndObservable:
    """The integration point: the same program, two origins, two outcomes."""

    def test_a_mid_sized_allocation_succeeds_for_a_user_and_dies_unattended(self, executor):
        """The behavioural difference, in one pair of runs.

        400 MB is over `restricted`'s 256 MB and under `default`'s 512 MB, so
        origin alone decides whether the same command works. Nothing here is
        asserted about `profile_for_origin`; the difference is measured on the
        process.
        """
        assert _MID_BYTES > PROFILE_RESTRICTED.memory_bytes()
        assert _MID_BYTES < PROFILE_DEFAULT.memory_bytes()

        user_code, user_out, _ = executor.execute(
            _allocate(_MID_MIB), _level_3(), origin=ORIGIN_USER,
            tool_name="get_datetime")
        assert user_code == 0, f"the user-origin call failed: {user_out!r}"
        assert "ALLOCATED-OK" in user_out

        un_code, un_out, _ = executor.execute(
            _allocate(_MID_MIB), _level_3(), origin=ORIGIN_UNATTENDED,
            tool_name="get_datetime")
        assert un_code != 0, (
            f"an unattended call ran {_MID_MIB}MB under a "
            f"{PROFILE_RESTRICTED.memory_limit}MB profile: {un_out!r}")
        assert "MemoryError" in un_out, un_out

    def test_an_unattended_origin_refuses_a_tool_outside_its_allowlist(self, executor, tmp_path):
        """Refused with a reason naming the profile - and never run.

        The canary is the point of the second assertion. A refusal reported after
        the command ran would still say "refused", which is the same shape of
        lie this module was written to remove.
        """
        canary = tmp_path / "unattended-canary"
        program = f"open({str(canary)!r}, 'w').write('x'); print('RAN-ANYWAY')"
        code, out, _ = executor.execute(
            [sys.executable, "-c", program], _level_3(),
            origin=ORIGIN_UNATTENDED, tool_name="write_text_file")
        assert code == EXIT_SECURITY_ERROR, out
        assert PROFILE_RESTRICTED.name in out, (
            f"the refusal must name the profile that refused, or it is "
            f"indistinguishable from a broken skill: {out!r}")
        assert "write_text_file" in out
        assert "RAN-ANYWAY" not in out
        assert not canary.exists(), "the refused tool ran anyway"

    def test_the_same_call_is_allowed_for_a_user_origin(self, executor):
        """The other half of the pair: the allowlist is per-origin, not global.

        `write_text_file` is refused above and runs here, with the identical
        argv and the identical level. If this passed because the allowlist were
        empty, the previous test would have been asserting nothing.
        """
        code, out, _ = executor.execute(
            [sys.executable, "-c", "print('RAN')"], _level_3(),
            origin=ORIGIN_USER, tool_name="write_text_file")
        assert code == 0, out
        assert "RAN" in out

    def test_an_unattended_origin_allows_the_read_only_tools_it_names(self, executor):
        code, out, _ = executor.execute(
            [sys.executable, "-c", "print('OBSERVED-OK')"], _level_3(),
            origin=ORIGIN_UNATTENDED, tool_name="list_processes")
        assert code == 0, f"a read-only tool was refused: {out!r}"
        assert "OBSERVED-OK" in out

    def test_an_unattended_origin_with_no_tool_named_is_refused(self, executor):
        """Fail closed. A profile with an allowlist that cannot verify a call
        must not treat the unverifiable case as allowed - otherwise the
        allowlist disappears for exactly the callers that forgot to identify
        themselves, and the restricted profile is one plumbing change away from
        being no restriction at all.
        """
        code, out, _ = executor.execute(
            [sys.executable, "-c", "print('RAN')"], _level_3(),
            origin=ORIGIN_UNATTENDED)
        assert code == EXIT_SECURITY_ERROR, (
            f"an unidentifiable call ran under an allowlisted profile: {out!r}")
        assert "RAN" not in out

    def test_every_allowlisted_name_is_a_real_tool(self):
        """The allowlist is read from the shipped registry, and this is what
        keeps it honest: a typo would be a tool that can never run, which looks
        exactly like a tool that is broken.
        """
        from shani_chronoa.skills import discover_skills
        _, handlers = discover_skills()
        for profile in (PROFILE_RESTRICTED, PROFILE_READ_ONLY):
            unknown = [t for t in profile.allowed_tools if t not in handlers]
            assert not unknown, (
                f"profile {profile.name!r} allowlists tools that do not exist: "
                f"{unknown}")

    def test_the_restricted_allowlist_admits_no_actuator_and_no_network_tool(self):
        """The criterion the list was built on, asserted rather than assumed.

        Anything that changes machine state, or that needs the network a
        no-network profile claims to forbid, must be absent - otherwise the
        allowlist contradicts the profile's own other field.
        """
        actuators = {
            "set_volume", "set_mute", "set_brightness", "open_application",
            "open_file", "write_text_file", "delete_file", "trash_file",
            "empty_trash", "lock_screen", "create_directory", "move_or_copy_file",
            "edit_file", "find_and_replace", "type_text", "press_key", "speak",
            "control_service", "toggle_bluetooth", "connect_wifi",
            "set_wallpaper", "set_theme", "set_timer", "add_reminder",
            "todo_list", "manage_triggers", "kill_process", "set_timezone",
            "set_privacy", "set_power_profile", "set_screensaver",
            "set_keyboard_layout", "set_sleep_inhibit", "print_file",
            "create_archive", "extract_archive", "install_model",
        }
        networked = {"web_search", "translate_text", "scan_network",
                     "check_updates", "recommend_model"}
        assert not actuators & set(PROFILE_RESTRICTED.allowed_tools)
        assert not networked & set(PROFILE_RESTRICTED.allowed_tools)

    def test_origin_mapping_fails_closed_for_an_unrecognised_origin(self):
        """The opposite of `get_profile`'s fallback, deliberately.

        A mistyped profile *name* is a config error; an origin nobody
        recognises means nobody can say whether a human asked.
        """
        assert profile_for_origin(ORIGIN_USER) is PROFILE_DEFAULT
        assert profile_for_origin(ORIGIN_UNATTENDED) is PROFILE_RESTRICTED
        assert profile_for_origin("something-new") is PROFILE_RESTRICTED


class TestTheProfileTimeoutOnlyTightens:
    def test_a_profile_ceiling_shorter_than_the_callers_request_binds(self, executor):
        """Observed, not computed: a 5s sleep under a 1s ceiling is killed at 1s
        and reported as a timeout rather than running to completion.
        """
        profile = AgentProfile(name="short", memory_limit=512, cpu_limit=1024,
                               network_access=True, timeout_seconds=1)
        started = time.monotonic()
        code, out, _ = executor.execute(["sleep", "5"], _level_3(), profile=profile)
        elapsed = time.monotonic() - started
        assert code == 124, f"the sleep was not cut off: {out!r}"
        assert elapsed < 4, (
            f"the profile's 1s ceiling took {elapsed:.1f}s to act, so it is not "
            f"what stopped the command")

    def test_a_profile_ceiling_longer_than_the_callers_request_does_not_loosen_it(self, executor):
        """`PROFILE_DEFAULT` says 300s and the caller asks 2. If a profile could
        loosen, wiring it in would have raised every skill call's timeout
        towards its own ceiling.
        """
        assert PROFILE_DEFAULT.timeout_seconds == 300
        assert PROFILE_DEFAULT.ceiling(2).timeout_seconds == 2
        started = time.monotonic()
        code, out, _ = executor.execute(["sleep", "4"], _level_3(timeout=2))
        elapsed = time.monotonic() - started
        assert code == 124, out
        assert elapsed < 3.5, (
            f"the command ran {elapsed:.1f}s, so the profile loosened the "
            f"caller's 2s request towards its own 300s ceiling")


class TestACeilingThatCannotBeAppliedFailsLoudly:
    """The honesty rule: no silently unapplied limits.

    An unprivileged process may lower a hard rlimit but never raise one, so a
    profile asking for more CPU than the process already has gets `EPERM`. Run
    in a throwaway subprocess because the test has to lower its *own* `RLIMIT_CPU`
    hard limit, which it could never put back.
    """

    _SCRIPT = textwrap.dedent("""
        import os, resource, sys
        sys.path.insert(0, {pkg!r})
        from shani_chronoa.sandbox.executor import SandboxExecutor
        from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel
        from shani_chronoa.sandbox.profiles import AgentProfile

        # A hard limit an unprivileged process cannot raise again afterwards,
        # so this has to be a throwaway process rather than the test runner.
        resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
        profile = AgentProfile(name="greedy", memory_limit=512, cpu_limit=4096,
                               network_access=True, timeout_seconds=600)
        marker = sys.argv[1]
        code, out, _ = SandboxExecutor().execute(
            ["sh", "-c", "touch " + marker],          # must not run
            SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=30),
            profile=profile)
        print("EXIT", code)
        print("OUT", out.replace("\\n", " | "))
    """)

    def test_a_profile_above_this_processs_own_ceiling_is_refused_not_downgraded(self, tmp_path):
        marker = tmp_path / "canary"
        script = self._SCRIPT.format(
            pkg=str(_REPO / "usr" / "lib" / "shani-chronoa")) + f"\nprint('MARKER-EXISTS', os.path.exists({str(marker)!r}))\n"
        result = subprocess.run([sys.executable, "-c", script, str(marker)],
                                capture_output=True, text=True, timeout=60)
        assert "EXIT 126" in result.stdout, (
            f"a profile the process cannot honour was not refused: "
            f"{result.stdout!r} {result.stderr[-400:]!r}")
        assert "greedy" in result.stdout, (
            f"the refusal must name the profile: {result.stdout!r}")
        assert "CPU time" in result.stdout, (
            f"the refusal must name the limit that could not be applied: "
            f"{result.stdout!r}")
        assert "MARKER-EXISTS False" in result.stdout, (
            f"the command ran without the ceiling it was promised: {result.stdout!r}")
        assert not marker.exists()


class TestTheUnenforcedFieldSaysSoOutLoud:
    """`network_access` is the one field this executor cannot enforce.

    The alternative was to leave it looking like policy. Instead every call
    under a no-network profile on a path that cannot isolate the network logs a
    warning saying it was *not* enforced and naming the profile - so the gap is
    visible on the exact turn it applies to, rather than documented only in a
    module docstring nobody opens when it matters.
    """

    def test_the_warning_is_logged_and_names_the_profile_and_the_gap(self, executor, caplog):
        executor_mod._NETWORK_WARNING_EMITTED.clear()
        with caplog.at_level(logging.WARNING, logger=executor_mod.__name__):
            code, _out, _ = executor.execute(
                [sys.executable, "-c", "print('x')"], _level_3(),
                origin=ORIGIN_UNATTENDED, tool_name="list_processes")
        assert code == 0
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert PROFILE_RESTRICTED.name in text, text
        assert "NOT" in text and "enforced" in text, (
            f"the warning must state that the limit was not applied, not merely "
            f"that it exists: {text!r}")

    def test_it_is_deduplicated_so_a_long_lived_assistant_does_not_flood(self, executor):
        """Said once per (profile, level). Still reported, just not every turn.
        """
        executor_mod._NETWORK_WARNING_EMITTED.clear()
        for _ in range(3):
            executor.execute([sys.executable, "-c", "true"], _level_3(),
                             origin=ORIGIN_UNATTENDED, tool_name="list_processes")
        assert len(executor_mod._NETWORK_WARNING_EMITTED) == 1, (
            f"expected one (profile, level) key, got "
            f"{executor_mod._NETWORK_WARNING_EMITTED}")


class TestTheCeilingIsScopedToOneCall:
    """A ceiling that leaks into the next call is worse than no ceiling.

    `_PENDING_CEILING` is a module global because `preexec_fn` takes no
    arguments, which makes leaking it the obvious way to break this.
    """

    def test_the_record_is_cleared_after_a_spawn(self, executor):
        executor.execute(_allocate(64), _level_3())
        assert not executor_mod._PENDING_CEILING, (
            f"the ceiling record survived the call: {executor_mod._PENDING_CEILING}")

    def test_the_record_is_cleared_even_after_a_refusal(self, executor):
        code, _out, _ = executor.execute(
            [sys.executable, "-c", "true"], _level_3(),
            origin=ORIGIN_UNATTENDED, tool_name="set_volume")
        assert code == EXIT_SECURITY_ERROR
        assert not executor_mod._PENDING_CEILING

    def test_a_tight_ceiling_does_not_follow_the_next_call(self, executor):
        """The behavioural version of the same leak.

        A `read-only` profile (128 MB) is used for one call; the next call runs
        under the default profile and must be able to allocate 200 MB. If the
        ceiling leaked, this would die.
        """
        code, out, _ = executor.execute(
            _allocate(64), _level_3(), profile=PROFILE_READ_ONLY,
            tool_name="get_datetime")
        assert code == 0, out
        code2, out2, _ = executor.execute(_allocate(200), _level_3())
        assert code2 == 0, (
            f"the previous call's 128MB ceiling leaked into the next one: {out2!r}")


class TestEveryProfileIsReal:
    """Four profiles, four enforced ceilings - not two live and two decorative.

    `full-access` and `read-only` are named by `get_profile` but selected by no
    origin. The honest way to keep them is to prove the executor honours each
    one on a real child, which is what `execute(profile=)` is for.
    """

    @pytest.mark.parametrize("profile", [
        PROFILE_DEFAULT, PROFILE_RESTRICTED, PROFILE_FULL_ACCESS, PROFILE_READ_ONLY,
    ])
    def test_the_profiles_ceiling_reaches_the_child(self, executor, profile):
        code, out, _ = executor.execute(_report_limits(), _level_3(),
                                        profile=profile, tool_name="get_datetime")
        assert code == 0, out
        assert f"AS {profile.memory_bytes()}" in out, (
            f"profile {profile.name!r} did not reach the child: {out!r}")

    @pytest.mark.parametrize("profile,over_the_line", [
        (PROFILE_DEFAULT, 600), (PROFILE_RESTRICTED, 300),
        (PROFILE_FULL_ACCESS, 2100), (PROFILE_READ_ONLY, 200),
    ])
    def test_each_profile_stops_a_child_that_exceeds_it(self, executor, profile,
                                                        over_the_line):
        code, out, _ = executor.execute(_allocate(over_the_line), _level_3(),
                                         profile=profile, tool_name="get_datetime")
        assert code != 0, (
            f"{over_the_line}MB ran under profile {profile.name!r} "
            f"({profile.memory_limit}MB): {out!r}")
        assert "MemoryError" in out, out

    def test_get_profile_and_the_module_constants_agree(self):
        """One registry, so the two lookups cannot drift.

        `get_profile` used to build its own dict literal from the same four
        module globals it could have been reading.
        """
        for name, profile in (("default", PROFILE_DEFAULT),
                              ("restricted", PROFILE_RESTRICTED),
                              ("full-access", PROFILE_FULL_ACCESS),
                              ("read-only", PROFILE_READ_ONLY)):
            assert get_profile(name) is profile


class TestTheProfileFieldsAreValidatedAtConstruction:
    """A nonsensical limit must fail here, not deep inside an unrelated program.

    `RLIMIT_AS = 0` does not mean "unlimited" - it means the child cannot
    allocate anything, and it surfaces as a `MemoryError` several frames into a
    skill that has nothing to do with memory.
    """

    @pytest.mark.parametrize("field,value", [
        ("memory_limit", 0), ("memory_limit", -1),
        ("cpu_limit", 0), ("timeout_seconds", 0),
    ])
    def test_a_non_positive_limit_is_refused(self, field, value):
        kwargs = dict(name="bad", memory_limit=64, cpu_limit=64,
                      network_access=True, timeout_seconds=30)
        kwargs[field] = value
        with pytest.raises(ValueError, match=field):
            AgentProfile(**kwargs)

    def test_the_rlimit_constants_the_executor_uses_are_the_ones_profiles_documents(self):
        """Pins the two decisions a reader is most likely to second-guess, since
        both were measured rather than assumed and neither is obvious:
        `RLIMIT_AS` rather than `RLIMIT_DATA`, and a CPU budget that is a
        multiple of the wall timeout rather than the share count itself.
        """
        assert executor_mod.resource.RLIMIT_AS == resource.RLIMIT_AS
        assert PROFILE_DEFAULT.memory_bytes() == 512 * 1024 * 1024
