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
]