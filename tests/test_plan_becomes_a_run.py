"""A plan is data, not a paragraph.

Plan mode told the model "describe the steps instead", and that prose was the
whole record. Nothing could be ticked off, resumed, or corrected, and approving
it meant asking again in a new turn and hoping the model re-derived the same
steps from its own text. Meanwhile `goals.py` already had `PlannedStep`,
`Phase.AWAITING` and `tool_executor` - and nothing connected them.

**Two properties this has to keep, and the second is the important one.**

The plan is built from the calls plan mode *actually refused*. A model may
describe steps in prose without calling anything, and that case stores nothing -
because a plan assembled from prose would be our guess at what it meant, and a
stored guess that later executes is the worst thing this app could hold.

**Approving a stored plan grants nothing.** Every step goes through
`goals.advance` -> `tool_executor` -> `tools.execute_tool`, which is where
consent and verification live. These tests check that path rather than trusting
it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import pytest  # noqa: E402

from shani_chronoa import goals, planmode, tools  # noqa: E402


@pytest.fixture
def state(tmp_path, monkeypatch):
    """An isolated store, and plan mode switched off afterwards whatever
    happens - a leaked plan mode would refuse the next test's tools."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    planmode.begin_turn()
    planmode.set_enabled(False)
    yield
    planmode.set_enabled(False)
    planmode.begin_turn()


def _runs():
    return goals.GoalStore(goals.store_root()).list()


# -- recording --------------------------------------------------------------


def test_a_refused_call_is_recorded_with_its_arguments(state):
    planmode.set_enabled(True)
    planmode.begin_turn()
    tools.execute_tool("write_text_file", {"path": "/tmp/a.txt", "text": "hi"})
    recorded = planmode.refusals()
    assert [t for t, _a in recorded] == ["write_text_file"], recorded
    assert recorded[0][1] == {"path": "/tmp/a.txt", "text": "hi"}


def test_the_arguments_are_copied_not_held(state):
    """The turn carries on after the refusal, and a later mutation of the
    caller's dict would rewrite the step that was recorded."""
    planmode.set_enabled(True)
    planmode.begin_turn()
    arguments = {"path": "/tmp/a.txt"}
    tools.execute_tool("write_text_file", dict(arguments))
    arguments["path"] = "/tmp/somewhere-else.txt"
    assert planmode.refusals()[0][1]["path"] == "/tmp/a.txt", (
        planmode.refusals()[0][1])


def test_a_new_turn_starts_an_empty_plan(state):
    planmode.set_enabled(True)
    planmode.begin_turn()
    tools.execute_tool("write_text_file", {"path": "/tmp/a.txt"})
    assert planmode.refusals()
    planmode.begin_turn()
    assert planmode.refusals() == [], (
        "the plan accumulated across turns - it is per-turn state on a module "
        "that outlives turns")


def test_repeated_tools_are_listed_once_each_in_order(state):
    planmode.set_enabled(True)
    planmode.begin_turn()
    tools.execute_tool("write_text_file", {"path": "/a"})
    tools.execute_tool("delete_file", {"path": "/b"})
    tools.execute_tool("write_text_file", {"path": "/c"})
    assert planmode.refused_tools() == ["write_text_file", "delete_file"], (
        planmode.refused_tools())


def test_nothing_is_recorded_when_plan_mode_is_off(state):
    planmode.set_enabled(False)
    planmode.begin_turn()
    tools.execute_tool("write_text_file", {"path": "/tmp/plan-off.txt",
                                          "text": "x"})
    assert planmode.refusals() == [], (
        "a refusal was recorded while plan mode was off")


# -- storing ----------------------------------------------------------------


def test_a_refused_plan_becomes_a_stored_run(state):
    run_id = goals.plan_from_refusals(
        "clean up my scratch files",
        [("write_text_file", {"path": "/a"}), ("delete_file", {"path": "/b"})])
    assert run_id, "nothing was stored"
    run = next(r for r in _runs() if r.id == run_id)
    assert run.goal == "clean up my scratch files"
    assert [s.tool_name for s in run.steps] == ["write_text_file", "delete_file"]
    assert run.steps[0].tool_arguments == {"path": "/a"}


