"""Vector retrieval for memory facts, and the keyword path it must not disturb.

Facts are embedded once, at the write, into their own durable JSON line - not
into a second index that `forget()` and capacity eviction would have to keep in
step. At recall time cosine similarity is fused with the shipped IDF-weighted
keyword rank. When the local embedding model is absent, `embed` answers None
and recall is byte-for-byte the keyword path: no download, no cloud call, no
new dependency, and no change to what a fact looks like on disk.

The fake embed below maps a substring of the (prefixed) text to a unit vector,
so `local_embed.similarity` - the real dot product - gives exact numbers. It
raises on any text the test did not account for, so a change in what gets
embedded cannot pass by being ignored.
"""

import dataclasses
import json

import pytest

from shani_chronoa import local_embed
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import memory
from shani_chronoa.senses.memory import (
    forget_facts,
    recall,
    remember_fact,
)
from shani_chronoa.senses.store import PerceptStore

DRINK_QUERY = "what beverage do I drink"
FLAT_WHITE = "The user's order: a flat white"
NEOVIM = "my editor is neovim"
WATER = "my drink is tepid water"

# Ordered longest/most specific first: a fact may contain more than one needle.
DRINK_PAIRS = [
    (DRINK_QUERY.lower(), [1.0, 0.0]),
    ("flat white", [1.0, 0.0]),
    ("neovim", [0.0, 1.0]),
    ("water", [0.0, 1.0]),
    ("alpha", [0.0, 1.0]),
]


@pytest.fixture(autouse=True)
def memory_allowed(monkeypatch):
    """Grant the memory sense without touching the user's GSettings."""
    monkeypatch.setattr(ChronoaConfig, "sense_allowed_reason", lambda self, sense: "")
    monkeypatch.setattr(memory, "_warned_empty_vault", True)


def _fake_embed(pairs, calls=None):
    def embed(texts, kind="", timeout=30.0, transport=None):
        if calls is not None:
            calls.append((tuple(texts), kind))
        out = []
        for text in texts:
            low = text.lower()
            vector = next((v for needle, v in pairs if needle in low), None)
            if vector is None:
                raise AssertionError("unexpected embedding input: %r" % text)
            out.append(list(vector))
        return out

    return embed


def _store(tmp_path, name="memory.jsonl", capacity=None):
    return PerceptStore(
        durable_path=tmp_path / name,
        live_path=tmp_path / (name + ".live"),
        durable_capacity=capacity,
    )


def test_a_fact_with_no_shared_word_is_recalled_by_meaning(tmp_path, monkeypatch):
    """"what beverage do I drink" now finds "a flat white" through the vector."""
    monkeypatch.setattr(local_embed, "embed", _fake_embed(DRINK_PAIRS))
    store = _store(tmp_path)
    flat = remember_fact(FLAT_WHITE, store=store)
    remember_fact(NEOVIM, store=store)

    assert flat.metadata["vector"] == [1.0, 0.0]
    assert [p.content for p in recall(DRINK_QUERY, store=store)] == [FLAT_WHITE]


def test_without_the_model_recall_is_exactly_the_keyword_path(tmp_path, monkeypatch):
    """No model means no vector on disk and no change in the ranking."""
    monkeypatch.setattr(local_embed, "embed", lambda *args, **kwargs: None)
    store = _store(tmp_path)
    remember_fact(FLAT_WHITE, store=store)
    remember_fact(NEOVIM, store=store)

    assert all("vector" not in p.metadata for p in store.durable())
    assert '"vector"' not in store.durable_path.read_text(encoding="utf-8")
    assert recall(DRINK_QUERY, store=store) == []
    assert [p.content for p in recall("editor", store=store)] == [NEOVIM]

    # The same facts with no vector field at all must answer identically.
    plain = _store(tmp_path, "plain.jsonl")
    for percept in store.durable():
        plain.add(dataclasses.replace(percept))
    for query in (DRINK_QUERY, "editor", "neovim", "flat"):
        assert [p.content for p in recall(query, store=store)] == [
            p.content for p in recall(query, store=plain)
        ]


