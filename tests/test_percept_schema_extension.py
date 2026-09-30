"""The percept schema's newer fields, and the paths that silently drop them.

`Percept` is a frozen dataclass that four separate places rebuild by hand:
`store._from_dict` (off disk), `context.sanitize_percepts` (the redaction
path), `store._as_dict` (onto disk and into `live.json`), and
`ContextBuilder._rank` (into a sort key). **A field added to `Percept` and
forgotten in any of them is dropped silently** - the dataclass applies its
default, so a `valid_until` that never reaches disk, or a `confidence` reset
to the midpoint, is indistinguishable from one that was never set. Nothing
raises. This module is what makes that loud.

The redaction rebuild is the dangerous one. `sanitize_percepts` is the last
thing between a fact and a JSON line that is read back on every later turn,
and a field dropped there is a field whose value is *reset*, not merely lost -
so a test that enumerates the fields it expects would need the same edit and
would be wrong in the same way. `TestTheRedactionPath` therefore compares
against `dataclasses.fields(Percept)` and uses a sentinel for every field, so
it is automatically wrong the moment a field is added and not threaded.

Each of the four increments also gets a mutation, because a test that cannot
fail is worse than no test. The exact mutation and the exact red count are in
the module docstring of each class.
"""

import dataclasses
import json
import time

import pytest

from shani_chronoa.senses import (
    CONFIDENCE_UNSTATED,
    MEMORY_KINDS,
    SENSITIVITY_PRIVATE,
    Percept,
)
from shani_chronoa.senses import memory
from shani_chronoa.senses.context import ContextBuilder, sanitize_percepts
from shani_chronoa.senses.memory import (
    _idf,
    _keywords,
    _relevance,
    _stem,
    _tokens,
    _words,
    recall,
    recall_report,
    remember_fact,
    store_fact,
)
from shani_chronoa.senses.store import PerceptStore, _as_dict, _from_dict

# One non-default value per field, so a field dropped by a rebuild is visibly
# reset rather than coincidentally equal. Compared against
# `dataclasses.fields(Percept)` in two separate tests, which is what makes a
# newly added field fail here even before anyone has written its sentinel.
SENTINELS = {
    "sense": "memory",
    "kind": "preference",
    "content": "the api key is sk-live-0123456789-do-not-leak",
    "created_at": 1_000.5,
    "ttl_seconds": 12.5,
    "source": "unit-test",
    "sensitivity": SENSITIVITY_PRIVATE,
    "metadata": {"key": "coffee", "keywords": ["coffe", "order"]},
    "valid_until": 2_000.5,
    "confidence": 0.25,
    "last_accessed_at": 1_500.5,
}

EPOCH = 1_700_000_000.0


@pytest.fixture
def memory_allowed(monkeypatch):
    """Let the real write path through without touching the user's GSettings."""
    from shani_chronoa.config import ChronoaConfig

    monkeypatch.setattr(
        ChronoaConfig, "sense_allowed", lambda self, sense: sense == "memory"
    )
    monkeypatch.setattr(memory, "_warned_empty_vault", True)


def _store(tmp_path, name="memory.jsonl", **kwargs):
    return PerceptStore(
        durable_path=tmp_path / name, live_path=tmp_path / f"{name}.live", **kwargs
    )


def _seed(entries, tmp_path, name="memory.jsonl", **kwargs):
    """Write `entries` through the real write path into a store, oldest first.

    Two stores, because the write path stamps `time.time()` and a ranking
    fixture needs to know which fact is newer: the facts themselves come from
    the real write path, and only the timestamp is replaced. Writing the
    timestamp-fudged copy through `add()` rather than editing the file keeps
    the seeded store's contents identical to what the module writes.
    """
    scratch = _store(tmp_path, "scratch.jsonl", durable_capacity=10_000)
    store = _store(tmp_path, name, **kwargs)
    for content, age_days in entries:
        stored = remember_fact(content, store=scratch)
        assert stored is not None, f"the write path refused {content!r}"
        store.add(dataclasses.replace(stored, created_at=EPOCH - age_days * 86400))
    return store


def _use_global_store(monkeypatch, store):
    """Point `memory._run`'s process-wide store at a test's own store.

    `_run` resolves its store through `get_store()`, so without this it reads
    the durable file of whoever is running the tests - the same leak
    `AGENTS.md` records, and the reason these tests could otherwise pass or
    fail depending on what is in the real home directory.
    """
    monkeypatch.setattr(memory, "_STORE", store)
    return store


# ===========================================================================
# The redaction path. THE control for this change.
# ===========================================================================


