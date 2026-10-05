"""The outcome model: does it learn, and can a useless model pass the gate?

Every test here was written after a measurement contradicted something the code
claimed. Each one guards a specific failure that was real on this machine's own
log (11,636 calls: 10,936 `unverified`, 351 `verified`, 349 `failed`):

- **`OutcomeModel().fit()` raised.** `__init__` defaulted `self.w` to `None`
  while `fit()` read `self.w.get(...)`, so every fit from scratch died on its
  first line and `train_and_report()` crashed instead of reporting. A crash is
  not a refusal: the report that would have said "no signal" never appeared.
- **Class balancing was missing**, and without it the model predicted
  `unverified` for all 11,636 calls - precision and recall exactly 0.000 on both
  minority verdicts.
- **Balanced weights were not normalised**, so every step was ~1e-7 and the
  fitted weights landed near 1e-4 - where `to_dict`'s `round(v, 6)` stored them
  as **zeros**. The file verified its own digest and predicted 0.3333 for all
  three verdicts on every input.
- **The time-ordered split put zero `verified` in the training half**, because
  verdicts are only recorded partway through the log.
- **A vector-respecting split still starved `verified`** (32 train / 319 test),
  because all 1,979 `verified` calls live in just 3 distinct feature vectors.
- **Recall lift alone is gameable**: `verified` is ~3% of the data, so flagging
  *every* call as verified scores ~33x detection while being useless.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import learning, tools  # noqa: E402


def _examples(rows):
    """(feature names, label) pairs -> Examples, the way `load()` builds them."""
    out = []
    for names, label in rows:
        vector = [0.0] * learning._N_FEATURES
        for name in names:
            vector[learning._index(name)] = 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        active = tuple((i, v / norm) for i, v in enumerate(vector) if v)
        out.append(learning.Example(active=active, y=label))
    return out


VERIFIED, FAILED, UNVERIFIED = 1, 2, 0


def _log(per_class=20, distinct=8):
    """A log where every verdict has plenty of examples and several vectors."""
    rows = []
    for label, tag in ((UNVERIFIED, "plain"), (VERIFIED, "good"), (FAILED, "bad")):
        for copy in range(per_class):
            for shape in range(distinct):
                rows.append(([f"tool={tag}-{shape}", "origin=user", "bias"], label))
    return _examples(rows)


@pytest.fixture
def models(tmp_path, monkeypatch):
    directory = tmp_path / "models"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(learning, "models_dir", lambda: directory)
    monkeypatch.setattr(tools, "_OUTCOME_TRIED", False)
    monkeypatch.setattr(tools, "_OUTCOME_MODEL", None)
    return directory


# ---------------------------------------------------------------------------
# it can fit at all
# ---------------------------------------------------------------------------


def test_a_model_can_be_fitted_from_scratch():
    """The crash: `self.w` was `None` where `fit()` expected a dict."""
    model = learning.OutcomeModel()
    assert model.w == {}, "an unfitted model must present an empty index map"
    model.fit(_log())
    assert model.w, "fit produced no weights at all"


def test_balanced_weights_do_not_shrink_the_step_to_nothing():
    """Unnormalised inverse-class-frequency weights made every update ~1e-7."""
    examples = _log(per_class=40, distinct=6)
    model = learning.OutcomeModel().fit(examples)
    biggest = max(abs(v) for row in model.w.values() for v in row)
    assert biggest > 1e-3, f"weights collapsed to {biggest:.2e}, which serialises to zero"


def test_a_model_round_trips_through_a_file_with_its_signal_intact(tmp_path):
    """`round(v, 6)` stored the whole model as zeros."""
    model = learning.OutcomeModel().fit(_log())
    target = tmp_path / "outcome.json"
    learning.sign_model(model.to_dict({}))
    target.write_text(__import__("json").dumps(
        learning.sign_model(model.to_dict({}))), encoding="utf-8")
    back = learning.OutcomeModel.load(target)
    assert back.w == model.w
    record = {"tool_name": "good-0", "args": {}, "origin": "user"}
    probs = model.predict_proba(record)
    assert max(probs.values()) - min(probs.values()) > 1e-6, \
        "the saved model predicts one number for everything"


def _imbalanced(minority=30, majority=180):
    """A 6:1 log whose only signal is a marker carried by the minority class.

    `verdict` labels are carried by an argument-shaped feature, which is what
    the real log looks like: 97% of calls share nearly all of their features, and
    the minority is separable only by the rest.
    """
    rows = [(["origin=user", "bias", f"argstr=plain.m:{i % 8}"], UNVERIFIED)
            for i in range(majority)]
    rows += [(["origin=user", "bias", f"argstr=good.m:{i % 3}"], VERIFIED)
             for i in range(minority)]
    return _examples(rows)


def test_an_unweighted_fit_never_finds_a_minority_class():
    """The measured failure: 0.000 recall on all 700 minority calls, real log."""
    examples = _imbalanced()
    report = learning.evaluate(
        learning.OutcomeModel().fit(examples, balance=False), examples[-20:])
    assert report.per_class["verified"]["recall"] == 0.0


def test_balancing_is_what_makes_a_minority_class_reachable():
    examples = _imbalanced()
    report = learning.evaluate(
        learning.OutcomeModel().fit(examples, balance=True), examples[-20:])
    assert report.per_class["verified"]["recall"] > 0.5, \
        "a class-balanced fit should reach a minority class an unweighted one cannot"


def test_balancing_moves_the_minority_feature_further():
    """The mechanism, so the test above is about cause and not luck."""
    examples = _imbalanced()
    marker = learning._index("argstr=good.m:0")
    plain = learning.OutcomeModel().fit(examples, epochs=1, balance=False)
    balanced = learning.OutcomeModel().fit(examples, epochs=1, balance=True)
    assert balanced.w[marker][VERIFIED] > plain.w[marker][VERIFIED], \
        "balancing should pull the minority feature further toward its own class"


def test_a_class_below_the_minimum_is_dropped_not_mislabelled():
    """`_MIN_CLASS` is why a tiny fixture cannot demonstrate anything."""
    tiny = _examples([(["origin=user", "bias", f"tool=x-{i}"], VERIFIED)
                      for i in range(5)]
                     + [(["origin=user", "bias", f"tool=y-{i}"], UNVERIFIED)
                        for i in range(40)])
    model = learning.OutcomeModel().fit(tiny)
    assert model.w, "the majority should still train"
    assert max(abs(v) for row in model.w.values() for v in row) > 0


# ---------------------------------------------------------------------------
# the split may not leak, and may not starve a class
# ---------------------------------------------------------------------------


def test_the_split_keeps_every_feature_vector_on_one_side():
    train, held, _note = learning.split_examples(_log())
    assert train and held
    assert not ({e.active for e in train} & {e.active for e in held})


def test_the_split_gives_every_verdict_examples_on_both_sides():
    """The measured failure: 8,462 `unverified`, 265 `failed`, zero `verified`."""
    train, held, note = learning.split_examples(_log())
    for label in (UNVERIFIED, VERIFIED, FAILED):
        assert any(e.y == label for e in train), f"training half has no {learning.VERDICTS[label]}"
        assert any(e.y == label for e in held), f"held-out half has no {learning.VERDICTS[label]}"
    assert "no training example" not in note


def test_a_vector_held_out_is_never_counted_as_a_moved_example():
    """An earlier guard reported 2,827 moves and left 35 shared vectors in place."""
    train, held, _ = learning.split_examples(_log())
    shared = {e.active for e in train} & {e.active for e in held}
    assert not shared


def test_too_little_data_refuses_rather_than_measuring_nothing():
    """The other documented outcome of `split_examples`: an early, stated return."""
    one = [learning.Example(active=((0, 1.0),), y=UNVERIFIED)]
    train, held, note = learning.split_examples(one, 0.25)
    assert train and not held
    assert note == "held nothing out", "the early return must say so"


# ---------------------------------------------------------------------------
# cross-validation, because a single split cannot answer this
# ---------------------------------------------------------------------------


def test_cross_validation_never_leaks_a_vector():
    """Folds are over vectors, so no example's vector appears in its own training."""
    examples = _log()
    seen = []
    original = learning.OutcomeModel.fit

    def spy(self, ex, **kwargs):
        seen.append({e.active for e in ex})
        return original(self, ex, **kwargs)

    learning.OutcomeModel.fit = spy
    try:
        report = learning.cross_validate(examples, folds=5)
    finally:
        learning.OutcomeModel.fit = original
    assert report.n == len(examples)
    assert len(seen) == 5, "five folds should fit five models"


