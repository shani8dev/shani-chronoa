"""API keys: keyring first, GSettings as the fallback, and a migration that can never lose a key.

The keyring is faked in memory - the tests must not write to a real user's keyring.
Measured on the images (2026-10-02): with gnome-keyring unlocked the GNOME image
migrated, read back and cleared the plain copy; on the Plasma image with no
usable keyring nothing moved and the plain copy stayed.
"""

import pytest

from shani_chronoa import secret_store as ss
from shani_chronoa.config import API_KEY_SETTINGS, ChronoaConfig


class FakeConfig:
    def __init__(self, **d):
        self.d = dict(d)

    def get(self, k, default=""):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


@pytest.fixture
def keyring(monkeypatch):
    store = {}
    monkeypatch.setattr(ss, "get", lambda p: store.get(p, ""))
    monkeypatch.setattr(ss, "put", lambda p, v: (store.__setitem__(p, v) if v else store.pop(p, None)) or True)
    return store


def test_migration_moves_and_clears_only_after_read_back(keyring):
    cfg = FakeConfig(**{"groq-api-key": "gsk-1", "openai-api-key": ""})
    assert ss.migrate(cfg, API_KEY_SETTINGS) == ["groq"]
    assert keyring["groq"] == "gsk-1" and cfg.d["groq-api-key"] == ""


def test_a_failed_store_keeps_the_plain_copy(monkeypatch):
    monkeypatch.setattr(ss, "put", lambda p, v: False)
    cfg = FakeConfig(**{"groq-api-key": "gsk-1"})
    assert ss.migrate(cfg, API_KEY_SETTINGS) == []
    assert cfg.d["groq-api-key"] == "gsk-1", "never clear a key the keyring did not take"


def test_keys_are_read_from_the_keyring_first(keyring, monkeypatch):
    keyring["anthropic"] = "sk-ant-ring"
    monkeypatch.setattr(ChronoaConfig, "get", lambda self, k, d="": {"anthropic-api-key": "sk-plain",
                                                                     "groq-api-key": "gsk-plain"}.get(k, d))
    keys = ChronoaConfig.cloud_llm_api_keys(ChronoaConfig.__new__(ChronoaConfig))
    assert keys["anthropic"] == "sk-ant-ring" and keys["groq"] == "gsk-plain"


def test_settings_writes_to_the_keyring_when_there_is_one(keyring, monkeypatch):
    written = {}
    cfg = ChronoaConfig.__new__(ChronoaConfig)
    monkeypatch.setattr(ChronoaConfig, "set", lambda self, k, v: written.__setitem__(k, v))
    ChronoaConfig.set_api_key(cfg, "openrouter-api-key", "or-1")
    assert keyring["openrouter"] == "or-1" and written == {"openrouter-api-key": ""}
    monkeypatch.setattr(ss, "put", lambda p, v: False)
    ChronoaConfig.set_api_key(cfg, "openrouter-api-key", "or-2")
    assert written["openrouter-api-key"] == "or-2", "no keyring: GSettings, exactly as before"


def test_the_keyring_opt_out_is_honoured_by_the_store_itself(monkeypatch):
    """With SHANI_CHRONOA_KEYRING=0 the store must not reach Secret Service at
    all - neither read nor write. `config` honoured it and the store did not,
    so the suite could read and write the real session keyring."""
    monkeypatch.setenv("SHANI_CHRONOA_KEYRING", "0")
    assert ss.get("anthropic") is None, "read the real keyring with the opt-out set"
    assert ss.put("anthropic", "sk-must-not-land") is False
