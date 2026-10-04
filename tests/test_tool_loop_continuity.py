"""An interrupted turn used to poison every request after it.

Three changes to `Assistant.handle()` are pinned here, and they share one
premise: a turn that stops partway through a batch of tool calls leaves
`tool_calls` in `_history` with no results beside them. That is not a cosmetic
gap. **Every later request carries it** - the next turn, and every turn after
that, because `_history` is the conversation. So one interrupted turn does not
cost the user one turn; it degrades the rest of the session, and it does so
silently on Ollama and loudly on the cloud fallbacks, which reject the whole
request with `No tool call found for function call output`.

The three mechanisms, and why each is separate:

- **Repair on every request** (`history_repair.clean_history`) is the backstop.
  It cannot fix history it is not given, and it must not mutate `_history` -
  that is the record, and the on-disk transcript with it.
- **Repair at the end of an interrupted turn** (`close_interrupted_turn`) is what
  actually fixes the record. Going through `_record` is the part that matters:
  a turn interrupted by a shutdown is repaired in the transcript, and is
  therefore already correct when the next *process* reads it back.
- **Per-tool attempt budgets** stop one tool eating a round, so a run that also
  needed a different tool still gets it.

Verified with a real `OllamaLLM` over `httpx.MockTransport` (sanctioned by
`AGENTS.md` for exactly this loop), so the round-trip is genuinely
`parse tool call -> execute -> feed result back -> final answer` rather than a
stub's account of it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import assistant as assistant_mod  # noqa: E402
from shani_chronoa import history_repair as hr  # noqa: E402
from shani_chronoa.assistant import (  # noqa: E402
    DEFAULT_TOOL_ATTEMPTS_PER_ROUND,
    MAX_TOOL_ROUNDS,
    Assistant,
)
from shani_chronoa.ollama_llm import OllamaLLM  # noqa: E402
from shani_chronoa.loops import LOOP_THRESHOLD  # noqa: E402


def _call(call_id: str, name: str, arguments: dict = None) -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments or {}}}


def _turn_reply(calls) -> dict:
    return {"message": {"role": "assistant", "content": "", "tool_calls": calls},
            "prompt_eval_count": 10, "eval_count": 5}


def _final_reply(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text},
            "prompt_eval_count": 12, "eval_count": 7}


class _MockOllama:
    """A real `OllamaLLM` whose HTTP transport is replaced, replies queued."""

    def __init__(self, replies, executed=None):
        self.replies = list(replies)
        self.requests: "list[dict]" = []
        self.executed = executed if executed is not None else []
        self._transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        reply = self.replies.pop(0) if self.replies else _final_reply("done")
        return httpx.Response(200, json=reply)

    def build(self) -> OllamaLLM:
        llm = OllamaLLM(model="qwen3:4b")
        llm._get_client = _immediate(llm)  # type: ignore[method-assign]
        llm._transport = self._transport  # type: ignore[attr-defined]
        return llm


def _immediate(llm):
    async def get():
        if llm.client is None or llm.client.is_closed:
            client = httpx.AsyncClient(transport=llm._transport,
                                       base_url=llm.host, timeout=5.0)
            llm.client = client
        return llm.client
    return get


@pytest.fixture
def no_tools(monkeypatch):
    """Record tool dispatches instead of running real skills."""
    ran: "list[str]" = []

    def _execute(name, arguments):
        ran.append(name)
        return f"{name} ok"

    monkeypatch.setattr(assistant_mod, "execute_tool", _execute)
    return ran


class TestTheRealLoopOverMockTransport:
    """The whole point of using a mock transport rather than a stub object.

    A stub `chat_message` cannot catch a request whose *payload* is invalid, and
    an invalid payload is the entire failure being fixed here.
    """

    def test_a_tool_call_round_trips_and_the_prompt_is_valid(self, no_tools):
        mock = _MockOllama([
            _turn_reply([_call("c1", "get_datetime")]),
            _final_reply("It is half past six."),
        ])
        answer = asyncio.run(Assistant(mock.build()).handle("what time is it?"))

        assert answer == "It is half past six."
        assert no_tools == ["get_datetime"]
        # The second request is the one that matters: it carries the result back
        # to the model, correlated by id.
        second = mock.requests[1]["messages"]
        assert any(m.get("tool_call_id") == "c1" for m in second), (
            f"the result did not reach the model: {second}")
        # Every tool result in the prompt answers a call made before it.
        ids = {c["id"] for m in second if m.get("tool_calls")
               for c in m["tool_calls"]}
        for message in second:
            if message.get("role") == "tool" and message.get("tool_call_id"):
                assert message["tool_call_id"] in ids, (
                    f"a result reached the wire with no call: {message}")


class _SlowTool:
    """Dispatch where *every* call is slow, so the budget expires partway through
    a batch rather than before it or after it.

    Two earlier attempts at this helper reached the wrong state, and both looked
    like a broken implementation rather than a broken fixture:

    - A budget of `0.0` expires *before the first model call*, so the turn
      returns having made no calls at all. Nothing to repair, the assertion
      passed, and the test proved nothing - a control that cannot fail.
    - Sleeping only on the first call either exceeds the whole budget (1 call
      runs, so the batch is not cut *mid-way*) or fits inside it (all 4 run).
      There is no middle. A uniform per-call delay is what actually splits a
      batch, and the figures below were measured, not guessed: 0.2s per call
      against a 0.45s budget runs 3 of 4.

    Arguments are distinct per call because identical ones are `LoopDetector`'s
    case, and it would end the turn at `LOOP_THRESHOLD` before the budget was
    consulted - which is correct behaviour and would mask what is under test.
    """

    def __init__(self, seconds: float = 0.2):
        self.seconds = seconds
        self.ran: "list[str]" = []

    def __call__(self, name, arguments):
        import time as _time
        self.ran.append(name)
        _time.sleep(self.seconds)
        return f"{name} ok"


def _interrupted_batch() -> "list[dict]":
    """Four calls to one tool with distinct arguments, and a budget that expires
    after the third. Measured: 3 of 4 run."""
    return [_call(f"c{i}", "slow", {"k": i}) for i in (1, 2, 3, 4)]


#: Per-call delay and budget for `_interrupted_batch()`. See `_SlowTool`.
_SLOW_SECONDS = 0.2
_BUDGET_SECONDS = 0.45
_BATCH_SIZE = 4


class TestInterruptedTurnsAreClosedOut:
    def test_a_turn_cut_off_mid_batch_leaves_no_unpaired_call(self, monkeypatch):
        """The bug, end to end, through the real loop.

        The budget is checked before each dispatch, so a turn that runs out
        partway through a batch has already recorded the assistant message that
        asked for all of them. Before this, that stayed in `_history` forever.
        """
        slow = _SlowTool(_SLOW_SECONDS)
        monkeypatch.setattr(assistant_mod, "execute_tool", slow)
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", _BUDGET_SECONDS)

        mock = _MockOllama([_turn_reply(_interrupted_batch())])
        assistant = Assistant(mock.build())
        answer = asyncio.run(assistant.handle("what time is it?"))

        assert "ran out of time" in answer
        # The batch really was cut off partway. Without this the test would pass
        # on a turn that was never interrupted.
        assert 0 < len(slow.ran) < _BATCH_SIZE, (
            f"{len(slow.ran)} of {_BATCH_SIZE} calls ran, so the batch was not "
            f"cut off mid-way and this test asserts nothing")
        assert hr.unpaired_tool_calls(assistant._history) == [], (
            "the interrupted turn left an unpaired call in _history, so every "
            f"later request carries it: {assistant._history}")

    def test_the_calls_that_ran_keep_their_real_results(self, monkeypatch):
        """A repair that overwrote a genuine result would be worse than the bug:
        the model would be told nothing ran when something did."""
        slow = _SlowTool(_SLOW_SECONDS)
        monkeypatch.setattr(assistant_mod, "execute_tool", slow)
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", _BUDGET_SECONDS)
        mock = _MockOllama([_turn_reply(_interrupted_batch())])
        assistant = Assistant(mock.build())
        asyncio.run(assistant.handle("what time is it?"))
        assert 0 < len(slow.ran) < _BATCH_SIZE, "the batch was not cut off"

        real = [m for m in assistant._history
                if m.get("role") == "tool" and not m.get(hr.SYNTHESIZED_KEY)]
        made_up = [m for m in assistant._history if m.get(hr.SYNTHESIZED_KEY)]
        assert len(real) == len(slow.ran), (
            f"{len(slow.ran)} tools ran but {len(real)} real results were kept")
        assert made_up, "nothing was repaired despite an interrupted batch"
        assert all(m[hr.REASON_KEY] == "budget" for m in made_up)


class TestInterruptedTurnsAreClosedOut:
    def test_the_repair_reaches_the_saved_transcript(self, tmp_path, monkeypatch):
        """The reason `close_interrupted_turn` goes through `_record`.

        A turn interrupted by a shutdown is never seen again by this process. If
        the repair were applied to `_history` alone it would be lost, and the
        next process would restore the broken history from disk and send it.
        """
        slow = _SlowTool(_SLOW_SECONDS)
        monkeypatch.setattr(assistant_mod, "execute_tool", slow)
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", _BUDGET_SECONDS)
        transcript = tmp_path / "t.jsonl"
        mock = _MockOllama([_turn_reply(_interrupted_batch())])
        asyncio.run(Assistant(mock.build(), session_path=transcript)
                    .handle("what time is it?"))
        assert 0 < len(slow.ran) < _BATCH_SIZE, "the batch was not cut off"

        restored = [json.loads(line) for line in
                    transcript.read_text(encoding="utf-8").splitlines() if line]
        assert hr.unpaired_tool_calls(restored) == [], (
            "the repair did not survive to disk; the next process restores "
            f"this and sends it: {restored}")

        # And the real thing: a *new* Assistant reading that file back produces
        # a history that is already valid, with no repair needed at request time.
        reopened = Assistant(_MockOllama([_final_reply("hi")]).build(),
                             session_path=transcript)
        assert hr.unpaired_tool_calls(reopened._history) == []
        assert any(m.get(hr.SYNTHESIZED_KEY) for m in reopened._history)

    def test_a_backend_failure_mid_batch_still_closes_out(self, monkeypatch):
        """`ConnectionError` from a backend that died *between tool calls*.

        The failure has to land inside the batch. A backend that dies between
        *rounds* leaves a turn whose tool loop already finished, so the history
        is already valid and the handler is not what saves it - the first version
        of this test did exactly that, passed with the handler deleted, and
        proved nothing.
        """
        state = {"calls": 0}

        def _execute(name, arguments):
            state["calls"] += 1
            if state["calls"] == 2:
                raise ConnectionError("Ollama server not available")
            return f"{name} ok"

        monkeypatch.setattr(assistant_mod, "execute_tool", _execute)

        class _Dying(OllamaLLM):
            async def chat_message(self, messages, tools=None, stream=False):
                return {"role": "assistant", "content": "", "tool_calls": [
                    _call(f"c{i}", "get_datetime", {"k": i}) for i in (1, 2, 3)]}

        assistant = Assistant(_Dying())
        with pytest.raises(ConnectionError):
            asyncio.run(assistant.handle("what time is it?"))
        # The batch really was cut mid-way, so a handler is the only thing that
        # can have closed it.
        assert state["calls"] == 2, (
            f"{state['calls']} calls ran, so the turn was not cut off mid-batch "
            f"and this test asserts nothing")
        assert hr.unpaired_tool_calls(assistant._history) == [], (
            f"a failed turn left unpaired calls: {assistant._history}")

    def test_a_cancellation_mid_batch_still_closes_out(self, monkeypatch):
        """`AsyncBridge.cancel_pending()` at shutdown. `CancelledError` is a
        `BaseException`, so an `except Exception` handler misses it entirely and
        the history stays broken through the most common interruption this app
        has - the user closing the window.

        The `BaseException` in the source is load-bearing: with `except
        Exception` this is the one test that goes red, and it is the interruption
        that actually happens.
        """
        state = {"calls": 0}

        def _execute(name, arguments):
            state["calls"] += 1
            if state["calls"] == 2:
                raise asyncio.CancelledError()
            return f"{name} ok"

        monkeypatch.setattr(assistant_mod, "execute_tool", _execute)

        class _Cancelled(OllamaLLM):
            async def chat_message(self, messages, tools=None, stream=False):
                return {"role": "assistant", "content": "", "tool_calls": [
                    _call(f"c{i}", "get_datetime", {"k": i}) for i in (1, 2, 3)]}

        assistant = Assistant(_Cancelled())
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(assistant.handle("what time is it?"))
        assert state["calls"] == 2, (
            f"{state['calls']} calls ran, so the turn was not cut off mid-batch "
            f"and this test asserts nothing")
        assert hr.unpaired_tool_calls(assistant._history) == [], (
            f"a cancelled turn left unpaired calls: {assistant._history}")

    def test_a_tool_that_raises_does_not_leave_a_dangling_call(self, monkeypatch):
        """The ordinary case, and the one most likely to be hit: a skill throws.

        `execute_tool` normally returns a string rather than raising, but a
        subprocess that dies in an unexpected way, or a bug in a skill, reaches
        here - and the exception propagates out of `handle()` to `app.py`, which
        shows the user an error. The history has to survive that.
        """
        def _execute(name, arguments):
            raise RuntimeError("skill crashed")

        monkeypatch.setattr(assistant_mod, "execute_tool", _execute)

        class _Model(OllamaLLM):
            async def chat_message(self, messages, tools=None, stream=False):
                return {"role": "assistant", "content": "", "tool_calls": [
                    _call("c1", "get_datetime")]}

        assistant = Assistant(_Model())
        with pytest.raises(RuntimeError):
            asyncio.run(assistant.handle("what time is it?"))
        assert hr.unpaired_tool_calls(assistant._history) == [], (
            f"a crashing tool left an unpaired call: {assistant._history}")

    def test_a_turn_with_nothing_to_repair_is_untouched(self, no_tools):
        mock = _MockOllama([_final_reply("hello")])
        assistant = Assistant(mock.build())
        asyncio.run(assistant.handle("hi"))
        before = list(assistant._history)
        assert assistant.close_interrupted_turn("interrupted") == []
        assert assistant._history == before, "a complete turn was modified"

    def test_the_next_request_is_told_what_happened(self, monkeypatch):
        """The model opens the next turn needing to know the state of the
        conversation, and results saying 'nothing ran' do not say why."""
        slow = _SlowTool(_SLOW_SECONDS)
        monkeypatch.setattr(assistant_mod, "execute_tool", slow)
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", _BUDGET_SECONDS)
        mock = _MockOllama([_turn_reply(_interrupted_batch())])
        assistant = Assistant(mock.build())
        asyncio.run(assistant.handle("what time is it?"))
        assert 0 < len(slow.ran) < _BATCH_SIZE, "the batch was not cut off"

        assistant_mod.MAX_TURN_SECONDS = 300.0
        mock.replies.append(_final_reply("sorry, that got cut off"))
        asyncio.run(assistant.handle("never mind"))

        sent = json.dumps(mock.requests[-1]["messages"])
        assert "interrupted" in sent, (
            "the next turn's prompt does not say the last one was interrupted")
        assert "not executed" in sent, (
            "the next turn's prompt does not say the call did not run")

    def test_the_notice_is_sent_once_and_only_once(self, monkeypatch):
        """It is context about the *previous* turn. Re-sending it makes a stale
        explanation look like a current event."""
        slow = _SlowTool(_SLOW_SECONDS)
        monkeypatch.setattr(assistant_mod, "execute_tool", slow)
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", _BUDGET_SECONDS)
        mock = _MockOllama([_turn_reply(_interrupted_batch())])
        assistant = Assistant(mock.build())
        asyncio.run(assistant.handle("what time is it?"))
        assert 0 < len(slow.ran) < _BATCH_SIZE, "the batch was not cut off"
        assistant_mod.MAX_TURN_SECONDS = 300.0

        for _ in range(2):
            mock.replies.append(_final_reply("ok"))
            asyncio.run(assistant.handle("again"))

        assert "interrupted" in json.dumps(mock.requests[-2]["messages"])
        assert "interrupted" not in json.dumps(mock.requests[-1]["messages"])

    def test_the_notice_is_not_recorded_as_something_the_assistant_said(
            self, monkeypatch):
        """The transcript is a record of what happened. A turn that was cut off
        produced no assistant message, so inventing one would put words in the
        assistant's mouth in a file the user can read back."""
        slow = _SlowTool(_SLOW_SECONDS)
        monkeypatch.setattr(assistant_mod, "execute_tool", slow)
        monkeypatch.setattr(assistant_mod, "MAX_TURN_SECONDS", _BUDGET_SECONDS)
        mock = _MockOllama([_turn_reply(_interrupted_batch())])
        assistant = Assistant(mock.build())
        asyncio.run(assistant.handle("what time is it?"))
        assert 0 < len(slow.ran) < _BATCH_SIZE, "the batch was not cut off"

        assert all(hr.CONTINUE_PROMPT not in str(m.get("content", ""))
                   for m in assistant._history), (
            "the continue prompt was written into the conversation as though "
            "the assistant had said it")
        # ... but the repair *is* recorded, because it is a true fact about a
        # call that was made and not answered.
        assert any(m.get(hr.SYNTHESIZED_KEY) for m in assistant._history)


