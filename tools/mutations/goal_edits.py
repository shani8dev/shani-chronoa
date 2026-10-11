"""Mutation set for the goal-plan edit round-trip.

Rebuilt on 2026-10-11 from the committed tests and code, after the original
`/tmp` harness was lost. The set covers the three things this module exists for:
a removed step is *kept*, the model is told what changed, and both entry points
still work.
"""

TEST_FILE = "tests/test_goal_edits.py"

_GOALS = "usr/lib/shani-chronoa/shani_chronoa/goals.py"
_PANEL = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/goals.py"
_SKILL = "usr/lib/shani-chronoa/shani_chronoa/skills/manage_goals.py"

MUTATIONS = [
    # -- a removed step is kept, not deleted -----------------------------------
    {
        "file": _GOALS,
        "old": "        removed=run.removed + (next(s for s in run.steps\n"
               "                                    if s.variable_name == variable_name),),\n",
        "new": "        removed=run.removed,\n",
        "label": "a removed step is thrown away, so the dimmed row has nothing",
        "expect": ["test_the_step_is_kept_in_the_run",
                   "test_a_json_round_trip_keeps_the_removed_step",
                   "test_a_removed_step_is_drawn_but_dimmed"],
    },
    {
        "file": _GOALS,
        "old": "    steps = tuple(s for s in run.steps if s.variable_name != variable_name)\n",
        "new": "    steps = run.steps\n",
        "label": "the step is not actually removed from the plan",
        "expect": ["test_a_leaf_step_comes_out"],
    },

    # -- the dependency guard, and the transitive closure ---------------------
    {
        "file": _GOALS,
        "old": "    blocked_by = downstream_of(run, variable_name)\n",
        "new": "    blocked_by = [s.variable_name for s in run.steps\n"
               "                  if variable_name in s.depends_on]\n",
        "label": "only the direct dependents block a removal",
        "expect": ["test_the_refusal_names_every_dependent"],
    },
    {
        "file": _GOALS,
        "old": "    if blocked_by:\n",
        "new": "    if False:\n",
        "label": "a depended-on step can be removed, orphaning the rest",
        "expect": ["test_an_earlier_step_cannot_be_removed_while_something_needs_it",
                   "test_dropping_a_depended_on_step_renders_the_refusal"],
    },
    {
        "file": _GOALS,
        # **The message, not the statement.** The first version replaced the
        # whole `return run, (f"..." ...)` and left the parenthesised expression's
        # tail dangling, which is a SyntaxError - the run produced no summary at
        # all. A mutation that cannot parse is not a mutation.
        "old": "        return run, (f\"there is no step named ${variable_name} in this plan, so \"\n"
               "                     \"nothing was removed. The steps are: \"\n",
        "new": "        return run, (\"nothing was removed.\"\n"
               "                     \"\"\n",
        "label": "a removal of a step that is not there names nothing",
        "expect": ["test_a_step_that_is_not_there_is_refused_by_name"],
    },

    # -- restore, and its position ---------------------------------------------
    {
        "file": _GOALS,
        "old": "    if any(s.variable_name == variable_name for s in run.steps):\n"
               "        return run, f\"${variable_name} is already in this plan, so nothing was restored.\"\n",
        "new": "",
        "label": "restoring a step already present duplicates it",
        "expect": ["test_restore_names_the_refusal"],
    },
    {
        "file": _GOALS,
        "old": "        results={k: v for k, v in run.results.items() if k != variable_name},\n",
        "new": "        results=dict(run.results),\n",
        "label": "a removed step's result is kept, so a later step reads a stale value",
        "expect": ["test_a_removed_steps_result_goes_with_it"],
    },
    {
        "file": _GOALS,
        "old": "    index = min(run.index, len(steps))\n",
        "new": "    index = run.index\n",
        "label": "an index past the end is not clamped",
        "expect": ["test_an_index_past_the_end_is_clamped"],
    },
    {
        "file": _GOALS,
        "old": "    if phase is Phase.RUNNING and index >= len(steps):\n        phase = Phase.COMPLETED\n",
        "new": "",
        "label": "removing the last step leaves a running plan empty forever",
        "expect": ["test_removing_the_last_step_completes_a_running_plan"],
    },

    # -- the model is told ------------------------------------------------------
    {
        "file": _SKILL,
        "old": "    line += goals.describe_edits(run)\n",
        "new": "",
        "label": "the model is never told a person edited the plan",
        "expect": ["test_the_skills_description_carries_the_edit"],
    },
    {
        "file": _GOALS,
        "old": "    if not run.edits:\n        return \"\"\n",
        "new": "    if not run.edits:\n        return \"(User's edits to this route: )\"\n",
        "label": "an untouched plan is announced as edited",
        "expect": ["test_an_untouched_plan_says_nothing",
                   "test_the_skills_description_of_an_untouched_plan_barely_changes"],
    },
    {
        "file": _GOALS,
        "old": "            \"edits\": [list(e) for e in self.edits],\n",
        "new": "",
        "label": "the edit log is not persisted",
        "expect": ["test_edits_survive_a_json_round_trip"],
    },

    # -- both entry points still exist ------------------------------------------
    {
        "file": _PANEL,
        "old": "    def remove(self, run_id: str) -> None:\n",
        "new": "",
        "label": "the goal-level remove method is gone",
        "expect": ["test_the_goals_remove_method_exists"],
    },
    {
        "file": _PANEL,
        "old": "    def restore_step(self, run: goals.GoalRun, step: goals.PlannedStep) -> None:\n",
        "new": "",
        # **Not the tooltip or reachability tests.** The back button is built by
        # a lambda that is never invoked during construction, so removing the
        # method leaves the button present and its handler missing - the tooltip
        # walk still finds it. The witnesses are the ones that *press* it.
        "label": "the panel's restore control is gone",
        "expect": ["test_restoring_through_the_panel_puts_it_back",
                   "test_restore_names_the_refusal"],
    },
    {
        "file": _PANEL,
        "old": "    def drop_step(self, run: goals.GoalRun, step: goals.PlannedStep) -> None:\n",
        "new": "",
        "label": "the panel's drop control is gone",
        "expect": ["test_the_control_is_reachable_in_the_built_tree",
                   "test_dropping_a_leaf_step_says_so"],
    },
]
