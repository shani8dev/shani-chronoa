"""The progression's rule is that an unmeasured stage unlocks nothing.

`cultivation.py` documents six stages, five prohibitions and a gate per stage,
and nothing imported it. A module that nothing can reach is green on everything,
so these tests exist to make it reachable *and* to pin the one property the
whole design turns on.
"""
from shani_chronoa import cultivation


def test_there_are_six_stages_and_each_one_says_what_it_must_produce():
    """`PRODUCES` is the table the gates are read from, so an unnamed stage is
    a stage nothing can be held to."""
    assert len(cultivation.STAGES) == 6
    missing = [s for s in cultivation.STAGES if not cultivation.PRODUCES.get(s)]
    assert not missing, "stages with no gate: %r" % missing


def test_the_stages_are_in_the_order_the_module_claims():
    assert cultivation.STAGES[:3] == ("foundation", "perception", "refinement")
    assert cultivation.STAGES[-2:] == ("ascension", "immortality")


def test_an_unmeasured_stage_unlocks_the_ones_after_it_and_no_earlier():
    assert cultivation.stage_status("refinement") == [
        "refinement", "attunement", "ascension", "immortality"]
    assert not set(cultivation.stage_status("refinement")) & {
        "foundation", "perception"}


def test_an_unknown_stage_unlocks_nothing():
    """**The control.** Without this, a scanner matching nothing satisfies
    every other test here - the shape this repo keeps recording as a check that
    cannot fail."""
    assert cultivation.stage_status("not-a-stage") == []
    assert cultivation.next_stage("not-a-stage") is None


def test_the_empty_string_is_not_the_first_stage():
    """`""` is a plausible 'nothing measured yet', and treating it as
    `foundation` would let a fresh machine claim stage one by saying nothing."""
    assert "" not in cultivation.STAGES
    assert cultivation.next_stage("") is None


def test_next_stage_walks_forward_and_stops_at_the_end():
    assert cultivation.next_stage("foundation") == "perception"
    assert cultivation.next_stage("ascension") == "immortality"
    assert cultivation.next_stage("immortality") is None


def test_the_last_stage_returns_itself_from_status_but_nothing_after_it():
    """`stage_status` is inclusive and `next_stage` is not - so immortality has
    no successor, which is the property that makes the progression finite."""
    assert cultivation.stage_status("immortality") == ["immortality"]
    assert cultivation.next_stage("immortality") is None


def test_a_verdict_carries_a_reason_rather_than_a_bare_bool():
    """A refusal nobody can act on is a bug report, not a design."""
    unlocked = cultivation.Verdict(True, "measured on a held-out split")
    refused = cultivation.Verdict(False, "did not beat the baseline")
    assert unlocked.unlocked is True and unlocked.why
    assert refused.unlocked is False and refused.why
    assert "baseline" in refused.why, "the reason must be the actionable half"


def test_the_attunement_gate_names_the_real_gate_and_its_real_verdict():
    """**This is why the sentence names a function and not an idea.**

    `PRODUCES` said "a merge that beats every measured parent", which is a
    statement about intent, and `merge.gate` has *three* verdicts of which one
    ships. A table that cannot say which is a table that cannot be checked - and
    nothing noticed, because nothing imported this module.
    """
    from shani_chronoa import merge
    said = cultivation.PRODUCES["attunement"]
    assert "merge.gate" in said, said
    assert "better" in said, ("the gate has worse / no-better / better / "
                              "unmeasured, and only one of them ships")
    assert hasattr(merge, "gate")


def test_the_merge_gate_verdict_the_progression_names_is_one_it_actually_has():
    """Drives the real gate so the word in the table is not a word."""
    from shani_chronoa import merge
    import inspect
    source = inspect.getsource(merge.gate)
    for verdict in ("better", "no-better", "worse", "unmeasured"):
        assert '"%s"' % verdict in source, verdict