class TestPerToolAttemptBudgets:
    def test_one_tool_cannot_eat_a_whole_round(self, no_tools):
        """The gap `MAX_TOOL_ROUNDS` and `LoopDetector` both cannot see.

        `MAX_TOOL_ROUNDS` bounds rounds. `LoopDetector` bounds *identical
        arguments in a row*. A model that is failing and rephrasing produces
        calls that are all different, so neither notices - and the tool the turn
        also needed never runs at all.
        """
        calls = [_call(f"c{i}", "web_search", {"query": f"query {i}"})
                 for i in range(12)]
        mock = _MockOllama([_turn_reply(calls), _final_reply("giving up")])
        asyncio.run(Assistant(mock.build()).handle("research this"))

        assert len(no_tools) == DEFAULT_TOOL_ATTEMPTS_PER_ROUND, (
            f"{len(no_tools)} calls to one tool ran in a single round; the "
            f"per-tool budget is not doing anything")

    def test_a_different_tool_still_runs(self, no_tools):
        """The point of a *per-tool* budget: one bad tool does not consume the
        round, so the tool the turn actually needed is still reached."""
        calls = [_call(f"w{i}", "web_search", {"query": f"q{i}"})
                 for i in range(10)]
        calls.append(_call("d1", "get_datetime"))
        mock = _MockOllama([_turn_reply(calls), _final_reply("here you go")])
        asyncio.run(Assistant(mock.build()).handle("look it up and tell me the time"))

        assert "get_datetime" in no_tools, (
            "one tool's budget consumed the round and starved a second tool")
        assert no_tools.count("web_search") == DEFAULT_TOOL_ATTEMPTS_PER_ROUND

    def test_the_refusal_carries_the_original_call_id(self, no_tools):
        """Not cosmetic. The refused call still needs a result, or the very next
        request carries an unpaired call - the fix would cause the bug."""
        calls = [_call(f"c{i}", "web_search", {"query": f"q{i}"})
                 for i in range(10)]
        mock = _MockOllama([_turn_reply(calls),
                             _turn_reply([_call("later", "get_datetime")]),
                             _final_reply("done")])
        assistant = Assistant(mock.build())
        asyncio.run(assistant.handle("research this"))

        results = [m for m in assistant._history if m.get("role") == "tool"]
        refused = [m for m in results if m.get(hr.REASON_KEY) == "limit"]
        assert len(refused) == 10 - DEFAULT_TOOL_ATTEMPTS_PER_ROUND
        assert all(m["tool_call_id"].startswith("c") for m in refused), (
            "a refusal lost the call it was refusing, so it answers nothing")
        assert hr.unpaired_tool_calls(assistant._history) == []

    def test_the_refusal_is_in_band_and_tells_the_model_not_to_retry(self, no_tools):
        """agno's in-band shape (`libs/agno/agno/models/base.py:2217`): a
        synthetic result, not an exception. An exception here leaves the turn
        half-cleaned and the user reading a traceback instead of an answer."""
        calls = [_call(f"c{i}", "web_search", {"query": f"q{i}"})
                 for i in range(10)]
        mock = _MockOllama([_turn_reply(calls), _final_reply("no answer")])
        answer = asyncio.run(Assistant(mock.build()).handle("research this"))

        assert answer == "no answer", "the refusal broke the turn instead of "\
            f"ending it naturally: {answer!r}"

    def test_the_refusal_message_says_the_limit_not_a_failure(self, no_tools):
        calls = [_call(f"c{i}", "web_search", {"query": f"q{i}"})
                 for i in range(10)]
        mock = _MockOllama([_turn_reply(calls), _final_reply("x")])
        assistant = Assistant(mock.build())
        asyncio.run(assistant.handle("research this"))
        refused = [m for m in assistant._history
                   if m.get(hr.REASON_KEY) == "limit"][0]
        content = refused["content"].lower()
        assert "do not call it again" in content, (
            "the model is not told to stop retrying, so it will: "
            f"{refused['content']!r}")
        assert "do not assume it failed" in content, (
            "the model is not told the tool is broken, so it will report a "
            f"failure that did not happen: {refused['content']!r}")

    def test_legitimate_use_of_one_tool_is_unaffected(self, no_tools, monkeypatch):
        """Below the budget, a tool called repeatedly in a round still runs every
        time. `loops.py` already learned this lesson the hard way: a rule that
        cannot tell a re-read from a stuck turn fires on people."""
        calls = [_call(f"c{i}", "get_datetime") for i in range(3)]
        mock = _MockOllama([_turn_reply(calls), _final_reply("it is late")])
        asyncio.run(Assistant(mock.build()).handle("what time is it?"))
        assert no_tools.count("get_datetime") == 3, (
            f"only {no_tools.count('get_datetime')} of 3 calls ran")

    def test_the_budget_resets_between_rounds(self, no_tools):
        """Per *round*, and that is deliberate: `tests/test_turn_budget_inside_
        tool_loop.py` pins sixteen legitimate `ask_user` calls in one turn, so a
        per-turn per-name budget below 16 would refuse real questions."""
        replies = []
        for r in range(MAX_TOOL_ROUNDS):
            replies.append(_turn_reply([_call(f"c{r}", "web_search",
                                              {"query": f"q{r}"})]))
        replies.append(_final_reply("done"))
        mock = _MockOllama(replies)
        asyncio.run(Assistant(mock.build()).handle("research this"))
        assert len(no_tools) == MAX_TOOL_ROUNDS, (
            f"{len(no_tools)} calls ran across {MAX_TOOL_ROUNDS} rounds; the "
            f"budget is being applied per turn rather than per round")

    def test_the_budget_sits_at_the_loop_threshold(self):
        """Ordering between the two bounds, and it is load-bearing both ways.

        Below the loop threshold, this budget fires first and steals the turn's
        ending - `tests/test_loops.py` requires the *loop detector's* wording for
        a run of identical calls, and got a bare refusal instead.
        """
        assert DEFAULT_TOOL_ATTEMPTS_PER_ROUND >= LOOP_THRESHOLD, (
            "the per-tool budget fires before the loop detector, so a run of "
            "identical calls is ended by a refusal instead of by the mechanism "
            "that can explain it")

    def test_the_loop_detector_still_owns_identical_calls(self, no_tools):
        """A run of *identical* calls is judged by the mechanism that explains
        it. The per-tool budget is the backstop for calls that are all different,
        which the detector cannot see."""
        calls = [_call(f"c{i}", "get_battery_status") for i in range(12)]
        mock = _MockOllama([_turn_reply(calls)])
        answer = asyncio.run(Assistant(mock.build()).handle("check the battery"))
        assert "identical arguments" in answer, (
            f"the loop detector's explanation was replaced: {answer!r}")

    def test_an_override_raises_one_tools_budget(self, no_tools, monkeypatch):
        """A skill legitimately called several times in a round needs a way to
        say so, and it must not require editing the loop.

        Arguments are made distinct because identical ones are the *loop
        detector's* case, and it stops them at `LOOP_THRESHOLD` regardless of any
        budget - which is correct, and which the first version of this test
        tripped over by using ten identical calls.
        """
        monkeypatch.setitem(assistant_mod.TOOL_ATTEMPT_OVERRIDES,
                            "check_timer", 10)
        calls = [_call(f"c{i}", "check_timer", {"id": f"t{i}"})
                 for i in range(8)]
        mock = _MockOllama([_turn_reply(calls), _final_reply("done")])
        asyncio.run(Assistant(mock.build()).handle("check my timers"))
        assert len(no_tools) == 8, (
            f"the override did not apply: only {len(no_tools)} of 8 ran, so the "
            f"per-tool budget refused them despite the override")
