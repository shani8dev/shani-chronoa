"""Memory links ("Priya works_at Acme"), `about`, and the change history.

Harvested from the MCP reference memory server (entities + typed relations,
`open_nodes` returning a one-hop neighbourhood) and mem0's history table
(old -> new per change). Everything goes through the real store and the real
write path; the one promise worth a test of its own is that `forget` erases
the forgotten text from the history as well, not only from the store.
"""

import json
import os

import pytest

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import memory
from shani_chronoa.senses.store import PerceptStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = PerceptStore(durable_path=tmp_path / "mem" / "memory.jsonl")
    monkeypatch.setattr(memory, "_STORE", s)
    ChronoaConfig().set("memory-sense-enabled", "true")
    return s


def _history_file(store):
    return store.durable_path.with_name(memory.HISTORY_FILE)


def test_a_link_is_a_fact_recall_and_about_both_find(store):
    out = memory._run({"operation": "link", "subject": "Priya", "relation": "works_at", "object": "Acme"})
    assert out.ttl_seconds is None and out.content == "Priya works at Acme"
    assert out.metadata["relation"] == ["Priya", "works_at", "Acme"]
    memory._run({"operation": "link", "subject": "Acme", "relation": "uses", "object": "Postgres"})
    memory._run({"operation": "remember", "fact": "Priya's birthday is in May"})
    memory._run({"operation": "remember", "fact": "Priyanka likes tea"})

    said = memory._run({"operation": "about", "query": "priya"}).content
    assert "Priya works at Acme" in said and "Priya's birthday is in May" in said
    assert "Priyanka" not in said, "a different name that only starts the same is not the same thing"
    assert "Linked to: Acme" in said
    # one hop the other way
    acme = memory._run({"operation": "about", "query": "Acme"}).content
    assert "Priya works at Acme" in acme and "Acme uses Postgres" in acme
    assert "Priya" in acme and "Postgres" in acme
    # and plain recall still sees a link
    assert "Priya works at Acme" in memory._run({"operation": "recall", "query": "works acme"}).content


def test_replace_moves_a_relation_instead_of_keeping_both(store):
    memory._run({"operation": "link", "subject": "Priya", "relation": "works_at", "object": "Acme"})
    memory._run({"operation": "link", "subject": "Priya", "relation": "knows", "object": "Sam"})
    memory._run({"operation": "link", "subject": "Priya", "relation": "works_at", "object": "Globex",
                 "replace": True})
    contents = [p.content for p in store.durable()]
    assert "Priya works at Globex" in contents and "Priya works at Acme" not in contents
    assert "Priya knows Sam" in contents, "replace touches only the one relation"
    history = memory._run({"operation": "history", "query": "priya"}).content
    assert "'Priya works at Acme' -> 'Priya works at Globex'" in history


def test_bad_links_are_refused_with_a_reason(store):
    assert "relation must be" in memory._run(
        {"operation": "link", "subject": "a", "relation": "Rm -rf; x", "object": "b"}).content
    assert "itself" in memory._run(
        {"operation": "link", "subject": "Sam", "relation": "knows", "object": " sam "}).content
    assert "subject and an object" in memory._run({"operation": "link", "subject": "Sam", "relation": "knows"}).content
    assert store.durable() == []


def test_history_records_adds_and_changes(store):
    memory.remember_from_turn("my editor is vim")
    memory.remember_from_turn("my editor is neovim")
    events = memory.memory_history("editor")
    assert [e["event"] for e in events] == ["UPDATE", "ADD"]
    assert events[0]["old"] == "The user's editor: vim" and events[0]["new"] == "The user's editor: neovim"
    assert oct(os.stat(_history_file(store)).st_mode)[-3:] == "600"


def test_forget_erases_the_text_from_the_history_too(store):
    memory._run({"operation": "remember", "fact": "my locker code is 4417"})
    memory.remember_from_turn("my editor is vim")
    memory.remember_from_turn("my editor is neovim")
    assert "4417" in _history_file(store).read_text()
    assert memory.forget_facts("locker code") == 1
    raw = _history_file(store).read_text()
    assert "4417" not in raw and "locker" not in raw, "the forgotten words survived in the history"
    assert "neovim" in raw, "an unrelated fact's history is kept"
    last = json.loads(raw.splitlines()[-1])
    assert last["event"] == "DELETE" and last["old"] == last["new"] == ""
    # forgetting the editor removes both the vim and neovim lines (same key)
    memory.forget_facts("editor")
    raw = _history_file(store).read_text()
    assert "vim" not in raw


def test_links_respect_the_memory_consent(store):
    ChronoaConfig().set("memory-sense-enabled", "false")
    out = memory._run({"operation": "link", "subject": "Priya", "relation": "works_at", "object": "Acme"})
    assert "Did not link" in out.content and store.durable() == []
    assert not _history_file(store).exists()


def test_a_link_with_a_secret_is_redacted_before_it_reaches_disk(store, monkeypatch):
    from shani_chronoa.redaction import redactor
    monkeypatch.setattr(redactor, "sanitize", lambda text: text.replace("sk-live-AbC123", "[REDACTED]"))
    monkeypatch.setattr(redactor, "names", lambda: ["k"])
    memory._run({"operation": "link", "subject": "Server", "relation": "has_key", "object": "sk-live-AbC123"})
    disk = store.durable_path.read_text() + _history_file(store).read_text()
    assert "sk-live-AbC123" not in disk and "[REDACTED]" in disk
