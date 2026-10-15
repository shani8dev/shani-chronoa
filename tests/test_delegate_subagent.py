"""`delegate` runs a subagent: fresh conversation, same gates, one paragraph back.

Driven through a real `Assistant.handle` with a scripted model. The parent asks
for `delegate`; the child (recognised by its preamble) calls a tool and
summarises. What must hold:

- the child's conversation is not the parent's, and the parent gets back only
  the summary, never what the child's tools returned;
- the child's tool calls run under the **parent's** turn label and channel, so
  turn grants, Revert and the remote-desk rule cover them;
- labels are the parent's again once the child is done;
- a child cannot delegate (depth one);
- the subagent shares the parent's clock.
"""
import asyncio
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import assistant as assistant_mod, tools  # noqa: E402
from shani_chronoa.assistant import Assistant  # noqa: E402

SECRET = "LINE-FROM-THE-FILE-THE-PARENT-MUST-NOT-SEE"


def _call(name, args, ident):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": ident, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}


def _is_child(messages):
    return any("You are a subagent" in str(m.get("content")) for m in messages
               if m.get("role") == "user")


class _ScriptedLLM:
    """Parent: delegate, then answer. Child: one tool call, then a summary."""

    def __init__(self, child_calls_delegate=False):
        self.parent_saw = []
        self.child_saw = []
        self.child_calls_delegate = child_calls_delegate

    async def chat_message(self, messages, tools=None):
        if _is_child(messages):
            self.child_saw.append([dict(m) for m in messages])
            if not any(m.get("role") == "tool" for m in messages):
                if self.child_calls_delegate:
                    return _call("delegate", {"task": "deeper"}, "c1")
                return _call("read_file", {"path": "~/proxy.conf"}, "c1")
            return {"role": "assistant", "content": "The proxy is set in proxy.conf."}
        self.parent_saw.append([dict(m) for m in messages])
        if not any(m.get("role") == "tool" for m in messages):
            return _call("delegate", {"task": "Find which file sets the proxy."}, "p1")
        return {"role": "assistant", "content": "It is proxy.conf."}


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def execute_tool(name, arguments, by_reference=False, origin=None, **_):
        calls.append((name, tools.current_turn(), tools.current_channel()))
        return SECRET

    monkeypatch.setattr(assistant_mod, "execute_tool", execute_tool)
    yield calls
    tools.clear_current_turn()
    tools.set_current_channel("")


def _turn(llm, channel=""):
    a = Assistant(llm)
    reply = asyncio.run(a.handle("which file sets my proxy?", channel=channel))
    return a, reply


def test_the_parent_gets_the_summary_and_not_the_childs_reading(ran):
    llm = _ScriptedLLM()
    a, reply = _turn(llm)
    assert reply == "It is proxy.conf."
    assert [c[0] for c in ran] == ["read_file"], ran
    delegate_result = [m for m in llm.parent_saw[-1] if m.get("role") == "tool"]
    assert len(delegate_result) == 1
    text = delegate_result[0]["content"]
    assert "The proxy is set in proxy.conf." in text and "read_file" in text, text
    assert SECRET not in json.dumps(llm.parent_saw), "the child's tool output reached the parent"
    # and the child started empty: no parent question in its conversation
    assert "which file sets my proxy?" not in json.dumps(llm.child_saw)
    assert "delegate>read_file" in a._turn_tools


def test_the_child_runs_under_the_parents_turn_and_channel(ran):
    a, _ = _turn(_ScriptedLLM(), channel="telegram")
    (name, turn, channel), = ran
    assert turn == a._turn_label and turn.startswith("turn-"), ran
    assert channel == "telegram", "a remote turn's subagent stopped being remote"
    # cleared once the whole turn is over, not left at the child's
    assert tools.current_turn() == "" and tools.current_channel() == ""


def test_the_parents_label_is_back_after_the_child(ran, monkeypatch):
    seen = []
    real = Assistant._delegate

    async def spy(self, arguments, origin, deadline):
        out = await real(self, arguments, origin, deadline)
        seen.append(tools.current_turn())
        return out

    monkeypatch.setattr(Assistant, "_delegate", spy)
    a, _ = _turn(_ScriptedLLM())
    assert seen == [a._turn_label]


def test_a_subagent_cannot_delegate(ran):
    llm = _ScriptedLLM(child_calls_delegate=True)
    _turn(llm)
    child_results = [m["content"] for m in llm.child_saw[-1] if m.get("role") == "tool"]
    assert child_results and "cannot hand work to another subagent" in child_results[0]
    assert ran == []


def test_no_time_left_means_no_subagent(ran, monkeypatch):
    monkeypatch.setattr(assistant_mod, "DELEGATE_RESERVE_SECONDS",
                        assistant_mod.MAX_TURN_SECONDS)
    llm = _ScriptedLLM()
    _turn(llm)
    results = [m["content"] for m in llm.parent_saw[-1] if m.get("role") == "tool"]
    assert "Too little of this turn's time" in results[0]
    assert llm.child_saw == []


def test_the_skill_alone_refuses():
    from shani_chronoa.skills import delegate
    assert delegate.run({"task": "x"}).startswith("Not run.")
    assert any(t["function"]["name"] == "delegate" for t in tools.TOOLS)


def test_the_card_and_clock_say_subagent():
    from shani_chronoa.gui.blocks import tool_words
    assert tool_words("delegate") == "subagent"
    assert tool_words("read_file") == "read file"
