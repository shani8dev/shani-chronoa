"""One turn could block for hours, and the turn budget did not notice.

`MAX_TURN_SECONDS` was only ever checked between model calls. A tool call that blocks
longer than the whole budget was therefore unbounded, and `ask_user` blocks for up to
`ask_bridge.DEFAULT_TIMEOUT_SECONDS` waiting for a user who may not be there.

Measured, with the shipped defaults: `MAX_TOOL_ROUNDS` is 4 and each ask blocks 180s, so
a model that only ever asks can hold one turn open for 48 minutes - four rounds of four
asking calls - and the 300-second budget is not consulted during any of it, because those
waits happen *inside* the tool loop.

`assistd` bounds the same problem from the other side, with a 120-second prompt timeout
and a hard cap of 32 pending confirmations. The check below is what makes the budget mean
what it says regardless of which tool is slow.

Two properties are pinned, and the second is the easy one to get wrong:

  - the turn stops rather than running every call in every round;
  - a call already shown to the user is **not** abandoned. The check happens *before*
    dispatching, so a question on screen is allowed to time out on its own terms. Killing
    the wait underneath one would strand the user looking at a prompt that will never
    answer.

## Why these tests set module state directly

The first version of this file used fixtures that patched `ask_bridge.ask`'s
`__defaults__` and installed a presenter through `set_presenter`. Every one of those is a
mutation of a function object invisible to the code under test, and the tests passed with
the fix *entirely removed* - coverage that could not fail. The version below assigns
`ask_bridge._presenter` and `ask_bridge.ask.__defaults__` directly, and asserts on the
number of tool calls that actually ran, which is the thing the fix changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ask_bridge  # noqa: E402
from shani_chronoa import assistant as assistant_mod  # noqa: E402
from shani_chronoa.assistant import MAX_TOOL_ROUNDS, Assistant  # noqa: E402

#: Short enough that four rounds of four asking calls cost 32s rather than 48 minutes.
_ASK_SECONDS = 2.0


class _AskingModel:
    """Asks `per_round` questions in every assistant message, for every round."""

    def __init__(self, per_round: int = 4):
        self.per_round = per_round

    async def chat_message(self, messages, tools=None):
        if tools is None:
            return {"role": "assistant", "content": "done"}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": str(i),
                 "function": {"name": "ask_user",
                              "arguments": {"question": f"q{i}", "options": ["a", "b"]}}}
                for i in range(self.per_round)],
        }


@pytest.fixture
def absent_user():
    """A prompt that is shown and never answered - the user has walked away.

    Assigned to `_presenter` rather than installed through `set_presenter`, because an
    earlier version of this file stubbed `set_presenter` out with a no-op lambda and then
    called it. No presenter was installed, every `ask` returned instantly, and the
    "bounded" turn finished in 0.0s - passing for entirely the wrong reason.
    """
    ask_bridge._presenter = lambda question, options: threading.Event()
    assert ask_bridge._presenter is not None
    yield
    ask_bridge._presenter = None


@pytest.fixture
def run_turn():
    """Run one turn; return (answer, elapsed, tool calls actually dispatched)."""
    original_budget = assistant_mod.MAX_TURN_SECONDS
    original_defaults = ask_bridge.ask.__defaults__
    original_execute = assistant_mod.execute_tool
    calls: list = []

    def _execute(name, args, origin=None):
        calls.append(name)
        if name == "ask_user":
            return ask_bridge.ask(args.get("question", ""),
                                  args.get("options", ["a", "b"]))
        return "ok"

    def _run(model, budget: float):
        calls.clear()
        assistant_mod.execute_tool = _execute
        ask_bridge.ask.__defaults__ = ("", [], _ASK_SECONDS)
        assistant_mod.MAX_TURN_SECONDS = budget
        started = time.monotonic()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                answer = asyncio.run(Assistant(model).handle("ask me things"))
        finally:
            assistant_mod.MAX_TURN_SECONDS = original_budget
            ask_bridge.ask.__defaults__ = original_defaults
            assistant_mod.execute_tool = original_execute
        return answer, time.monotonic() - started, list(calls)

    yield _run


class TestTheBudgetAppliesInsideTheToolLoop:
    def test_a_turn_stops_even_though_every_tool_call_blocks(
            self, absent_user, run_turn):
        answer, elapsed, calls = run_turn(_AskingModel(per_round=4), 5.0)
        assert "ran out of time" in answer, (
            "the turn ran every round with every call blocked; the budget is "
            "still only consulted between model calls")
        assert elapsed < 12, f"the turn took {elapsed:.1f}s against a 5s budget"

    def test_it_makes_far_fewer_calls_than_an_unbounded_turn(
            self, absent_user, run_turn):
        # The comparison the earlier version of this file got wrong: both turns
        # have to be timed and counted, because an assertion on the answer text
        # alone passes whether or not the check exists.
        _, _, bounded = run_turn(_AskingModel(per_round=4), 5.0)
        _, _, unbounded = run_turn(_AskingModel(per_round=4), 10**9)
        assert len(bounded) < len(unbounded), (
            f"a 5s budget ran {len(bounded)} calls and an unlimited budget ran "
            f"{len(unbounded)}; the check is not doing anything")
        # 3 against 16, measured. Pinning the exact figures is what makes a
        # regression visible: a check that fires too eagerly would show 1.
        assert len(bounded) == 3, (
            f"the bounded turn ran {len(bounded)} calls, expected 3 - one in "
            f"flight, then the budget stops it before the next")
        assert len(unbounded) == 16, (
            f"the unbounded turn ran {len(unbounded)} calls, expected all "
            f"{MAX_TOOL_ROUNDS} rounds of 4")

    def test_an_in_flight_call_is_not_abandoned(self, absent_user, run_turn):
        # A 1s budget with a 2s ask: the turn must let that ask finish rather than
        # cutting the wait, and stop before the next one.
        answer, elapsed, calls = run_turn(_AskingModel(per_round=4), 1.0)
        assert calls, "the budget stopped the turn before any call was dispatched"
        assert elapsed >= _ASK_SECONDS - 0.2, (
            f"the turn returned after {elapsed:.1f}s with a 1s budget and a 2s ask; "
            f"a question already on the user's screen was cut short")
        assert "ran out of time" in answer

    def test_a_fast_turn_is_untouched(self, absent_user, run_turn):
        class _Quick:
            async def chat_message(self, messages, tools=None):
                if tools is None:
                    return {"role": "assistant", "content": "done"}
                return {"role": "assistant", "content": "",
                        "tool_calls": [{"id": "1",
                                        "function": {"name": "get_datetime",
                                                     "arguments": {}}}]}

        answer, elapsed, calls = run_turn(_Quick(), 300.0)
        assert answer == "done"
        assert calls and set(calls) == {"get_datetime"}, (
            f"expected only get_datetime calls, got {calls}")
        assert len(calls) == MAX_TOOL_ROUNDS, (
            f"the fast turn ran {len(calls)} rounds, expected all {MAX_TOOL_ROUNDS}")


class TestTheMagnitudeThisFixes:
    def test_the_shipped_defaults_would_hold_a_turn_open_for_half_an_hour(self):
        # Arithmetic about the shipped constants, so the numbers stay visible if
        # either is ever raised. Not a runtime test.
        ask_timeout = ask_bridge.DEFAULT_TIMEOUT_SECONDS
        assert ask_timeout >= 60, "the ask timeout is no longer minutes-scale"
        assert MAX_TOOL_ROUNDS * 4 * ask_timeout > 1800, (
            "the unbounded figure is no longer half an hour; re-measure before "
            "assuming this still matters")
        assert ask_timeout > assistant_mod.MAX_TURN_SECONDS / 2, (
            "a single ask can no longer exceed half the turn budget, so the inner "
            "check matters less than when this was written")
