"""Mutations for the volume skill's post-condition.

**Two things were wrong with it, and both are the shape this repository records
most often.**

1. **It reported a read it could not make as a failed write.** When
   `wpctl get-volume` failed, the post-condition returned
   `(False, "could not read <device> back")` - and `verification.verify` maps a
   false callable verdict to **FAILED**. So a volume change that worked was
   reported as a volume change that did not, whenever the *check* could not be
   made. `verification.verify`'s own docstring draws exactly this line: *"A
   callable post-condition may return None: 'this call changed nothing I can
   check' ... That is UNVERIFIED, never FAILED - reporting a read as a failed
   write is a confident wrong answer."* The argv form of the same contract
   was fixed earlier; the callable form here was not.

2. **It kept its own copy of the `[MUTED]` rule.** `pipewire.mute_state` now
   owns it, so the three modules that read a mute state back have one answer
   rather than three - one correct, one that threw the answer away, and one
   that looked where the state was never written.

**Why the second is not a mutation here.** The copy it replaced was
**correct**, so restoring it changes nothing - and the two markers are
nested (`[MUTED]` contains `MUTED`), so a get-volume reader cannot tell them
apart on get-volume output either. A mutation that restores a correct copy is
equivalent by construction. The mutation below is therefore aimed at the
behaviour the sharing now makes possible: **reading the state and then
ignoring it.**
"""

TEST_FILE = 'tests/test_actuator_post_conditions.py'

_VOLUME = 'usr/lib/shani-chronoa/shani_chronoa/skills/volume.py'

MUTATIONS = [
    {
        'label': 'a read that could not be made is reported as a failed write',
        'why': '**the shipped defect, restored verbatim.** `wpctl get-volume` '
               'failing says the *read* could not be made; the set may well '
               'have worked. Reporting it as FAILED tells the caller the '
               'volume did not change, which is a claim nobody made - and a '
               'failed verdict is what the learning layer trains on.',
        'expect': ['test_a_volume_read_that_cannot_be_made_is_unverified'],
        'file': _VOLUME,
        'old': '    if shown.returncode != 0:\n'
               '        # **None, not a verdict.** The read could not be made, and a check that\n'
               '        # could not answer is UNVERIFIED - `verification.verify` says so in its\n'
               '        # own docstring: "reporting a read as a failed write is a confident\n'
               '        # wrong answer". The set may well have worked; wpctl simply could not\n'
               '        # be asked about it afterwards. The same line applies to the two\n'
               '        # sibling modules that read a mute state back.\n'
               '        return None',
        'new': '    if shown.returncode != 0:\n'
               '        return False, f"could not read {device} back"',
    },
    {
        'label': 'the state is read back and then ignored',
        'why': 'the defect the sharing now makes possible, and the one a '
               'post-condition that only checks "could I read it?" would let '
               'through. The read is the whole point: a mute that did not take '
               'has to be reported as a failure, or the caller believes a '
               'microphone is muted when it is not. The first version of this '
               'mutation restored a second copy of the `[MUTED]` rule, which '
               'is equivalent by construction - the copy it restored was '
               'correct - and it survived.',
        'expect': ['test_volume_level_and_mute'],
        'file': _VOLUME,
        'old': '        return state.muted == want, f"read back: {state.text}"',
        'new': '        return state.muted is not None, f"read back: {state.text}"',
    },
]
