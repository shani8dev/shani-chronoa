"""Mutations for the voice-cloning consent gate and the cascade link to it.

**The defect this set exists to prevent shipped, in two halves, and neither
half looked like a defect.** Measured on a machine with nothing configured at
all: `tts.engine()` answered `'voice-clone'`, so the cascade was unreachable
below its first link and every reply would have tried to speak through an
engine that had no voice to speak with. Nothing refused and nothing said so -
a short-circuit that answers `voice-clone` *looks like an answer*, which is why
this is a shape worth pinning rather than a bug worth remembering.

The two halves are separate and each alone is a fix that a reviewer would
accept:

1. `_engine`'s `clone_reason` default was `""`, and `""` is what the parameter
   means when cloning *is* permitted - so a caller that said nothing about
   cloning did not get "not chosen", it got "chosen". Its own docstring said it
   "defaults to a non-empty string" and the signature said the opposite; the
   docstring was the specification.
2. `_clone_reason()` returned `""` for the ordinary case of *nothing chosen*,
   which is every install that has never touched this feature.

Both are here as mutations 1 and 2, restored verbatim, because **an
equivalent-looking fix is what a permission bug looks like in review.**

The rest are the three directions each guard can fail in, which are not
symmetrical and must not be tested in only one:

- **opening** (`_switch_allows` always allows; `register` stops refusing an
  unreviewable record) - the direction that would let a voice be built;
- **holding** (`check`'s verdict ignored by the cascade) - the direction that
  would select a voice the gate refused;
- **telling the truth** (`_registry_problem` not consulted, `revoke` claiming a
  success it did not perform, the digest comparison dropped) - a gate that
  refuses correctly and *misnames the remedy* is the failure this repository
  records in four costumes, and it is invisible to every other assertion here.

The two `chmod` mutations exist because the file mode is asserted by
`stat`, so deleting the `chmod` is measurable - and because `mkdir(mode=...)`
alone would not be: the directory is created by the first write and left alone
by the second, which is why the second write is its own test.
"""

TEST_FILE = 'tests/test_voice_clone.py'

_TTS = 'usr/lib/shani-chronoa/shani_chronoa/tts.py'
_CLONE = 'usr/lib/shani-chronoa/shani_chronoa/voice_clone.py'
_VOICE = 'usr/lib/shani-chronoa/shani_chronoa/settings_window/voice.py'

