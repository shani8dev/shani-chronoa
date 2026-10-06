"""A merge is a hypothesis until it is scored.

`merge_models()` writes `"honest": True` unconditionally and never evaluated the
weights it had just combined. Every writeup on TIES/DARE/SLERP says the merged
model must be evaluated like one — sign election makes conflicts cancel, but
nothing guarantees the result beats the models that went into it, and on
independently fitted models there is no shared base to make it likely.

It was not a small shortcut: `tools._outcome_model()` refuses any model whose
`provenance.honest` is false, so a merge that was pure noise was stamped
trustworthy *by construction* and then loaded.
"""

import json
import random

import pytest


@pytest.fixture
def a_fitted_model(tmp_path, monkeypatch):
    """One real model, fitted from a small synthetic log in its own feature space."""
    from shani_chronoa import learning

    monkeypatch.setattr(learning, "models_dir", lambda: tmp_path / "models")
    examples, model = _detecting_model(learning)
    payload = learning.sign_model(model.to_dict({
        "accuracy": 0.5, "baseline": 0.9, "honest": True,
        "example_count": len(examples)}))
    return payload


def _detecting_model(learning):
    """A model that genuinely detects a minority class, and the data proving it.

    Feature 3 marks `verified`: present on every verified example, absent on every
    unverified one. So a fit has something real to find, `Report.honest()` has a
    minority verdict to detect, and the write path is reachable **deterministically**
    rather than only sometimes - a skip here would be a hole in the coverage, not a
    tolerance.
    """
    examples = []
    for i in range(400):
        examples.append(learning.Example(
            active=((0, 1.0), (1, 1.0), (2, 1.0), (3, 0.0)), y=0, width=7))
        examples.append(learning.Example(
            active=((0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)), y=0, width=7))
        examples.append(learning.Example(
            active=((0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)), y=1, width=7))
    model = learning.OutcomeModel()
    model.fit(examples, epochs=60)
    return examples, model


def _held_out(learning, groups=137):
    """Fresh examples from the same rule, so the merge is scored on unseen data.

    **137 groups, deliberately.** A constant-prediction model scores exactly the
    majority fraction, so with a round number of examples the baseline - and any
    accuracy a broken model happens to reach - lands on a round percentage. My
    first version used 400 groups, the noise merge scored **exactly 0.0%**, and a
    mutation that hardcoded the string `"0.0%"` passed: right answer, unfalsifiable
    test. 411 examples puts the baseline at 73.9%, so no plausible constant can
    stand in for the measured number.
    """
    out = []
    for _ in range(groups):
        out.append(learning.Example(active=((0, 1.0), (1, 1.0), (2, 1.0), (3, 0.0)), y=0, width=7))
        out.append(learning.Example(active=((0, 1.0), (1, 1.0), (2, 1.0), (3, 0.0)), y=0, width=7))
        out.append(learning.Example(active=((0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)), y=1, width=7))
        # **All three verdicts, or the fixture lies.** With no class-2 examples a
        # noise merge predicts the absent class and scores exactly 0.0% - which is
        # what made a mutation hardcoding "0.0%" unfalsifiable. One `failed` per
        # group also puts the baseline on a non-round fraction.
        out.append(learning.Example(active=((0, 1.0), (4, 1.0), (2, 1.0), (3, 0.0)), y=2, width=7))
    return out


def _noise(payload, seed):
    random.seed(seed)
    n = json.loads(json.dumps(payload))
    n.pop("digest", None)
    n.pop("signature", None)
    n["weights"] = {k: [random.uniform(-1.0, 1.0) for _ in v]
                    for k, v in payload["weights"].items()}
    n["bias"] = [0.0] * len(n.get("bias") or [0])
    return learning_module().sign_model(n)


def learning_module():
    from shani_chronoa import learning
    return learning


