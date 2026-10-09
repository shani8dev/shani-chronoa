"""Skill: save a multi-step goal as a plan, and look at the ones already saved.

The producer `goals.py` never had. The goal queue - plans of tool steps with
named results and dependencies, a run that can park until a person answers and
pick up again from disk - was complete and nothing could put a run in it, so
the rail and the panels deliberately showed nothing for it.

**This skill only writes the plan. It runs nothing.** Steps are run from the
Goals panel, one at a time or to the end, and each runs through
`tools.execute_tool` - the chat path, with the same consent keys, approval
questions and destructive-tool rules. A plan that names a tool you have not
allowed is saved and then refused at that step, which is the honest order: a
skill that executed its own plan would be a second, unaudited way to run tools.

Every tool a step names is checked against the shipped whitelist when the plan
is saved, and a plan may not name this skill (a goal that writes goals is a
loop with nobody in it).
"""

from __future__ import annotations

from shani_chronoa import goals
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "goals-enabled"
_ACTIONS = ("add", "list", "answer", "remove")
_MAX_STEPS = 20

SCHEMA = {
    "type": "function",
    "function": {
        "name": "manage_goals",
        "description": (
            "Save a multi-step goal as a plan of tool steps that can be run "
            "later from the Goals panel, list saved goals, answer one that is "
            "waiting for you, or remove one. Saving runs nothing. Each step "
            "names a tool, its arguments, and a name for its result; a later "
            "step can use an earlier result as $name in a string argument, and "
            "lists the names it depends_on. A depends_on name that no earlier "
            "step produces is a question for the user: the goal waits there "
            "until they answer it. Requires the 'goals-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": f"What to do: {', '.join(_ACTIONS)}. Defaults to list.",
                },
                "goal": {"type": "string", "description": "What the plan is for. Required for add."},
                "steps": {
                    "type": "array",
                    "description": (
                        "For add: the plan, in order. Each item: {thought, tool, "
                        "arguments, name, depends_on}."
                    ),
                    "items": {"type": "object"},
                },
                "id": {"type": "string", "description": "A saved goal's id. Required for answer and remove."},
                "answer": {"type": "string", "description": "For answer: what the waiting step needs."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"keeping goals is turned off (enable '{_CONSENT_KEY}' in Settings). "
            "A saved goal outlives the conversation, so it is a separate permission."
        )
    return True, ""


def _known_tools() -> set:
    from shani_chronoa import tools
    return {t["function"]["name"] for t in tools.TOOLS}


def parse_steps(raw) -> "tuple[list, str]":
    """Validate the model's plan into `PlannedStep`s, or say what is wrong."""
    if not isinstance(raw, list) or not raw:
        return [], "a plan needs at least one step"
    if len(raw) > _MAX_STEPS:
        return [], f"a plan may have at most {_MAX_STEPS} steps, not {len(raw)}"
    known = _known_tools()
    steps, names = [], set()
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return [], f"step {i} is not an object"
        tool = str(item.get("tool") or item.get("tool_name") or "").strip()
        if not tool:
            return [], f"step {i} names no tool"
        if tool == "manage_goals":
            return [], f"step {i} would save another goal from inside a goal"
        if tool not in known:
            return [], f"step {i} names {tool!r}, which is not an installed skill"
        arguments = item.get("arguments") or item.get("tool_arguments") or {}
        if not isinstance(arguments, dict):
            return [], f"step {i}'s arguments are not an object"
        name = str(item.get("name") or item.get("variable_name") or f"step{i}").strip()
        if name in names:
            return [], f"step {i} reuses the result name {name!r}"
        # A name no earlier step produces is a question for the person: the run
        # parks at this step, the Goals panel asks, and the answer fills that
        # name (`GoalStore.resume`). That is the case the queue exists for.
        depends = tuple(str(d) for d in (item.get("depends_on") or ()))
        names.add(name)
        steps.append(goals.PlannedStep(
            thought=str(item.get("thought") or ""), tool_name=tool,
            tool_arguments=arguments, variable_name=name, depends_on=depends))
    return steps, ""


def describe(run: "goals.GoalRun") -> str:
    done = min(run.index, len(run.steps))
    line = f"{run.id}  [{run.phase.value}]  {run.goal}  ({done}/{len(run.steps)} steps)"
    if run.awaiting_reason:
        line += f"  - waiting: {run.awaiting_reason}"
    return line


def _run(arguments: dict) -> str:
    action = str(arguments.get("action") or "list").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}, not {action!r}."
    store = goals.GoalStore(goals.store_root())
    if action == "list":
        runs = store.list()
        if not runs:
            return "No goals are saved."
        return "\n".join(describe(r) for r in sorted(runs, key=lambda r: r.created_at))

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change goals: {reason}"

    if action == "add":
        goal = str(arguments.get("goal") or "").strip()
        if not goal:
            return "A goal needs a description, so nothing was saved."
        steps, problem = parse_steps(arguments.get("steps"))
        if problem:
            return f"Refusing to save {goal!r}: {problem}. Nothing was saved."
        run = store.save(goals.new_run(goal, steps))
        return (f"Saved goal {run.id}: {goal} ({len(steps)} steps). Nothing has run - "
                "start it from the Goals panel, where each step asks as chat would.")

    run_id = str(arguments.get("id") or "").strip()
    if not run_id:
        return f"{action} needs the goal's id."
    if action == "answer":
        run = store.resume(run_id, str(arguments.get("answer") or ""))
        if run is None:
            return f"Goal {run_id!r} is not waiting for an answer."
        return f"Answered {run_id}; it can continue from the Goals panel."
    # remove
    path = goals._state_path(store.root, run_id)
    if not path.is_file():
        return f"No saved goal is called {run_id!r}."
    path.unlink()
    return f"Removed goal {run_id}."


SKILLS = [Skill(name="manage_goals", schema=SCHEMA, run=_run)]
