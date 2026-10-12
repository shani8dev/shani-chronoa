"""The retrieval layer's recall, measured - the number nothing here computed.

`tools/eval_cases.json` carries 221 cases that name the tool a correct answer
calls, and 5 that expect none. For every one of them `tool_select.select_tools()`
decides which schemas are sent, so **whether the right tool is offered at all is
answerable without a model, without a server, and in well under a second** -
and until this file existed, nothing in this repository asked.

The measured answer, recorded in `AGENTS.md`:

| | |
|---|---:|
| scorable cases | 221 |
| **correct tool present in the offered set** | **211 (95.5%)** |
| mean tools offered per request | 6.2 (max 11) |
| the correct tool ranked first, when offered | 171 of 211 (82%) |

**This is the ceiling on `task_eval`'s `select` configuration**, and it is a
property of the selector rather than of the model. The paper this repository's
own research entry cites - arXiv:2609.34971 - reports that the *same* task
exposed through functionally-equivalent schemas swings success from complete
failure to 97%, so a retrieval layer that drops a tool is indistinguishable
from a tool the model cannot use.

**What the measurement then found, and it is not what was expected.** The first
version of this file asserted that every missed tool scores *zero* on word
overlap - a vocabulary failure needing synonyms - **and the suite proved that
wrong**: 16 of the 18 misses score between 1.5 and 34.0 and are simply not
offered. The selector is not failing to see them.

| | measured |
|---|---:|
| correct tool offered (recall) | **211/221 = 95.5%** |
| **top-ranked tool is the correct one** | **177/221 = 80%** |
| misses that were **outranked** rather than invisible | **16 of 18** |
| **offered tools sharing no request word at all** | **287/825 = 35%** |

So recall is healthy and the top pick is usually right, while **a third of what
is actually sent to the model shares no word with the request**. That is the
finding worth acting on: `select_tools` exists so a request is not answered
from 257 schemas, and it is still spending a third of a small budget on
irrelevant ones. The ordering itself is sound - **1 inversion in 2,202 adjacent
pairs** - so this is dilution, not mis-ranking.

**What this suite does and does not cover, stated because a mutation proved
it.** The recall, top-1 and dilution assertions are **floors**. A mutation that
*improves* them is therefore invisible, and one was run: adding the word-overlap
count to `ranked()`'s score **left all 11 tests green**, because it makes
selection more relevant and every floor is satisfied by an improvement. The
control that was run and **caught** is dropping `CORE` from the union in
`select_tools` - `test_the_core_tools_are_always_available` goes red.

So this file is a **regression guard, not a metric lock**, and that is the same
trade `Report.honest()` and the recall floors elsewhere in this repository make:
a number pinned exactly turns the suite red every time a skill lands and says
nothing about the regression it was written for. Anyone who wants to know whether
the metric *moved* re-runs the table above; anyone who wants to know whether it
*broke* runs this file.
"""
import json
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import tool_select as TS  # noqa: E402
from shani_chronoa.tools import TOOLS  # noqa: E402

CASES = json.loads((_REPO / "tools" / "eval_cases.json").read_text())["cases"]

#: Floors, not the measured values. A metric asserted at its current number
#: fails the first time a skill lands and says nothing about regressions, which
#: is the stale-label failure this repository records about dead code.
RECALL_FLOOR = 0.90
FIRST_FLOOR = 0.70


def _scored():
    return [c for c in CASES if "expect" in c]


def _offered(say):
    return [s["function"]["name"] for s in TS.select_tools(say, TOOLS)]


def _recall(select):
    hits = total = 0
    for case in _scored():
        got = set(select(case["say"]))
        for want in {e["tool"] for e in case["expect"]}:
            total += 1
            hits += want in got
    return hits, total


class TestTheSelectorOffersTheRightTool:
    def test_the_corpus_is_the_size_this_file_claims(self):
        assert len(_scored()) >= 200, len(_scored())

    def test_recall_is_at_least_the_floor(self):
        hits, total = _recall(_offered)
        assert hits / total >= RECALL_FLOOR, "%d/%d = %.1f%%" % (
            hits, total, 100 * hits / total)

    def test_the_right_tool_is_offered_at_rank_one_often(self):
        """Distinct from recall: a tool that is offered but ranked last is nearly
        as useless as one not offered, because the small models this repository
        ships with do not reliably dig through ten near-synonymous schemas."""
        first = total = 0
        for case in _scored():
            order = [r[1] for r in TS.ranked(case["say"], TOOLS)]
            got = set(_offered(case["say"]))
            for want in {e["tool"] for e in case["expect"]}:
                if want in got:
                    total += 1
                    first += order[:1] == [want]
        assert total, "no case offered its tool at all"
        assert first / total >= FIRST_FLOOR, "%d/%d" % (first, total)

    def test_a_selector_that_offers_nothing_scores_zero(self):
        """The control. Without it, a `select_tools` that returned the CORE
        triple unconditionally would pass the recall floor on the cases that
        happen to want a core tool, and this file would be reporting coverage
        it never measured."""
        hits, total = _recall(lambda say: [])
        assert hits == 0 and total > 200


