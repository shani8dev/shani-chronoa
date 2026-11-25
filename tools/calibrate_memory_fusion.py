#!/usr/bin/env python3
"""Calibrate the two constants in `senses.memory` that fuse facts by meaning.

    python3 tools/calibrate_memory_fusion.py

No dependency, no cloud call, no download. The one embedding path is
`local_embed.embed`, which talks only to the loopback llama.cpp server. If a
model is configured **and answering**, the harness measures with its real
vectors. Otherwise it uses the deterministic fake below, so the check is
reproducible on a machine with no model installed - which is every fresh
install, and the state of the box this was written on.

Why a measurement at all
------------------------
`VECTOR_WEIGHT = 0.5` and the old `VECTOR_MIN_SIMILARITY = 0.55` were borrowed
from conversation search, whose hits are whole messages. Memory facts are much
shorter and carry a hub word ("The user ..."), so neither number was tuned on
the text it now scores. This harness puts a labelled corpus of fact-length
text through the *same* keyword scorer and the *same* fusion function as
recall, and prints the numbers the corpus actually supports.

The fake, and what it stands for
--------------------------------
Every text is assigned a topic. A vector is a hub component (norm sqrt(0.1))
plus a topic component (the remaining sqrt(0.9)). The hub is the documented
"the user" prefix that a fact written by `extract_fact` carries in every
fact; it makes two unrelated facts score 0.10, not 0.00. Same-topic texts
score 1.00. The one intended fact in each case is deliberately *leaned* onto
a neighbouring topic, so its cosine is about 0.95 rather than a perfect 1.00
- a paraphrase is rarely the exact same sentence.

What is derived
---------------
* Floor: each case labels two facts as on topic (the intended one and the
  closer meaning decoy) and the rest as non-matches. The floor is the midpoint
  of the measured gap between the highest non-match and the lowest on-topic
  match. On the deterministic corpus that is 0.10 -> 0.95, midpoint 0.52.
* Weight: a grid search over 0.00..1.00 scores top-1 accuracy on the labelled
  corpus. Each case has one intended fact and two decoys: one wins on words
  alone, one wins on meaning alone. Weight 0 loses the meaning cases; weight
  1 loses the word cases; the interior plateau is the useful range. Ties are
  broken toward 0.50, the neutral blend of two [0, 1] signals.

The harness is a maintenance tool, not packaged. It never writes to the
package; it only imports its scorer and fusion rule. `main()` checks the
derived values against the checked-in constants in the deterministic-fake
mode, so an edit to either constant that the corpus does not support fails
here rather than silently shipping.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "usr" / "lib" / "shani-chronoa"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from shani_chronoa import local_embed  # noqa: E402
from shani_chronoa.senses import memory  # noqa: E402


@dataclass(frozen=True)
class Case:
    """One labelled query and the three facts it is scored against.

    `topic` is only for the fake embed; the real model ignores it. The words
    are deliberately arranged so `decoy_words` has more keyword overlap than
    `intended` while `decoy_meaning` is the closest vector but the wrong fact.
    """

    query: str
    intended: str
    decoy_words: str
    decoy_meaning: str
    topic: int


CASES = (
    Case(
        "what beverage does the user prefer",
        "the user prefers a flat white order",
        "what does the user order for breakfast",
        "a warm milky drink the user enjoys",
        0,
    ),
    Case(
        "which writing tool does the user pick",
        "the user picks neovim for writing",
        "which editor does the user pick daily",
        "the users favourite text application",
        1,
    ),
    Case(
        "when does the user need a reminder",
        "the user needs a kitchen timer for pasta",
        "when does the user set the timer",
        "a countdown the user asked for",
        2,
    ),
)

#: Topics 0..2 are the queries; 3..5 are unrelated topics used by the word
#: decoys and by the neighbour component of a leaned intended fact. Keeping
#: them disjoint matters: a decoy that accidentally shares a query's topic
#: would score 1.00 and corrupt the measured separation.
_HUB = 0.1
_DIMENSIONS = 7  # hub + six topics


def _unit(parts: "list[tuple[int, float]]") -> "list[float]":
    vector = [0.0] * _DIMENSIONS
    for index, weight in parts:
        vector[index] = weight
    # The same L2 normalisation `local_embed` applies to its own output, so a
    # dot product really is the cosine `memory` will compute at recall time.
    return local_embed.normalise(vector)


def fake_vector_table(cases=CASES) -> "dict[str, list[float]]":
    """Deterministic topic vectors for every text in the corpus."""
    hub = _HUB**0.5
    topic = (1.0 - _HUB) ** 0.5
    lean = 0.8**0.5
    neighbour = 0.1**0.5
    table: "dict[str, list[float]]" = {}
    for case in cases:
        table[case.query] = _unit([(0, hub), (1 + case.topic, topic)])
        table[case.decoy_meaning] = _unit([(0, hub), (1 + case.topic, topic)])
        spare = 1 + 3 + case.topic  # an unrelated topic, never a query's
        table[case.intended] = _unit(
            [(0, hub), (1 + case.topic, lean), (spare, neighbour)]
        )
        table[case.decoy_words] = _unit([(0, hub), (spare, topic)])
    return table


def fake_embed(cases=CASES):
    """`local_embed.embed`'s signature, backed by the deterministic table."""
    table = fake_vector_table(cases)

    def embed(texts, kind="", timeout=30.0, transport=None):
        try:
            return [list(table[text]) for text in texts]
        except KeyError as exc:
            raise AssertionError("uncounted corpus text: %s" % exc) from exc

    return embed


