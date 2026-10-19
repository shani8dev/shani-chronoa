"""Mutations for the reference recording - the one refusal `check()` cannot make.

`voice_clone.clip_problem(path)` measures whether a *reference clip* is
usable - there, empty, far too big - and its own docstring says it "is what
the voice surface and `speak`'s own refusal print". **Neither asked it**:
both asked `clone_problem()`, which answers a different question - whether
the *engine* can run on this machine at all.

So the case the engine's answer cannot cover was covered by nobody:

- **too big.** A recording of an hour is a hundred megabytes, its digest
  still matches the registration, so `check()` allows it - and the gate
  opened on a file the synthesis would choke on, with a refusal that named
  the engine instead of the recording.

The other two cases - the recording is gone, or it has been replaced - were
already covered, by `check()`'s digest, which refuses them first and says
which of the two happened. **This is not a duplicate refusal: it is the one
refusal `check()` cannot make.**

And the voice surface's row named the file without saying whether the file
was there, so a recording that had been deleted read like an agreement that
was fine.
"""

TEST_FILE = 'tests/test_the_voice_recording_is_checked.py'

_TTS = 'usr/lib/shani-chronoa/shani_chronoa/tts.py'
_VOICE = 'usr/lib/shani-chronoa/shani_chronoa/voice_clone.py'
_PAGE = 'usr/lib/shani-chronoa/shani_chronoa/settings_window/voice.py'

MUTATIONS = [
    {
        'label': "speak's refusal does not ask about the recording",
        'why': 'the shipped defect, verbatim. The refusal names the *engine* '
               'instead of the file, so a recording far too big - which the '
               'digest allows, because the digest matches - is accepted with '
               'no refusal at all, and an empty refusal is what the cascade '
               'reads as "nothing is in the way".',
        'expect': ['test_a_recording_that_is_too_big_is_refused'],
        'file': _TTS,
        'old': '''        clip = voice_clone.clip_of(self.cloned_voice)
        problem = (voice_clone.clip_problem(clip) if clip else "")
        if not problem:
            problem = voice_clone.clone_problem()''',
        'new': '''        problem = voice_clone.clone_problem()''',
    },
    {
        'label': 'the voice surface names the file and not whether it is there',
        'why': 'the row is where a person looks at an agreement they made. A '
               'row that says "Agreed on ... for alice-5s.wav" and nothing '
               'about the file reads like an agreement that is fine - and a '
               'recording that had been deleted is not fine.',
        'expect': ['test_the_row_says_when_the_recording_is_gone'],
        'file': _PAGE,
        'old': '''            clip_problem = voice_clone.clip_problem(consent.clip)
            if clip_problem:
                subtitle += f" The recording cannot be used: {clip_problem}"''',
        'new': '''            pass''',
    },
    {
        'label': 'the recording is found by name rather than by path',
        'why': '`clip_problem` measures a *file*, so handing it a name is a '
               'file that is not there - which is the very failure the '
               'measurement exists to catch, and it would report every '
               'recording as missing.',
        'expect': ['test_the_recording_is_found_by_its_voice'],
        'file': _VOICE,
        'old': '''    for entry in _read_registry():
        if entry.voice == voice:
            return entry.clip
    return ""''',
        'new': '''    for entry in _read_registry():
        if entry.voice == voice:
            return entry.voice
    return ""''',
    },
]