class TestTheRedactionPath:
    """Every field must survive `sanitize_percepts`, not just the ones in the file.

    Mutation used to prove these can fail: delete the `valid_until=` line from
    the rebuild in `context.sanitize_percepts`, leaving the field on the
    dataclass. Measured red: 2 of 48 - the field-by-field comparison and the
    disk round trip that reads the rebuilt percept back. Restored: 0.

    Nothing raises in that state, which is the whole reason this class
    exists: the percept is written out with its deadline reset to `None` and
    looks exactly like one that was never given a deadline. A test asserting
    that sanitization "succeeds" would have stayed green throughout.
    """

    def test_the_sentinels_cover_every_field_the_schema_declares(self):
        """A field with no sentinel here is a field nothing below can catch.

        Written as an equality against the live schema rather than a list, so
        adding a field to `Percept` fails this test on the spot instead of
        leaving the next two tests comparing a field that was never set -
        which would pass, because a default equals a default.
        """
        declared = {f.name for f in dataclasses.fields(Percept)}
        assert declared == set(SENTINELS), (
            f"Percept gained or lost a field: {declared ^ set(SENTINELS)}. "
            f"Give it a non-default sentinel in SENTINELS, or the two tests "
            f"below stop being able to see it dropped."
        )

    def test_the_rebuild_preserves_every_field(self):
        original = Percept(**SENTINELS)
        assert original.content != "the api key is $SECRET:CLOUD_LLM_ANTHROPIC"

        [rebuilt] = sanitize_percepts([original], _redact)

        assert rebuilt.content == "the api key is $SECRET:CLOUD_LLM_ANTHROPIC", (
            "the fixture did not actually redact, so nothing below is tested"
        )
        for field in dataclasses.fields(Percept):
            if field.name == "content":
                continue
            assert getattr(rebuilt, field.name) == getattr(original, field.name), (
                f"sanitize_percepts dropped {field.name!r}: "
                f"{getattr(original, field.name)!r} became {getattr(rebuilt, field.name)!r}. "
                f"A field missing from that rebuild is silently reset to its "
                f"default, so it reaches disk as if it had never been set."
            )

    def test_a_field_dropped_by_the_rebuild_does_not_survive_a_disk_round_trip(self, tmp_path):
        """The consequence, not the mechanism: a reset value reaches the file.

        Reading it back through a *second*, independent store is what makes
        this a statement about the file rather than about one instance's
        in-memory list.
        """
        # ttl_seconds None so `add()` routes it to the durable tier at all;
        # every other field keeps its sentinel.
        probe = dataclasses.replace(Percept(**SENTINELS), ttl_seconds=None)
        [rebuilt] = sanitize_percepts([probe], _redact)
        durable = tmp_path / "memory.jsonl"
        _store(tmp_path).add(rebuilt)

        [from_disk] = PerceptStore(
            durable_path=durable, live_path=tmp_path / "live.json"
        ).durable()

        assert from_disk == rebuilt
        assert from_disk.valid_until == SENTINELS["valid_until"]
        assert from_disk.confidence == SENTINELS["confidence"]
        assert from_disk.last_accessed_at == SENTINELS["last_accessed_at"]

    def test_a_verbatim_passthrough_still_works_when_there_is_nothing_to_redact(self):
        """Both early-return branches must not rebuild, or they lose fields too.

        The `content == percept.content` branch returns the *original object*,
        so a field cannot be lost there - but only because it short-circuits
        rather than reconstructing. Asserted so a future refactor that
        "simplifies" the two returns into one rebuild is caught here instead
        of in production.
        """
        original = Percept(**SENTINELS)
        [unchanged] = sanitize_percepts([original], lambda text: text)
        assert unchanged is original

    def test_no_sanitizer_at_all_is_a_pure_renderer(self):
        [unchanged] = sanitize_percepts([Percept(**SENTINELS)], None)
        assert unchanged == Percept(**SENTINELS)


def _redact(text):
    return text.replace("sk-live-0123456789-do-not-leak", "$SECRET:CLOUD_LLM_ANTHROPIC")


# ===========================================================================
# Increment 1 - stemming. Morphological variants only, and nothing wider.
# ===========================================================================


