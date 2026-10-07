"""A reply streams to the window through the event sink the app actually uses.

The app calls `handle(text, sink=...)` and puts its reply handler on the sink
(`ConversationMixin.event_sink`). `handle` only streamed when its own `on_text`
parameter was set, and nothing read the sink's - so the words-as-they-arrive
display and the sentence-by-sentence speech it feeds were wired to a slot nothing
called, and every reply arrived whole. Same fake model, both routes, measured.
"""

import asyncio

from shani_chronoa import events
from shani_chronoa.assistant import Assistant


class _StreamingLLM:
    stream_supported = True
    model = "fake"

    async def chat_message(self, messages, **_):
        return {"role": "assistant", "content": "whole reply"}

    async def chat_message_stream(self, messages, tools=None, on_text=None):
        for piece in ("streamed ", "reply"):
            on_text(piece)
        return {"role": "assistant", "content": "streamed reply"}


def test_the_sink_receives_the_streamed_words():
    pieces = []
    final = asyncio.run(Assistant(_StreamingLLM()).handle(
        "hi", sink=events.EventSink(on_text=pieces.append)))
    assert pieces == ["streamed ", "reply"], "the sink's on_text was never called"
    assert final == "streamed reply"


def test_an_explicit_on_text_still_wins():
    from_param, from_sink = [], []
    asyncio.run(Assistant(_StreamingLLM()).handle(
        "hi", on_text=from_param.append, sink=events.EventSink(on_text=from_sink.append)))
    assert from_param == ["streamed ", "reply"] and from_sink == []
