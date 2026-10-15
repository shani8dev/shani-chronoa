"""How hard the model thinks: one setting, translated per engine, read off the wire.

There was no such control. A local Qwen3 had `enable_thinking: False` hard-coded
on every request and no cloud provider was ever asked for an effort. The setting
is one word (`cloud_llm.THINKING_LEVELS`) and every engine says it differently,
so **each test here reads the request that actually left**, over
`httpx.MockTransport` - a test of the helper that builds the fields is not a
test that they reach the provider.

Off sends nothing at all, which is the request every engine sent before this
existed. That is pinned too, because a regression there changes the behaviour
of every install that never touched the control.
"""
import asyncio
import json

import httpx
import pytest

from shani_chronoa import cloud_llm, local_llm
from shani_chronoa.cloud_llm import (PROVIDERS, AnthropicLLM, CloudLLMChain,
                                     OpenAICompatibleLLM, thinking_level)
from shani_chronoa.ollama_llm import OllamaLLM


class _Wire:
    """Records each request body; answers from `respond(body) -> (status, json)`."""

    def __init__(self, respond):
        self.respond = respond
        self.bodies: "list[dict]" = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        status, payload = self.respond(body)
        return httpx.Response(status, json=payload)

    def attach(self, llm, base_url: str):
        transport = httpx.MockTransport(self._handle)

        async def get():
            return httpx.AsyncClient(transport=transport, base_url=base_url)
        llm._get_client = get
        return llm


def _openai_ok(_body):
    return 200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


def _openai(provider_id="openai", thinking="off", respond=_openai_ok):
    wire = _Wire(respond)
    llm = OpenAICompatibleLLM(PROVIDERS[provider_id], api_key="k")
    llm.thinking = thinking
    wire.attach(llm, "https://example.invalid/v1")
    asyncio.run(llm.chat_message([{"role": "user", "content": "hi"}]))
    return llm, wire.bodies


# --- the word ----------------------------------------------------------------

def test_an_unknown_level_reads_as_off():
    assert thinking_level("MAX") == "max"
    assert thinking_level("turbo") == "off"
    assert thinking_level(None) == "off"


# --- OpenAI-shaped providers ---------------------------------------------------

def test_off_sends_no_thinking_field():
    _llm, bodies = _openai(thinking="off")
    assert "reasoning_effort" not in bodies[0] and "reasoning" not in bodies[0], bodies[0]


@pytest.mark.parametrize("level,sent", [
    ("low", "low"), ("medium", "medium"), ("high", "high"),
    ("xhigh", "xhigh"), ("max", "xhigh"),
])
def test_each_level_reaches_the_request(level, sent):
    _llm, bodies = _openai(thinking=level)
    assert bodies[0]["reasoning_effort"] == sent, bodies[0]


def test_openrouter_gets_its_own_shape():
    _llm, bodies = _openai("openrouter", thinking="medium")
    assert bodies[0]["reasoning"] == {"effort": "medium"}, bodies[0]
    assert "reasoning_effort" not in bodies[0]


def test_a_refused_top_level_steps_down_to_high():
    def respond(body):
        if body.get("reasoning_effort") == "xhigh":
            return 400, {"error": {"message": "Unsupported value: 'reasoning_effort' "
                                              "does not support 'xhigh' with this model."}}
        return _openai_ok(body)
    llm, bodies = _openai(thinking="max", respond=respond)
    assert [b.get("reasoning_effort") for b in bodies] == ["xhigh", "high"], bodies
    assert llm._thinking_cap == "high", "the step-down is paid again every turn"


def test_a_model_that_cannot_think_is_asked_without_it():
    def respond(body):
        if "reasoning_effort" in body:
            return 400, {"error": {"message": "Unrecognized request argument "
                                              "supplied: reasoning_effort"}}
        return _openai_ok(body)
    llm, bodies = _openai(thinking="high", respond=respond)
    assert [("reasoning_effort" in b) for b in bodies] == [True, False], bodies
    assert llm._thinking_refused


def test_an_unrelated_error_is_not_retried_without_thinking():
    def respond(_body):
        return 400, {"error": {"message": "invalid api key"}}
    with pytest.raises(cloud_llm.CloudLLMError):
        _openai(thinking="high", respond=respond)


# --- the local llama-server ---------------------------------------------------

def test_the_local_model_thinks_only_when_asked():
    llm = local_llm.LocalLLM()
    assert llm._payload_extra()["chat_template_kwargs"]["enable_thinking"] is False
    assert llm._thinking_fields() == {}, "llama-server would be sent reasoning_effort too"
    llm.thinking = "low"
    assert llm._payload_extra()["chat_template_kwargs"]["enable_thinking"] is True
    # The JSON helpers (titles, tool picks) never think: nobody reads it.
    assert llm._payload_extra(think=False)["chat_template_kwargs"]["enable_thinking"] is False


def test_the_local_request_carries_the_switch(monkeypatch):
    wire = _Wire(_openai_ok)
    llm = local_llm.LocalLLM()
    llm.thinking = "high"
    wire.attach(llm, "http://127.0.0.1:1/v1")
    monkeypatch.setattr(local_llm, "COMPACT_TOOLS", False, raising=False)
    asyncio.run(llm.chat_message([{"role": "user", "content": "hi"}]))
    assert wire.bodies[0]["chat_template_kwargs"] == {"enable_thinking": True}, wire.bodies[0]
    assert "reasoning_effort" not in wire.bodies[0]


