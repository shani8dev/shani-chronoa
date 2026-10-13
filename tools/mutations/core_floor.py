"""Mutations for the CORE-floor attribution and its measured verdict.

The class exists to stop a *budget figure* being read as a defect: 40% of the
offered schemas score no word match, the arithmetic is right, and reclaiming it
costs 2 of 8 cases. These mutations aim at both halves - the attribution and the
recorded verdict - because either half going missing lets the next reader reach
for the same fix.
"""

TEST_FILE = 'tests/test_tool_select_recall.py'

_SRC = 'usr/lib/shani-chronoa/shani_chronoa/tool_select.py'

MUTATIONS = [
    {
        'label': 'the measured verdict is dropped from the module that declares CORE',
        'why': 'without it the 40% figure stands alone, and a number with no '
               'mechanism beside it is an invitation to try the same fix.',
        'expect': ['test_conditional_core_is_refused_by_the_measurement_not_by_taste'],
        'file': _SRC,
        'old': '7/8 as shipped against 5/8',
        'new': 'better after the change',
    },
    {
        'label': 'the attribution stops naming where the floor comes from',
        'why': 'a total with no attribution is the shape that makes 40% read as '
               'waste; the per-tool counts are what say it is a constant.',
        'expect': ['test_conditional_core_is_refused_by_the_measurement_not_by_taste'],
        'file': _SRC,
        'old': '`get_datetime` on 212, `ask_user` on 177, `web_search` on 163',
        'new': 'those three members of the set',
    },
    {
        'label': 'the zero-match budget stops being CORE-dominated',
        'why': 'the attribution claim. If the floor stopped being CORE the whole '
               'reasoning changes, so the test re-derives it from the real '
               'selector rather than trusting the docstring.',
        'expect': ['test_conditional_core_is_refused_by_the_measurement'
                   '_not_by_taste'],
        'file': _SRC,
        'old': 'CORE = ("ask_user", "get_datetime", "web_search")',
        'new': 'CORE = ("ask_user", "get_datetime", "web_search", "browse")',
    },
]