MUTATIONS = [
    {
        'label': 'the cascade default is empty again, so a caller that says '
                 'nothing about cloning selects it',
        'why': '**the shipped defect, half one, verbatim.** The docstring on '
               'this very parameter says it "defaults to a non-empty string so '
               'that any caller which has not been updated to think about '
               'cloning gets \'not chosen\' rather than accidentally choosing '
               'it", and the signature said the opposite. Three existing callers '
               'pass only `kokoro_reason`. A parameter whose absence enables a '
               'feature is the wrong default in a permission.',
        'expect': [
            'test_a_caller_that_says_nothing_about_cloning_is_told_no',
            'test_each_half_is_load_bearing',
        ],
        'file': _TTS,
        'old': '                clone_reason: str = "not consulted") -> Optional[str]:',
        'new': '                clone_reason: str = "") -> Optional[str]:',
    },
    {
        'label': 'an empty voice choice is an absence again, so nothing is in '
                 'the way',
        'why': '**the shipped defect, half two, verbatim.** `_clone_reason` '
               'reads `""` as *nothing prevents cloning*, so returning `""` for '
               '"nobody has chosen a voice" - the state of every install that '
               'has never touched this feature - was the most enabling answer '
               'available. `engine()` passes this explicitly, so mutation 1 '
               'alone leaves `engine()` broken, which is why both halves are '
               'in the set.',
        'expect': [
            'test_an_empty_choice_is_a_refusal_not_an_absence',
            'test_nothing_configured_does_not_select_cloning',
            'test_each_half_is_load_bearing',
        ],
        'file': _TTS,
        'old': (
            '        if not self.cloned_voice:\n'
            '            return ("no recorded voice is chosen, so the ordinary voices are "\n'
            '                    "used instead; pick one under \'Speak with which recorded "\n'
            '                    "voice\' in Settings")'
        ),
        'new': '        if not self.cloned_voice:\n            return ""',
    },
    {
        'label': "the cascade ignores the gate's verdict",
        'why': 'The forward direction, and the one mutation 2 could not reach: '
               'the gate refuses correctly and the caller discards the answer. '
               'A refusal that returns "" here is what the cascade reads as '
               '"nothing is in the way", so this is how the shipped defect '
               'would return through the same door from the other side. '
               '`test_nothing_configured_does_not_select_cloning` cannot bite - '
               'that path never reaches `check()` because nothing is chosen - '
               'which is exactly why the chosen-but-unregistered case is a '
               'separate test.',
        'expect': [
            'test_a_chosen_voice_that_is_not_registered_is_refused',
            'test_the_refusal_carries_the_gate_s_own_sentence',
        ],
        'file': _TTS,
        'old': (
            '        if not allowed:\n'
            '            return why'
        ),
        'new': '        if not allowed:\n            return ""',
    },
    {
        'label': 'the switch is always on',
        'why': 'The opening direction. `_switch_allows` fails closed on an '
               'unreadable preference, which is the deliberate opposite of '
               '`_piper_enabled`: that one is a preference whose absence must '
               'not change every reply, and this one is a permission whose '
               'absence must stop a voice being built from a person who did '
               'not agree. Both gates are required and neither is sufficient, '
               'so this mutation also removes the registry gate\'s ability to '
               'be the only thing standing between a recording and a voice.',
        'expect': [
            'test_a_registered_voice_still_refuses_with_the_switch_off',
            'test_registering_does_not_itself_enable_it',
            'test_opening_the_gate_needs_both_halves_not_one',
            'test_an_unreadable_setting_is_a_refusal',
        ],
        'file': _CLONE,
        'old': '        return bool(config.get_bool(CONSENT_KEY, False)), ""',
        'new': '        return True, ""',
    },
    {
        'label': 'the digest comparison is dropped, so the agreement is for a '
                 'path rather than for audio',
        'why': '**The load-bearing part of the whole mechanism.** `register` '
               'stores the clip\'s SHA-256 precisely so that replacing the file '
               'stops applying the agreement. Without this comparison a consent '
               'record is a path, and every file that ever lands at that path is '
               'covered by it - which is the substitution the gate exists to '
               'catch. The module docstring says the digest, not the path, is '
               'what makes the gate real, so this is the mutation that sentence '
               'was written for.',
        'expect': [
            'test_replacing_the_recording_stops_the_agreement_applying',
            'test_the_substitution_refusal_differs_from_a_missing_one',
        ],
        'file': _CLONE,
        'old': '    if current != entry.digest:',
        'new': '    if False:',
    },
    {
        'label': 'a corrupt registry is reported as an empty one',
        'why': 'The honesty direction, and the half that is invisible to every '
               'other assertion in the file: the gate still refuses, so a test '
               'that only checks "it said no" is green. What changes is the '
               'remedy - an absent registry wants a registration written, and '
               'a corrupt one does not, so the corrupt case used to tell a '
               'person to record an agreement that would then be written into a '
               'file nothing reads back. Measured before `_registry_problem()` '
               'existed: absent, unparseable, holding a list, and genuinely '
               'holding somebody else\'s voice all produced one sentence.',
        'expect': [
            'test_an_unreadable_registry_is_not_an_absent_one',
            'test_an_unreadable_registry_names_no_voice',
        ],
        'file': _CLONE,
        'old': '        unreadable = _registry_problem()',
        'new': '        unreadable = ""',
    },
    {
        'label': 'the consent registry is left world-readable',
        'why': 'The one file here whose contents are about a person rather '
               'than about the machine: a list of who agreed to be built from '
               'a recording of themselves. Asserted through `stat` on a real '
               'written file rather than by reading the source, because a '
               'source-level assertion would be satisfied by a comment.',
        'expect': [
            'test_the_file_and_its_directory_are_not_world_readable',
            'test_writing_a_second_voice_does_not_relax_the_modes',
        ],
        'file': _CLONE,
        'old': '        tmp.chmod(0o600)',
        'new': '        pass',
    },
    {
        'label': 'the directory holding the consent registry is left world-readable',
        'why': 'The other half of the same pair, and separable because the '
               'directory is created by the *first* write and left alone by the '
               'second - which is why there is a test for the second write. '
               'This is also the recorded cost of `mkdir(mode=0o700)` alone: '
               'with `parents=True` the mode applies to the leaf only, '
               'intermediate directories come out at the umask default, and on '
               'a machine whose umask is 002 the directory is world-readable '
               'however carefully the leaf was asked.',
        'expect': [
            'test_the_file_and_its_directory_are_not_world_readable',
            'test_writing_a_second_voice_does_not_relax_the_modes',
        ],
        'file': _CLONE,
        'old': '    path.parent.chmod(0o700)',
        'new': '    pass',
    },
    {
        'label': 'revoking a voice reports success without withdrawing it',
        'why': 'Withdrawal is the half that matters after the fact - the '
               'panel\'s own docstring says showing what has been asserted and '
               'letting it be withdrawn is the job it can still do. A revoke '
               'that returns True and leaves the record in place is the shape '
               'this repository records for `stop_service()` returning \'\' for '
               'an already-inactive unit: a caller cannot tell "I stopped it" '
               'from "there was nothing to stop". Here it is worse, because the '
               'agreement is still applying.',
        'expect': ['test_a_revoked_voice_stops'],
        'file': _CLONE,
        'old': '    del raw[voice]\n    _save(raw)',
        'new': '    pass',
    },
    {
        'label': 'registering a voice that names nobody is allowed',
        'why': 'The opening direction on the record itself. A consent record '
               'that cannot say who agreed, or on what basis, is a record a '
               'person cannot review and therefore cannot withdraw '
               'meaningfully - which is the module\'s own stated reason for '
               'requiring both. The over-fix direction matters here too: this is '
               'a gate that validates its own input, and deleting a validation '
               'is always green on the tests that only exercise valid input.',
        'expect': [
            'test_an_unreviewable_record_is_refused',
            'test_a_registration_that_refused_wrote_nothing',
        ],
        'file': _CLONE,
        'old': (
            '    if not speaker:\n'
            '        raise ValueError("say who the voice belongs to")'
        ),
        'new': '    if False:\n        raise ValueError("say who the voice belongs to")',
    },
    # --- the Settings rows -------------------------------------------------
    #
    # The rest of this set is about a gate that *refuses*. These are about the
    # control that reaches it, and they exist because `_clone_rows` was the one
    # half of this feature nothing ever executed: written, never run, and it
    # shipped calling two `Adw.ComboRow` methods this build does not have.
    #
    # Both shipped defects are here **verbatim**, and the second is the
    # instructive one - it would have survived the fix of the first, because
    # with `append` restored the window never builds and the write path is never
    # reached at all.

    {
        'label': 'the picker is built with append(label, value) again, which '
                 'does not exist on this libadwaita',
        'why': '**the shipped defect, first half, verbatim.** `Adw.ComboRow` '
               'has no `append` on libadwaita 1.9.1: measured, and '
               '`Adw.ComboRowItem` is not in the introspection at all, so the '
               'per-item value slot the API appears to have is not reachable. '
               'The row is built while `SettingsWindow.__init__` constructs the '
               'voice group, so this took **the entire Settings window** down '
               'rather than one control - which is why every test behind the '
               'shared fixture fails and not just the one about the picker.',
        'expect': [
            'test_the_picker_is_on_the_page',
            'test_each_entry_writes_the_voice_it_names',
        ],
        'file': _VOICE,
        'old': '        picker.set_model(labels)\n',
        'new': ('        picker.append("x", "x")\n'
                '        picker.set_model(labels)\n'),
    },
    {
        'label': 'the chosen value is read with get_selected_item().get_value() '
                 'again, which does not exist either',
        'why': '**the shipped defect, second half, verbatim, and the one a '
               'review would have missed.** `get_selected_item()` does exist - '
               'it returns a plain `Gtk.StringObject` - but `get_value()` on it '
               'does not. So this is a perfectly well-formed call to a method '
               'that is not there, inside a signal handler, where PyGObject '
               '**swallows the exception**: the handler raises, nothing is '
               'written, and the picker looks untouched.',
        'expect': [
            # Index 0 is deliberately NOT named, and the reason is the point:
            # its expected value is `""`, which is exactly what an exception
            # inside the handler leaves behind - so it passes with this defect
            # present. The mutation below is what gives index 0 a witness.
            'test_each_entry_writes_the_voice_it_names[1-Alice]',
            'test_each_entry_writes_the_voice_it_names[2-Bob]',
        ],
        'file': _VOICE,
        'old': ('            if 0 <= index < len(values):\n'
                '                self._set_clone_voice(values[index])\n'),
        'new': ('            item = row.get_selected_item()\n'
                '            self._set_clone_voice(item.get_value() if item else "")\n'),
    },
    {
        'label': 'the entry a person left the choice on is not restored',
        'why': 'The picker is built from the registry but opened from the '
               'setting, and those are two different lists - so opening the '
               'window contradicts what the switch says without changing '
               'anything. A person who chose a voice sees the ordinary voices '
               'highlighted, which is the row that looks like the gate is off.',
        'expect': [
            'test_the_entry_a_person_left_it_on_is_the_entry_shown',
        ],
        'file': _VOICE,
        'old': ('            picker.set_selected(\n'
                '                values.index(current) if current in values else 0)\n'),
        'new': '            picker.set_selected(0)\n',
    },
    {
        'label': 'the picker stays sensitive with nothing registered to pick',
        'why': 'With no registered voice the only entry is "The ordinary '
               'voices", so a sensitive control offers a choice it cannot '
               'express. **Asserted as `get_sensitive` rather than inferred '
               'from the count**, because a one-entry picker and an '
               'insensitive picker look identical from the labels alone.',
        'expect': [
            'test_nothing_registered_leaves_nothing_to_pick',
        ],
        'file': _VOICE,
        'old': '        picker.set_sensitive(bool(entries))\n',
        'new': '        picker.set_sensitive(True)\n',
    },
    {
        'label': 'the ordinary-voices entry writes a voice name instead of the '
                 'empty setting',
        'why': 'The one place `""` and a name meet is the pair of lists - the '
               'model holds labels, `values` holds the setting each one writes, '
               'and index 0 is the only entry whose value is not its own label. '
               'Naming a voice there is the failure this whole arrangement '
               'exists to prevent: the gate would go on answering for a voice '
               'nobody picked. **This mutation is what gives index 0 a '
               'witness at all** - see the note on the mutation above.',
        'expect': [
            'test_each_entry_writes_the_voice_it_names[0-]',
        ],
        'file': _VOICE,
        'old': '        values = [""] + [name for name, _consent in entries]\n',
        'new': '        values = ["nobody"] + [name for name, _consent in entries]\n',
    },
]
