"""The engine picks the same model the GTK app does: Ollama, else the local server.

The headless engine asked Ollama alone, so with no GTK app it answered
"Ollama server not available" on a machine whose local llama.cpp model was
installed and running. `llm_choice.local_first` is that choice with no GTK.
"""

import pytest

from shani_chronoa import llm_choice, local_llm


class FakeOllama:
    def __init__(self, up):
        self.up = up

    def is_available(self):
        return self.up


@pytest.fixture
def local(monkeypatch):
    state = {"up": False, "installed": ["m.gguf"], "start": "", "started": 0}

    def start():
        state["started"] += 1
        if not state["start"]:
            state["up"] = True
        return state["start"]

    monkeypatch.setattr(local_llm, "is_up", lambda *a, **k: state["up"])
    monkeypatch.setattr(local_llm, "installed", lambda: state["installed"])
    monkeypatch.setattr(local_llm, "start_service", start)
    monkeypatch.setattr(local_llm, "LocalLLM", lambda: "LOCAL")
    return state


def test_ollama_answers_first(local):
    ollama = FakeOllama(True)
    assert llm_choice.local_first(ollama) == (ollama, "ollama")
    assert local["started"] == 0


def test_a_running_local_server_answers_when_ollama_does_not(local):
    local["up"] = True
    assert llm_choice.local_first(FakeOllama(False)) == ("LOCAL", "local")
    assert local["started"] == 0


def test_the_local_server_is_started_when_it_is_installed(local):
    assert llm_choice.local_first(FakeOllama(False)) == ("LOCAL", "local")
    assert local["started"] == 1


def test_nothing_installed_says_so_and_starts_nothing(local):
    local["installed"] = []
    ollama = FakeOllama(False)
    assert llm_choice.local_first(ollama) == (ollama, "no local model is installed")
    assert local["started"] == 0


def test_a_failed_start_says_why(local):
    local["start"] = "unit not found"
    ollama = FakeOllama(False)
    llm, how = llm_choice.local_first(ollama)
    assert llm is ollama and "unit not found" in how


def test_wake_false_starts_nothing(local):
    ollama = FakeOllama(False)
    assert llm_choice.local_first(ollama, wake=False)[0] is ollama
    assert local["started"] == 0


def test_the_engine_uses_it(monkeypatch, local):
    """The negative control for the engine: without the choice it stays on Ollama."""
    from shani_chronoa import engine

    local["up"] = True
    monkeypatch.setattr(engine, "OllamaLLM", lambda **k: FakeOllama(False))
    e = engine.Engine()
    assert e.llm == "LOCAL" and e.llm_how == "local"
