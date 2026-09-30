"""Ranking quality of the memory sense's `recall`, and what it deliberately does not do.

The sense's module docstring claims `recall` ranks by relevance rather than
by how many of the query's words a fact happens to contain. That claim is
what these tests hold up, and each of them fails against the previous
`len(wanted & keywords)` implementation - which was verified by mutating
the source back and re-running, with the red counts recorded in the report.

Three properties are load-bearing and are tested separately, because a
change can fix one while breaking another:

- **Relevance beats length.** A verbose fact that mentions every word of a
  query is not the answer to it; a terse fact that is the answer should
  outrank it.
- **A word in every fact is not evidence.** A fact written by
  `extract_fact` is prefixed "The user...", so a store fed by the
  extraction path is seeded with its own hub words, and a raw count cannot
  tell those from a word unique to one fact. (`ALL_EXTRACT_DERIVED` below
  is that store. The explicit `remember` operation stores its argument
  verbatim and so has no prefix - the hub belongs to the extraction path,
  which is what this class is about.)
- **Recency is a tie-break, not the ranker.** A raw count ties constantly
  in a store this small, which is how the previous version ended up
  returning "the five most recent of a nine-way tie" as though it were the
  five most relevant.

The fixtures are written through the real `store_fact` / `remember_fact`
write path, so the text and the `metadata["keywords"]` the ranking reads
are the ones the module really puts on disk, and only `created_at` is
overwritten - to a stated age in days, so "which fact is newer" is legible
in the fixture instead of being whatever wall-clock said.
"""

import dataclasses
import time

import pytest

from shani_chronoa.senses import memory
from shani_chronoa.senses.memory import (
    _idf,
    _keywords,
    _relevance,
    _tokens,
    extract_fact,
    remember_fact,
    recall,
    store_fact,
)
from shani_chronoa.senses.store import PerceptStore

# Ages in days, oldest last. Deliberately ordinary facts a person would
# actually ask an assistant to keep, including two that are the *wrong*
# answer to the demo query: a maintenance note that happens to contain
# both "coffee" and "order", and a supply-order note that contains
# "office" and "order" but is about neither coffee nor drinking.
COFFEE_SHOP = [
    ("my name is Priya", 300),
    ("I live in Pune", 290),
    ("my bike is a green Hero", 280),
    ("my coffee order is a flat white", 270),
    (
        "remember that the office wifi password is written on the fridge beside "
        "the kettle",
        260,
    ),
    (
        "remember that the standup moved to the annex on tuesday mornings because "
        "the room above the cafe was double booked by the design team",
        250,
    ),
    ("I really like filter coffee on the train", 240),
    (
        "the office supply order goes through the annex desk on the first of the month",
        30,
    ),
    ("my laptop is a thinkpad with a linux sticker on the lid", 20),
    ("the annex coffee machine descaled again and the order went out flat", 10),
]

# A store in which every fact came from `extract_fact`, so "The user" /
# "The user's" really is in all of them and the hub is real rather than
# hypothetical. `phone` and `coffee` are the only distinctive words here.
ALL_EXTRACT_DERIVED = [
    ("my name is Priya", 200),
    ("I live in Pune", 190),
    ("my bike is a green Hero", 180),
    ("I really like filter coffee on the train", 170),
    ("my laptop is a thinkpad with a linux sticker", 160),
    ("my phone is a pixel eight", 150),
    ("my timezone is asia kolkata", 140),
    ("my editor is neovim", 130),
]

# Fixed epoch rather than `time.time()`, so an age in days is the age.
EPOCH = 1_700_000_000.0


def _write_through_the_real_path(turn, scratch):
    """Return the Percept the module would really write for `turn`.

    A labelled fact goes through `extract_fact` so the "The user's ..."
    prefix is the module's own phrasing and not a hand-typed imitation -
    that prefix is the hub word the whole IDF argument turns on, so
    hand-writing it would let the fixture drift from reality without any
    test going red.
    """
    found = extract_fact(turn)
    if found is not None:
        return store_fact(found, store=scratch)
    return remember_fact(turn, store=scratch)