class TestWhatTheMissesActuallyAre:
    """The diagnosis, pinned so it cannot quietly change class.

    **The first version of this file asserted that every missed tool scores
    zero on word overlap, and the suite caught it being wrong**: 16 of the 18
    misses score between 1.5 and 34.0 and are simply not offered. So the misses
    are **mostly outranked, not invisible** - and a "add a synonym" story would
    have been the wrong remedy for sixteen of eighteen cases.

    What is actually measured:

    | | |
    |---|---:|
    | correct tool offered (recall) | 211/221 = 95.5% |
    | **top-ranked tool is the correct one** | **177/221 = 80%** |
    | **offered tools that share no request word at all** | **287/825 = 35%** |

    So the selector is good at the top and **dilute in the set**: a third of
    the slots go to tools with no lexical relation to what was asked, which is
    both the budget `select` exists to protect and attention the model spends.
    """

    def _misses(self):
        out = []
        for case in _scored():
            got = set(_offered(case["say"]))
            scored = dict((n, s) for s, n, _ in TS.ranked(case["say"], TOOLS))
            for want in {e["tool"] for e in case["expect"]}:
                if want not in got:
                    out.append((case, want, scored.get(want, 0.0)))
        return out

    def test_there_are_misses_to_diagnose(self):
        """The control for everything below: an empty list satisfies every
        "every miss is X" assertion vacuously."""
        assert len(self._misses()) >= 5, len(self._misses())

    def test_most_misses_are_outranked_rather_than_invisible(self):
        """A floor, not the measured value - a synonym table that grew would move
        this and should not turn the suite red for it."""
        misses = self._misses()
        scored = [s for _, _, s in misses if s > 0.0]
        assert len(scored) / len(misses) >= 0.60, (
            "%d of %d misses score above zero - if this drops, the misses have "
            "become a vocabulary problem and the remedy changed"
            % (len(scored), len(misses)))

    def test_the_score_ordering_is_not_broken(self):
        """`ranked()` is internally consistent: a tool with more request-word
        overlap is not routinely ranked below one with less.

        Measured over 2,202 adjacent pairs: **one inversion**. This is the
        assertion that keeps the "outranked" explanation honest - if the
        ordering were arbitrary the misses would be a ranking bug instead.
        """
        inversions = pairs = 0
        for case in _scored():
            rows = [(s, ov) for s, _, ov in TS.ranked(case["say"], TOOLS)][:12]
            for i in range(len(rows) - 1):
                pairs += 1
                if rows[i][0] < rows[i + 1][0] and rows[i][1] > rows[i + 1][1]:
                    inversions += 1
        assert pairs > 1000, pairs
        assert inversions / pairs < 0.05, "%d inversions in %d pairs" % (
            inversions, pairs)

    def test_the_top_pick_is_often_right_which_is_why_recall_is_not_the_whole_story(self):
        first = total = 0
        for case in _scored():
            rows = list(TS.ranked(case["say"], TOOLS))
            if not rows:
                continue
            total += 1
            first += rows[0][1] in {e["tool"] for e in case["expect"]}
        assert first / total >= 0.65, "%d/%d" % (first, total)


class TestTheOfferedSetIsDilute:
    """The measurement that says what is actually worth fixing.

    Recall at 95.5% looks fine and hides this: **35% of the tools actually sent
    share no word with the request.** Every one of them is a schema the model
    reads and may call, and `select_tools` exists precisely so a request is not
    answered from the whole catalogue. A third of a small set being unrelated is
    the same waste at a smaller scale.
    """

    def test_a_large_share_of_offered_tools_share_no_request_word(self):
        offered = total = 0
        for case in _scored():
            got = set(_offered(case["say"]))
            for _score, name, overlap in TS.ranked(case["say"], TOOLS):
                if name in got:
                    total += 1
                    offered += (overlap == 0)
        assert total > 400, total
        # A floor rather than the measured 35%: this is a property to keep from
        # getting worse, not a target, and tightening it to the current number
        # would make the suite red for the next skill that lands.
        assert offered / total <= 0.50, "%d of %d unrelated" % (offered, total)


class TestTheOfferedSetIsBounded:
    def test_a_selection_is_small_even_though_the_registry_is_not(self):
        """The reason selection exists, asserted rather than assumed.

        257 schemas cost 139,634 real tokens against an 8k context, so a
        selection that grew would stop being affordable before it stopped being
        wrong. This is the shape of that constraint on the live path.
        """
        sizes = [len(_offered(c["say"])) for c in _scored()]
        assert max(sizes) <= 16, max(sizes)
        assert sum(sizes) / len(sizes) < 12, sum(sizes) / len(sizes)

    def test_the_core_tools_are_always_available(self):
        """`select_tools` unions `CORE` in unconditionally, so a request that
        matches nothing still gets the three tools every turn may need."""
        for case in _scored()[:40]:
            got = set(_offered(case["say"]))
            assert set(TS.CORE) <= got, (case["id"], got)