def test_meaning_changes_the_rank_against_a_keyword_hit(tmp_path, monkeypatch):
    """A semantic-only hit outranks a fact the query really shares a word with."""
    monkeypatch.setattr(local_embed, "embed", _fake_embed(DRINK_PAIRS))
    store = _store(tmp_path)
    remember_fact(WATER, store=store)  # "drink" matches, vector is unrelated
    remember_fact(FLAT_WHITE, store=store)  # no word matches, vector is identical

    found = [p.content for p in recall(DRINK_QUERY, store=store)]
    assert found == [FLAT_WHITE, WATER]


def test_each_fact_is_embedded_once_at_write(tmp_path, monkeypatch):
    """One document embedding per fact, stored in the percept and its JSON line."""
    calls = []
    monkeypatch.setattr(local_embed, "embed", _fake_embed(DRINK_PAIRS, calls))
    store = _store(tmp_path)

    percept = remember_fact(FLAT_WHITE, store=store)

    assert len(calls) == 1
    assert calls[0][1] == local_embed.DOCUMENT
    assert percept.metadata["vector"] == [1.0, 0.0]
    row = json.loads(store.durable_path.read_text(encoding="utf-8").strip())
    assert row["metadata"]["vector"] == [1.0, 0.0]


def test_forget_removes_the_vector_with_the_fact(tmp_path, monkeypatch):
    """There is no second index left holding a forgotten fact's meaning."""
    monkeypatch.setattr(local_embed, "embed", _fake_embed(DRINK_PAIRS))
    store = _store(tmp_path)
    remember_fact(FLAT_WHITE, store=store)
    remember_fact(NEOVIM, store=store)

    assert forget_facts("flat white", store=store) == 1
    assert [p.content for p in store.durable()] == [NEOVIM]
    on_disk = store.durable_path.read_text(encoding="utf-8")
    assert "flat white" not in on_disk
    assert "[1.0, 0.0]" not in on_disk
    assert "[0.0, 1.0]" in on_disk
    carriers = [
        path.name
        for path in tmp_path.iterdir()
        if path.is_file() and '"vector"' in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert carriers == ["memory.jsonl"]
    assert recall(DRINK_QUERY, store=store) == []


def test_eviction_rewrites_vectors_with_the_surviving_facts(tmp_path, monkeypatch):
    """Capacity eviction rewrites the one file; every survivor keeps its vector."""
    monkeypatch.setattr(local_embed, "embed", _fake_embed(DRINK_PAIRS))
    store = _store(tmp_path, capacity=2)
    remember_fact("an unrelated alpha note", store=store)
    remember_fact(FLAT_WHITE, store=store)
    remember_fact(NEOVIM, store=store)

    rows = [
        json.loads(line)
        for line in store.durable_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert {row["content"]: row["metadata"]["vector"] for row in rows} == {
        FLAT_WHITE: [1.0, 0.0],
        NEOVIM: [0.0, 1.0],
    }
    assert [p.content for p in recall(DRINK_QUERY, store=store)] == [FLAT_WHITE]


def test_stored_vectors_are_ignored_when_the_model_goes_away(tmp_path, monkeypatch):
    """A machine that loses the model falls back even for facts with vectors."""
    monkeypatch.setattr(local_embed, "embed", _fake_embed(DRINK_PAIRS))
    store = _store(tmp_path)
    remember_fact(FLAT_WHITE, store=store)
    remember_fact(NEOVIM, store=store)
    assert [p.content for p in recall(DRINK_QUERY, store=store)] == [FLAT_WHITE]

    monkeypatch.setattr(local_embed, "embed", lambda *args, **kwargs: None)
    assert recall(DRINK_QUERY, store=store) == []
    assert [p.content for p in recall("editor", store=store)] == [NEOVIM]
