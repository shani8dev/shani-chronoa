"""A change Chronoa makes to a setting or an armed rule can be taken back.

The file pre-image ring covered edits; it did not cover "Chronoa turned off my
privacy mode" or "Chronoa armed a rule that fires every night". These tests
pin the journal for both, and the two properties that make it safe: secrets
are never recorded, and a restore does not journal itself.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import undo_ring  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.skills import undo_last_change  # noqa: E402


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A ChronoaConfig over a tmp state dir, via the keyfile backend."""
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return ChronoaConfig()


def test_a_setting_change_is_journalled(config):
    config.set("wake-phrase", "hey")
    config.set("wake-phrase", "hello")
    entries = [e for e in undo_ring.load()
               if (e.get("kind") or "file") == "setting" and e.get("key") == "wake-phrase"]
    assert [(e["before"], e["after"]) for e in entries] == [("hey chronoa", "hey"), ("hey", "hello")]


def test_a_no_op_change_records_nothing(config):
    config.set("wake-phrase", "hey")
    config.set("wake-phrase", "hey")
    entries = [e for e in undo_ring.load() if (e.get("kind") or "file") == "setting"]
    assert len(entries) == 1


def test_an_api_key_is_never_journalled(config):
    config.set("openai-api-key", "sk-secret")
    assert undo_ring.load() == []
    assert undo_ring.is_secret_key("openai-api-key")
    assert not undo_ring.is_secret_key("wake-phrase")


def test_undo_restores_the_previous_setting(config, monkeypatch):
    config.set("wake-phrase", "hey")
    config.set("wake-phrase", "hello")
    monkeypatch.setattr(undo_last_change, "_consent", lambda c: (True, ""))
    said = undo_last_change._run({"setting": "wake-phrase"})
    assert "Restored" in said and "wake-phrase" in said
    # A fresh instance: the keyfile backend does not re-read another
    # instance's writes, so the fixture's own handle would report the old value.
    assert ChronoaConfig().get("wake-phrase") == "hey"


def test_a_restore_does_not_journal_itself(config, monkeypatch):
    config.set("wake-phrase", "hey")
    config.set("wake-phrase", "hello")
    monkeypatch.setattr(undo_last_change, "_consent", lambda c: (True, ""))
    undo_last_change._run({"setting": "wake-phrase"})
    entries = [e for e in undo_ring.load() if (e.get("kind") or "file") == "setting"]
    assert len(entries) == 1           # the 'hi' change, spent; nothing new
    assert entries[0]["after"] == "hey"


def test_undo_steps_back_through_setting_changes(config, monkeypatch):
    for value in ("hey", "hello", "namaste"):
        config.set("wake-phrase", value)
    monkeypatch.setattr(undo_last_change, "_consent", lambda c: (True, ""))
    undo_last_change._run({"setting": "wake-phrase", "steps": 2})
    assert ChronoaConfig().get("wake-phrase") == "hey"


def test_an_unknown_setting_reports_plainly(config, monkeypatch):
    monkeypatch.setattr(undo_last_change, "_consent", lambda c: (True, ""))
    said = undo_last_change._run({"setting": "wake-phrase"})
    assert "No recorded change" in said
    assert "nothing was restored" in said.lower()


def test_the_list_names_settings_and_files(config, monkeypatch):
    config.set("wake-phrase", "hello")
    undo_ring.record_file(Path("notes.txt"), b"before")
    monkeypatch.setattr(undo_last_change, "_consent", lambda c: (True, ""))
    listed = undo_last_change._run({"list": True})
    assert "setting wake-phrase" in listed
    assert "notes.txt" in listed


def test_file_and_setting_journals_trim_independently(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for i in range(undo_ring.MAX_PER_KIND + 5):
        undo_ring.record_setting("wake-phrase", f"v{i}", f"v{i + 1}")
        undo_ring.record_file(Path(f"f{i}.txt"), b"x")
    kinds = [e.get("kind") or "file" for e in undo_ring.load()]
    assert kinds.count("setting") == undo_ring.MAX_PER_KIND
    assert kinds.count("file") == undo_ring.MAX_PER_KIND


def test_a_trigger_store_write_journals_the_rules_file(tmp_path, monkeypatch):
    """Arming a rule is a change a person may want to take back."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from shani_chronoa.triggers import RuleStore, TriggerRule

    store = RuleStore(path=tmp_path / "rules.json")
    store._rules = {}
    store._write()                                   # an empty store on disk
    before = store._path.read_bytes()

    rule = TriggerRule(name="morning", sense="clock", match_mode="any",
                       actuator="notify", arguments={"text": "hi"})
    store.add(rule)

    entries = [e for e in undo_ring.load() if e.get("path") == str(store._path)]
    assert entries, "the rules file was overwritten with no pre-image"
    assert entries[-1]["content"].encode() == before


def test_the_journal_survives_a_reload():
    undo_ring.record_setting("wake-phrase", "hey", "hello")
    reread = json.loads(undo_ring.ring_path().read_text())
    assert reread[-1]["kind"] == "setting"
    assert reread[-1]["before"] == "hey"