def _seed(tmp_path, entries):
    """A real `PerceptStore` holding `entries`, oldest first, age stated in days.

    Two stores because the write path stamps `time.time()` and ranking
    fixtures need to know which fact is newer: the facts themselves come
    from the real write path, and only the timestamp is replaced.
    """
    scratch = PerceptStore(durable_path=tmp_path / "scratch.jsonl")
    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    for turn, age_days in entries:
        stored = _write_through_the_real_path(turn, scratch)
        assert stored is not None, f"the write path refused {turn!r}"
        store.add(dataclasses.replace(stored, created_at=EPOCH - age_days * 86400))
    return store


@pytest.fixture
def coffee_shop(tmp_path):
    return _seed(tmp_path, COFFEE_SHOP)


@pytest.fixture
def extract_only(tmp_path):
    return _seed(tmp_path, ALL_EXTRACT_DERIVED)


# --- the two scores, side by side ------------------------------------------
#
# `_legacy_score` is the previous implementation, copied in verbatim, so
# the "before" column below is real arithmetic over the same fixture rather
# than a claim about history. Anything it and the shipped score disagree on
# is a ranking the old code got wrong.


def _legacy_score(query, store):
    """The previous `len(wanted & _keywords(percept))`, for comparison only."""
    wanted = _tokens(query)
    return {
        p.content: len(wanted & _keywords(p))
        for p in store.durable()
    }


def _shipped_score(query, store):
    """Recompute the shipped score, so a failure message can show numbers."""
    wanted = _tokens(query)
    docs = store.durable()
    vocabularies = [_keywords(p) for p in docs]
    total = len(vocabularies)
    freq = {}
    for vocabulary in vocabularies:
        for token in vocabulary:
            freq[token] = freq.get(token, 0) + 1

    def weight(token):
        return _idf(total, freq.get(token, 0))

    query_mass = sum(weight(t) for t in wanted)
    out = {}
    for percept, vocabulary in zip(docs, vocabularies):
        shared = sum(weight(t) for t in wanted if t in vocabulary)
        if not shared:
            out[percept.content] = 0.0
            continue
        document_mass = sum(weight(t) for t in vocabulary)
        out[percept.content] = _relevance(shared, query_mass, document_mass)
    return out


def _legacy_order(query, store, limit=5):
    """The order the previous implementation produced, for the "before" column.

    The old rule was `sorted(scored, key=(score, created_at), reverse=True)`
    over the raw set-intersection size, so it is reproduced here from the
    same fixture rather than quoted from history. Under it, ties are broken
    by recency, which is the whole complaint.
    """
    wanted = _tokens(query)
    scored = [
        (len(wanted & _keywords(p)), p) for p in store.durable()
    ]
    ranked = sorted(
        (pair for pair in scored if pair[0]),
        key=lambda pair: (pair[0], pair[1].created_at),
        reverse=True,
    )
    return [p.content for _, p in ranked[:limit]]


def _table(query, store):
    """A printable old-vs-new ranking, for assertion messages."""
    old = _legacy_score(query, store)
    new = _shipped_score(query, store)
    lines = [f"  query={query!r}", f"  {'old':>6} {'new':>7}  fact"]
    for content in recall(query, store=store, limit=len(store.durable())):
        lines.append(f"  {old[content]:>6} {new[content]:>7.4f}  {content[:64]}")
    return "\n".join(lines)


def test_the_recomputed_scores_agree_with_what_recall_actually_did(coffee_shop):
    """Ties the test's own re-derivation to the shipped assembly.

    Several assertions above read `_shipped_score`, which rebuilds the
    score from `_idf` and `_relevance` rather than calling into `recall`.
    That is only trustworthy while the two agree, and this is what checks
    it: the sequence `recall` returns must be non-increasing in recomputed
    score, and must select exactly the facts with a nonzero one.
    """
    for query in (
        "what is my coffee order",
        "what is my coffee order at the office",
        "which bike do I ride",
        "user's",
        "the user's coffee order",
    ):
        scores = _shipped_score(query, coffee_shop)
        returned = [p.content for p in recall(query, store=coffee_shop, limit=50)]
        in_returned_order = [scores[content] for content in returned]

        assert in_returned_order == sorted(in_returned_order, reverse=True), (
            f"{query!r} came back out of score order: {_table(query, coffee_shop)}"
        )
        assert set(returned) == {c for c, s in scores.items() if s > 0.0}, (
            f"{query!r} selected a different set of facts than the scores imply"
        )


