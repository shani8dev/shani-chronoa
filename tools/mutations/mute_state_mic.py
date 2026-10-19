"""Mutations for the microphone skill's mute state.

**It read the answer and threw it away.** `_is_muted` ran `wpctl get-volume`
and then returned `None` on every path, so "is the microphone muted" was
always UNKNOWN and a mute was always reported as an unknown outcome.

The docstring said why, and the reason was the mistake: *"wpctl has no
`get-mute` ... so mute can be set but not read back through it."* The first
part is true and the conclusion is false. `wpctl get-volume <id>` prints
`Volume: 1.00 [MUTED]` when the node is muted - measured on this machine by
muting a live source and reading it back, the source being unmuted again
immediately afterwards. `volume.py`'s own post-condition had read it that way
all along, which is what makes the belief a belief rather than a measurement.

UNKNOWN is still a state this code can represent - it is what a *failed* read
reports - and the mutations below include the two ways of losing it: throwing
the answer away, and blaming the tool for a read this skill could not make.
"""

TEST_FILE = 'tests/test_microphone_source_parsing.py'

_MIC = 'usr/lib/shani-chronoa/shani_chronoa/skills/set_mic_mute.py'

MUTATIONS = [
    {
        'label': 'the mic skill reads the state and throws it away',
        'why': '**the shipped defect, restored verbatim.** `_is_muted` ran '
               '`wpctl get-volume` and then returned `None` on every path, so '
               '"is the microphone muted" was always UNKNOWN and a mute was '
               'always reported as an unknown outcome. The docstring said '
               'wpctl cannot report the state - a claim `volume.py` had been '
               'contradicting all along.',
        'expect': ['test_status_reports_the_state_it_read',
                   'test_a_mute_is_confirmed_by_reading_it_back',
                   'test_the_post_condition_verifies_the_mute'],
        'file': _MIC,
        'old': '    return mute_state(ident).muted',
        'new': '    mute_state(ident)\n'
               '    return None',
    },
    {
        'label': 'the mic skill calls a command that does not exist',
        'why': 'the first version of this skill called `wpctl get-mute <id>`, '
               'which is not a command, so every status call answered '
               'UNKNOWN. The command list is fixed - status, get-volume, '
               'inspect, set-default, set-volume, set-mute, set-profile, '
               'clear-default - and `get-mute` is not on it.',
        'expect': ['test_it_does_not_call_a_command_that_does_not_exist'],
        'file': _MIC,
        'old': '    return mute_state(ident).muted',
        'new': '    return run_wpctl("get-mute", ident).returncode == 0',
    },
    {
        'label': 'the answer blames wpctl for a read the skill could not make',
        'why': 'the sentence that carried the false belief: "wpctl can set a '
               'microphone\'s mute state but cannot report it". It is false - '
               '`get-volume` reports it - and a reader who believes it would '
               'go looking for a missing capability rather than a broken read, '
               'and would not know that the skill could be fixed.',
        'expect': ['test_it_does_not_claim_wpctl_cannot_report_it'],
        'file': _MIC,
        'old': '                    f"Mute state: UNKNOWN - wpctl could not read this "\n'
               '                    f"microphone\'s volume back, so its mute state could not be "\n'
               '                    f"seen from here.")',
        'new': '                    f"Mute state: UNKNOWN - wpctl can set a microphone\'s mute "\n'
               '                    f"state but cannot report it, so this is not knowable from "\n'
               '                    f"the command line on this system.")',
    },
]
