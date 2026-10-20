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
        # Witness corrected 2026-10-16 from the run's own `NOT caught by`. The
        # dropped test asserts
        #     orb.get_size_request()[0] == _orb_size(AssistantState.IDLE)
        # and `_orb_size` is **imported from the source being mutated** (test
        # line 51), so it compares the widget against the very function this
        # entry replaces: whatever `_orb_size` returns, the comparison holds.
        # It cannot see this mutation, and it was never scoped to - its own
        # docstring says what it is for ("applying the size only on the first
        # *state change* would pass every test above"), which is the entry below
        # this one and does fire on it.
        "expect": ["test_a_quiet_window_does_not_carry_the_reserve"],
    },
    {
        "file": _WIDGETS,
        "old": "    return _ORB_LIVE_PX if state in _LIVE_STATES else _ORB_RESTING_PX\n",
        "new": "    return _ORB_RESTING_PX\n",
        # The over-fix, and the dangerous direction: the halo is clipped for the
        # whole length of every reply and every spoken answer.
        "label": "the orb never grows, clipping the halo while listening and speaking",
        # Witness corrected 2026-10-16 from the run's own `NOT caught by`. Same
        # tautology as the entry above, and here it is the whole test:
        #     assert orb.get_size_request()[0] == _orb_size(state), state.name
        # both operands move when `_orb_size` does, since it is imported from
        # the mutated source at test line 51. It can only fail if the computed
        # size stops being *applied*, which is the mutation two entries down -
        # and that one is caught by it. The witness that survives here moves on
        # one side only: `side` comes from the mutated `_orb_size`, while the
        # bound it is compared against comes from `_halo_size` - a different
        # function in the same module that this entry does not touch - and is
        # chosen per state by which states actually feed a level.
        "expect": ["test_the_orb_is_never_smaller_than_the_halo_it_can_contain"],
    },
    {
        "file": _WIDGETS,
        # Re-anchored 2026-10-20: `DICTATING` joined the live states (a
        # dictation feeds the level too), so the needle moved and the
        # mutation drops both of the states that are not listening.
        "old": "_LIVE_STATES = frozenset({AssistantState.LISTENING, AssistantState.DICTATING,\n"
               "                           AssistantState.SPEAKING})\n",
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
        # Re-anchored 2026-10-16: `_apply_state` now scales the computed side
        # through `self._scaled(...)` before applying it, so the anchor moved
        # with the call. The defect is the same one - the size is computed on a
        # state change and then thrown away - and the scaled form has to be in
        # the needle, because anchoring on the bare `_orb_size(state)` would be
        # a substring of this line rather than a line of its own.
        "old": "        side = self._scaled(_orb_size(state))\n"
               "        self.set_size_request(side, side)\n",
        "new": "        side = self._scaled(_orb_size(state))\n",
        # The size is computed and thrown away, so the orb never follows the
        # state at all: every assertion above reads the construction.
        "label": "the size is computed on a state change and then not applied",
        "expect": ["test_the_size_follows_the_state",
                   "test_the_orb_is_never_smaller_than_the_halo_it_can_contain"],
    },
]