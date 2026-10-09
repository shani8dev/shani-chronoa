"""The `alarm` skill, against a fake systemd so nothing real is ever scheduled.

`alarm` arrived with the Siri/Google parity work (2026-10-08) with **no test
file**, and it is the most dangerous of that batch to ship untested: it
schedules real `systemd --user` transient timers. A test that called it for real
would leave units behind in the developer's session, and a test that stubbed
`subprocess.run` without checking the stub was in front of `systemctl` would do
the same while looking green.

So:

- **every** call here runs under `tests/fake_systemd.py`, which puts logging
  fakes first on `PATH` and **asserts that it got there** before any call - the
  guard that file exists for is asserted here too, so a future PATH mistake fails
  loudly instead of creating a real alarm;
- the store lives under a temp state directory, so the developer's real
  `alarms.json` is never read or written (AGENTS.md records a suite run having
  contaminated a real `timers.json` for exactly this reason);
- assertions are on **behaviour a person would be misled by**: an alarm reported
  as set that systemd never accepted, a delete that matches several alarms, a
  snooze of zero minutes, an unreadable time guessed at.

The property this module is most careful about is in its own docstring: *a
transient unit does not survive a reboot, so an alarm listed as set that will not
ring is the lie this module exists to avoid.* That is asserted directly - a lost
unit must be re-armed and said out loud, not quietly listed.

Run: `python3 -m pytest tests/test_alarm_skill.py`
"""

from __future__ import annotations

import json
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
sys.path.insert(0, "tests")

import fake_systemd  # noqa: E402
from shani_chronoa.skills import alarm as alarm_mod  # noqa: E402


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """The fake systemd, plus an isolated store.

    `XDG_STATE_HOME` is the variable that matters: the alarm store resolves from
    it, and AGENTS.md records that a full suite run without it wrote fixture data
    into the real `~/.local/state/shani-chronoa/timers.json`.
    """
    for name, sub in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"),
                      ("XDG_CONFIG_HOME", "config")):
        monkeypatch.setenv(name, str(tmp_path / sub))
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return fake_systemd.install(tmp_path, monkeypatch)


def _store() -> list:
    """Read the alarm store the way the module does, not a private constant.

    An **absent** store reads as `[]`, not as a `FileNotFoundError`. The store
    only exists once something has been written, so `_store() == []` has to mean
    "nothing is set" for the "nothing was stored" assertions to mean anything -
    and the first version of this file raised instead, so a refusal assertion
    failed with a path error that looked nothing like the bug it was checking.
    """
    path = alarm_mod._data_path()
    if not path.exists():
        return []
    return json.loads(path.read_text())


def test_the_fake_systemd_is_actually_in_front(fake):
    """The control for every other test here.

    If PATH ordering ever let the real binaries through, every other assertion
    would still pass while a real timer was created in the developer's session.
    `fake_systemd.install` asserts this itself; asserting it here as well means
    the guard is part of what these tests verify rather than a side effect.
    """
    import shutil

    for name in ("systemd-run", "systemctl"):
        resolved = shutil.which(name)
        assert resolved is not None and "fake-systemd-bin" in resolved, (
            f"{name} resolves to {resolved!r}, so a real call would be made"
        )


def test_the_store_is_the_temporary_one_not_the_real_one(fake, tmp_path):
    """Conftest-level hygiene, asserted because a leak here is invisible."""
    path = alarm_mod._data_path()
    assert str(tmp_path) in str(path), (
        f"the alarm store is {path}, which is outside this test's tmp_path - "
        "this test would write the developer's real alarms"
    )


def test_setting_an_alarm_says_when_it_will_ring_and_arms_a_timer(fake):
    result = alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    assert "7" in result, result
    stored = _store()
    assert len(stored) == 1, stored
    assert stored[0]["label"] == "work"
    assert stored[0]["unit"], "the alarm was stored with no systemd unit"
    # And the timer was really scheduled - a stored alarm with no transient
    # unit is exactly the "listed as set but will not ring" case.
    #
    # The calendar is passed as `--on-calendar=...`, systemd's long-option form.
    # The first version of this asserted the string `OnCalendar`, which is the
    # property name as it appears in a unit file, and it failed against a timer
    # that was scheduled perfectly correctly.
    runs = fake.runs()
    assert runs, "no systemd-run call was made, so nothing will ring"
    assert any(
        any(arg.startswith("--on-calendar=") for arg in argv) for argv in runs
    ), f"the transient timer carries no --on-calendar: {runs}"


