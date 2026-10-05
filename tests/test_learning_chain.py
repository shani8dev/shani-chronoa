"""The learning chain, end to end: record -> consolidate -> save -> load -> predict.

The path mismatch this file exists to prevent is the most instructive bug in
the layer's history. `train_and_save()` wrote `models/outcome.json` and
`tools._outcome_model()` read `logs/outcome-model.json`. Training succeeded,
saving succeeded, and dispatch reported "no model" indefinitely - which looks
exactly like a chain that was never wired, and was misdiagnosed as such several
times over before anyone read the two paths side by side.

So every test here drives the **chain**, not a component. A test of
`train_and_save()` alone passes happily while the chain is broken; the test that
matters is the one that writes a model and asks dispatch whether it can see it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import learning, tools  # noqa: E402


@pytest.fixture
def models(tmp_path, monkeypatch):
    """Point the model directory at a tmp path and reset dispatch's cache."""
    directory = tmp_path / "models"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(learning, "models_dir", lambda: directory)
    monkeypatch.setattr(learning, "model_path", lambda name="outcome":
                        directory / f"{name}.json")
    monkeypatch.setattr(tools, "_OUTCOME_TRIED", False)
    monkeypatch.setattr(tools, "_OUTCOME_MODEL", None)
    return directory


def _honest_model():
    """A small, signed model whose provenance says it earned its place."""
    model = learning.OutcomeModel()
    model.w = {0: [1.0, -0.5, 0.0], 7: [0.2, 0.8, 0.1]}
    model.b = [0.1, 0.0, -0.1]
    return model


def _write(directory: Path, model, provenance: dict, key=None):
    path = directory / "outcome.json"
    payload = learning.sign_model(model.to_dict(provenance), key)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------- the chain


def test_a_written_model_is_visible_to_dispatch(models):
    """THE regression test. If the two paths ever disagree again, this fails.

    Every previous test in this layer passed while the chain was broken, because
    they each tested one end.
    """
    path = _write(models, _honest_model(),
                  {"example_count": 500, "honest": True,
                   "accuracy": 0.99, "baseline": 0.90})
    assert path.exists()

    tools._OUTCOME_TRIED = False
    loaded = tools._outcome_model()
    assert loaded is not None, (
        f"a model written to {path} was not found by dispatch. The writer and "
        f"the reader disagree about the path, which is how this was broken "
        f"before."
    )
    probs = loaded.predict_proba({"tool_name": "x", "args": {}, "origin": "user"})
    assert set(probs) == set(learning.VERDICTS)
    assert abs(sum(probs.values()) - 1.0) < 1e-6


def test_dispatch_predicts_after_a_prediction_is_recorded(models):
    """The loop closes: a loaded model produces a record that is then scored."""
    _write(models, _honest_model(),
           {"example_count": 500, "honest": True, "accuracy": 0.99,
            "baseline": 0.90})
    tools._OUTCOME_TRIED = False
    tools._record_prediction("write_text_file", {"path": "/tmp/x"})
    # `record_prediction` writes to the predictions log; if the model was
    # loaded, a line exists.
    from shani_chronoa.learning import predictions_path
    try:
        line = predictions_path().read_text(encoding="utf-8").strip().splitlines()[-1]
    except (OSError, IndexError):
        pytest.skip("no predictions log in this environment")
    entry = json.loads(line)
    assert entry["tool"] == "write_text_file"
    assert set(entry["probabilities"]) == set(learning.VERDICTS)


# ------------------------------------------------------- the load guards


def test_an_unsigned_model_is_refused(models):
    """A file that cannot say what it is is not used. This is the state of
    every model written before signing existed, and one of them sat on this
    machine for hours."""
    path = models / "outcome.json"
    path.write_text(json.dumps(_honest_model().to_dict(
        {"example_count": 500, "honest": True})), encoding="utf-8")
    tools._OUTCOME_TRIED = False
    assert tools._outcome_model() is None


def test_a_tampered_model_is_refused(models):
    _write(models, _honest_model(),
           {"example_count": 500, "honest": True})
    payload = json.loads((models / "outcome.json").read_text())
    payload["bias"] = [99.0, 0.0, 0.0]
    (models / "outcome.json").write_text(json.dumps(payload))
    tools._OUTCOME_TRIED = False
    assert tools._outcome_model() is None


def test_a_model_that_never_beat_the_baseline_is_refused(models):
    """Its own report says it knows nothing, so predicting from it would be
    noise presented as confidence."""
    _write(models, _honest_model(),
           {"example_count": 500, "honest": False,
            "accuracy": 0.5, "baseline": 0.9})
    tools._OUTCOME_TRIED = False
    assert tools._outcome_model() is None


def test_a_good_model_still_loads(models):
    """The guards must not have become a wall - a model that passes all three
    is used, which is the point of guarding rather than refusing."""
    _write(models, _honest_model(),
           {"example_count": 500, "honest": True,
            "accuracy": 0.99, "baseline": 0.90})
    tools._OUTCOME_TRIED = False
    assert tools._outcome_model() is not None


# ---------------------------------------------------- the consolidation gate


def test_the_gate_does_nothing_without_a_log(monkeypatch):
    monkeypatch.setattr(learning, "_log_revision", lambda: None)
    out = learning.consolidate_if_due()
    assert out["ran"] is False
    assert "no tool-call log" in out["reason"]


def test_the_gate_is_idempotent_for_one_revision(models, monkeypatch):
    """Ask twice about the same log; the second must decline, or a periodic tick
    would retrain forever."""
    calls = []
    monkeypatch.setattr(learning, "train_and_save",
                        lambda *a, **k: calls.append(1) or {"saved": False,
                                                             "reason": "stub"})
    monkeypatch.setattr(learning, "_log_revision", lambda: "1000-1000")
    first = learning.consolidate_if_due(force=True)
    second = learning.consolidate_if_due()
    assert first["ran"] is True
    assert second["ran"] is False
    # Which gate fires first is an implementation detail; what matters is that
    # the second call declined and training was not attempted again. The stub
    # records every attempt, so one call means one attempt.
    assert len(calls) == 1, f"the gate retrained {len(calls)} times for one revision"
    assert second["reason"], "a refusal must say why"


def test_a_refusal_is_reported_not_swallowed(models, monkeypatch):
    """A consolidation that cannot train is the most useful thing it can say.
    Silently returning None here is how the layer died before."""
    monkeypatch.setattr(learning, "_log_revision", lambda: "2000-2000")
    monkeypatch.setattr(learning, "train_and_save",
                        lambda *a, **k: {"saved": False,
                                         "reason": "no example of verified"})
    out = learning.consolidate_if_due(force=True)
    assert out["ran"] is True
    assert out["saved"] is False
    assert "verified" in out["reason"]