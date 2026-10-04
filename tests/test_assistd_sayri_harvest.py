"""Improvements harvested from assistd and sayri: small-model robustness, the voice loop, stopping a turn.

Each test names the behaviour, not the source; the sources are in the
docstrings of the code under test. The live check of the local-model pieces
(a real llama-server answering a tool call with these hooks in place) is
shani-testbed's slot-tests/chronoa-setup.sh.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from shani_chronoa import local_llm, markdown_lite, stt, vad, wakeword
from shani_chronoa.config import ChronoaConfig


# --- the local model: one stable system message, context with the turn -----

def test_normalize_keeps_the_prefix_stable_and_one_system_message():
    history = [{"role": "system", "content": "SYS"},
               {"role": "system", "content": "Live percepts: battery 40%"},
               {"role": "system", "content": "The user's standing rules for you (...):\nBe brief."},
               {"role": "user", "content": "first"}, {"role": "assistant", "content": "ok"},
               {"role": "user", "content": "second"}]
    out = local_llm.normalize_messages(history)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert out[0]["content"] == "SYS\n\nThe user's standing rules for you (...):\nBe brief."
    assert out[1]["content"] == "first", "earlier turns are unchanged, so llama.cpp's cache still matches"
    assert out[3]["content"].startswith("Live percepts: battery 40%") and out[3]["content"].endswith("second")
    assert history[5]["content"] == "second", "the caller's history is not modified"


def test_tool_calls_written_as_text_are_recovered_only_for_offered_tools():
    tools = [{"type": "function", "function": {"name": "get_datetime"}}]
    msg = {"role": "assistant", "content": '<think>hmm</think>\n<tool_call>{"name": "get_datetime", "arguments": {}}</tool_call>'}
    out = local_llm.recover_tool_calls(msg, tools)
    assert out["content"] == "" and out["tool_calls"][0]["function"]["name"] == "get_datetime"
    fenced = {"content": '```json\n{"name": "get_datetime", "arguments": {"tz": "UTC"}}\n```'}
    assert json.loads(local_llm.recover_tool_calls(fenced, tools)["tool_calls"][0]["function"]["arguments"]) == {"tz": "UTC"}
    unknown = {"content": '<tool_call>{"name": "rm_rf", "arguments": {}}</tool_call>'}
    assert "tool_calls" not in local_llm.recover_tool_calls(unknown, tools), "never a tool that was not offered"
    assert local_llm.recover_tool_calls({"content": "<think>x</think>Hello"}, None)["content"] == "Hello"


def test_the_local_client_turns_thinking_off_and_uses_the_hooks():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "<think>a</think>Hi"}}]})

    llm = local_llm.LocalLLM("qwen3-0.6b")
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, **kw):
            super().__init__(transport=httpx.MockTransport(handler), **kw)
    httpx.AsyncClient = Patched
    try:
        reply = asyncio.run(llm.chat_message([{"role": "system", "content": "S"},
                                              {"role": "system", "content": "percept"},
                                              {"role": "user", "content": "hi"}]))
    finally:
        httpx.AsyncClient = real
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]
    assert reply["content"] == "Hi" and llm.read_timeout >= 300


def test_malformed_tool_arguments_are_refused_not_run_with_defaults(monkeypatch):
    from shani_chronoa import assistant as A
    ran = []
    monkeypatch.setattr(A, "execute_tool", lambda name, args: ran.append((name, args)) or "done")
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "get_datetime", "arguments": "{not json"}}]},
        {"role": "assistant", "content": "fixed it"}])

    class LLM:
        async def chat_message(self, messages, tools=None):
            return next(replies)
    a = A.Assistant(LLM())
    assert asyncio.run(a.handle("what time is it")) == "fixed it"
    assert ran == [], "the tool must not run on arguments the model did not give"
    tool_msgs = [m for m in a._history if m.get("role") == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "c1" and "not valid JSON" in tool_msgs[0]["content"]


# --- hearing: noise is not a turn, the wake phrase carries its request ------

@pytest.mark.parametrize("noise", ["Thank you.", " [BLANK_AUDIO] ", "(music)", "Thanks for watching!",
                                   "you", "*cough*", "...", "Subtitles by the Amara.org community"])
def test_whisper_silence_phrases_are_not_a_turn(noise):
    assert stt.clean_transcription(noise) == ""


def test_real_requests_survive_cleaning():
    assert stt.clean_transcription("Thank you, set a timer for ten minutes [noise]") == \
        "Thank you, set a timer for ten minutes"
    assert stt.clean_transcription("what's the weather") == "what's the weather"


def test_wake_phrase_remainder():
    assert wakeword.phrase_remainder("Hey Chronoa, what's the time?") == "what's the time?"
    assert wakeword.phrase_remainder("Okay Chronoa.") == ""
    assert wakeword.phrase_remainder("the Chronoa lot") is None
    assert wakeword._MAX_UTTERANCE_FRAMES * 0.08 >= 7.5, "a request fits after the phrase"


def test_the_app_takes_a_one_breath_request_as_the_turn(monkeypatch):
    from shani_chronoa.app import ChronoaApplication
    did = []
    app = SimpleNamespace(_listening=False, _begin_listening=lambda: did.append("listen"),
                          _on_transcribed=lambda text: did.append(("turn", text)))
    ChronoaApplication._on_wake_word_detected_main(app, "what's the weather in Pune")
    ChronoaApplication._on_wake_word_detected_main(app, "")
    ChronoaApplication._on_wake_word_detected_main(app, "um")
    assert did == [("turn", "what's the weather in Pune"), "listen", "listen"]


def test_one_loud_frame_is_not_speech():
    loud, quiet = b"\xff\x7f" * 1280, b"\x00\x00" * 1280
    d = vad.SilenceDetector(threshold=1000)
    d.feed(loud); d.feed(quiet)
    assert not d.heard_speech, "a click is not speech"
    d.feed(loud); d.feed(loud)
    assert d.heard_speech


def test_long_code_blocks_are_not_read_aloud():
    said = markdown_lite.to_speech("Run this:\n```\nsudo pacman -Syu\n```\nOr:\n```python\na=1\nb=2\nc=3\n```")
    assert "sudo pacman -Syu" in said and "a=1" not in said and "code block on screen" in said


# --- stopping a turn, and no dead end without a model -----------------------

def test_stop_cancels_a_turn_still_thinking():
    from shani_chronoa.app import ChronoaApplication

    class Future:
        cancelled = False

        def done(self):
            return False

        def cancel(self):
            Future.cancelled = True
    states = []
    window = SimpleNamespace(set_orb_state=lambda s: None, set_state=states.append, set_status=lambda s: None,
                             get_state=lambda: None)
    app = SimpleNamespace(player=SimpleNamespace(stop=lambda: None), _turn_future=Future(), window=window,
                          _silence_reply=lambda: None)
    ChronoaApplication._stop_speaking(app, None, None)
    assert Future.cancelled and states


def test_no_model_installed_opens_setup(monkeypatch):
    from shani_chronoa.app import ChronoaApplication
    monkeypatch.setattr(local_llm, "installed", lambda: [])
    said, opened = [], []
    window = SimpleNamespace(set_orb_state=lambda s: None, set_response=said.append, set_status=lambda s: None)
    app = SimpleNamespace(window=window, _open_setup=lambda: opened.append(1))
    ChronoaApplication._explain_no_model(app, "hello")
    assert opened and "no language model yet" in said[0]


def test_speech_rate_and_pause_settings():
    from shani_chronoa.tts import PiperTTS
    c = ChronoaConfig()
    c.set("speech-rate", "1.5")
    assert c.get_double("speech-rate") == 1.5
    c.set("speech-rate", "9")
    assert c.get_double("speech-rate") == 2.0, "clamped to the schema's range"
    c.set("end-of-speech-pause", "2.0")
    assert c.get_double("end-of-speech-pause") == 2.0
    t = PiperTTS()
    assert t.rate == 1.0


def test_desktop_actions_exist():
    import configparser
    ini = configparser.ConfigParser(interpolation=None)
    ini.read("usr/share/applications/shani-chronoa.desktop")
    assert ini["Desktop Entry"]["Actions"].split(";")[:3] == ["talk", "new-conversation", "setup"]
    assert ini["Desktop Action talk"]["Exec"] == "shani-chronoa --listen"