class TestStemming:
    """    Two mutations, one for each direction, because this class has to catch
    both an expansion that does nothing and one that is too wide.

    - `_stem` returns its argument unchanged: measured red 4 of 48 - the
      cross-inflection recalls, the root-convergence test and the legacy
      record. The no-overlap tests stay green, which is the point of them.
    - `_stem` returns a single constant for every token, so every query
      matches every fact: measured red 8 of 48, including
      `test_the_expansion_cannot_make_an_unrelated_query_match`. That is the
      no-shared-word contract failing in the only direction that matters.
    """

    def test_a_morphological_variant_matches(self, tmp_path, memory_allowed):
        store = _seed([("my editor is neovim", 1)], tmp_path)

        assert [p.content for p in recall("my editors are set to what", store=store)] == [
            "my editor is neovim"
        ], "a plural query no longer finds the singular fact"

    def test_the_verb_forms_meet_too(self, tmp_path, memory_allowed):
        store = _seed([("my editor is neovim", 1)], tmp_path)

        assert [p.content for p in recall("what am I editing with", store=store)] == [
            "my editor is neovim"
        ]

    def test_the_roots_actually_converge(self):
        """Stated as data, so a rule change that breaks it says which pair."""

        for group in (
            ("editor", "editors", "editing", "edit", "edits"),
            ("city", "cities"),
            ("order", "orders", "ordered"),
            ("cat", "cats"),
        ):
            roots = {_stem(word) for word in group}
            assert len(roots) == 1, f"{group} reduced to {roots}"

    def test_stemming_is_idempotent(self):
        """The property the whole design rests on.

        A single-application stripper is not closed under composition, so
        `editors` would become `editor` while `editor` became `edit` and the
        two halves of one word would stop meeting. The fixpoint loop is what
        makes the stem of a stem the stem - and it is also what lets stored
        keywords be stems already without a second, different reduction.
        """
        words = [
            "editors", "editor", "editing", "edit", "cities", "city", "orders",
            "order", "ordered", "classes", "class", "analysis", "address",
            "thing", "string", "running", "cats", "cat", "gas", "author",
        ]
        for word in words:
            once = _stem(word)
            assert _stem(once) == once, f"{word!r} -> {once!r} -> {_stem(once)!r}"

    def test_words_that_must_not_be_mangled_are_left_alone(self):
        """The guards that stop the stripper producing fragments."""
        for word in ("analysis", "address", "thing", "string", "gas", "class"):
            assert _stem(word) == word, f"{word!r} was reduced to {_stem(word)!r}"

    def test_it_cannot_reach_across_a_gap_in_the_vocabulary(self, tmp_path, memory_allowed):
        """The honest limit, asserted rather than asserted-in-a-docstring.

        "what do I drink" shares no root with "a flat white", and no suffix
        rule bridges that. A future change that made it match would be a
        change in what the module claims to be, so it is a test.
        """
        store = _seed([("my coffee order is a flat white", 1)], tmp_path)

        assert recall("what do I drink", store=store) == []
        assert recall("aardvark", store=store) == []

    def test_a_fact_written_before_stemming_existed_is_still_reachable(self):
        """The expansion must work on the older half of a user's memory.

        `metadata["keywords"]` on disk held surface forms until this change.
        A record like that is read through `_stem` too, so an inflected query
        finds it; if only the *content* were stemmed this would fail, and the
        bug would be invisible on any store written after the deploy.
        """
        legacy = Percept(
            sense="memory",
            kind="fact",
            content="The user's editor: neovim",
            created_at=EPOCH,
            metadata={"key": "editor", "keywords": ["my", "editor", "neovim"]},
        )
        assert "edit" in _keywords(legacy), (
            "a legacy record's surface-form keywords did not reduce"
        )
        assert _stem("editor") in _keywords(legacy)
        assert "neovim" in _keywords(legacy)

    def test_forgetting_does_not_stem(self, tmp_path, memory_allowed):
        """The one asymmetry in the module, and it is deliberate.

        Recall widening by a suffix is a nuisance; forget widening by one is
        an irreversible deletion of a fact the user did not name. So
        `forget_facts` compares the words as typed.
        """
        store = _seed(
            [("my editor is neovim", 2), ("I edited the deck yesterday", 1)], tmp_path
        )
        assert recall("my editor", store=store), "fixture no longer has a match at all"

        removed = memory.forget_facts("my editor", store=store)

        assert removed == 1, (
            "forget matched a fact whose words merely share a root; the match "
            "must stay literal because the deletion is irreversible"
        )
        assert [p.content for p in store.durable()] == ["I edited the deck yesterday"]

    def test_forgetting_still_finds_a_fact_by_the_words_the_user_typed(self, tmp_path, memory_allowed):
        """The narrowing must not have broken the operation that has to work."""
        store = _store(tmp_path)
        remember_fact("my editor is neovim", store=store)

        assert memory.forget_facts("my editor", store=store) == 1
        assert store.durable() == []

    def test_forgetting_finds_a_word_the_label_phrasing_dropped(self, tmp_path, memory_allowed):
        """`extract_fact` rewrites the fact; the user's word survives in keywords.

        Without that, "forget my espresso" could not find a fact stored as
        "The user's espresso: a flat white" - and the fix for it (stemming the
        forget query) is precisely what the previous test rules out. Recording
        the keywords unstemmed is what lets both be true.
        """
        store = _store(tmp_path)
        stored = store_fact(memory.extract_fact("my espresso is a flat white"), store=store)
        assert stored.content == "The user's espresso: a flat white"
        assert "espresso" in _words(stored.content), (
            "the fixture no longer needs its keywords: the word survived the "
            "label phrasing, so this test would pass without them"
        )

        assert memory.forget_facts("espresso", store=store) == 1
        assert store.durable() == []

    def test_the_expansion_cannot_make_an_unrelated_query_match(self, tmp_path, memory_allowed):
        """The no-shared-word contract, stated against the *expanded* vocabulary.

        This is the assertion that would catch an over-broad stemmer: it
        compares the query's roots against every fact's roots directly, so it
        is sensitive to what the expansion produced rather than only to the
        final answer. If a future rule collapsed two unrelated words onto one
        root, this goes red even if the top-level `recall(...) == []` check
        happened to survive.
        """
        store = _seed(
            [
                ("my coffee order is a flat white", 3),
                ("my bike is a green Hero", 2),
                ("I live in Pune", 1),
            ],
            tmp_path,
        )
        vocabularies = [_keywords(p) for p in store.durable()]
        assert len(vocabularies) == 3, "fixture no longer holds three facts"

        for query in ("what do I drink", "aardvark zebra", "quantum tunnelling"):
            wanted = _tokens(query)
            assert wanted, f"fixture drifted: {query!r} tokenises to nothing"
            for vocabulary in vocabularies:
                assert not (wanted & vocabulary), (
                    f"{query!r} expanded to {wanted & vocabulary}, which a stored "
                    f"fact also contains - the expansion is now wide enough to "
                    f"invent a match that was not there"
                )
            assert recall(query, store=store) == []


# ===========================================================================
# Increment 2 - valid_until. Fact-scoped staleness, separate from TTL.
# ===========================================================================


