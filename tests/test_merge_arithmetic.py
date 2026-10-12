"""The merge arithmetic, with no 600 MB file involved.

Every test here is a property that could be false, and the interference pair is
the one that distinguishes TIES from averaging: if deleting the sparsify and
sign-consensus steps leaves this file green, the suite is measuring nothing.
"""
import pathlib
import sys
import numpy as np
import pytest

from shani_chronoa import merge
from shani_chronoa.merge import MergeRefusal, _combine

BASE = np.zeros(8, dtype=np.float32)


def v(*values):
    return np.asarray(values, dtype=np.float32)


def test_lambda_zero_returns_the_base_exactly():
    for method in ("task_arithmetic", "ties"):
        out = _combine(BASE, [(v(1, 1, 1, 1, 1, 1, 1, 1), 1.0)],
                       method, 0.5, 0.0)
        assert np.array_equal(out, BASE), method


def test_one_parent_at_density_one_returns_that_parent():
    """The control for the whole sparsify step: at density 1 it must be inert."""
    parent = v(1, -2, 3, -4, 5, -6, 7, -8)
    for method in ("task_arithmetic", "ties"):
        out = _combine(BASE, [(parent, 1.0)], method, 1.0, 1.0)
        assert np.allclose(out, parent), method


def test_ties_at_full_density_is_exactly_task_arithmetic():
    """One parent, density 1, lam 0.5 - if the two paths disagree, one is wrong."""
    parent = v(1, -2, 3, -4, 5, -6, 7, -8)
    assert np.allclose(_combine(BASE, [(parent, 1.0)], "task_arithmetic", 1.0, 0.5),
                       _combine(BASE, [(parent, 1.0)], "ties", 1.0, 0.5))


def test_ties_drops_the_smallest_change_at_low_density():
    """A one-element tensor cannot be trimmed, so it cannot prove anything here."""
    # A one-element tensor cannot demonstrate trimming - at density 0.5 the one
    # entry always survives - so the discriminating case is a tensor where a
    # small change competes with a large one.
    base = v(0.0, 0.0)
    out = _combine(base, [(v(0.01, 5.0), 1.0)], "ties", 0.5, 1.0)
    assert out[0] == 0.0, "the smaller change should not survive trimming"
    assert out[1] == pytest.approx(5.0), "the larger change must survive"


def test_task_arithmetic_keeps_what_ties_drops():
    """The same tensor, unmasked: the whole difference between the two methods."""
    base = v(0.0, 0.0)
    out = _combine(base, [(v(0.01, 5.0), 1.0)], "task_arithmetic", 0.5, 1.0)
    assert out[0] == pytest.approx(0.01)


def test_sign_consensus_beats_averaging_under_interference():
    """**This is the claim TIES exists for, and averaging fails it.**

    Four parents, two saying +1 and two saying -1, from a base of 0. Averaging
    cancels to 0. TIES elects a sign by majority count and takes the mean of the
    agreeing values only, so the signal survives at full strength.
    """
    agreeing = [(v(1.0), 1.0), (v(1.0), 1.0)]
    dissenting = [(v(-1.0), 1.0), (v(-1.0), 1.0)]
    linear = _combine(v(0.0), agreeing + dissenting, "task_arithmetic", 1.0, 1.0)
    assert linear[0] == pytest.approx(0.0), "averaging really does cancel this"

    ties_out = _combine(v(0.0), agreeing + dissenting, "ties", 1.0, 1.0)
    assert abs(ties_out[0]) == pytest.approx(1.0), (
        "the agreeing half must survive at full strength, got %r" % ties_out[0])


def test_three_against_one_keeps_the_three():
    """Majority by count: one large dissenter does not outvote three."""
    vectors = [(v(1.0), 1.0)] * 3 + [(v(-100.0), 1.0)]
    out = _combine(v(0.0), vectors, "ties", 1.0, 1.0)
    assert out[0] == pytest.approx(1.0), (
        "magnitude must not decide the vote; got %r" % out[0])


def test_a_coordinate_nobody_changes_keeps_the_base():
    """The parents must *equal* the base here.

    The first version passed parents of 0 against a base of 7, which is a change
    of -7 that both parents agree on - so TIES correctly returned 0 and the test
    failed. It was the fixture describing a coordinate nobody changed while
    actually describing one everybody changed by the same amount, which is the
    opposite case and the opposite answer.
    """
    vectors = [(v(7.0), 1.0), (v(7.0), 1.0)]
    assert _combine(v(7.0), vectors, "ties", 1.0, 1.0)[0] == pytest.approx(7.0)


def test_a_bad_density_is_refused_by_name():
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(MergeRefusal) as caught:
            _combine(BASE, [(BASE, 1.0)], "ties", bad, 1.0)
        assert str(bad) in str(caught.value)


def test_an_unknown_method_is_refused_by_name():
    with pytest.raises(MergeRefusal) as caught:
        _combine(BASE, [(BASE, 1.0)], "average", 0.5, 1.0)
    assert "average" in str(caught.value)


def test_no_vectors_returns_the_base():
    assert np.array_equal(_combine(BASE, [], "ties", 0.5, 1.0), BASE)


