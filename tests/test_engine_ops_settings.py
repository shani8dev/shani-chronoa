"""Settings rows, gets, writes, presets and the email group as plain data."""

import json

import pytest


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("SHANI_CHRONOA_KEYRING", "0")
    from shani_chronoa.config import ChronoaConfig

    return ChronoaConfig()


def test_rows_cover_the_schema_and_never_return_a_secret(cfg):
    from shani_chronoa.engine_ops import settings

    rows = settings.rows(cfg)
    assert {r["name"] for r in rows} == set(cfg._valid_keys)
    assert all(
        set(r) == {"name", "label", "value", "kind", "secret", "consent_key"}
        for r in rows
    )

    secret = next(r for r in rows if r["name"] == "llm7-api-key")
    assert secret["secret"] is True and secret["value"] == ""
    cfg.set("llm7-api-key", "sk-private-value")
    after = settings.rows(cfg)
    assert next(r for r in after if r["name"] == "llm7-api-key")["value"] == ""
    assert "sk-private-value" not in repr(after)


def test_get_accepts_a_key_or_a_readable_label(cfg):
    from shani_chronoa.engine_ops import settings

    row = next(r for r in settings.rows(cfg) if r["name"] == "privacy-mode")
    assert settings.get("privacy-mode", config=cfg) == row
    assert settings.get(row["label"], config=cfg) == row
    assert "error" in settings.get("no-such-setting", config=cfg)


def test_set_requires_confirm_only_to_turn_a_consent_key_on(cfg):
    from shani_chronoa.engine_ops import settings

    # The destructive switches ship ON, but each action still asks at run
    # time; a refused settings write must leave that default untouched.
    before = cfg.get_bool("file-delete-enabled")
    refused = settings.set("file-delete-enabled", "true", config=cfg)
    assert "confirmation required" in refused["error"]
    assert cfg.get_bool("file-delete-enabled") is before

    written = settings.set("file-delete-enabled", "false", confirm=True, config=cfg)
    assert written["name"] == "file-delete-enabled"
    assert written["value"] is False and written["changed"] is True
    assert cfg.get_bool("file-delete-enabled") is False

    # Turning a dangerous switch OFF is the safe direction: no confirm needed.
    settings.set("file-delete-enabled", "true", confirm=True, config=cfg)
    off = settings.set("file-delete-enabled", "false", config=cfg)
    assert off["value"] is False and off["changed"] is True


def test_set_validates_before_writing(cfg):
    from shani_chronoa.engine_ops import settings

    assert "error" in settings.set("speak-replies", "maybe", config=cfg)
    assert "error" in settings.set("speech-rate", "fast", config=cfg)
    assert settings.set("speak-replies", "false", config=cfg)["value"] is False
    changed = settings.set("speech-rate", "1.5", config=cfg)
    assert changed["value"] == 1.5 and changed["changed"] is True
    assert "error" in settings.set("no-such-setting", "x", config=cfg)


def test_secret_set_needs_confirm_and_never_echoes_the_value(cfg):
    from shani_chronoa.engine_ops import settings

    assert (
        "confirmation required"
        in settings.set("llm7-api-key", "sk-secret", config=cfg)["error"]
    )
    written = settings.set("llm7-api-key", "sk-secret", confirm=True, config=cfg)
    assert written["value"] == ""
    assert "sk-secret" not in repr(written)
    assert cfg.get("llm7-api-key") == "sk-secret"


def test_preset_requires_confirm_and_applies_the_real_rule(cfg):
    from shani_chronoa.capabilities import current_preset
    from shani_chronoa.engine_ops import settings

    assert "confirmation required" in settings.preset("chat", config=cfg)["error"]
    assert "error" in settings.preset("bogus", confirm=True, config=cfg)
    chat = settings.preset("chat", confirm=True, config=cfg)
    assert chat == {"preset": "chat", "current": "chat"}
    assert current_preset(cfg) == "chat"
    full = settings.preset("full", confirm=True, config=cfg)
    assert full["current"] == "full"


def test_email_validates_keys_before_writing_and_delegates_the_save(cfg, tmp_path):
    from shani_chronoa.engine_ops import settings
    from shani_chronoa import email_setup

    path = email_setup.account_path()
    assert (
        "unknown email field"
        in settings.email(
            {"imap_host": "imap.example", "bogus": "x"}, confirm=True, config=cfg
        )["error"]
    )
    assert (
        "keyring"
        in settings.email({"password": "hunter2"}, confirm=True, config=cfg)["error"]
    )
    assert (
        "confirmation required"
        in settings.email(
            {"imap_host": "imap.example", "smtp_host": "smtp.example"}, config=cfg
        )["error"]
    )
    assert not path.exists()

    result = settings.email(
        {
            "imap_host": "imap.example",
            "smtp_host": "smtp.example",
            "user": "me@example.com",
        },
        confirm=True,
        config=cfg,
    )
    assert result["status"] == email_setup.CONFIGURED
    assert result["path"] == str(path)
    assert path.exists() and path.stat().st_mode & 0o777 == 0o600
    assert "password" not in json.loads(path.read_text())
