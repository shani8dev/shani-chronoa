"""Mutations for a setting whose default is stated in two places.

**A setting's default lives in the gschema and in the code that reads it.**
`ChronoaConfig.get_bool(key, default)` reads the **schema** whenever the key
is declared in it; the `default` argument is only reached on an install whose
schema predates the key. So a fallback that disagrees with the schema means
two machines read the same setting differently - and that is not a hypothetical
machine, because `ChronoaConfig._new_settings` builds an explicit keyfile
backend rather than inheriting the desktop's.

Three real instances of it, all found on 2026-10-19 and all the same shape:

- `324e597` changed `privacy-mode` from `true` to `false` and moved the
  Python fallback with it - but missed a **second reader of the same key** in
  `gui/surfaces/senses.py`, which still fell back to `True`. The Senses
  panel's footer therefore said "Privacy mode is on, so a sense that reaches
  outside this machine stays refused" on a machine whose privacy mode was off.
- The same commit swept **ten keys** in the schema to `true` and left the
  code's fallbacks at `False`, so each of them read one way on a fresh install
  and the other way on an older one.
- `setup_wizard.py` read `cloud-fallback-enabled` with a `False` fallback
  while `config.py` read the same key with `True` - and the **first scan for
  this missed it**, because the scan collected a *set* of fallbacks per key and
  a set holding the right answer hides the wrong one. The check here is
  per-site for exactly that reason, and one of its mutations is aimed at the
  hole itself.
"""

TEST_FILE = 'tests/test_schema_and_code_defaults_agree.py'

_CUES = 'usr/lib/shani-chronoa/shani_chronoa/cues.py'
_VOICE = 'usr/lib/shani-chronoa/shani_chronoa/settings_window/voice.py'
_SENSES_SURFACE = 'usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/senses.py'
_WIZARD = 'usr/lib/shani-chronoa/shani_chronoa/setup_wizard.py'

MUTATIONS = [
    {
        'label': 'a fallback goes back to False while the schema says True',
        'why': 'the shipped defect, restored verbatim, at one of the ten sites '
               'the commit that turned the key on in the schema left behind. '
               'A fresh install reads the schema and gets `True`; a machine '
               'with an older schema reads the fallback and gets `False`.',
        'expect': ['test_no_fallback_disagrees_with_the_schema'],
        'file': _CUES,
        'old': '    if not config.get_bool("sound-cues-enabled", True):',
        'new': '    if not config.get_bool("sound-cues-enabled", False):',
    },
    {
        'label': 'one of two readers of a key keeps the other answer',
        'why': 'the shape a per-key check cannot see, and the reason the '
               'check here is per-site. `sound-cues-enabled` is read in two '
               'places; breaking one of them leaves a *set* of fallbacks that '
               'still contains the right answer, so a set-based check stays '
               'green. The first version of this check was set-based and the '
               'mutation was applied to it and the suite did not move.',
        'expect': ['test_one_key_is_not_read_with_two_different_fallbacks',
                   'test_no_fallback_disagrees_with_the_schema'],
        'file': _VOICE,
        'old': '                     config.get_bool("sound-cues-enabled", True), lambda a: self._set_bool("sound-cues-enabled", a))',
        'new': '                     config.get_bool("sound-cues-enabled", False), lambda a: self._set_bool("sound-cues-enabled", a))',
    },
    {
        'label': 'the senses panel falls back to privacy mode being on',
        'why': 'the second reader of `privacy-mode` that `324e597` missed. '
               'The panel\'s footer says "Privacy mode is on, so a sense that '
               'reaches outside this machine stays refused even when it is '
               'granted here" - and on a machine whose schema predates the '
               'key, the panel was describing a policy the app was not '
               'enforcing. A footer that names a policy the product does not '
               'apply is the confident wrong answer.',
        'expect': ['test_one_key_is_not_read_with_two_different_fallbacks',
                   'test_no_fallback_disagrees_with_the_schema'],
        'file': _SENSES_SURFACE,
        'old': '        privacy = config is not None and bool(config.get_bool("privacy-mode", False))',
        'new': '        privacy = config is not None and bool(config.get_bool("privacy-mode", True))',
    },
    {
        'label': 'the setup wizard reads the cloud fallback with the other answer',
        'why': 'the instance the first scan for this **missed**, and the one '
               'that shows why the scan had to be redone. `config.py` reads '
               '`cloud-fallback-enabled` with a `True` fallback and the '
               'schema says `true`; the wizard read the same key with '
               '`False`, so a fresh install chose the cloud mode and an older '
               'one chose local. The scan that missed it collected a *set* of '
               'fallbacks per key, and a set holding the right answer hides '
               'the wrong one.',
        'expect': ['test_one_key_is_not_read_with_two_different_fallbacks',
                   'test_no_fallback_disagrees_with_the_schema'],
        'file': _WIZARD,
        'old': '        "cloud-fallback-enabled", True) else "local")',
        'new': '        "cloud-fallback-enabled", False) else "local")',
    },
]
