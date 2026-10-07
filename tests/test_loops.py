"""A turn that repeats one call forever is bounded, and a turn that legitimately
repeats is not.

The distinction is the whole design. An earlier attempt at this in Chronoa refused the
*second* identical call outright, and it broke 13 tests and real behaviour: reading a
file again after editing it, re-checking a value that may have moved, retrying a sense
that has not settled. Repeats are normal. A *run* of them is not.

So the threshold does the work. Every repeat executes, up to the threshold, and only the
run itself is stopped - with a message saying what happened rather than an exception the
window would render as a spinner with no explanation.

These tests also pin the thing that is easy to get wrong and would otherwise be a
no-op: `MAX_TOOL_ROUNDS` bounds *rounds*, so a backend that returns a dozen identical
calls in one round was never bounded by it. That inner loop is the gap.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import assistant as assistant_mod  # noqa: E402
from shani_chronoa.assistant import MAX_TOOL_ROUNDS, Assistant  # noqa: E402
from shani_chronoa.loops import LOOP_THRESHOLD, LoopDetector, call_key  # noqa: E402


#: Distinct tool names a "changing" turn cycles through, so no single call ever repeats
#: and a normal turn contains no run at all.
#: Distinct tool names a "changing" turn cycles through. None of them may be the tool the
#: first phase used, or a turn meant to look normal would still repeat one call.
_ROTATION = ("get_time", "list_processes", "list_windows", "get_timezone")


def _detector(threshold: int = LOOP_THRESHOLD) -> LoopDetector:
    return LoopDetector(threshold=threshold)


class _StuckLLM:
    """A backend that keeps asking for the identical call.

    `per_round` is the number of identical calls returned in a single assistant message.
    Setting it above 1 is the case `MAX_TOOL_ROUNDS` cannot see: one round, a dozen calls.
    """

    def __init__(self, per_round: int = 1, different_after: "int | None" = None):
        self.per_round = per_round
        self.different_after = different_after
        self.rounds = 0
        self.calls_returned = 0

    async def chat_message(self, messages, tools=None):
        if tools is None:
            return {"role": "assistant", "content": "final answer"}
        self.rounds += 1
        use_different = (
            self.different_after is not None and self.rounds > self.different_after)
        if not use_different:
            name = "get_battery_status"
        else:
            # Rotate rather than repeat one alternate name. Returning the same second tool
            # for every later round would itself be a run of identical calls, so a turn
            # meant to look normal would contain a loop the detector is right to notice.
            name = _ROTATION[(self.rounds - self.different_after - 1) % len(_ROTATION)]
        self.calls_returned += self.per_round
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": f"call-{self.rounds}-{i}", "type": "function",
                 "function": {"name": name, "arguments": {}}}
                for i in range(self.per_round)],
        }


@pytest.fixture
def counted_tools(monkeypatch):
    """Record which tools actually executed, and stub out the real ones."""
    executed: "list[str]" = []

    def _execute(name, args, origin=None):
        executed.append(name)
        return "battery: 87%"

    monkeypatch.setattr(assistant_mod, "execute_tool", _execute)
    return executed


def _turn(llm, text: str = "how is it going?") -> str:
    """Drive one turn to completion.

    This project has no pytest-asyncio, so an `async def test_` would be collected and
    never awaited - a green test that asserts nothing. Every other async test here calls
    asyncio.run() from a sync body, and these do the same.
    """
    return asyncio.run(Assistant(llm).handle(text))


class TestCallIdentity:
    def test_key_order_does_not_make_a_new_call(self):
        # A backend that reorders keys between rounds has not made a new request. Counting
        # these as different calls would mean a stuck turn never trips.
        assert call_key("t", {"b": 1, "a": 2}) == call_key("t", {"a": 2, "b": 1})

    def test_different_arguments_are_a_different_call(self):
        assert call_key("t", {"path": "/etc/hosts"}) != call_key("t", {"path": "/etc/passwd"})

    def test_different_tools_are_different_calls(self):
        assert call_key("t", {}) != call_key("u", {})

    def test_a_call_id_is_not_part_of_the_identity(self):
        # Every tool call has a unique id. Including it would make every call unique and
        # the detector would never fire - which is precisely the bug this guards.
        assert call_key("t", {"a": 1}) == call_key("t", {"a": 1})

    def test_unserialisable_arguments_do_not_raise(self):
        assert call_key("t", {"obj": object()})

    def test_a_non_dict_argument_does_not_raise(self):
        assert call_key("t", "a string")
        assert call_key("t", None)


class TestRunsNotOccurrences:
    def test_a_single_call_is_not_a_repeat(self):
        detector = _detector()
        assert detector.repeat("t", {}) is False
        assert detector.run_length == 1

    def test_an_identical_second_call_is_reported_as_a_repeat(self):
        detector = _detector()
        detector.repeat("t", {})
        assert detector.repeat("t", {}) is True

    def test_alternating_calls_are_not_a_loop(self):
        # a, b, a, b repeats each call twice but is a model narrowing a search, which is
        # the opposite of being stuck. Counting occurrences rather than runs would stop it.
        detector = _detector(threshold=3)
        for _ in range(5):
            detector.repeat("a", {})
            detector.repeat("b", {})
        assert detector.stopped is False, "alternating calls were called a loop"

    def test_a_different_argument_resets_the_run(self):
        detector = _detector(threshold=4)
        for _ in range(3):
            detector.repeat("t", {"path": "/a"})
        detector.repeat("t", {"path": "/b"})
        assert detector.run_length == 1, "the run did not reset on a different call"

    def test_a_different_tool_resets_the_run(self):
        detector = _detector(threshold=4)
        for _ in range(3):
            detector.repeat("t", {})
        detector.repeat("u", {})
        assert detector.run_length == 1


class TestTheThreshold:
    def test_repeats_below_the_threshold_all_execute(self):
        # The core promise. Four identical reads is a model that is allowed to re-check.
        detector = _detector()
        results = [detector.repeat("t", {}) for _ in range(LOOP_THRESHOLD - 1)]
        assert detector.stopped is False
        assert results[0] is False
        assert all(results[1:]), "a repeat below the threshold was refused"

    def test_the_run_trips_at_the_threshold(self):
        detector = _detector()
        for _ in range(LOOP_THRESHOLD):
            detector.repeat("t", {})
        assert detector.stopped is True
        assert detector.run_length == LOOP_THRESHOLD

    def test_stopping_is_latched(self):
        # A caller that checks after executing a batch must not see it flip back.
        detector = _detector()
        for _ in range(LOOP_THRESHOLD):
            detector.repeat("t", {})
        detector.repeat("t", {})
        assert detector.stopped is True, "the latch released"

    def test_the_threshold_is_configurable(self):
        detector = _detector(threshold=2)
        detector.repeat("t", {})
        detector.repeat("t", {})
        assert detector.stopped is True


class TestTheHonestStop:
    def test_there_is_nothing_to_explain_before_it_stops(self):
        detector = _detector()
        detector.repeat("t", {})
        assert detector.explain() == ""

    def test_the_message_says_what_happened_and_how_many(self):
        detector = _detector()
        for _ in range(LOOP_THRESHOLD):
            detector.repeat("get_battery_status", {})
        out = detector.explain()
        assert str(LOOP_THRESHOLD) in out
        assert "get_battery_status" in out

    def test_the_message_admits_repeats_are_normal(self):
        # Otherwise the message reads as "never repeat", which is the wrong instruction
        # and the reason the earlier refusal was removed.
        detector = _detector()
        for _ in range(LOOP_THRESHOLD):
            detector.repeat("t", {})
        out = detector.explain().lower()
        assert "normal" in out or "allowed" in out

    def test_the_message_is_returned_not_raised(self):
        # The turn's contract is to return an answer. An exception would leave the window
        # showing a spinner with no explanation - the same reasoning as the time budget.
        assert not issubclass(LoopDetector, Exception)

    def test_a_very_long_tool_name_is_truncated(self):
        detector = _detector(threshold=2)
        detector.repeat("x" * 500, {})
        detector.repeat("x" * 500, {})
        # The name is the part that can be arbitrarily long, so that is what gets capped.
        # The prose around it is fixed-length, so a bound on the whole message would be a
        # bound on the prose rather than on the truncation.
        out = detector.explain()
        assert "x" * 500 not in out, "an entire 500-character name went into the message"
        inside = out.split("`")[1::2]
        assert inside and max(len(n) for n in inside) <= 60, \
            f"the name was not capped at 60: {max(len(n) for n in inside)}"


class TestThroughTheAssistant:
    def test_a_dozen_identical_calls_in_one_round_is_bounded(self, counted_tools):
        # The gap `MAX_TOOL_ROUNDS` cannot see: one round, twelve calls, and the rounds
        # counter never moved. Before this, all twelve ran.
        out = _turn(_StuckLLM(per_round=12))
        assert len(counted_tools) <= LOOP_THRESHOLD, \
            f"{len(counted_tools)} identical calls ran; the inner loop is still uncapped"
        # Assert on the text the user is shown. "consecutive identical" is the logger's
        # wording; explain() says "in a row with identical arguments".
        assert "identical arguments" in out, f"the stop was not explained: {out[:80]!r}"
        assert "get_battery_status" in out, "the stop did not name the tool that looped"

    def test_the_reported_count_is_the_count_that_ran(self, counted_tools):
        # Checking *before* dispatch would stop the turn before the Nth call and then
        # claim N calls in a row when only N-1 had happened.
        out = _turn(_StuckLLM(per_round=12))
        assert str(len(counted_tools)) in out, (
            f"the message says a count that is not the {len(counted_tools)} that ran")

    def test_legitimate_repeats_still_run(self, counted_tools):
        # This is the case the earlier refusal broke. Three identical reads in a row, then
        # something else: all three must execute and the turn must not stop.
        out = _turn(_StuckLLM(per_round=1, different_after=3))
        # The turn does not stop when the tool changes; it continues to MAX_TOOL_ROUNDS
        # with the other tool. So the total is not what matters - the three identical
        # reads running is.
        assert counted_tools.count("get_battery_status") == 3, (
            f"only {counted_tools.count('get_battery_status')} of 3 identical reads ran: "
            f"{counted_tools}")
        assert "identical arguments" not in out, \
            "the detector stopped a turn that was making progress"

    def test_a_normal_turn_is_untouched(self, counted_tools):
        out = _turn(_StuckLLM(per_round=1, different_after=1))
        # Every call is to a different point in the sequence, so no single call repeats.
        assert max(counted_tools.count(n) for n in set(counted_tools)) == 1, \
            f"a run of identical calls appeared in a normal turn: {counted_tools}"
        assert "identical arguments" not in out

    def test_the_detector_does_not_carry_across_turns(self, counted_tools):
        # A conversation may legitimately make the same call at the start of one turn and
        # the start of the next. Only a run *within* a turn means the turn is stuck.
        llm = _StuckLLM(per_round=1, different_after=1)
        assistant = Assistant(llm)
        asyncio.run(assistant.handle("first"))
        before = len(counted_tools)
        asyncio.run(assistant.handle("second"))
        assert len(counted_tools) > before, "the second turn did nothing"
        assert "identical arguments" not in asyncio.run(assistant.handle("third"))

    def test_the_result_is_still_recorded_for_the_transcript(self, counted_tools):
        # Stopping the turn early must not corrupt the history: the tool results that did
        # run still need their messages, or the next turn sees tool calls with no results.
        assistant = Assistant(_StuckLLM(per_round=12))
        asyncio.run(assistant.handle("go"))
        results = [m for m in assistant._history if m.get("role") == "tool"]
        assert len(results) == len(counted_tools), \
            "a tool ran without its result being recorded"

    def test_the_round_limit_still_bounds_the_slow_loop(self, counted_tools):
        # Recorded so the relationship between the two bounds stays explicit: one
        # identical call per round is capped by MAX_TOOL_ROUNDS at 4, which is below the
        # loop threshold, so that path is the rounds limit's job and not this module's.
        llm = _StuckLLM(per_round=1)
        out = _turn(llm)
        assert len(counted_tools) <= MAX_TOOL_ROUNDS
        assert llm.rounds <= MAX_TOOL_ROUNDS + 1
