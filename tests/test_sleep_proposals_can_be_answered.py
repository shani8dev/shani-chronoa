"""Accepting or declining a fact the sleep pass proposed.

`consolidation.sleep_now()` reads a finished conversation and writes a digest
of `Candidate`s - sentences it noticed and **nobody has agreed to**. That is
the right boundary: a background pass must not decide what the assistant
believes about somebody. On its own it was also a dead end, because the daemon
wrote digests to `sleep/` and nothing anywhere read them, so every night's
work proposed facts to nobody.

These tests drive the real digest, the real `PerceptStore` and the real
consent path. The shape of the answers is the point: **"I agreed to it" and
"I agreed to it and it was stored" are different facts**, and a caller that
cannot tell them apart tells the person a memory exists when it does not.
"""

import json

import pytest

from shani_chronoa import consolidation
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import memory
from shani_chronoa.senses.store import PerceptStore

#: A conversation long enough for `consolidate` to work (MIN_MESSAGES is 6)
#: and carrying a sentence worth proposing.
CHAT = [
    {"role": "user", "content": "I moved to Pune in March."},
    {"role": "assistant", "content": "Noted."},
    {"role": "user", "content": "My manager is called Ramesh and he prefers email."},
    {"role": "assistant", "content": "Okay."},
    {"role": "user", "content": "The build server lives in the annex."},
    {"role": "assistant", "content": "Sure."},
]

FACT = "My manager is called Ramesh and he prefers email."


@pytest.fixture
def granted(tmp_path, monkeypatch):
    """Memory consent on, the store in a temp file, sleep digests in a temp dir."""
    store = PerceptStore(durable_path=tmp_path / "mem" / "memory.jsonl")
    monkeypatch.setattr(memory, "_STORE", store)
    ChronoaConfig().set("memory-sense-enabled", "true")
    return store


@pytest.fixture
def sleep_dir(tmp_path):
    return tmp_path / "sleep"


def _write(session_id="20260101-120000-abcd", text=FACT, created=None):
    """A digest holding one proposal, written through the real `save_digest`."""
    digest = consolidation.consolidate(session_id, CHAT)
    digest.candidates = [consolidation.Candidate(text, 2, "user")]
    if created is not None:
        digest.created = created
    return digest


def _stored(store, text):
    return [p for p in store.durable() if p.content == text]


# --- the decision itself ----------------------------------------------------

def test_agreeing_stores_the_fact(granted, sleep_dir):
    consolidation.save_digest(_write(), sleep_dir)
    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "kept" and reason == ""
    assert [p.content for p in _stored(granted, FACT)] == [FACT]


def test_agreeing_marks_it_in_the_digest_so_it_is_not_proposed_again(
        granted, sleep_dir):
    """The decision is durable, so a nightly pass does not ask again forever."""
    consolidation.save_digest(_write(), sleep_dir)
    memory.decide_candidate("20260101-120000-abcd", 0, True, granted, sleep_dir)

    again = consolidation.load_digest("20260101-120000-abcd", sleep_dir)
    assert again.candidates[0].accepted is True
    assert again.candidates[0].rejected is False
    assert memory.pending_proposals(sleep_dir) == []


def test_declining_records_the_answer_and_stores_nothing(granted, sleep_dir):
    consolidation.save_digest(_write(), sleep_dir)
    outcome, _ = memory.decide_candidate(
        "20260101-120000-abcd", 0, False, granted, sleep_dir)
    assert outcome == "declined"
    assert _stored(granted, FACT) == []

    again = consolidation.load_digest("20260101-120000-abcd", sleep_dir)
    assert again.candidates[0].rejected is True
    assert memory.pending_proposals(sleep_dir) == []


def test_the_stored_memory_says_where_it_came_from(granted, sleep_dir):
    """`sleep:<session>#<index>`, so "why does it think that" has an answer.

    Provenance is the **history file**, not the percept's own metadata -
    measured: a stored `Percept`'s `metadata` holds `key` and `keywords` and
    nothing else, so an assertion against `metadata["source"]` would be
    asserting a field the store does not have. The trace is the thing a person
    is shown when they ask where a memory came from.
    """
    consolidation.save_digest(_write(), sleep_dir)
    memory.decide_candidate("20260101-120000-abcd", 0, True, granted, sleep_dir)

    history = (sleep_dir.parent / "mem" / memory.HISTORY_FILE)
    assert history.exists(), "an ADD was recorded, so the audit file is there"
    rows = [json.loads(line) for line in
            history.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["source"] for row in rows] == ["sleep:20260101-120000-abcd#0"]


