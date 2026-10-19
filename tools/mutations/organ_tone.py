"""Mutations for the organ tone - a switch that was shipped and did nothing.

**The defect this set exists to prevent is a control that is shipped and
does nothing.** `organ-audible-enabled` says, in the schema's own words:

    When true, starting to listen, look at the screen or the camera, speak,
    reach the network or act on something plays a short quiet tone naming
    the organ class. Off by default: the lights in the window are always on,
    and a sound people have learned to mute is worse than no sound.

So the feature is *described*. The body had the API for it - `announce()`
asks, `set_audible()` installs the answer - and the organ strip had the tone
player in `OrganStrip.play`, and **nothing joined them**. The switch did
nothing at all: a person who turned it on heard silence, and a person who
left it off could not tell the difference.

**And the shape of the hole is the shape of the repair.** `body.set_notice`
is the text half of the same idea and it *is* wired: the window installs it,
because the body cannot speak for itself and the window is what gives it a
voice. The sound half had the same two ends and no wire between them.

The mutations below are the ways each of those can come back, and the first
one restores the shipped defect verbatim.
"""

TEST_FILE = 'tests/test_the_organ_tone_is_wired.py'

_BODY = 'usr/lib/shani-chronoa/shani_chronoa/body.py'
_WINDOW = 'usr/lib/shani-chronoa/shani_chronoa/gui/window.py'
_ORGANS = 'usr/lib/shani-chronoa/shani_chronoa/gui/organs.py'

MUTATIONS = [
    {
        'label': 'the body does not ask for a tone when an organ starts',
        'why': 'the shipped defect, verbatim. `use()` is the one place an '
               'organ starts - `lit()` goes through it, and the tools go '
               'through it - so an organ that never asks is an organ that '
               'never makes a sound, whatever the switch says.',
        'expect': ['test_starting_an_organ_asks_for_its_tone',
                   'test_a_pulse_asks_too_because_reaching_the_network_is_one'],
        'file': _BODY,
        'old': '        self.announce(organ, what)\n        return activity',
        'new': '        return activity',
    },
    {
        'label': 'the window installs no hook, so the body asks nobody',
        'why': 'the other end of the same wire. `announce()` asks whoever '
               'installed the hook, and a window that installs nothing is a '
               'window where the question has no listener - which is exactly '
               'what the switch did before.',
        'expect': ['test_the_window_asks_the_strip_to_play'],
        'file': _WINDOW,
        'old': '            body.body.set_audible(self._play_organ_tone)',
        'new': '            body.body.set_audible(None)',
    },
    {
        'label': 'the window builds the hook and never installs it',
        'why': 'the call site, which is a separate thing from the method. A '
               'method nothing calls is a method: the window can hold a '
               'perfect installer and still ship a switch that does nothing, '
               'and only the call site can tell that apart from the method '
               'being wrong.',
        'expect': ['test_the_window_actually_calls_the_installer'],
        'file': _WINDOW,
        'old': '        self._install_body_notices()\n        self._install_body_sounds()\n',
        'new': '        self._install_body_notices()\n',
    },
    {
        'label': 'a broken listener takes the light with it',
        'why': 'the light is the indicator; the sound is a courtesy on top of '
               'it. A hook that raises must not stop the activity being '
               'recorded - and the notice hook beside it swallows its own '
               'exceptions for the same reason.',
        'expect': ['test_a_hook_that_raises_does_not_take_the_activity_with_it'],
        'file': _BODY,
        'old': '''        if self._audible is not None:
            try:
                self._audible(organ, what)
            except Exception:                          # noqa: BLE001
                logger.debug("the audible hook failed", exc_info=True)''',
        'new': '''        if self._audible is not None:
            self._audible(organ, what)''',
    },
    {
        'label': 'the strip plays the tone with the switch off',
        'why': 'the switch is the whole point of the setting: "off by '
               'default: the lights in the window are always on, and a sound '
               'people have learned to mute is worse than no sound". A strip '
               'that plays regardless is a person who turned it off and '
               'cannot tell the difference from one who turned it on.',
        'expect': ['test_the_switch_off_means_no_tone'],
        'file': _ORGANS,
        'old': '''        if not self.audible_enabled():
            return
        entry = ORGAN_TONE.get(organ)''',
        'new': '''        entry = ORGAN_TONE.get(organ)''',
    },
]