class TestValidityWindow:
    """    Two mutations, because the field can be lost on the way in or on the way
    back out and the two failures are indistinguishable to a user.

    - Delete the `valid_until` check from `Percept.is_expired`: measured red
      6 of 48 - the stale fact is quoted, reaches the context block, and is
      reported as current.
    - Drop `valid_until` from `store._from_dict`: measured red 2 of 48. The
      record works in the process that wrote it and stops working after a
      restart, which is the failure this one is here to catch.
    """

    def test_a_fact_past_its_validity_is_not_quoted_as_current(self, tmp_path, memory_allowed):
        store = _store(tmp_path)
        remember_fact("my job title is staff engineer", store=store, valid_until=time.time() - 1)
        remember_fact("my bike is a green Hero", store=store)

        found = recall("my job title", store=store)

        assert found == [], "a fact past its stated validity was quoted as current"
        assert [p.content for p in store.active()] == ["my bike is a green Hero"]

    def test_the_same_fact_inside_its_window_is_quoted(self, tmp_path, memory_allowed):
        store = _store(tmp_path)
        remember_fact("my job title is staff engineer", store=store, valid_until=time.time() + 3600)

        assert [p.content for p in recall("job title", store=store)] == [
            "my job title is staff engineer"
        ]

    def test_expiry_stays_out_of_the_context_block(self, tmp_path, memory_allowed):
        """The path the model actually reads, checked directly.

        `store.active()` filtering is not sufficient evidence: `render()` is
        handed a list by callers other than the store's own read path, and a
        stale fact reaching the prompt is quoted back as current by a model
        that has no way to know its window closed.
        """
        stale = Percept(
            sense="memory", kind="fact", content="the standup room is the annex",
            created_at=time.time(), valid_until=time.time() - 1,
            sensitivity=SENSITIVITY_PRIVATE,
        )
        fresh = Percept(
            sense="memory", kind="fact", content="the standup room is 2B",
            created_at=time.time(), sensitivity=SENSITIVITY_PRIVATE,
        )

        rendered = ContextBuilder().render([stale, fresh])

        assert "annex" not in rendered
        assert "2B" in rendered, "withholding one stale percept silenced the rest"

    def test_expired_is_reported_as_expired_and_not_as_never_stored(self, tmp_path, memory_allowed, monkeypatch):
        """The three outcomes must be distinguishable in what the user is told.

        This is the requirement that a bare `recall() -> list` cannot express,
        and the reason `recall_report()` exists: "you told me this and it has
        run out" sends the user to update the fact, while "you never told me"
        sends them to say it again from scratch.
        """
        store = _use_global_store(
            monkeypatch, _store(tmp_path, "cli.jsonl")
        )
        remember_fact("my standup is in the annex", store=store, valid_until=time.time() - 1)

        report = recall_report("my standup", store=store)

        assert report.facts == []
        assert [p.content for p in report.expired] == ["my standup is in the annex"]
        assert recall("my standup", store=store) == []

        said = memory._run({"operation": "recall", "query": "my standup"}).content
        assert "Nothing current" in said, said
        assert "No stored fact shares a content word" not in said, (
            "an expired fact was reported as never having been stored:\n" + said
        )

    def test_a_never_stored_fact_says_something_different_from_an_expired_one(self, tmp_path, memory_allowed, monkeypatch):
        store = _use_global_store(monkeypatch, _store(tmp_path, "cli2.jsonl"))
        remember_fact("my standup is in the annex", store=store, valid_until=time.time() - 1)

        expired_answer = memory._run({"operation": "recall", "query": "my standup"}).content
        absent_answer = memory._run({"operation": "recall", "query": "aardvark"}).content

        assert expired_answer != absent_answer
        assert "No stored fact shares a content word" in absent_answer
        assert recall("aardvark", store=store) == [], (
            "a query sharing no content word must still return nothing"
        )

    def test_a_current_fact_alongside_an_expired_one_names_the_expired_one(self, tmp_path, memory_allowed, monkeypatch):
        store = _use_global_store(monkeypatch, _store(tmp_path, "cli3.jsonl"))
        remember_fact("the standup is at half past ten in the annex", store=store)
        remember_fact("the standup used to be in the annex", store=store,
                      valid_until=time.time() - 1)

        said = memory._run({"operation": "recall", "query": "standup annex"}).content

        assert "still on disk" in said, (
            "the expired match was dropped without saying it exists, which is "
            f"indistinguishable from never having been told it:\n{said}"
        )
        assert "annex" in said, "the expired fact was not even named"

    def test_a_current_fact_is_answered_normally(self, tmp_path, memory_allowed, monkeypatch):
        """The other branch, so the expired-reporting cannot be all it ever says."""
        store = _use_global_store(monkeypatch, _store(tmp_path, "cli4.jsonl"))
        remember_fact("my standup is in the annex", store=store, valid_until=time.time() + 3600)

        said = memory._run({"operation": "recall", "query": "my standup"}).content

        assert "annex" in said
        assert "still on disk" not in said

    def test_an_expired_fact_is_filtered_not_deleted(self, tmp_path, memory_allowed):
        """Expiry is about relevance. Consent is about permission. Neither erases.

        A fact whose window closes stays on disk, because the window may be
        reopened and because silently destroying stored facts on a schedule is
        exactly the surveillance-shaped behaviour the transient/durable split
        exists to avoid. The proof that it is filtered rather than deleted is
        that `forget_facts` can still find and erase it afterwards.
        """
        store = _store(tmp_path)
        remember_fact("my job title is staff engineer", store=store, valid_until=time.time() - 1)

        assert [p.content for p in store.durable()] == ["my job title is staff engineer"]
        assert len((tmp_path / "memory.jsonl").read_text().strip().splitlines()) == 1
        assert recall("job title", store=store) == []

        assert memory.forget_facts("job title", store=store) == 1
        assert store.durable() == []

    def test_the_window_survives_a_restart(self, tmp_path, memory_allowed):
        store = _store(tmp_path)
        remember_fact("my job title is staff engineer", store=store, valid_until=12345.0)

        reread = PerceptStore(durable_path=tmp_path / "memory.jsonl", live_path=tmp_path / "live.json")

        assert reread.durable()[0].valid_until == 12345.0
        assert reread.durable()[0].is_expired(now=20000.0)
        assert not reread.durable()[0].is_expired(now=10000.0)

    def test_a_corrupt_window_degrades_to_permanent_rather_than_guessed(self):
        """Both plausible repairs would be wrong in the dangerous direction.

        Treating an unparseable `valid_until` as "now" destroys a fact the user
        did not ask to lose; treating it as absent quotes a stale one as
        current. The first is chosen and the reason is recorded in
        `_from_dict`; this asserts the choice rather than the reasoning.
        """
        base = _as_dict(Percept(sense="memory", kind="fact", content="x", created_at=0.0))

        for corrupt in ("soon", None, True, [1]):
            raw = {**base, "valid_until": corrupt}
            assert _from_dict(raw).valid_until is None, f"{corrupt!r} became a deadline"

    def test_the_window_is_reachable_through_the_sense(self, memory_allowed, tmp_path, monkeypatch):
        """A schema field nothing can set is the dead-code shape AGENTS.md warns about."""
        import shani_chronoa.senses.memory as memory_mod

        store = _store(tmp_path, "cli.jsonl")
        monkeypatch.setattr(memory_mod, "_STORE", store)

        stored = memory_mod._run(
            {
                "operation": "remember",
                "fact": "the standup moved to 2B",
                "valid_for_minutes": 45,
            }
        )

        assert stored.ttl_seconds is None, "memory is the only durable sense"
        assert stored.valid_until == pytest.approx(time.time() + 2700, abs=30)
        assert recall("standup", store=store) == [stored]

    def test_a_nonsense_window_is_ignored_rather_than_crashing(self, memory_allowed, tmp_path, monkeypatch):
        import shani_chronoa.senses.memory as memory_mod

        store = _store(tmp_path, "cli2.jsonl")
        monkeypatch.setattr(memory_mod, "_STORE", store)

        stored = memory_mod._run(
            {"operation": "remember", "fact": "the standup is in 2B", "valid_for_minutes": "ages"}
        )

        assert stored.valid_until is None
        assert recall("standup", store=store) == [stored]


