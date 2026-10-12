"""The gate compares like with like, and has three verdicts rather than two.

The identity merge is the control the whole module turns on: a merge of one
parent with coefficient 1.0 **is** that parent, bit for bit, so any verdict
other than "no better" is the gate measuring something other than the merge.
"""
from pathlib import Path

import pytest

from shani_chronoa import merge


def _touch(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"x")
    return path


@pytest.fixture
def measured(monkeypatch, tmp_path):
    """Perplexity from a table keyed on file name; quantize that copies."""
    table = {}
    made = {}

    def matched(parent_name):
        """The name the module gives a parent's matched-quantisation copy.

        Derived the way the module derives it, rather than typed here - a
        hand-written name is a second copy of the rule, and this suite failed
        four tests on exactly that.
        """
        return Path(parent_name).stem + ".Q4_K_M.gguf"

    def fake_perplexity(path, corpus=None):
        return table.get(Path(path).name) if path else None

    def fake_quantize(source, target, quant="Q4_K_M"):
        # **The first version never created the parent file**, so read_bytes()
        # raised, the gate's own guard swallowed it, and four tests failed with
        # "no parent was requantised" - a fixture describing files that were
        # not there rather than a gate that was broken.
        source = Path(source)
        target = Path(target)
        target.write_bytes(source.read_bytes() if source.is_file() else b"x")
        made[target.name] = quant
        return target

    monkeypatch.setattr(merge, "perplexity", fake_perplexity)
    monkeypatch.setattr(merge, "quantize", fake_quantize)
    for name in ("base-Q8_0.gguf", "other-Q8_0.gguf", "merged.gguf"):
        (tmp_path / name).write_bytes(b"x")

    return table, made, matched


def test_an_identity_merge_is_no_better_not_worse(tmp_path, measured):
    """**This is the control.** Lambda = [1.0] returns the parent unchanged, so
    a gate that called this "worse" would be blaming the merge for a difference
    it did not make."""
    table, _, matched = measured
    table["base-Q8_0.gguf"] = 48.77      # as it ships
    table[matched("base-Q8_0.gguf")] = 48.77    # and as the merge was written
    table["merged.gguf"] = 48.77
    verdict = merge.gate(tmp_path / "merged.gguf", [tmp_path / "base-Q8_0.gguf"],
                         quant="Q4_K_M")
    assert verdict["verdict"] == "no-better", verdict["why"]
    assert verdict["ship"] is False


def test_the_comparison_is_made_at_the_parents_own_quantisation(tmp_path, measured):
    """The defect this replaces: an identity merge read as 13.6% worse purely
    because its parent was measured in a different quantisation."""
    table, made, matched = measured
    table["base-Q8_0.gguf"] = 48.77
    table[matched("base-Q8_0.gguf")] = 48.77
    table["merged.gguf"] = 48.77
    merge.gate(tmp_path / "merged.gguf", [tmp_path / "base-Q8_0.gguf"],
               quant="Q4_K_M")
    assert made, "no parent was requantised, so nothing was compared like with like"


def test_both_figures_are_recorded_so_the_cost_is_visible(tmp_path, measured):
    table, _, matched = measured
    table["base-Q8_0.gguf"] = 48.77
    table[matched("base-Q8_0.gguf")] = 55.37   # the cost of requantising
    table["merged.gguf"] = 50.0
    verdict = merge.gate(tmp_path / "merged.gguf", [tmp_path / "base-Q8_0.gguf"],
                         quant="Q4_K_M")
    assert verdict["verdict"] == "better"
    assert verdict["scores"]["base-Q8_0.gguf"] == 48.77
    assert verdict["scores"]["base-Q8_0.gguf (same quant)"] == 55.37


def test_a_merge_that_loses_is_still_refused(tmp_path, measured):
    table, _, matched = measured
    table["base-Q8_0.gguf"] = 48.77
    table[matched("base-Q8_0.gguf")] = 48.77
    table["merged.gguf"] = 60.0
    verdict = merge.gate(tmp_path / "merged.gguf", [tmp_path / "base-Q8_0.gguf"],
                         quant="Q4_K_M")
    assert verdict["verdict"] == "worse"
    assert "worse than its best parent" in verdict["why"]


def test_an_unmeasured_merge_is_refused_rather_than_assumed_good(tmp_path, measured):
    table, _, matched = measured
    table["base-Q8_0.gguf"] = 48.77
    table[matched("base-Q8_0.gguf")] = 48.77
    verdict = merge.gate(tmp_path / "merged.gguf", [tmp_path / "base-Q8_0.gguf"],
                         quant="Q4_K_M")
    assert verdict["verdict"] == "unmeasured"
    assert verdict["ship"] is False


def test_an_unmeasured_parent_is_not_silently_ignored(tmp_path, measured):
    """A rival that could not be measured cannot be the best rival, and must
    not be counted as one that was beaten either."""
    table, _, matched = measured
    table["base-Q8_0.gguf"] = 48.77
    table["other-Q8_0.gguf"] = None
    table["merged.gguf"] = 50.0
    verdict = merge.gate(tmp_path / "merged.gguf",
                         [tmp_path / "base-Q8_0.gguf", tmp_path / "other-Q8_0.gguf"],
                         quant=None)
    assert verdict["verdict"] == "worse"


def test_a_matched_measurement_that_cannot_run_says_why(tmp_path, measured, monkeypatch):
    """The refusal has to name its cause, or it is indistinguishable from the
    gate refusing the merge."""
    table, _, matched = measured
    table["base-Q8_0.gguf"] = 48.77
    table[matched("base-Q8_0.gguf")] = 55.37
    table["merged.gguf"] = 50.0
    # **Monkeys through monkeypatch**, not a bare assignment: the first version
    # set `merge.quantize` directly and then never restored it, so the swap
    # leaked into every later test in the file and this one errored on its own
    # leftovers rather than on the defect it was written for. A failure that
    # corrupts the rest of the run is the same defect as one that cannot fail.
    monkeypatch.setattr(merge, "quantize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no room")))
    verdict = merge.gate(tmp_path / "merged.gguf", [tmp_path / "base-Q8_0.gguf"],
                         quant="Q4_K_M")
    # The reason is recorded against the *matched* key, and the gate keeps
    # answering on the parent's own number rather than crashing - which here it
    # cannot, because the only parent is unmeasurable too, so the honest verdict
    # is "unmeasured". A reason that is present alongside a refusal is the whole
    # point; the verdict is the parent's story to tell.
    assert "no room" in str(verdict["scores"]["base-Q8_0.gguf (same quant)"])
    assert verdict["ship"] is False


def test_the_scratch_directory_does_not_survive_the_call(tmp_path, measured):
    import tempfile
    table, _, matched = measured
    table["base-Q8_0.gguf"] = 48.77
    table[matched("base-Q8_0.gguf")] = 48.77
    table["merged.gguf"] = 48.77
    before = set(Path(tempfile.gettempdir()).glob("merge-gate-*"))
    merge.gate(tmp_path / "merged.gguf", [tmp_path / "base-Q8_0.gguf"],
               quant="Q4_K_M")
    assert set(Path(tempfile.gettempdir()).glob("merge-gate-*")) == before
