"""Planner: priorities and a bounded re-plan on step failure."""

from shani_chronoa import goals, planner


def _step(name, priority=0, depends_on=()):
    return goals.PlannedStep(
        name, "get_datetime", {}, name, depends_on=tuple(depends_on), priority=priority
    )


def test_priority_orders_runnable_steps_high_first():
    run = goals.new_run("g", [_step("low", 0), _step("high", 9), _step("mid", 3)])
    ordered = planner.priority_order(run)
    assert [s.variable_name for s in ordered.steps] == ["high", "mid", "low"]


def test_priority_never_moves_a_step_above_its_input():
    run = goals.new_run(
        "g", [_step("consumer", 9, ("producer",)), _step("producer", 0)]
    )
    ordered = planner.priority_order(run)
    assert [s.variable_name for s in ordered.steps] == ["producer", "consumer"]


def test_priority_survives_a_store_round_trip(tmp_path):
    run = goals.new_run("g", [_step("a", 4)])
    store = goals.GoalStore(tmp_path / "goals")
    store.save(run)
    back = store.load(run.id)
    assert back.steps[0].priority == 4


def _failed_run():
    run = goals.new_run("g", [_step("a"), _step("b", depends_on=("a",))])
    return run._replace(phase=goals.Phase.FAILED, index=0)


def test_a_failure_can_be_replaced_without_discarding_the_plan():
    run = _failed_run()
    new, why = planner.replan_failed(
        run, lambda step, results: [_step("retry-1"), _step("retry-2")], now=5
    )
    assert why == ""
    assert new.phase is goals.Phase.RUNNING
    assert [s.variable_name for s in new.steps] == ["retry-1", "a", "b"]
    assert new.steps[1].variable_name == "a"  # downstream still has a producer
    assert planner.replan_count(new) == 1


def test_an_empty_or_raising_replan_leaves_the_failure_alone():
    run = _failed_run()
    assert planner.replan_failed(run, lambda step, results: [])[1] != ""

    def broken(step, results):
        raise ValueError("no idea")

    assert "no idea" in planner.replan_failed(run, broken)[1]


def test_replans_are_bounded():
    run = _failed_run()._replace(results={planner._REPLAN_KEY: planner.MAX_REPLANS})
    out, why = planner.replan_failed(run, lambda step, results: [_step("x")])
    assert out is run and "already been re-planned" in why


def test_only_a_failed_run_is_replanned():
    run = goals.new_run("g", [_step("a")])
    out, why = planner.replan_failed(run, lambda step, results: [_step("x")])
    assert out is run and "only a failed run" in why