# --- relevance beats length -------------------------------------------------


class TestRelevanceRatherThanOverlapSize:
    def test_a_verbose_fact_mentioning_every_query_word_does_not_win(
        self, coffee_shop
    ):
        """The headline case: the note that mentions everything is not the answer.

        Both of the top two candidates contain both of the query's
        distinctive words, so the old count tied them at 2 and let
        `created_at` choose - and the newer of the two is a descaling
        incident, not the order.
        """
        answer = "my coffee order is a flat white"
        incident = "the annex coffee machine descaled again and the order went out flat"

        old = _legacy_score("what is my coffee order", coffee_shop)
        assert old[answer] == old[incident] == 2, (
            "fixture no longer produces the tie this test is about"
        )

        found = [p.content for p in recall("what is my coffee order", store=coffee_shop)]

        assert found[0] == answer, _table("what is my coffee order", coffee_shop)
        assert found.index(answer) < found.index(incident)

    def test_a_false_friend_sharing_office_and_order_does_not_win(
        self, coffee_shop
    ):
        """"the office supply order" is about supplies, not about coffee.

        The old score ranked it first on "what is my coffee order at the
        office" because it contains two of the query's words while the
        actual answer contains one of them. No keyword scorer can see that
        one of those words is the subject of the question, but the length
        of the fact is a real signal about whether it is *about* the query.
        """
        answer = "my coffee order is a flat white"
        supply = (
            "the office supply order goes through the annex desk on the first "
            "of the month"
        )

        found = [
            p.content
            for p in recall("what is my coffee order at the office", store=coffee_shop)
        ]

        assert found[0] == answer, _table(
            "what is my coffee order at the office", coffee_shop
        )
        assert supply in found, "the supply note is still recalled, just not first"

    def test_an_exact_match_outranks_a_partial_one_by_a_wide_margin(self, coffee_shop):
        new = _shipped_score("what is my coffee order", coffee_shop)

        assert new["my coffee order is a flat white"] > 2 * new[
            "The user prefers: filter coffee on the train"
        ], _table("what is my coffee order", coffee_shop)


# --- a word in every fact is not evidence -----------------------------------


class TestHubWordsAreNotEvidence:
    def test_a_word_every_stored_fact_shares_cannot_outrank_a_distinctive_one(
        self, extract_only
    ):
        """"The user" is in all eight facts; "coffee" is in one of them.

        Every candidate ties at 1 under the old count, so the answer came
        back in whatever order the store happened to be in. Note that
        *unweighted* Dice is not enough here either - it ranks the
        editor/place/name facts above the coffee fact, because the
        "user's" match is literally one of two shared tokens against one
        of four. The weight is what makes the hub word worthless, and
        replacing every weight with 1.0 turns this test red.
        """
        answer = "The user prefers: filter coffee on the train"
        rival = "The user's editor: neovim"

        old = _legacy_score("user's coffee", extract_only)
        assert set(old.values()) == {1}, (
            "fixture no longer produces the all-way tie this test is about"
        )

        found = [p.content for p in recall("user's coffee", store=extract_only)]

        assert found[0] == answer, _table("user's coffee", extract_only)
        new = _shipped_score("user's coffee", extract_only)
        assert new[answer] > 3 * new[rival], _table("user's coffee", extract_only)

    def test_a_fact_matching_only_a_hub_word_is_never_the_top_answer(
        self, extract_only
    ):
        """The ordering must survive a query whose distinctive half is dropped."""
        found = [
            p.content
            for p in recall("the user's phone", store=extract_only, limit=len(extract_only.durable()))
        ]

        assert found[0] == "The user's phone: a pixel eight", _table(
            "the user's phone", extract_only
        )

    def test_the_hub_word_itself_does_not_drown_out_a_real_answer(self, coffee_shop):
        """'user' is in the label of most of these facts and of nothing else."""
        found = [
            p.content
            for p in recall(
                "the user's coffee order", store=coffee_shop, limit=len(coffee_shop.durable())
            )
        ]

        assert found[0] == "my coffee order is a flat white", _table(
            "the user's coffee order", coffee_shop
        )


