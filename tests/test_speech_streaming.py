"""Sentence-by-sentence speech (speech.py) and the streamed local reply (cloud_llm.chat_message_stream)."""

import asyncio
import json
import threading
import time

import httpx

from shani_chronoa import speech


def test_sentences_split_where_a_listener_expects():
    text = ("Hello there, I am Chronoa. The weather is 23.5 degrees, e.g. warm today! Dr. Rao called at 3 p.m. "
            "today. See https://x.org/a.b now.\n\n- first item here\n- second item here")
    assert speech.split_sentences(text) == [
        "Hello there, I am Chronoa.", "The weather is 23.5 degrees, e.g. warm today!",
        "Dr. Rao called at 3 p.m. today.", "See https://x.org/a.b now.", "- first item here",
        "- second item here"]


def test_streaming_one_character_at_a_time_gives_the_same_sentences():
    text = "It is ten past nine. Your next meeting is at ten. Shall I remind you at a quarter to?"
    buf, out = speech.SentenceBuffer(), []
    for ch in text:
        out += buf.push(ch)
    first_ready_at = None
    assert out[0] == "It is ten past nine.", "the first sentence is released before the reply ends"
    assert out + buf.flush() == speech.split_sentences(text)


def test_a_code_block_is_one_unit_never_split():
    pieces = speech.split_sentences("Here:\n```\na = 1. B = 2.\nc = 3\n```\nDone now, thanks.")
    assert any(p.startswith("```") and p.rstrip().endswith("```") for p in pieces)


def test_the_queue_synthesises_ahead_and_plays_in_order():
    events, lock = [], threading.Lock()

    def synth(text):
        with lock:
            events.append(("synth", text))
        time.sleep(0.05)
        return text.encode()

    def play(wav):
        with lock:
            events.append(("play", wav.decode()))
        time.sleep(0.15)
        return True
    started, done = threading.Event(), threading.Event()
    q = speech.SpeechQueue(synth, play, on_start=started.set, on_done=done.set)
    q.speak(["One is first.", "Two is second.", "Three is third."])
    q.close()
    assert done.wait(5) and started.is_set()
    assert q.spoken == ["One is first.", "Two is second.", "Three is third."]
    # sentence two was synthesised while sentence one was playing
    assert events.index(("synth", "Two is second.")) < events.index(("play", "Two is second."))
    assert events.index(("synth", "Two is second.")) < events.index(("play", "One is first.")) + 2


def test_stop_drops_what_is_queued():
    played = []
    q = speech.SpeechQueue(lambda t: t.encode(), lambda w: (played.append(w), time.sleep(0.3)), lambda: None)
    q.speak([f"Sentence number {i} here." for i in range(6)])
    time.sleep(0.1)
    q.stop()
    assert q.wait(3) and len(played) <= 2


def test_the_stream_passes_words_on_and_assembles_tool_calls():
    from shani_chronoa import local_llm

    def sse(chunks):
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    replies = iter([
        [{"choices": [{"delta": {"content": "<think>\n\n</think>\n\n"}}]},
         {"choices": [{"delta": {"content": "It is "}}]}, {"choices": [{"delta": {"content": "nine."}}]}],
        [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "function": {"name": "get_date", "arguments": "{\"a\""}}]}}]},
         {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}}]}],
    ])
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(lambda r: sse(next(replies))), **kw)
    httpx.AsyncClient = Patched
    try:
        llm = local_llm.LocalLLM("qwen3-0.6b")
        words = []
        msg = asyncio.run(llm.chat_message_stream([{"role": "user", "content": "time?"}], on_text=words.append))
        call = asyncio.run(llm.chat_message_stream([{"role": "user", "content": "date?"}],
                                                   tools=[{"type": "function", "function": {"name": "get_date"}}],
                                                   on_text=words.append))
    finally:
        httpx.AsyncClient = real
    assert words == ["It is ", "nine."], "the empty think block is held back, the answer streams"
    assert msg["content"].endswith("It is nine.") and "tool_calls" not in msg
    assert call["tool_calls"][0]["function"] == {"name": "get_date", "arguments": "{\"a\": 1}"}