def test_cross_validation_counts_every_example_exactly_once():
    report = learning.cross_validate(_log(), folds=5)
    assert report.n == len(_log())


def test_the_report_says_which_evaluation_it_used(tmp_path, monkeypatch):
    """A model whose provenance does not say how it was measured is a claim."""
    log = tmp_path / "tool_calls.log"
    import json
    rows = []
    for label, tag in ((UNVERIFIED, "plain"), (VERIFIED, "good"), (FAILED, "bad")):
        for copy in range(30):
            for shape in range(6):
                rows.append(json.dumps({"tool_name": f"{tag}-{shape}", "args": {},
                                        "origin": "user", "verdict":
                                        learning.VERDICTS[label], "result": "x",
                                        "evidence": "", "ran": True}))
    log.write_text("\n".join(rows), encoding="utf-8")
    out = learning.train_and_save(path=log, dry_run=True)
    provenance = out.get("provenance") or {}
    assert "cross-validation" in str(provenance.get("evaluation", ""))
    assert "recall_lift" in provenance and "precision_lift" in provenance


# ---------------------------------------------------------------------------
# the gate: a useless model must not pass
# ---------------------------------------------------------------------------


def _report(per_class, n=1000):
    return learning.Report(n, 0.9, 0.5, per_class, {})


