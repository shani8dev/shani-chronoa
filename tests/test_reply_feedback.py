"""A wrong answer is a signal, and it has to survive past the turn.

The call log records what Chronoa did and the transcript records what it said;
neither recorded that a person rejected either one. These tests pin the third
record and, more importantly, the things it must NOT do: it must not change
what the model is told, must not grant or refuse a tool, and must not invent a
verdict for a caller that gave no verdict.
"""

from __future__ import annotations

import json

import pytest

from shani_chronoa import assistant as assistant_mod
from shani_chronoa import conversation_store, feedback, tool_tracking


class _LLM:
    """Only used so an Assistant can be built without a real model."""

    async def chat_message(self, messages, tools=None, stream=False):
        return {"role": "assistant", "content": ""}


def test_a_verdict_must_be_one_the_store_knows(tmp_path):
    with pytest.raises(ValueError):
        feedback.record_answer("maybe", path=tmp_path / "feedback.jsonl")


def test_a_judgement_survives_a_reload(tmp_path):
    target = tmp_path / "feedback.jsonl"
    written = feedback.record_answer(
        feedback.CORRECTION, "use the trash instead", session_id="s1", path=target
    )
    assert written.verdict == feedback.CORRECTION
    loaded = feedback.load(path=target)
    assert len(loaded) == 1
    assert loaded[0].correction == "use the trash instead"
    assert loaded[0].session_id == "s1"


def test_an_empty_correction_is_just_wrong(tmp_path):
    """Two names for one answer is a second answer to the same question."""
    written = feedback.record_answer(
        feedback.CORRECTION, "  ", path=tmp_path / "f.jsonl"
    )
    assert written.verdict == feedback.WRONG
    assert written.correction == ""


def test_a_missing_log_is_not_an_excuse_to_lose_the_judgement(tmp_path):
    written = feedback.record_answer(feedback.WRONG, path=tmp_path / "f.jsonl")
    assert written.verdict == feedback.WRONG
    assert not (tmp_path / "tool_calls.log").exists(), (
        "recording feedback must not fabricate a call"
    )


class TestWriteBackToTheCallRecord:
    def test_the_newest_call_carries_the_judgement(self, tmp_path):
        tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
        tracker.record_call(
            "delete_file", {"path": "/tmp/x"}, "trashed", 10.0, verdict="verified"
        )
        feedback.record_answer(feedback.WRONG, "wrong file", tracker=tracker)
        line = json.loads((tmp_path / "tool_calls.log").read_text().splitlines()[-1])
        assert line["feedback"] == "wrong file"
        assert line["feedback_verdict"] == feedback.WRONG

    def test_the_in_memory_ring_sees_the_same_answer(self, tmp_path):
        tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
        record = tracker.record_call("write_text_file", {}, "done", 1.0)
        feedback.record_answer(feedback.CORRECTION, "put it in notes", tracker=tracker)
        assert record.feedback == "put it in notes"
        assert record.feedback_verdict == feedback.CORRECTION

    def test_other_records_are_left_alone(self, tmp_path):
        tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
        first = tracker.record_call("get_datetime", {}, "noon", 1.0)
        tracker.record_call("delete_file", {}, "gone", 1.0)
        feedback.record_answer(feedback.WRONG, tracker=tracker)
        assert first.feedback == ""
        lines = [
            json.loads(line)
            for line in (tmp_path / "tool_calls.log").read_text().splitlines()
        ]
        assert lines[0].get("feedback", "") == ""
        assert lines[1]["feedback_verdict"] == feedback.WRONG

    def test_a_call_record_can_carry_feedback_from_the_start(self):
        record = tool_tracking.ToolCallRecord(
            "t", {}, "r", 1.0, feedback="no", feedback_verdict="wrong"
        )
        assert record.to_dict()["feedback"] == "no"
        assert record.to_log_dict()["feedback_verdict"] == "wrong"


class TestMarkingAnAnswer:
    def _assistant(self, tmp_path):
        path = tmp_path / "session.jsonl"
        for message in (
            {"role": "user", "content": "delete my notes"},
            {"role": "assistant", "content": "Deleted."},
        ):
            conversation_store.append(message, path)
        return assistant_mod.Assistant(_LLM(), session_path=path), path

    def test_the_judgement_is_written_beside_the_answer(self, tmp_path):
        bot, path = self._assistant(tmp_path)
        bot.mark_last_answer(feedback.WRONG, "use the trash, not a delete")
        saved = conversation_store.load(path)
        assert saved[-1]["feedback"]["verdict"] == feedback.WRONG
        assert saved[-1]["feedback"]["correction"] == "use the trash, not a delete"

    def test_it_does_not_change_what_the_model_is_told(self, tmp_path):
        bot, path = self._assistant(tmp_path)
        before = [dict(m) for m in bot._history]
        bot.mark_last_answer(feedback.WRONG)
        assert bot._history == before, "feedback is a record, not a prompt injection"

    def test_a_correction_is_not_taken_as_the_next_question(self, tmp_path):
        bot, path = self._assistant(tmp_path)
        bot.mark_last_answer(feedback.CORRECTION, "the answer is 42")
        assert bot._history[-1]["content"] == "Deleted."
        assert len(bot._history) == 3  # system, user, assistant - no synthetic turn

    def test_a_session_with_no_answer_still_records_the_judgement(self, tmp_path):
        bot = assistant_mod.Assistant(_LLM())
        written = bot.mark_last_answer(feedback.WRONG)
        assert written.verdict == feedback.WRONG
