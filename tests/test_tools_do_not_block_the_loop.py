"""A running tool must not freeze the turn's event loop.

`Assistant` called `execute_tool` synchronously inside its coroutine, so every
tool held the loop for as long as it ran. `ask_user` waits up to 180 s for a
person, and the transcription of a *spoken* answer is a coroutine on that same
loop - so it could only run after the question had timed out. Measured
2026-10-08 in the real app: the answer was recorded at 13:56:33 and transcribed
at 13:59:23, after the model had been told "no option was chosen".
"""

import asyncio
import threading
import time

from shani_chronoa import assistant as assistant_mod
from shani_chronoa.assistant import Assistant


class _AsksOnce:
    def __init__(self):
        self.calls = 0

    async def chat_message(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "1", "function": {"name": "get_datetime", "arguments": {}}}]}
        return {"role": "assistant", "content": "done"}


def test_a_waiting_tool_can_be_answered_from_the_same_loop(tmp_path, monkeypatch):
    answered = threading.Event()
    results = []

    def waiting_tool(name, args, origin=None):
        # Like ask_user: blocks until something else delivers the answer.
        results.append("answered" if answered.wait(3.0) else "timed out")
        return results[-1]

    monkeypatch.setattr(assistant_mod, "execute_tool", waiting_tool)

    async def main():
        a = Assistant(_AsksOnce(), session_path=tmp_path / "s.jsonl")

        async def answer_soon():
            # Stands in for the spoken answer's transcription coroutine.
            await asyncio.sleep(0.2)
            answered.set()

        helper = asyncio.create_task(answer_soon())
        start = time.monotonic()
        out = await a.handle("go")
        await helper
        return out, time.monotonic() - start

    out, elapsed = asyncio.run(main())
    assert results == ["answered"], "the answer could not arrive while the tool waited"
    assert elapsed < 2.0, f"the turn took {elapsed:.1f}s"
    assert out == "done"
