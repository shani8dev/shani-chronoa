"""Mutation set for `Report.honest_reason` - a refusal is not a result.

`harness-study/laya-decision`'s `laya/evidence.py` states the doctrine:

> Every state it assigns is either derived directly from an upstream artifact or
> marked UNKNOWN. It never manufactures a healthy-looking state from absent
> input.

and it keeps `INSUFFICIENT` apart from `MISSING`: *there and too thin to support
a claim* is not *not there*.

Chronoa's `Report.honest()` collapsed the same distinction into a bare `False`.
Measured: a model whose minority verdict has 3 examples (under `_MIN_CLASS`'s 20)
and a model that genuinely failed return **the same answer**, and the two have
opposite remedies - use the assistant more, or stop quoting the model. This file
records the principle from the other side: a truthful refusal demoted as a
failure is a defect.

The callsites read `honest()` and are unchanged; the reason is additive, so the
tests assert the two never disagree.
"""

TEST_FILE = "tests/test_honest_reason.py"

_L = "usr/lib/shani-chronoa/shani_chronoa/learning.py"

MUTATIONS = [
    # -- the reason exists at all -------------------------------------------
    {
        "file": _L,
        "old": "        if self.honest():\n            return \"\"\n",
        "new": "        if True:\n            return \"\"\n",
        "label": "**the finding, restored**: no reason is ever given, so a "
                 "refusal reads as a result",
        "expect": ["test_too_little_data_is_not_a_negative_result",
                   "test_a_real_failure_is_a_different_answer"],
    },
    {
        "file": _L,
        "old": "        if self.honest():\n            return \"\"\n",
        "new": "        if not self.honest():\n            return \"\"\n",
        "label": "**the over-fix**: the reason is given for every *quotable* "
                 "report and none for the refused ones",
        "expect": ["test_a_quotable_report_gives_no_reason",
                   "test_the_reason_and_the_bool_never_disagree"],
    },

    # -- the two cases must stay apart --------------------------------------
    {
        "file": _L,
        "old": "        thin = [name for name, count in counts.items()\n"
               "                if 0 < count < _MIN_CLASS]\n"
               "        if thin and len(counts) <= 2:\n",
        "new": "        thin = []\n        if False:\n",
        "label": "an unanswerable question is reported as a negative result",
        "expect": ["test_too_little_data_is_not_a_negative_result",
                   "test_a_real_failure_is_a_different_answer"],
    },
    {
        "file": _L,
        "old": "        thin = [name for name, count in counts.items()\n"
               "                if 0 < count < _MIN_CLASS]\n"
               "        if thin and len(counts) <= 2:\n",
        "new": "        thin = [name for name, count in counts.items()\n"
               "                if count < _MIN_CLASS]\n"
               "        if thin:\n",
        "label": "the thin test drops `0 <`, so a verdict with **no examples at "
                 "all** reads as thin rather than absent",
        "expect": ["test_too_little_data_is_not_a_negative_result",
                   "test_a_wide_report_with_one_thin_verdict_is_a_negative_result"],
    },
    {
        "file": _L,
        "old": "        thin = [name for name, count in counts.items()\n"
               "                if 0 < count < _MIN_CLASS]\n"
               "        if thin and len(counts) <= 2:\n",
        "new": "        thin = [name for name, count in counts.items()\n"
               "                if 0 < count < _MIN_CLASS]\n"
               "        if thin:\n",
        "label": "the thin test drops the class-count guard, so a wide report "
                 "with one thin verdict is unanswerable rather than negative",
        "expect": ["test_a_wide_report_with_one_thin_verdict_is_a_negative_result",
                   "test_a_real_failure_is_a_different_answer"],
    },

    # -- the refusal must be actionable -------------------------------------
    {
        "file": _L,
        "old": "            return (\"insufficient\", \"no verdict has \"\n"
               "                    f\"{_MIN_CLASS} examples (\"\n"
               "                    + \", \".join(f\"{name} {count}\" for name, count in sorted(counts.items()))\n"
               "                    + \"), so whether this model detects anything cannot be \"\n"
               "                      \"answered from this log rather than answered no\")\n",
        "new": "            return (\"insufficient\", \"not enough data\")\n",
        "label": "the refusal quotes no counts and no reason, so a person cannot "
                 "tell whether one more call would change the answer",
        "expect": ["test_too_little_data_is_not_a_negative_result",
                   "test_the_reason_names_the_counts_it_refused_on"],
    },
    {
        "file": _L,
        "old": "        return (\"not-detected\",\n",
        "new": "        return (\"insufficient\",\n",
        "label": "**the conflation, restored**: a measured negative is reported "
                 "as an unanswerable question",
        "expect": ["test_a_real_failure_is_a_different_answer"],
    },
    {
        "file": _L,
        "old": "        return (\"not-detected\",\n",
        "new": "        return (\"\",\n",
        "label": "a measured negative gives no reason at all",
        # **Not `test_the_reason_and_the_bool_never_disagree`:** `return ("",
        # "text")` is a *tuple*, which is truthy, so `bool(reason) is not
        # honest()` holds either way - which is why the mutation survived the
        # witness first named for it.
        "expect": ["test_a_real_failure_is_a_different_answer"],
    },

    # **The `# pragma: no cover` marker on the unreachable branch is a comment,
    # and removing it changes nothing observable.** That mutant was in the first
    # version of this set and came back as a survivor - a mutant that cannot be
    # caught is not a mutant, so it is deleted rather than kept with a note.

]
