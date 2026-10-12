"""Mutations for the reranker-corpus generator.

`tools/build_reranker_corpus.py` decides what a model is trained and scored on,
and `tests/test_reranker_corpus.py` holds it. Neither is exercised by anything
else, so this set is what says the tests can fail.

**The first two are not hypothetical.** Both were live defects in the first
version, and the first was invisible to every behavioural test until two builds
of the same commit disagreed with each other:

1. `ranked(say, tools)` with `learned` defaulted reads this machine's live
   tool-call log, so the corpus was a function of the operator's history. Every
   assertion in the file passes on a machine with an empty log.
2. the negatives were shuffled, truncated, and *then* sorted - so `NEGATIVES`
   kept a random subset while the comment above it claimed it kept the confusers.
"""

TEST_FILE = 'tests/test_reranker_corpus.py'

_SOURCE = 'tools/build_reranker_corpus.py'

MUTATIONS = [
    {
        'label': 'the corpus follows this machine tool-call log again',
        'why': 'the measured defect: two builds of one commit reported 212 and '
               '211 positives inside the offered set.',
        'expect': [
            'test_the_call_says_out_loud_that_it_wants_no_learning',
            'test_different_learned_weights_give_the_same_corpus',
        ],
        'file': _SOURCE,
        'old': 'S.ranked(say, T.TOOLS, learned={})',
        'new': 'S.ranked(say, T.TOOLS)',
    },
    {
        'label': 'truncation keeps a random subset instead of the confusers',
        'equivalent': True,
        'why': 'NOT a defect today, and the reason is the finding: NEGATIVES is '
               '12 and DEFAULT_LIMIT is 8, so the offered slice holds at most 7 '
               'non-positive tools and [:NEGATIVES] never truncates. '
               'Shuffle-then-truncate and truncate-then-shuffle are the same '
               'list. The order still matters - raise DEFAULT_LIMIT above '
               'NEGATIVES and it silently becomes a random subset - and '
               'test_the_negative_cap_cannot_exceed_the_offer is what notices '
               'that, because a mutation set cannot notice it from here.',
        'expect': [],
        'file': _SOURCE,
        'old': '        negatives = sorted(negatives, key=lambda n: rank.get(n, 10 ** 6))\n'
               '        negatives = negatives[:NEGATIVES]\n',
        'new': '        rng = random.Random(SEED)\n'
               '        rng.shuffle(negatives)\n'
               '        negatives = sorted(negatives[:NEGATIVES],\n'
               '                           key=lambda n: rank.get(n, 10 ** 6))\n',
    },
    {
        'label': 'a recall failure is dropped from the corpus',
        'why': 'the eight rows where the answer is not in the offered set are '
               'the population a reranker exists to recover.',
        'expect': [
            'test_rows_whose_positive_was_not_offered_are_present',
        ],
        'file': _SOURCE,
        'old': '            stats["not_offered"] += 1\n',
        'new': '            stats["not_offered"] += 1\n            continue\n',
    },
    {
        'label': 'negatives are drawn from the whole registry, not the offer',
        'why': 'the obvious alternative design, and the one every structural '
               'check passes while the corpus trains nothing.',
        'expect': [
            'test_every_negative_was_in_the_selectors_own_offer',
        ],
        'file': _SOURCE,
        'old': '        negatives = [n for n in offered if n != want]\n',
        'new': '        negatives = [S._name(t) for t in T.TOOLS][:NEGATIVES]\n',
    },
    {
        'label': 'the answer is also listed among its own negatives',
        'why': 'a row whose positive is a negative is unscoreable: every '
               'metric over it is undefined rather than wrong.',
        'expect': [
            'test_the_positive_is_not_among_its_own_negatives',
        ],
        'file': _SOURCE,
        'old': '        negatives = [n for n in offered if n != want]\n',
        'new': '        negatives = list(offered)\n',
    },
    {
        'label': 'the arguments are dropped from the corpus again',
        'why': 'the second wrong claim, in the other direction: a scan for '
               '`arguments` instead of `args` concluded 111 argument-bearing '
               'cases carried none, and the corpus followed the scan. '
               '`ascension_gate` scores arguments, so this drops the evidence the '
               'gate is scored on.',
        'expect': [
            'test_the_arguments_reach_every_row_that_has_them',
            'test_the_arguments_are_the_ones_the_corpus_names',
        ],
        'file': _SOURCE,
        'old': 'args = dict(case["expect"][0].get("args") or {})\n',
        'new': 'args = dict(case["expect"][0].get("arguments") or {})\n',
    },
    {
        'label': 'the stated argument limit is replaced with a vague claim',
        'why': 'a file that claims a limit nothing checks is the claim this '
               'repository records as false; this proves the test reads what '
               'the generator writes rather than the file left on disk.',
        'expect': [
            'test_the_limit_is_stated_in_what_the_generator_writes',
        ],
        'file': _SOURCE,
        'old': 'answerable on 111 rows',
        'new': 'answerable on some rows',
    },
    {
        'label': 'the held-out split is dropped so every row is training',
        'why': 'a split that does not hold anything out is a score with no '
               'held-out number behind it.',
        'expect': [
            'test_it_writes_the_file_it_claims',
            'test_the_sides_are_disjoint_on_the_request',
        ],
        'file': _SOURCE,
        'old': '    train, held = rows[:cut], rows[cut:]\n',
        'new': '    train, held = rows, []\n',
    },
]