def test_listing_an_alarm_names_it(fake):
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    listed = alarm_mod._run({"action": "list"})
    assert "work" in listed, listed
    assert "7" in listed, listed


def test_setting_with_no_time_asks_for_it_rather_than_guessing(fake):
    """A guessed alarm is worse than no alarm: it rings at the wrong time."""
    result = alarm_mod._run({"action": "set"})
    assert "No time given" in result, result
    assert _store() == [], "an alarm was stored with no time at all"


def test_an_unreadable_time_is_refused_not_approximated(fake):
    result = alarm_mod._run({"action": "set", "time": "half past tea"})
    assert _store() == [], f"an alarm was stored from an unreadable time: {_store()}"
    assert result.strip(), "the refusal says nothing at all"


def test_deleting_by_id_removes_exactly_that_alarm(fake):
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    alarm_mod._run({"action": "set", "time": "6am", "label": "run"})
    stored = _store()
    target = [a for a in stored if a["label"] == "work"][0]["id"]
    result = alarm_mod._run({"action": "delete", "id": target})
    remaining = _store()
    assert [a["label"] for a in remaining] == ["run"], remaining
    assert target in result, result


def test_a_delete_that_matches_several_alarms_refuses_and_lists_them(fake):
    """Ambiguity is refused, not resolved by picking one.

    Deleting the wrong alarm is unrecoverable from the user's point of view -
    they asked to remove one and cannot tell which went.
    """
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    alarm_mod._run({"action": "set", "time": "7:30", "label": "work"})
    assert len(_store()) == 2
    result = alarm_mod._run({"action": "delete", "label": "work"})
    assert len(_store()) == 2, (
        f"an ambiguous delete removed one anyway: {_store()}"
    )
    lowered = result.lower()
    # The module's own wording is "N alarms match label 'X'; nothing was
    # deleted. Say which by id:" - so the assertion names that shape rather than
    # accepting any plausible-sounding phrase, which is how a test ends up
    # passing against a sentence that means something else.
    assert "nothing was deleted" in lowered, (
        f"an ambiguous delete did not say that nothing happened: {result}"
    )
    assert "match" in lowered, f"the refusal does not say it matched several: {result}"
    for stored_alarm in _store():
        assert stored_alarm["id"] in result, (
            f"the refusal does not offer {stored_alarm['id']} as a choice, so the "
            f"user cannot act on it: {result}"
        )


def test_deleting_something_that_is_not_there_says_so(fake):
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    result = alarm_mod._run({"action": "delete", "label": "gym"})
    assert "gym" in result or "no alarm" in result.lower(), result


def test_snooze_of_zero_minutes_is_refused(fake):
    """Zero minutes is a snooze that fires immediately, which is not a snooze."""
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    before = len(_store())
    result = alarm_mod._run({"action": "snooze", "minutes": 0})
    assert "Invalid snooze length" in result, result
    assert len(_store()) == before, "a zero-minute snooze changed the store"


def test_a_non_numeric_snooze_is_refused_with_the_shape_it_wants(fake):
    result = alarm_mod._run({"action": "snooze", "minutes": "ten"})
    assert "Invalid snooze length" in result, result
    assert "minutes" in result, (
        f"the refusal does not say what a valid snooze looks like: {result}"
    )


def test_an_unknown_action_lists_the_real_ones(fake):
    result = alarm_mod._run({"action": "explode"})
    assert "explode" in result
    for action in alarm_mod._ACTIONS:
        assert action in result, (
            f"the valid-action list omits {action!r}: {result}"
        )


def test_arguments_that_are_not_a_mapping_are_refused(fake):
    result = alarm_mod._run(["set", "7am"])
    assert "not an object" in result or "nothing was done" in result.lower(), result


