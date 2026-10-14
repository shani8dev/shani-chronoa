"""The `review_proposals` skill, driven through the real dispatch path.

The acceptor has **two** consent surfaces and this file holds the one a person
reaches by asking: the skill. The panel is `gui/surfaces/memory.py`. Both call
`senses.memory.decide_candidate`, and the test that matters is at the bottom -
that the skill does not carry a second implementation of "did the person agree
to this?", because two implementations of one decision that writes to durable
memory is how they drift.

Every call here is `tools.execute_tool_outcome`, not the private `_run`: a skill
is a **subprocess**, so a handler that works in-process can still be
unreachable. That distinction is this repository's most repeated finding.
"""

import json
import os
from pathlib import Path

import pytest

from shani_chronoa import consolidation, tools
from shani_chronoa.config import ChronoaConfig

CHAT = [
    {"role": "user", "content": "I moved to Pune in March."},
    {"role": "assistant", "content": "Noted."},
    {"role": "user", "content": "The build server lives in the annex."},
    {"role": "assistant", "content": "Okay."},
    {"role": "user", "content": "I keep a spare key under the mat."},
    {"role": "assistant", "content": "Sure."},
]

SESSION = "20260101-120000-abcd"
FACT = "I keep a spare key under the mat"


def _digest(session_id=SESSION, text=FACT):
    digest = consolidation.consolidate(session_id, CHAT)
    digest.candidates = [consolidation.Candidate(text, 4, "user")]
    return digest


@pytest.fixture
def proposed(monkeypatch, tmp_path):
    """One real proposal in the real sleep store, and the sense granted."""
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    ChronoaConfig().set("memory-sense-enabled", "true")
    from shani_chronoa import consolidation as store

    digest = _digest()
    digest_dir = store.store_dir()
    consolidation.save_digest(digest, digest_dir)
    monkeypatch.setattr(store, "store_dir", lambda: digest_dir)
    yield digest
    for path in digest_dir.glob("*.json"):
        path.unlink()


def call(**arguments):
    outcome = tools.execute_tool_outcome("review_proposals", arguments)
    text = getattr(outcome, "text", None) or str(outcome)
    assert "Traceback" not in text, text
    return outcome, text


def test_the_skill_is_reachable_and_lists_what_sleep_noticed(proposed):
    _, text = call()
    assert "1 proposed fact" in text
    assert FACT in text
    # Nothing is remembered by listing it.
    assert "none of them remembered" in text


def test_the_listing_says_where_each_sentence_came_from(proposed):
    """Three things a person cannot choose a number without.

    Which turn it was said in (zero-based in `consolidate`, so +1 here and the
    test states it), which conversation, and that nothing is stored yet. Each
    is one clause in `_listing`, and each is the only place that fact exists
    for this surface - the model cannot ask for it, because the listing is the
    only thing the model ever reads.
    """
    _, listing = call()

    # `Candidate(text, 4, "user")` is the fixture's source_index, and the
    # listing is the only place the +1 happens for this surface - so the number
    # is derived from the fixture rather than typed beside it.
    assert f"turn {4 + 1}" in listing, listing
    assert SESSION in listing, listing
    assert "none of them remembered" in listing, listing
    assert "Nothing is stored until you say so" in listing, listing


def test_the_listing_says_how_many_are_waiting(proposed):
    """Two, so the count is a count and not a constant.

    One proposal was the first version's fixture, and with one row a header
    reading "" and one reading the number are the same shape on screen.
    """
    consolidation.save_digest(
        _digest("20260102-120000-bbcd", "A second thing worth keeping"),
        consolidation.store_dir())

    _, listing = call()
    assert "2 proposed fact" in listing, listing


def test_keeping_stores_the_fact_and_says_so(proposed):
    _, text = call(action="keep", number=1)
    assert text.startswith(f"Remembered: {FACT}")

    # **Read where the child wrote, not where the parent thinks the store is.**
    # `conftest`'s `isolated` fixture patches `senses.store.PERCEPT_DIR` in this
    # process and points `XDG_DATA_HOME` somewhere else; the sandboxed child
    # inherits the variable and recomputes the path from it at import, so the
    # two disagree - the child lands in `xdg-data/shani-chronoa/percepts/` and
    # the parent would look in `xdg/state/`. Measured by the file simply not
    # existing where the parent says it should.
    #
    # **On a real machine there is one data home and the two agree**, so this is
    # a harness split rather than a product one. It is recorded rather than
    # worked around silently because it is the same shape as the settings- and
    # PATH-fixture findings this repository keeps meeting: the parent and the
    # child must be made to look at the same machine before a dispatch tells you
    # anything.
    child_store = (Path(os.environ["XDG_DATA_HOME"]) / "shani-chronoa"
                   / "percepts" / "memory.jsonl")
    assert child_store.exists(), f"the child wrote nothing; looked in {child_store}"
    stored = [json.loads(line) for line in
              child_store.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["content"] for row in stored] == [FACT]


