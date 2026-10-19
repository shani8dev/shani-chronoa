"""Mutations for the two readers that answer "is this node muted".

**The failure this set exists to prevent is a confident wrong answer about a
privacy control.** Three modules in this tree had three different answers to
"is this node muted", and only one of them was right:

- `senses/audio.py` and `skills/privacy.py` parsed `wpctl status` output for
  a **`[muted: yes]`** marker. wpctl's binary carries no such string, so the
  parse could only ever have matched a device's own description, and it never
  fired. The audio sense's count of muted devices was therefore **always zero**,
  whatever was actually muted, while its own schema description promised "each
  with its volume, whether it is muted, and which is the default".
- `skills/set_mic_mute.py` ran `wpctl get-volume`, and then returned `None` on
  every path - it read the answer and threw it away. So "is the microphone
  muted" was always UNKNOWN.
- `skills/privacy.py`'s post-condition read a GSettings key,
  `privacy-mic-muted`, that **nothing writes** and that is not in the schema.
  The effect was not a GSettings key at all: `_mute_mic` runs `wpctl set-mute`.
  So the check could not tell its two cases apart - muting was reported as
  FAILED every single time even when the microphone really was muted, and
  unmuting was reported as VERIFIED every single time without having checked
  anything.

**What was measured**, by muting a live source on this machine and reading both
commands back (the source was unmuted again immediately afterwards, and the
restoration was verified):

    wpctl status        ->  │  61. ... Stereo Microphone [vol: 1.00 MUTED]
    wpctl get-volume 61 ->  Volume: 1.00 [MUTED]

**The two commands use different markers.** `status` puts `MUTED` *inside* the
volume bracket; `get-volume` puts `[MUTED]` *after* the level.

**And the two markers are nested, which decides where a conflation can be
seen.** `[MUTED]` *contains* `MUTED`, so a reader of get-volume output cannot
tell the two apart at all - every rule that finds one finds the other. The
conflation is therefore only observable in the other direction, on a **status**
line: a status reader that looks for the get-volume marker finds nothing in
`[vol: 1.00 MUTED]`, and reports a muted microphone as unmuted. The mutations
below are aimed accordingly - the two that would be equivalent are not here,
and the reason is in the file.

The set is shaped around the four ways a reader can be wrong: it can look for
a marker that does not exist, it can read the *other* command's marker, it can
round an unanswered read down to a clean "no", and it can read a place the
state was never written.
"""

TEST_FILE = 'tests/test_pipewire_mute_state.py'

_PW = 'usr/lib/shani-chronoa/shani_chronoa/pipewire.py'

MUTATIONS = [
    {
        'label': 'the status reader looks for the marker wpctl never writes',
        'why': '**the shipped defect, restored verbatim.** `[muted: yes]` is '
               'not a string in the wpctl binary, so the parse could only ever '
               'have matched a device\'s own description - and it never fired. '
               'The audio sense\'s count of muted devices was therefore always '
               'zero, whatever was actually muted. A count of muted devices '
               'that is always zero is the confident wrong answer: it says '
               'nothing is muted, on a machine where something is.',
        'expect': ['test_a_muted_node_is_muted',
                   'test_an_unmuted_node_is_not_muted'],
        'file': _PW,
        'old': '    return annotation.rstrip().endswith("MUTED")',
        'new': '    return "muted: yes" in annotation',
    },
    {
        'label': 'the status reader looks for the get-volume marker instead',
        'why': 'the conflation the two commands invite, and the only direction '
               'in which it is visible. The markers are nested - `[MUTED]` '
               'contains `MUTED` - so a reader of get-volume output cannot '
               'tell them apart; a reader of a *status* line can, and looking '
               'for the wrong one finds nothing in `[vol: 1.00 MUTED]` and '
               'reports a muted microphone as unmuted. The first version of '
               'this set aimed the same mutation at the get-volume reader, '
               'where it is equivalent by construction, and it survived.',
        'expect': ['test_a_muted_node_is_muted',
                   'test_the_two_commands_do_not_share_a_marker'],
        'file': _PW,
        'old': '    return annotation.rstrip().endswith("MUTED")',
        'new': '    return "[MUTED]" in entry',
    },
    {
        'label': 'a command that printed nothing reads as unmuted',
        'why': 'the absence-shaped wrong answer, and the one a reader is most '
               'likely to introduce. The marker is absent from every unmuted '
               'node, so an empty output and an unmuted node are the same '
               'absence - and only one of them is a fact. A post-condition that '
               'rounds "wpctl said nothing" down to "not muted" reports a '
               'successful unmute on a machine where the microphone is still '
               'muted.',
        'expect': ['test_a_command_that_printed_nothing_is_unknown'],
        'file': _PW,
        'old': '    if "Volume:" not in text:\n'
               '        # A command that exited 0 and printed nothing has not answered. Reading\n'
               '        # that as "not muted" would be the absence-shaped wrong answer: the\n'
               '        # marker is absent from every unmuted node, so an empty output and an\n'
               '        # unmuted node are the same absence and only one of them is a fact.\n'
               '        return MuteState(None, text)\n',
        'new': '',
    },
    {
        'label': 'a failed read is reported as unmuted',
        'why': 'the same shape, one step earlier: `wpctl get-volume` failing is '
               'a read that could not be made, and UNKNOWN is the state the '
               'code can represent for that. Reporting it as a clean "no" '
               'turns "I could not determine" into a fact.',
        'expect': ['test_a_failed_read_is_unknown'],
        'file': _PW,
        'old': '    if proc.returncode != 0:\n'
               '        return MuteState(None, (proc.stderr or "").strip())',
        'new': '    if proc.returncode != 0:\n'
               '        return MuteState(False, (proc.stderr or "").strip())',
    },
    {
        'label': 'a node with no annotation at all is read as unmuted',
        'why': 'the third state, which is the one this class of reader most '
               'often loses, and the real case on this machine. The '
               'integrated camera is one: `wpctl` refuses to mute it ("Node 79 '
               'does not support mute"), refuses to report a volume for it, '
               'and its status line carries no annotation at all. Reporting '
               'that as "unmuted" says the machine knows a thing it was never '
               'told.',
        'expect': ['test_a_node_with_no_annotation_is_unknown_not_unmuted'],
        'file': _PW,
        'old': '    start = entry.rfind("[")\n'
               '    if start < 0 or not entry.endswith("]"):\n'
               '        return None',
        'new': '    start = entry.rfind("[")\n'
               '    if start < 0 or not entry.endswith("]"):\n'
               '        return False',
    },
    {
        'label': 'a bracket that is not a volume annotation is read as unmuted',
        'why': 'the same third state, one step along: a device whose own '
               'description ends in a bracket - "Speaker [aux]" - has no '
               'volume annotation at all, and its mute state was never '
               'reported. This is a different case from the camera\'s, which '
               'has no bracket either, so it has its own witness: the first '
               'version of this mutation named the camera\'s test and it did '
               'not fire, because the camera returns before this branch is '
               'reached at all. The runner reported it as "failed for another '
               'reason", which is the mis-named witness this repository keeps '
               'recording - the name was corrected rather than a new test '
               'written.',
        'expect': ['test_a_name_that_merely_mentions_muted_is_not_a_mute'],
        'file': _PW,
        'old': '    if not annotation.startswith("vol:"):\n'
               '        return None',
        'new': '    if not annotation.startswith("vol:"):\n'
               '        return False',
    },
]
