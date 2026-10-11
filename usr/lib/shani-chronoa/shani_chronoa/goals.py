"""Persistent goal queue: a run that can park, and pick up later.

Chronoa's turns are stateless, and that is a deliberate choice for the
common case - a request in, a reply out. It is the wrong shape for the other
case: a trigger fired at 2 a.m. that needs a person before it can go on.
AgentScope's SOP engine (`sop/_engine.py:24-64`, `_state.py:23-44`) is the
reference: the run state is plain data, a step that needs a person ends the
stream, and come back with the answer and the run picks up from the state.
No thread stays suspended.

This module is the data half of that: the queue, the phases, the checkpoint
on disk. The caller drives it - one `advance()` step at a time - so the
async loop, a trigger, or a test all drive it the same way (sayri's
headless-core discipline, `core.py:34-89`, applied to goals rather than the
voice loop).

Why the CAMEL `Task` fields are here too: a goal that decomposes is a `Task`
with a parent, subtasks and dependency edges, and the same persistence
carries it. `Task.state` rides the same phase enum as the run, so a
subtask that parks does not need a second vocabulary.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Callable, NamedTuple, Optional

logger = logging.getLogger(__name__)

#: How long a run may sit in RUNNING without any save before it is declared
#: stopped rather than in progress. **oh-my-openagent's
#: `OMO_BOULDER_STALE_WORK_THRESHOLD_MS`, default 6h**, renamed for this module
#: and reached the same way: an environment variable, so a person debugging a
#: run can watch it go stale in minutes without editing code.
#:
#: Six hours is not arbitrary - it is longer than any single step's tool timeout
#: (the slowest is `_SLOW_TOOLS`' 300s) by a wide margin, so a run that is
#: genuinely stepping itself is never at risk. It is short enough that a run
#: abandoned overnight is noticed the next morning rather than next month.
STALE_RUN_THRESHOLD_SECONDS = 6 * 3600


def _stale_threshold() -> float:
    """The threshold, from the environment if it names a positive number.

    A value that is not a positive number falls back to the default rather than
    to zero: a threshold of 0 would make every run stale the instant it was
    created, which is the same failure as no threshold at all - it would report
    a live run as stopped.
    """
    import os

    raw = os.environ.get("CHRONOA_GOAL_STALE_SECONDS", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return float(STALE_RUN_THRESHOLD_SECONDS)
    return value if value > 0 else float(STALE_RUN_THRESHOLD_SECONDS)


def _last_activity(run: "GoalRun") -> float:
    """When this run was last known to be moving.

    **The newest of `created_at` and `updated_at`, and 0.0 when neither.**
    `updated_at` is stamped by every `GoalStore.save`, and the panel saves after
    every step, so a live run has a recent one. A run from an older build - which
    `from_json` defaults to 0.0 - has **no activity evidence at all**, and
    boulder's rule for exactly that case is that it is stale: *"a work with no
    activity evidence at all is stale."* Returning 0.0 rather than `created_at`
    alone is what makes that rule expressible without a second branch.
    """
    return max(run.created_at or 0.0, run.updated_at or 0.0)


def reconcile_stale_runs(runs: "list[GoalRun]", now: float = 0.0,
                         threshold: Optional[float] = None) -> "list[GoalRun]":
    """Stamp every RUNNING run that has stopped moving.

    **oh-my-openagent's `reconcileStaleWorks`, and the defect it closes:**
    `boulder-state`'s own notes say *"`completeBoulder` is the only completion
    transition, so a work whose session died abnormally would stay `active`
    forever (#8413)"* - and Chronoa had the identical hole with no name for it.
    A run mid-`advance` when the process died keeps `phase=RUNNING` with a frozen
    `updated_at`, and the panel rendered "in progress" forever. Measured: a run
    last touched 30 days ago still said "in progress".

    Four rules, each copied because each is the part that makes it safe:

    - **Only RUNNING runs.** AWAITING is "parked, somebody outside has to
      answer" and may legitimately be weeks old; COMPLETED and FAILED are
      settled. Touching either would rewrite a settled state.
    - **A run already stamped is left exactly as it is.** Re-stamping would move
      `stale_since` forward on every refresh, so a run that stopped last week
      would appear to have stopped today.
    - **Nothing stale means nothing written.** The list comes back with the same
      objects, so a caller can diff and a store write only happens when
      something actually changed.
    - **A threshold of zero or less is ignored** by falling back to the default,
      because it would mark a live run as stopped.

    The caller decides what a stamped run *means*; this only stamps. That split
    is boulder's own - `selectActiveWork` returns a `stale_since` work to active
    and drops the stamp, and here `advance` does the same, so resuming a run is
    the same operation as starting it.
    """
    limit = _stale_threshold() if threshold is None else threshold
    if limit <= 0:
        limit = float(STALE_RUN_THRESHOLD_SECONDS)
    if now <= 0:
        now = time.time()
    out = []
    for run in runs:
        if run.phase is not Phase.RUNNING or run.stale_since:
            out.append(run)
            continue
        if now - _last_activity(run) < limit:
            out.append(run)
            continue
        out.append(run._replace(stale_since=now))
    return out


class Phase(Enum):
    """Where a goal (or a task, or the run itself) is.

    AWAITING is the load-bearing one: parked, somebody outside has to answer
    before it can go on. Nothing in the state is a suspended thread - the run
    is exactly what it will read back off disk.
    """

    PENDING = "pending"
    RUNNING = "running"
    AWAITING = "awaiting"
    COMPLETED = "completed"
    FAILED = "failed"


class PlannedStep(NamedTuple):
    """One step of a plan, ReWOO/Plan-Execute style
    (AutoGPT-classic `prompt_strategies/base.py:55-70`).

    `variable_name` names the step's output so a later step can read it, and
    `depends_on` names the variables that must exist before this step may
    run. The orchestrator refuses to start a step while a dependency is
    unsettled - a plan whose steps cannot see their inputs is not a plan.
    """

    thought: str
    tool_name: str
    tool_arguments: dict
    variable_name: str
    depends_on: tuple = ()


class Task(NamedTuple):
    """CAMEL's `Task` shape, reduced to what a local model will actually use."""

    id: str
    content: str
    state: Phase = Phase.PENDING
    parent: Optional[str] = None
    subtasks: tuple = ()
    result: Optional[str] = None
    failure_count: int = 0
    dependencies: tuple = ()


class GoalRun(NamedTuple):
    """The persisted state of one run. Plain data: json round-trips it."""

    id: str
    goal: str
    phase: Phase
    steps: tuple
    index: int = 0
    awaiting_reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    results: dict = {}
    #: When this run was found stopped mid-plan without having finished, or 0.
    #:
    #: **oh-my-openagent's `boulder-state` (#8413), and the exact defect it
    #: names: "`completeBoulder` is the only completion transition, so a work
    #: whose session died abnormally would stay `active` forever."** Chronoa had
    #: the same hole with no name for it: a run mid-`advance` when the process
    #: died keeps `phase=RUNNING` and a frozen `updated_at`, and the panel
    #: rendered `_PHASE_WORD[RUNNING]` - "in progress" - forever. Measured: a run
    #: last touched 30 days ago still said "in progress", with nothing anywhere
    #: in the tree to age it out.
    #:
    #: The stamp is the demotion, and it is separate from `phase` on purpose.
    #: `Phase.AWAITING` means "parked, somebody outside has to answer", which is
    #: not what a dead run is; a new Phase would ripple through `_PHASE_WORD`,
    #: `LIVE`, `manage_goals` and every reader. Boulder uses `paused` plus a
    #: `stale_since` stamp and leaves its readers alone, and that is the shape
    #: here: `phase` stays RUNNING, the stamp says it stopped, `advance` clears
    #: it, and the word a person reads changes.
    stale_since: float = 0.0
    #: What a person changed by hand, newest last, as `(variable_name, at)`
    #: pairs. **This is the half open-intelligent-ui contributed** (see
    #: `describe_edits`): its route panel lets a person remove stops and add
    #: suggestions, that state is saved with the message, and the next turn
    #: rewrites it as a sentence the model reads - "(User's edits to this route:
    #: removed stops (by id): belem; added stops: LX Factory.)". Without it a
    #: plan a person trimmed looks exactly like one the model wrote whole, and a
    #: model asked to continue simply re-adds what was taken out.
    edits: tuple = ()
    #: The steps a person took out, exactly as they were, newest last.
    #: **open-intelligent-ui dims a removed stop rather than deleting it**
    #: (`travel.css`: `.tv-stop.is-removed { opacity: .45 }` over its photo, time,
    #: title and description), so the person keeps seeing what they removed and
    #: can put it back. Chronoa threw the step away entirely - and a step dropped
    #: by mistake had no route back at all.
    #:
    #: `edits` is what the *model* is told; this is what the *person* is shown.
    #: Two fields on purpose: one is a sentence, the other is state to restore
    #: from, and merging them would lose the step itself.
    removed: tuple = ()

    def to_json(self) -> str:
        return json.dumps({
            "id": self.id, "goal": self.goal, "phase": self.phase.value,
            "steps": [dict(s._asdict()) for s in self.steps],
            "index": self.index, "awaiting_reason": self.awaiting_reason,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "results": dict(self.results),
            "edits": [list(e) for e in self.edits],
            "removed": [dict(s._asdict()) for s in self.removed],
            "stale_since": self.stale_since,
        })

    @classmethod
    def from_json(cls, raw: str) -> "GoalRun":
        data = json.loads(raw)
        steps = tuple(
            PlannedStep(
                thought=s["thought"], tool_name=s["tool_name"],
                tool_arguments=s.get("tool_arguments", {}),
                variable_name=s["variable_name"],
                depends_on=tuple(s.get("depends_on", ())),
            ) for s in data.get("steps", []))
        return cls(
            id=data["id"], goal=data["goal"], phase=Phase(data["phase"]),
            steps=steps, index=data.get("index", 0),
            awaiting_reason=data.get("awaiting_reason", ""),
            created_at=_stored_number(data.get("created_at")),
            updated_at=_stored_number(data.get("updated_at")),
            results=dict(data.get("results", {})),
            edits=tuple(tuple(e) for e in data.get("edits", ())),
            stale_since=_stored_number(data.get("stale_since")),
            removed=tuple(
                PlannedStep(
                    thought=r["thought"], tool_name=r["tool_name"],
                    tool_arguments=r.get("tool_arguments", {}),
                    variable_name=r["variable_name"],
                    depends_on=tuple(r.get("depends_on", ())),
                ) for r in data.get("removed", [])),
        )


def _state_path(root: Path, run_id: str) -> Path:
    return Path(root) / f"{run_id}.json"


def _stored_number(value: object) -> float:
    """A timestamp read off disk, or 0.0 when it is not one.

    **A hand-edited or truncated file can hold a string here, and 0.0 is the only
    honest reading.** `"created_at": "yesterday"` passed straight through before
    this, and `max(created_at, updated_at)` then raised `TypeError` inside
    `_last_activity` - so a corrupt file took the *whole store* down rather than
    one run, because `GoalStore.list` raises on the first bad row and the panel
    reports "the goal store could not be read" for every run in it.

    0.0 rather than the current time on purpose: it means "no activity evidence",
    which `reconcile_stale_runs` already treats as stale, so a file with a broken
    timestamp is demoted rather than looked young forever.

    The finite test is `sysfs.finite`, the one implementation - this was a fifth
    copy of it, written in the same session that consolidated the other four, and
    `test_no_third_copy_exists_anywhere` is what noticed.
    """
    from shani_chronoa import sysfs

    if not sysfs.finite(value):
        return 0.0
    return float(value)


class GoalStore:
    """The durable half: a directory of one-file-per-run checkpoints.

    Atomic replace (temp + `os.replace`), the same discipline as
    `PerceptStore`: a crash mid-write cannot leave a torn run state, only an
    older intact one.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, run: GoalRun, stamp: bool = True) -> GoalRun:
        """Write one run. `stamp=False` writes it exactly as it is.

        **Why the switch exists:** `updated_at` is the *activity* signal
        `reconcile_stale_runs` reads, and stamping it on the write that records
        a run has stopped would erase the only evidence of when it stopped
        moving. Measured without it: a run last touched 30 days ago was
        demoted correctly, and the very save that recorded the demotion reset
        `updated_at` to now - so the card read "last moving in the last hour"
        about a run that had been dead for a month.

        `stamp=True` is the default and every existing caller keeps it: a step
        that ran *is* activity, and the panel's run loop relies on the stamp.
        """
        if stamp:
            run = run._replace(updated_at=time.time())
        path = _state_path(self.root, run.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(run.to_json(), encoding="utf-8")
        os.replace(tmp, path)
        return run

    def load(self, run_id: str) -> Optional[GoalRun]:
        try:
            return GoalRun.from_json(_state_path(self.root, run_id).read_text(
                encoding="utf-8"))
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("Could not load goal run %s: %s", run_id, exc)
            return None

    def list(self) -> "list[GoalRun]":
        runs = []
        for path in sorted(self.root.glob("*.json")):
            try:
                runs.append(GoalRun.from_json(path.read_text(encoding="utf-8")))
            except (ValueError, KeyError):
                continue
        return runs

    def park(self, run: GoalRun, reason: str) -> GoalRun:
        return self.save(run._replace(phase=Phase.AWAITING, awaiting_reason=reason))

    def resume(self, run_id: str, answer: str) -> Optional[GoalRun]:
        """An AWAITING run continues: the answer is the variable the parked
        step was missing, the phase returns to RUNNING."""
        run = self.load(run_id)
        if run is None or run.phase != Phase.AWAITING:
            return None
        # Filed under the variable the parked step is waiting for. It went to
        # `awaiting_answer` only, so a step parked on a missing `depends_on`
        # found that name still missing after the answer and parked again -
        # an answer that could never unblock anything (found wiring the panel).
        key = "awaiting_answer"
        if run.index < len(run.steps):
            missing = [d for d in run.steps[run.index].depends_on if d not in run.results]
            if missing:
                key = missing[0]
        return self.save(run._replace(
            phase=Phase.RUNNING, awaiting_reason="",
            results={**run.results, key: answer, "awaiting_answer": answer}))


def new_run(goal: str, steps, run_id: Optional[str] = None) -> GoalRun:
    now = time.time()
    return GoalRun(
        id=run_id or uuid.uuid4().hex[:12], goal=goal, phase=Phase.PENDING,
        steps=tuple(steps), created_at=now, updated_at=now)


def dependencies_settled(run: GoalRun, step: PlannedStep) -> bool:
    """Every `depends_on` variable has a result. A step started before its
    inputs exist would be run against nothing, so the plan never runs it."""
    return all(name in run.results for name in step.depends_on)


# ---------------------------------------------------------------------------
# Editing a saved plan by hand (2026-10-11, from open-intelligent-ui).
#
# The queue above and `advance()` below are the machine's half: the model
# writes a plan and the queue steps it. What was missing is the person's half,
# and open-intelligent-ui is the reference that has it - its route panel lets
# somebody remove stops and add suggestions, and the next turn rewrites those
# edits as a sentence the model reads. Chronoa's Goals panel had "remove this
# goal" and nothing else, so the only way to correct one wrong step was to
# throw the whole plan away and ask again.
#
# The constraint open-intelligent-ui never faces: a PlannedStep names a
# `variable_name` and other steps depend on it, so removing a step can orphan a
# later one. `remove_step` therefore refuses rather than leaving a plan whose
# steps cannot see their inputs - and it names the step that is in the way,
# because a refusal nobody can locate is a refusal nobody will fix.
# ---------------------------------------------------------------------------

def dependents_of(run: GoalRun, variable_name: str) -> "list[str]":
    """The steps that would lose an input if `variable_name` went away.

    **Direct dependents only, and that is the wrong question here.** See
    `downstream_of`, which is what the refusal uses: the person is deciding
    whether to take a step out of *their* plan, and what they need to know is
    the whole cost, not the first step of it.
    """
    return [s.variable_name for s in run.steps if variable_name in s.depends_on]


def downstream_of(run: GoalRun, variable_name: str) -> "list[str]":
    """Every step that stops being runnable, transitively, in plan order.

    Removing `$a` from a chain `a -> b -> c -> d` directly breaks `$b` only, but
    `$c` and `$d` park too - `$c` on `$b`'s missing result. A refusal that named
    just `$b` would send the person back for a second refusal, then a third,
    which is the "a refusal nobody can locate is a refusal nobody will fix"
    failure wearing a different hat: the cost of the removal is spread over
    three messages none of which states it.

    Measured on a four-step chain, removing the first step names all three of
    the others, so "remove those steps first" is one instruction the person can
    actually carry out.
    """
    order = [s.variable_name for s in run.steps]
    found: "list[str]" = []
    frontier = [variable_name]
    seen = {variable_name}
    while frontier:
        current = frontier.pop(0)
        for s in run.steps:
            if current in s.depends_on and s.variable_name not in seen:
                seen.add(s.variable_name)
                found.append(s.variable_name)
                frontier.append(s.variable_name)
    return sorted(found, key=order.index)


def remove_step(run: GoalRun, variable_name: str, now: float = 0.0
                ) -> "tuple[GoalRun, str]":
    """(run, why-not). Drops one step, or refuses naming what depends on it.

    The refusal is the load-bearing half. Open-intelligent-ui removes a stop
    from an itinerary; a Chronoa step is a tool call whose output later steps
    read by name, so deleting it silently would leave a plan that parks forever
    with `waiting for:` naming a variable nothing produces - which reads as a
    missing result rather than as a plan the person broke.
    """
    if not any(s.variable_name == variable_name for s in run.steps):
        return run, (f"there is no step named ${variable_name} in this plan, so "
                     "nothing was removed. The steps are: "
                     + ", ".join(f"${s.variable_name}" for s in run.steps))
    blocked_by = downstream_of(run, variable_name)
    if blocked_by:
        return run, (
            f"${variable_name} is still needed: " + ", ".join(f"${b}" for b in blocked_by)
            + " cannot run without it. Remove "
            + ("that step" if len(blocked_by) == 1 else "those steps")
            + " first, or the plan would park waiting for a result nothing "
            "produces. Nothing was removed.")
    steps = tuple(s for s in run.steps if s.variable_name != variable_name)
    index = min(run.index, len(steps))
    phase = run.phase
    if phase is Phase.RUNNING and index >= len(steps):
        phase = Phase.COMPLETED
    return run._replace(
        steps=steps, index=index, phase=phase,
        results={k: v for k, v in run.results.items() if k != variable_name},
        edits=run.edits + ((variable_name, now),),
        # **Kept, not deleted.** open-intelligent-ui dims a removed stop so the
        # person still sees what they took out; Chronoa threw it away, and a
        # step dropped by mistake had no way back at all. Measured before this
        # landed: `run.removed` did not exist, and the only route back from a
        # mis-click was to ask the model to plan the whole goal again.
        removed=run.removed + (next(s for s in run.steps
                                    if s.variable_name == variable_name),),
        updated_at=now,
    ), ""


def restore_step(run: GoalRun, variable_name: str, now: float = 0.0
                 ) -> "tuple[GoalRun, str]":
    """(run, why-not). Puts a removed step back, or refuses.

    **The other half of the dimmed row, and the part that makes it useful.** A
    control that puts something back is the only reason "I removed the wrong
    step" is recoverable.

    It refuses rather than duplicating: a second step with the same
    `variable_name` would make every `depends_on` that names it ambiguous about
    which one it means, which is the "a plan whose steps cannot see their
    inputs" failure this module exists to refuse.
    """
    if any(s.variable_name == variable_name for s in run.steps):
        return run, f"${variable_name} is already in this plan, so nothing was restored."
    kept = [s for s in run.removed if s.variable_name == variable_name]
    if not kept:
        return run, (f"there is no removed step named ${variable_name}, so nothing "
                     "was restored.")
    step = kept[-1]
    # **Restored to a position where its dependencies still run first.** Putting
    # it back at the end is wrong exactly when it matters: `$c` depends on `$b`,
    # so appending it behind the remaining steps runs it before its input is
    # produced and it parks forever. Measured on `a -> b -> c` with `$c`
    # removed: the first version restored it as `['a', 'c', 'b']`.
    #
    # A removed step's dependents cannot have been removed - that is what
    # `remove_step` refuses - so they are all present and all want to be after
    # it. The safe spot is after the last thing it needs and before the first
    # thing that needs it.
    order = [s.variable_name for s in run.steps]
    last_dep = max((order.index(name) for name in step.depends_on if name in order),
                   default=-1)
    first_dependent = min((i for i, other in enumerate(run.steps)
                           if variable_name in other.depends_on),
                          default=len(order))
    spot = min(max(last_dep + 1, 0), first_dependent, len(order))
    steps = list(run.steps)
    steps.insert(spot, step)
    # A run can be COMPLETED *because* its last step was taken out
    # (`remove_step` sets exactly that). Restoring the step has to re-open it,
    # or the restored step is a step nothing will ever run - the same silence
    # this function exists to undo. Measured: a one-step run removed to `[]`
    # becomes COMPLETED, and restoring it stayed COMPLETED.
    phase = run.phase
    if phase is Phase.COMPLETED and step.variable_name not in run.results:
        phase = Phase.RUNNING
    return run._replace(
        steps=tuple(steps),
        removed=tuple(s for s in run.removed if s.variable_name != variable_name),
        index=min(run.index, len(steps)),
        phase=phase,
        updated_at=now,
    ), ""


def move_step(run: GoalRun, variable_name: str, to_index: int, now: float = 0.0
              ) -> "tuple[GoalRun, str]":
    """(run, why-not). Reorders one step, or refuses if that breaks its inputs.

    **A step may only move where its dependencies still run before it.** A plain
    reorder is free of that rule only when nothing moves across it; here the
    dependency is checked in the *new* position, so moving a step above one it
    depends on is refused by name.
    """
    order = [s.variable_name for s in run.steps]
    if variable_name not in order:
        return run, (f"there is no step named ${variable_name} in this plan, so "
                     "nothing was moved.")
    if not 0 <= to_index < len(order):
        return run, (f"a plan of {len(order)} steps has no position {to_index}; "
                     f"the range is 0 to {len(order) - 1}. Nothing was moved.")
    steps = list(run.steps)
    step = next(s for s in steps if s.variable_name == variable_name)
    steps = [s for s in steps if s.variable_name != variable_name]
    steps.insert(to_index, step)
    proposed = tuple(steps)
    settled = {d for d in _depends_on_of(proposed, variable_name)}
    seen = set()
    for s in proposed:
        if s.variable_name == variable_name:
            break
        seen.add(s.variable_name)
    missing = [d for d in settled if d not in seen and d not in run.results]
    if missing:
        return run, (
            f"${variable_name} needs " + ", ".join(f"${d}" for d in missing)
            + f" to run first, and position {to_index} is before "
            + ("it" if len(missing) == 1 else "them")
            + ". Nothing was moved.")
    return run._replace(steps=proposed, index=min(run.index, len(proposed)),
                        updated_at=now), ""


def _depends_on_of(steps: tuple, variable_name: str) -> "list[str]":
    for s in steps:
        if s.variable_name == variable_name:
            return list(s.depends_on)
    return []


def describe_edits(run: GoalRun) -> str:
    """A sentence about what a person changed, for the model to read.

    **This is the open-intelligent-ui mechanism, in Chronoa's words.** Its route
    handler takes the saved component state and rewrites it as
    "(User's edits to this route: removed stops (by id): belem; added stops:
    LX Factory.)" so the next turn's model treats the edited route as current
    and does not re-add what was taken out. `manage_goals`' `list` calls this,
    which is the only place a model ever reads a saved plan.

    Empty for a plan nobody touched, so an untouched plan reads as untouched -
    a sentence that named no edits would be indistinguishable from one that
    named few.
    """
    if not run.edits:
        return ""
    removed = [name for name, _at in run.edits]
    return ("\n\n(A person edited this plan by hand: removed "
            + ", ".join(f"${n}" for n in removed)
            + ". Treat the edited plan as the one they want, and do not put "
            "those steps back unless they ask.)")


def advance(run: GoalRun, execute: Callable[[PlannedStep, dict], str],
            now: float = 0.0) -> GoalRun:
    """One forward step of the run.

    `execute` runs one step against the settled variables and returns the
    step's output string. A run whose step is waiting on inputs is parked,
    not failed - that is a task for outside, and the state says so. An
    `execute` that raises fails the run rather than killing the queue: a
    queue that loses parked runs on one bad step is not durable.

    `now` is injectable so a test can prove the *timestamps* rather than
    measuring how long a step took; it defaults to the clock.
    """
    if run.phase in (Phase.COMPLETED, Phase.FAILED):
        return run
    if run.index >= len(run.steps):
        return run._replace(phase=Phase.COMPLETED)
    step = run.steps[run.index]
    if not dependencies_settled(run, step):
        return run._replace(phase=Phase.AWAITING,
                            awaiting_reason=f"waiting for: {[d for d in step.depends_on if d not in run.results]}")
    try:
        output = execute(step, run.results)
    except Exception as exc:  # noqa: BLE001 - a bad step fails the run, not the queue
        logger.warning("goal %s failed at step %s: %s", run.id, step.variable_name, exc)
        return run._replace(phase=Phase.FAILED)
    results = {**run.results, step.variable_name: output}
    index = run.index + 1
    phase = Phase.COMPLETED if index >= len(run.steps) else Phase.RUNNING
    # **A step that runs clears the stamp**, which is boulder's
    # "`selectActiveWork` returns a `stale_since` work to `active` and drops the
    # stamp". Resuming a run is the same operation as starting one, so there is
    # no separate `resume` to call and no way to resume a run into a state that
    # still says it stopped.
    return run._replace(phase=phase, index=index, results=results,
                        stale_since=0.0, updated_at=now if now > 0 else time.time())


# ---------------------------------------------------------------------------
# The wiring (2026-10-08). The queue above had no producer and no consumer;
# `skills/manage_goals.py` saves runs, the Goals panel steps them, the rail
# counts them. Both go through the two functions below, so there is one place
# that says where runs live and one that says how a step is run.
# ---------------------------------------------------------------------------

def store_root() -> Path:
    """Where runs are kept, resolved per call (never at import).

    `$XDG_STATE_HOME/shani-chronoa/goals`, with the `~/.local/state` fallback -
    the same rule `todo_list` follows, for the same recorded reason: a path
    captured at import has twice written test data into a real home.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / "goals"


def fill(arguments: dict, results: dict) -> dict:
    """`$name` in a string argument becomes the result of the step that produced it.

    Only whole-word `$name` for names that have a result; anything else is left
    as written, so a literal dollar sign in an argument is never eaten.
    """
    import re

    def one(value):
        if isinstance(value, str):
            return re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)",
                          lambda m: str(results[m.group(1)]) if m.group(1) in results else m.group(0),
                          value)
        if isinstance(value, dict):
            return {k: one(v) for k, v in value.items()}
        if isinstance(value, list):
            return [one(v) for v in value]
        return value

    return {k: one(v) for k, v in (arguments or {}).items()}


def tool_executor(origin: str = "user") -> Callable[[PlannedStep, dict], str]:
    """Run a step through `tools.execute_tool` - the chat path, permissions and all.

    A step that is refused or asks and is told no comes back as that sentence:
    the run records it as the step's result rather than pretending it worked.
    """
    def execute(step: PlannedStep, results: dict) -> str:
        from shani_chronoa import tools
        return str(tools.execute_tool(step.tool_name, fill(step.tool_arguments, results),
                                      origin=origin))
    return execute