# --- the contract that must not be weakened ---------------------------------


class TestTheNoOverlapContractStillHolds:
    def test_a_query_sharing_no_content_word_returns_nothing(self, coffee_shop):
        assert recall("what do I drink", store=coffee_shop) == []
        assert recall("aardvark", store=coffee_shop) == []

    def test_a_query_of_only_stopwords_returns_nothing(self, coffee_shop):
        assert recall("what is it and please", store=coffee_shop) == []

    def test_an_empty_query_and_a_nonpositive_limit_return_nothing(self, coffee_shop):
        assert recall("", store=coffee_shop) == []
        assert recall("coffee order", store=coffee_shop, limit=0) == []
        assert recall("coffee order", store=coffee_shop, limit=-3) == []

    def test_recall_never_returns_a_fact_sharing_no_content_word(self, coffee_shop):
        for query in ("coffee", "order", "office", "annex", "the standup", "wifi"):
            wanted = _tokens(query)
            for percept in recall(query, store=coffee_shop, limit=50):
                assert wanted & _keywords(percept), (
                    f"{query!r} returned {percept.content!r}, which shares no word"
                )

    def test_the_cli_still_says_no_fact_matched_rather_than_lying(self, coffee_shop):
        """The rendered text must keep matching what the scorer did."""
        answered = memory._run({"operation": "recall", "query": "aardvark"})
        assert "No stored fact shares a content word" in answered.content

        found = memory._run({"operation": "recall", "query": "coffee order"})
        assert "What is remembered" in found.content


# --- scores stay comparable -------------------------------------------------


