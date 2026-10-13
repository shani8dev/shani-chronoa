"""Mutations for the balanced-ladder tool.

The tool's whole value is the numbers it prints before starting a server, so the
mutations are aimed at those: that the scales are the reciprocal of the measured
norms, that they preserve combined magnitude, and that the case subset is the one
the recorded ladder used - because a fresh sample is a different experiment and
the comparison it exists for would be void.
"""

TEST_FILE = 'tests/test_ladder_balanced.py'

_SRC = 'tools/ladder_balanced.py'

MUTATIONS = [
    {
        'label': 'the scales no longer balance - every adapter gets 1.0',
        'why': 'this is the arithemic under test. Without it the run is the '
               'recorded 0/5 again, and the tool would report a balanced '
               'experiment that was not balanced.',
        'expect': ['test_the_scales_are_the_reciprocal_of_the_measured_norms'],
        'file': _SRC,
        'old': '    target = sum(n for _n, n in norms) / len(norms)\n'
               '    return [target / n for _n, n in norms]',
        'new': '    return [1.0 for _n, n in norms]',
    },
    {
        'label': 'the combined magnitude is inflated instead of preserved',
        'why': 'normalising the strongest to 1 makes the whole sum louder, so '
               'any gain cannot be separated from the volume - the confound this '
               'experiment exists to remove would reappear as its own result.',
        'expect': ['test_balancing_preserves_the_combined_magnitude'],
        'file': _SRC,
        'old': '    target = sum(n for _n, n in norms) / len(norms)',
        'new': '    target = max(n for _n, n in norms)',
    },
    {
        'label': 'the case subset is not the one the recorded ladder used',
        'why': 'a fresh sample is a different experiment; the comparison this '
               'run exists for would be void and the 0/5 beside it meaningless.',
        'expect': ['test_the_case_subset_is_the_recorded_one'],
        'file': _SRC,
        'old': 'CASES = "time,weather,volume,set_timer,reminder"',
        'new': 'CASES = ""',
    },
    {
        'label': 'the dry run starts a server anyway',
        'why': 'the numbers are the thing to check before launching a server; a '
               'dry run that launches one cannot be used to check them.',
        'expect': ['test_the_dry_run_launches_nothing'],
        'file': _SRC,
        'old': '    if args.dry_run:\n        print("\\n(dry run: no server started)")\n        return 0',
        'new': '    if args.dry_run:\n        pass',
    },
]