def test_a_stored_plan_is_parked_and_not_under_way(state):
    """**The distinction that matters.** A run left `PENDING` and never driven
    looks exactly like one waiting for a person, and the panel would show a
    plan as though it were already running."""
    run_id = goals.plan_from_refusals("x", [("edit_file", {"path": "/a"})])
    run = next(r for r in _runs() if r.id == run_id)
    assert run.phase is goals.Phase.AWAITING, run.phase
    assert run.awaiting_reason == goals.PLAN_AWAITING_REASON


def test_a_plan_with_no_refused_calls_stores_nothing(state):
    """**The honesty case.** A model may describe its steps in prose without
    calling a tool. Inventing steps from that prose is our guess at what it
    meant, and a stored guess that later executes is the worst thing this app
    could hold."""
    assert goals.plan_from_refusals("x", []) == ""
    assert _runs() == []


def test_refusals_with_no_tool_name_store_nothing(state):
    assert goals.plan_from_refusals("x", [("", {}), ("", {})]) == ""
    assert _runs() == []


def test_arguments_that_are_not_a_dict_are_stored_empty(state):
    """A corrupt record must not stop the plan being stored - and must not be
    stored as a string that `fill()` would then walk into."""
    run_id = goals.plan_from_refusals("x", [("edit_file", "not-a-dict")])
    run = next(r for r in _runs() if r.id == run_id)
    assert run.steps[0].tool_arguments == {}


def test_a_plan_that_cannot_be_stored_returns_nothing(state, monkeypatch):
    """A plan the user does not get is better reported by the caller than
    raised into the turn - raising here would take the whole turn down."""

    class _Broken:
        def save(self, run, stamp=True):
            raise OSError("the disk is full")

    monkeypatch.setattr(goals, "GoalStore", lambda _root: _Broken())
    assert goals.plan_from_refusals("x", [("edit_file", {})]) == ""


def test_steps_are_named_so_one_step_can_read_another(state):
    """`depends_on` names variables, so a variable name is what makes a later
    step able to reference an earlier one's result."""
    run_id = goals.plan_from_refusals(
        "x", [("write_text_file", {"path": "/a"}),
              ("edit_file", {"path": "/b"})])
    run = next(r for r in _runs() if r.id == run_id)
    names = [s.variable_name for s in run.steps]
    assert names == ["step1", "step2"], names
    assert goals.fill({"path": "$step1"}, {"step1": "value"}) == {"path": "value"}


# -- approving a plan grants nothing ----------------------------------------


def test_advancing_a_stored_plan_goes_through_the_dispatcher(state, monkeypatch):
    """**The safety property, checked rather than assumed.**

    `plan_from_refusals` puts a list of tool calls on disk. If approval ran
    them by any path other than `tools.execute_tool`, every consent key and
    every verification in this app would be bypassed by approving a plan - and
    it would look like the ordinary way of doing things.
    """
    seen = []

    def spy(name, arguments, **kwargs):
        seen.append((name, arguments))
        return "done"

    monkeypatch.setattr(tools, "execute_tool", spy)
    executor = goals.tool_executor()
    step = goals.PlannedStep(thought="t", tool_name="write_text_file",
                             tool_arguments={"path": "/a"},
                             variable_name="step1")
    executor(step, {})
    assert seen == [("write_text_file", {"path": "/a"})], seen


def test_a_plan_step_is_refused_while_plan_mode_is_still_on(state, monkeypatch):
    """The obvious mistake: approving a plan while still in plan mode. Every
    step is refused, which is correct - and it is refused by the same check,
    not by anything to do with the plan."""
    # **The real dispatcher**, so this is the real refusal and not a stand-in.
    monkeypatch.setattr(tools, "execute_tool", tools.execute_tool)
    executor = goals.tool_executor()
    step = goals.PlannedStep(thought="t", tool_name="write_text_file",
                             tool_arguments={"path": "/a"},
                             variable_name="step1")
    planmode.set_enabled(True)
    assert "Plan mode" in executor(step, {}), executor(step, {})
