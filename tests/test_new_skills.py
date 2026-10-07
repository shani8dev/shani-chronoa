"""Three new skills: timers, power profiles, and a calculator.

Each test here corresponds to a way the skill can report something true-sounding
and false, which is the failure this codebase cares about most:

- a **timer recorded as pending that will never fire** — the old implementation
  was a `threading.Timer` in the assistant's own process, so closing the window
  killed every pending timer silently and nothing could list or cancel one;
- a **power profile reported as set that the daemon did not apply** — the set is
  asynchronous, so reading back immediately can race it;
- a **calculator that is `eval`** — its arguments come from a language model,
  so anything that reaches the interpreter is arbitrary code execution.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from shani_chronoa.skills import calculate, power_profile, timer


# --- timers ---------------------------------------------------------------

@pytest.fixture
def state(tmp_path, monkeypatch):
    """An isolated timer store, with systemd faked as available."""
    store = tmp_path / "state" / "timers.json"
    monkeypatch.setattr(timer, "_data_path", lambda: store)
    monkeypatch.setattr(timer, "_systemd_available", lambda: True)
    scheduled = []
    monkeypatch.setattr(
        timer, "_schedule",
        lambda ident, secs, label="timer": (scheduled.append((ident, secs, label)), (True, ""))[1])
    unscheduled = []
    monkeypatch.setattr(timer, "_unschedule", lambda ident: unscheduled.append(ident))
    return store, scheduled, unscheduled


class TestTimersSurviveTheAssistant:
    def test_a_set_timer_is_written_to_disk_not_kept_in_process(self, state):
        """The old skill used `threading.Timer`, so a restart lost every pending
        timer with no error and no way to re-set one without duplicates."""
        store, scheduled, _ = state
        timer.set_timer(600, "pasta")
        assert store.exists(), "the timer was not persisted anywhere"
        assert len(timer._load()) == 1
        assert scheduled == [(timer._load()[0]["id"], 600, "pasta")]

    def test_a_timer_systemd_refused_is_not_recorded(self, tmp_path, monkeypatch):
        """`systemd-run` failing is the common case, not an exotic one - no
        user session, a unit name already taken, a transient-unit limit. The
        first version discarded its exit status, so this reported a timer set
        that would never fire and listed it as pending until it expired."""
        store = tmp_path / "timers.json"
        monkeypatch.setattr(timer, "_data_path", lambda: store)
        monkeypatch.setattr(timer, "_systemd_available", lambda: True)
        monkeypatch.setattr(
            timer, "_schedule", lambda *a: (False, "Unit name already exists."))
        result = timer.set_timer(600, "pasta")
        assert "NOT set" in result
        assert "Unit name already exists" in result
        assert not store.exists(), (
            "a timer systemd refused to schedule was still written to disk, so "
            "list would show a pending timer that will never fire"
        )

    def test_it_can_be_listed_with_the_time_left(self, state):
        timer.set_timer(600, "pasta")
        listing = timer.list_timers()
        assert "pasta" in listing
        assert "left" in listing
        assert "9m" in listing or "10m" in listing

    def test_it_can_be_cancelled(self, state):
        _, _, unscheduled = state
        timer.set_timer(600, "pasta")
        identifier = timer._load()[0]["id"]
        assert "Cancelled" in timer.cancel_timer(identifier)
        assert timer._load() == []
        assert unscheduled == [identifier], "systemd was not told to stop the timer"

    def test_no_timers_pending_is_a_normal_answer(self, state):
        assert timer.list_timers() == "No timers are pending."


class TestTimerHonesty:
    def test_an_unschedulable_timer_is_refused_not_recorded(self, tmp_path, monkeypatch):
        """A timer recorded as pending that will never fire is the same class of
        lie as a camera reported as disabled when it is not."""
        store = tmp_path / "timers.json"
        monkeypatch.setattr(timer, "_data_path", lambda: store)
        monkeypatch.setattr(timer, "_systemd_available", lambda: False)
        result = timer.set_timer(60, "x")
        assert "NOT set" in result
        assert not store.exists(), "an unschedulable timer was written to disk"

    def test_cancelling_twice_is_not_an_error(self, state):
        """A user tidying up after a timer went off should not be told they made
        a mistake - and must not be told something was cancelled when it was
        not."""
        timer.set_timer(60, "x")
        identifier = timer._load()[0]["id"]
        timer.cancel_timer(identifier)
        again = timer.cancel_timer(identifier)
        assert "Nothing was cancelled" in again

    def test_an_expired_timer_is_not_listed(self, state):
        store, _, _ = state
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text('[{"id": "abcd1234", "label": "old", "due": 1}]')
        assert timer.list_timers() == "No timers are pending."

    def test_a_corrupt_state_file_reads_as_empty_not_a_crash(self, tmp_path, monkeypatch):
        store = tmp_path / "timers.json"
        store.write_text("{not json")
        monkeypatch.setattr(timer, "_data_path", lambda: store)
        assert timer._load() == []
        assert timer.list_timers() == "No timers are pending."

    @pytest.mark.parametrize("bad", ["../../etc/passwd", "not-an-id", "'; rm -rf /", ""])
    def test_an_id_that_is_not_an_id_is_refused(self, state, bad):
        """The id goes into a `systemctl` argument, so it is matched against the
        hex shape the skill itself mints."""
        assert "not a timer id" in timer.cancel_timer(bad)

    def test_a_non_numeric_duration_is_refused(self, state):
        assert "Invalid timer duration" in timer._run({"seconds": "soon"})

    def test_a_negative_duration_is_refused(self, state):
        assert "positive" in timer.set_timer(-5, "x")

    def test_a_week_long_timer_is_refused(self, state):
        assert "reminder, not a timer" in timer.set_timer(86400 * 8, "x")

    def test_the_schema_advertises_all_three_actions(self):
        actions = timer.SCHEMA["function"]["parameters"]["properties"]["action"]
        assert set(actions["enum"]) == {"set", "list", "cancel"}


# --- power profiles -------------------------------------------------------

PPD_LIST = """Available profiles:
    performance:
      CpuDriver:	intel_pstate
      PlatformDriver:	platform_profile
      Degraded:   no

