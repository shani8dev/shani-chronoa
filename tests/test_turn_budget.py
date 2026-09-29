"""Nothing bounded how long a turn could take.

`MAX_TOOL_ROUNDS = 4` bounds how many times the model may act, not how long any
of those may take. Four rounds against a 4B model on a cold start, or one slow
tool on a spinning disk, is minutes with the window showing a spinner and no way
to tell whether it is working or stuck. A turn that cannot finish is a turn the
user cannot interrupt.

mini-swe-agent carries a `wall_time_limit_seconds` for the same reason, and puts
its budget check where the loop can see it rather than trusting a prompt.

What is *not* claimed here: money. When the cloud fallback is on, a call has a
real price, but it depends on the provider and the model, and a number invented
here would be worse than none. So durations and call counts are recorded and the
pricing is left to whoever knows it.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import assistant as assistant_mod  # noqa: E402
from shani_chronoa.assistant import MAX_TOOL_ROUNDS, Assistant  # noqa: E402


class _Looping:
    """Always asks for a tool, so the loop genuinely keeps going.

    A model that answers immediately exits on the first round whatever the
    budget, so using one would be a control that cannot fail - which is how the
    first version of this test proved nothing.
    """

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    async def chat_message(self, messages, tools=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return {"role": "assistant", "content": "still working",
                "tool_calls": [{"id": str(self.calls),
                                "function": {"name": "get_datetime", "arguments": {}}}]}


class _Answers:
    def __init__(self):
        self.calls = 0

    async def chat_message(self, messages, tools=None):
        self.calls += 1
        return {"role": "assistant", "content": "answered"}


@pytest.fixture
def transcript(tmp_path):
    return tmp_path / "t.jsonl"


@pytest.fixture
def shrink_budget(monkeypatch):
    """Shrink the budget rather than waiting five real minutes."""
    monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", 0.6)
    yield
    monkeypatch.undo()


class TestTheBudgetStopsALoop:
    def test_a_slow_loop_is_cut_short(self, transcript, shrink_budget):
        llm = _Looping(delay=0.25)
        a = Assistant(llm, session_path=transcript)
        out = asyncio.run(a.handle("loop forever"))
        assert llm.calls < MAX_TOOL_ROUNDS, (
            f"the loop ran {llm.calls} times, so the round cap stopped it - the "
            f"budget never fired"
        )
        assert "ran out of time" in out

    def test_it_does_not_return_the_models_own_words(self, transcript, shrink_budget):
        """A partial answer that looks complete is worse than an honest stop."""
        llm = _Looping(delay=0.25)
        a = Assistant(llm, session_path=transcript)
        out = asyncio.run(a.handle("loop forever"))
        assert "still working" not in out

    def test_the_message_says_nothing_further_was_done(self, transcript, shrink_budget):
        a = Assistant(_Looping(delay=0.25), session_path=transcript)
        out = asyncio.run(a.handle("loop forever"))
        assert "Nothing further was done" in out


class TestNormalTurnsAreUntouched:
    def test_a_quick_turn_is_not_cut_short(self, transcript, monkeypatch):
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", 60.0)
        a = Assistant(_Answers(), session_path=transcript)
        out = asyncio.run(a.handle("hello"))
        assert "ran out of time" not in out
        assert out == "answered"

    def test_the_round_cap_still_applies_with_a_generous_budget(
            self, transcript, monkeypatch):
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", 600.0)
        llm = _Looping()
        a = Assistant(llm, session_path=transcript)
        asyncio.run(a.handle("loop"))
        # 4 rounds, then one final plain answer.
        assert llm.calls == MAX_TOOL_ROUNDS + 1


class TestTheMessageDoesNotMisstateTheTime:
    def test_a_fractional_budget_is_not_rounded_into_a_different_number(self):
        """`{0.6:.0f}` is "1", and "it had 1 seconds" is both wrong and ugly."""
        assert assistant_mod._seconds(0.6) == "0.6 seconds"
        assert assistant_mod._seconds(9.4) == "9.4 seconds"

    def test_whole_seconds_stay_whole(self):
        assert assistant_mod._seconds(300.0) == "300 seconds"
        assert assistant_mod._seconds(301.4) == "301 seconds"

    def test_the_overrun_message_carries_the_real_budget(self, transcript, shrink_budget):
        a = Assistant(_Looping(delay=0.25), session_path=transcript)
        out = asyncio.run(a.handle("loop"))
        assert "0.6 seconds" in out


class TestTurnStats:
    def test_an_overrun_is_reported_as_one(self, transcript, shrink_budget):
        a = Assistant(_Looping(delay=0.25), session_path=transcript)
        asyncio.run(a.handle("loop"))
        assert a.turn_stats()["over_budget"] is True

    def test_a_completed_turn_is_not_reported_as_an_overrun(self, transcript, monkeypatch):
        """The first version inferred this from a cleared deadline, which is
        also true after a turn that finished normally - so it claimed overruns
        that never happened."""
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", 600.0)
        a = Assistant(_Answers(), session_path=transcript)
        asyncio.run(a.handle("hello"))
        assert a.turn_stats()["over_budget"] is False

    def test_it_counts_calls_and_their_time(self, transcript, monkeypatch):
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", 600.0)
        a = Assistant(_Looping(delay=0.01), session_path=transcript)
        asyncio.run(a.handle("loop"))
        stats = a.turn_stats()
        assert stats["model_calls"] == MAX_TOOL_ROUNDS + 1
        assert stats["model_seconds"] > 0
        assert stats["wall_seconds"] >= stats["model_seconds"]

    def test_no_money_figure_is_invented(self, transcript, monkeypatch):
        """Pricing depends on the provider and model; a wrong number is worse
        than none, so the stats report work done and leave pricing alone."""
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", 600.0)
        a = Assistant(_Answers(), session_path=transcript)
        asyncio.run(a.handle("hi"))
        assert not any("cost" in k or "usd" in k or "price" in k
                       for k in a.turn_stats())
