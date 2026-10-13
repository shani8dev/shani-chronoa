"""The zoo report said "Use RandomForest" with no way to use it.

`model_zoo.recommend()` ends in a bare imperative:

    Use {best.name}: {best.note}. It is the cheapest model that clears the bar (...).

Measured with the built-in model failing the bar and a forest clearing it, the
whole sentence was:

    Use RandomForest: .... It is the cheapest model that clears the bar
    (93.0% against a 85.0% baseline, minority recall 41.0%).

It named **none** of *still running the built-in model*, *no control switches it*,
or *the built-in failed and that is why*. The panel hosting this line says
*"nothing here changes a setting"* - so the reader is left holding an instruction
with no reachable outcome, while Chronoa quietly keeps the model that **failed**
the bar.

## Why this branch is the one that matters

`CANDIDATES[0]` is `builtin-logistic`, and `recommend()` sorts by that order, so
the built-in always comes first among the models that clear the bar. On the data
on this machine it clears, so the recommendation is the built-in — already in
service. **That is why the bare sentence is right by accident today and wrong the
moment the labels improve**, which is the whole point of the comparison being
re-runnable after post-conditions land. So both directions are asserted.

## `best_available()` stays uncalled, on purpose

It re-runs `compare()` on every call — training every candidate — and the
prediction path is in another session's file. `recommend()` answers the question a
caller actually had ("is the recommendation already in effect?") without training
anything. Recorded in the function's own docstring so the next orphan sweep does
not re-derive it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import model_zoo as Z  # noqa: E402


class _R:
    """A `Result`-shaped stand-in, so the table order can be driven."""

    def __init__(self, name, accuracy, baseline, minority_recall, **over):
        self.name = name
        self.accuracy = accuracy
        self.baseline = baseline
        self.minority_recall = minority_recall
        self.available = True
        self.beats_baseline = True
        self.note = f"note for {name}"
        self.__dict__.update(over)


def test_the_builtin_is_the_first_candidate():
    """The whole branch hinges on this. Asserted so a reordering of the zoo table
    cannot quietly turn the confirmation below into a switch."""
    assert Z.CANDIDATES[0].name == Z._BUILTIN
    assert Z._BUILTIN == "builtin-logistic"


def test_when_the_builtin_wins_it_says_it_is_already_running():
    said = Z.recommend([_R(Z._BUILTIN, 0.88, 0.835, 0.24),
                        _R("RandomForest", 0.93, 0.835, 0.41)])

    assert "already running" in said, said
    assert said.startswith("Keep "), (
        f"the recommendation is the model in service, so it is a confirmation and "
        f"'Use' implies a switch that does not exist: {said!r}")


def test_when_an_exotic_model_wins_it_says_that_nothing_switches_it():
    """**The branch the defect was in.**"""
    said = Z.recommend([_R(Z._BUILTIN, 0.80, 0.835, 0.0),
                        _R("RandomForest", 0.93, 0.835, 0.41)])

    assert "RandomForest" in said, said
    for phrase in ("still running", "nothing in the interface switches it",
                   "not an action"):
        assert phrase in said, (
            f"the recommendation omits {phrase!r}, so it reads as an instruction "
            f"with no reachable outcome: {said!r}")
    assert Z._BUILTIN in said, (
        f"the sentence does not say which model is in service instead: {said!r}")


def test_the_exotic_branch_does_not_claim_the_builtin_is_good():
    """The built-in *failed* the bar in this branch. Saying only 'still running' and
    not why would leave the reader to assume the running model is the safe one."""
    said = Z.recommend([_R(Z._BUILTIN, 0.80, 0.835, 0.0),
                        _R("RandomForest", 0.93, 0.835, 0.41)])

    assert "labels" in said and "post-condition" in said, (
        f"the sentence does not point at what would actually change the answer: {said!r}")


def test_the_winners_measurement_survives_in_both_branches():
    """The numbers are the point of the report. The added prose must not have
    displaced them - and they must be the **winner's**, not a fixed set.

    The first version asserted `93.0%` and `41.0%` in both branches, which failed
    against correct code: in the first branch the built-in wins at 88.0% and
    24.0%, and quoting the forest's figures there would report a measurement of a
    model that was not recommended.
    """
    said = Z.recommend([_R(Z._BUILTIN, 0.88, 0.835, 0.24),
                        _R("RandomForest", 0.93, 0.835, 0.41)])
    assert "88.0%" in said and "24.0%" in said, said
    assert "93.0%" not in said, (
        f"the loser's figures are quoted in place of the winner's: {said!r}")

    said = Z.recommend([_R(Z._BUILTIN, 0.80, 0.835, 0.0),
                        _R("RandomForest", 0.93, 0.835, 0.41)])
    assert "93.0%" in said and "41.0%" in said, said
    for said in (Z.recommend([_R(Z._BUILTIN, 0.88, 0.835, 0.24)]),
                 Z.recommend([_R(Z._BUILTIN, 0.80, 0.835, 0.0),
                              _R("RandomForest", 0.93, 0.835, 0.41)])):
        assert "83.5%" in said, f"the baseline is missing from: {said!r}"


def test_nothing_clearing_the_bar_is_still_answered():
    """The other branch, unchanged: the fix is about the imperative, not about
    replacing the honest negative result."""
    said = Z.recommend([_R(Z._BUILTIN, 0.80, 0.835, 0.0, beats_baseline=False)])

    assert "None of the available models" in said, said
    assert "post-conditions, not a bigger model" in said, said


def test_best_available_stays_uncalled_for_a_stated_reason():
    """The orphan is closed in the report, not the predictor. This asserts the
    *reason* is written down, so the next sweep reads it instead of re-deriving it."""
    source = (ROOT / "usr/lib/shani-chronoa/shani_chronoa/model_zoo.py").read_text(
        encoding="utf-8")

    start = source.index("def best_available(")
    doc = source[start:source.index('"""', source.index('"""', start) + 3)]
    for phrase in ("Deliberately not called", "compare()", "learning.py"):
        assert phrase in doc, (
            f"best_available()'s docstring no longer says {phrase!r}, so the next "
            f"orphan sweep will have to re-derive why it stays unwired")
