"""Mutation set for the stale-goal-run reconciliation (oh-my-openagent #8413).

Ported from a `/tmp` harness on 2026-10-11.
"""

TEST_FILE = "tests/test_stale_goal_runs.py"

_GOALS = "usr/lib/shani-chronoa/shani_chronoa/goals.py"

MUTATIONS = [
    # -- the predicate -------------------------------------------------------
    {
        "file": _GOALS,
        "old": "        if run.phase is not Phase.RUNNING or run.stale_since:\n"
               "            out.append(run)\n            continue\n",
        "new": "",
        "label": "already-stamped runs are re-stamped, moving the stopped-time forward",
        "expect": ["test_an_already_stamped_run_is_not_restamped"],
    },
    {
        "file": _GOALS,
        "old": "    if run.phase is not Phase.RUNNING or run.stale_since:\n",
        "new": "    if run.stale_since:\n",
        "label": "non-RUNNING runs are demoted too (a parked week-old run goes 'stopped')",
        "expect": ["test_a_parked_run_a_month_old_is_untouched",
                   "test_a_settled_run_is_untouched"],
    },
    {
        "file": _GOALS,
        "old": "    return max(run.created_at or 0.0, run.updated_at or 0.0)\n",
        "new": "    return run.updated_at or 0.0\n",
        "label": "only updated_at counts, so a run whose updated_at field was lost looks dead",
        # **The witness has to be a run whose `updated_at` is absent while
        # `created_at` is recent** - the case `from_json` actually produces. Two
        # zeros give the same answer either way.
        "expect": ["test_a_run_with_a_lost_updated_at_is_young"],
    },
    {
        "file": _GOALS,
        "old": "    if now - _last_activity(run) < limit:\n            out.append(run)\n"
               "            continue\n",
        "new": "",
        "label": "every RUNNING run is demoted (the live case goes with it)",
        "expect": ["test_a_run_that_just_saved_is_left_alone",
                   "test_nothing_stale_means_nothing_written",
                   "test_only_the_stale_one_changes"],
    },
    {
        "file": _GOALS,
        "old": "    if limit <= 0:\n        limit = float(STALE_RUN_THRESHOLD_SECONDS)\n",
        "new": "",
        "label": "a zero threshold demotes every run, live ones included",
        # **A live run, not a dead one:** a zero threshold also stamps a 30-day
        # dead run, so that witness passes against the mutation.
        "expect": ["test_a_threshold_of_zero_does_not_demote_a_live_run"],
    },
    {
        "file": _GOALS,
        "old": "    raw = os.environ.get(\"CHRONOA_GOAL_STALE_SECONDS\", \"\").strip()\n",
        "new": "    raw = \"\"\n",
        "label": "the environment variable is ignored",
        "expect": ["test_the_threshold_comes_from_the_environment"],
    },

    # -- the write ------------------------------------------------------------
    {
        "file": _GOALS,
        "old": "        if stamp:\n            run = run._replace(updated_at=time.time())\n",
        "new": "        run = run._replace(updated_at=time.time())\n",
        "label": "save always stamps, so the demotion write erases the activity evidence",
        "expect": ["test_the_demotion_write_keeps_updated_at",
                   "test_the_write_does_not_reset_the_activity"],
    },

    # -- the stamp's persistence ---------------------------------------------
    {
        "file": _GOALS,
        "old": "            \"stale_since\": self.stale_since,\n",
        "new": "",
        "label": "the stamp is not persisted, so it is lost on the next open",
        "expect": ["test_the_round_trip_keeps_it"],
    },
    {
        "file": _GOALS,
        "old": "    if not sysfs.finite(value):\n        return 0.0\n    return float(value)\n",
        "new": "    return float(value)\n",
        "label": "a non-numeric timestamp raises inside from_json and the run disappears",
        "expect": ["test_a_garbage_stamp_is_not_a_stamp"],
    },

    # -- what advance does ----------------------------------------------------
    {
        "file": _GOALS,
        "old": "    return run._replace(phase=phase, index=index, results=results,\n"
               "                        stale_since=0.0, updated_at=now if now > 0 else time.time())\n",
        "new": "    return run._replace(phase=phase, index=index, results=results)\n",
        "label": "advance neither clears the stamp nor records the activity",
        "expect": ["test_advance_clears_it", "test_advance_stamps_updated_at"],
    },
    {
        "file": _GOALS,
        "old": "                        stale_since=0.0, updated_at=now if now > 0 else time.time())\n",
        "new": "                        stale_since=0.0)\n",
        "label": "advance clears the stamp but records no activity",
        "expect": ["test_advance_stamps_updated_at"],
    },
    {
        "file": _GOALS,
        "old": "        return run._replace(phase=Phase.FAILED)\n",
        "new": "        return run._replace(phase=Phase.FAILED, stale_since=0.0)\n",
        "label": "a failed run looks live again",
        "expect": ["test_a_step_that_fails_keeps_the_stamp"],
    },
]
