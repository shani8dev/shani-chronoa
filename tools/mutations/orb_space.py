"""Mutation set for the orb's size following the halo it actually contains.

The orb was hardcoded to 124px so the level halo had somewhere to go, and
`_halo_size` runs 84px at rest to 120px at full input level - so 124 is right
for exactly two states and 40px of permanent reserve for the other five. Measured
on the real window that reserve was larger than the entire conversation: the orb
124px against a transcript of 162px, which did not grow at all after three turns.

The set is the two ends of the range (always big, always small), the wrong
**pair** of live states, and the two places the size is applied - because a
function that computes the right number and a window that never asks it for it are
both green.
"""

TEST_FILE = "tests/test_orb_gives_its_space_back.py"

_WIDGETS = "usr/lib/shani-chronoa/shani_chronoa/gui/widgets.py"

MUTATIONS = [
    {
        "file": _WIDGETS,
        "old": "    return _ORB_LIVE_PX if state in _LIVE_STATES else _ORB_RESTING_PX\n",
        "new": "    return _ORB_LIVE_PX\n",
        # The shipped behaviour, verbatim: the orb never gives any of its 40px
        # back, so the conversation stays 162px while a decorative microphone
        # occupies 124 of them.
        "label": "the orb never gives its space back (the shipped bug)",
        "expect": ["test_a_quiet_window_does_not_carry_the_reserve",
                   "test_a_freshly_built_orb_is_already_at_resting_size"],
    },
    {
        "file": _WIDGETS,
        "old": "    return _ORB_LIVE_PX if state in _LIVE_STATES else _ORB_RESTING_PX\n",
        "new": "    return _ORB_RESTING_PX\n",
        # The over-fix, and the dangerous direction: the halo is clipped for the
        # whole length of every reply and every spoken answer.
        "label": "the orb never grows, clipping the halo while listening and speaking",
        "expect": ["test_the_orb_is_never_smaller_than_the_halo_it_can_contain",
                   "test_the_size_follows_the_state"],
    },
    {
        "file": _WIDGETS,
        "old": "_LIVE_STATES = frozenset({AssistantState.LISTENING, AssistantState.SPEAKING})\n",
        "new": "_LIVE_STATES = frozenset({AssistantState.LISTENING})\n",
        # **The first version of this change**, kept because it is what a
        # plausible reading of "the halo grows while you talk" produces:
        # `set_playback_level` feeds it during SPEAKING too, so this clips the
        # halo for the whole of every spoken reply.
        "label": "only listening grows the orb (clips the halo for every spoken reply)",
        "expect": ["test_the_states_that_grow_are_exactly_the_states_that_feed_a_level",
                   "test_the_orb_is_never_smaller_than_the_halo_it_can_contain"],
    },
    {
        "file": _WIDGETS,
        "old": "        self._apply_state(AssistantState.IDLE)\n",
        "new": "        pass\n",
        # The construction path. The first version of this set mutated the size
        # request inside `_setup_ui` and came back a **SURVIVOR**, because
        # `_setup_ui` also calls `_apply_state` three lines later and that call
        # set the size again - the mutated line had no effect at all, so the
        # mutation could not fail rather than the test being weak.
        #
        # The redundant line is now gone (one place sets the size) and this
        # mutation targets the call that actually does it. Without it the orb
        # never gets a size request at all and falls back to the stylesheet's
        # 80px minimum.
        "label": "a freshly built orb is never given a size at all",
        "expect": ["test_a_freshly_built_orb_is_already_at_resting_size"],
    },
    {
        "file": _WIDGETS,
        "old": "        side = _orb_size(state)\n"
               "        self.set_size_request(side, side)\n",
        "new": "        side = _orb_size(state)\n",
        # The size is computed and thrown away, so the orb never follows the
        # state at all: every assertion above reads the construction.
        "label": "the size is computed on a state change and then not applied",
        "expect": ["test_the_size_follows_the_state",
                   "test_the_orb_is_never_smaller_than_the_halo_it_can_contain"],
    },
]