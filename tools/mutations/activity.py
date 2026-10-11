"""Mutation set for the measured/unmeasured partition on the Activity panel.

ECC's own skill dashboard shipped the bug this closes (#2463): a never-run skill
was folded into "healthy", so a broken telemetry writer produced a green report
across every skill and nothing ever alerted. Chronoa's per-skill block had the
same shape from the other side - `per_skill()` returns only skills that *have*
calls, so three rows read as a complete account of skill health while the other
252 skills were simply absent from the screen.

The defence is arithmetic rather than prose: `total = measured + unmeasured`, so
an absence is counted. These mutations are the two ways that arithmetic breaks -
folding one count into the other - plus the one way it invents a denominator.
"""

TEST_FILE = "tests/test_tool_activity_panel.py"

_ACTIVITY = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/activity.py"

MUTATIONS = [
    {
        "file": _ACTIVITY,
        "old": "    return (len(known), len(measured), len(known - measured))\n",
        "new": "    return (len(known), len(measured), 0)\n",
        "label": "unmeasured is always zero (the panel claims full coverage)",
        "expect": ["test_measured_and_unmeasured_partition_the_whole_whitelist",
                   "test_a_skill_that_ran_is_measured_and_one_that_did_not_is_not",
                   "test_the_sentence_names_the_absence_and_what_the_block_is",
                   "test_the_partition_is_not_a_number_that_is_always_zero"],
    },
    {
        "file": _ACTIVITY,
        "old": "    return (len(known), len(measured), len(known - measured))\n",
        "new": "    return (len(measured), len(measured), 0)\n",
        # The exact #2463 fold: `total` becomes `measured`, so the whitelist is
        # however many skills happened to run and nothing can be missing.
        "label": "total folds measured in (#2463: the whitelist becomes what ran)",
        "expect": ["test_measured_and_unmeasured_partition_the_whole_whitelist",
                   "test_a_skill_that_ran_is_measured_and_one_that_did_not_is_not",
                   "test_the_sentence_names_the_absence_and_what_the_block_is",
                   "test_the_partition_is_not_a_number_that_is_always_zero"],
    },
    {
        "file": _ACTIVITY,
        "old": "    if not known:\n        return (0, 0, 0)\n",
        "new": "    if not known:\n        known = {r.get(\"tool_name\") for r in records}\n",
        # An unreadable registry must state no number at all. Counting the
        # skills that happened to be in the log instead puts a number over the
        # wrong universe - a claim about the log dressed as a claim about the
        # whitelist, which is the confident-wrong-answer shape this panel
        # exists to avoid.
        "label": "an unreadable registry still invents a denominator",
        "expect": ["test_an_unreadable_registry_states_no_number_at_all"],
    },
    {
        "file": _ACTIVITY,
        "old": '        if tool in known and record.get("verdict") in ("verified", "failed"):\n',
        "new": '        if tool in known:\n',
        # An `unverified` call is most calls on a real machine, and it says
        # nothing about whether the skill works. Counting it would report the
        # shape of the log rather than the state of the skill.
        "label": "an unverified call counts as a measurement",
        "expect": ["test_an_unverified_call_is_not_a_measurement"],
    },
]