def test_the_covering_guard_compares_keys_the_way_they_are_stored(granted):
    """`_covered_by` could not fire on any capital, which is every sentence.

    `store_fact` lowercases the supersession key before writing it, so a
    plain `!=` against the caller's raw key made the guard answer "not
    covered" for every input. The shipped callers pass keys that are already
    lowercase by construction, so nothing was broken - the acceptor passes the
    sentence itself, and a sentence has capitals in it.

    The `Fact` here is exactly what `_fact_from_candidate` builds for a
    proposal, because the guard is asked about that key and not about an
    arbitrary one.
    """
    memory.remember_fact(FACT, store=granted)
    proposed = memory.Fact(FACT, FACT, FACT, "sleep:s1#0",
                           memory.CONFIDENCE_STATED)
    assert memory._covered_by(proposed, granted) is not None, \
        "a stored fact whose key carries capitals is the same fact"


def test_an_agreed_fact_is_believed_as_much_as_a_typed_one(granted, sleep_dir):
    """A person pressed Keep, so it is a *stated* fact - not an extracted one.

    Sleep read the sentence out of a conversation it was not present for, so
    *it* should not be believed. The person agreed to it, which is the same
    provenance a typed `remember_fact` carries.
    """
    consolidation.save_digest(_write(), sleep_dir)
    memory.decide_candidate("20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert _stored(granted, FACT)[0].confidence == memory.CONFIDENCE_STATED
    assert memory.CONFIDENCE_STATED > memory.CONFIDENCE_EXTRACTED


def test_a_second_answer_to_the_same_proposal_is_reported_not_re_done(
        granted, sleep_dir):
    consolidation.save_digest(_write(), sleep_dir)
    assert memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)[0] == "kept"

    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "already-decided"
    assert "accepted" in reason
    # One press, one memory - the retry must not double-write.
    assert len(_stored(granted, FACT)) == 1


def test_agreeing_to_what_is_already_remembered_does_not_duplicate(
        granted, sleep_dir):
    memory.remember_fact(FACT, store=granted)
    consolidation.save_digest(_write(), sleep_dir)

    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "kept" and "already remembered" in reason
    assert len(_stored(granted, FACT)) == 1


# --- the refusals, each with a different remedy ----------------------------

def test_a_non_boolean_answer_is_refused_rather_than_guessed(sleep_dir, granted):
    """`accept` arrives from a button, a command line and a model.

    Three callers that spell "no" as `False`, `"false"` and a non-clicked row.
    `bool(accept)` would read `"no"` as yes, so the ambiguous shape is refused
    rather than interpreted.
    """
    consolidation.save_digest(_write(), sleep_dir)
    for spelling in ("false", "no", 0, None, "True"):
        outcome, reason = memory.decide_candidate(
            "20260101-120000-abcd", 0, spelling, granted, sleep_dir)
        assert outcome == "refused", spelling
        assert "true or false" in reason
    # ...and nothing was decided on the way through.
    assert consolidation.load_digest(
        "20260101-120000-abcd", sleep_dir).candidates[0].accepted is False
    assert memory.pending_proposals(sleep_dir) != []


def test_a_proposal_that_is_not_there_is_refused_with_the_count(
        granted, sleep_dir):
    consolidation.save_digest(_write(), sleep_dir)
    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 7, True, granted, sleep_dir)
    assert outcome == "refused"
    assert "1 proposed" in reason


def test_an_index_that_is_not_a_number_is_refused_rather_than_guessed(
        granted, sleep_dir):
    """`"first"` must not become proposal 0.

    The out-of-range guard catches 7 and catches -1, and neither is what arrives
    when a caller means "the first one" and sends a word. Coercing an unreadable
    index to 0 decides about somebody else's sentence while answering as though
    it read the question - the same shape as a non-bool `accept` coerced with
    `bool()`, refused for the same reason: this argument comes from a button, a
    command line and a model, and all three spell "one" differently.
    """
    consolidation.save_digest(_write(), sleep_dir)
    for index in ("first", None, ["0"]):
        outcome, reason = memory.decide_candidate(
            "20260101-120000-abcd", index, True, granted, sleep_dir)
        assert outcome == "refused", (index, outcome)
        assert "not a position" in reason, (index, reason)
    # ...and none of them decided anything on the way through.
    assert [row.text for row in memory.pending_proposals(sleep_dir)] == [FACT]


def test_an_empty_proposal_is_kept_unrecorded_rather_than_kept(
        granted, sleep_dir):
    """`kept` and `kept-unrecorded` on the only path a caller can reach here.

    A proposal sleep noticed and then had nothing to say about it - punctuation,
    a fragment - reaches the acceptor with empty text, and there is no fact to
    store. Reporting that as `kept` is the false promise the whole outcome
    vocabulary exists to avoid, and it is what the empty-list case would look
    like if the check were dropped.
    """
    digest = _write(text="   ")
    consolidation.save_digest(digest, sleep_dir)
    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "kept-unrecorded", outcome
    assert "nothing to store" in reason, reason
    # The person still said yes, so it is not offered again tomorrow either.
    assert memory.pending_proposals(sleep_dir) == []


