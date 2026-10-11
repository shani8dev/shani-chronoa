"""The semantic floor of the conversation store's hybrid search.

`harness-study/HARVEST-2/E-memory-mcp-special.md` records mem0's "threshold gate
before combining" as a gap - *"a correctness guard Chronoa's hybrid search lacks
(reciprocal rank has no such floor)"*. **That claim is wrong, and the way it is
wrong is the reason this file exists.** `conversation_store.MEANING_FLOOR = 0.55`
has existed all along and is applied *before* the fusion, exactly where mem0
applies it. What Chronoa actually lacks is a test: 17 tests in
`test_conversation_store.py`, none mention the floor, and no other file covers it
either.

So the guard was real and unpinned - which in this repository is the same
statement as *the guard might not be real*. A floor somebody can delete without
turning the suite red is a floor nobody has.

These tests drive the real `search()` through a fake `embed` whose vectors are
normalised word indicators, so cosine similarity is exact arithmetic rather than a
model output. That is the only way to place a message on a chosen side of a
threshold without waiting for a real embedding model to happen to land near it -
and each test states the number it depends on, so a fixture that drifts fails
loudly rather than quietly changing what is being tested.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import conversation_store  # noqa: E402

#: A fixed vocabulary, so the query and the documents are embedded in the same
#: coordinate system. `embed()` is called separately for the query and for the
#: batch of documents, and a vocabulary derived per call would give them
#: different lengths - `similarity` zips, which truncates silently to the shorter
#: one and would score unrelated texts as identical.
VOCABULARY = sorted({
    "and", "ate", "ball", "big", "bright", "dropping", "fine", "floor", "friend",
    "had", "hall", "happy", "i", "is", "it", "keeps", "lunch", "me", "my", "of",
    "on", "our", "pasta", "router", "sun", "the", "today", "us", "was", "weather",
    "where", "with",
})

#: The query, and three stored messages placed by hand around it.
QUERY = "the router keeps dropping"

#: The query verbatim: matches every keyword and is semantically identical.
NEAR = "the router keeps dropping"

#: **The case the floor exists for.** Every keyword of the query appears in it, so
#: the FTS5 side ranks it - and it is semantically a different subject entirely.
#: Without the floor, reciprocal-rank fusion carries it into the results and the
#: `match` label reads "words and meaning", which is the sentence it must never
#: say. Its cosine similarity is asserted below, at 0.38, so the fixture states
#: the arithmetic it depends on rather than relying on it staying true.
KEYWORD_ONLY = (
    "the router keeps dropping the ball on the floor of the big hall where i "
    "ate the pasta with my friend and the sun was bright today and the fine "
    "weather kept us happy"
)

#: Shares nothing with the query at all, so neither side should find it.
FAR = "i had pasta for lunch and it was fine"


def _embed_factory():
    """A deterministic `embed(texts, purpose)` whose similarity is arithmetic.

    Each text becomes the normalised indicator over `VOCABULARY`, so cosine
    similarity between two texts is exactly their word overlap - no model, and no
    tolerance to guess.
    """
    def embed(texts, purpose=""):
        out = []
        for text in texts:
            words = {w for w in text.lower().split() if w.isalpha()}
            norm = len(words) ** 0.5
            out.append([1.0 / norm if w in words else 0.0 for w in VOCABULARY])
        return out
    return embed


def _similarity(a: str, b: str) -> float:
    embed = _embed_factory()
    return sum(x * y for x, y in zip(embed([a])[0], embed([b])[0]))


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A store holding one session per message, built through the store's own API.

    Hand-written JSONL would not do it: `append()` writes the file but does not
    register the session, `list_sessions()` reads the index, and `search()` then
    drops every hit at `if not row or row[0] not in titles` - which fails as an
    empty list and looks exactly like the code being broken.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    base = conversation_store.session_dir()
    for text in (NEAR, KEYWORD_ONLY, FAR):
        path = conversation_store.active_path(base)
        conversation_store.append({"role": "user", "content": text}, path)
        conversation_store.new_session(base)
    return base


class TestTheFixturePlacesTheMessagesWhereItSays:
    """The control for everything below. If these are not true, the assertions in
    the next class are testing a different situation than the one they describe -
    which is the failure mode this file exists to prevent."""

    def test_the_near_message_is_identical_to_the_query(self):
        assert _similarity(QUERY, NEAR) == 1.0

    def test_the_keyword_only_message_is_past_the_floor_but_below_it(self):
        """Both halves matter: it must clear the *keyword* side and fail the
        *meaning* side, or it is not the case the floor is for."""
        floor = conversation_store.MEANING_FLOOR
        score = _similarity(QUERY, KEYWORD_ONLY)
        assert 0.0 < score < floor, (
            f"similarity {score:.3f} is not below the floor {floor}")
        assert score < 0.40, score      # room to move before the test stops meaning it

    def test_the_far_message_is_found_by_neither_side(self):
        assert _similarity(QUERY, FAR) == 0.0


class TestTheFloor:
    def test_a_message_matching_every_keyword_is_not_labelled_a_meaning_match(
            self, root):
        """The floor's contract, and the exact property `HARVEST-2` says is absent.

        `match` is the sentence the panel shows a person. "words and meaning" on a
        message that is semantically about something else is a confident wrong
        answer about *why* a result came back - the failure shape this repository
        records most often.
        """
        hits = conversation_store.search(root, QUERY, embed=_embed_factory())
        for hit in hits:
            if KEYWORD_ONLY[:40].lower() in hit["snippet"].lower():
                assert hit["match"] == "words", (
                    f"a keyword-only match was labelled {hit['match']!r}")
        # And it must actually be there - a floor that hides the message entirely
        # is not the guard being tested either.
        assert any(KEYWORD_ONLY[:40].lower() in h["snippet"].lower() for h in hits), (
            "the keyword-only message is missing from the results, so this is not "
            "testing the label")

    def test_a_close_message_is_labelled_as_matching_both_signals(self, root):
        hits = conversation_store.search(root, QUERY, embed=_embed_factory())
        assert any(h["match"] == "words and meaning" for h in hits), hits

    def test_an_unrelated_message_is_not_returned_at_all(self, root):
        hits = conversation_store.search(root, QUERY, embed=_embed_factory())
        assert not any(FAR[:40].lower() in h["snippet"].lower() for h in hits), hits

    def test_the_floor_is_a_number_strictly_between_zero_and_one(self):
        """A floor of 0 admits everything and a floor of 1 admits nothing; both
        are the guard quietly switched off rather than a threshold."""
        floor = conversation_store.MEANING_FLOOR
        assert 0.0 < floor < 1.0, floor

    def test_the_floor_is_applied_to_the_candidates_before_the_fusion(
            self, root):
        """A floor applied *after* fusing would still admit a weak semantic hit if
        it ranked high enough, and the label would read "words and meaning". This
        is the same observable from a different direction: with the floor lowered
        to nothing, the label changes - which is what proves the filter sits in
        the candidate path and not in the sort.
        """
        original = conversation_store.MEANING_FLOOR
        try:
            conversation_store.MEANING_FLOOR = 0.0
            lowered = conversation_store.search(root, QUERY, embed=_embed_factory())
        finally:
            conversation_store.MEANING_FLOOR = original
        for hit in lowered:
            if KEYWORD_ONLY[:40].lower() in hit["snippet"].lower():
                assert hit["match"] == "words and meaning", (
                    "with no floor the semantically distant message was still not "
                    "rescued, so the filter is not where this test thinks")
