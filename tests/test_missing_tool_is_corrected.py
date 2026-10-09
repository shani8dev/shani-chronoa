"""A reply that calls a tool that does not exist is corrected, not shown.

Measured 2026-10-08, real app, voice turn "remind me tomorrow at nine": the free
model answered `{"name": "set_reminder", ...}` as text. The real skill is
`reminders`, so nothing was recovered; the JSON became the reply (spoken aloud)
and no reminder was set.
"""

import asyncio

from shani_chronoa import assistant as assistant_mod
from shani_chronoa.assistant import Assistant


class _InventsATool:
    """First answers with a made-up tool as text, then (once told) the real one."""

    def __init__(self, keeps_inventing=False):
        self.keeps_inventing, self.seen = keeps_inventing, []

    async def chat_message(self, messages, tools=None):
        self.seen.append(messages[-1].get("content") or "")
        if len(self.seen) == 1 or self.keeps_inventing:
            return {"role": "assistant",
                    "content": '{"name": "set_reminder", "parameters": {"time": "09:00", "message": "Pack"}}'}
        if len(self.seen) == 2:
            return {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {
                "name": "reminders", "arguments": {"action": "add", "text": "Pack", "due": "tomorrow 9am"}}}]}
        return {"role": "assistant", "content": "Reminder set for 9 tomorrow."}


def _turn(llm, tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(assistant_mod, "execute_tool",
                        lambda name, args, origin=None: ran.append((name, args)) or "Reminder written.")
    out = asyncio.run(Assistant(llm, session_path=tmp_path / "s.jsonl").handle("remind me tomorrow at nine"))
    return out, ran


def test_the_model_is_told_and_the_real_tool_runs(tmp_path, monkeypatch):
    llm = _InventsATool()
    out, ran = _turn(llm, tmp_path, monkeypatch)
    assert ran and ran[0][0] == "reminders", ran
    assert "set_reminder" in llm.seen[1] and "reminders(" in llm.seen[1], llm.seen[1]
    assert "{" not in out and "Reminder set" in out


def test_a_model_that_keeps_inventing_is_not_corrected_forever(tmp_path, monkeypatch):
    llm = _InventsATool(keeps_inventing=True)
    out, ran = _turn(llm, tmp_path, monkeypatch)
    assert ran == [] and len(llm.seen) == assistant_mod.MAX_CALL_CORRECTIONS + 1


def test_an_ordinary_answer_is_not_touched():
    assert assistant_mod._call_correction("It is 8 degrees in London.", [], 0) is None
    assert assistant_mod._call_correction('Quoting {"name": "x"} here.', [], 0) is None