# ===========================================================================
# Increment 3 - confidence. A tie-break, and nothing that moves the budget.
# ===========================================================================


class TestConfidence:
    """    Mutation used to prove these can fail: put `confidence` *after*
    `created_at` in `recall`'s sort key, so the tie it exists to break is
    handed straight back to recency. Measured red 1 of 48.

    One is the honest count. A tie-break has exactly one observable
    consequence, and the rest of this class guards the opposite risk - that
    the field overreaches and starts overriding relevance or sensitivity.
    """

    def test_confidence_breaks_a_tie_the_relevance_score_cannot(self, tmp_path, memory_allowed):
        """Two identical facts, different provenance, and the older one wins.

        The content is deliberately byte-identical, so relevance ties exactly
        and nothing but `confidence` can separate them - and the *older*
        record is the one carrying the user's own confidence. Ordering by
        `created_at` alone hands the answer to the newer, less trustworthy
        copy, which is the failure this field exists to stop.
        """
        scratch = _store(tmp_path, "scratch.jsonl", durable_capacity=10)
        stated = remember_fact("my coffee order is a flat white", store=scratch)
        guessed = store_fact(
            memory.extract_fact("I really like filter coffee"), store=scratch
        )
        assert stated.confidence > guessed.confidence, (
            "fixture drifted: the two write paths no longer disagree about "
            "their own confidence, so there is no tie for confidence to break"
        )

        store = _store(tmp_path, "ordered.jsonl")
        store.add(dataclasses.replace(stated, created_at=EPOCH, confidence=0.9))
        store.add(dataclasses.replace(stated, created_at=EPOCH + 500.0, confidence=0.1))

        docs = store.durable()
        assert _keywords(docs[0]) == _keywords(docs[1]), "fixture is not a genuine tie"
        scores = {_shipped_score(store, "coffee", p.content) for p in docs}
        assert len(scores) == 1, f"fixture no longer ties on relevance: {scores}"

        found = recall("coffee", store=store, limit=2)

        assert [p.confidence for p in found] == [0.9, 0.1], (
            f"confidence did not break the tie; got {[(p.content, p.confidence) for p in found]}"
        )
        assert found[0].created_at < found[1].created_at, (
            "the newer fact won, so `created_at` is still doing the ranking"
        )

    def test_the_default_is_uniform_so_nothing_existing_reordered(self):
        """The upgrade property, and the reason the default is a midpoint.

        A new field that disagreed between pre-existing records would silently
        re-rank every prompt for every user who already had a store. Uniform
        means the key is a constant, so a set of such percepts sorts exactly as
        `(rank, -created_at)` did - asserted by computing both.
        """
        assert CONFIDENCE_UNSTATED == 0.5
        builder = ContextBuilder()
        now = time.time()
        percepts = [
            Percept(sense="power", kind="machine-state", content=f"reading {i}",
                    created_at=now - i * 60, ttl_seconds=600.0)
            for i in range(6)
        ]
        for p in percepts:
            assert p.confidence == CONFIDENCE_UNSTATED

        by_new_field = [p.content for p in sorted(percepts, key=builder._rank)]
        by_old_key = [
            p.content
            for p in sorted(percepts, key=lambda p: (0, -p.created_at))
        ]

        assert by_new_field == by_old_key

    def test_a_corrupt_confidence_is_clamped_not_trusted(self):
        base = _as_dict(Percept(sense="memory", kind="fact", content="x", created_at=0.0))

        assert _from_dict({**base, "confidence": 4.2}).confidence == 1.0
        assert _from_dict({**base, "confidence": -3}).confidence == 0.0
        assert _from_dict({**base, "confidence": "very"}).confidence == CONFIDENCE_UNSTATED
        assert _from_dict({**base, "confidence": True}).confidence == CONFIDENCE_UNSTATED

    def test_confidence_does_not_change_the_message_count_or_the_budget(self):
        """The perturbation the schema change could have caused, checked.

        `ContextBuilder` budgets by characters and caps the percept count, so
        "confidence" is only safe if it reorders within a set and never
        promotes an extra percept past `max_percepts` or an extra line past
        `budget_chars`. Both are asserted against a builder that would notice.
        """
        percepts = [
            Percept(sense="memory", kind="fact", content=f"fact number {i} about coffee",
                    created_at=EPOCH - i, sensitivity=SENSITIVITY_PRIVATE)
            for i in range(10)
        ]
        builder = ContextBuilder(budget_chars=200, max_percepts=3)
        history = [{"role": "system", "content": "s"}]

        baseline = builder.render(percepts)
        boosted = builder.render(
            [dataclasses.replace(p, confidence=1.0 if i == 9 else 0.0) for i, p in enumerate(percepts)]
        )

        for rendered in (baseline, boosted):
            lines = rendered.splitlines()[1:]
            assert 0 < len(lines) <= 3, f"max_percepts was exceeded: {len(lines)}"
            assert sum(len(line) for line in lines) <= 200, "budget_chars was exceeded"
        assert len(builder.build_messages(history, percepts)) == 2, (
            "a new field changed the number of messages in the prompt"
        )
        assert len(builder.build_messages(history, [])) == 1

    def test_confidence_does_not_outrank_sensitivity(self):
        """It must not promote a private percept over a public one.

        Sensitivity is the privacy ordering and it is not negotiable by a
        content signal: confidence says how much a producer trusts its claim,
        not how sendable the claim is.
        """
        now = time.time()
        public = Percept(sense="power", kind="machine-state", content="battery 80%",
                         created_at=now, ttl_seconds=60.0, confidence=0.0)
        private = Percept(sense="memory", kind="fact", content="my name is Priya",
                          created_at=now, sensitivity=SENSITIVITY_PRIVATE, confidence=1.0)

        rendered = ContextBuilder().render([public, private])

        assert rendered.index("battery") < rendered.index("Priya")

    def test_a_confident_fact_still_loses_to_a_more_relevant_one(self, tmp_path, memory_allowed):
        """A tie-break, not a second ranking function.

        This is the line the field must not cross: relevance decides, and
        confidence only orders what relevance left tied. Both facts here
        mention the query's words, but only one of them is the answer, and
        giving the confident-but-wrong one the top slot would be worse than
        the recency ordering it replaced.
        """
        store = _store(tmp_path, "relevance.jsonl")
        scratch = _store(tmp_path, "relscratch.jsonl", durable_capacity=10)
        terse = remember_fact("my coffee order is a flat white", store=scratch)
        verbose = remember_fact(
            "the annex coffee machine descaled again and the order went out flat",
            store=scratch,
        )
        store.add(dataclasses.replace(terse, created_at=EPOCH, confidence=0.0))
        store.add(dataclasses.replace(verbose, created_at=EPOCH + 1000.0, confidence=1.0))

        found = recall("what is my coffee order", store=store, limit=2)

        assert [p.content for p in found] == [
            "my coffee order is a flat white",
            "the annex coffee machine descaled again and the order went out flat",
        ], "confidence was allowed to override relevance"


