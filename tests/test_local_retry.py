"""The local model's second chance: a clear command answered in words is asked again, narrowly.

Measured need (tools/task_eval.py, Qwen3-0.6B, 2026-10-02): "lock my screen",
"mute the sound", "is my internet working?" and seven more were answered in
words instead of with the tool, in every config. Driven through the real
LocalLLM client against a stub llama-server, so the payload checked is the one
that would be sent.
"""

import asyncio
import json

import httpx
import pytest

from shani_chronoa import local_llm


def _names(tools):
    return [t["function"]["name"] for t in tools]


def test_a_clear_command_gets_a_narrow_set_with_an_escape():
    tools = local_llm.forced_tools([{"role": "user", "content": "lock my screen"}])
    assert "lock_screen" in _names(tools) and "ask_user" in _names(tools)
    assert _names(tools)[-1] == local_llm.NO_TOOL_NAME and len(tools) <= 5


def test_chat_and_destructive_requests_are_never_forced():
    assert local_llm.forced_tools([{"role": "user", "content": "tell me a joke"}]) == []
    assert local_llm.forced_tools([{"role": "user", "content": "delete it"}]) == [], \
        "a vague destructive request is when to ask, never when to force"


def _client(monkeypatch, replies):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"choices": [{"message": replies[len(seen) - 1]}]})
    llm = local_llm.LocalLLM()
    llm._client = httpx.AsyncClient(base_url=local_llm.BASE_URL, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(local_llm, "context_tokens", lambda default=8192: 8192)

    async def get_client():
        return llm._client
    llm._get_client = get_client
    return llm, seen


def _call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


MSGS = [{"role": "system", "content": "You are Chronoa."}, {"role": "user", "content": "lock my screen"}]


def test_the_retry_is_used_when_it_calls_a_real_tool(monkeypatch):
    monkeypatch.setattr(local_llm, "FORCE_RETRY", True)
    monkeypatch.setattr(local_llm, "TEMPERATURE", 0.0)
    llm, seen = _client(monkeypatch, [{"role": "assistant", "content": "Sure, locking it."}, _call("lock_screen")])
    reply = asyncio.run(llm.chat_message(MSGS, tools=[{"type": "function", "function": {"name": "lock_screen",
                                                                                         "parameters": {}}}]))
    assert reply["tool_calls"][0]["function"]["name"] == "lock_screen"
    assert "tool_choice" not in seen[0] and seen[1]["tool_choice"] == "required"
    assert seen[0]["temperature"] == 0.0 and local_llm.NO_TOOL_NAME in _names(seen[1]["tools"])


def test_choosing_not_to_act_keeps_the_words(monkeypatch):
    monkeypatch.setattr(local_llm, "FORCE_RETRY", True)
    llm, seen = _client(monkeypatch, [{"role": "assistant", "content": "Done."},
                                      _call(local_llm.NO_TOOL_NAME, text="ok")])
    reply = asyncio.run(llm.chat_message(MSGS, tools=[{"type": "function", "function": {"name": "lock_screen",
                                                                                         "parameters": {}}}]))
    assert reply.get("content") == "Done." and not reply.get("tool_calls")
    assert "temperature" not in seen[0], "TEMPERATURE None means llama-server's own default"


def test_off_means_one_request(monkeypatch):
    monkeypatch.setattr(local_llm, "FORCE_RETRY", False)
    llm, seen = _client(monkeypatch, [{"role": "assistant", "content": "Sure."}])
    asyncio.run(llm.chat_message(MSGS, tools=[{"type": "function", "function": {"name": "lock_screen",
                                                                              "parameters": {}}}]))
    assert len(seen) == 1


# --- constrained two-step calling (adopted from Alpaca's schema-constrained titles) ---

TOOLS = [{"type": "function", "function": {"name": "set_timer", "description": "Set a timer. Long text.",
          "parameters": {"type": "object", "properties": {"seconds": {"type": "integer"}, "label": {"type": "string"}},
                         "required": ["seconds", "missing"]}}},
         {"type": "function", "function": {"name": "get_weather", "description": "The weather.",
          "parameters": {"type": "object", "properties": {"place": {"type": "string"}}}}}]


def test_constrained_picks_a_name_then_fills_its_arguments(monkeypatch):
    monkeypatch.setattr(local_llm, "CONSTRAINED", True)
    llm, seen = _client(monkeypatch, [{"role": "assistant", "content": '{"tool": "set_timer"}'},
                                      {"role": "assistant", "content": '{"seconds": 300, "label": "tea"}'}])
    reply = asyncio.run(llm.chat_message([{"role": "system", "content": "You are Chronoa."},
                                          {"role": "user", "content": "timer 5 minutes for tea"}], tools=TOOLS))
    call = reply["tool_calls"][0]["function"]
    assert call["name"] == "set_timer" and json.loads(call["arguments"]) == {"seconds": 300, "label": "tea"}
    pick, args = seen
    assert "tools" not in pick and "tools" not in args, "free-form tool calling is not used at all"
    enum = pick["response_format"]["json_schema"]["schema"]["properties"]["tool"]["enum"]
    assert enum == ["set_timer", "get_weather", "none"]
    assert "set_timer: Set a timer." in pick["messages"][0]["content"], "the menu is one sentence per tool"
    schema = args["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"seconds", "label"} and schema["required"] == ["seconds"], \
        "a required name the tool does not have is dropped, or the grammar could never be satisfied"


def test_constrained_none_answers_in_words(monkeypatch):
    monkeypatch.setattr(local_llm, "CONSTRAINED", True)
    llm, seen = _client(monkeypatch, [{"role": "assistant", "content": '{"tool": "none"}'},
                                      {"role": "assistant", "content": "Why did the scarecrow win an award?"}])
    reply = asyncio.run(llm.chat_message([{"role": "user", "content": "tell me a joke"}], tools=TOOLS))
    assert reply["content"].startswith("Why") and not reply.get("tool_calls")
    assert len(seen) == 2 and "response_format" not in seen[1] and "tools" not in seen[1]


def test_constrained_only_decides_on_a_new_request(monkeypatch):
    """After a tool result the model answers from it; the two-step choice is for what the user asked."""
    monkeypatch.setattr(local_llm, "CONSTRAINED", True)
    llm, seen = _client(monkeypatch, [{"role": "assistant", "content": "It is 21 degrees."}])
    messages = [{"role": "user", "content": "weather?"}, _call("get_weather", place="Pune"),
                {"role": "tool", "tool_call_id": "1", "content": "21 C"}]
    reply = asyncio.run(llm.chat_message(messages, tools=TOOLS))
    assert reply["content"] == "It is 21 degrees." and "response_format" not in seen[0]


# --- the person names the tool ('/timer 5 minutes', adopted from Alpaca's per-message tool choice) ---

def test_slash_names_resolve_to_one_tool_or_none():
    from shani_chronoa import user_prompts
    assert user_prompts.forced_tool("/set_timer 5 minutes") == ("set_timer", "5 minutes")
    assert user_prompts.forced_tool("/timer 5 minutes") == ("set_timer", "5 minutes")
    assert user_prompts.forced_tool("/weather Pune") == ("get_weather", "Pune")
    assert user_prompts.forced_tool("/set 5")[0] == "", "a word in many tools' names is ambiguous"
    assert user_prompts.forced_tool("/nosuchthing hi") == ("", "/nosuchthing hi")
    assert user_prompts.forced_tool("set a timer") == ("", "set a timer")


def test_a_named_tool_is_the_only_one_offered_and_only_arguments_are_asked(monkeypatch):
    from shani_chronoa.assistant import Assistant
    llm, seen = _client(monkeypatch, [{"role": "assistant", "content": '{"seconds": 300}'},
                                      {"role": "assistant", "content": "Timer set."}])
    a = Assistant(llm)
    monkeypatch.setattr("shani_chronoa.tools.execute_tool", lambda name, args, **kw: "set", raising=False)
    import shani_chronoa.assistant as amod
    monkeypatch.setattr(amod, "execute_tool", lambda name, args, *x, **kw: "Timer set for 300 s", raising=False)
    a.forced_tool = "set_timer"
    asyncio.run(a.handle("5 minutes"))
    first = seen[0]
    assert "tools" not in first and first["response_format"]["json_schema"]["schema"]["properties"].get("seconds")
    assert a.forced_tool == "" and llm.required_tool == "", "it applies to one turn only"