* balanced:
      CpuDriver:	intel_pstate
      PlatformDriver:	platform_profile

  power-saver:
      CpuDriver:	intel_pstate
"""


class TestPowerProfileParsing:
    def _wire(self, monkeypatch, *, stdout_list=PPD_LIST, active="balanced",
              set_rc=0, set_stderr=""):
        calls = []

        def fake(argv, **kwargs):
            calls.append(argv[1])
            class R:
                returncode = set_rc if argv[1] == "set" else 0
                stderr = set_stderr if argv[1] == "set" else ""
                stdout = active if argv[1] == "get" else stdout_list
            return R()
        monkeypatch.setattr(power_profile.shutil, "which",
                            lambda n: "/usr/bin/powerprofilesctl")
        monkeypatch.setattr(power_profile.subprocess, "run", fake)
        return calls

    def test_the_active_profile_is_in_the_list(self, monkeypatch):
        """`powerprofilesctl` prints the active one as `* balanced:` with the
        star in column 0, and the others indented. A pattern requiring leading
        whitespace drops exactly the profile a user is most likely to ask for -
        the one already in use."""
        self._wire(monkeypatch)
        assert power_profile.available() == ["performance", "balanced", "power-saver"]

    def test_driver_detail_lines_are_not_profiles(self, monkeypatch):
        self._wire(monkeypatch)
        assert "CpuDriver" not in power_profile.available()
        assert "PlatformDriver" not in power_profile.available()

    def test_a_profile_this_machine_does_not_have_is_refused(self, monkeypatch):
        self._wire(monkeypatch)
        result = power_profile._run({"profile": "turbo"})
        assert "is not a profile this machine offers" in result
        assert "performance" in result, "the valid names are not offered"

    def test_the_set_is_checked_afterwards(self, monkeypatch):
        """The daemon applies the change asynchronously, so a set that did not
        take must not be reported as set."""
        calls = self._wire(monkeypatch, active="balanced")
        result = power_profile._run({"profile": "power-saver"})
        assert "set" in calls
        assert "did not take" in result, (
            "the set was reported as done while the daemon still reported the "
            "previous profile"
        )

    def test_a_failing_set_is_reported_as_a_failure(self, monkeypatch):
        self._wire(monkeypatch, set_rc=1, set_stderr="not supported by firmware")
        result = power_profile._run({"profile": "power-saver"})
        assert "NOT changed" in result
        assert "not supported by firmware" in result

    def test_a_missing_daemon_is_not_a_machine_without_power_management(self, monkeypatch):
        monkeypatch.setattr(power_profile.shutil, "which", lambda n: None)
        result = power_profile._run({"profile": "balanced"})
        assert "power-profiles-daemon" in result
        assert "does not mean the machine has no power management" in result


# --- calculator -----------------------------------------------------------

class TestCalculatorArithmetic:
    @pytest.mark.parametrize("expression,expected", [
        ("(12+3)*4", "60"),
        ("2+2", "4"),
        ("-5+3", "-2"),
        ("1/3", "0.333333"),
        ("2^10", "1024"),
        ("sqrt(144)", "12"),
        ("round(3.7)", "4"),
        ("200*15%", "30"),
    ])
    def test_it_evaluates(self, expression, expected):
        assert calculate._run({"expression": expression}) == expected

    def test_decimal_arithmetic_not_floating_point_noise(self):
        """`0.1 + 0.2` is 0.30000000000000004 in binary floating point, and a
        currency sum that says that is worse than no answer."""
        assert calculate._run({"expression": "0.1+0.2"}) == "0.3"

    def test_division_by_zero_is_reported_not_raised(self):
        result = calculate._run({"expression": "10/0"})
        assert "divide by zero" in result
        assert "ZeroDivisionError" not in result
        assert "inf" not in result

    def test_a_bare_percentage_is_refused(self):
        """`30%` has no number to apply to, and guessing 0.3 or 30 would each be
        an answer to a question nobody asked."""
        assert "needs a number to apply to" in calculate._run({"expression": "30%"})


class TestCalculatorIsNotEval:
    """These arguments come from a language model, so reaching the interpreter
    is arbitrary code execution. The AST is walked against a whitelist."""

    @pytest.mark.parametrize("hostile", [
        '__import__("os").system("id")',
        "os.system('id')",
        "().__class__.__bases__[0].__subclasses__()",
        "[1,2][0]",
        "(lambda: 1)()",
        "1 if 1 else 2",
        "{1:2}",
        "open('/etc/passwd')",
    ])
    def test_nothing_reaches_the_interpreter(self, hostile):
        result = calculate._run({"expression": hostile})
        assert "Could not evaluate" in result or "not valid arithmetic" in result

    def test_a_name_that_is_not_a_constant_is_refused(self):
        out = calculate._run({"expression": "x+1"})
        assert "Only pi, e and tau are available by name" in out and "solve_math" in out

    def test_an_unknown_function_lists_the_known_ones(self):
        assert "sqrt" in calculate._run({"expression": "bogus(2)"})

    def test_keyword_arguments_to_a_whitelisted_function_are_refused(self):
        assert "no keyword arguments" in calculate._run({"expression": "round(x=1)"})

    def test_a_bare_string_constant_is_refused(self):
        assert "only numbers" in calculate._run({"expression": "'a'"})


class TestCalculatorUnits:
    @pytest.mark.parametrize("expression,fragment", [
        ("200km to miles", "124.274"),
        ("200 km in mi", "124.274"),
        ("70kg in pounds", "154.324"),
        ("100 celsius to f", "148"),
        ("1gb to kib", "976562"),
        ("1 hour to seconds", "3600"),
    ])
    def test_it_converts(self, expression, fragment):
        assert fragment in calculate._run({"expression": expression})

    def test_temperature_uses_offsets_not_a_factor(self):
        """Fahrenheit has an offset, so a multiplicative factor cannot express
        it - 100C is 212F, not 180F."""
        assert "212" in calculate._run({"expression": "212f to c"})

    def test_converting_between_different_quantities_is_an_error(self):
        """A number here would be a fabricated physical quantity."""
        result = calculate._run({"expression": "1kg to m"})
        assert "different things" in result
        assert "=" not in result

    def test_an_unknown_unit_is_named(self):
        assert "Unknown unit" in calculate._run({"expression": "5 parsecs to m"})

    def test_an_empty_expression_explains_itself(self):
        assert "Nothing to calculate" in calculate._run({})


class TestSystemdProbe:
    """Whether a user systemd instance is usable, which gates every timer.

    The return-code check here is the one that is easy to lose: a failing
    `systemctl` prints nothing, and `""` does not contain "offline", so a
    stdout-only check reports a broken systemd as available - and then every
    timer it "sets" fails at `systemd-run`, minutes later, with the user
    believing it is pending.
    """

    def _probe(self, monkeypatch, *, present=True, returncode=0, stdout="", stderr=""):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        monkeypatch.setattr(timer.shutil, "which",
                            lambda n: "/usr/bin/systemctl" if present else None)
        monkeypatch.setattr(timer.subprocess, "run", fake_run)
        return calls

    @pytest.mark.parametrize("state", ["running", "degraded"])
    def test_a_live_user_systemd_is_available(self, monkeypatch, state):
        # `degraded` means some unit failed; timers still work.
        self._probe(monkeypatch, stdout=state)
        assert timer._systemd_available() is True

    def test_an_offline_systemd_is_not_available(self, monkeypatch):
        self._probe(monkeypatch, stdout="offline")
        assert timer._systemd_available() is False

    def test_a_failing_probe_is_not_available(self, monkeypatch):
        """The silent one: a non-zero exit with no stdout at all."""
        self._probe(monkeypatch, returncode=1, stdout="",
                    stderr="Failed to connect to bus")
        assert timer._systemd_available() is False, (
            "a failed systemctl probe was treated as available, because its "
            "empty stdout does not contain the word 'offline'"
        )

    def test_a_missing_systemctl_is_not_available(self, monkeypatch):
        calls = self._probe(monkeypatch, present=False)
        assert timer._systemd_available() is False
        assert calls == [], "systemctl was invoked even though it is not installed"

    def test_a_probe_that_raises_is_not_available(self, monkeypatch):
        def boom(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 5)

        monkeypatch.setattr(timer.shutil, "which", lambda n: "/usr/bin/systemctl")
        monkeypatch.setattr(timer.subprocess, "run", boom)
        assert timer._systemd_available() is False


class TestTheTimerStoreCannotEscapeTheTestRun:
    """Guards the autouse `_isolate_timer_store` fixture in `conftest.py`.

    Every other timer test in this file patches the store through the `state`
    fixture, which is exactly why all of them were clean while a full suite run
    still wrote fixture data into a real user's state directory. These two
    deliberately do *not* patch it.

    The store used to be a module-level `_DATA`, resolved at **import** time
    from `$XDG_STATE_HOME`, so the per-test `HOME` isolation could not reach it
    and no fixture set `XDG_STATE_HOME` at all. It now resolves per call through
    `_data_path()`, and the fixture redirects that function. Observed leak that
    motivated all of it: a real
    `~/.local/state/shani-chronoa/timers.json` holding fixture data, including
    the `'; touch .../pwned; '` label from the shell-injection test in
    `test_skills.py`. Asserting against `Path.home()` here would be vacuous —
    `_hermetic_env` has already replaced `HOME` with a tmp dir by the time a
    test body runs — so this asserts the store is under pytest's own base temp
    directory, which is what actually distinguishes "redirected" from "not".
    """

    def test_the_store_points_into_pytests_temp_dir(self, tmp_path_factory):
        base = Path(str(tmp_path_factory.getbasetemp())).resolve()
        store = timer._data_path().resolve()
        assert store.is_relative_to(base), (
            f"the timer store is {store}, which is outside pytest's base temp "
            f"dir {base}; the _isolate_timer_store fixture is not being applied "
            f"and a test can write into the real ~/.local/state")

    def test_an_unpatched_caller_writes_only_to_the_isolated_store(
        self, monkeypatch
    ):
        monkeypatch.setattr(timer, "_systemd_available", lambda: True)
        monkeypatch.setattr(
            timer, "_schedule", lambda ident, secs, label="timer": (True, ""))
        monkeypatch.setattr(timer, "_unschedule", lambda ident: None)

        store = timer._data_path()
        assert not store.exists(), "the isolated store should start empty"
        timer.set_timer(600, "hermeticity-probe")
        assert store.exists(), (
            "set_timer did not write to the redirected store at all, so this "
            "test cannot tell a working fixture from a broken one")
        labels = [t.get("label") for t in json.loads(store.read_text())]
        assert labels == ["hermeticity-probe"]
