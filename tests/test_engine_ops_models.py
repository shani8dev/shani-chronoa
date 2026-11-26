"""Model listing and selection, with gates and engine calls under test control."""

from types import SimpleNamespace

import pytest


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("SHANI_CHRONOA_KEYRING", "0")
    from shani_chronoa.config import ChronoaConfig

    return ChronoaConfig()


class _StubOllama:
    async def list_models(self):
        return [{"name": "qwen3:4b"}]


def _seed_local(monkeypatch):
    from shani_chronoa import local_llm

    spec = SimpleNamespace(key="small", blurb="quick", filename="small.gguf")
    monkeypatch.setattr(local_llm, "TIERS", [(spec, "Small - quick", 0)])
    monkeypatch.setattr(local_llm, "SPECS", {"small": spec})
    monkeypatch.setattr(local_llm, "installed", lambda: ["small"])
    monkeypatch.setattr(local_llm, "active", lambda: "small")
    monkeypatch.setattr(local_llm, "answering_key", lambda: "small")
    return spec


def test_list_models_returns_stable_rows_for_every_engine(cfg, monkeypatch):
    from shani_chronoa import llm_choice
    from shani_chronoa.engine_ops import models

    monkeypatch.setattr(llm_choice, "local_first", lambda o, wake=True: (o, "ollama"))
    monkeypatch.setattr(models, "_ollama", lambda config: _StubOllama())
    _seed_local(monkeypatch)

    result = models.list_models(cfg)
    assert result["how"] == "ollama"
    rows = result["items"]
    assert all(
        set(r) == {"engine", "name", "detail", "installed", "current"} for r in rows
    )
    ollama = next(
        r for r in rows if r["engine"] == "ollama" and r["name"] == "qwen3:4b"
    )
    assert ollama["installed"] is True and ollama["current"] is True
    local = next(r for r in rows if r["engine"] == "local" and r["name"] == "small")
    assert local["detail"] == "Small" and local["installed"] is True
    assert local["current"] is False
    assert any(r["engine"] == "cloud" and r["name"] == "kilo" for r in rows)


def test_list_models_marks_the_answering_local_tier_current(cfg, monkeypatch):
    from shani_chronoa import llm_choice
    from shani_chronoa.engine_ops import models

    monkeypatch.setattr(llm_choice, "local_first", lambda o, wake=True: (o, "local"))
    monkeypatch.setattr(models, "_ollama", lambda config: _StubOllama())
    _seed_local(monkeypatch)

    local = next(r for r in models.list_models(cfg)["items"] if r["engine"] == "local")
    assert local["current"] is True


def test_list_models_marks_the_selected_cloud_provider_current(cfg, monkeypatch):
    from shani_chronoa import llm_choice
    from shani_chronoa.engine_ops import models

    cfg.set("answer-mode", "cloud")
    cfg.set("kilo-api-key", "private-key")
    monkeypatch.setattr(llm_choice, "local_first", lambda o, wake=True: (o, "local"))
    monkeypatch.setattr(models, "_ollama", lambda config: _StubOllama())
    _seed_local(monkeypatch)

    rows = models.list_models(cfg)["items"]
    cloud = {r["name"]: r for r in rows if r["engine"] == "cloud"}
    assert cloud["kilo"]["current"] is True
    assert cloud["openai"]["current"] is False
    assert cloud["openai"]["installed"] is False  # no key configured
    assert "private-key" not in repr(rows)


def test_use_cloud_requires_confirm_then_a_real_gate_check(cfg, monkeypatch):
    from shani_chronoa import llm_choice
    from shani_chronoa.engine_ops import models

    monkeypatch.setattr(
        llm_choice,
        "cloud_gate_reason",
        lambda config=None: "privacy mode is on",
        raising=False,
    )
    assert "confirmation required" in models.use("cloud", config=cfg)["error"]
    assert (
        "privacy mode is on" in models.use("cloud", confirm=True, config=cfg)["error"]
    )
    assert cfg.get("answer-mode") != "cloud"

    monkeypatch.setattr(
        llm_choice, "cloud_gate_reason", lambda config=None: "", raising=False
    )
    chosen = models.use("cloud", confirm=True, config=cfg)
    assert chosen == {
        "name": "cloud",
        "engine": "cloud",
        "how": "cloud",
        "changed": True,
    }
    assert cfg.get("answer-mode") == "cloud"
    assert cfg.get_bool("cloud-fallback-enabled") is True


def test_use_cloud_provider_checks_its_key(cfg, monkeypatch):
    from shani_chronoa import llm_choice
    from shani_chronoa.engine_ops import models

    monkeypatch.setattr(
        llm_choice, "cloud_gate_reason", lambda config=None: "", raising=False
    )
    assert "needs an API key" in models.use("openai", confirm=True, config=cfg)["error"]
    assert cfg.get("answer-mode") != "cloud"


def test_use_local_delegates_to_local_first(cfg, monkeypatch):
    from shani_chronoa import llm_choice
    from shani_chronoa.engine_ops import models

    seen = {}

    def fake_local_first(ollama, *, wake=True):
        seen["wake"] = wake
        return ollama, "local"

    cfg.set("answer-mode", "cloud")
    monkeypatch.setattr(llm_choice, "local_first", fake_local_first)
    monkeypatch.setattr(models, "_ollama", lambda config: _StubOllama())

    chosen = models.use("local", config=cfg)
    assert seen["wake"] is True
    assert chosen["engine"] == "local" and chosen["how"] == "local"
    assert chosen["changed"] is True and cfg.get("answer-mode") == "local"


def test_use_installed_local_tier_and_refuse_a_failed_gate(cfg, monkeypatch):
    from shani_chronoa import local_llm
    from shani_chronoa.engine_ops import models

    _seed_local(monkeypatch)
    monkeypatch.setattr(
        local_llm, "use", lambda key: {"promote": True, "why": "quality gate passed"}
    )
    assert models.use("small", config=cfg)["engine"] == "local"
    assert cfg.get("answer-mode") == "local"

    monkeypatch.setattr(
        local_llm, "use", lambda key: {"promote": False, "why": "too slow"}
    )
    assert "too slow" in models.use("small", config=cfg)["error"]


def test_use_rejects_uninstalled_tiers_and_unknown_names(cfg, monkeypatch):
    from shani_chronoa import local_llm
    from shani_chronoa.engine_ops import models

    _seed_local(monkeypatch)
    monkeypatch.setattr(local_llm, "installed", lambda: [])
    monkeypatch.setattr(models, "_ollama", lambda config: _StubOllama())
    assert "not installed" in models.use("small", config=cfg)["error"]
    assert "error" in models.use("no-such-model", config=cfg)
    assert "error" in models.use("", config=cfg)


def test_use_ollama_model_id_writes_the_model(cfg, monkeypatch):
    from shani_chronoa import llm_choice
    from shani_chronoa.engine_ops import models

    monkeypatch.setattr(llm_choice, "local_first", lambda o, wake=True: (o, "ollama"))
    monkeypatch.setattr(models, "_ollama", lambda config: _StubOllama())

    chosen = models.use("qwen3:4b", config=cfg)
    assert chosen == {
        "name": "qwen3:4b",
        "engine": "ollama",
        "how": "ollama",
        "changed": True,
    }
    assert cfg.get("model") == "qwen3:4b"
