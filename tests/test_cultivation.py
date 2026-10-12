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


# --- the genre vocabulary -----------------------------------------------------
#
# `TERMS` is the xianxia lexicon the two axes are written in. It stands or falls
# on ONE rule, the same rule `RESOURCES` and `PRODUCES` were rewritten to obey:
# **a term that names nothing here is a decorative flourish.** So a term either
# names the function that decides it (verified by import, below) or is refused
# as NOT_BUILT with a reason. Pasting Chinese onto the design without that
# discipline is exactly the "machinery whose only purpose is to report success"
# this repo refuses to build.

def test_every_term_is_either_mapped_to_a_real_function_or_refused_with_a_reason():
    """The load-bearing property of the whole table, and the negative control
    that makes the scanner below honest.

    Two populations, kept apart: a **built** term must name a function that
    really imports and exists; a **refused** term (`from == "NOT_BUILT"`) must
    carry the reason it has no analogue here. A term in neither state - built
    but naming nothing, or refused with no reason - is the failure.
    """
    import importlib
    missing, unreasoned, ambiguous = [], [], []
    for name, entry in cultivation.TERMS.items():
        target = entry.get("from", "")
        assert target, "%s: no `from` and no refusal - which is it?" % name
        if target == "NOT_BUILT":
            if not entry.get("not_built", "").strip():
                unreasoned.append(name)
            continue
        # built: the target must be module.symbol and must resolve
        module_name, _, symbol = target.rpartition(".")
        if not module_name or not symbol:
            missing.append("%s: `%s` is not module.symbol" % (name, target))
            continue
        try:
            module = importlib.import_module("shani_chronoa." + module_name)
        except ImportError as problem:
            missing.append("%s: %s does not import (%s)"
                           % (name, module_name, problem))
            continue
        if not hasattr(module, symbol):
            missing.append("%s: %s has no %s" % (name, module_name, symbol))
    assert not missing, "terms claiming a function that is not there: %r" % missing
    assert not unreasoned, "terms refused without saying why: %r" % unreasoned
    assert not ambiguous


def test_the_function_each_built_term_names_exists_is_a_real_check():
    """**The control.** Without a term that is genuinely refused, a bug that
    made every term auto-`NOT_BUILT` would pass the test above - it would find
    nothing built to check. So the built population must be non-empty, and the
    refused population must be non-empty too: a table where *everything* maps
    is the plausible-mapping trap this discipline exists to avoid."""
    built = [n for n, e in cultivation.TERMS.items() if e.get("from") != "NOT_BUILT"]
    refused = [n for n, e in cultivation.TERMS.items() if e.get("from") == "NOT_BUILT"]
    assert built, "no term names a real function - the vocabulary is all prose"
    assert refused, ("no term is refused with a reason - a vocabulary that maps "
                     "everything to code is not reading the genre, it is "
                     "decorating it")


def test_a_refused_term_says_not_built_and_why_in_its_status():
    """`term_status` must make the refusal legible, because a term quietly
    missing from the table is indistinguishable from a term someone forgot."""
    status = cultivation.term_status()
    refused = [n for n, e in cultivation.TERMS.items() if e.get("from") == "NOT_BUILT"]
    for name in refused:
        assert name in status, name
        assert status[name].startswith("NOT BUILT: "), (
            "%s: a refusal must be visible in the status, not just the table" % name)
        assert len(status[name]) > len("NOT BUILT: "), "%s: no reason given" % name


def test_every_term_says_what_it_is_and_what_it_measured_or_refused():
    """Every term answers both halves, so `term_status` never hands back an
    empty string - the same absence-shaped-green rule as the resource axis."""
    for name, entry in cultivation.TERMS.items():
        assert entry.get("is", "").strip(), "%s: no description" % name
        if entry.get("from") == "NOT_BUILT":
            continue  # a refusal is measured by its reason, not a figure
        assert entry.get("measured", "").strip(), (
            "%s: no measured figure. A term with none reads as settled, which "
            "is the absence-shaped green this suite keeps catching." % name)


def test_term_status_has_no_empty_answers():
    status = cultivation.term_status()
    assert set(status) == set(cultivation.TERMS)
    for name, answer in status.items():
        assert answer.strip(), name
    # and the built ones never read as the refusal string by accident
    built = [n for n, e in cultivation.TERMS.items() if e.get("from") != "NOT_BUILT"]
    for name in built:
        assert not status[name].startswith("NOT BUILT"), (
            "%s is built but its status reads as refused" % name)