def embedder():
    """Real vectors when the local model answers, the deterministic fake else.

    `configured()` only checks files, so a configured but stopped server still
    returns None from `embed` and takes the fallback. Nothing here starts a
    service, and nothing outside loopback is ever contacted.
    """
    if local_embed.configured():
        probe = local_embed.embed([local_embed.QUERY])
        if probe:
            return local_embed.embed, "local_embed (real vectors)"
    return fake_embed(), "deterministic fake (no local model answering)"


def _facts(cases=CASES) -> "list[str]":
    return [
        text
        for case in cases
        for text in (case.intended, case.decoy_words, case.decoy_meaning)
    ]


def keyword_scores(cases=CASES) -> "list[list[float]]":
    """The shipped IDF-weighted Dice score, one row per case, one per fact.

    Uses memory's own tokenizer, IDF and `_relevance` rather than a copy, so
    a change to the scorer is reflected in the calibration instead of being
    calibrated around.
    """
    facts = _facts(cases)
    vocabularies = [{memory._stem(w) for w in memory._words(t)} for t in facts]
    doc_freq: "dict[str, int]" = {}
    for vocabulary in vocabularies:
        for token in vocabulary:
            doc_freq[token] = doc_freq.get(token, 0) + 1
    total = len(vocabularies)
    rows = []
    for case in cases:
        wanted = memory._tokens(case.query)
        query_mass = sum(memory._idf(total, doc_freq.get(t, 0)) for t in wanted)
        row = []
        for vocabulary in vocabularies:
            shared = sum(
                memory._idf(total, doc_freq.get(t, 0))
                for t in wanted
                if t in vocabulary
            )
            row.append(
                memory._relevance(
                    shared,
                    query_mass,
                    sum(memory._idf(total, doc_freq.get(t, 0)) for t in vocabulary),
                )
            )
        rows.append(row)
    return rows


