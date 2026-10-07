"""The tool loop under the new budgets, over a real OllamaLLM with a mock
transport: per-origin round budgets and the malformed-call ceiling, verified
against the exact requests the client sends."""

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
from shani_chronoa.assistant import Assistant  # noqa: E402
from shani_chronoa.ollama_llm import OllamaLLM  # noqa: E402


def _call(call_id: str, name: str, arguments) -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


def _turn_reply(calls) -> dict:
    return {"message": {"role": "assistant", "content": "", "tool_calls": calls},
            "prompt_eval_count": 10, "eval_count": 5}


def _final_reply(text: str) -> dict:
    return {"message": {"role": "assistant", "content": text},
            "prompt_eval_count": 12, "eval_count": 7}


class _MockOllama:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self._transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        reply = self.replies.pop(0) if self.replies else _final_reply("done")
        return httpx.Response(200, json=reply)

    def build(self) -> OllamaLLM:
        llm = OllamaLLM(model="qwen3:4b")
        async def _get():
            if llm.client is None or llm.client.is_closed:
                llm.client = httpx.AsyncClient(transport=self._transport,
                                               base_url=llm.host, timeout=5.0)
            return llm.client
        llm._get_client = _get  # type: ignore[method-assign]
        return llm


@pytest.fixture
def recorded(monkeypatch):
    ran = []
    monkeypatch.setattr(assistant_mod, "execute_tool",
                        lambda name, args, origin=None: ran.append(name) or f"{name} ok")
    return ran


def test_unattended_origin_gets_one_round(recorded):
    mock = _MockOllama([
        _turn_reply([_call("c1", "get_datetime", {})]),
        _final_reply("the time is half past six"),
    ])
    answer = asyncio.run(Assistant(mock.build()).handle(
        "what time is it?", origin="unattended"))
    assert answer == "the time is half past six"
    # One tool round, not four: an unattended turn that kept calling tools
    # would be a turn nobody could stop.
    assert recorded == ["get_datetime"]
    assert len(mock.requests) == 2


def test_unknown_origin_also_gets_one_round(recorded):
    mock = _MockOllama([
        _turn_reply([_call("c1", "get_datetime", {})]),
        _final_reply("the time is half past six"),
    ])
    answer = asyncio.run(Assistant(mock.build()).handle(
        "what time is it?", origin="no-such-origin"))
    assert answer == "the time is half past six"
    assert recorded == ["get_datetime"]


def test_malformed_arguments_stop_after_three(recorded):
    mock = _MockOllama([
        _turn_reply([_call("c1", "get_datetime", "{not json")]),
        _turn_reply([_call("c2", "get_datetime", "{not json")]),
        _turn_reply([_call("c3", "get_datetime", "{not json")]),
        _final_reply("should not be reached"),
    ])
    answer = asyncio.run(Assistant(mock.build()).handle("what time is it?"))
    assert "could not form a valid call" in answer
    # Three refusals, not ever-four-rounds, and nothing ever ran.
    assert recorded == []
    assert len(mock.requests) == 3


def test_a_good_call_resets_the_malformed_streak(recorded):
    mock = _MockOllama([
        _turn_reply([_call("c1", "get_datetime", "{not json")]),
        _turn_reply([_call("c2", "get_datetime", {})]),
        _turn_reply([_call("c3", "get_datetime", "{not json")]),
        _final_reply("done now"),
    ])
    answer = asyncio.run(Assistant(mock.build()).handle("what time is it?"))
    assert answer == "done now"
    assert recorded == ["get_datetime"]