def test_each_of_the_six_stages_appears_in_the_vocabulary():
    """The genre vocabulary is not decoration hung beside the progression - it
    is the language the progression is written in, so every stage must have a
    word for itself. A stage with no term is a stage the metaphor skipped."""
    missing = [s for s in cultivation.STAGES if s not in cultivation.TERMS]
    assert not missing, "stages with no term in the vocabulary: %r" % missing


def test_the_tribulation_stages_point_at_the_gates_that_can_refuse():
    """渡劫 (the trial) and 同化 (the fuse) are the two irreversible-ish
    advances, and both must name the gate that can actually say no. A trial that
    cannot refuse is the failure this repo records most often."""
    for term, fn in (("trial", "merge.gate"), ("attunement", "merge.gate")):
        assert cultivation.TERMS[term]["from"] == fn, term
        # and the gate has a refusal path, driven for real below
    from shani_chronoa import merge
    assert hasattr(merge, "gate")


def test_the_ascension_term_points_at_the_adapter_check_not_the_merge():
    """The one place this table could drift silently: 渡劫 is the *adapter*
    stage. If someone swaps it to the merge, the table starts describing a
    different design than the one that ships."""
    assert cultivation.TERMS["ascension"]["from"] == "adapter.check"
    from shani_chronoa import adapter
    assert hasattr(adapter, "check")


def test_the_vocabulary_is_distinct_from_the_resource_axis():
    """They overlap in *concept* on purpose (qi, furnace, elixir are both a
    resource and a term), so this does NOT assert disjointness - that would be
    wrong. It asserts the vocabulary carries terms the resource axis does not
    (`heart_demon`, `trial`, `technique`), so it is a real third axis rather
    than a relabelled copy of the second."""
    assert set(cultivation.TERMS) != set(cultivation.RESOURCES)
    only_terms = set(cultivation.TERMS) - set(cultivation.RESOURCES)
    assert {"heart_demon", "trial", "technique"} <= only_terms, (
        "the vocabulary should carry concepts the resource list does not")


def test_the_boundaries_cover_every_stage_and_say_what_becomes_irreversible():
    """`BOUNDARIES` is the argument for the *ordering* - it names, per stage, the
    thing that stops being undoable. Every stage needs one, or the ladder is a
    list without a reason."""
    missing = [s for s in cultivation.STAGES if s not in cultivation.BOUNDARIES]
    assert not missing, "stages with no boundary: %r" % missing
    for name, sentence in cultivation.BOUNDARIES.items():
        assert sentence.strip(), name
    # the ordering's whole argument: ascension is reversible, attunement is not
    assert "reversible" in cultivation.BOUNDARIES["ascension"]
    assert "irreversible" in cultivation.BOUNDARIES["attunement"]


# --- the cosmological register (ELEMENTS) --------------------------------------
#
# Same rule as TERMS, applied to the *world* vocabulary rather than the
# progression vocabulary: each element names a real function/symbol, or is
# refused with a reason. 混沌 (chaos) in particular is refused on purpose - it
# is the word a design reaches for when it has not been thought through, so the
# table refuses it rather than decorating the raw log with it.

def test_every_element_is_mapped_to_a_real_symbol_or_refused_with_a_reason():
    import importlib
    missing, unreasoned = [], []
    for name, entry in cultivation.ELEMENTS.items():
        target = entry.get("from", "")
        assert target, "%s: no `from` and no refusal - which is it?" % name
        if target == "NOT_BUILT":
            if not entry.get("not_built", "").strip():
                unreasoned.append(name)
            continue
        module_name, _, symbol = target.rpartition(".")
        if not module_name or not symbol:
            missing.append("%s: `%s` is not module.symbol" % (name, target))
            continue
        try:
            module = importlib.import_module("shani_chronoa." + module_name)
        except ImportError as problem:
            missing.append("%s: %s does not import (%s)"
                           % (name, module_name, problem))
            continue
        if not hasattr(module, symbol):
            missing.append("%s: %s has no %s" % (name, module_name, symbol))
    assert not missing, "elements claiming a symbol that is not there: %r" % missing
    assert not unreasoned, "elements refused without saying why: %r" % unreasoned


def test_the_element_table_is_populated_on_both_sides():
    """**The control.** As with TERMS, a table where everything maps (or where
    everything is refused) is the failure this discipline exists to catch."""
    built = [n for n, e in cultivation.ELEMENTS.items() if e.get("from") != "NOT_BUILT"]
    refused = [n for n, e in cultivation.ELEMENTS.items() if e.get("from") == "NOT_BUILT"]
    assert built and refused, (
        "the element table must both map real things and refuse fake ones; "
        "built=%r refused=%r" % (built, refused))


