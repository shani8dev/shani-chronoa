"""A sleep hold must be bounded, attributable, and impossible to smuggle through.

`set_sleep_inhibit` takes a lock that makes the machine ignore its own sleep schedule.
That is a real change to real hardware behaviour, and the failure modes are worth more
than the feature:

  - **An unbounded hold is a machine that will not sleep tonight.** Nothing in any setting
    would say why, and the next person to use it would file a bug. The bound is enforced
    by the lifetime of the inhibited command rather than by a timer in this process, so a
    crash, a kill, or a reboot releases it.
  - **The reason is model-supplied text going onto a command line.** A newline in it would
    let a caller append its own `--what=` or `--who=`, turning "hold sleep" into something
    else entirely. Measured here: `nightly backup\nFAKE --who=someone-else` reaches
    systemd-inhibit's list as one space-flattened argument, and the fake `--who` never
    takes effect.
  - **"Nothing is holding sleep" is false on a running desktop.** GNOME Shell,
    NetworkManager, UPower and ModemManager all take inhibitors routinely, so a status that
    claims an empty list is wrong on most machines, including this one.

None of these tests touch the machine. The state file is redirected into `tmp_path`, and
every subprocess is a stub, because a test suite that can actually stop a laptop sleeping
is a test suite that will eventually do it at the wrong moment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.capabilities import MUTATING_TOOLS, tool_annotations  # noqa: E402
from shani_chronoa.skills import set_sleep_inhibit as S  # noqa: E402


class _StubConfig:
    """A ChronoaConfig stand-in that answers from a dict."""

    def __init__(self, granted: bool = True):
        self._granted = granted

    def get_bool(self, key, default=False):
        return self._granted if key == S._CONSENT_KEY else default


class _FakeChild:
    """A Popen stand-in that records the argv it was given."""

    def __init__(self, argv, returncode=None):
        self.argv = list(argv)
        self.pid = 4242
        self._returncode = returncode
        self.stderr = None

    def poll(self):
        return self._returncode


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A skill wired to stubs, with the state file inside tmp_path."""
    spawned = []
    state = {
        "granted": True,
        "headless": False,
        "which": "/usr/bin/systemd-inhibit",
        "no_ask_password": False,
        "listings": [],
        "spawn_rc": None,
    }

    monkeypatch.setattr(S, "_STATE_FILE", tmp_path / "sleep-inhibit.json")
    monkeypatch.setattr(S, "ChronoaConfig", lambda: _StubConfig(state["granted"]))
    monkeypatch.setattr(S, "_headless", lambda: state["headless"])
    monkeypatch.setattr(S.shutil, "which", lambda name: state["which"])
    monkeypatch.setattr(
        S, "_supports_no_ask_password", lambda: state["no_ask_password"])
    monkeypatch.setattr(S.time, "sleep", lambda _s: None)
    # The fake child carries a made-up pid, so the real `os.getpgid` would raise
    # ProcessLookupError and every release test would read as "already lapsed" - which the
    # code is right to conclude, for the wrong reason.
    monkeypatch.setattr(S.os, "getpgid", lambda pid: pid)

    def _list():
        return True, state["listings"].pop(0) if state["listings"] else ""

    monkeypatch.setattr(S, "_list_inhibitors", _list)

    def _popen(args, **kwargs):
        child = _FakeChild(args, state["spawn_rc"])
        spawned.append(child)
        return child

    monkeypatch.setattr(S.subprocess, "Popen", _popen)
    return state, spawned


def _listing(*rows: str) -> str:
    header = ("WHO                          UID  USER             PID  COMM"
              "            WHAT")
    return "\n".join([header, *rows]) + "\n"


_OTHERS = _listing(
    "ModemManager                 0    root             1437 ModemManager    sleep",
    "NetworkManager               0    root             1362 NetworkManager  sleep",
)
_OURS = _listing(
    "ModemManager                 0    root             1437 ModemManager    sleep",
    "Shani Chronoa                1001 user              4242 systemd-inhibit sleep",
)


