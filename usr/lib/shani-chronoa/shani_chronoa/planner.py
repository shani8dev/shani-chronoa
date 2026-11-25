"""Priorities and re-planning on step failure for saved goals.

`goals.py` holds the plan, its phases and the `advance()` stepper. What it did
not have is two decisions: which of several runnable steps goes first, and what
happens when a step fails. A priority is a field on `PlannedStep`; a failure is
a chance to replace one step rather than to discard the whole run.

Everything here returns a new `GoalRun` and a reason. Nothing executes a tool,
grants consent or writes the store: `goals.advance` and the Goals panel stay the
only drivers, so a re-plan is a proposal in the same durable format as the plan
it replaces.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

from shani_chronoa import goals

#: A failure loop must end. Three re-plans of one step is already a model that
#: is not learning from the error; unbounded retries are a background loop
#: nobody can stop.
MAX_REPLANS = 3
_REPLAN_KEY = "__replans"


def priority_order(run: goals.GoalRun) -> goals.GoalRun:
    """Reorder steps by priority without breaking their dependency edges.

    Higher priority first, ties keep their original order. A step that depends
    on another never moves above it - priority chooses among steps that are
    *ready*, not among steps whose inputs do not exist yet. Reordering across
    that edge would turn a priority into a parked plan.
    """
    order = sorted(
        range(len(run.steps)), key=lambda i: (-int(run.steps[i].priority), i)
    )
    placed: list[goals.PlannedStep] = []
    placed_names: set = set()
    deferred = list(order)
    while deferred:
        progressed = False
        for i in list(deferred):
            step = run.steps[i]
            if all(d in placed_names or d in run.results for d in step.depends_on):
                placed.append(step)
                placed_names.add(step.variable_name)
                deferred.remove(i)
                progressed = True
        if not progressed:
            # A cycle in depends_on cannot be satisfied. Keep the original
            # order for the rest rather than dropping steps.
            placed.extend(run.steps[i] for i in deferred)
            break
    return run._replace(steps=tuple(placed))


def replan_count(run: goals.GoalRun) -> int:
    try:
        return int(run.results.get(_REPLAN_KEY, 0))
    except (TypeError, ValueError):
        return 0


def replan_failed(
    run: goals.GoalRun,
    replan: Callable[[goals.PlannedStep, dict], Iterable[goals.PlannedStep]],
    *,
    now: float = 0.0,
) -> "tuple[goals.GoalRun, str]":
    """(run, why-not). Replace the failed step and re-open the run.

    `replan` is handed the failed step and the settled results; it returns the
    steps to try instead. The last replacement inherits the failed step's
    `variable_name`, so any later step that read that result still has a
    producer - a re-plan that renamed it would park the next step forever.

    Nothing is re-run here. The caller drives `goals.advance` again as usual,
    and every new step still passes through the same consent and verification.
    """
    if run.phase is not goals.Phase.FAILED:
        return run, "only a failed run can be re-planned"
    if not 0 <= run.index < len(run.steps):
        return run, "this failed run has no step to replace"
    if replan_count(run) >= MAX_REPLANS:
        return run, (
            f"this step has already been re-planned {MAX_REPLANS} times; "
            "it was not changed again"
        )
    failed = run.steps[run.index]
    try:
        replacement = tuple(replan(failed, dict(run.results)))
    except Exception as exc:  # noqa: BLE001
        return run, f"the re-plan could not be made: {type(exc).__name__}: {exc}"
    replacement = tuple(s for s in replacement if isinstance(s, goals.PlannedStep))
    if not replacement:
        return run, "the re-plan produced no steps, so the plan was left alone"
    last = replacement[-1]._replace(variable_name=failed.variable_name)
    steps = replacement[:-1] + (last,)
    # A downstream step waiting on the failed result is now waiting on the new
    # last step - same name, so no rewrite of its `depends_on` is needed.
    results = {k: v for k, v in run.results.items() if k != failed.variable_name}
    results[_REPLAN_KEY] = replan_count(run) + 1
    return run._replace(
        steps=run.steps[: run.index] + steps + run.steps[run.index + 1 :],
        phase=goals.Phase.RUNNING,
        results=results,
        awaiting_reason="",
        stale_since=0.0,
        updated_at=now if now > 0 else time.time(),
    ), ""