class TestAMergeIsScoredBeforeItIsTrusted:
    def test_a_merge_of_noise_is_refused_and_nothing_is_written(self, a_fitted_model):
        from shani_chronoa import learning

        out = learning.merge_models([_noise(a_fitted_model, 1),
                                     _noise(a_fitted_model, 2),
                                     _noise(a_fitted_model, 3)],
                                    examples=[learning.Example(active=((i % 7, 1.0),), y=0, width=7)
                                               for i in range(400)]
                                    + [learning.Example(active=((i % 7, 1.0),), y=1, width=7)
                                       for i in range(20)])
        assert out.get("merged") is False, (
            f"a merge of three noise models was accepted: {out!r}")
        assert not out.get("path"), (
            f"a model that failed the gate was still written to {out.get('path')!r}")
        assert "not worth quoting" in str(out.get("reason")), out.get("reason")

    def test_the_reason_carries_the_numbers_that_failed_it(self, a_fitted_model):
        """A refusal that does not say why is the same defect as no refusal."""
        from shani_chronoa import learning

        out = learning.merge_models([_noise(a_fitted_model, 4),
                                     _noise(a_fitted_model, 5),
                                     _noise(a_fitted_model, 6)],
                                    examples=_held_out(learning))
        assert out.get("merged") is False, out
        assert isinstance(out.get("accuracy"), float), out
        assert isinstance(out.get("baseline"), float), out
        # **Both** numbers, and the baseline is the load-bearing one: this
        # fixture's noise merge scores exactly 0.0% while the constant is 50.0%,
        # so a mutation that hardcoded *both* to the same plausible value is still
        # caught - by the baseline. Asserting only the accuracy would let "0.0%"
        # through, which is what happened on the first attempt.
        assert f"{out['accuracy']:.1%}" in str(out.get("reason")), (
            f"the reason does not quote the accuracy it failed on: {out.get('reason')!r}")
        assert f"{out['baseline']:.1%}" in str(out.get("reason")), (
            f"the reason does not quote the constant it lost to: {out.get('reason')!r}")

    def test_it_refuses_when_there_is_nothing_to_measure_against(self, a_fitted_model):  # noqa: E501
        """**Fails closed.** No examples means no verdict, so no file."""
        from shani_chronoa import learning

        out = learning.merge_models([a_fitted_model], examples=[])
        assert out.get("merged") is False, out
        assert not out.get("path"), out
        assert "could not be measured" in str(out.get("reason")), out

    def test_the_written_provenance_comes_from_the_report(self, a_fitted_model,
                                                          tmp_path, monkeypatch):
        """`honest` in the file must equal what the report said, not `True`."""
        from shani_chronoa import learning

        monkeypatch.setattr(learning, "models_dir", lambda: tmp_path / "models")
        examples = _held_out(learning)
        # Merging a model with itself: TIES elects the sign it already has, so
        # the result must keep detecting what the parent detected. No `skip` -
        # if this stops clearing the gate the failure is the point.
        out = learning.merge_models([a_fitted_model, a_fitted_model],
                                    examples=examples)
        assert out.get("merged") is True, out
        written = json.loads(next((tmp_path / "models").glob("outcome-merged*.json")).read_text())
        prov = written["provenance"]
        assert prov["honest"] == out["honest"] is True, (
            f"the file says honest={prov['honest']} while the report said "
            f"{out['honest']}")
        assert prov["accuracy"] == out["accuracy"], (prov, out)
        assert prov["baseline"] == out["baseline"], (prov, out)
        assert prov["evaluated_on"] == len(examples), prov
        assert prov["detected"] == out["detected"], (prov, out)
        # The old payload carried none of these.
        for key in ("accuracy", "baseline", "beats_baseline", "evaluated_on",
                    "detected", "recall_lift", "precision_lift"):
            assert key in prov, f"{key} is missing from the merged provenance"

    def test_the_caller_is_told_the_verdict_too(self, a_fitted_model):
        from shani_chronoa import learning

        out = learning.merge_models([a_fitted_model], examples=[])
        assert "honest" in out or out.get("merged") is False, (
            "the return value must carry the verdict either way, so a caller "
            "cannot read 'merged' as 'good'")
