"""Mutations for the three-arm report.

The report's job is to keep **form** and **choice** apart, and the way it can
fail is by quietly recombining them - which produces a number that looks fine and
means nothing. Each mutation below is a different recombination, and each is
caught by a test that asserts the split survives.

The denominator is the other half. A row the *service* failed is not a row the
model got wrong, and folding one in makes a broken arm read as a weak one.
"""

TEST_FILE = 'tests/test_three_arm_report.py'

_SRC = 'tools/three_arm.py'

MUTATIONS = [
    {
        'label': 'form is measured with the choice test',
        'why': 'the whole report collapses back to one number. "form up and '
               'choice flat" would then be reported as a gain in accuracy, which '
               'is the reading this file exists to prevent.',
        'expect': ['test_a_call_that_was_wrong_counts_as_form_but_not_choice'],
        'file': _SRC,
        'old': '    acted = sum(1 for r in scorable if r.get("seen") != "no tool")',
        'new': '    acted = sum(1 for r in scorable if r.get("tool"))',
    },
    {
        'label': 'not calling a tool counts as calling one',
        'why': 'form validity would read 100% for every arm including the ones '
               'that answered in prose, so the measurement could not fail.',
        'expect': ['test_no_call_is_neither'],
        'file': _SRC,
        'old': '    acted = sum(1 for r in scorable if r.get("seen") != "no tool")',
        'new': '    acted = sum(1 for r in scorable if r.get("seen") is not None)',
    },
    {
        'label': 'the failed rows stay in the denominator',
        'why': 'a 429 or a timeout becomes a miss, so an arm that never reached '
               'the model scores zero and reads as the weakest one rather than '
               'as a run that did not happen.',
        'expect': ['test_an_unavailable_row_leaves_the_denominator',
                   'test_an_unavailable_row_is_not_counted_as_a_miss'],
        'file': _SRC,
        'old': '    scorable = [r for r in rows if not r.get("unavailable")]',
        'new': '    scorable = list(rows)',
    },
    {
        'label': 'an all-failed run reports a population of one',
        'why': 'a run in which every request failed would print 0/1 rather than '
               '0/0, and 0/1 is a score a reader can compare against another '
               'arm\'s. 0/0 is visibly a run that did not happen.',
        'expect': ['test_all_unavailable_is_an_empty_population_not_a_zero_score'],
        'file': _SRC,
        'old': '            sum(1 for r in scorable if r.get("args")),\n            len(scorable))',
        'new': '            sum(1 for r in scorable if r.get("args")),\n'
               '            max(1, len(scorable)))',
    },
    {
        'label': 'the default case list stops matching the recorded ladder',
        'why': 'a fresh sample is a different experiment; the comparison the run '
               'exists for would be void and the recorded numbers beside it '
               'meaningless.',
        'expect': ['test_the_population_is_read_from_the_file_not_from_the_spec'],
        'file': _SRC,
        'old': 'CASES = "time,weather,volume,set_timer,reminder"',
        'new': 'CASES = "time,weather"',
    },
    {
        'label': 'the re-report flag starts a server and asks the model again',
        'why': '**the whole point of the flag is that it does not.** A per-arm '
               'JSON file is worth nothing if reading it costs five hours of '
               'inference, and the run in flight right now loaded this module '
               'before the paired comparison existed - so its console output is '
               'the old report and the new one has to come from somewhere.',
        'expect': ['test_it_starts_no_server',
                   'test_a_finished_run_is_reported',
                   'test_it_does_not_need_the_cases_file_to_exist'],
        'file': _SRC,
        'old': '    if args.report:\n        return report_only(args.report)',
        'new': '    if False:\n        return report_only(args.report)',
    },
    {
        'label': 'a re-report of a file that was never written succeeds',
        'why': 'the header would print over nothing, and the reader has no way '
               'to tell an empty report from a finished one.',
        'expect': ['test_a_missing_file_is_refused_by_name'],
        'file': _SRC,
        'old': '        print("could not read %s: %s" % (path, exc))\n        return 2',
        'new': '        print("could not read %s: %s" % (path, exc))\n        return 0',
    },
    {
        'label': 'a file holding no arm of this run is reported as one',
        'why': 'it prints a header naming arms it never found, which is the '
               '`skills_total` failure: a figure that cannot distinguish "did '
               'not happen" from "was zero".',
        'expect': ['test_a_file_holding_no_arm_of_this_run_says_which_arms_it_knew'],
        'file': _SRC,
        'old': '        print("arms this tool knows: %s" % ", ".join(ARMS))\n        return 2',
        'new': '        print("arms this tool knows: %s" % ", ".join(ARMS))\n        return 0',
    },
    {
        'label': 'the re-reported population is the number of arms, not of cases',
        'why': 'the header would claim a population nobody ran, and the whole '
               'point of re-deriving it is that a remembered 220 is a stale '
               'label on a measurement.',
        'expect': ['test_the_population_is_derived_from_the_rows_not_remembered'],
        'file': _SRC,
        'old': '    report(merged, arms, len(ids), dropped)',
        'new': '    report(merged, arms, len(arms), dropped)',
    },
    {
        'label': 'a re-report of two arms calls itself a three-arm result',
        'why': 'the run that died in arm two is the case this file exists for, '
               'and a report that does not say so is the failure to keep the '
               'arms that did finish while appearing to have lost them.',
        'expect': ['test_a_two_arm_run_is_still_called_a_two_arm_run'],
        'file': _SRC,
        'old': '    arms = [a for a in ARMS if merged.get(a, {}).get("rows")]',
        'new': '    arms = list(ARMS)',
    },
]
