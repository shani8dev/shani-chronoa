"""`Report.honest()`'s reason, so a refusal is not read as a result.

`harness-study/laya-decision`'s `laya/evidence.py` states the doctrine:

> Every state it assigns is either derived directly from an upstream artifact or
> marked UNKNOWN. **It never manufactures a healthy-looking state from absent
> input.**

and it keeps `INSUFFICIENT` apart from `MISSING` for the reason this needs:
*there and too thin to support a claim* is not *not there*.

Chronoa's `Report.honest()` collapses the same distinction into a bare `False`.
Measured: a model whose minority verdict has 3 examples (under `_MIN_CLASS`'s
20) and a model that has genuinely failed to learn that verdict return **the same
answer** - and the two have opposite remedies, one being *use the assistant
more* and the other *stop quoting the model*. This file already records the
principle from the other side: a truthful refusal demoted as a failure is a
defect (`train_and_save()` declining with *"the training split contains no
example of verified"* is the correct output for that data).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa import learning  # noqa: E402


def _report(support, recall, precision):
    return learning.Report(
        n=100, baseline=0.9, accuracy=0.85,
        per_class={"unverified": {"support": support[0], "recall": recall[0],
                                  "precision": precision[0]},
                   "verified": {"support": support[1], "recall": recall[1],
                                "precision": precision[1]}},
        confusion={})


class TestTheReasonDistinguishesTheTwoCases:
    def test_too_little_data_is_not_a_negative_result(self):
        """**The defect.** 3 examples and a real failure both reported `False`."""
        thin = _report([97, 3], [0.99, 0.6], [0.9, 0.2])
        assert thin.honest() is False
        why, said = thin.honest_reason()
        assert why == "insufficient", why
        assert "20" in said, said
        assert "cannot be answered" in said, (
            f"the refusal does not say the question is unanswerable: {said!r}")

    def test_a_real_failure_is_a_different_answer(self):
        failed = _report([80, 20], [0.99, 0.5], [0.9, 0.2])
        assert failed.honest() is False
        why, said = failed.honest_reason()
        assert why == "not-detected", why
        assert why != "insufficient", (
            "a measured negative and an unanswerable question gave one answer")

    def test_a_quotable_report_gives_no_reason(self):
        good = _report([80, 20], [0.99, 0.9], [0.9, 0.7])
        assert good.honest() is True
        assert good.honest_reason() == ""

    def test_a_wide_report_with_one_thin_verdict_is_a_negative_result(self):
        """**The witness the class-count guard needed.**

        `_report` builds exactly two verdicts, so `len(counts) <= 2` was always
        true and removing that guard changed nothing any test measured. A real
        outcome model has three, and with one thin among three the right answer is
        *not-detected* - the thin one is not the question being asked.
        """
        wide = learning.Report(
            n=1000, baseline=0.85, accuracy=0.8,
            per_class={"unverified": {"support": 850, "recall": 0.99, "precision": 0.95},
                       "verified": {"support": 140, "recall": 0.25, "precision": 0.2},
                       "failed": {"support": 10, "recall": 0.4, "precision": 0.1}},
            confusion={})
        assert wide.honest() is False
        why, _said = wide.honest_reason()
        assert why == "not-detected", (
            f"a report with two well-measured verdicts read as unanswerable "
            f"because a third was thin: {why}")

    def test_the_reason_names_the_counts_it_refused_on(self):
        """A refusal that quotes no numbers is a refusal a person cannot act on -
        they cannot tell whether one more call would change the answer."""
        thin = _report([97, 3], [0.99, 0.6], [0.9, 0.2])
        _why, said = thin.honest_reason()
        for name in ("unverified", "verified"):
            assert name in said, said

    def test_the_unreachable_branch_is_called_unreachable(self):
        """**Measuring found a dead branch in the first version of this method.**

        `best_detection()` only returns verdicts for which `detected()` is true,
        and any of those makes `honest()` true - so a `near-miss` branch placed
        after the early return could never run. It is recorded as unreachable
        rather than left looking like a reachable case, and this assertion fails
        if somebody makes it reachable without noticing.
        """
        for support, recall, precision in (
            ([80, 20], [0.99, 0.5], [0.9, 0.2]),
            ([80, 20], [0.99, 0.9], [0.9, 0.4]),
            ([97, 3], [0.99, 0.6], [0.9, 0.2]),
        ):
            report = _report(support, recall, precision)
            if not report.honest():
                assert report.honest_reason()[0] != "detected", (
                    "the unreachable branch was taken")

    def test_the_reason_and_the_bool_never_disagree(self):
        """`honest()` is unchanged, which is what every caller in the tree reads.
        The reason is additive, and a reason without a matching bool would be a
        second answer to one question."""
        for support, recall, precision in (
            ([97, 3], [0.99, 0.6], [0.9, 0.2]),
            ([80, 20], [0.99, 0.5], [0.9, 0.2]),
            ([80, 20], [0.99, 0.9], [0.9, 0.7]),
            ([1, 99], [0.99, 0.9], [0.9, 0.7]),
        ):
            report = _report(support, recall, precision)
            assert bool(report.honest_reason()) is (not report.honest()), (
                f"{support} {recall} {precision}: the reason and the bool "
                f"disagree - {report.honest_reason()!r} vs {report.honest()}")
