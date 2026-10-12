"""Mutation set for the calibrated/naive comparison nobody was making.

`calibrated_quantize` builds two files on purpose - the same target from the same
source, one fitted with an importance matrix and one without - and its own return
value calls the second *"beside it for comparison"*. Measured over the tree before
this work: `naive_path` had **no production reader at all**, only a test asserting
the two paths differ. So the operation's entire justification went unverified at
the only moment a person looks at it, and `adopt_path`'s gate could not answer
it - that compares against the model being *replaced*, which is a different
question.

Two ways this breaks, and the third one is the mutation set's real reason:

1. the outcome is flattened to "helped", so a matrix that made the model *worse*
   is reported as a success;
2. an absent measurement is reported as a measured zero - the substitution this
   file keeps recording, in the one place it is easiest to do by accident;
3. **the Rebuild button stops carrying the sentence.** Every other mutation is in
   a helper that all thirteen tests call directly, so with only those, deleting
   the call in `settings_window/window.py` leaves the file green while the panel
   says "Built Q4_K_M" and nothing else. That is this repository's own rule - a
   test that calls the function is not a test that the control is reachable - so
   three of the tests drive the real handler and read the lines a person sees.
"""

TEST_FILE = "tests/test_calibration_comparison.py"

_LLM = "usr/lib/shani-chronoa/shani_chronoa/local_llm.py"
_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/settings_window/window.py"

MUTATIONS = [
    {
        "file": _LLM,
        "old": '    if after == before:\n'
               '        verdict = "unchanged"\n'
               '    elif after < before:\n'
               '        verdict = "helped"\n'
               '    else:\n'
               '        verdict = "hurt"\n',
        "new": '    verdict = "helped"\n',
        "label": "every outcome is reported as an improvement",
        "expect": ["test_a_worse_matrix_is_reported_as_hurting",
                   "test_two_identical_numbers_are_unchanged_not_helped"],
    },
    {
        "file": _LLM,
        "old": "    if before is None or after is None:\n",
        "new": "    if False:\n",
        "label": "an unmeasurable build is treated as a measured number",
        # **Test ids, not the class.** The first version of this witness was
        # `["TestWhatIsNotAMeasurement"]`, and the runner reported the mutation
        # a SURVIVOR while printing "4 failed" - every one of them the right
        # test. A named witness that matches no test id is a witness that never
        # ran, which is the same defect as a check that cannot fail.
        "expect": ["test_an_unmeasurable_calibrated_build_is_not_zero",
                   "test_both_missing_names_both"],
    },
    {
        "file": _LLM,
        "old": '            "why": f"perplexity {before:.3f} uncalibrated against {after:.3f} "\n'
               '                   f"calibrated ({change:+.3f}%), so the matrix made it {direction} "\n'
               '                   "on the same fixed corpus"}\n',
        "new": '            "why": f"{change:+.3f}%, so the matrix made it {direction}"}\n',
        "label": "the sentence keeps the percentage and drops both absolute numbers",
        "expect": ["test_the_sentence_carries_both_numbers_and_not_only_the_percentage"],
    },
    {
        "file": _LLM,
        "old": "    if before <= 0.0:\n",
        "new": "    if False:\n",
        "label": "a non-positive baseline is divided instead of refused",
        "expect": ["test_a_non_positive_baseline_is_refused_rather_than_divided"],
    },
    {
        "file": _LLM,
        "old": "    if naive == calibrated:\n",
        "new": "    if False:\n",
        "label": "one path reported twice counts as a comparison of two",
        "expect": ["test_one_file_reported_twice_is_not_a_comparison"],
    },
    {
        "file": _WINDOW,
        "old": "        comparison = local_llm.compare_calibration(out)\n",
        "new": '        comparison = {"measured": True, "why": "perplexity 1.750, built"}\n',
        "label": "the Rebuild button stops asking whether the matrix helped",
        "expect": ["test_a_person_is_told_whether_the_matrix_helped",
                   "test_an_unmeasured_comparison_is_announced_rather_than_omitted"],
    },
    {
        "file": _WINDOW,
        "old": '        if comparison.get("measured"):\n'
               '            report(str(comparison["why"]))\n'
               '        else:\n'
               '            report(f"The matrix\'s effect was NOT measured. "\n'
               '                   f"{str(comparison[\'why\'])[:160]}")\n',
        "new": '        report(str(comparison["why"]))\n',
        "label": "always take the measured branch, so an unmeasured result is silent",
        "expect": ["test_an_unmeasured_comparison_is_announced_rather_than_omitted"],
    },
]