def test_a_digest_that_cannot_be_written_back_is_refused_on_a_decline_too(
        granted, sleep_dir, monkeypatch):
    """The write-back precedes the keep/decline split, so both go through it.

    Worth its own test because a fix that moves the save *after* the
    `if not accept: return` would leave every decline durable and every keep
    unreliable - which is the inversion this checks for.
    """
    consolidation.save_digest(_write(), sleep_dir)

    def _cannot_write(digest, directory=None):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(consolidation, "save_digest", _cannot_write)
    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, False, granted, sleep_dir)
    assert outcome == "refused", outcome
    assert "could not be recorded" in reason, reason


def test_the_covering_guard_needs_the_matching_key_and_not_only_the_text(
        granted, sleep_dir):
    """The over-fix of a guard that could not fire on any capital.

    `_covered_by` treats a stored fact as covering this one when the **key**
    matches and the text is identical. Dropping the key half - "same words,
    different subject" - makes every stored fact cover every proposal, so a
    person agrees to one sentence and is told it is already remembered because
    an unrelated one happens to read the same.

    The fixture is therefore the half that can tell the two apart: identical
    text, **a different key**. With the key comparison in place the guard
    declines to fire and the fact is written; with it dropped the guard fires
    and nothing is.
    """
    consolidation.save_digest(_write(), sleep_dir)
    other = memory.Fact(text=FACT, span=FACT, key="a different subject",
                        source="typed", confidence=memory.CONFIDENCE_STATED)
    assert memory.store_fact(other, granted) is not None

    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "kept", outcome
    assert "already remembered" not in reason, reason
    assert len(_stored(granted, FACT)) == 1


def test_a_session_with_no_proposals_says_so_by_name(granted, sleep_dir):
    outcome, reason = memory.decide_candidate(
        "20260101-130000-ffff", 0, True, granted, sleep_dir)
    assert outcome == "refused"
    assert "20260101-130000-ffff" in reason


def test_consent_is_the_acceptors_own_gate_too(granted, sleep_dir, monkeypatch):
    """The panel is not the only door, and neither door checks its own consent."""
    consolidation.save_digest(_write(), sleep_dir)
    ChronoaConfig().set("memory-sense-enabled", "false")

    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "refused" and "remember" in reason.lower()
    assert _stored(granted, FACT) == []


def test_an_accepted_answer_survives_a_write_it_could_not_perform(
        granted, sleep_dir, monkeypatch):
    """`kept` and `kept-unrecorded` are different facts about a person.

    The digest says they agreed; the store does not hold it. Reporting both
    as `kept` tells them a memory exists that does not - which is the shape of
    refusal this repository records repeatedly, and the reason the outcome is a
    word rather than a boolean.
    """
    consolidation.save_digest(_write(), sleep_dir)
    monkeypatch.setattr(memory, "store_fact", lambda *a, **k: None)

    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "kept-unrecorded"
    assert "nothing was stored" in reason
    # ...and it is still not proposed again: they did agree.
    assert memory.pending_proposals(sleep_dir) == []


def test_a_digest_that_cannot_be_written_back_is_refused_and_nothing_decided(
        granted, sleep_dir, monkeypatch):
    """Answering into a file that did not change would be a lie about an answer.

    The decision is un-applied in memory too, so the next reader sees a
    proposal rather than an answer nobody recorded.
    """
    consolidation.save_digest(_write(), sleep_dir)

    def _refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(consolidation, "save_digest", _refuse)
    outcome, reason = memory.decide_candidate(
        "20260101-120000-abcd", 0, True, granted, sleep_dir)
    assert outcome == "refused" and "could not be recorded" in reason

    monkeypatch.undo()
    again = consolidation.load_digest("20260101-120000-abcd", sleep_dir)
    assert again.candidates[0].accepted is False
    assert again.candidates[0].rejected is False


# --- the listing the two surfaces share ------------------------------------

def test_the_proposals_are_listed_with_the_context_they_were_drawn_from(
        granted, sleep_dir):
    consolidation.save_digest(_write(), sleep_dir)
    listed = memory.pending_proposals(sleep_dir)

    assert len(listed) == 1
    row = listed[0]
    assert (row.text, row.index) == (FACT, 0)
    assert row.source_index == 2 and row.source_role == "user"
    assert "Ramesh" in row.summary


def test_listing_creates_nothing(granted, tmp_path):
    """Asking what sleep has produced is a read, and a read makes no store."""
    absent = tmp_path / "never-used"
    assert memory.pending_proposals(absent) == []
    assert not absent.exists()


