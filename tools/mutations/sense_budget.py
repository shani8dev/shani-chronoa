"""Mutation set for the per-sense wall-clock budget in the ambient scheduler.

The defect: `poll()` called `sense.run()` directly, guarded only by
`except Exception`. That catches a sense that *raises* and does nothing at all
about one that *hangs*. Measured: a single sense whose `run` blocked on
`threading.Event().wait()` left `poll_due()` blocked forever, and the healthy
sense registered next to it was never polled - one hung sense stopped the whole
ambient layer for the life of the process, which is every sense in it.

The budget is the sense's own `poll_interval`, floored and capped, because the
scheduler already defines a sense slower than its own interval as one that is too
slow (`poll_due`'s comment on stamping `_last_poll` after the run). A constant
would silently decide which of a 2s audio sense and a 10-minute snapshot check is
allowed to be slow.

ECC's `observe.sh` solves the same problem with `signal.alarm(8)` - "self-
terminate before the async hook 10s timeout can orphan us". That does not port:
`signal.alarm` can only be armed on the main thread and the scheduler is not on
it. The honest limit of this fix is that the sense's **thread is abandoned, not
killed**; what stops is everything downstream of it.
"""

TEST_FILE = "tests/test_sense_budget.py"

_SCHED = "usr/lib/shani-chronoa/shani_chronoa/senses/scheduler.py"

G_LINE = '                              f"timed out after {exc.seconds:g}s; the result was "\n'
RETURN_FALSE = "            return PollResult(name, False, None,\n"
RETURN_TRUE = "            return PollResult(name, True, None,\n"

MUTATIONS = [
    {
        "file": _SCHED,
        "old": "            result = _run_with_budget(name, sense, dict(self._arguments.get(name, {})))\n",
        "new": "            result = sense.run(dict(self._arguments.get(name, {})))\n",
        # The original defect, restored verbatim: a hung sense blocks the loop
        # forever and every other sense goes unpolled.
        "label": "the budget is removed (the original bug)",
        "expect": ["test_the_healthy_sense_is_still_polled",
                   "test_the_hung_sense_reports_a_timeout_rather_than_a_crash"],
    },
    {
        "file": _SCHED,
        "old": "    worker.join(seconds)\n    if worker.is_alive():\n        raise _SenseTimedOut(seconds)\n",
        "new": "    worker.join(None)\n    if worker.is_alive():\n        raise _SenseTimedOut(seconds)\n",
        # The join's timeout is the whole mechanism. Waiting forever is the bug
        # with the exception left in place - which is the shape that would make
        # this look handled while nothing was bounded.
        "label": "the join waits forever (the TimeoutOut branch becomes unreachable)",
        "expect": ["test_the_healthy_sense_is_still_polled"],
    },
    {
        "file": _SCHED,
        "old": "    interval = sense.poll_interval or 0.0\n    return max(SENSE_BUDGET_FLOOR, min(interval, SENSE_BUDGET_CEILING))\n",
        "new": "    return SENSE_BUDGET_CEILING\n",
        # The budget is the sense's own interval, floored and capped. A constant
        # cannot serve both a 2s audio sense and a 10-minute snapshot check, and
        # picking one number decides which of them is allowed to be slow.
        "label": "the budget is a constant instead of the sense's own interval",
        "expect": ["test_the_budget_is_the_senses_own_interval"],
    },
    {
        "file": _SCHED,
        "old": "    return max(SENSE_BUDGET_FLOOR, min(interval, SENSE_BUDGET_CEILING))\n",
        "new": "    return interval\n",
        # The floor is what stops a fast interval from truncating real work, and
        # the ceiling is what stops a slow one licensing a long hang. Dropping
        # both leaves a 2s audio sense with a 2s budget - killed by its own
        # schedule the first time the machine is loaded.
        "label": "the floor and ceiling are dropped",
        "expect": ["test_the_budget_is_the_senses_own_interval"],
    },
    {
        "file": _SCHED,
        "old": "        try:\n            box[\"result\"] = sense.run(arguments)\n        except BaseException as exc:  # propagated below, on this thread's terms\n            box[\"error\"] = exc\n",
        "new": "        try:\n            box[\"result\"] = sense.run(arguments)\n        except Exception as exc:\n            box[\"error\"] = exc\n",
        # `BaseException` on purpose, so a `KeyboardInterrupt` or `SystemExit`
        # inside a sense reaches the polling thread rather than dying in a daemon
        # where nobody is watching. Narrowing it to `Exception` is the obvious
        # simplification and it loses exactly the signals that matter.
        "expect": ["test_a_sense_that_raises_still_behaves_exactly_as_before"],
        "equivalent": True,
        "why": "no sense in the suite raises a BaseException, so the narrower "
               "clause reaches the same outcome for every input these tests "
               "supply. Kept because the wider clause is the deliberate choice "
               "and a reader should see it tested rather than assumed.",
        "label": "the worker catches Exception instead of BaseException",
    },
    {
        "file": _SCHED,
        "old": G_LINE,
        "new": G_LINE.replace(":g}", ":.0f}"),
        # Found by running, not reading: `:.0f` on a sub-second budget prints
        # "timed out after 0s", which is a wrong number in the one sentence a
        # person reads to find out what happened to their sense.
        "label": "the timeout message rounds a sub-second budget to zero",
        "expect": ["test_a_sub_second_budget_does_not_read_as_zero"],
    },
    {
        "file": _SCHED,
        "old": RETURN_FALSE + G_LINE,
        "new": RETURN_TRUE + G_LINE,
        # `ok` is the honesty of the result: ok=True says a percept was deposited.
        # A timeout deposits nothing, so marking it ok would put a green row in
        # the history for a sense that never answered.
        "label": "a timeout is recorded as a success",
        "expect": ["test_the_hung_sense_reports_a_timeout_rather_than_a_crash"],
    },
]
