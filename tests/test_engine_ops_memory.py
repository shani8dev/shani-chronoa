"""Memory engine ops: stand-in store and digest decisions, no model needed."""

from types import SimpleNamespace

import pytest

from shani_chronoa.engine_ops import memory
from shani_chronoa.senses import memory as sense_memory


@pytest.fixture(autouse=True)
def _throwaway_xdg(tmp_path, monkeypatch):
    data = tmp_path / "xdg-data"
    config = tmp_path / "xdg-config"
    data.mkdir()
    config.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(sense_memory, "_consent_error", lambda: "")


class Store:
    def __init__(self, percepts):
        self._percepts = percepts

    def durable(self):
        return list(self._percepts)


def _fact(text, created=1.0, source="user-stated"):
    return SimpleNamespace(
        content=text,
        created_at=created,
        source=source,
        confidence=0.9,
        metadata={"secret": "sk-do-not-return-this"},
    )


def _proposal(index=2, text="I live in Pune"):
    return sense_memory.Proposal(
        "20261126-120000-abcd", index, text, 0, "user", "a trip to Pune", 3.0
    )


def test_list_memory_returns_facts_and_undecided_proposals(monkeypatch):
    seen = []
    monkeypatch.setattr(
        sense_memory, "get_store", lambda: Store([_fact("my editor is neovim")])
    )
    monkeypatch.setattr(
        sense_memory,
        "pending_proposals",
        lambda limit=20: seen.append(limit) or [_proposal()],
    )

    out = memory.list_memory(limit=5)

    assert out["facts"] == [
        {
            "text": "my editor is neovim",
            "created": 1.0,
            "source": "user-stated",
            "confidence": 0.9,
        }
    ]
    assert out["proposals"] == [
        {
            "id": "20261126-120000-abcd:2",
            "text": "I live in Pune",
            "summary": "a trip to Pune",
            "source_index": 0,
            "source_role": "user",
            "created": 3.0,
        }
    ]
    assert seen == [5]
    assert "sk-do-not-return-this" not in repr(out)
    assert "metadata" not in repr(out)


def test_list_memory_applies_the_limit_to_both_tiers(monkeypatch):
    monkeypatch.setattr(
        sense_memory, "get_store", lambda: Store([_fact("one", 1.0), _fact("two", 2.0)])
    )
    monkeypatch.setattr(sense_memory, "pending_proposals", lambda limit=20: [])

    out = memory.list_memory(limit=1)

    assert len(out["facts"]) == 1
    assert out["facts"][0]["text"] == "one"


def test_list_memory_honours_a_denied_consent(monkeypatch):
    monkeypatch.setattr(sense_memory, "_consent_error", lambda: "turn on Memory first")
    monkeypatch.setattr(sense_memory, "get_store", lambda: pytest.fail("must not read"))

    assert memory.list_memory() == {"error": "turn on Memory first"}


def test_decide_refuses_without_confirm(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sense_memory,
        "decide_candidate",
        lambda *a, **k: calls.append((a, k)) or ("kept", ""),
    )

    out = memory.decide("20261126-120000-abcd:2", "yes")

    assert "confirmation required" in out["error"]
    assert calls == []


@pytest.mark.parametrize(
    "word,expected",
    [
        ("yes", True),
        ("Y", True),
        ("keep", True),
        ("1", True),
        (True, True),
        ("no", False),
        ("false", False),
        ("n", False),
        (False, False),
    ],
)
def test_decide_parses_the_id_and_passes_a_real_bool(monkeypatch, word, expected):
    calls = []

    def fake_decide(*args, **kwargs):
        calls.append((args, kwargs))
        return ("kept" if args[2] else "declined"), ""

    monkeypatch.setattr(sense_memory, "decide_candidate", fake_decide)

    out = memory.decide("20261126-120000-abcd:2", word, confirm=True)

    word_out = "yes" if expected else "no"
    assert out == {"id": "20261126-120000-abcd:2", "decision": word_out}
    assert calls == [(("20261126-120000-abcd", 2, expected), {})]


@pytest.mark.parametrize(
    "outcome,reason",
    [
        ("kept", ""),
        ("declined", ""),
        ("kept-unrecorded", "the store refused"),
        ("refused", "memory is off"),
        ("already-decided", "proposal 2 was already accepted"),
    ],
)
def test_decide_reports_the_outcome_honestly(monkeypatch, outcome, reason):
    monkeypatch.setattr(
        sense_memory, "decide_candidate", lambda *a, **k: (outcome, reason)
    )

    out = memory.decide("20261126-120000-abcd:2", "yes", confirm=True)

    if outcome in ("kept", "declined"):
        assert out == {
            "id": "20261126-120000-abcd:2",
            "decision": "yes" if outcome == "kept" else "no",
        }
    else:
        assert "error" in out
        assert reason in out["error"]


def test_bad_decision_and_bad_id_are_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sense_memory,
        "decide_candidate",
        lambda *a, **k: calls.append(a) or ("kept", ""),
    )

    assert "must be yes or no" in memory.decide("sid:0", "maybe", confirm=True)["error"]
    assert "not a proposal id" in memory.decide("sid", "yes", confirm=True)["error"]
    assert "not a proposal id" in memory.decide("sid:x", "yes", confirm=True)["error"]
    assert calls == []


def test_decide_honours_a_denied_consent(monkeypatch):
    monkeypatch.setattr(sense_memory, "_consent_error", lambda: "turn on Memory first")
    monkeypatch.setattr(
        sense_memory, "decide_candidate", lambda *a, **k: pytest.fail("must not decide")
    )

    assert memory.decide("sid:0", "yes", confirm=True) == {
        "error": "turn on Memory first"
    }
