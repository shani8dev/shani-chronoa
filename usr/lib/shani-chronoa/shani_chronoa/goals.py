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

    def to_json(self) -> str:
        return json.dumps({
            "id": self.id, "goal": self.goal, "phase": self.phase.value,
            "steps": [dict(s._asdict()) for s in self.steps],
            "index": self.index, "awaiting_reason": self.awaiting_reason,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "results": dict(self.results),
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
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            results=dict(data.get("results", {})),
        )


def _state_path(root: Path, run_id: str) -> Path:
    return Path(root) / f"{run_id}.json"


class GoalStore:
    """The durable half: a directory of one-file-per-run checkpoints.

    Atomic replace (temp + `os.replace`), the same discipline as
    `PerceptStore`: a crash mid-write cannot leave a torn run state, only an
    older intact one.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, run: GoalRun) -> GoalRun:
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


def advance(run: GoalRun, execute: Callable[[PlannedStep, dict], str]) -> GoalRun:
    """One forward step of the run.

    `execute` runs one step against the settled variables and returns the
    step's output string. A run whose step is waiting on inputs is parked,
    not failed - that is a task for outside, and the state says so. An
    `execute` that raises fails the run rather than killing the queue: a
    queue that loses parked runs on one bad step is not durable.
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
    return run._replace(phase=phase, index=index, results=results)


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