def test_the_answer_records_the_decision_where_the_parent_can_read_it(proposed):
    """The decision itself is in the digest, which both processes *do* share.

    So the half of the record that decides whether the proposal is offered again
    is readable in-process even though the stored fact is not - which is why a
    refused write is still `kept-unrecorded` rather than silently lost.
    """
    call(action="keep", number=1)
    digest = consolidation.load_digest(SESSION, consolidation.store_dir())
    assert digest.candidates[0].accepted is True


def test_discarding_removes_it_and_does_not_remove_anything_else(proposed):
    _, text = call(action="discard", number=1)
    assert text.startswith("Dropped:") and "not be offered again" in text
    _, again = call()
    assert "Nothing is waiting" in again


def test_a_proposal_nothing_can_be_stored_is_not_reported_as_remembered(
        proposed):
    """`kept` and `kept-unrecorded`, on a path the dispatcher really reaches.

    Sleep can notice a fragment - punctuation, a half-sentence - and the
    acceptor reports that as `kept-unrecorded`. **The skill's return string is
    the only channel a subprocess has**, so collapsing the two here is the whole
    of the difference as far as the model can see, and the model's next move
    depends on it.

    Built through `execute_tool_outcome` rather than by patching `store_fact`,
    because the child process cannot see a monkeypatch - which is also why the
    empty proposal is the route here and not a stub.
    """
    consolidation.save_digest(_digest(text="   "), consolidation.store_dir())

    _, text = call(action="keep", number=1)
    assert "not in memory" in text, text
    assert not text.startswith("Remembered:"), text
    # ...and the reason says why, because "not in memory" alone is a verdict
    # with no way to act on it.
    assert "nothing to store" in text, text


def test_the_number_is_the_one_the_list_showed(proposed):
    """Proposal 1 of the listing is proposal 1 to keep, whichever that is.

    Measured, and it is **not** the older conversation: `pending_proposals`
    orders newest-first, so a digest written a moment ago is "1.". Which is why
    the test resolves the number against the listing it printed rather than
    against a digest it built - a fixture that assumes an order the product does
    not use cannot tell this skill from one that keeps the wrong row.
    """
    consolidation.save_digest(
        _digest("20260102-120000-bbcd", "A second thing worth keeping"),
        consolidation.store_dir())

    _, listing = call()
    numbered = {}
    for line in listing.splitlines():
        line = line.strip()
        for number in (1, 2):
            if line.startswith(f"{number}. "):
                numbered[number] = line[3:]
    assert len(numbered) == 2, listing

    _, text = call(action="keep", number=2)
    assert text.startswith(f"Remembered: {numbered[2]}")
    assert numbered[1] not in text


def test_a_bad_enum_or_type_is_refused_before_the_skill_runs(proposed):
    """`guardrail` checks the schema ahead of dispatch, so the handler is spared.

    **This is the actual protection on the real path**, which is why the skill's
    own `_ANSWER` table is not the guard - see the next test, which reaches the
    handler directly.
    """
    _, bad_action = call(action="delete", number=1)
    assert "Not run" in bad_action
    assert "list, keep, discard" in bad_action

    _, bad_type = call(action="keep", number="first")
    assert "Not run" in bad_type
    assert "integer" in bad_type


def test_the_handler_also_refuses_a_bad_argument_when_reached_directly(proposed):
    """Defence in depth, and the reason it is named rather than removed.

    `guardrail` refuses an `action` outside the enum and a `number` that is not
    an integer **before the handler is entered**, so both branches below are
    unreachable through the dispatch path. They are kept because this handler is
    also the thing a future caller imports, and a guard that exists only above
    the call it guards disappears with the caller.
    """
    from shani_chronoa.skills import review_proposals

    assert "not one of list, keep or discard" in \
        review_proposals._run({"action": "delete", "number": 1})
    assert "is not a number" in \
        review_proposals._run({"action": "keep", "number": "first"})
    # ...and neither one changed anything.
    _, again = call()
    assert "1 proposed fact" in again