class TestTheReasonCannotSmuggleArguments:
    def test_a_newline_cannot_start_a_new_argument(self):
        assert "\n" not in S._sanitize_reason("backup\n--what=shutdown")
        assert "\n" not in S._sanitize_reason("a\rb")
        assert "\r" not in S._sanitize_reason("a\rb")

    def test_control_characters_and_del_are_removed(self):
        assert S._sanitize_reason("a\x07b\x00c") == "a b c"
        assert "\x7f" not in S._sanitize_reason("a\x7fb")

    def test_a_fake_who_cannot_displace_ours(self):
        # The exact reason used against the real binary, and the reason the module
        # docstring quotes. If a newline ever became a separator again, this breaks.
        hostile = "nightly backup\nFAKE --who=someone-else --what=shutdown\x07"
        cleaned = S._sanitize_reason(hostile)
        assert cleaned == "nightly backup FAKE --who=someone-else --what=shutdown"

    def test_an_overlong_reason_is_capped(self):
        assert len(S._sanitize_reason("x" * 5000)) <= S._MAX_REASON_LENGTH

    def test_an_empty_reason_is_replaced_not_passed_empty(self):
        # A reason-less lock in a list whose job is to explain itself is the failure
        # this avoids.
        assert S._sanitize_reason("") == S._DEFAULT_REASON
        assert S._sanitize_reason("   \x07  ") == S._DEFAULT_REASON

    def test_the_hostile_reason_stays_inside_the_why_argument(self, wired):
        # The point of the sanitiser, checked on the argv rather than on the helper: our
        # `--who` is a separate element, and the attacker's must not become one too. If a
        # newline ever separated arguments again, this is what would catch it.
        state, spawned = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60, "reason": "backup\n--who=someone-else"})
        argv = spawned[0].argv
        whos = [a for a in argv if a.startswith("--who=")]
        assert whos == [f"--who={S._WHO}"], f"attribution was altered or duplicated: {whos}"
        why = next(a for a in argv if a.startswith("--why="))
        assert why == "--why=backup --who=someone-else", \
            "the attacker's --who escaped into its own argument"

    def test_the_hold_reports_the_reason_systemd_actually_holds(self, wired):
        state, _ = wired
        state["listings"] = [_OURS]
        out = S._run({"action": "hold", "seconds": 60,
                      "reason": "backup\n--who=someone-else"})
        # The caller's text contained a newline; what was recorded must not.
        assert "Reason recorded: 'backup --who=someone-else'" in out
        assert "\\n" not in out.split("Reason recorded:")[1].splitlines()[0]


class TestTheHoldIsBounded:
    def test_a_seconds_value_below_the_minimum_is_refused(self, wired):
        state, spawned = wired
        out = S._run({"action": "hold", "seconds": S._MIN_SECONDS - 1})
        assert out.startswith("Refused")
        assert not spawned, "refused, yet something was started"

    def test_a_seconds_value_above_the_maximum_is_refused(self, wired):
        state, spawned = wired
        out = S._run({"action": "hold", "seconds": S._MAX_SECONDS + 1})
        assert out.startswith("Refused")
        assert not spawned

    def test_the_bound_is_exactly_the_maximum(self, wired):
        state, spawned = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": S._MAX_SECONDS})
        assert spawned and spawned[0].argv[-1] == str(S._MAX_SECONDS)

    def test_the_bound_is_enforced_by_the_inhibited_command_not_a_timer(self, wired):
        # This is the whole safety property. If the seconds went somewhere other than the
        # command `systemd-inhibit` runs, the lock would outlive any timer in this process
        # and survive a crash.
        state, spawned = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 120})
        argv = spawned[0].argv
        assert argv[-2:] == [S._INHIBITED_COMMAND, "120"], \
            f"the bound is not the lifetime of the inhibited command: {argv}"

    def test_it_holds_sleep_and_not_the_idle_timer(self, wired):
        state, spawned = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        argv = spawned[0].argv
        assert "--what=sleep" in argv
        assert "--what=idle" not in argv, \
            "blocking idle would hold the screen on for a build, which is not asked for"

    def test_it_blocks_rather_than_delays(self, wired):
        state, spawned = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        assert "--mode=block" in spawned[0].argv

    def test_no_ask_password_is_used_when_the_system_has_it(self, wired):
        state, spawned = wired
        state["no_ask_password"] = True
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        assert "--no-ask-password" in spawned[0].argv

    def test_its_absence_is_not_a_refusal(self, wired):
        # This systemd rejects the flag outright. Treating that as fatal - which is what
        # copying qwen-code's shape invites - makes the skill dead on the machine it was
        # written for, and a hold by a session user needs no privilege anyway.
        state, spawned = wired
        state["no_ask_password"] = False
        state["listings"] = [_OURS]
        out = S._run({"action": "hold", "seconds": 60})
        assert spawned, "refused to hold just because the flag is missing"
        assert "--no-ask-password" not in spawned[0].argv
        assert "no --no-ask-password" in out, "the missing guarantee was not disclosed"

    def test_the_hold_is_attributed_to_us(self, wired):
        state, spawned = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        assert f"--who={S._WHO}" in spawned[0].argv


