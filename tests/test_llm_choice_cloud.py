"""The cloud choice is one engine decision, with no GTK and no secrets in data.

`llm_choice` holds the gates, the answer mode, the provider rows and the LLM
selection. The GTK brain only applies the result to its own objects and redraws
the strip. Stand-ins here take the real interfaces and refuse a call that
should not happen - no network, no keyring, no real credentials.
"""

from types import SimpleNamespace

import pytest

from shani_chronoa import llm_choice, local_llm
from shani_chronoa.app.brain import BrainMixin


class Cfg:
    """Only the config surface the chooser reads."""

    def __init__(
        self,
        privacy=False,
        fallback=True,
        mode="local",
        keys=None,
        models=None,
        values=None,
    ):
        self.privacy_mode = privacy
        self.cloud_fallback_enabled = fallback
        self.answer_mode = mode
        self._keys = dict(keys or {})
        self._models = dict(models or {})
        self._values = dict(values or {})

    def cloud_llm_api_keys(self):
        return dict(self._keys)

    def cloud_llm_models(self):
        return dict(self._models)

    def get(self, key, default=""):
        return self._values.get(key, default)


class Provider:
    def __init__(self, pid, name, model="default", requires_key=False):
        self.id = pid
        self.name = name
        self.default_model = model
        self.requires_key = requires_key


def cloud_engine(monkeypatch, *, available=True, custom=True, raises=None):
    """A stand-in cloud module, patched where the engine reads it."""
    built = []

    class Chain:
        def __init__(self, provider_ids, api_keys, custom_base_url="", custom_model=""):
            if raises:
                raise raises
            built.append(
                SimpleNamespace(
                    provider_ids=tuple(provider_ids),
                    api_keys=dict(api_keys),
                    custom_base_url=custom_base_url,
                    custom_model=custom_model,
                )
            )

        def is_available(self):
            return available

    providers = {
        "anthropic": Provider("anthropic", "Anthropic", "claude", True),
        "openai": Provider("openai", "OpenAI", "gpt", True),
        "llm7": Provider("llm7", "LLM7", "codestral"),
        "kilo": Provider("kilo", "Kilo Gateway", "kilo-auto/free"),
        "custom": Provider("custom", "my-server", "local-model"),
    }
    engine = SimpleNamespace(
        BYOK_PROVIDER_ORDER=("anthropic", "openai"),
        DEFAULT_PROVIDER_ORDER=("llm7", "kilo"),
        CUSTOM_ID="custom",
        PROVIDERS=providers,
        custom_provider=(lambda url, model: providers["custom"] if custom else None),
        CloudLLMChain=Chain,
    )
    monkeypatch.setattr(llm_choice, "_cloud", lambda: engine)
    return built


@pytest.fixture
def registrations(monkeypatch):
    """Record key registrations without ever handing a key to the real redactor."""
    seen = []
    monkeypatch.setattr(
        llm_choice,
        "redactor",
        SimpleNamespace(register=lambda name, value: seen.append((name, value))),
    )
    return seen


@pytest.mark.parametrize(
    "privacy,fallback,expected",
    [
        (True, True, "privacy mode is on - nothing leaves this machine"),
        (
            False,
            False,
            "cloud fallback is off; turn on 'Use the free cloud gateways "
            "when no local model answers' in Settings",
        ),
        (True, False, "privacy mode is on and cloud fallback is off"),
        (False, True, ""),
    ],
)
def test_both_gates_are_read_as_one_sentence(privacy, fallback, expected):
    """Both gates must hold; a shut gate always names itself."""
    assert llm_choice.cloud_gate_reason(Cfg(privacy, fallback)) == expected


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("local", "local"),
        ("local-cloud", "auto"),
        ("cloud", "cloud"),
        ("auto", "auto"),
        ("FROM-THE-FUTURE", "local"),
    ],
)
def test_answer_mode_has_three_words(stored, expected):
    """The strip's stored wording folds into the engine's three choices."""
    assert llm_choice.answer_mode(Cfg(mode=stored)) == expected