def test_flagging_everything_as_the_minority_class_does_not_pass():
    """The gameable metric. `verified` is 3% of the data, so recall lift for a
    model that says `verified` every time is ~33x - and it is useless."""
    report = _report({
        "unverified": {"precision": 0.0, "recall": 0.0, "support": 970},
        "verified": {"precision": 0.03, "recall": 1.0, "support": 30},
        "failed": {"precision": 0.0, "recall": 0.0, "support": 0},
    })
    assert report.detection("verified") > 20, "the premise: this metric is inflated"
    assert report.precision_lift("verified") < 2.0
    assert not report.detected("verified")
    assert not report.honest()


def test_a_genuinely_useful_flag_does_pass():
    report = _report({
        "unverified": {"precision": 0.9, "recall": 0.7, "support": 900},
        "verified": {"precision": 0.30, "recall": 0.50, "support": 60},
        "failed": {"precision": 0.0, "recall": 0.0, "support": 40},
    })
    assert report.detected("verified")
    assert report.honest()


def test_high_recall_with_no_precision_is_not_detection():
    report = _report({
        "unverified": {"precision": 0.5, "recall": 0.9, "support": 900},
        "failed": {"precision": 0.02, "recall": 0.9, "support": 50},
        "verified": {"precision": 0.0, "recall": 0.0, "support": 50},
    })
    assert report.detection("failed") >= 18
    assert not report.detected("failed")
    assert not report.honest()


def test_finding_one_call_in_fifty_is_not_detection():
    """Recall floor: good precision on almost nothing is not a working model."""
    report = _report({
        "unverified": {"precision": 0.9, "recall": 0.8, "support": 900},
        "failed": {"precision": 0.9, "recall": 0.02, "support": 100},
        "verified": {"precision": 0.0, "recall": 0.0, "support": 0},
    })
    assert report.precision_lift("failed") > 8
    assert not report.detected("failed")


def test_the_rendered_report_shows_both_lifts():
    """A reader given only accuracy would read 94% as success."""
    text = learning.render_outcome(_report({
        "unverified": {"precision": 0.9, "recall": 0.9, "support": 900},
        "verified": {"precision": 0.3, "recall": 0.5, "support": 60},
        "failed": {"precision": 0.0, "recall": 0.0, "support": 40},
    }))
    assert "recall" in text and "precision" in text
    assert "detected" in text


# ---------------------------------------------------------------------------
# the chain: a saved model the dispatch path can see
# ---------------------------------------------------------------------------


def test_a_saved_honest_model_is_visible_to_dispatch(models):
    """The outcome layer's original bug: trained, saved, and invisible."""
    from shani_chronoa import learning as core
    model = core.OutcomeModel().fit(_log())
    report = core.evaluate(model, _log())
    assert report.detected("verified") or report.honest(), \
        "the fixture should produce something worth saving"
    path = core.sign_model(model.to_dict({"honest": report.honest()}),
                           None)
    target = models / "outcome.json"
    target.write_text(__import__("json").dumps(path), encoding="utf-8")
    assert tools._outcome_model() is not None
    assert tools._outcome_model().w == model.w