def _shipped_score(store, query, content):
    """The shipped relevance score for one fact, recomputed for tie assertions."""
    docs = store.durable()
    vocabularies = [_keywords(p) for p in docs]
    wanted = _tokens(query)
    total = len(vocabularies)
    freq = {t: sum(t in v for v in vocabularies) for v in vocabularies for t in v}
    query_mass = sum(_idf(total, freq.get(t, 0)) for t in wanted)
    for percept, vocabulary in zip(docs, vocabularies):
        if percept.content != content:
            continue
        shared = sum(_idf(total, freq.get(t, 0)) for t in wanted if t in vocabulary)
        mass = sum(_idf(total, freq.get(t, 0)) for t in vocabulary)
        return _relevance(shared, query_mass, mass)
    raise AssertionError(f"{content!r} is not in the store")


# ===========================================================================
# Increment 4 - last_accessed_at, and a durable tier bounded by construction.
# ===========================================================================


class TestTheDurableTierIsBounded:
    """    Two mutations, one for each half of the bound.

    - Evict by list position instead of by `access_key`, i.e. a FIFO bound:
      measured red 2 of 48. The most recently recalled fact is the one
      dropped, which is a bound with no policy in it.
    - Remove the cap check entirely, restoring the unbounded tier this change
      exists to bound: measured red 5 of 48 - the cap itself, the eviction
      order, both "capacity is not expiry / not consent" tests, and the
      duplicate-line check, which only becomes reachable once the rewrite
      path is live.
    """

    def test_the_tier_stops_growing_at_its_cap(self, tmp_path, memory_allowed):
        store = _store(tmp_path, durable_capacity=5)

        for i in range(20):
            remember_fact(f"remembered thing number {i}", store=store)

        assert len(store.durable()) == 5, "the durable tier is not bounded by count"
        assert len((tmp_path / "memory.jsonl").read_text().strip().splitlines()) == 5, (
            "the bound is only in memory: the file on disk is what grows forever"
        )
        assert [p.content for p in store.durable()] == [
            f"remembered thing number {i}" for i in range(15, 20)
        ], "eviction is not FIFO among facts nobody has recalled"

    def test_eviction_prefers_the_fact_nobody_asked_for(self, tmp_path, memory_allowed):
        """LRU, and a fixture where FIFO gives a different answer.

        The *oldest* fact is the one recalled, so it is not the eviction
        candidate - which is the whole difference between a bound with a
        policy in it and a bound that drops whatever is oldest. Evicting by
        position instead of by `access_key` drops "my editor is neovim" here
        and leaves the assertion below failing.
        """
        store = _seed(
            [("my editor is neovim", 3), ("my bike is a green Hero", 2), ("I live in Pune", 1)],
            tmp_path,
            durable_capacity=3,
        )
        assert [p.content for p in store.durable()] == [
            "my editor is neovim", "my bike is a green Hero", "I live in Pune"
        ], "fixture no longer starts oldest-first"

        recall("my editor", store=store)
        remember_fact("my timezone is asia kolkata", store=store)

        assert [p.content for p in store.durable()] == [
            "my editor is neovim", "I live in Pune", "my timezone is asia kolkata"
        ], "the recalled fact was evicted; this is FIFO, not least-recently-used"

    def test_an_expired_fact_still_occupies_its_slot(self, tmp_path, memory_allowed):
        """Capacity is not expiry, and the two must not be merged.

        The expired fact here has the *most recent* access key, so a bound that
        consults `is_expired()` deletes it and one that consults the access key
        keeps it. It is stamped through `mark_accessed` rather than through
        `recall()` because recall never returns an expired fact and so can
        never improve its key - which is a property worth knowing, and is why
        the stamp is exercised through the store's own API.
        """
        store = _store(tmp_path, "expiry.jsonl", durable_capacity=2)
        scratch = _store(tmp_path, "expiry-scratch.jsonl", durable_capacity=10)
        expired = remember_fact("my job title is staff engineer", store=scratch)
        current = remember_fact("my bike is a green Hero", store=scratch)
        store.add(dataclasses.replace(expired, created_at=EPOCH - 3 * 86400,
                                      valid_until=time.time() - 1))
        store.add(dataclasses.replace(current, created_at=EPOCH - 2 * 86400))
        # The store's own copy, not the scratch one: `mark_accessed` matches by
        # identity, so passing an equal-but-distinct percept stamps nothing.
        assert store.mark_accessed([store.durable()[0]], now=EPOCH + 10 * 86400) == 1

        remember_fact("I live in Pune", store=store)

        assert [p.content for p in store.durable()] == [
            "my job title is staff engineer", "I live in Pune"
        ], "an expired fact was evicted, which is a deletion the user never asked for"
        assert len((tmp_path / "expiry.jsonl").read_text().strip().splitlines()) == 2

    def test_an_expired_fact_is_still_erased_by_an_explicit_forget(self, tmp_path, memory_allowed):
        """The other half of "not deleted": the cap is the only silent deletion.

        Expiry on its own must never remove a record, or a relevance filter
        would double as a retention policy the user never chose.
        """
        store = _store(tmp_path)
        remember_fact("my job title is staff engineer", store=store, valid_until=time.time() - 1)

        assert len(store.durable()) == 1
        assert memory.forget_facts("job title", store=store) == 1
        assert store.durable() == []

    def test_a_consent_withheld_fact_still_occupies_its_slot(self, tmp_path, memory_allowed):
        """Capacity is not consent either.

        `ContextBuilder` withholds a fact whose sense is no longer permitted
        and keeps it in the store so re-granting costs nothing. The fixture
        makes eviction actually happen (the older, *permitted* fact is the
        one dropped) and the withheld fact the one that survives - so a
        consent-aware eviction, which would have deleted it, is ruled out
        rather than merely unexercised.
        """
        store = _seed(
            [("my editor is neovim", 3), ("my name is Priya", 2)], tmp_path, durable_capacity=2
        )
        builder = ContextBuilder(consent=lambda sense: sense == "power")
        assert builder.render(store.active()) == "", "fixture: nothing is being withheld"

        recall("my name is Priya", store=store)
        remember_fact("my timezone is asia kolkata", store=store)

        assert [p.content for p in store.durable()] == [
            "my name is Priya", "my timezone is asia kolkata"
        ], (
            "eviction consulted something other than use: the withheld, "
            "recalled fact was dropped and the older permitted one kept, so "
            "withdrawing a permission would cost the user a memory"
        )

    def test_a_fresh_rewrite_never_leaves_a_duplicate_line_behind(self, tmp_path, memory_allowed):
        """The append-only trap, caught by counting lines in the file.

        `durable()` cannot see a duplicate - it is one instance's list - so
        the only place this is visible is the file, and a duplicate there is
        permanent because nothing ever rewrites the file except `forget()`.
        """
        store = _store(tmp_path, durable_capacity=3)
        for i in range(9):
            remember_fact(f"remembered thing number {i}", store=store)

        contents = [
            json.loads(line)["content"]
            for line in (tmp_path / "memory.jsonl").read_text().strip().splitlines()
        ]

        assert len(contents) == len(set(contents)), f"a fact is stored twice: {contents}"
        assert len(contents) == 3

    def test_the_access_stamp_reaches_disk_on_the_next_rewrite(self, tmp_path, memory_allowed):
        """The piggyback, and why it is not a second write path.

        The durable file is append-only apart from one full rewrite, so a
        per-read write would be a new write path for one timestamp. Instead
        the stamp rides out on the rewrite `forget()` already performs.
        """
        store = _store(tmp_path, durable_capacity=10)
        remember_fact("my editor is neovim", store=store)
        remember_fact("my bike is a green Hero", store=store)

        recall("my editor", store=store)
        lines = (tmp_path / "memory.jsonl").read_text().strip().splitlines()
        assert all(json.loads(line)["last_accessed_at"] is None for line in lines), (
            "a read wrote to the durable file, which is the second write path "
            "this was supposed to avoid"
        )

        removed = memory.forget_facts("my bike", store=store)
        assert removed == 1

        records = {
            r["content"]: r
            for r in (
                json.loads(line)
                for line in (tmp_path / "memory.jsonl").read_text().strip().splitlines()
            )
        }
        assert records["my editor is neovim"]["last_accessed_at"] is not None, (
            "the access stamp was not persisted by the rewrite that already happened"
        )

    def test_the_stamp_survives_a_restart(self, tmp_path, memory_allowed):
        store = _store(tmp_path, durable_capacity=10)
        remember_fact("my editor is neovim", store=store)
        remember_fact("my bike is a green Hero", store=store)
        recall("my editor", store=store)
        memory.forget_facts("my bike", store=store)

        reopened = PerceptStore(
            durable_path=tmp_path / "memory.jsonl", live_path=tmp_path / "memory.jsonl.live"
        )

        assert reopened.durable()[0].last_accessed_at is not None

    def test_a_forget_that_matches_nothing_does_not_rewrite_the_file(self, tmp_path, memory_allowed):
        """The reason the piggyback is allowed to be late.

        Rewriting on a no-op would turn "what do you remember about nothing"
        into a full-file write, and would also lose the stamps-as-in-memory
        argument for ever being flushed at all.
        """
        store = _store(tmp_path, durable_capacity=10)
        remember_fact("my editor is neovim", store=store)
        before = (tmp_path / "memory.jsonl").read_text()

        assert memory.forget_facts("aardvark", store=store) == 0

        assert (tmp_path / "memory.jsonl").read_text() == before

    def test_a_fact_never_recalled_is_a_first_eviction_candidate(self, tmp_path, memory_allowed):
        """`access_key` falls back to `created_at`, which is what makes the
        order mean something at all.

        With every stamp equal to None the ordering would be arbitrary; with
        every stamp equal to "now" it would be arbitrary too, which is the
        trap that stopped `active()` from stamping.
        """
        early = Percept(sense="memory", kind="fact", content="old", created_at=100.0)
        late = Percept(sense="memory", kind="fact", content="new", created_at=200.0)
        used = dataclasses.replace(early, last_accessed_at=500.0)

        assert early.access_key() == 100.0
        assert used.access_key() == 500.0
        assert sorted([late, used, early], key=Percept.access_key) == [early, late, used]

    def test_marking_the_same_fact_twice_only_counts_once(self, tmp_path, memory_allowed):
        store = _store(tmp_path, durable_capacity=10)
        remembered = remember_fact("my editor is neovim", store=store)
        same_again = store.durable()[0]

        assert store.mark_accessed([remembered, same_again], now=1000.0) == 1, (
            "the identity check fell back to equality, so a second fact with "
            "identical fields was restamped instead"
        )
        assert store.durable()[0].last_accessed_at == 1000.0

    def test_a_cap_that_cannot_be_reached_by_a_single_store_is_not_the_default(self, tmp_path, memory_allowed):
        """The default has to be big enough that eviction is not routine."""
        from shani_chronoa.senses.store import _DURABLE_CAPACITY

        assert _DURABLE_CAPACITY >= 100, (
            "a cap this low starts evicting real facts for a user who "
            "legitimately remembers a lot"
        )
        assert _store(tmp_path)._durable_capacity == _DURABLE_CAPACITY


