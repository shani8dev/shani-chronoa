"""Mutations for the paired comparison.

**The pair the whole three-arm decision turns on.** The question is whether a
grammar buys anything over a format request, the arms see the *same* 220 cases,
and the evidence is the handful that changed hands. A comparison that quietly
turned into something else would still print a plausible table - which is the
`skills_total` shape: a figure that cannot distinguish "measured" from "not
measured".

So the mutations here aim at the four ways that happens:

1. **the denominator widens to the union** - a case the service failed in one arm
   becomes a disagreement, so a *missing measurement* reads as a *wrong answer*;
2. **the difference comes from the marginals** - which is a different number
   whenever the two arms have different scorable sets, and identical to it when
   they do not;
3. **`acted` is defined twice** - so the paired table and the marginal table can
   report different things about the same run;
4. **a missing arm prints zeros** - and a row of zeros reads as a measurement of
   nothing.

The interval, the exact p and the discordance direction are here because each has
a failure that produces a *plausible* wrong number rather than an error: an
unseeded bootstrap moves between runs, an unclamped p exceeds one, and swapping
the two discordant counts flips the sign of the finding.
"""

TEST_FILE = 'tests/test_three_arm_paired.py'

_SRC = 'tools/three_arm.py'

MUTATIONS = [
    {
        'label': 'a case one arm failed is counted as a disagreement',
        'why': '**the mutation this design exists to prevent.** Pairing over '
               'the union makes an unavailable row look like a wrong answer, so '
               'a service failure inflates the difference between the arms - '
               'and the arms that fail more are the ones that look better.',
        'expect': ['test_a_case_one_arm_failed_is_dropped_not_counted_as_a_wrong_answer',
                   'test_pairing_uses_only_the_cases_both_arms_answered',
                   'test_pairing_uses_only_the_cases_both_arms_answered'],
        'file': _SRC,
        'old': '    common = sorted(set(a) & set(b))',
        'new': '    common = sorted(set(a) | set(b))',
    },
    {
        'label': 'rows the service failed are scored like any other',
        'why': 'the other route to the same place, and the one a reader '
               'checking only the denominator would miss.',
        'expect': ['test_a_case_one_arm_failed_is_dropped_not_counted_as_a_wrong_answer',
                   'test_pairing_uses_only_the_cases_both_arms_answered'],
        'file': _SRC,
        'old': '        return {r["id"]: r for r in rows\n'
               '                if r.get("id") and not r.get("unavailable")}',
        'new': '        return {r["id"]: r for r in rows if r.get("id")}',
    },
    {
        'label': 'the difference is the two fractions subtracted',
        'why': 'the obvious implementation, and it is a *different number* '
               'exactly when the arms have different scorable sets - so it is '
               'invisible on a clean run and wrong on a real one.',
        'expect': ['test_the_two_numbers_really_do_differ',
                   'test_pairing_uses_only_the_cases_both_arms_answered'],
        'file': _SRC,
        'old': '    diff = ((b_only - a_only) / n) if n else 0.0',
        'new': '    def frac(rows):\n'
               '        sc = [r for r in rows if not r.get("unavailable")]\n'
               '        return sum(bool(r.get(outcome)) for r in sc) / len(sc)\n'
               '    diff = (frac(rows_b) - frac(rows_a)) if a and b else 0.0',
    },
    {
        'label': 'a row carrying an error does not count as acted',
        'why': '`score_arm` asks `seen != "no tool"`, so an error row counts. A '
               'second definition here lets the paired table and the marginal '
               'table report different numbers for the same run, and neither is '
               'wrong on its own terms.',
        'expect': ['test_acted_is_read_the_way_the_marginal_table_reads_it',
                   'test_acted_moves_while_args_does_not'],
        'file': _SRC,
        'old': '        return row.get("seen") != "no tool"',
        'new': '        return row.get("seen") == ""',
    },
    {
        'label': 'the two discordant counts are swapped',
        'why': 'flips the sign of the finding while every count still sums '
               'correctly, so it reads as a small effect rather than a bug.',
        'expect': ['test_gains_and_losses_are_counted_in_opposite_directions',
                   'test_four_points_of_real_effect_is_visible_and_significant'],
        'file': _SRC,
        'old': '    b_only = sum(1 for x, y in pairs if y and not x)\n'
               '    a_only = sum(1 for x, y in pairs if x and not y)',
        'new': '    a_only = sum(1 for x, y in pairs if y and not x)\n'
               '    b_only = sum(1 for x, y in pairs if x and not y)',
    },
    {
        'label': 'no case changed hands is reported as certainty',
        'why': 'zero discordance is the strongest null there is and it is the '
               'result this experiment is most likely to produce. Returning 0.0 '
               'turns the expected outcome into the loudest one.',
        'expect': ['test_no_discordance_is_the_strongest_null_not_an_error',
                   'test_a_tie_reports_a_unit_interval_not_a_narrow_one'],
        'file': _SRC,
        'old': '    n = b + c\n'
               '    if n == 0:\n'
               '        return 1.0',
        'new': '    n = b + c\n'
               '    if n == 0:\n'
               '        return 0.0',
    },
    {
        'label': 'the p is not clamped to one',
        'why': 'doubling the smaller tail exceeds one for balanced counts, and '
               'a probability above 1 is not a probability - it reads as a very '
               'strong effect to anyone who does not check.',
        'expect': ['test_a_p_is_never_above_one'],
        'file': _SRC,
        'old': '    return min(1.0, 2.0 * tail)',
        'new': '    return 2.0 * tail',
    },
    {
        'label': 'the bootstrap is seeded from the clock instead of a constant',
        'why': '**Measured, and it changes nothing this suite can see.** '
               'Dropping the seed does not make the interval differ, because at '
               '2000 draws the 2.5th percentile is the 51st order statistic and '
               'that is a stable functional of the dataset. '
               '`test_the_seed_is_insurance_not_something_the_numbers_depend_on` '
               'draws the interval twelve times from an unseeded generator on '
               'five datasets - including one whose zero-mass is 2.4% against a '
               '2.5% percentile, which is where an order statistic should be '
               'least stable - and gets one distinct answer every time.',
        'equivalent': True,
        'expect': [],
        'file': _SRC,
        'old': '    rnd = random.Random(seed)',
        'new': '    rnd = random.Random()',
    },
    {
        'label': 'the percentiles are taken before the resamples are sorted',
        'why': 'an unsorted list gives a *narrower* interval than the data '
               'supports and one that is the wrong way round, which is the '
               'direction a report reads as more confident.',
        'expect': ['test_the_interval_is_oriented_low_to_high',
                   'test_it_brackets_the_measured_difference',
                   'test_a_tie_reports_a_unit_interval_not_a_narrow_one'],
        'file': _SRC,
        'old': '    diffs.sort()',
        'new': '    diffs = diffs[::-1]',
    },
    {
        'label': 'an arm that never ran prints a number instead of saying so',
        'why': 'a row of zeros for an arm with no results is '
               '**indistinguishable from a measured zero**, which is how a run '
               'that died in arm two comes to read as a finding.',
        'expect': ['test_an_arm_with_no_rows_is_named_as_missing', 'test_a_populated_comparison_does_print_numbers'],
        'file': _SRC,
        'old': '            if not rows_a or not rows_b:',
        'new': '            if False:',
    },
    {
        'label': 'the cases that could not be paired are not counted',
        'why': 'dropping them from the denominator is correct; dropping them '
               'from the *report* is what makes a 40-case shortfall invisible '
               'on a run that claims 220.',
        'expect': ['test_a_dropped_case_is_said_aloud_on_the_line_it_happened',
                   'test_pairing_uses_only_the_cases_both_arms_answered'],
        'file': _SRC,
        'old': '                note = "   [%d case(s) could not be paired]" % r["dropped"]',
        'new': '                note = ""',
    },
    {
        'label': 'the report stops printing the paired comparison at all',
        'why': '**the failure mode of not calling it is a report that looks '
               'complete**, because the marginal table above is still there and '
               'still reads like an answer. Everything else in this set tests '
               '`paired_table` directly, so without a witness on the call site '
               'the function could stop being used and every test would stay '
               'green - the `Registry.register` / `Gateway.may_execute` shape.',
        'expect': ['test_report_prints_the_paired_section',
                   'test_a_grammar_that_only_changes_form_reads_as_such'],
        'file': _SRC,
        'old': '    if len(arms) > 1:\n        paired_table(results, arms)',
        'new': '    if False:\n        paired_table(results, arms)',
    },
    {
        'label': 'form is no longer paired separately from choice',
        'why': '**the split is the finding.** A grammar that makes every '
               'answer well-formed and no more of them right is a different '
               'result from one that changed nothing, and one combined number '
               'reads as the latter.',
        'expect': ['test_acted_moves_while_args_does_not',
                   'test_all_three_sections_are_printed',
                   'test_each_outcome_gets_its_own_pairing'],
        'file': _SRC,
        'old': 'OUTCOMES = (("acted", "acted (form)"),\n'
               '            ("tool", "right tool"),\n'
               '            ("args", "tool+args"))',
        'new': 'OUTCOMES = (("tool", "right tool"),\n'
               '            ("args", "tool+args"))',
    },
]
