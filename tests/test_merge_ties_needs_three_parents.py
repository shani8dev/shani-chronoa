"""Two parents cannot express a majority, and that is a property worth pinning.

The measurement this file records: on the only sibling pair available, 49.90%
of the 594,811,145 coordinates with a non-zero task vector are *contested* -
the two parents push opposite ways. With exactly two parents every contested
coordinate is a 1-1 tie, so TIES' sign election falls through to its magnitude
fallback on half the tensor.

That is a defensible rule. It is not the interference resolution the paper
describes, and a docstring that lets a reader believe otherwise is the defect
this test exists to keep visible.
"""
import inspect

from shani_chronoa import merge


def test_the_module_says_two_parents_cannot_resolve_by_consensus():
    """A reader must not be able to believe a two-parent merge is doing what
    the paper's three-parent result does."""
    said = inspect.getdoc(merge._combine)
    assert "two parents" in said or "two-parent" in said
    assert "49.90" in said, "the measured fraction belongs beside the claim"
    assert "594,811,145" in said.replace(",", ","), "and the count it came from"


def test_the_fallback_is_reachable_so_the_note_is_about_the_code_not_prose():
    """The note claims a path is taken on half the coordinates. If the count tie
    could not happen, the note would be about a branch that does not exist - and
    a warning about an unreachable branch is decoration.

    Two vectors of equal magnitude and opposite sign, so the count is 1-1 and
    the magnitudes are equal too: any resolution is the fallback's, and it must
    resolve rather than leave the coordinate unset.
    """
    import numpy as np
    out = merge._combine(np.array([0.0]), [(np.array([1.0]), 1.0),
                                            (np.array([-1.0]), 1.0)],
                         "ties", 1.0, 1.0)
    assert out[0] != 0.0, "an exactly-tied coordinate must still be resolved"
    assert abs(out[0]) == 1.0, "and it must carry one parent's full change"


def test_three_parents_agreeing_beat_a_dissenter_regardless_of_magnitude():
    """The preconditon the method exists for, at the extreme.

    Two parents push +1 and one pushes -10. By magnitude the dissenter should
    win; by count the majority should. TIES counts, so the majority wins - and
    this is the case a two-parent merge cannot express at any magnitude.
    """
    import numpy as np
    out = merge._combine(
        np.array([0.0]),
        [(np.array([1.0]), 1.0), (np.array([1.0]), 1.0), (np.array([-10.0]), 1.0)],
        "ties", 1.0, 1.0)
    assert out[0] == 1.0, (
        "magnitude must not decide the vote; got %r" % out[0])


def test_one_parent_against_two_is_the_same_case_the_paper_describes():
    """Sanity on the other side: the *single* dissent is dropped, not averaged.

    Under plain averaging the dissenter moves the result to (1 + 1 - 10) / 3;
    under TIES it does not count at all.
    """
    import numpy as np
    vectors = [(np.array([1.0]), 1.0), (np.array([1.0]), 1.0),
               (np.array([-10.0]), 1.0)]
    plain = merge._combine(np.array([0.0]), vectors, "task_arithmetic", 1.0, 1.0)
    consensus = merge._combine(np.array([0.0]), vectors, "ties", 1.0, 1.0)
    # **Not -8/3.** `task_arithmetic` *sums* the task vectors; the division by the
    # agreeing count is TIES' "disjoint mean" step, so averaging it is comparing
    # two different things and the control would have passed for the wrong
    # reason. 1 + 1 - 10 = -8, and TIES returns 1.0.
    assert plain[0] == -8.0, "the control must be summing for this to mean anything"
    assert consensus[0] == 1.0, "the dissent must be excluded, not summed in"


def test_three_parents_do_resolve_by_count():
    """The case the paper is about, and the one this implementation can do."""
    import numpy as np
    three_agreeing = [(np.array([1.0]), 1.0)] * 3
    out = merge._combine(np.array([0.0]), three_agreeing + [(np.array([-0.01]), 1.0)],
                         "ties", 1.0, 1.0)
    assert out[0] == 1.0, (
        "a 3-1 majority must win over a tiny dissenter; got %r" % out[0])
