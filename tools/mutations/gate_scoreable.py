"""Mutations for the population clause on `ascension_gate`.

`Measurement.right` is "the cases it got right" **plus** "the cases that held
nothing to get right", and 109 of the 220 are the second kind. Without the
clause a verdict over 220 rows reads as a verdict over 220 gradable ones.

Three of these six were mis-aimed on the first run and are recorded that way,
because each is a different way a mutation can be wrong while still applying:

- the first aimed at the `None` branch of `_scoreable_clause`, which the
  emitting test never reaches;
- the fourth removed only the **adapter** half of the tie sentence, and the
  witness stayed green because the **base** half was still there and the
  assertion was on the phrase rather than on which side carried it;
- the fifth named the wrong witness, so the test it actually broke reported as
  "failed for another reason".

A mutation that applies is not a mutation that measures.
"""

TEST_FILE = 'tests/test_cultivation.py'

_GATE = 'usr/lib/shani-chronoa/shani_chronoa/cultivation.py'

MUTATIONS = [
    {
        'label': 'the population clause is never emitted',
        'why': 'a passing verdict of "53 of 220" against "50 of 220" reads as a '
               'proportion of the corpus, when 109 of those 220 rows held no '
               'argument to be right about.',
        'expect': ['test_the_verdict_names_the_population_it_was_graded_on'],
        'file': _GATE,
        'old': 'return f" ({m.scoreable} of {m.of} could tell right from wrong)"',
        'new': 'return ""',
    },
    {
        'label': 'an unmeasured population is reported as a number',
        'why': 'None means "not measured"; turning it into a count is the '
               'confident wrong answer the rest of the module avoids.',
        'expect': [
            'test_a_population_that_was_not_measured_says_nothing_about_it',
        ],
        'file': _GATE,
        'old': '    if m.scoreable is None:\n        return ""\n',
        'new': '    if m.scoreable is None:\n'
               '        return f" ({m.of} of {m.of} could tell right from wrong)"\n',
    },
    {
        'label': 'two runs graded over different populations are compared anyway',
        'why': 'the denominator refusal. Without it, 53/220 scored over 111 '
               'gradable rows is subtracted from 50/220 scored over 96, and the '
               'difference is arithmetic on two experiments.',
        'expect': [
            'test_two_runs_over_different_populations_are_not_comparable',
        ],
        'file': _GATE,
        'old': 'if adapted.scoreable is not None and base.scoreable != adapted.scoreable:',
        'new': 'if False:',
    },
    {
        'label': 'a tie is refused without naming the population it was judged on',
        'why': 'a refusal is where a reader looks for the reason. BOTH halves: '
               'the first version removed only the adapter side and the witness '
               'stayed green, because the base side was still in the sentence '
               'and the assertion was on the phrase rather than on which side '
               'carried it.',
        'expect': ['test_a_tie_still_names_the_population_in_its_refusal'],
        'file': _GATE,
        'old': 'f"arguments{_scoreable_clause(adapted)} against {base.right} of "'
               '\n            f"{base.of} for the base alone{_scoreable_clause(base)}. "',
        'new': 'f"arguments against {base.right} of "'
               '\n            f"{base.of} for the base alone. "',
    },
    {
        'label': 'a caller that measured nothing is handed a population',
        'why': 'the default flips from None (unknown) to 220 (everything), so '
               'the unmeasured case starts claiming a population it does not '
               'have. This is the mutation the previous revision aimed at the '
               'wrong field for - it broke the unmeasured test, not the one it '
               'named.',
        'expect': [
            'test_a_population_that_was_not_measured_says_nothing_about_it',
        ],
        'file': _GATE,
        'old': 'scoreable: Optional[int] = None',
        'new': 'scoreable: int = 220',
    },
    {
        'label': 'the measured headroom is replaced by vacuous assertions',
        'equivalent': True,
        'why': 'EQUIVALENT, and the reason is the finding: `vacuous >= 0` and '
               '`vacuous == 109` are both true on this corpus, because all 109 '
               'no-argument cases vacuously score as passing. Weakening the '
               'bound changes nothing any assertion here can see. The test is '
               'not therefore worthless - it DRIVES task_eval rather than '
               'restating a number, so the `== 111` assertion beside it is what '
               'moves when a case gains arguments. The lesson is the pair, not '
               'this one: a count nobody computed from the source is the '
               'constant that stays right by accident and then lies.',
        'expect': [],
        'file': TEST_FILE,
        'old': 'assert vacuous == len(rows) - gradable == 109, (',
        'new': 'assert vacuous >= 0, (',
    },
]
