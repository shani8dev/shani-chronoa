"""`decision.confidence`'s floor, and the number its one caller was feeding it.

`harness-study/laya` (the decision model) states the hazard in
`laya/confidence.py`'s docstring:

> `confidence` is a different quantity on a different scale ... So this
> deliberately does **not** fall back to it: reporting the entropy number under
> the `answer_confidence` name would be worse than reporting nothing, because
> **the name is what a caller filters on.**

Chronoa's `decision.confidence` floors at `1/k`, and its only caller used to launder
a **reward-space** number through that **probability-space** clamp. Two measured
consequences, both of which the name was hiding:

**1. The floor inflated any k above 2.** Its reason was "a binary outcome cannot be
less than even" - true at k=2, false at every k above, and this repository's own
outcome model has three verdicts:

| distribution | max(p) | was reported as |
|---|---|---|
| 0.33/0.33/0.34 | 0.34 | **0.5** |
| 0.40/0.30/0.30 | 0.40 | **0.5** |

A genuinely-below-even 3-way answer read as *even*, and two different distributions
read as the *same* number, so a caller could not order them.

**2. The caller was passing a reward.** `ActionSelector.select` read
`confidence({c: rewards.value[c]}) < threshold`, so an arm worth 0.05 and one worth
0.49 both reached the comparison as 0.5. Measured, that was **inert at the default
threshold of 0.7** - every value below 0.7 is unconfident whether or not it was
clamped first. It was one threshold change from live: at 0.3, an arm worth 0.05
climbs to 0.5 and reads as **confident**. The name was the bug, not the arithmetic.

`k=2` is unchanged, because 1/2 *is* 0.5 - asserted below so the k=2 case cannot
quietly drift while k=3 is being fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa.learning import decision  # noqa: E402, F811


class TestTheFloorIsOneOverK:
    def test_a_binary_outcome_is_unchanged(self):
        """1/2 is 0.5, so the k=2 case this floor was written for is bit-identical."""
        assert decision.confidence({"a": 0.5, "b": 0.5}) == 0.5
        assert decision.confidence({"a": 0.1, "b": 0.9}) == 0.9
        assert decision.confidence({"a": 0.33, "b": 0.67}) == pytest.approx(0.67)
        # A binary outcome cannot be below even, and still floors at 0.5.
        assert decision.confidence({"a": 0.05, "b": 0.95}) == 0.95

    def test_a_three_way_answer_reports_its_real_maximum(self):
        """**The defect.** 0.34 read as 0.5, which is a claim of even."""
        three = {"a": 0.33, "b": 0.33, "c": 0.34}
        assert decision.confidence(three) == pytest.approx(0.34)

    def test_two_below_even_distributions_are_distinguishable(self):
        """The second half of the defect: they used to collide at 0.5, so a caller
        could not order them."""
        low = decision.confidence({"a": 0.33, "b": 0.33, "c": 0.34})
        higher = decision.confidence({"a": 0.40, "b": 0.30, "c": 0.30})
        assert low < higher, "two different distributions still report one number"

    def test_the_floor_is_the_smallest_maximum_k_can_have(self):
        for k in (2, 3, 4, 7):
            flat = {f"o{i}": 1.0 / k for i in range(k)}
            assert decision.confidence(flat) == pytest.approx(1.0 / k), (
                f"a flat {k}-way distribution did not read as its own 1/k")

    def test_an_empty_and_a_saturated_case(self):
        assert decision.confidence({}) == 0.0
        assert decision.confidence({"a": 1.0, "b": 0.0}) == 1.0


class TestTheCallerNoLongerLaundersAReward:
    """`ActionSelector.select` compared a *reward* through a *probability* clamp.

    Inert at the default 0.7 - and that is the reason it is worth a test: a defect
    that only bites when a constant changes is one nobody re-measures.

    **The first version of these witnesses never called `select()`.** They rebuilt
    the `unconfident` list by hand with the same expression, or asserted on
    `rewards.value` directly, so all three mutations of `select()` came back as
    survivors. A witness has to drive the thing it names.
    """

    def _selector(self, threshold, **rewards):
        selector = decision.ActionSelector(explore_threshold=threshold)
        for action, reward in rewards.items():
            selector.rewards.value[action] = reward
        return selector

    def _explored(self, selector, pool):
        """The arms `select` would treat as unconfident, read off its own branch.

        `select` returns one choice, so this drives it many times with a seeded
        rng and reports which arms came back through the explore half.
        """
        explored = set()
        for _ in range(200):
            choice, mode = selector.select(pool, values={})
            if mode == "explore":
                explored.add(choice)
        return explored

    def test_the_default_threshold_is_unchanged(self):
        """Behaviour at 0.7 must not move: the clamp was inert there, so the fix is
        a contract change and not a behavioural one."""
        selector = self._selector(0.7, good=0.9, mediocre=0.55, bad=0.05)
        explored = self._explored(selector, ["good", "mediocre", "bad"])
        assert "good" not in explored, "the best arm should never be explored"
        assert "bad" in explored, "the worst arm must stay explorable"
        assert "mediocre" in explored, "an arm below the threshold stays explorable"

    def test_a_low_threshold_no_longer_calls_a_bad_arm_confident(self):
        """**The case that was one constant away from live.**

        At 0.3, an arm worth 0.05 used to climb to 0.5 through the clamp and read
        as *confident* - so the explore path stopped asking about the worst tool
        this machine has. Drive `select` and the arm must still be reachable.
        """
        selector = self._selector(0.3, good=0.9, bad=0.05)
        assert "bad" in self._explored(selector, ["good", "bad"]), (
            "an arm worth 0.05 is no longer explored below a 0.3 threshold, so "
            "the worst tool on the machine is never asked about")
        assert "good" not in self._explored(selector, ["good", "bad"])

    def test_an_unknown_arm_is_still_explored(self):
        """The half the clamp never touched: a candidate with no data at all."""
        selector = self._selector(0.7, good=0.9)
        assert "never-seen" in self._explored(selector, ["good", "never-seen"]), (
            "an arm with no data is never explored, so the selector stops asking "
            "about anything it has not measured")

    def test_a_single_value_is_not_certainty(self):
        """**A regression my own fix introduced, caught by the mutation run.**

        A `1/k` floor is only right where the input is a distribution over k
        outcomes. The previous caller passed `{c: reward}` - one key, an arbitrary
        scalar - and `1/1 = 1.0` reported a 0.05 arm as *certainty*, which is worse
        than the 0.5 the old floor gave. Measured, then guarded.
        """
        assert decision.confidence({"x": 0.05}) == pytest.approx(0.05)
        assert decision.confidence({"x": 0.9}) == pytest.approx(0.9)