def test_a_repeating_alarm_says_when_it_next_rings_and_what_days(fake):
    """'Weekdays' has to survive into the summary, or the user cannot tell
    which days it will ring."""
    alarm_mod._run({"action": "set", "time": "6am", "repeat": "weekdays"})
    stored = _store()
    assert stored and stored[0].get("days"), stored
    listed = alarm_mod._run({"action": "list"})
    assert "weekday" in listed.lower(), listed


def test_an_unreadable_repeat_is_refused(fake):
    result = alarm_mod._run({"action": "set", "time": "7am", "repeat": "sometimes"})
    assert _store() == [], f"an alarm was stored with an unreadable repeat: {_store()}"
    assert result.strip()


def test_a_lost_unit_is_re_armed_and_said_out_loud(fake, monkeypatch):
    """The module's own stated purpose.

    A transient unit does not survive a reboot. So a machine that rebooted holds
    alarms in its list with no unit behind them, and listing them as set would be
    the lie. `FAKE_INACTIVE` makes the fake report the unit as gone, and the
    answer has to both re-arm and tell the person.
    """
    monkeypatch.setenv("FAKE_INACTIVE", "timer")
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    # A second listing now finds the unit inactive.
    listed = alarm_mod._run({"action": "list"})
    lowered = listed.lower()
    assert any(
        phrase in lowered
        for phrase in ("re-armed", "rearmed", "re-armed", "lost", "again", "not ring")
    ), (
        "a lost transient unit was listed without saying so, which is the "
        f"alarm-that-will-not-ring lie this module exists to avoid: {listed}"
    )
    runs = fake.runs()
    assert len(runs) >= 2, (
        f"the lost unit was not re-armed (only {len(runs)} systemd-run call(s)): {runs}"
    )


def test_a_post_condition_that_cannot_be_checked_says_none(fake):
    """`list` has nothing to verify, and must report that rather than pass.

    A post-condition returning a manufactured pass is the shape this repository
    treats as worse than an honest "not measured".
    """
    assert alarm_mod.POST_CONDITION({"action": "list"}) is None
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    stored = _store()
    ok = alarm_mod.POST_CONDITION(
        {"action": "set", "time": "7am", "label": stored[0]["label"]}
    )
    assert ok is not None and ok[0] is True, (
        f"a real set should verify against the store, and did not: {ok}"
    )


def test_the_post_condition_fails_when_the_store_does_not_hold_the_alarm(fake, monkeypatch):
    """The other direction, so the assertion above is not passing for free.

    Without this, a post-condition that always returned `(True, ...)` would pass
    `test_a_post_condition_that_cannot_be_checked_says_none`'s sibling too -
    the same "a check that cannot fail is not a check" trap AGENTS.md records
    several times.
    """
    alarm_mod._run({"action": "set", "time": "7am", "label": "work"})
    verdict = alarm_mod.POST_CONDITION(
        {"action": "set", "time": "7am", "label": "a label nobody asked for"}
    )
    assert verdict is not None and verdict[0] is False, (
        f"a label that was never stored verified as correct: {verdict}"
    )


def test_the_skill_is_reachable_through_the_real_dispatch_path(fake):
    """Registered and dispatchable - the property the parity batch lacked.

    `list` is used because it needs no scheduling: this proves the tool answers
    through the registry and the sandboxed child, not that alarms work (the tests
    above cover that in process).
    """
    from shani_chronoa import tools

    outcome = tools.execute_tool_outcome("alarm", {"action": "list"})
    assert outcome.ran, "the alarm skill did not run through the real path"
    assert isinstance(outcome.text, str) and outcome.text, outcome


def test_the_schema_documents_that_the_unit_does_not_survive_a_reboot():
    """The ceiling is stated in the schema the model reads, not only in the
    module docstring.

    A model told 'set an alarm for 7am' will tell a person it will ring, so the
    limit has to be visible where the claim is made.
    """
    description = alarm_mod.SCHEMA["function"]["description"].lower()
    assert "reboot" in description or "logout" in description, (
        "the schema does not warn that a transient unit is lost on reboot: "
        + alarm_mod.SCHEMA["function"]["description"][:200]
    )
    assert "7am" in description or "7" in description, (
        "the schema has no example, so the model has nothing to imitate"
    )