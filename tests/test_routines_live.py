"""A routine run end to end with the REAL local model and real tools.

`test_routines.py` checks the wiring with a stand-in model; this one is the
evidence: the saved phrase goes to the actual Chronoa assistant backed by the
local llama-server, and the model must call the routine's tools, which really
run (the weather is fetched, the time is read). Skipped when no local model
server answers (set CHRONOA_TEST_LLM_PORT; 8790, then Chronoa's own 8765).
"""

import asyncio
import os

import httpx
import pytest


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


PORT = _port()
pytestmark = pytest.mark.skipif(PORT is None, reason="no local llama-server answering")


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
