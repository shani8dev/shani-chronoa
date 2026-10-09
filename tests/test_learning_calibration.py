"""Calibration of the outcome model, and pairing predictions with their calls.

Both were measured on this machine's log (17,891 labelled calls) before being
written:

- **The class-balanced model's probabilities were far from honest**: expected
  calibration error 0.430 on held-out logits. A fitted temperature and per-class
  shift (fitted on out-of-fold logits, scored by cross-fitting) brought it to
  0.107, with NLL 0.938 -> 0.547 and Brier 0.561 -> 0.272.
- **`score_predictions` paired every prediction with the first call of its tool
  ever logged**: it "sorted" with a constant key and never consumed a call.
"""

import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import learning  # noqa: E402


def _sample(n, true_scale, seed=0):
    """Logits whose real class probabilities are softmax(z * true_scale)."""
    rng = random.Random(seed)
    logits, labels = [], []
    for _ in range(n):
        z = [rng.gauss(0, 1.5) for _ in range(3)]
        p = learning._exp([v * true_scale for v in z])
        r, acc, y = rng.random(), 0.0, 2
        for c, pc in enumerate(p):
            acc += pc
            if r < acc:
                y = c
                break
        logits.append(z)
        labels.append(y)
    return logits, labels


def test_overconfident_logits_get_a_temperature_above_one():
    # The model's logits are 3x too sharp: the true probabilities use z / 3.
    logits, labels = _sample(4000, 1 / 3)
    fit = learning.fit_calibration(logits, labels, prior_shift=False)
    assert fit["fitted"]
    assert 2.4 < fit["temperature"] < 3.8
    before = learning.calibration_metrics([learning.calibrated(z) for z in logits], labels)
    after = learning.calibration_metrics(
        [learning.calibrated(z, fit["temperature"], fit["shift"]) for z in logits], labels)
    assert after["ece"] < before["ece"] / 2
    assert after["nll"] < before["nll"]


def test_already_calibrated_logits_are_left_alone():
    # Negative control: the true probabilities ARE softmax(z), so T should stay
    # near 1 and the fit must not invent a correction.
    logits, labels = _sample(4000, 1.0, seed=1)
    fit = learning.fit_calibration(logits, labels, prior_shift=False)
    assert 0.85 < fit["temperature"] < 1.15 or not fit["fitted"]


def test_prior_shift_recovers_base_rates_a_balanced_fit_removed():
    # Logits carry no class preference (as a class-balanced fit's do), but the
    # data is 90% class 0: the shift must put that prior back.
    rng = random.Random(2)
    logits = [[rng.gauss(0, 0.1) for _ in range(3)] for _ in range(2000)]
    labels = [0 if rng.random() < 0.9 else rng.choice([1, 2]) for _ in logits]
    fit = learning.fit_calibration(logits, labels)
    assert fit["fitted"]
    p = learning.calibrated(logits[0], fit["temperature"], fit["shift"])
    assert 0.8 < p[0] < 0.97


def test_too_little_data_is_identity():
    fit = learning.fit_calibration([[1.0, 0.0, 0.0]] * 5, [0, 1, 0, 1, 0])
    assert fit == {"temperature": 1.0, "shift": [0.0, 0.0, 0.0], "fitted": False}


def test_cross_fit_scores_out_of_half_only():
    logits, labels = _sample(3000, 1 / 3, seed=3)
    groups = [i % 50 for i in range(len(labels))]
    result = learning.cross_fit_calibration(logits, labels, groups)
    assert result["after"]["ece"] < result["before"]["ece"]
    assert result["after"]["n"] == result["before"]["n"] == 3000


def test_model_round_trips_its_calibration(tmp_path):
    model = learning.OutcomeModel()
    model.w = {1: [0.5, -0.2, 0.1]}
    model.b = [0.1, 0.0, -0.1]
    raw = model.predict_proba({"tool_name": "x", "args": {}})
    model.temperature, model.shift = 0.5, [1.0, -0.5, -0.5]
    path = model.save(tmp_path / "m.json")
    loaded = learning.OutcomeModel.load(path)
    assert loaded.temperature == 0.5 and loaded.shift == [1.0, -0.5, -0.5]
    assert loaded.predict_proba({"tool_name": "x", "args": {}}) != raw
    # A file written before calibration existed loads as identity.
    data = json.loads(path.read_text())
    del data["calibration"]
    path.write_text(json.dumps(data))
    old = learning.OutcomeModel.load(path)
    assert old.temperature == 1.0 and old.predict_proba({"tool_name": "x", "args": {}}) == raw


def test_out_of_range_calibration_in_a_file_is_ignored(tmp_path):
    model = learning.OutcomeModel()
    model.w, model.b = {1: [0.0, 0.0, 0.0]}, [0.0, 0.0, 0.0]
    path = model.save(tmp_path / "m.json")
    data = json.loads(path.read_text())
    data["calibration"] = {"temperature": 1e-9, "shift": [500.0, 0.0, 0.0]}
    path.write_text(json.dumps(data))
    assert learning.OutcomeModel.load(path).temperature == 1.0


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def test_predictions_pair_with_their_own_call_not_the_first_one(tmp_path):
    log = tmp_path / "tool_calls.log"
    preds = tmp_path / "preds.jsonl"
    t0 = 1_790_000_000.0
    calls = [
        {"timestamp": _iso(t0 - 86400), "tool_name": "convert_media", "verdict": "verified"},
        {"timestamp": _iso(t0 + 1), "tool_name": "convert_media", "verdict": "failed"},
        {"timestamp": _iso(t0 + 101), "tool_name": "convert_media", "verdict": "failed"},
    ]
    log.write_text("".join(json.dumps(c) + "\n" for c in calls))
    for at in (t0, t0 + 100):
        learning.record_prediction("convert_media",
                                   {"unverified": 0.1, "verified": 0.1, "failed": 0.8},
                                   "", epoch=at, path=preds)
    text = learning.score_predictions(preds, log)
    # The old pairing scored both against the day-old `verified` call: 0/2.
    assert "predicted correctly: 2/2" in text
    assert "NLL" in text


def test_a_call_answers_only_one_prediction(tmp_path):
    log = tmp_path / "tool_calls.log"
    preds = tmp_path / "preds.jsonl"
    t0 = 1_790_000_000.0
    log.write_text(json.dumps({"timestamp": _iso(t0 + 1), "tool_name": "x",
                               "verdict": "failed"}) + "\n")
    for at in (t0, t0 + 0.5):
        learning.record_prediction("x", {"unverified": 0.2, "verified": 0.2, "failed": 0.6},
                                   "", epoch=at, path=preds)
    text = learning.score_predictions(preds, log)
    assert "2 prediction(s) recorded, 1 could be paired" in text
    assert "1 prediction(s) had no matching call" in text


def test_ece_is_zero_for_perfect_calibration_and_large_for_overconfidence():
    perfect = learning.calibration_metrics([[1.0, 0.0, 0.0]] * 10, [0] * 10)
    assert perfect["ece"] == 0.0 and perfect["accuracy"] == 1.0
    wrong = learning.calibration_metrics([[0.99, 0.005, 0.005]] * 10, [1] * 10)
    assert wrong["ece"] > 0.9 and wrong["nll"] > math.log(100)
