"""Mutations for the sense defaults, which live in the schema and in Python.

**A sense's default is stated twice and the two have to agree.** The gschema
carries the `<default>` a fresh install reads; `config._SENSE_DEFAULT_ENABLED`
is the Python mirror a machine with an older schema falls back to.

Three senses' defaults were swept in the schema on 2026-10-19 by a commit
about `privacy-mode` and the cloud fallback, and the Python mirror was not
moved with them. The three are `containers`, `listeners` and `stale` - and
moving them off in the first place was a **measured** decision (`ef3e952`,
2026-09-30), not a default: each of them reports *process identity* rather
than the machine's own state. `listeners` resolves each listening socket to
an owner process name and PID, `stale` reports a per-PID executable path for
every process in the account, and `containers` reports container names and
images. `git` was already off for exactly this class of reason, and the commit
made the three agree with it.

The commit that swept them back on left `test_the_schema_agrees_with_the_
python_default` failing, which is the test that caught it. The gschema is XML
and this harness mutates Python, so **the schema side is held by that test
reading the file and comparing it** - and the mutations here are on the Python
side, which is the half a machine with an older schema actually reads.
"""

TEST_FILE = 'tests/test_senses_new_batch.py'

_CONFIG = 'usr/lib/shani-chronoa/shani_chronoa/config.py'

MUTATIONS = [
    {
        'label': 'a personal sense is put back in the default-on set',
        'why': '**the shipped defect, restored verbatim, on the Python side.** '
               '`containers` was moved out of `_SENSE_DEFAULT_ENABLED` on '
               '2026-09-30 because it reports container names and images - the '
               'user\'s work, not the machine\'s state - and it declares '
               'itself `SENSITIVITY_PERSONAL`. Putting it back makes a fresh '
               'install and an older one disagree, which is what the sweep '
               'that undid the decision left behind. The witness named first '
               'is the one that compares the table with the schema across the '
               'whole registry; the second is the one that says why these '
               'four are off, and it is the test that actually fired when '
               'this mutation was first applied - the first version of this '
               'entry named the other one, and the runner reported it as '
               '"failed for another reason", which is the mis-named witness '
               'this repository keeps recording.',
        'expect': ['test_every_registered_sense_agrees_with_the_schema',
                   'test_the_four_software_facts_are_off_and_the_hardware_facts_are_on'],
        'file': _CONFIG,
        'old': '    "ups",\n',
        'new': '    "ups",\n    "containers",\n',
    },
    {
        'label': 'a default-on sense is taken out of the set',
        'why': 'the other direction, and the one that silently switches a '
               'feature off. `ups` reads the machine\'s utility power and is '
               'on by default with the rest of the machine\'s own state; a '
               'machine whose schema predates the key would read it as off '
               'while a fresh install reads it as on, so the same machine '
               'answers two ways. Nothing caught this until the check was '
               'widened from the eight senses one file added to the whole '
               'registry - a per-file list of names is a list that rots the '
               'moment a sense is added.',
        'expect': ['test_every_registered_sense_agrees_with_the_schema'],
        'file': _CONFIG,
        'old': '    "ups",\n',
        'new': '',
    },
]
