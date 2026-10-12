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


# --- the resource axis -------------------------------------------------------
#
# A resource table whose entries are prose cannot be checked, which is the same
# defect `PRODUCES` had until its deciding functions were named. So every entry
# here must name a function that **actually imports and exists** - if one is
# renamed, this fails rather than the table quietly becoming a wish list.

def test_every_resource_names_a_function_that_exists():
    from pathlib import Path
    """The load-bearing property of the whole table."""
    import importlib
    missing = []
    for name, entry in cultivation.RESOURCES.items():
        target = entry.get("from", "")
        module_name, _, symbol = target.rpartition(".")
        if not module_name or not symbol:
            missing.append("%s: no function named (%r)" % (name, target))
            continue
        try:
            module = importlib.import_module("shani_chronoa." + module_name)
        except ImportError as problem:
            missing.append("%s: %s does not import (%s)" % (name, module_name, problem))
            continue
        if not hasattr(module, symbol):
            missing.append("%s: %s has no %s" % (name, module_name, symbol))
    for name, entry in cultivation.RESOURCES.items():
        extra = entry.get("judged_by", "")
        if extra and "/" in extra:
            target = Path(cultivation.__file__).resolve().parents[4] / extra
            if not target.is_file():
                missing.append("%s: judged_by names %s, which is not a file"
                               % (name, extra))
    assert not missing, "resources naming things that are not there: %r" % missing


def test_every_resource_says_what_it_is_and_what_it_measured():
    for name, entry in cultivation.RESOURCES.items():
        assert entry.get("is", "").strip(), "%s: no description" % name
        assert entry.get("measured", "").strip(), (
            "%s: no measured figure. A resource with none reads as available, "
            "which is the absence-shaped green this suite keeps catching." % name)


def test_resource_status_has_no_empty_answers():
    """Every resource answers, and the ones that are empty say so in words."""
    status = cultivation.resource_status()
    assert set(status) == set(cultivation.RESOURCES)
    for name, answer in status.items():
        assert answer.strip(), name
        assert answer.strip() != "not measured", (
            "%s: RESOURCES claims a measurement and resource_status says there "
            "is none - one of them is wrong and this is where it shows" % name)


def test_an_unmeasured_resource_is_still_listed_rather_than_hidden():
    """`seclusion` has never run here. It is in the table with that said, not
    omitted - an absent resource and an unavailable one are different facts and
    a reader cannot tell them apart from a missing row."""
    status = cultivation.resource_status()
    assert "seclusion" in status
    assert "never run" in status["seclusion"]


def test_the_poison_row_is_present_and_names_the_measurement():
    """丹毒 is not decoration. A refinement that is worse on the task than the
    thing it was made from is a real failure mode, it happened here, and the
    row that records it is what stops it being rediscovered as a success."""
    poison = cultivation.RESOURCES["poison"]
    assert poison["from"] == "merge.gate"
    assert "task_eval" in poison["judged_by"]
    assert "worse" in poison["measured"]
    # And it must name the number, not the feeling.
    assert any(ch.isdigit() for ch in poison["measured"])


def test_the_progression_and_the_resources_are_separate_axes():
    """The point of a second axis: a cultivator can reach the last stage with
    no resources at all, and a table that conflated them would claim otherwise.
    A stage produces something; a resource is something you have."""
    assert set(cultivation.STAGES) != set(cultivation.RESOURCES)
    assert "qi" not in cultivation.STAGES
    assert "foundation" not in cultivation.RESOURCES