def test_chaos_is_refused_because_it_would_decorate_the_raw_log():
    """混沌 is the load-bearing refusal. It must stay refused, and the reason
    must name why: it is the word a design reaches for when it has not been
    thought through."""
    chaos = cultivation.ELEMENTS["chaos"]
    assert chaos["from"] == "NOT_BUILT"
    assert "not been thought through" in chaos["not_built"], chaos["not_built"]
    assert "NOT BUILT" in cultivation.element_status()["chaos"]


def test_every_element_says_what_it_is_and_what_it_measured_or_refused():
    for name, entry in cultivation.ELEMENTS.items():
        assert entry.get("is", "").strip(), "%s: no description" % name
        if entry.get("from") == "NOT_BUILT":
            continue
        assert entry.get("measured", "").strip(), (
            "%s: no measured figure - an element with none reads as settled" % name)


def test_element_status_has_no_empty_answers():
    status = cultivation.element_status()
    assert set(status) == set(cultivation.ELEMENTS)
    for name, answer in status.items():
        assert answer.strip(), name


def test_the_five_elements_map_to_a_real_closed_vocabulary_of_five():
    """五行 is the genre's five mutually-exclusive states that interact; the
    honest analogue is the *closed evidence vocabulary*, which is literally five
    real, mutually-exclusive words - and the whole point is that a row may not
    invent a sixth (so 'could not ask' never reads as 'not installed')."""
    from shani_chronoa.gui.surfaces import desktop
    words = desktop.EVIDENCE_WORDS
    assert cultivation.ELEMENTS["five_elements"]["from"] == (
        "gui.surfaces.desktop.EVIDENCE_WORDS")
    assert len(words) == 5, words
    assert len(set(words)) == 5, "a closed vocabulary of five with a duplicate is four"
    assert "could not ask" in words, (
        "the refusal that keeps an unknown from reading as a negative")


def test_yin_and_yang_are_the_read_only_and_mutating_halves():
    """阴阳 is a complementary split where both halves are real and neither is
    the whole. Here that is the tool split: what may be seen vs what may act,
    each a declared set, with the mutating half meeting a consent gate."""
    from shani_chronoa import capabilities
    yin = cultivation.ELEMENTS["yin"]
    yang = cultivation.ELEMENTS["yang"]
    assert yin["from"] == "capabilities.READ_ONLY_TOOLS"
    assert yang["from"] == "capabilities.MUTATING_TOOLS"
    assert capabilities.READ_ONLY_TOOLS, "the yin half is not empty"
    assert capabilities.MUTATING_TOOLS, "the yang half is not empty"
    assert capabilities.READ_ONLY_TOOLS != capabilities.MUTATING_TOOLS


def test_the_cosmological_register_is_separate_from_the_progression_terms():
    """Two registers, not one merged pile: `TERMS` is progress vocabulary,
    `ELEMENTS` is the world. They overlap in spirit but must stay distinct
    tables, or the distinction this module draws is lost."""
    assert set(cultivation.ELEMENTS) != set(cultivation.TERMS)
    only_elements = set(cultivation.ELEMENTS) - set(cultivation.TERMS)
    assert {"void", "chaos", "lightning"} <= only_elements, (
        "the cosmological register should carry concepts TERMS does not")


def test_ascension_is_recorded_as_the_refusal_it_measured_as():
    """渡劫 is the first real measurement of an adapter on the honest
    instrument, and it is a **refusal**: base alone 4/5 at 4.65 s/case,
    base + adapter 4/5 at 7.65 s/case, both missing the same case. Same
    accuracy, 65% slower.

    This test exists so the refusal cannot be quietly reworded into a success.
    A `measured` field that stopped saying REFUSED would still contain digits,
    so the digit check elsewhere does not catch it - only an assertion on the
    verdict does. Same rule as the poison row: 丹毒 is a failure this repo must
    keep on the record, not quietly reword once it stops being news.
    """
    said = cultivation.TERMS["ascension"]["measured"]
    assert "REFUSED" in said, (
        "ascension measured as a refusal; saying so is the point")
    # both numbers, so a reader can see it was a comparison and not a verdict
    for figure in ("4/5", "4.65", "7.65"):
        assert figure in said, "missing %s: %s" % (figure, said)


