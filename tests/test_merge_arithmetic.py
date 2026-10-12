"""The merge arithmetic, with no 600 MB file involved.

Every test here is a property that could be false, and the interference pair is
the one that distinguishes TIES from averaging: if deleting the sparsify and
sign-consensus steps leaves this file green, the suite is measuring nothing.
"""
import numpy as np
import pytest

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
