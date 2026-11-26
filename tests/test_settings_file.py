"""Layered settings files: one table, later wins, atomic writes, one migration."""

from __future__ import annotations

import json

import pytest

from shani_chronoa import settings_file, settings_table


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config"
    config.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("HOME", str(home))
    vendor = tmp_path / "vendor"
    admin = tmp_path / "admin"
    vendor.mkdir()
    admin.mkdir()
    monkeypatch.setenv("SHANI_CHRONOA_VENDOR_DEFAULTS", str(vendor))
    monkeypatch.setenv("SHANI_CHRONOA_ADMIN_DEFAULTS", str(admin))
    settings_file.invalidate()
    yield {"home": home, "config": config, "vendor": vendor, "admin": admin}
    settings_file.invalidate()


def _key_of_kind(kind):
    for key in settings_table.KEYS:
        if str(settings_table.kind(key)).lower() in kind:
            return key
    raise AssertionError(f"no key of kind {kind}")


def test_table_defaults_are_the_bottom_layer(isolated):
    key = _key_of_kind(("b", "bool"))
    assert settings_file.get(key) == settings_table.default_for(key)


def test_layers_override_in_order(isolated):
    key = _key_of_kind(("b", "bool"))
    opposite = not bool(settings_table.default_for(key))
    (isolated["vendor"] / "10.json").write_text(json.dumps({key: opposite}))
    settings_file.invalidate()
    assert settings_file.get(key) is opposite
    (isolated["admin"] / "10.json").write_text(json.dumps({key: not opposite}))
    settings_file.invalidate()
    assert settings_file.get(key) is not opposite
    person = settings_file.user_path()
    person.parent.mkdir(parents=True, exist_ok=True)
    person.write_text(json.dumps({key: opposite}))
    settings_file.invalidate()
    assert settings_file.get(key) is opposite


def test_only_the_engine_writes_atomically(isolated):
    key = "speech-rate"
    settings_file.set_value(key, 1.7)
    raw = json.loads(settings_file.user_path().read_text())
    assert raw[key] == 1.7
    assert not list(settings_file.user_path().parent.glob("*.tmp"))
    assert settings_file.get(key) == 1.7


def test_setting_a_value_back_to_the_layer_default_drops_the_override(isolated):
    key = _key_of_kind(("b", "bool"))
    default = bool(settings_table.default_for(key))
    settings_file.set_value(key, not default)
    assert key in json.loads(settings_file.user_path().read_text())
    settings_file.set_value(key, default)
    assert key not in json.loads(settings_file.user_path().read_text())


def test_migration_keeps_only_non_defaults_and_never_repeats(isolated, monkeypatch):
    monkeypatch.setattr(
        settings_file,
        "_gsettings_values",
        lambda: {
            "privacy-mode": True,
            "speech-rate": 1.0,  # the default: not written
            "retired-sense-enabled": True,
        },
    )
    monkeypatch.setattr(settings_file, "retired_values", lambda: {})
    assert settings_file.migrate_once() is True
    data = json.loads(settings_file.user_path().read_text())
    assert data["privacy-mode"] is True
    assert "speech-rate" not in data
    assert data[settings_file.MIGRATION_KEY] is True
    assert settings_file.get("privacy-mode") is True

    monkeypatch.setattr(
        settings_file,
        "_gsettings_values",
        lambda: pytest.fail("GSettings read again after migration"),
    )
    assert settings_file.migrate_once() is False


def test_no_schema_marks_migrated_without_writing_values(isolated, monkeypatch):
    monkeypatch.setattr(settings_file, "_gsettings_values", lambda: None)
    assert settings_file.migrate_once() is False
    data = json.loads(settings_file.user_path().read_text())
    assert data == {settings_file.MIGRATION_KEY: True}


def test_config_api_reads_the_layers(isolated, monkeypatch):
    from shani_chronoa.config import ChronoaConfig

    key = "speech-rate"
    settings_file.set_value(key, 1.4)
    config = ChronoaConfig()
    assert config.get_double(key) == 1.4
    config.set(key, "1.6")
    assert ChronoaConfig().get_double(key) == 1.6
    assert config.setting_readable(key)
    assert not config.setting_readable("no-such-key")


def test_monitor_invalidation_reaches_live_instances(isolated):
    from shani_chronoa.config import ChronoaConfig

    config = ChronoaConfig()
    assert config.get_double("speech-rate") == settings_table.default_for("speech-rate")
    person = settings_file.user_path()
    person.parent.mkdir(parents=True, exist_ok=True)
    person.write_text(json.dumps({"speech-rate": 1.9}))
    settings_file.invalidate()  # what the Gio.FileMonitor calls
    assert config.get_double("speech-rate") == 1.9


def test_retired_switches_move_to_their_successor(isolated, monkeypatch):
    monkeypatch.setattr(settings_file, "_gsettings_values", lambda: {})
    monkeypatch.setattr(
        settings_file, "retired_values", lambda: {"thermal-sense-enabled": True}
    )
    assert settings_file.migrate_once() is True
    data = json.loads(settings_file.user_path().read_text())
    assert data["hwmon-sense-enabled"] is True