def test_an_outcome_this_module_does_not_know_is_explained_not_read_as_success(
        proposed, monkeypatch):
    """The fallback `return reason` is the whole of a growing vocabulary's safety.

    `decide_candidate` answers with one of five outcomes today. A sixth is a
    change to the acceptor, and the skill would then have a branch for five of
    them - so the last line reached is what keeps a **new** outcome from being
    reported to the model as a success the first time it exists. Answering
    "Remembered" there is a false promise introduced by nobody editing this file.

    Driven through `_run` with the acceptor stubbed, because a subprocess
    cannot be handed one: the child reads the real sense, which cannot be made
    to return an outcome it has never returned.
    """
    from shani_chronoa.senses import memory
    from shani_chronoa.skills import review_proposals

    def _half_agreed(session_id, index, accept, *args, **kwargs):
        return "partly-agreed", "the store accepted it and the digest did not"

    monkeypatch.setattr(memory, "decide_candidate", _half_agreed)
    said = review_proposals._run({"action": "keep", "number": 1})

    assert said == "the store accepted it and the digest did not", said
    assert "Remembered" not in said, said
    assert "Dropped" not in said, said


def test_a_sleep_pass_that_cannot_be_read_is_a_sentence_not_a_traceback(
        proposed, monkeypatch):
    """An ImportError from the sense must not reach the model as a stack.

    A traceback to a model is an unhandled failure it will retry, or route
    around - and the question it was asked ("what did you notice?") has a
    perfectly good honest answer, which is that the thing which notices is not
    there.

    `try` around the listing, driven directly because the subprocess cannot be
    given a raising `pending_proposals`.
    """
    from shani_chronoa.senses import memory
    from shani_chronoa.skills import review_proposals

    def _cannot_read(*args, **kwargs):
        raise OSError("the sleep store is not a directory")

    monkeypatch.setattr(memory, "pending_proposals", _cannot_read)
    said = review_proposals._run({"action": "list"})

    assert "not available" in said, said
    assert "the sleep store is not a directory" in said, said
    assert "Traceback" not in said, said


def test_a_missing_number_asks_rather_than_deciding_the_first_one(proposed):
    """`number` has no default, and nothing is not proposal 1."""
    _, text = call(action="keep")
    assert "Which proposal?" in text and "Nothing was changed" in text


def test_a_number_outside_the_list_is_refused_with_the_count(proposed):
    _, text = call(action="keep", number=9)
    assert "no proposal 9" in text and "the list has 1" in text


def test_the_whole_list_empty_is_an_answer_not_a_silence(monkeypatch):
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    ChronoaConfig().set("memory-sense-enabled", "true")
    _, text = call()
    assert "Nothing is waiting" in text
    assert "without asking first" in text


def test_consent_off_refuses_and_names_the_row_not_the_key(proposed):
    ChronoaConfig().set("memory-sense-enabled", "false")
    for arguments in ({}, {"action": "keep", "number": 1},
                      {"action": "discard", "number": 1}):
        _, text = call(**arguments)
        assert "Nothing was changed" in text, arguments
        assert "memory-sense-enabled" not in text, \
            "the refusal names a gsettings key no screen spells that way"
    assert "Remembered facts" in call()[1]


def test_the_listing_gate_is_the_same_one_as_the_decisions(proposed):
    """One switch for one decision, so there is nothing to work out.

    The cost is that *looking* is behind the same key as answering. That is
    stated rather than hidden, because the alternative - a read key beside the
    write key - is the `heard-sound` shape: two keys for one act.
    """
    ChronoaConfig().set("memory-sense-enabled", "false")
    _, listing = call()
    assert "Nothing is waiting" not in listing
    assert "Nothing was changed" in listing


# --- the one thing that makes this a second surface and not a second answer ---

def test_the_skill_calls_the_shared_acceptor_rather_than_its_own():
    """Two implementations of one decision that writes durable memory will drift.

    The panel is a different file, in a different layer, with no way to import
    this one. The only thing keeping them in step is that both reach
    `senses.memory.decide_candidate`, so the property is asserted against the
    source rather than trusted - and the source read is a plain string in this
    module, which is why the assertion is exact.
    """
    from tests._source import package_source

    source = package_source("skills.review_proposals")
    assert "decide_candidate" in source
    # No store, no percept, no write path of its own.
    for name in ("PerceptStore", "store_fact", "remember_fact", "_memory_percept"):
        assert name not in source, f"{name} is a second write path"


def test_a_second_write_path_would_be_caught_by_the_check_above(monkeypatch):
    """The control, because an absence only means something if it can fire."""
    from tests._source import package_source

    source = package_source("skills.review_proposals")
    assert "PerceptStore" in source + "\n# PerceptStore"
    assert "PerceptStore" not in source