def derive_floor(embed, cases=CASES) -> "tuple[float, float, float]":
    """(highest non-match, lowest on-topic match, midpoint floor)."""
    queries = embed([c.query for c in cases], kind=local_embed.QUERY)
    vectors = embed(_facts(cases), kind=local_embed.DOCUMENT)
    if not queries or not vectors or len(queries) != len(cases):
        raise RuntimeError("embedding backend returned no vectors")
    best, worst = [], []
    for case, query in zip(cases, queries):
        sims = [local_embed.similarity(query, vector) for vector in vectors]
        # The corpus labels two facts as on topic for its query: the intended
        # one and the closer meaning decoy. A fact belonging to another case,
        # or this case's word decoy, is a non-match. Using the labels rather
        # than "the single closest fact" matters: the intended paraphrase is
        # deliberately leaned and would otherwise be counted as a non-match
        # merely for being a less exact wording of the same thought.
        liked = {case.intended, case.decoy_meaning}
        for fact, sim in zip(_facts(cases), sims):
            (best if fact in liked else worst).append(sim)
    floor = round((max(worst) + min(best)) / 2.0, 2)
    return round(max(worst), 4), round(min(best), 4), floor


def derive_weight(
    embed, floor: float, cases=CASES
) -> "tuple[float, list[tuple[float, float]]]":
    """(chosen weight, [(weight, top-1 accuracy)]) by grid search."""
    queries = embed([c.query for c in cases], kind=local_embed.QUERY)
    vectors = embed(_facts(cases), kind=local_embed.DOCUMENT)
    relevance = keyword_scores(cases)
    scored = []
    for case, query, row in zip(cases, queries, relevance):
        facts = _facts(cases)
        items = []
        for index, (fact, vector, rel) in enumerate(zip(facts, vectors, row)):
            cosine = local_embed.similarity(query, vector)
            similar = cosine if cosine >= floor else None
            items.append((fact, rel, similar))
        scored.append((case.intended, items))

    def accuracy(weight: float) -> float:
        right = 0
        for intended, items in scored:
            ranked = sorted(
                items, key=lambda item: -memory._fuse(item[1], item[2], weight=weight)
            )
            if ranked[0][0] == intended:
                right += 1
        return right / len(scored)

    grid = [round(i / 20.0, 2) for i in range(21)]
    table = [(w, accuracy(w)) for w in grid]
    best = max(table, key=lambda pair: (pair[1], -abs(pair[0] - 0.5)))
    return best[0], table


def derive() -> "dict[str, object]":
    """Both constants, and the backend that produced them."""
    embed, backend = embedder()
    high_non, low_match, floor = derive_floor(embed)
    weight, sweep = derive_weight(embed, floor)
    return {
        "backend": backend,
        "highest_non_match": high_non,
        "lowest_on_topic": low_match,
        "VECTOR_MIN_SIMILARITY": floor,
        "VECTOR_WEIGHT": weight,
        "sweep": sweep,
    }


def main() -> int:
    result = derive()
    print("backend:", result["backend"])
    print("facts: %d queries x %d candidate facts" % (len(CASES), len(_facts())))
    print(
        "cosine: highest non-match %.4f, lowest on-topic %.4f -> floor %.2f"
        % (
            result["highest_non_match"],
            result["lowest_on_topic"],
            result["VECTOR_MIN_SIMILARITY"],
        )
    )
    print("weight sweep (weight: top-1 accuracy):")
    for weight, score in result["sweep"]:
        print(
            "  %.2f: %.2f%s"
            % (
                weight,
                score,
                "  <- chosen" if weight == result["VECTOR_WEIGHT"] else "",
            )
        )
    print("derived VECTOR_WEIGHT = %.2f" % result["VECTOR_WEIGHT"])
    print("derived VECTOR_MIN_SIMILARITY = %.2f" % result["VECTOR_MIN_SIMILARITY"])
    # A real model is a machine-specific measurement: print it, do not assert
    # the checked-in constants against somebody else's embedding vectors.
    if result["backend"].startswith("deterministic"):
        ok = (
            result["VECTOR_WEIGHT"] == memory.VECTOR_WEIGHT
            and result["VECTOR_MIN_SIMILARITY"] == memory.VECTOR_MIN_SIMILARITY
        )
        print("checked-in constants %s" % ("match" if ok else "DO NOT MATCH"))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