def test_the_newest_conversation_is_listed_first(granted, sleep_dir):
    """Ordered by when the conversation ended, not by when a file was written.

    Deciding a proposal writes the digest back, which makes it the newest file
    in the directory. Ordering on that would float a decided digest to the top
    of the list.
    """
    old = _write("20260101-120000-aaaa", "Older fact about the annex", 1000.0)
    new = _write("20260102-120000-bbbb", "Newer fact about the annex", 2000.0)
    consolidation.save_digest(old, sleep_dir)
    consolidation.save_digest(new, sleep_dir)

    assert [row.text for row in memory.pending_proposals(sleep_dir)] == \
        ["Newer fact about the annex", "Older fact about the annex"]

    # Deciding the *older* one rewrites it, so it is now the newest file.
    memory.decide_candidate("20260101-120000-aaaa", 0, True, granted, sleep_dir)
    assert [row.text for row in memory.pending_proposals(sleep_dir)] == \
        ["Newer fact about the annex"]


def test_only_undecided_proposals_are_listed(granted, sleep_dir):
    """Two digests, three proposals; one is already answered."""
    first = _write("20260101-120000-aaaa", "The annex holds the build server")
    first.candidates.append(
        consolidation.Candidate("And a spare key for it", 4, "user"))
    second = _write("20260102-120000-bbbb", "Ramesh prefers email")
    consolidation.save_digest(first, sleep_dir)
    consolidation.save_digest(second, sleep_dir)

    assert len(memory.pending_proposals(sleep_dir)) == 3

    memory.decide_candidate(
        "20260101-120000-aaaa", 0, False, granted, sleep_dir)
    listed = memory.pending_proposals(sleep_dir)
    assert [row.text for row in listed] == [
        "Ramesh prefers email", "And a spare key for it"]


def test_the_limit_bounds_the_listing_and_survives_a_nonsense_one(
        granted, sleep_dir):
    for index in range(5):
        consolidation.save_digest(
            _write(f"2026010{index + 1}-120000-a{index:03x}",
                   f"Fact number {index}"), sleep_dir)
    assert len(memory.pending_proposals(sleep_dir)) == 5
    assert len(memory.pending_proposals(sleep_dir, limit=2)) == 2
    # 0 and None are "no limit given", not "show nothing".
    assert len(memory.pending_proposals(sleep_dir, limit=0)) == 5
    assert len(memory.pending_proposals(sleep_dir, limit=None)) == 5


def test_an_unreadable_digest_does_not_hide_the_others(granted, sleep_dir):
    consolidation.save_digest(
        _write("20260101-120000-aaaa", "First fact"), sleep_dir)
    consolidation.save_digest(
        _write("20260102-120000-bbbb", "Second fact"), sleep_dir)
    (sleep_dir / "20260103-120000-cccc.json").write_text("{not json")

    listed = memory.pending_proposals(sleep_dir)
    assert {row.text for row in listed} == {"First fact", "Second fact"}


def test_a_file_naming_an_invalid_session_is_not_a_proposal(granted, sleep_dir):
    """The id is a filename, and `digest_path` refuses one that could escape.

    So a file whose stem is not a session id is not something this reader can
    address, and listing it would hand out a `session_id` no decision could
    ever act on.
    """
    consolidation.save_digest(_write(), sleep_dir)
    # **With a candidate in it.** The first version wrote `"candidates": []`, so
    # the file contributed nothing to the listing whether or not the filter
    # existed - the assertion passed with the guard removed and the guard was
    # never under test. A fixture that cannot produce the thing the code would
    # otherwise produce is the "check that cannot fail" shape, and this one was
    # in the test written to close a real one.
    (sleep_dir / "not-a-session-id.json").write_text(
        '{"session_id": "not-a-session-id", "summary": "x",'
        ' "candidates": [{"text": "Stray sentence", "source_index": 0,'
        ' "source_role": "user", "accepted": false, "rejected": false}],'
        ' "message_count": 6, "created": 1.0}')
    assert [row.text for row in memory.pending_proposals(sleep_dir)] == [FACT]
    # ...and the lister itself refuses to hand the name out.
    assert "not-a-session-id" not in consolidation.list_digests(sleep_dir)


# --- where the acceptor is allowed to live ---------------------------------

def test_the_acceptor_is_not_in_the_module_that_must_only_propose():
    """`consolidation.py` may propose; it may not remember.

    `TestSleepOnlyProposes::test_it_never_touches_the_memory_store` greps that
    module's source, so an acceptor placed there would either fail it or
    satisfy it through a helper the grep cannot see - which is the laundering
    the test exists to stop. It lives here instead.
    """
    from tests._source import package_source

    source = package_source("consolidation")
    assert "decide_candidate" not in source
    assert "def list_digests" in source