def test_provider_rows_never_carry_a_key_value(monkeypatch):
    """Presence, not the secret: a row can be printed or sent over a bus."""
    cloud_engine(monkeypatch)
    key = "a-dummy-value-that-is-not-a-real-key"
    rows = llm_choice.providers(
        Cfg(
            keys={"anthropic": key},
            models={"openai_model": "a-chosen-model"},
            values={"custom-llm-base-url": "http://127.0.0.1:9/v1"},
        )
    )

    assert [r["id"] for r in rows] == ["anthropic", "openai", "llm7", "kilo", "custom"]
    assert [(r["kind"], r["key_set"]) for r in rows] == [
        ("byok", True),
        ("byok", False),
        ("free", False),
        ("free", False),
        ("custom", False),
    ]
    assert next(r for r in rows if r["id"] == "openai")["model"] == "a-chosen-model"
    assert key not in repr(rows)


def test_local_choice_wakes_the_local_server(monkeypatch):
    seen = []
    monkeypatch.setattr(
        llm_choice,
        "local_first",
        lambda ollama, *, wake: seen.append(wake) or ("LOCAL", "local"),
    )
    assert llm_choice.select_llm("local", Cfg()) == ("LOCAL", "local")
    assert seen == [True]


def test_auto_takes_a_local_answer_without_touching_the_gates(monkeypatch):
    monkeypatch.setattr(
        llm_choice, "local_first", lambda ollama, *, wake: ("OLLAMA", "ollama")
    )
    monkeypatch.setattr(
        llm_choice,
        "cloud_fallback",
        lambda config=None: pytest.fail("a local answer must not go to the cloud"),
    )
    assert llm_choice.select_llm("auto", Cfg(privacy=True), ollama="O") == (
        "OLLAMA",
        "ollama",
    )


def test_auto_falls_back_only_when_both_gates_are_open(monkeypatch):
    cloud = object()
    monkeypatch.setattr(
        llm_choice,
        "local_first",
        lambda ollama, *, wake: ("OLLAMA", "the local model server is not running"),
    )
    monkeypatch.setattr(llm_choice, "_cloud_chain", lambda config: (cloud, "cloud"))

    local, how = llm_choice.select_llm("auto", Cfg(privacy=True), ollama="O")
    assert local == "OLLAMA" and "privacy mode is on" in how
    assert llm_choice.select_llm("auto", Cfg(), ollama="O") == (cloud, "cloud")


def test_cloud_choice_keeps_byok_order_and_registers_keys(monkeypatch, registrations):
    built = cloud_engine(monkeypatch)
    key = "dummy-not-a-real-key"
    cfg = Cfg(
        keys={"anthropic": key, "openai": ""},
        models={"anthropic_model": "claude-chosen"},
        values={
            "custom-llm-base-url": "http://127.0.0.1:9/v1",
            "custom-llm-model": "mine",
        },
    )
    chosen, how = llm_choice.select_llm("cloud", cfg)

    assert how == "cloud" and chosen is not None
    assert built and built[0].provider_ids == (
        "custom",
        "anthropic",
        "openai",
        "llm7",
        "kilo",
    )
    assert built[0].api_keys["anthropic_model"] == "claude-chosen"
    assert built[0].custom_model == "mine"
    assert registrations == [("cloud_llm_anthropic", key), ("cloud_llm_openai", "")]


def test_an_unreachable_cloud_is_a_sentence_not_an_exception(
    monkeypatch, registrations
):
    cloud_engine(monkeypatch, available=False)
    chosen, how = llm_choice.select_llm("cloud", Cfg())
    assert chosen is None and "no cloud provider is reachable" in how

    cloud_engine(monkeypatch, raises=RuntimeError("constructor broke"))
    chosen, how = llm_choice.select_llm("cloud", Cfg())
    assert chosen is None and "RuntimeError: constructor broke" in how


def test_a_closed_gate_stops_before_any_cloud_object_is_built(monkeypatch):
    monkeypatch.setattr(
        llm_choice,
        "_cloud",
        lambda: pytest.fail("a shut gate must not reach the cloud engine"),
    )
    chosen, how = llm_choice.select_llm("cloud", Cfg(privacy=True))
    assert chosen is None and "privacy mode" in how
    assert llm_choice.cloud_fallback(Cfg(privacy=True)) == (None, how)