# --- Ollama -------------------------------------------------------------------

def _ollama_ok(_body):
    return 200, {"message": {"role": "assistant", "content": "ok"}}


def _ollama(thinking, respond=_ollama_ok):
    wire = _Wire(respond)
    llm = OllamaLLM(model="qwen3:4b")
    llm.thinking = thinking
    wire.attach(llm, "http://127.0.0.1:1")
    asyncio.run(llm.chat_message([{"role": "user", "content": "hi"}]))
    return llm, wire.bodies


def test_ollama_off_sends_no_think():
    _llm, bodies = _ollama("off")
    assert "think" not in bodies[0]


def test_ollama_any_level_turns_think_on():
    _llm, bodies = _ollama("medium")
    assert bodies[0]["think"] is True


def test_ollama_a_model_without_thinking_is_asked_again_without_it():
    def respond(body):
        if body.get("think"):
            return 400, {"error": '"llama3" does not support thinking'}
        return _ollama_ok(body)
    llm, bodies = _ollama("high", respond)
    assert [("think" in b) for b in bodies] == [True, False], bodies
    assert llm._thinking_refused


# --- Anthropic ----------------------------------------------------------------

def _anthropic(thinking, respond, messages=None):
    wire = _Wire(respond)
    llm = AnthropicLLM(api_key="k")
    llm.thinking = thinking
    wire.attach(llm, "https://example.invalid/v1")
    reply = asyncio.run(llm.chat_message(messages or [{"role": "user", "content": "hi"}]))
    return reply, wire.bodies


def _anthropic_ok(_body):
    return 200, {"content": [{"type": "text", "text": "ok"}]}


def test_anthropic_off_sends_no_thinking():
    _reply, bodies = _anthropic("off", _anthropic_ok)
    assert "thinking" not in bodies[0]
    assert bodies[0]["max_tokens"] == cloud_llm._ANTHROPIC_MAX_TOKENS


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max"])
def test_anthropic_gets_a_budget_below_max_tokens(level):
    _reply, bodies = _anthropic(level, _anthropic_ok)
    budget = cloud_llm._ANTHROPIC_THINKING_BUDGET[level]
    assert bodies[0]["thinking"] == {"type": "enabled", "budget_tokens": budget}
    assert bodies[0]["max_tokens"] > budget, "the API refuses max_tokens <= budget"


def test_anthropic_thinking_survives_a_tool_loop():
    """The API refuses a tool loop whose assistant turn lost its thinking block."""
    block = {"type": "thinking", "thinking": "check the clock", "signature": "sig"}

    def first(_body):
        return 200, {"content": [block, {"type": "tool_use", "id": "t1",
                                         "name": "get_datetime", "input": {}}]}
    reply, _ = _anthropic("high", first)
    assert reply["anthropic_thinking"] == [block]

    history = [{"role": "user", "content": "time?"}, reply,
               {"role": "tool", "tool_call_id": "t1", "content": "18:30"}]
    _reply, bodies = _anthropic("high", _anthropic_ok, history)
    assistant_turn = bodies[0]["messages"][1]["content"]
    assert assistant_turn[0] == block, assistant_turn
    assert assistant_turn[1]["type"] == "tool_use"


def test_anthropic_answers_without_thinking_when_it_is_refused():
    def respond(body):
        if "thinking" in body:
            return 400, {"error": {"type": "invalid_request_error", "message":
                                   "Expected `thinking` or `redacted_thinking`, but found `tool_use`."}}
        return _anthropic_ok(body)
    reply, bodies = _anthropic("high", respond)
    assert reply["content"] == "ok"
    assert [("thinking" in b) for b in bodies] == [True, False]


# --- the chain and the assistant ------------------------------------------------

def test_the_chain_hands_the_level_to_every_provider():
    chain = CloudLLMChain(("llm7", "kilo", "anthropic"), api_keys={"anthropic": "k"})
    chain.thinking = "high"
    assert [b.thinking for b in chain._backends] == ["high", "high", "high"]


def test_the_assistant_sets_the_level_before_every_call():
    from shani_chronoa.assistant import Assistant

    class _Config:
        thinking = "xhigh"

    seen = []

    class _LLM:
        thinking = "off"

        async def chat_message(self, messages, tools=None, stream=False):
            seen.append(self.thinking)
            return {"role": "assistant", "content": "ok"}

    assistant = Assistant(_LLM(), config=_Config())
    asyncio.run(assistant._ask([{"role": "user", "content": "hi"}], None, None))
    assert seen == ["xhigh"], "the setting never reached the engine"


# --- the control beside "Answers with" -------------------------------------------

def test_the_menu_offers_every_level_and_each_row_targets_its_own():
    """Read off the built menu, as `test_answer_control_is_beside_send` does:
    a row that renders the right word and fires the wrong target looks fine."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from shani_chronoa.gui.window import ChronoaWindow

    class _Shell:
        _THINKING_WORDS = ChronoaWindow._THINKING_WORDS

    button = ChronoaWindow._thinking_button(_Shell())
    model = button.get_menu_model()
    levels = model.get_item_link(0, "section")
    assert model.get_item_attribute_value(0, "label", None).get_string() == "How hard it thinks"
    rows = [(levels.get_item_attribute_value(i, "action", None).get_string(),
             levels.get_item_attribute_value(i, "target", None).get_string())
            for i in range(levels.get_n_items())]
    assert rows == [("app.thinking", level) for level in cloud_llm.THINKING_LEVELS], rows
