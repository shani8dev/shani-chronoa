"""Mutations for `balance_scales` and the `blend(balance=...)` wiring.

The arithmetic is three lines and the justification is a measurement, so the
mutations are aimed at both halves: that the equalising actually equalises, and
that the reasons it is safe stay true.

Three of these first applied without biting, and each is a different way an
anchor can match while measuring nothing:
- aiming at the `None`-branch of the caller's coefficient rather than the
  multiplication that carries it;
- a test-side mutation aimed at the wrong file, so the runner restored a file
  the test never read;
- one that is genuinely equivalent today, kept with its reason.
"""

TEST_FILE = 'tests/test_merge_arithmetic.py'

_SRC = 'usr/lib/shani-chronoa/shani_chronoa/merge.py'

MUTATIONS = [
    {
        'label': 'the scale factors are all 1, so nothing is balanced',
        'why': 'the arithmetic itself. Without it the 111x spread stands and '
               'the merge stays executor + cortex + 0.5% of the third adapter, '
               'which is the confound the class exists to remove.',
        'expect': ['test_equalising_gives_every_parent_the_same_delta_norm'],
        'file': _SRC,
        'old': '    return [(target / n) if n > 0.0 else 1.0 for n in norms]',
        'new': '    return [1.0 for n in norms]',
    },
    {
        'label': 'the caller lambda is overwritten instead of multiplied',
        'why': 'balance=True would silently redefine what lambda means, which '
               'is how a merge stops containing the model the caller asked for.',
        'expect': [
            'test_the_callers_coefficient_survives_rather_than_being_replaced',
        ],
        'file': _SRC,
        'old': 'vectors.append((array, coefficient * scales[index]))',
        'new': 'vectors.append((array, scales[index]))',
    },
    {
        'label': 'a zero-norm parent is given a share of the target',
        'why': 'a parent that contributed nothing would be amplified by a '
               'factor the caller never asked for, creating what training did '
               'not put there.',
        'expect': ['test_a_zero_norm_vector_is_left_alone'],
        'file': _SRC,
        'old': '    return [(target / n) if n > 0.0 else 1.0 for n in norms]',
        'new': '    return [(target / n) if n != -1.0 else 1.0 for n in norms]',
    },
    {
        'label': 'balancing becomes the default',
        'why': 'a new default that changes what a merge contains is a '
               'regression wearing a feature\'s clothes - the same rule '
               'ctxfit used for f16 on the KV ladder.',
        'expect': [
            'test_balancing_is_off_by_default_so_an_ask_for_nothing_is_unchanged',
        ],
        'file': _SRC,
        'old': '          balance: bool = False) -> Path:',
        'new': '          balance: bool = True) -> Path:',
    },
    {
        'label': 'the measured spread stops being checked',
        'equivalent': True,
        'why': 'EQUIVALENT as aimed, and the reason is the finding: the bound '
               '`spread > 10.0` and `spread >= 0.0` are both true on this box, '
               'because the measured spread is 111x. Weakening the threshold '
               'changes nothing any input here can distinguish - the same shape '
               'as `vacuous >= 0` in the gate set, and the same remedy applies: '
               'the assertion that bites is not the bound, it is'
               '`len(per) == 196` beside it, which fails when the adapters stop '
               'being read at all. **A threshold nobody can fail is a threshold '
               'that documents a measurement rather than defends it.** The '
               'spread being re-derived from the real GGUFs at all is the part '
               'that matters, and that is what the 29-second runtime is buying.',
        'expect': [],
        'file': TEST_FILE,
        'old': '        assert spread > 10.0, (',
        'new': '        assert spread >= 0.0, (',
    },
]