def test_startup_fallback_uses_the_same_gate_and_chain(monkeypatch):
    cloud_engine(monkeypatch)
    chosen, how = llm_choice.cloud_fallback(Cfg())
    assert how == "cloud" and chosen is not None
    assert llm_choice.cloud_fallback(Cfg(fallback=False))[1].startswith(
        "cloud fallback is off"
    )


def test_an_invalid_choice_names_the_valid_ones():
    chosen, how = llm_choice.select_llm("sideways", Cfg())
    assert chosen is None and "local" in how and "cloud" in how


# --- the GTK client: it applies the engine's answer, and does not re-decide ---


class Privacy:
    is_local_only = False


class Assistant:
    llm = None


def brain():
    app = BrainMixin.__new__(BrainMixin)
    app.privacy = Privacy()
    app.config = Cfg()
    app.assistant = Assistant()
    app.llm = "LOCAL"
    app._ollama_available = False
    app.window = None
    return app


def test_brain_gate_reads_the_shared_answer(monkeypatch):
    app = brain()
    seen = []
    monkeypatch.setattr(
        llm_choice, "cloud_gate_reason", lambda config: seen.append(config) or "SHUT"
    )
    assert app._cloud_gate_reason() == "SHUT"
    assert (
        seen
        and seen[0].privacy_mode is False
        and seen[0].cloud_fallback_enabled is True
    )


def test_brain_select_llm_applies_the_result_and_returns_the_sentence(monkeypatch):
    app = brain()
    calls = []
    chosen = object()
    monkeypatch.setattr(
        llm_choice,
        "select_llm",
        lambda what, config=None, ollama=None: (
            calls.append((what, config)) or (chosen, "cloud")
        ),
    )
    assert app.select_llm("cloud") == ""
    assert app.llm is chosen and app.assistant.llm is chosen
    assert calls[0][0] == "cloud"

    monkeypatch.setattr(llm_choice, "select_llm", lambda *a, **k: (None, "gate shut"))
    assert app.select_llm("local") == "gate shut"
    assert app.llm is chosen and app.assistant.llm is chosen


def test_brain_startup_fallback_delegates_the_choice(monkeypatch):
    app = brain()
    chosen = object()
    monkeypatch.setattr(
        llm_choice,
        "providers",
        lambda config=None: [{"name": "LLM7", "kind": "free", "key_set": False}],
    )
    monkeypatch.setattr(
        llm_choice, "cloud_fallback", lambda config=None: (chosen, "cloud")
    )
    app._maybe_enable_cloud_fallback()
    assert app.llm is chosen and app.assistant.llm is chosen


def test_brain_startup_fallback_stops_at_a_shut_gate(monkeypatch):
    app = brain()
    monkeypatch.setattr(
        llm_choice, "cloud_gate_reason", lambda config=None: "privacy mode is on"
    )
    monkeypatch.setattr(
        llm_choice,
        "cloud_fallback",
        lambda config=None: pytest.fail("the gate was shut"),
    )
    monkeypatch.setattr(
        llm_choice, "providers", lambda config=None: pytest.fail("no log rows needed")
    )
    monkeypatch.setattr(
        local_llm, "server_binary", lambda: "/usr/bin/llama-server", raising=False
    )
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b", raising=False)
    app._maybe_enable_cloud_fallback()
    assert app.llm == "LOCAL" and app.assistant.llm is None


def test_brain_local_server_pick_delegates(monkeypatch):
    app = brain()
    monkeypatch.setattr(local_llm, "is_up", lambda *a, **k: True)
    calls = []
    monkeypatch.setattr(
        llm_choice,
        "select_llm",
        lambda what, config=None, ollama=None: (
            calls.append(what) or ("PICKED", "local")
        ),
    )
    assert app._use_local_server() is True
    assert calls == ["local"]
    assert app.llm == "PICKED" and app._ollama_available is True