# ===========================================================================
# The documented kind vocabulary.
# ===========================================================================


class TestTheKindVocabularyIsReal:
    def test_every_documented_kind_is_used_or_declared_for_a_producer(self):
        """Documentation that cannot silently drift from the code.

        A free string is the right shape for `kind` - a user drop-in may
        declare whatever it likes - which means the list can rot. Asserting
        each entry is non-empty and that the memory layer's own producer uses
        the one it documents is what keeps the vocabulary honest.
        """
        assert MEMORY_KINDS == {
            "fact", "procedure", "entity", "state", "preference", "validity",
        }
        stored = Percept(sense="memory", kind="fact", content="x", created_at=0.0)
        assert stored.kind in MEMORY_KINDS

    def test_the_registry_never_declares_a_kind_outside_the_documented_names(self):
        """Machine-state and OCR/image kinds are a separate vocabulary.

        This does not close `kind` - a user drop-in may use any string, and
        forcing one vocabulary on them would skip their whole module over a
        naming preference. What it does check is that the *builtin* kinds are
        one of the two documented sets, so a new builtin cannot invent a
        third silently.
        """
        from shani_chronoa.senses import discover_senses

        known = MEMORY_KINDS | {"machine-state", "image_text", "image_description",
                                "page", "utterance", "text-file", "confirmation"}
        declared = {sense.kind for sense in discover_senses().values()}

        assert declared <= known, f"undeclared builtin kinds: {sorted(declared - known)}"
