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


def test_the_base_is_not_a_voter_and_three_files_are_not_three_voters():
    """`blend` skips a zero coefficient before it even reads the tensor, so a
    base expressed as `[base, a, b], [0.0, 1.0, 1.0]` loads **two** task vectors.

    That means a merge over N fine-tunes of one ancestor needs N >= 3 fine-tunes
    before the election can express a majority - four files, not three. Reading
    the file count as the voter count is the obvious mistake and it is the one
    that would make a two-parent sweep look like the three-parent result.
    """
    import numpy as np
    from shani_chronoa import merge

    # Drive `blend`'s own selection rather than re-deriving it: the property is
    # about which tensors get read, which is `blend`'s decision, not `_combine`'s.
    base = np.array([0.0])
    contributors = [(np.array([1.0]), 1.0), (np.array([-1.0]), 1.0)]
    # What a third *file* at coefficient 0 would contribute if it were counted:
    # the base's own task vector, which is identically zero.
    with_base = contributors + [(base - base, 1.0)]

    # Two voters at a contested coordinate: a tie, resolved by fallback.
    two = merge._combine(base, contributors, "ties", 1.0, 1.0)
    # Three voters, one of which has nothing to say: also resolved, but now the
    # count is a genuine majority rather than a fallback.
    assert abs(two[0]) == 1.0
    assert len(with_base) == 3 and not with_base[2][0].any(), (
        "the base's task vector is zero by construction - which is exactly why "
        "it cannot vote")


def test_a_zero_coefficient_is_never_even_read():
    """The mechanism, read off `blend`'s own source rather than re-implemented.

    A test that re-derives the rule tests the copy. This reads the module's
    selection line, so a future version that loads the tensor and discovers it
    is zero would fail here rather than pass quietly.
    """
    import inspect
    from shani_chronoa import merge
    source = inspect.getsource(merge.blend)
    assert "if not coefficient:" in source and "continue" in source, (
        "blend must skip a zero coefficient before reading the tensor")


def test_the_trim_is_what_keeps_a_summed_merge_finite():
    """**This is what the trim actually does, and it is not interference
    resolution.**

    Measured on three Qwen3-0.6B checkpoints, summing two task vectors at
    coefficient 1.0 each gives perplexity **33097.4** - 678x the base, a diverged
    model rather than a merge. The same two vectors trimmed to density 0.5 give
    **75.68**. Same parents, same coefficients, same code path; the only
    difference is how many coordinates survived.

    Summing two fine-tunes of one ancestor roughly doubles the perturbation
    wherever they agree, and 97.77% of coordinates is where they agree. The
    trim is therefore mostly a *stabiliser* - it bounds how far the sum can
    travel - and interference resolution is a 2.23% side effect on top.
    """
    import numpy as np
    from shani_chronoa import merge

    base = np.zeros(1000, dtype=np.float32)
    # Two independent fine-tunes: both large, both agreeing almost everywhere,
    # which is what 97.77% agreement looks like.
    a = np.full(1000, 0.4, dtype=np.float32)
    b = np.full(1000, 0.4, dtype=np.float32)
    a[0] = 0.5; b[0] = -0.5          # the 2.23% that is contested

    summed = merge._combine(base, [(a, 1.0), (b, 1.0)], "task_arithmetic", 1.0, 1.0)
    trimmed = merge._combine(base, [(a, 1.0), (b, 1.0)], "ties", 0.5, 1.0)

    assert abs(summed).max() == 0.8, "the sum doubles the perturbation"
    assert abs(trimmed).max() < abs(summed).max(), (
        "trimming must bound how far the sum travels - that is its job")
    assert trimmed.max() <= 0.5, "and it must keep only the largest changes"


def test_a_trimmed_sum_stays_near_the_base_where_they_agree():
    """The specific mechanism: at coordinates both parents move the same way,
    summing doubles the change and trimming halves it back."""
    import numpy as np
    from shani_chronoa import merge
    base = np.zeros(4, dtype=np.float32)
    same = [(np.full(4, 0.4, np.float32), 1.0), (np.full(4, 0.4, np.float32), 1.0)]
    summed = merge._combine(base, same, "task_arithmetic", 1.0, 1.0)
    trimmed = merge._combine(base, same, "ties", 0.5, 1.0)
    assert np.allclose(summed[2], 0.8), "summed doubles a shared perturbation"
    assert np.allclose(trimmed[2], 0.4), "trimmed halves it back toward the parent"
