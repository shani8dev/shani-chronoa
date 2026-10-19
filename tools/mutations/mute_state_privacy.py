"""Mutations for the privacy skill's mute state and its post-condition.

**The post-condition checked a place the effect was never written.** `_mute_mic`
runs `wpctl set-mute`, so the effect is the audio server's mute state - not a
GSettings key. The check read `privacy-mic-muted`, which **nothing writes**
and which is not even declared in the schema, so `get_bool` took its Python
default on every call.

The consequence was that the check could not tell its two cases apart:

- **muting was reported FAILED every single time**, even when the microphone
  really was muted;
- **unmuting was reported VERIFIED every single time**, without having checked
  anything at all.

Both are the confident wrong answer, and the second is the worse of the two: a
verified verdict is what the learning layer trains on, so a check that
verifies everything is a check that teaches the model that everything worked.

And the skill's `status` action always reported the microphone as "unknown",
because the parse it used looked for a `[muted: yes]` marker that wpctl does
not write - the state is in the volume annotation, as `[vol: 1.00 MUTED]`.
"""

TEST_FILE = 'tests/test_skill_privacy.py'

_PRIVACY = 'usr/lib/shani-chronoa/shani_chronoa/skills/privacy.py'

MUTATIONS = [
    {
        'label': "the post-condition checks a key nothing writes",
        'why': '**the shipped defect, restored verbatim.** '
               '`privacy-mic-muted` is written by nothing and is not in the '
               'schema, so the check always read `False`. Muting was FAILED '
               'every time even when the microphone was muted, and unmuting '
               'was VERIFIED every time without having checked anything.',
        'expect': ['test_muting_is_verified_when_the_microphone_is_muted',
                   'test_unmuting_is_failed_when_the_microphone_is_muted'],
        'file': _PRIVACY,
        'old': '    states = []\n'
               '    for target in targets:\n'
               '        muted = target.get("muted")\n'
               '        if muted is None:\n'
               '            continue\n'
               '        states.append((str(target.get("name") or target["id"]), muted))',
        'new': '    states = []\n'
               '    for target in targets:\n'
               '        muted = bool(ChronoaConfig().get_bool("privacy-mic-muted", False))\n'
               '        states.append((str(target.get("name") or target["id"]), muted))',
    },
    {
        'label': 'the post-condition accepts a source whose state is unknown',
        'why': 'the third state, which is the one this class of check most '
               'often loses. A node wpctl will not report a mute state for is '
               '**unknown**, and "unknown" is not "not muted" - so folding it '
               'into the unmuted answer lets a machine with a source that '
               'could not be read pass a mute it never performed.',
        'expect': ['test_muting_is_verified_when_the_microphone_is_muted',
                   'test_a_source_with_no_reported_state_is_unverified_not_failed'],
        'file': _PRIVACY,
        'old': '    for target in targets:\n'
               '        muted = target.get("muted")\n'
               '        if muted is None:\n'
               '            continue\n'
               '        states.append((str(target.get("name") or target["id"]), muted))',
        'new': '    for target in targets:\n'
               '        muted = bool(target.get("muted"))\n'
               '        states.append((str(target.get("name") or target["id"]), muted))',
    },
    {
        'label': 'the skill parses the old marker for itself',
        'why': '**the shipped defect, restored verbatim.** `[muted: yes]` is '
               'not a string in the wpctl binary, so the parse could only ever '
               'have matched a device\'s own description - and it never fired. '
               'The `status` action reported the microphone as "unknown" on '
               'every machine, whatever it was doing.',
        'expect': ['test_muting_is_verified_when_the_microphone_is_muted'],
        'file': _PRIVACY,
        'old': '            "muted": pipewire.status_muted(match.group(2)),',
        'new': '            "muted": "muted: yes" in bare,',
    },
    {
        'label': 'the evidence names the settings rather than the audio server',
        'why': 'a claim about where the state came from, and the old one said '
               '"settings" - which is what made the defect look like a '
               'settings bug rather than a reader bug. The effect is the audio '
               'server\'s, and the evidence has to say so.',
        'expect': ['test_the_evidence_names_the_audio_server'],
        'file': _PRIVACY,
        'old': '        return (True, f"microphone is {\'muted\' if expected else \'unmuted\'} on "\n'
               '                      f"{names}, read back from the audio server")',
        'new': '        return (True, f"microphone is {\'muted\' if expected else \'unmuted\'} on "\n'
               '                      f"{names}, read back from settings")',
    },
]
