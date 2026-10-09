"""The `calendar_edit` skill: the gate, the confirmation, and refusing ambiguity.

**This is the skill that shipped dead.** It arrived with the Siri/Google parity
work (2026-10-08) gating on `calendar-write-enabled`, a key that was not in the
schema, so `get_bool` returned its Python default `False` for every value and
every call refused:

    Refusing: changing your calendar is turned off
    (enable 'calendar-write-enabled' in Settings). Nothing was changed.

The switch it named did not exist. Measured, not inferred - and the module
imported cleanly throughout, so only running it showed the refusal. Three of the
four skills in that batch had no test file at all; the fourth (`maps`) had one
that could not fail. So the first thing here is the gate, checked in both
directions, through the real dispatch path.

The rest is the part that would matter next, once the gate opened:

- **a move and a cancel ask, and silence is a no.** `approvals.confirm` is
  stubbed to answer, because a headless run has nobody to ask - and a test that
  assumed a "yes" would pass on a skill that had stopped asking entirely.
- **ambiguity is refused.** Two events at 5 PM on Friday are not resolved by
  picking one; deleting the wrong meeting is not something a user can undo from
  a transcript.
- **an hour is never guessed.** "3" alone could be 3 AM or 3 PM.
- **no calendar service is reported as itself**, not as a success and not as an
  empty calendar - the confident-wrong-answer shape this repository treats as
  worse than a refusal. This machine has no Evolution Data Server bindings, so
  that is the *live* answer here and it is asserted as the honest one.

Run: `python3 -m pytest tests/test_calendar_edit_skill.py`
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import calendar_edit as ce  # noqa: E402


class _Cfg:
    """A config stub that answers only the two keys this skill reads."""

    def __init__(self, write=False, read=False):
        self._write, self._read = write, read

    def get_bool(self, key, default=False):
        if key == ce._WRITE_KEY:
            return self._write
        if key == ce._READ_KEY:
            return self._read
        return default


@pytest.fixture
def granted(monkeypatch):
    """Both switches on, the calendar service stubbed, and no one to ask.

    The calendar stub raises: no real Evolution Data Server is involved, and
    every assertion below is about what the skill decides *before* it writes, so
    a write that somehow happened would surface as an error rather than as a
    quiet success on this machine.
    """
    state = {"asked": [], "answer": (True, ""), "writes": []}

    def fake_config():
        return _Cfg(write=True, read=True)

    def fake_confirm(title, body):
        state["asked"].append((title, body))
        return state["answer"]

    def refuse(*args, **kwargs):
        raise ce.cal.CalendarUnavailable("no Evolution Data Server bindings")

    def record_write(name):
        def fake_write(*args, **kwargs):
            state["writes"].append(name)
            raise ce.cal.CalendarUnavailable("no Evolution Data Server bindings")
        return fake_write

    monkeypatch.setattr(ce, "_config", fake_config, raising=True)
    monkeypatch.setattr(ce, "_confirm", fake_confirm, raising=True)
    # `eds_calendar` reaches the desktop through `_bindings()`, which is what
    # raises on a machine without Evolution Data Server. Patching that - rather
    # than a function name - is what makes this a statement about the skill's
    # own refusal instead of a statement about whichever binding the box has.
    monkeypatch.setattr(ce.cal, "_bindings", refuse, raising=True)
    # And every write is recorded before it fails. "It asked" is not the
    # property that matters - "it wrote nothing after being told no" is - and a
    # mutation that skips the confirmation while still calling `_confirm()`
    # left the first version of these tests fully green.
    monkeypatch.setattr(ce.cal, "create_event", record_write("create"), raising=True)
    monkeypatch.setattr(ce.cal, "move_event", record_write("move"), raising=True)
    monkeypatch.setattr(ce.cal, "remove_event", record_write("remove"), raising=True)
    return state


@pytest.fixture
def one_event(granted, monkeypatch):
    """`granted`, plus a calendar holding exactly one matching event.

    A move and a cancel find the event *before* they ask, so with no calendar
    the question is never reached and a test that asserted "it asked" would be
    asserting that the lookup worked. Giving the lookup one event is what puts
    the confirmation on the path.
    """
    when = "tomorrow 3pm"
    start, problem = ce._parse_due(when)
    assert not problem, f"the fixture's own time {when!r} did not parse: {problem}"
    start = float(start.timestamp())
    event = ce.cal.Event(
        start=start, end=start + 3600, summary="Meeting", location="",
        calendar="Personal", uid="evt-1", all_day=False,
    )
    monkeypatch.setattr(ce.cal, "events_between",
                        lambda lo, hi, timeout=10: [event], raising=True)
    monkeypatch.setattr(ce.cal, "describe", lambda e, now=None: "Meeting", raising=True)
    granted["event"] = event
    granted["when"] = when
    return granted


# --- the gate ----------------------------------------------------------------

def test_the_write_switch_is_off_by_default_and_nothing_is_written():
    """The original defect, in its original shape.

    `_config` answers `False` for both keys, which is what a default install
    looks like.
    """
    result = ce._run({"action": "create", "title": "Meeting", "start": "tomorrow 3pm"})
    assert "Refusing" in result, result
    assert ce._WRITE_KEY in result, (
        f"the refusal does not name the switch that would open it: {result}"
    )


def test_a_move_or_cancel_needs_the_read_switch_as_well_as_the_write_one(monkeypatch):
    """Finding the event to move or cancel *is* the reading half.

    A machine that allows writes but withholds reads must still refuse, or the
    write half would become a way to read somebody's calendar through the back
    of a mutation.
    """
    monkeypatch.setattr(ce, "_config", lambda: _Cfg(write=True, read=False), raising=True)
    for action in ("cancel", "move"):
        result = ce._run({"action": action, "title": "Meeting", "start": "tomorrow 3pm"})
        assert "Refusing" in result, f"{action} was allowed without read consent: {result}"
        assert ce._READ_KEY in result, (
            f"the {action} refusal does not name the missing read switch: {result}"
        )


def test_granting_the_switch_reaches_the_calendar_service(granted):
    """The other direction, and the one the dead skill could never pass.

    Past the gate the skill does real work, so on a machine with no calendar
    service the answer becomes that honest refusal - a *different* sentence from
    the consent refusal, which is what shows the gate actually opened.
    """
    result = ce._run({"action": "create", "title": "Meeting", "start": "tomorrow 3pm"})
    assert "Refusing: changing your calendar is turned off" not in result, (
        f"the gate is still closed after it was granted: {result}"
    )
    lowered = result.lower()
    assert "nothing was added" in lowered, (
        f"past the gate the skill did not say the event was not added: {result}"
    )
    assert "evolution data server" in lowered or "calendar service" in lowered, (
        f"the failure did not name the missing calendar service: {result}"
    )


def test_the_write_switch_is_in_the_schema_so_a_person_can_turn_it_on():
    """The half of the defect that made it permanent.

    A key nothing can set is not a permission; it is a permanent refusal with a
    helpful-sounding message.
    """
    import re
    from pathlib import Path

    schema = Path("usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml")
    keys = set(re.findall(r'<key\s+name="([^"]+)"', schema.read_text()))
    assert ce._WRITE_KEY in keys, (
        f"{ce._WRITE_KEY} is not in the schema, so no user action can grant it"
    )
    assert ce._READ_KEY in keys, f"{ce._READ_KEY} is not in the schema either"


# --- confirmation ------------------------------------------------------------

def test_a_move_asks_before_it_changes_anything(one_event):
    """A move is announced to the calendar and must be agreed to.

    Silence is a no: `approvals.confirm` returns False when nobody is there, and
    the stub here returns True, so an empty `asked` list means the skill stopped
    asking.
    """
    ce._run({"action": "move", "title": "Meeting",
              "start": one_event["when"], "new_start": "5pm"})
    assert one_event["asked"], "the move did not ask anybody"
    title, body = one_event["asked"][0]
    assert title.lower().startswith("move"), f"the question does not say what: {title!r}"
    assert body, "the question carries no detail of what is being changed"


def test_a_cancel_asks_before_it_deletes_anything(one_event):
    ce._run({"action": "cancel", "title": "Meeting", "start": one_event["when"]})
    assert one_event["asked"], "the cancel did not ask anybody"
    title, _ = one_event["asked"][0]
    assert "delete" in title.lower() or "cancel" in title.lower(), title


def test_a_refused_confirmation_is_reported_as_a_refusal_not_as_a_change(one_event):
    """The half that matters most.

    If a denied move reported the same sentence as a permitted one, the user
    would be told their calendar changed when it did not - which is the exact
    lie this module's docstring says it exists to avoid.
    """
    one_event["answer"] = (False, "the person said no")
    result = ce._run({"action": "cancel", "title": "Meeting", "start": one_event["when"]})

    # The write itself is the assertion. "It said no" is what the skill reports
    # about itself; "nothing was written" is what happened.
    assert one_event["writes"] == [], (
        f"the calendar was written after the confirmation was refused: "
        f"{one_event['writes']} (answer was: {result})"
    )
    lowered = result.lower()
    assert "not cancelled" in lowered or "not deleted" in lowered, (
        f"a refusal was not reported as one: {result}"
    )
    assert "still" in lowered, (
        f"the answer does not say the event is still there: {result}"
    )


def test_adding_needs_no_extra_question(granted):
    """Adding destroys nothing, so it must not make the user confirm.

    A confirmation on every add would train people to click through one, which
    is how a confirmation stops meaning anything.
    """
    ce._run({"action": "create", "title": "Meeting", "start": "tomorrow 3pm"})
    assert granted["asked"] == [], (
        f"adding asked for confirmation, which the module says it must not: {granted['asked']}"
    )


# --- shape and refusal -------------------------------------------------------

def test_an_unknown_action_lists_the_real_ones():
    result = ce._run({"action": "teleport", "title": "x"})
    assert "teleport" in result
    for action in ce._ACTIONS:
        assert action in result, f"the valid-action list omits {action!r}: {result}"


def test_arguments_that_are_not_a_mapping_are_refused():
    result = ce._run(["create", "Meeting"])
    assert "object of arguments" in result or "not" in result.lower(), result


def test_adding_without_a_title_asks_rather_than_creating_an_untitled_event(granted):
    result = ce._run({"action": "create", "start": "tomorrow 3pm"})
    lowered = result.lower()
    assert "title" in lowered or "called" in lowered, result
    assert "nothing was added" in lowered, (
        f"an untitled event may have been created: {result}"
    )


def test_adding_without_a_time_asks_rather_than_guessing(granted):
    result = ce._run({"action": "create", "title": "Meeting"})
    lowered = result.lower()
    assert "when" in lowered or "start" in lowered, result
    assert "nothing was added" in lowered, (
        f"an event with no time may have been created: {result}"
    )


def test_a_bare_hour_is_not_guessed_when_adding(granted):
    """"3" alone could be 3 AM or 3 PM.

    Adding refuses it. Moving is allowed to take the nearer of the two, because
    "move my 5 PM call to 6" means 6 PM - that asymmetry is deliberate and is
    checked here only for the half that must refuse.
    """
    result = ce._run({"action": "create", "title": "Meeting", "start": "3"})
    assert "nothing was added" in result.lower(), (
        f"a bare hour was turned into a specific time and the event created: {result}"
    )


# --- reachability ------------------------------------------------------------

def test_the_skill_is_reachable_through_the_real_dispatch_path():
    """Registered, discoverable and callable.

    With the write switch off by default this proves the gate is reachable
    through the registry and the sandboxed child; the granted direction is
    covered above in process, because a child cannot be handed a stub.
    """
    from shani_chronoa import tools

    outcome = tools.execute_tool_outcome(
        "calendar_edit", {"action": "create", "title": "Meeting", "start": "tomorrow 3pm"}
    )
    assert outcome.ran, "the calendar_edit skill did not run through the real path"
    assert isinstance(outcome.text, str) and outcome.text, outcome


def test_the_skill_is_never_treated_as_a_reader():
    """`calendar_events` is in `READ_ONLY_TOOLS`, which MCP uses as its
    read-only hint. `calendar_edit` must not be in it, or a client would treat a
    deletion as a read.

    It is *not* in `MUTATING_TOOLS` either, and that is deliberate rather than an
    omission: the module documents that a consent-gated tool does not belong in
    both sets, because `tool_annotations` has a separate gated branch which
    already answers `read_only_hint: False` and disagrees with the mutating
    branch about idempotency. So the claim is made through the gate - asserted
    here on the annotations themselves, which is what an MCP client receives.
    """
    from shani_chronoa import capabilities

    assert "calendar_edit" not in capabilities.READ_ONLY_TOOLS, (
        "calendar_edit is listed read-only, so an MCP client would treat a "
        "deletion as a read"
    )
    annotations = capabilities.tool_annotations("calendar_edit", "")
    assert annotations.get("read_only_hint") is False, (
        f"the annotations a client sees do not say this acts: {annotations}"
    )
    assert "calendar_edit" not in capabilities.MUTATING_TOOLS, (
        "calendar_edit is both gated and in MUTATING_TOOLS, which the module "
        "documents as the one tool to hit both annotation branches at once"
    )


def test_the_gated_and_mutating_sets_stay_disjoint():
    """The invariant, asserted as a property over the whole registry.

    `set_sleep_inhibit` was the tool that first hit it and the fix was to keep
    the sets apart; nothing stopped the next gated actuator from being added to
    both. This walks every registered tool rather than one name, so the check
    does not need editing each time a gated skill is added.
    """
    from shani_chronoa import capabilities
    from shani_chronoa.skills import discover_skills

    registered, _ = discover_skills()
    both = [
        t["function"]["name"]
        for t in registered
        if t["function"]["name"] in capabilities.MUTATING_TOOLS
        and capabilities.gated_by(t["function"]["name"], t["function"].get("description", ""))
    ]
    assert both == [], (
        f"these are consent-gated and in MUTATING_TOOLS at once, so "
        f"tool_annotations reaches both branches: {both}"
    )


def test_the_two_calendar_switches_are_separate_permissions():
    """One switch for both would mean a machine can allow being read and
    refuse being changed, which is the choice a person actually makes."""
    from shani_chronoa import capabilities

    assert capabilities.GATED["calendar_edit"] == ce._WRITE_KEY
    assert capabilities.GATED["calendar_events"] == ce._READ_KEY
    assert ce._WRITE_KEY != ce._READ_KEY