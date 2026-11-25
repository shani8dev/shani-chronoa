"""A person's verdict is an outcome the learning layer can actually see.

The call log records a tool's own post-condition verdict. A reply verdict is
written onto that record later, and an undo marks it "done, then undone".
Neither is a new prompt and neither grants a tool - they change what Chronoa
*learned* from a call that already happened. These tests pin that mapping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import feedback, learning, reward  # noqa: E402


def _record(verdict="verified", **fields):
    row = {"tool_name": "write_text_file", "args": {}, "verdict": verdict}
    row.update(fields)
    return row


def test_feedback_fields_map_into_outcome_features():
    wrong = learning.outcome_features(
        _record(feedback_verdict=feedback.WRONG, feedback="use the trash")
    )
    assert wrong["verdict"] == "verified"
    assert wrong["corrected"] is True
    assert wrong["label"] == "failed"
    assert wrong["reward"] == reward.reward_for(wrong) < 1.0

    undone = learning.outcome_features(_record(undone=True))
    assert undone["undone"] is True
    assert undone["reward"] == 0.0  # done, then put back: neutral, not a win

    plain = learning.outcome_features(_record())
    assert "corrected" not in plain and plain["undone"] is False
    assert plain["reward"] == 1.0


def test_a_wrong_answer_changes_the_learned_tool_weight():
    good = [_record()]
    rejected = [_record(feedback_verdict=feedback.WRONG)]
    assert learning.reliability(rejected) < learning.reliability(good)
    assert (
        learning.weights_from_records(rejected)["write_text_file"]
        < learning.weights_from_records(good)["write_text_file"]
    )


def test_a_done_then_undone_call_is_neutral_not_a_success():
    plain = learning.reliability([_record()])
    undone = learning.reliability([_record(undone=True)])
    failed = learning.reliability([_record("failed")])
    assert failed < undone < plain


def test_a_wrong_answer_changes_the_outcome_model_label(tmp_path):
    log = tmp_path / "tool_calls.log"
    lines = [
        _record(),
        _record(feedback_verdict=feedback.WRONG, feedback="use the trash"),
    ]
    log.write_text("\n".join(json.dumps(row) for row in lines) + "\n")
    examples = learning.load(log)
    assert [example.y for example in examples] == [
        learning.VERDICTS.index("verified"),
        learning.VERDICTS.index("failed"),
    ]

    # Editing a record in place changes the label on the next read: the verdict
    # alone would have kept both examples identical.
    lines[1].pop("feedback_verdict")
    log.write_text("\n".join(json.dumps(row) for row in lines) + "\n")
    assert [example.y for example in learning.load(log)] == [
        learning.VERDICTS.index("verified"),
        learning.VERDICTS.index("verified"),
    ]


def test_the_learning_tally_counts_a_rejected_verified_call_as_failed():
    tally = learning._tally_from_log(
        [_record(), _record(feedback_verdict=feedback.CORRECTION, feedback="trash it")]
    )
    assert tally == {"write_text_file": {"verified": 1, "failed": 1}}