class TestArgumentValidation:
    def test_a_bool_is_not_accepted_as_seconds(self, wired):
        # bool is a subclass of int, so an `isinstance(x, int)` check alone lets True
        # through as 1 second.
        state, spawned = wired
        out = S._run({"action": "hold", "seconds": True})
        assert "whole number" in out
        assert not spawned

    def test_a_string_is_not_accepted_as_seconds(self, wired):
        state, spawned = wired
        out = S._run({"action": "hold", "seconds": "600"})
        assert "whole number" in out
        assert not spawned

    def test_a_non_string_reason_is_refused(self, wired):
        state, spawned = wired
        out = S._run({"action": "hold", "seconds": 60, "reason": 42})
        assert "must be text" in out
        assert not spawned

    def test_an_unknown_action_is_reported_not_guessed(self, wired):
        out = S._run({"action": "nonsense"})
        assert "Unknown action" in out

    def test_the_default_action_is_status_and_needs_no_consent(self, wired):
        state, _ = wired
        state["granted"] = False
        state["listings"] = [_OTHERS]
        out = S._run({})
        assert "turned off" not in out, "reporting was gated behind a consent key"


class TestConsent:
    def test_holding_is_refused_without_consent(self, wired):
        state, spawned = wired
        state["granted"] = False
        out = S._run({"action": "hold", "seconds": 60})
        assert S._CONSENT_KEY in out
        assert not spawned

    def test_releasing_is_refused_without_consent(self, wired):
        # Releasing makes the machine *more* likely to sleep, so gating it is
        # conservative rather than obvious. The consent decision is about who may hold the
        # machine awake, and one function does all three actions.
        state, _ = wired
        state["granted"] = False
        assert S._CONSENT_KEY in S._run({"action": "release"})

    def test_the_key_is_its_own_not_the_idle_timeout(self):
        assert S._CONSENT_KEY != "idle-timeout-enabled"


class TestUnavailableRatherThanOff:
    def test_a_missing_systemd_inhibit_is_not_reported_as_a_hold(self, wired):
        state, spawned = wired
        state["which"] = None
        out = S._run({"action": "hold", "seconds": 60})
        assert "NOT held" in out
        assert not spawned

    def test_a_headless_session_does_not_pretend_to_hold(self, wired):
        state, spawned = wired
        state["headless"] = True
        out = S._run({"action": "hold", "seconds": 60})
        assert "Not held" in out
        assert not spawned

    def test_a_child_that_dies_immediately_is_reported_as_not_held(self, wired):
        state, _ = wired
        state["spawn_rc"] = 1
        out = S._run({"action": "hold", "seconds": 60})
        assert "NOT held" in out
        assert "unchanged" in out

    def test_an_unreadable_list_is_unknown_not_nothing(self, wired, monkeypatch):
        monkeypatch.setattr(
            S, "_list_inhibitors", lambda: (False, "exited 1: not a systemd system"))
        out = S._run({})
        assert "UNKNOWN" in out
        assert "not the same as nothing holding it" in out

    def test_a_hold_that_cannot_be_confirmed_says_so(self, wired, monkeypatch):
        # Reported from having started the process only. Claiming a hold we could not see
        # would be the same lie as a status that reported an empty list.
        state, _ = wired
        state["listings"] = [_OTHERS]  # started, but our row is not there
        out = S._run({"action": "hold", "seconds": 60})
        assert "may not have taken effect" in out


class TestStatusTellsTheTruthAboutTheMachine:
    def test_it_does_not_say_nothing_holds_sleep_when_something_does(self, wired):
        state, _ = wired
        state["listings"] = [_OTHERS]
        out = S._run({})
        assert "Nothing is holding" not in out
        assert "ModemManager" in out and "NetworkManager" in out

    def test_an_actually_empty_list_may_be_reported_as_empty(self, wired):
        state, _ = wired
        state["listings"] = [_listing()]
        out = S._run({})
        assert "not holding sleep" in out
        assert "nothing else in this user's list" in out, \
            "an empty list was not reported as empty"

    def test_the_header_row_is_not_reported_as_an_inhibitor(self, wired):
        state, _ = wired
        state["listings"] = [_OTHERS]
        out = S._run({})
        assert "Something else is:" in out
        body = out.split("Something else is:")[1]
        assert not body.splitlines()[1].strip().startswith("WHO"), \
            "the column header was listed as though it were an inhibitor"

    def test_our_own_hold_is_reported_separately_from_theirs(self, wired):
        state, _ = wired
        state["listings"] = [_OURS]
        out = S._run({})
        assert "IS holding sleep" in out
        assert "Something else is" not in out

    def test_our_rows_are_found_in_a_table_with_no_blank_lines(self, wired):
        # The real output is a fixed-width table, one line per inhibitor, no blank lines
        # between. Anything that splits on paragraphs finds nothing.
        state, _ = wired
        state["listings"] = [_OURS]
        assert S._our_rows(_OURS) == [
            "Shani Chronoa                1001 user              4242 systemd-inhibit sleep"]
        assert len(S._other_rows(_OURS)) == 1