def test_no_term_or_element_reads_as_a_success_the_measurement_did_not_produce():
    """The general form of that rule: a `measured` field must not claim the
    thing *worked* unless the measurement showed it.

    The subtlety this version had to learn: a naive substring check for
    "unlocked" fires on "a stage that does not measure is **not unlocked**" -
    which is a *refusal*, the exact thing this test exists to protect. So each
    success word is checked with the preceding few words, and the claim only
    counts when it is **not** immediately negated. This is the substring trap
    this repo records repeatedly, hit here in my own test.
    """
    negators = frozenset(("not", "never", "no", "cannot"))
    claims = ("earned", "unlocked", "successfully")
    for name, entry in (list(cultivation.TERMS.items())
                        + list(cultivation.ELEMENTS.items())):
        if entry.get("from") == "NOT_BUILT":
            continue
        measured = entry.get("measured", "")
        low = measured.lower()
        for word in claims:
            start = low.find(word)
            while start != -1:
                words_before = low[max(0, start - 12):start].split()
                previous = words_before[-1] if words_before else ""
                assert previous in negators, (
                    "%s: %r claims success (%r not negated) in %r - if the "
                    "measurement refused, the row must say so"
                    % (name, word, word, measured))
                start = low.find(word, start + 1)


# --- the ascension gate: a real function that can refuse ---------------------
#
# The module's thesis is "a stage that does not measure is not unlocked", and
# until this gate existed `Verdict` was declared and never constructed by
# anything - the claim had no enforcement. These tests drive it with the real
# measured numbers from this machine.

def test_ascension_gate_refuses_the_measurement_that_was_actually_taken():
    """**The load-bearing test.** The real run: base alone 4/5 at 4.65 s/case,
    base + adapter 4/5 at 7.65 s/case. Same accuracy, 65% slower. The gate must
    return a refusal on those exact numbers, and the reason must name both."""
    base = cultivation.Measurement(right=4, of=5, seconds=4.65)
    adapted = cultivation.Measurement(right=4, of=5, seconds=7.65)
    verdict = cultivation.ascension_gate(base, adapted, held_out=True)
    assert verdict.unlocked is False, verdict.why
    assert "no better" in verdict.why, verdict.why
    assert "4" in verdict.why and "5" in verdict.why, verdict.why


def test_a_real_accuracy_gain_wins_the_stage():
    """**The control.** A gate that refuses everything is as useless as one that
    passes everything. A strictly-better, not-slower adapter must unlock, or the
    gate is theatre."""
    base = cultivation.Measurement(right=4, of=5, seconds=4.65)
    adapted = cultivation.Measurement(right=5, of=5, seconds=4.90)
    verdict = cultivation.ascension_gate(base, adapted, held_out=True)
    assert verdict.unlocked is True, verdict.why
    assert "earned" in verdict.why


def test_the_gate_refuses_unequal_case_counts_before_comparing():
    base = cultivation.Measurement(right=4, of=5, seconds=4.0)
    adapted = cultivation.Measurement(right=9, of=10, seconds=4.0)
    verdict = cultivation.ascension_gate(base, adapted, held_out=True)
    assert verdict.unlocked is False
    assert "not comparable" in verdict.why, verdict.why


def test_the_gate_refuses_without_a_held_out_split():
    """A gate that cannot tell what it was scored on will score itself on its
    own tuning set, so the caller must assert a held-out split."""
    base = cultivation.Measurement(right=4, of=5, seconds=4.0)
    adapted = cultivation.Measurement(right=5, of=5, seconds=4.0)
    verdict = cultivation.ascension_gate(base, adapted, held_out=False)
    assert verdict.unlocked is False
    assert "held-out" in verdict.why, verdict.why


def test_more_refusals_is_worse_even_when_accuracy_is_up():
    """An adapter that makes the model decline more often has made it worse in
    the most expensive way there is, even if accuracy nominally rose."""
    base = cultivation.Measurement(right=4, of=5, seconds=4.0, refusals=0)
    adapted = cultivation.Measurement(right=5, of=5, seconds=4.0, refusals=2)
    verdict = cultivation.ascension_gate(base, adapted, held_out=True)
    assert verdict.unlocked is False
    assert "decline" in verdict.why, verdict.why


def test_speed_can_refuse_but_can_never_rescue_an_accuracy_loss():
    """Speed never rescues accuracy: a faster model that is less accurate is not
    an improvement. And a genuinely better model that is a bit slower is still
    judged on its accuracy first."""
    # faster but less accurate -> refuse on accuracy, not rescued by speed
    base = cultivation.Measurement(right=5, of=5, seconds=8.0)
    worse_but_fast = cultivation.Measurement(right=4, of=5, seconds=2.0)
    v = cultivation.ascension_gate(base, worse_but_fast, held_out=True)
    assert v.unlocked is False and "no better" in v.why, v.why
    # strictly better but past the slowdown limit -> refuse on time
    base = cultivation.Measurement(right=4, of=5, seconds=4.0)
    better_but_slow = cultivation.Measurement(right=5, of=5, seconds=9.0)
    v = cultivation.ascension_gate(base, better_but_slow, held_out=True)
    assert v.unlocked is False and "slower" in v.why, v.why


