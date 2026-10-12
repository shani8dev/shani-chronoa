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


def test_three_parents_do_resolve_by_count():
    """The case the paper is about, and the one this implementation can do."""
    import numpy as np
    three_agreeing = [(np.array([1.0]), 1.0)] * 3
    out = merge._combine(np.array([0.0]), three_agreeing + [(np.array([-0.01]), 1.0)],
                         "ties", 1.0, 1.0)
    assert out[0] == 1.0, (
        "a 3-1 majority must win over a tiny dissenter; got %r" % out[0])