class TestRelease:
    def test_releasing_with_nothing_recorded_signals_nothing(self, wired):
        state, _ = wired
        state["listings"] = [_OTHERS]
        out = S._run({"action": "release"})
        assert "Nothing of ours" in out

    def test_releasing_clears_the_recorded_state(self, wired, monkeypatch):
        state, _ = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        assert S._read_state() is not None
        state["listings"] = [_OURS, _OTHERS]  # then confirmed gone
        monkeypatch.setattr(S.os, "killpg", lambda pgid, sig: None)
        S._run({"action": "release"})
        assert S._read_state() is None, "the record outlived the hold"

    def test_a_hold_that_already_lapsed_is_reported_as_lapsed(self, wired, monkeypatch):
        state, _ = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        state["listings"] = [_OTHERS]

        def _gone(_pgid, _sig):
            raise ProcessLookupError

        monkeypatch.setattr(S.os, "killpg", _gone)
        out = S._run({"action": "release"})
        assert "already lapsed" in out
        assert S._read_state() is None

    def test_release_reports_failure_to_signal_rather_than_claiming_success(
            self, wired, monkeypatch):
        state, _ = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})

        def _denied(_pgid, _sig):
            raise PermissionError("not ours")

        monkeypatch.setattr(S.os, "killpg", _denied)
        out = S._run({"action": "release"})
        assert "Could not signal" in out
        assert "Released." not in out, "claimed a release that did not happen"

    def test_release_is_verified_by_reading_the_list_back(self, wired, monkeypatch):
        # Signalling a process is not releasing a lock. If the row is still there after,
        # saying "released" would be a lie in exactly the case that matters.
        state, _ = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        state["listings"] = [_OURS, _OURS]  # still listed after signalling
        monkeypatch.setattr(S.os, "killpg", lambda pgid, sig: None)
        out = S._run({"action": "release"})
        assert "still lists it" in out
        assert "Released." not in out

    def test_an_unreadable_list_after_signalling_is_not_reported_as_released(
            self, wired, monkeypatch):
        state, _ = wired
        state["listings"] = [_OURS]
        S._run({"action": "hold", "seconds": 60})
        monkeypatch.setattr(S.os, "killpg", lambda pgid, sig: None)
        monkeypatch.setattr(
            S, "_list_inhibitors", lambda: (False, "systemd-inhibit could not be run"))
        out = S._run({"action": "release"})
        assert "could not be read to confirm" in out
        assert "Released." not in out


class TestRegistration:
    def test_it_announces_that_it_is_not_read_only(self):
        # A hold changes machine behaviour, so `read_only_hint` must be False rather than
        # absent: a client is entitled to know before it calls. It comes from the consent
        # gate, not from MUTATING_TOOLS.
        description = S.SKILLS[0].schema["function"]["description"]
        assert tool_annotations(
            "set_sleep_inhibit", description)["read_only_hint"] is False

    def test_it_is_not_in_the_ungated_actuator_registry(self):
        # Recorded because this skill was the first tool to be both gated and listed there,
        # and the two annotation branches disagree about idempotency. A gated tool already
        # gets `read_only_hint: False` from the gate, so listing it as well is redundant
        # and contradictory. See the comment on MUTATING_TOOLS in capabilities.py.
        assert "set_sleep_inhibit" not in MUTATING_TOOLS

    def test_the_schema_is_well_formed_and_describes_its_actions(self):
        schema = S.SKILLS[0].schema
        assert schema["function"]["name"] == "set_sleep_inhibit"
        props = schema["function"]["parameters"]["properties"]
        assert props["action"]["enum"] == ["status", "hold", "release"]
        assert props["seconds"]["type"] == "integer"
        assert props["action"]["type"] == "string"
        assert schema["function"]["parameters"].get("required", []) == [], \
            "every argument is optional, so a bare call must be a valid status report"

    def test_the_description_points_at_the_right_tool_for_a_permanent_change(self):
        # Two tools that both answer "stop it sleeping" is a real ambiguity; the
        # description has to resolve it.
        description = S.SKILLS[0].schema["function"]["description"]
        assert "set_screensaver" in description
        assert S._CONSENT_KEY in description