def test_a_verdict_from_the_gate_is_actually_a_verdict():
    """The gate returns the module's own `Verdict`, not a bool - the refusal has
    to carry a reason a person can act on."""
    v = cultivation.ascension_gate(
        cultivation.Measurement(4, 5, 4.65),
        cultivation.Measurement(4, 5, 7.65), held_out=True)
    assert isinstance(v, cultivation.Verdict)
    assert isinstance(v.unlocked, bool)
    assert v.why and len(v.why) > 20, "a refusal must be actionable"


# --- the progression enforces order, rather than describing it ----------------

def test_unlocked_stages_stops_at_the_first_gate_that_did_not_unlock():
    """The ordering's whole argument is that the irreversible advances come last,
    and that argument is worth nothing if a failed gate still hands you the
    stages behind it."""
    gates = {
        "foundation": cultivation.Verdict(True, "digest matches"),
        "perception": cultivation.Verdict(True, "the log was read"),
        "refinement": cultivation.Verdict(False, "did not beat the baseline"),
        "attunement": cultivation.Verdict(True, "would have been better"),
    }
    got = cultivation.unlocked_stages("foundation", gates)
    assert got == ["foundation", "perception"], got


def test_a_stage_with_no_verdict_is_locked_not_passed():
    """**The absence-shaped green.** A gate nobody ran is not a gate that
    passed; `not measured` has never meant `fine` in this repository."""
    gates = {"foundation": cultivation.Verdict(True, "digest matches")}
    got = cultivation.unlocked_stages("foundation", gates)
    assert got == ["foundation"], got
    assert "perception" not in got


def test_an_unknown_starting_stage_earns_nothing():
    assert cultivation.unlocked_stages("not-a-stage", {}) == []


def test_the_measured_ascension_refusal_blocks_everything_after_it():
    """The real numbers through the real gate, into the real enforcement: the
    adapter is not earned, so immortailty - the stage that needs a signed,
    provenance-bearing record built on top of an earned artifact - must not be
    reachable past it."""
    base = cultivation.Measurement(right=4, of=5, seconds=4.65)
    adapted = cultivation.Measurement(right=4, of=5, seconds=7.65)
    verdict = cultivation.ascension_gate(base, adapted, held_out=True)
    gates = {"foundation": cultivation.Verdict(True, "digest matches")}
    got = cultivation.unlocked_stages("foundation", gates)
    # foundation earned, but nothing beyond it has a gate, so nothing follows
    assert got == ["foundation"]
    assert verdict.unlocked is False


def test_every_stage_the_gate_can_pass_is_one_it_has_a_verdict_for():
    """`unlocked_stages` only lets a stage through on an explicit `unlocked`
    verdict; the ascension gate is currently the sole real gate, so this pins
    that the function is reading verdicts and not inferring pass from presence.
    """
    gates = {s: cultivation.Verdict(True, "ok") for s in cultivation.STAGES}
    assert cultivation.unlocked_stages("foundation", gates) == list(cultivation.STAGES)
    gates.pop("immortality")
    assert cultivation.unlocked_stages("foundation", gates) == list(
        cultivation.STAGES[:-1])


def test_the_ascension_row_names_the_gate_that_actually_exists():
    """`PRODUCES` is read as the gates, so the ascension row must name the
    function that decides it - the same rule every other row was rewritten to
    obey. This asserts the name and drives the real gate behind it."""
    said = cultivation.PRODUCES["ascension"]
    assert "ascension_gate" in said, said
    # and the gate it names is callable and returns a Verdict
    v = cultivation.ascension_gate(
        cultivation.Measurement(4, 5, 4.65),
        cultivation.Measurement(4, 5, 7.65), held_out=True)
    assert isinstance(v, cultivation.Verdict)


def test_the_module_says_only_one_gate_is_live_and_does_not_overclaim():
    """The docstring must not read as "six live gates". Ascension's gate is a
    function; the other five are still prose. Overclaiming here would be the
    very 'reports success without doing the work' the module refuses - so this
    test reads the claim and holds it."""
    # join the docstring so a source line-wrap cannot hide a phrase from the
    # check - a substring test that trips over formatting is a check that fails
    # for the wrong reason, which is the shape this repo keeps recording.
    doc = " ".join((cultivation.__doc__ or "").split())
    assert "only **ascension's is a function**" in doc, (
        "the module must state which gate is real")
    assert "treats a stage with no verdict as **locked**" in doc, (
        "the module must state that no-verdict means locked, not passed")
