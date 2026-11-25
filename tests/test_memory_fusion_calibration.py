"""Calibrated fusion constants and fair scoring for facts without vectors.

Two things are pinned here, one per half of the change:

1. `tools/calibrate_memory_fusion.py` derives both constants from a labelled
   corpus of fact-length text, using the shipped keyword scorer and the
   shipped fusion rule. The deterministic fake is the backend on a box with
   no local model, so the numbers are reproducible everywhere.
2. A fact with no vector - written before the model was set up - keeps its
   full keyword rank once a model appears. So does a fact whose vector is
   below the meaning floor. Neither is multiplied by `1 - VECTOR_WEIGHT`.

The gate tests set `RECALL_MIN_RELEVANCE` between the old halved score and the
new full one. That makes the fairness property observable from `recall()`
itself rather than from a private rank the caller never sees: the old rule
drops the fact, the new one returns it.
"""

import pathlib
import sys

import pytest

from shani_chronoa import local_embed
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import Percept
from shani_chronoa.senses import memory
from shani_chronoa.senses.memory import recall, remember_fact
from shani_chronoa.senses.store import PerceptStore

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import calibrate_memory_fusion as cal  # noqa: E402


@pytest.fixture(autouse=True)
def memory_allowed(monkeypatch):
    monkeypatch.setattr(ChronoaConfig, "sense_allowed_reason", lambda self, sense: "")
    monkeypatch.setattr(memory, "_warned_empty_vault", True)


def _store(tmp_path):
    return PerceptStore(
        durable_path=tmp_path / "memory.jsonl",
        live_path=tmp_path / "memory.jsonl.live",
    )


def _embed(pairs):
    def embed(texts, kind="", timeout=30.0, transport=None):
        return [list(pairs[text]) for text in texts]

    return embed


def _legacy_fact(content, now=1_000.0):
    """A durable fact with no vector: exactly what an older install holds."""
    return Percept(
        sense=memory.CONSENT_SENSE,
        kind=memory.MEMORY_KIND_FACT,
        content=content,
        created_at=now,
        ttl_seconds=None,
        source="test",
        sensitivity="private",
        metadata={"key": "place", "keywords": [content], "private": True},
        confidence=1.0,
    )


# ----------------------------------------------------------------- calibration


def test_calibration_derives_the_checked_in_constants():
    """The shipped constants are the measured ones, not borrowed numerals."""
    result = cal.derive()
    assert result["backend"].startswith("deterministic")
    assert result["VECTOR_WEIGHT"] == memory.VECTOR_WEIGHT
    assert result["VECTOR_MIN_SIMILARITY"] == memory.VECTOR_MIN_SIMILARITY


def test_calibration_measures_a_real_cosine_gap():
    """The floor sits inside an observed gap, not at a remembered value."""
    result = cal.derive()
    assert result["highest_non_match"] < result["VECTOR_MIN_SIMILARITY"]
    assert result["VECTOR_MIN_SIMILARITY"] < result["lowest_on_topic"]
    assert result["lowest_on_topic"] - result["highest_non_match"] > 0.5


def test_the_weight_grid_loses_each_signal_at_an_extreme():
    """0 loses the paraphrases; 1 loses the word hits; an interior wins both."""
    result = cal.derive()
    sweep = dict(result["sweep"])
    assert sweep[0.0] < sweep[memory.VECTOR_WEIGHT]
    assert sweep[1.0] < sweep[memory.VECTOR_WEIGHT]
    assert sweep[memory.VECTOR_WEIGHT] == pytest.approx(1.0)


def test_fusion_is_neutral_when_a_signal_is_absent_and_stable_when_they_agree():
    """No vector means keyword rank unchanged; equal signals fuse to themselves."""
    weight = memory.VECTOR_WEIGHT
    assert memory._fuse(0.7, None) == 0.7
    assert memory._fuse(0.7, 0.7) == pytest.approx(0.7)
    assert memory._fuse(0.0, 0.9) == 0.9
    assert 0.0 <= memory._fuse(0.2, 0.6, weight=weight) <= 1.0
    assert memory._fuse(0.2, 0.6, weight=0.0) == 0.2
    assert memory._fuse(0.2, 0.6, weight=1.0) == 0.6


# -------------------------------------------------------------------- fairness


def test_a_legacy_fact_is_not_halved_when_the_model_appears(tmp_path, monkeypatch):
    """A fact written before vectors existed keeps its full keyword score.

    The query is a single token shared exactly, so relevance is 1.0. A gate
    at 0.75 admits that rank and rejects the old halved 0.5, so the returned
    fact is evidence about the score, not about a threshold nobody checks.
    """
    monkeypatch.setattr(
        local_embed, "embed", _embed({"neovim": [1.0, 0.0], "alpha": [0.0, 1.0]})
    )
    monkeypatch.setattr(memory, "RECALL_MIN_RELEVANCE", 0.75)
    store = _store(tmp_path)
    store.add(_legacy_fact("neovim"))
    store.add(_legacy_fact("alpha"))

    assert [p.content for p in recall("neovim", store=store)] == ["neovim"]


def test_a_below_floor_vector_is_not_a_penalty(tmp_path, monkeypatch):
    """A vector too unlike the query is a missing signal, not a vote against it."""
    monkeypatch.setattr(
        local_embed,
        "embed",
        _embed({"neovim": [1.0, 0.0], "neovim note": [0.0, 1.0]}),
    )
    monkeypatch.setattr(memory, "RECALL_MIN_RELEVANCE", 0.6)
    store = _store(tmp_path)
    remembered = remember_fact("neovim note", store=store)
    assert remembered.metadata["vector"] == [0.0, 1.0]

    # The word hit is strong; its vector (cosine 0) says nothing useful. The
    # old rule scored it 0.5 * relevance and would have gated it here.
    found = [p.content for p in recall("neovim", store=store)]
    assert found == ["neovim note"]


def test_a_meaning_only_hit_keeps_its_cosine_when_no_word_matches(
    tmp_path, monkeypatch
):
    """With relevance 0 the rank is the cosine itself, not a fraction of it."""
    monkeypatch.setattr(
        local_embed,
        "embed",
        _embed({"what beverage do I drink": [1.0, 0.0], "flat white": [1.0, 0.0]}),
    )
    monkeypatch.setattr(memory, "RECALL_MIN_RELEVANCE", 0.9)
    store = _store(tmp_path)
    remember_fact("flat white", store=store)

    assert [p.content for p in recall("what beverage do I drink", store=store)] == [
        "flat white"
    ]


def test_without_a_model_the_keyword_path_is_unchanged(tmp_path, monkeypatch):
    """No query vector means no fusion at all, whatever is stored on disk."""
    monkeypatch.setattr(local_embed, "embed", lambda *a, **k: None)
    monkeypatch.setattr(memory, "RECALL_MIN_RELEVANCE", 0.75)
    store = _store(tmp_path)
    store.add(_legacy_fact("neovim"))

    assert [p.content for p in recall("neovim", store=store)] == ["neovim"]
