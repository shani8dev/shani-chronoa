"""Mutation set for `decision.confidence`'s floor and its one caller's scale.

`harness-study/laya` (the decision model) names the hazard in
`laya/confidence.py`'s docstring:

> reporting the entropy number under the `answer_confidence` name would be worse
> than reporting nothing, because **the name is what a caller filters on.**

Chronoa had it twice over, and the second is the one that matters:

1. **The floor inflated any k above 2.** A `0.5` floor whose reason is "a binary
   outcome cannot be less than even" is right at k=2 and wrong above - and this
   repository's outcome model has three verdicts. Measured, 0.34 was reported as
   0.5 and two different distributions collided on one number.
2. **The caller laundered a reward through that probability clamp.**
   `ActionSelector.select` read `confidence({c: rewards.value[c]}) < threshold`,
   so an arm worth 0.05 and one worth 0.49 both reached the comparison as 0.5.
   Measured inert at the default 0.7 - every value below 0.7 is unconfident
   whether or not it was clamped first - and **one constant away from live**: at
   0.3, an arm worth 0.05 climbs to 0.5 and reads as confident, so the explore
   path stops asking about the worst tool on the machine.

The k=2 case is asserted unchanged, because 1/2 *is* 0.5 - so this set is pinned
in both directions and cannot fix k=3 by breaking k=2.
"""

TEST_FILE = "tests/test_confidence_floor.py"

_L = "usr/lib/shani-chronoa/shani_chronoa/learning.py"

MUTATIONS = [
    # -- the floor ----------------------------------------------------------
    {
        "file": _L,
        "old": "        return max(1.0 / len(probabilities), min(1.0, top))\n",
        "new": "        return max(0.5, min(1.0, top))\n",
        "label": "**the shipped floor, restored**: a below-even 3-way answer is "
                 "reported as even, and two distributions collide",
        "expect": ["test_a_three_way_answer_reports_its_real_maximum",
                   "test_two_below_even_distributions_are_distinguishable"],
    },
    {
        "file": _L,
        "old": "        return max(1.0 / len(probabilities), min(1.0, top))\n",
        "new": "        return min(1.0, top)\n",
        "label": "the floor is gone entirely, so a distribution's own minimum is "
                 "the only limit",
        "expect": ["test_the_floor_is_the_smallest_maximum_k_can_have"],
        # **Equivalent, and provably so.** For any distribution over k outcomes
        # the maximum is at least the mean, which is 1/k - so `max(1/k, top)` is
        # `top` for every *valid* distribution. The floor is defence against a
        # caller passing something that is not a distribution (which is what the
        # old caller did, and what the `len < 2` guard now covers), never a
        # functional limit.
        "equivalent": True,
        "why": "max(p) >= 1/k for any distribution over k, so the floor never "
               "binds on valid input; non-distributions are covered by the "
               "len < 2 guard instead",
    },
    {
        "file": _L,
        "old": "        if len(probabilities) < 2:\n            return min(1.0, top)\n",
        "new": "",
        "label": "**the regression my own fix introduced, restored**: a single "
                 "value reports 1/k = 1.0, so a 0.05 arm reads as certainty",
        # Measured while writing the fix: `confidence({"x": 0.05})` returned 1.0,
        # worse than the 0.5 the old floor gave.
        "expect": ["test_a_single_value_is_not_certainty"],
    },
    {
        "file": _L,
        "old": "        return max(1.0 / len(probabilities), min(1.0, top))\n",
        "new": "        return max(1.0 / max(len(probabilities) - 1, 1), min(1.0, top))\n",
        "label": "the floor is off by one order, so k=2 floors at 1.0",
        "expect": ["test_a_binary_outcome_is_unchanged"],
    },

    # -- the caller's scale --------------------------------------------------
    {
        "file": _L,
        "old": "                           if c not in self.rewards.value\n"
               "                           or self.rewards.value[c] < self.explore_threshold]\n",
        "new": "                           if c not in self.rewards.value\n"
               "                           or decision.confidence({c: self.rewards.value[c]}) < self.explore_threshold]\n",
        "label": "**the conflation, restored**: a reward goes back through "
                 "`confidence` before the comparison",
        "expect": ["test_a_low_threshold_no_longer_calls_a_bad_arm_confident"],
        # **Equivalent, and that is the point of the `len < 2` guard.** With it,
        # `confidence({c: value})` returns the value unchanged, so putting the
        # call back changes no input. The conflation is not neutralised by
        # removing it from the caller - it is neutralised by making the function
        # refuse to apply a probability-shaped floor to a single scalar. Both
        # halves are needed: the caller reads honestly *and* the function is safe
        # for the next caller.
        "equivalent": True,
        "why": "confidence() with one key now returns the value unchanged, so the "
               "call site and the direct comparison are the same expression",
    },
    {
        "file": _L,
        "old": "                           if c not in self.rewards.value\n"
               "                           or self.rewards.value[c] < self.explore_threshold]\n",
        "new": "                           if c not in self.rewards.value\n"
               "                           or self.rewards.value[c] > self.explore_threshold]\n",
        "label": "**the over-fix**: the comparison is inverted, so the *good* "
                 "arms are explored and the bad ones are not",
        "expect": ["test_the_default_threshold_is_unchanged",
                   "test_an_unknown_arm_is_still_explored"],
    },
    {
        "file": _L,
        "old": "            unconfident = [c for c in pool\n"
               "                           if c not in self.rewards.value\n"
               "                           or self.rewards.value[c] < self.explore_threshold]\n",
        "new": "            unconfident = []\n",
        "label": "nothing is unconfident, so an arm with no data is never explored",
        "expect": ["test_an_unknown_arm_is_still_explored"],
    },

    # -- an edge the floor must survive -------------------------------------
    {
        "file": _L,
        "old": "        if not probabilities:\n            return 0.0\n",
        "new": "        if not probabilities:\n            return 0.5\n",
        "label": "an empty distribution reports even, so a caller with no data "
                 "reads as a confident one",
        "expect": ["test_an_empty_and_a_saturated_case"],
    },
]