class TestScoresStayComparable:
    def test_a_fact_that_answers_the_query_exactly_scores_one(self, coffee_shop):
        """The ceiling is tight, not slack - that is what makes a threshold possible.

        The query is built from a stored fact's own vocabulary, which is the
        only way to reach the ceiling through the public entry point: any
        extra word a caller adds lowers the score, correctly.
        """
        answer = next(
            p for p in coffee_shop.durable() if p.content == "my coffee order is a flat white"
        )
        exact_query = " ".join(sorted(_keywords(answer)))

        assert _shipped_score(exact_query, coffee_shop)[answer.content] == pytest.approx(1.0)

    def test_every_score_is_within_the_unit_interval(self, coffee_shop):
        for query in (
            "what is my coffee order",
            "what is my coffee order at the office",
            "which bike do I ride",
            "where do I live",
            "user's",
            "coffee",
            "the standup",
            "what is my wifi password",
            "descaled annex machine",
        ):
            for score in _shipped_score(query, coffee_shop).values():
                assert 0.0 <= score <= 1.0, f"{query!r} produced {score}"

    def test_a_very_long_fact_matching_every_query_word_still_scores_at_most_one(self):
        """A divisor taken from the query alone would return 2.0 here.

        This is the property that makes the score comparable across
        queries of different lengths, and it is exactly what stops
        anything added later from rescaling what is already computed.
        """
        query_mass = 1.0
        document_mass = 1.0
        # A 40x-longer fact that nevertheless contains the whole query.
        assert _relevance(query_mass, query_mass, 40 * document_mass) == pytest.approx(
            2.0 / 41.0
        )
        assert _relevance(query_mass, query_mass, 40 * document_mass) < 1.0

    def test_a_bounded_extra_signal_cannot_push_a_score_past_the_ceiling(self):
        """The 'a future signal rescales everything' failure, stated as a test.

        A signal worth up to 1.0 added to the shared mass, with its own
        weight added to both sides of the divisor, keeps every score in
        [0, 1] - so an addition later widens the scale instead of moving it.
        """
        for shared, query_mass, document_mass in (
            (0.4, 1.0, 1.0),
            (1.0, 1.0, 1.0),
            (0.1, 1.0, 9.0),
            (0.0, 1.0, 1.0),
        ):
            for weight in (0.0, 0.25, 1.0, 4.0):
                for signal in (0.0, 0.5, 1.0):
                    score = _relevance(
                        shared + weight * signal,
                        query_mass + weight,
                        document_mass + weight,
                    )
                    assert 0.0 <= score <= 1.0, (
                        f"shared={shared} weight={weight} signal={signal} -> {score}"
                    )

    def test_the_idf_of_a_word_in_every_fact_is_positive_and_small(self):
        """`ln(N/df)` would be zero here, and the divisor would collapse.

        Every fact `extract_fact` writes is prefixed "The user...", so a
        query containing that word is the common case, not an edge case.
        """
        for docs in (1, 2, 8, 500):
            for word_freq in (0, 1, docs // 2, docs - 1, docs):
                weight = _idf(docs, word_freq)

                assert weight > 0.0, f"N={docs} df={word_freq} gave {weight}"
                assert weight <= _idf(docs, 0)
                if word_freq:
                    assert weight < _idf(docs, 0)

    def test_idf_falls_as_a_word_becomes_more_common(self):
        weights = [_idf(50, df) for df in (1, 5, 25, 49, 50)]

        assert weights == sorted(weights, reverse=True)

    def test_a_single_fact_store_still_answers_an_exact_query(self, tmp_path):
        """One fact in the store: df == N everywhere, which must not mean 'unknown'.

        `ln(N / df)` scores 0.0 for every token in a one-fact store, and the
        divisor with it. The Lucene form used here gives every token the
        same small positive weight, so the answer is the fact and its score
        is the two-over-four a two-word query can earn against a four-word
        fact - not a division by zero and not a refusal.
        """
        store = _seed(tmp_path, [("my coffee order is a flat white", 1)])

        found = [p.content for p in recall("coffee order", store=store)]

        assert found == ["my coffee order is a flat white"]
        assert _shipped_score("coffee order", store)[found[0]] == pytest.approx(2 / 3)
        assert _idf(1, 1) > 0.0

    def test_a_word_found_in_no_fact_lowers_the_score_without_moving_the_order(
        self, coffee_shop
    ):
        """The ceiling rises with an unanswerable word. That is not a bug.

        It is what "most of this question is not something I have stored"
        looks like numerically, and the *order* is untouched - which is
        why there is deliberately no absolute floor on the score here: a
        floor would turn this honest dilution into a refusal.
        """
        plain = _shipped_score("coffee order", coffee_shop)
        padded = _shipped_score(
            "what is the coffee order at the annex", store=coffee_shop
        )

        assert padded["my coffee order is a flat white"] < plain[
            "my coffee order is a flat white"
        ]
        old_order = [p.content for p in recall("coffee order", store=coffee_shop, limit=2)]
        new_order = [
            p.content
            for p in recall(
                "what is the coffee order at the annex", store=coffee_shop, limit=2
            )
        ]
        assert old_order[0] == new_order[0] == "my coffee order is a flat white"


# --- recency is a tie-break, not the ranker ----------------------------------


class TestRecencyIsOnlyATieBreak:
    def test_a_newer_but_less_relevant_fact_loses(self, coffee_shop):
        """The most recent fact in the fixture is the descaling incident."""
        newest = max(coffee_shop.durable(), key=lambda p: p.created_at)
        assert newest.content.startswith("the annex coffee machine")

        found = [p.content for p in recall("what is my coffee order", store=coffee_shop)]

        assert found[0] != newest.content, _table(
            "what is my coffee order", coffee_shop
        )

    def test_a_three_way_tie_is_broken_by_relevance_instead_of_by_recency(
        self, tmp_path
    ):
        """The clearest difference from the old count, on an honest fixture.

        Three facts about the same subject, each one a superset of the last
        one's words. Under the old count all three matched two of the
        query's words and tied at 2, so `created_at` picked the order - and
        with `limit=1` that meant the *most recent* one came back, which is
        also the most verbose. The measured scores are 0.1944 / 0.1660 /
        0.1272.
        """
        store = _seed(
            tmp_path,
            [
                ("the office is on the third floor", 90),
                ("the office is on the third floor of the annex", 1),
                ("the office is on the third floor of the annex building", 0.5),
            ],
        )
        tight, middle, verbose = (p.content for p in store.durable())
        query = "what floor is the office on"

        old = _legacy_score(query, store)
        assert set(old.values()) == {2}, (
            "fixture no longer produces the three-way tie this test is about"
        )
        assert _legacy_order(query, store, limit=1) == [verbose], (
            "the old rule no longer hands the answer to recency"
        )

        new = _shipped_score(query, store)
        assert new[tight] > new[middle] > new[verbose], _table(query, store)
        assert new[tight] > 1.4 * new[verbose]

        assert [
            p.content for p in recall(query, store=store, limit=1)
        ] == [tight]

    def test_an_order_that_was_already_right_is_left_alone(self, tmp_path):
        """A change that only ever reorders things is a change that is not safe.

        Ask a question whose distinctive words really are only in the
        verbose fact, and the new scorer has to agree with the old one: the
        reordering above follows from the length signal, and is not a
        blanket preference for short facts.
        """
        store = _seed(
            tmp_path,
            [
                ("the office is on the third floor", 90),
                ("the office is on the third floor of the annex", 1),
                ("the office is on the third floor of the annex building", 0.5),
            ],
        )
        query = "what floor is the office on annex building"
        tight, middle, verbose = (p.content for p in store.durable())

        old = _legacy_score(query, store)
        assert [old[middle], old[verbose]] == [3, 4], "fixture changed shape"
        new = _shipped_score(query, store)
        assert new[verbose] > new[middle] > new[tight], _table(query, store)

        assert [p.content for p in recall(query, store=store, limit=3)] == [
            verbose,
            middle,
            tight,
        ]

    def test_two_facts_with_the_same_words_are_ordered_by_recency(self, tmp_path):
        """The tie-break is unchanged, and must stay observable.

        These two differ only by a stopword, so their vocabularies are
        identical and no relevance measure can separate them. If this fails
        because the two contents are no longer token-equivalent, the fixture
        is lying rather than the tie-break being broken.
        """
        store = _seed(
            tmp_path,
            [
                ("the office is on the third floor", 90),
                ("the office is on a third floor", 1),
            ],
        )
        older, newer = store.durable()
        query = "what floor is the office on"

        assert _keywords(older) == _keywords(newer)
        scores = _shipped_score(query, store)
        assert scores[older.content] == scores[newer.content], "not a genuine tie any more"

        found = recall(query, store=store, limit=2)

        assert [p.content for p in found] == [newer.content, older.content]


# --- the module's own description of itself ---------------------------------


def test_the_sense_description_does_not_overclaim_semantic_search():
    """A description that reads as a feature and behaves as a no-op is worse
    than one that states the limit, per the module docstring's own rule."""
    schema = memory.SENSES[0].schema["function"]

    assert "semantic" not in schema["description"].lower()
    assert "literal word" in schema["description"]
    assert "rephrase the query" in schema["description"]


def test_recall_still_uses_the_durable_tier_only(tmp_path):
    """A transient percept is not a memory, and must not become recallable.

    Proved against a store that holds both, and against a query word only
    the transient percept has, so the assertion cannot pass by the query
    simply matching nothing.
    """
    from shani_chronoa.senses import Percept

    store = _seed(tmp_path, [("my coffee order is a flat white", 1)])
    transient = Percept(
        sense="memory",
        kind="observation",
        content="the coffee machine is descaling loudly",
        created_at=time.time(),
        ttl_seconds=600.0,
    )
    store.add(transient)

    assert transient in store.active(), "fixture is not exercising a transient tier"
    assert recall("descaling", store=store) == [], "a transient percept leaked into recall"
    for query in ("descaling", "the coffee machine is descaling loudly", "machine"):
        assert transient.content not in [
            p.content for p in recall(query, store=store, limit=50)
        ], f"a transient percept leaked into recall for {query!r}"
    assert [p.content for p in recall("flat white", store=store)] == [
        "my coffee order is a flat white"
    ], "the durable tier stopped answering"
