"""What sleep learns from a conversation while nobody is watching.

The old sleep pass wrote a summary and a queue of fact candidates, and the
architecture doc was right to call the rest missing: no entities, no links, no
index refresh, and no model path even when a local one was already running.
These tests pin the four additions and the boundary that makes them safe to
run unattended: everything is a *proposal*, and only the consent-gated memory
acceptor turns a link into durable memory.
"""

from __future__ import annotations

import pytest

from shani_chronoa import (
    consolidation,
    conversation_learning,
    conversation_store,
    local_llm,
)
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import memory
from shani_chronoa.senses.store import PerceptStore

SID = "20260101-120000-abcd"

CHAT = [
    {"role": "user", "content": "My name is Sam and I live in Pune."},
    {"role": "assistant", "content": "Hello Sam."},
    {"role": "user", "content": "Ramesh works at the annex office."},
    {"role": "assistant", "content": "Noted."},
    {"role": "user", "content": "I prefer dark rooms and quiet notifications."},
    {"role": "assistant", "content": "Understood."},
]


class TestEntitiesAndLinks:
    def test_entities_come_from_what_the_person_said(self):
        names = consolidation.extract_entities(CHAT)
        assert "Sam" in names and "Pune" in names

    def test_an_assistant_name_is_not_an_entity(self):
        chat = [{"role": "assistant", "content": "My name is robot."}] * 6
        assert consolidation.extract_entities(chat) == []

    def test_links_keep_the_verbatim_source_and_the_triple(self):
        links = consolidation.extract_links(CHAT)
        assert links, "the conversation states two links"
        named = [c for c in links if c.relation and c.relation[2] == "Sam"]
        assert len(named) == 1
        assert named[0].text in CHAT[0]["content"]
        assert named[0].relation == ("user", "named", "Sam")

    def test_subject_relation_object_uses_the_existing_vocabulary(self):
        links = {
            c.relation[2]: c.relation
            for c in consolidation.extract_links(CHAT)
            if c.relation
        }
        assert "Pune" in links and links["Pune"][1] == "lives_in"

    def test_a_digest_carries_both_and_round_trips(self, tmp_path):
        digest = consolidation.consolidate(SID, CHAT)
        assert digest.entities
        assert any(c.relation for c in digest.candidates)
        consolidation.save_digest(digest, tmp_path)
        loaded = consolidation.load_digest(SID, tmp_path)
        assert loaded.entities == digest.entities
        assert [
            tuple(c.relation) if c.relation else None for c in loaded.candidates
        ] == [tuple(c.relation) if c.relation else None for c in digest.candidates]

    def test_links_are_not_facts_the_person_never_agreed_to(self):
        digest = consolidation.consolidate(SID, CHAT)
        assert all(not c.accepted and not c.rejected for c in digest.candidates)


@pytest.fixture
def granted(tmp_path, monkeypatch):
    store = PerceptStore(durable_path=tmp_path / "mem" / "memory.jsonl")
    monkeypatch.setattr(memory, "_STORE", store)
    ChronoaConfig().set("memory-sense-enabled", "true")
    return store


def test_accepting_a_link_writes_a_link_not_a_sentence(granted, tmp_path):
    digest = consolidation.consolidate(SID, CHAT)
    at = next(
        i
        for i, c in enumerate(digest.candidates)
        if c.relation and c.relation[2] == "Pune"
    )
    consolidation.save_digest(digest, tmp_path)
    outcome, reason = memory.decide_candidate(SID, at, True, granted, tmp_path)
    assert outcome == "kept", reason
    links, facts, neighbours = memory.about("user", store=granted)
    assert any("Pune" in p.content for p in links)
    assert not any("I live in Pune" == p.content for p in facts)


def test_declining_a_link_stores_nothing(granted, tmp_path):
    digest = consolidation.consolidate(SID, CHAT)
    at = next(i for i, c in enumerate(digest.candidates) if c.relation)
    consolidation.save_digest(digest, tmp_path)
    outcome, _ = memory.decide_candidate(SID, at, False, granted, tmp_path)
    assert outcome == "declined"
    assert memory.about("user", store=granted)[0] == []


class TestOptionalLocalSummary:
    def test_no_running_server_means_no_enricher(self, monkeypatch):
        monkeypatch.setattr(local_llm, "is_up", lambda: False)
        assert conversation_learning.local_enricher() is None

    def test_a_running_local_server_is_asked_once(self, monkeypatch):
        class Fake:
            local = True

            async def chat_message(self, messages, tools=None, stream=False):
                assert len(messages) == 2
                return {"role": "assistant", "content": "Sam lives in Pune."}

        monkeypatch.setattr(local_llm, "is_up", lambda: True)
        monkeypatch.setattr(local_llm, "LocalLLM", Fake)
        enrich = conversation_learning.local_enricher()
        assert enrich(CHAT, "extractive") == "Sam lives in Pune."

    def test_a_failed_summary_falls_back_to_extractive(self, monkeypatch):
        class Fake:
            local = True

            async def chat_message(self, messages, tools=None, stream=False):
                raise RuntimeError("server went away")

        monkeypatch.setattr(local_llm, "is_up", lambda: True)
        monkeypatch.setattr(local_llm, "LocalLLM", Fake)
        enrich = conversation_learning.local_enricher()
        assert enrich(CHAT, "extractive") == ""

    def test_sleep_uses_the_enricher_when_given(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        for message in CHAT:
            conversation_store.append(message, sessions / f"{SID}.jsonl")
        path = sessions / f"{SID}.jsonl"
        import os

        os.utime(path, (0, 0))
        slept = consolidation.sleep_now(
            sessions,
            minimum_age=1,
            directory=tmp_path / "sleep",
            enrich=lambda _messages, _summary: "A better local summary.",
            refresh=False,
        )
        assert slept and slept[0].summary == "A better local summary."


class TestIndexRefresh:
    def test_sleep_refreshes_the_search_index(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        sid = conversation_store.new_session(sessions)
        path = conversation_store.session_path(sessions, sid)
        for message in CHAT:
            conversation_store.append(message, path)
        import os

        os.utime(path, (0, 0))
        consolidation.sleep_now(sessions, minimum_age=1, directory=tmp_path / "sleep")
        hits = conversation_store.search(sessions, "Pune", embed=lambda *_: None)
        assert any(h["id"] == sid for h in hits)

    def test_refresh_reports_a_missing_store_without_creating_it(self, tmp_path):
        assert consolidation.refresh_index(SID, tmp_path / "absent") is False
        assert not (tmp_path / "absent").exists()

    def test_vectors_use_only_the_existing_local_embed_path(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        conversation_store.append(
            {"role": "user", "content": "I live in Pune."}, sessions / f"{SID}.jsonl"
        )
        seen = []

        def fake_embed(texts, kind):
            seen.append(kind)
            return [[0.1, 0.9, 0.0] for _ in texts]

        assert consolidation.refresh_index(SID, sessions, embed=fake_embed) is True
        from shani_chronoa import local_embed

        assert seen and seen[0] == local_embed.DOCUMENT