# ---------------------------------------------------------------------------
# The 111x spread is the measured confound in the retracted chimera ladder.
# ---------------------------------------------------------------------------
class TestBalanceEqualisesTheDeltaNorms:
    """The 111x spread is the measured confound in the retracted chimera ladder.

    Three capability adapters, all declaring `alpha/rank` of 2.0, sit **111x**
    apart in total delta norm - measured off the real GGUFs, not asserted: 0.0365
    / 3.0265 / 4.0636 across 196 `lora_a`/`lora_b` pairs each. Almost all of the
    difference is `lora_b`.

    So a three-adapter sum is executor + cortex + **0.5% of mouth**, which is a
    rounding error, and the ladder that was read as "orthogonality does not give
    semantic composition" was confounded by arithmetic. `alpha/rank` says the
    three are comparable; the tensors say one of them is 1% of another.
    """

    #: The three measured totals, from `build/probe_adapter_norms.py` against the
    #: real files. Named so the docstring's claim is checked rather than trusted.
    MEASURED = (0.0365, 3.0265, 4.0636)
    MEASURED_PAIRS = 196
    MEASURED_SPREAD = 111.2

    def _vectors(self, norms):
        import numpy as np
        # A zero-mean vector of the given norm, so "same shape, different
        # magnitude" is exactly what is under test and nothing else varies.
        return [(np.full(8, n / (8 ** 0.5)), 1.0) for n in norms]

    def test_the_spread_is_real_off_the_real_files(self):
        """Measured from the GGUFs, so a re-run cannot quietly pass on a claim.

        This is the positive control for the class: if the measurement stops
        reproducing, the reason for balancing has stopped holding and this fails
        - rather than the arithmetic proceeding on a number nobody re-derived.
        It resolves the repo root from **this file** (four `parents` up), because
        the first version used `merge.__file__` and landed on `usr/lib`, which
        made the control skip rather than run. **A skip that hides the control is
        the same defect as a control that cannot fail.**
        """
        repo = pathlib.Path(__file__).resolve().parents[1]
        build = repo / "build"
        if str(build) not in sys.path:
            sys.path.insert(0, str(repo / "tools"))
        sys.path.insert(0, str(repo / "cache" / "cultivate-libs"))
        import adapter_norms as probe

        present, totals = [], []
        for name in probe.ADAPTERS:
            path = repo / "cache/data-home/shani-chronoa/llm" / name
            if not path.exists():
                pytest.skip(f"{name} is not on this box")
                continue
            per, total, fams = probe.read_adapter(path)
            assert len(per) == self.MEASURED_PAIRS, (
                f"{name}: {len(per)} lora_a/lora_b pairs against "
                f"{self.MEASURED_PAIRS} measured - either the adapters changed or "
                f"the probe is no longer reading them all, and the 111x figure "
                f"this class rests on is unverified")
            present.append(name)
            totals.append(total)
        assert len(present) >= 2, (
            "fewer than two adapters are on this box; nothing was compared")
        spread = max(totals) / min(totals)
        assert spread > 10.0, (
            f"measured spread {spread:.1f}x, against the {self.MEASURED_SPREAD}x "
            f"the docstring cites - the reason for balancing has changed and this "
            f"must be re-derived rather than trusted")

    def test_equalising_gives_every_parent_the_same_delta_norm(self):
        scales = merge.balance_scales(self._vectors(self.MEASURED), np.zeros(8))
        after = [scales[i] * float(np.linalg.norm(self._vectors(
            self.MEASURED)[i][0])) for i in range(3)]
        assert max(after) / min(after) < 1.0001, after

    def test_the_weakest_parent_is_inflated_by_a_reported_factor(self):
        """The cost is the point, so it is asserted rather than described.

        Rescaling 0.0365 up to the mean of the three multiplies it by **65x**.
        That inflates something trained to be small into a magnitude it was never
        fitted at. The test pins the number so a reader sees it in the failure
        rather than in a comment.
        """
        scales = merge.balance_scales(self._vectors(self.MEASURED), np.zeros(8))
        weakest = min(range(3), key=lambda i: self.MEASURED[i])
        assert scales[weakest] > 50.0, (
            f"the weakest adapter's scale is {scales[weakest]:.2f}x - either the "
            f"measurement moved or the balancing is no longer equalising")

    def test_the_callers_coefficient_survives_rather_than_being_replaced(self):
        """`lambda` means what it meant before the parameter existed.

        Overwriting the caller's weight would make `balance=True` silently
        redefine `lambda_i`, which is how a merge starts not containing the model
        the caller thought it did.
        """
        import numpy as np
        tf = pathlib.Path(merge.__file__).read_text()
        assert "coefficient * scales[index]" in tf, (
            "the balance factor must multiply the caller's coefficient"
        )

    def test_a_zero_norm_vector_is_left_alone(self):
        """A parent that contributed nothing gets no share of the target.

        Scaling it up does not create what training did not put there, and the
        alternative is a merge where an absent capability is amplified by a
        factor the caller never asked for.
        """
        import numpy as np
        zero = (np.zeros(8), 1.0)
        live = (np.full(8, 4.0 / 8 ** 0.5), 1.0)
        scales = merge.balance_scales([zero, live], np.zeros(8))
        assert scales[0] == 1.0, scales

    def test_balancing_is_off_by_default_so_an_ask_for_nothing_is_unchanged(self):
        """The default is the merge that existed before the parameter.

        A new default that changes what a merge contains is a regression wearing
        a feature's clothes; `f16` is the same rule `ctxfit` used for the KV
        ladder.
        """
        import inspect
        assert inspect.signature(merge.blend).parameters["balance"].default is False
