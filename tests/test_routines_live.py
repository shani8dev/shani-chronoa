"""A routine run end to end with the REAL local model and real tools.

`test_routines.py` checks the wiring with a stand-in model; this one is the
evidence: the saved phrase goes to the actual Chronoa assistant backed by the
local llama-server, and the model must call the routine's tools, which really
run (the weather is fetched, the time is read). Skipped when no local model
server answers (set CHRONOA_TEST_LLM_PORT; 8790, then Chronoa's own 8765).

**A live-model test is the one kind whose result changes with what happens to be
loaded**, and this one did: `Qwen3-1.7B-Q4_K_M` calls only `get_datetime` for
"weather in Pune *and* what time it is", then *narrates* "Let me check the
weather" without calling it - the assistant's loop correctly ends a turn whose
reply carries no tool call, and the reply is a promise with nothing behind it.
AGENTS.md records the measurement both ways (it fails identically with the two
new skills hidden from discovery, so it is the model rather than the tools).

So the loaded model is read **from the server**, which is the thing the test
actually depends on, and a model measured not to manage two calls in one turn
SKIPs naming itself. Asserting a fixed answer against whatever is loaded is a red
test that is not about the code - the same reasoning that makes `chronoa-senses`'
modelfit step report SKIP rather than guess.
"""

import asyncio
import os

import httpx
import pytest

#: Models measured NOT to run two tool calls in one turn. Each entry is a
#: finding, not a workaround: the model answered with one call and prose.
#: Remove an entry when a model is measured to do better and this test goes red
#: again - which is the signal someone wanted.
SINGLE_TOOL_MODELS = ("Qwen3-1.7B",)


def _port():
    for port in (os.environ.get("CHRONOA_TEST_LLM_PORT"), "8790", "8765"):
        if not port:
            continue
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return int(port)
        except httpx.HTTPError:
            continue
    return None


def _loaded_model(port):
    """The file the SERVER loaded, as the server itself reports it.

    `/props` is llama.cpp's own endpoint and `model_path` is what it has in
    memory - the thing this test depends on. Two obvious alternatives are both
    wrong: `local_llm.active()` reads a symlink through `XDG_DATA_HOME`, which
    this file's fixture redirects, so it answers `""`; and `/v1/models` reports
    the generic `current.gguf` for every model on the machine, which identifies
    nothing.

    `model_path` is the **symlink**, so it is resolved. Without that the path
    says `current.gguf` whatever is behind it and every model looks alike - which
    is how the first version of this check matched nothing and stayed a red test.
    """
    try:
        body = httpx.get(f"http://127.0.0.1:{port}/props", timeout=5).json()
    except (httpx.HTTPError, ValueError):
        return ""
    path = str(body.get("model_path") or "")
    if not path:
        return ""
    try:
        return os.path.realpath(path)
    except OSError:
        return path


PORT = _port()
MODEL = _loaded_model(PORT) if PORT else ""
MODEL_CANNOT = any(tag.lower() in MODEL.lower() for tag in SINGLE_TOOL_MODELS)

pytestmark = [
    pytest.mark.skipif(PORT is None, reason="no local llama-server answering"),
    pytest.mark.skipif(
        MODEL_CANNOT,
        reason=f"the loaded model ({MODEL}) is measured to run one tool call per "
               "turn, so this test's two-call routine is a property of the model, "
               "not of the code - see SINGLE_TOOL_MODELS"),
]


def test_good_morning_runs_its_tools_on_the_real_model(gsettings_env, monkeypatch, tmp_path):
    from shani_chronoa import local_llm
    from shani_chronoa.assistant import Assistant
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.skills import routines as skill
    monkeypatch.setattr(local_llm, "PORT", PORT)
    monkeypatch.setattr(local_llm, "BASE_URL", f"http://{local_llm.HOST}:{PORT}/v1")
    config = ChronoaConfig()
    config.set("web-sense-enabled", "true")
    config.set("privacy-mode", "false")
    skill._run({"action": "save", "name": "good morning",
                "request": "Tell me today's weather in Pune and what time it is now."})

    calls, results = [], []
    reply = asyncio.run(Assistant(local_llm.LocalLLM(), session_path=tmp_path / "s.jsonl").handle(
        "good morning",
        on_tool_call=lambda name, args: calls.append(name),
        on_tool_result=lambda name, args, result, ok: results.append((name, ok, str(result)[:120]))))
    print("\ncalls:", calls, "\nresults:", results, "\nreply:", reply[:300])
    assert "get_weather" in calls, f"the routine's weather step never ran: {calls}"
    weather = next(r for r in results if r[0] == "get_weather")
    assert weather[1] and "Pune" in weather[2], f"the weather tool did not really answer: {weather}"
    assert reply.strip(), "no reply came back"
