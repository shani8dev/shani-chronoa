"""redaction.Redactor: live secrets kept out of prompts and out of skill children."""

import os

import pytest

from shani_chronoa.redaction import MIN_SECRET_CHARS, Redactor


@pytest.fixture
def r():
    return Redactor()


def test_nothing_registered_changes_nothing(r):
    assert r.sanitize("hello world") == "hello world"
    assert r.sanitize("") == "" and r.sanitize(None) is None


def test_registered_values_are_redacted_longest_first(r):
    r.register("openai", "sk-secret123")
    r.register("short-key", "sk-sec")
    out = r.sanitize("key sk-secret123 and sk-sec")
    assert out == "key $SECRET:OPENAI and $SECRET:SHORT_KEY"


def test_blank_and_tiny_values_are_ignored(r):
    r.register("blank", "  ")
    r.register("tiny", "ab")
    assert r.names() == ["TINY"]
    assert r.sanitize("ab cab") == "ab cab", f"values under {MIN_SECRET_CHARS} chars are not redacted"


def test_forget_and_clear(r):
    r.register("a", "value-one")
    assert r.forget("A") and not r.forget("A")
    r.register("b", "value-two")
    r.clear()
    assert r.names() == [] and r.sanitize("value-two") == "value-two"


def test_children_get_no_secret(r):
    r.register("openai", "sk-live-1234567")
    env = r.child_env({"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-live-1234567", "X": "pre sk-live-1234567"})
    assert env == {"PATH": "/usr/bin"}, "a skill subprocess must never hold a registered key"


def test_nothing_is_written_to_disk(r, tmp_path, monkeypatch):
    cfg = tmp_path / "fresh-config"
    cfg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("HOME", str(cfg))
    r.register("k", "secret-value")
    r.sanitize("secret-value")
    assert not any(cfg.rglob("*")), "registering a secret writes no file"
