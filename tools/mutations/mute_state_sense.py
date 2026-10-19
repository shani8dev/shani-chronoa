"""Mutations for the audio sense's mute state, and the count it reports.

**The number this sense reported was always zero.** `wpctl status` carries the
mute state inside the volume annotation - a muted node's line ends
`[vol: 1.00 MUTED]`, measured by muting a live source and reading the status
back - and the sense parsed it for a **`[muted: yes]`** marker instead. wpctl's
binary carries no such string, so the parse could only ever have matched a
device's own description, and it never fired.

The visible effect: the sense's own schema description promises "each with its
volume, whether it is muted, and which is the default", and the count of
muted devices it reported was `0` on every machine, whatever was actually
muted. A count that is always zero is the confident wrong answer - it says
nothing is muted, on a machine where something is.

The rule now lives in `pipewire.status_muted`, so the two modules that read
wpctl's status have one answer rather than two. The mutation that puts the
parse back in this module is here because a module that parses the marker for
itself is how the two drifted apart in the first place.
"""

TEST_FILE = 'tests/test_sense_audio.py'

_AUDIO = 'usr/lib/shani-chronoa/shani_chronoa/senses/audio.py'

MUTATIONS = [
    {
        'label': 'the sense parses the old marker for itself',
        'why': '**the shipped defect, restored verbatim.** `[muted: yes]` is '
               'not a string in the wpctl binary, so this could only ever have '
               'matched a device\'s own description - and it never fired. The '
               'count of muted devices was always zero, whatever was actually '
               'muted.',
        'expect': ['test_a_muted_device_is_counted',
                   'test_the_mute_flag_is_parsed_separately_from_volume'],
        'file': _AUDIO,
        'old': '        muted = pipewire.status_muted(match.group(2))',
        'new': '        muted = "muted: yes" in bare',
    },
    {
        'label': 'the sense reports every device as unmuted',
        'why': 'the other direction of the same defect, and the one a reader '
               'arrives at by "the marker is absent, so it is not muted" - a '
               'reasoning that is right for a node with a volume annotation '
               'and wrong for one without, where the state was never '
               'reported at all. The camera in the fixture is the case.',
        'expect': ['test_a_node_with_no_annotation_is_not_known_to_be_unmuted',
                   'test_a_muted_device_is_counted'],
        'file': _AUDIO,
        'old': '        muted = pipewire.status_muted(match.group(2))',
        'new': '        muted = False if pipewire.status_muted(match.group(2)) is None \\\n'
               '            else pipewire.status_muted(match.group(2))',
    },
    {
        'label': 'the count is the number of devices, not the number muted',
        'why': 'a number that answers a different question is the shape this '
               'repository records most often. The metadata field is named '
               '"muted" and is read as "how many devices are muted"; counting '
               'the devices instead would report 5 on a machine with one '
               'muted microphone, and the number would look like an answer.',
        'expect': ['test_a_muted_device_is_counted'],
        'file': _AUDIO,
        'old': '            "muted": sum(1 for e in sinks + sources if e.get("muted") is True),',
        'new': '            "muted": len(sinks) + len(sources),',
    },
]
