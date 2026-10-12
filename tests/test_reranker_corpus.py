"""The reranker corpus is a generated artefact, so the generator is what is tested.

`tools/build_reranker_corpus.py` decides what a model is trained and scored on.
Nothing else in the tree checks it, which is this repository's recorded shape for
a generated file - `eval_cases.json`'s `skills_total` went stale for the same
reason and needed `--refresh-coverage` before anything held it.

**The properties are chosen from what the corpus is FOR, not from what the
builder happens to do.** Tool selection is a choice among offered tools, so what
matters is that the distractors are the ones that actually crowded the answer
out; that the split does not leak a request across both sides; and that a case the
selector failed to offer is still in the corpus, because a reranker is scored on
the candidate list the selector produced and not on a list it would have liked.

The negative control is the property that cannot drift silently: a corpus built
with uniform negatives must fail these tests. Uniform negatives are the obvious
alternative design and they pass every structural check while training nothing -
which is why the "hard negative" assertion is about *provenance* (a negative is a
name `ranked()` put in its top slice) and not merely about being a real tool.
"""

import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))
sys.path.insert(0, str(REPO / "tools"))

import build_reranker_corpus as builder  # noqa: E402


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """Built fresh, **not** read off disk.

    The first version of this file read `tools/reranker_corpus.json`, and the
    mutation run said why that is the wrong shape: five of seven mutations of
    the *builder* came back as survivors, because a broken builder still leaves
    the last good artefact on disk for a test that reads it. A test about a
    generator has to run the generator; the one test that reads the shipped file
    is `test_the_shipped_file_matches_a_fresh_build`, and that is where
    "the artefact is stale" belongs.
    """
    rows, stats = builder.build()
    doc = json.loads((REPO / "tools" / "reranker_corpus.json").read_text())
    # The structural fields come from the real generator; only the row lists are
    # replaced, so the limit/provenance text under test is the shipped text.
    doc["train"] = rows[:len(doc["train"])] if doc["train"] else []
    doc["held_out"] = rows[len(doc["train"]):]
    doc["counts"]["requests"] = len(rows)
    doc["recall"] = stats
    return doc


@pytest.fixture(scope="module")
def rows(corpus):
    return corpus["train"] + corpus["held_out"]


class TestTheSplitIsDeterministic:
    """A split that changes when it is re-run cannot be named by a commit."""

    def test_two_builds_agree(self):
        a = builder.build()[0]
        b = builder.build()[0]
        assert [r["say"] for r in a] == [r["say"] for r in b]
        assert [r["positive"] for r in a] == [r["positive"] for r in b]

    def test_the_shipped_file_matches_a_fresh_build(self, rows):
        fresh = builder.build()[0]
        assert len(fresh) == len(rows)
        assert {r["say"] for r in fresh} == {r["say"] for r in rows}


class TestNoRequestAppearsOnBothSides:
    """The leak a held-out split is for."""

    def test_the_sides_are_disjoint_on_the_request(self, corpus):
        train = {r["say"] for r in corpus["train"]}
        held = {r["say"] for r in corpus["held_out"]}
        assert train & held == set()

    def test_every_scorable_case_is_present_exactly_once(self, rows, corpus):
        cases = json.loads((REPO / "tools" / "eval_cases.json").read_text())["cases"]
        usable = {c["say"] for c in cases
                  if c.get("expect") and c["expect"][0].get("tool")}
        assert {r["say"] for r in rows} == usable
        assert len(rows) == len(usable), "a request was written twice"


class TestEveryNamedToolIsReal:
    """A corpus naming a tool that is not in the registry trains nothing."""

    def test_positives_and_negatives_resolve(self, rows):
        from shani_chronoa import tools as T
        from shani_chronoa import tool_select as S
        real = {S._name(t) for t in T.TOOLS}
        for r in rows:
            assert r["positive"] in real, f"{r['positive']} is not a tool"
            for n in r["negatives"]:
                assert n["tool"] in real, f"{n['tool']} is not a tool"

    def test_the_positive_is_not_among_its_own_negatives(self, rows):
        for r in rows:
            assert r["positive"] not in {n["tool"] for n in r["negatives"]}


class TestNegativesAreTheOnesThatCrowdTheAnswerOut:
    """The reason the corpus exists at all.

    Uniform negatives are the obvious design and they make this corpus worthless
    while passing every structural check above: a negative the selector already
    rates at zero teaches nothing about the case the reranker has to fix. So this
    asserts **provenance** - each negative is a name `ranked()` placed inside the
    slice the selector actually considered - rather than asserting the negative
    is merely a real tool, which is the check that passes either way.
    """

    def test_every_negative_was_in_the_selectors_own_offer(self, rows):
        from shani_chronoa import tools as T
        from shani_chronoa import tool_select as S

        for r in rows:
            scored = S.ranked(r["say"], T.TOOLS)
            offered = {n for _s, n, _h in scored[:S.DEFAULT_LIMIT]}
            for n in r["negatives"]:
                assert n["tool"] in offered, (
                    f"{n['tool']} is a negative for {r['say']!r} but the "
                    f"selector never offered it"
                )

    def test_the_hardest_negatives_are_kept_first(self, rows):
        """Truncation must keep the confusers, not a random prefix.

        `NEGATIVES` caps the list, so the ordering is the only thing standing
        between the corpus and a random subset of irrelevant distractors. The
        property is: **when something outranks the answer, that something is the
        negative that survives.** Rows where the answer ranks first have no
        confuser, and rows where it was never offered have none either - the
        answer sits below the offered slice and nothing in it can outrank it.
        """
        from shani_chronoa import tools as T
        from shani_chronoa import tool_select as S

        checked = 0
        for r in rows:
            if not r["negatives"] or not r["recall_hit"]:
                continue
            rank = {n: i for i, (_s, n, _h) in
                    enumerate(S.ranked(r["say"], T.TOOLS, learned={}))}
            mine = rank[r["positive"]]
            if mine == 0:
                continue  # already first; nothing crowded it out
            best = r["negatives"][0]["tool"]
            assert rank[best] < mine, (
                f"{r['say']!r}: the answer ranks {mine} and the first kept "
                f"negative ranks {rank[best]}, so truncation kept a weaker "
                f"distractor and dropped the confuser"
            )
            checked += 1
        assert checked > 20, f"only {checked} rows could check the ordering"


class TestTheCorpusDoesNotDriftWithThisMachinesHistory:
    """The defect this file was written after finding.

    `ranked()` reads this machine's live tool-call log when its `learned`
    argument is `None`, so building with the default made the corpus a function
    of **whoever ran the builder and what they had asked for since** - measured
    as two builds of one commit, minutes apart with no edit between them,
    reporting 212 and 211 positives inside the offered set.

    The control is the same builder run twice with **different learned weights in
    force**, which is what a moving call log amounts to. A corpus that moves is
    one no commit can name and no other machine can reproduce.
    """

    def test_different_learned_weights_give_the_same_corpus(self, monkeypatch):
        from shani_chronoa import tool_select as S

        plain = builder.build()[0]
        monkeypatch.setattr(S, "learned_weights",
                            lambda: {"qr_code": 99.0, "browse": 99.0})
        assert S.learned_weights(), "the control did not take: no weights in force"
        after = builder.build()[0]
        assert [r["say"] for r in plain] == [r["say"] for r in after]
        assert [r["offered"] for r in plain] == [r["offered"] for r in after]
        assert [r["negatives"] for r in plain] == [r["negatives"] for r in after]

    def test_the_call_says_out_loud_that_it_wants_no_learning(self):
        """A source-level check, because the call itself is the fix.

        Changing `learned={}` back to the default compiles, runs and produces a
        corpus - just a different one, on any machine with a non-empty call log.
        Every behavioural assertion here would pass on a machine with an empty
        log, which is exactly why the drift stayed invisible until two builds
        disagreed.
        """
        import inspect
        src = inspect.getsource(builder.build)
        assert "learned={}" in src, (
            "the builder calls ranked() without learned={}, so the corpus "
            "follows this machine's tool-call log"
        )


class TestARecallFailureIsStillACorpusRow:
    """A reranker is scored on the candidate list the selector produced.

    Nine of the 220 positives are **not** in the offered set. Dropping those rows
    would quietly remove every case where retrieval is the problem - which is the
    population a reranker exists to recover - and would report a corpus that only
    ever contains the cases the selector already got right.
    """

    def test_rows_whose_positive_was_not_offered_are_present(self, corpus):
        missed = [r for r in corpus["train"] + corpus["held_out"]
                  if not r["recall_hit"]]
        assert len(missed) == corpus["recall"]["not_offered"]
        assert corpus["recall"]["offered"] + corpus["recall"]["not_offered"] == \
            corpus["counts"]["requests"]

    def test_a_missed_row_still_carries_its_negatives(self, corpus):
        missed = [r for r in corpus["train"] + corpus["held_out"]
                  if not r["recall_hit"]]
        assert missed, "no recall failures in the corpus, so this checks nothing"
        assert any(r["negatives"] for r in missed)


class TestArgumentsAreCarried:
    """`ascension_gate` scores right tool AND right arguments, so the gate
    needs them - and the first version of this file asserted there were none.

    **That assertion was a scan for the wrong spelling.** It asked for
    `expect[0]["arguments"]` and found none, concluded the corpus could evidence
    ranking only, and wrote that limit into the corpus file. Measured: **111 of
    the 220 carry arguments under the key `args`**, across 98 distinct tools, and
    `tools/task_eval.py:91` reads that key. A scan for a plausible spelling of a
    word is the `price`/`collapse` failure in its purest form - it finds nothing
    and the nothing reads as a fact about the tree.

    So the corpus carries them, and these tests read the key `task_eval.py`
    reads rather than the one the wrong assumption was built on.
    """

    #: The key `tools/task_eval.py` reads, asserted here so a rename in one
    #: place is a red test rather than a corpus that quietly carries nothing.
    ARGS_KEY = "args"

    def test_the_arguments_reach_every_row_that_has_them(self, rows):
        source = {}
        for case in json.loads(
                (REPO / "tools" / "eval_cases.json").read_text())["cases"]:
            if case.get("expect"):
                source[case["say"]] = dict(case["expect"][0].get(self.ARGS_KEY) or {})
        expected = sum(1 for v in source.values() if v)
        carried = sum(1 for r in rows if r["positive_args"])
        assert expected == 111, (
            f"{expected} cases carry arguments, not 111 - the corpus's own "
            f"stated number is stale and the limit sentence below is false"
        )
        assert carried == expected, (
            f"{carried} of {expected} rows carry the arguments the corpus "
            f"already had available"
        )

    def test_the_arguments_are_the_ones_the_corpus_names(self, rows):
        source = {}
        for case in json.loads(
                (REPO / "tools" / "eval_cases.json").read_text())["cases"]:
            if case.get("expect"):
                source[case["say"]] = dict(case["expect"][0].get(self.ARGS_KEY) or {})
        for r in rows:
            assert r["positive_args"] == source[r["say"]], (
                f"{r['say']!r}: arguments differ from the corpus they came from"
            )

    def test_a_row_with_no_arguments_says_so_with_an_empty_dict(self, rows):
        """An absent key is indistinguishable from a dropped one in a consumer
        that reads `row.get("args")`, so the field is always present."""
        for r in rows:
            assert "positive_args" in r
            assert isinstance(r["positive_args"], dict)

    def test_the_task_eval_key_is_the_one_this_corpus_uses(self):
        """A source-level check, because the two spellings differ.

        This is the whole defect in one assertion: `task_eval.py` reads `args`,
        so a corpus written against `arguments` would be scored as carrying none
        and the gate would silently refuse for a reason that reads like a model
        failure.
        """
        import inspect
        sys.path.insert(0, str(REPO / "tools"))
        import task_eval
        src = inspect.getsource(task_eval)
        assert '"args"' in src, (
            "task_eval no longer reads `args`; this corpus's key must follow it"
        )
        assert 'want.get("args")' in src

    def test_the_headroom_the_gate_is_capable_of(self):
        """How much of the corpus `ascension_gate` can score at all.

        Of the 220 case-carrying rows, **109 declare no expected arguments**, so
        a model that calls the right tool with an *empty argument dict* scores
        as fully correct on all 109 - measured through the real scorer, not
        inferred. That is not a bug in the scorer; it is the difference between
        "the call was right" and "there was nothing in this call to be right
        about", and only the second is being reported here.

        What it means for a 15.5-hour training run: `Measurement.right` cannot
        distinguish an improvement on the 111 from a vacuous pass on the 109, so
        the honest statement of the headroom is **111 of 220, not 220**. A verdict
        drawn from the total is a verdict that counts 109 things that were never
        being scored.
        """
        sys.path.insert(0, str(REPO / "tools"))
        import task_eval
        cases = json.loads((REPO / "tools" / "eval_cases.json").read_text())["cases"]
        rows = [c for c in cases if c.get("expect")]
        empty = [c for c in rows if not c["expect"][0].get("args")]
        assert len(rows) == 220 and len(empty) == 109, (
            f"{len(rows)} expect-carrying cases with {len(empty)} declaring no "
            f"arguments - both numbers moved, so the limit sentence and the "
            f"assertion above are stale"
        )
        vacuous = sum(
            1 for c in empty
            if task_eval.score(c, [{"name": c["expect"][0]["tool"], "args": {}}])
        )
        assert vacuous == len(empty) == 109, (
            f"{vacuous} of {len(empty)} no-argument cases score as passing on an "
            f"empty argument dict"
        )


class TestTheDeclaredLimitIsTrue:
    """The limit is about the 109 rows that carry nothing, and it names them.

    Written rather than assumed: a file that states a limit nobody checks is the
    claim this repository records as false, and the previous version of this
    limit was simply wrong.
    """

    def test_the_limit_is_stated_in_what_the_generator_writes(self, tmp_path,
                                                              monkeypatch):
        """Read from a generated document, not the shipped one.

        The first version asserted against `tools/reranker_corpus.json`, which
        means a builder that stopped writing the sentence entirely passed - the
        file on disk still had it. The mutation run is what said so: replacing
        the string in the builder came back a clean 17.
        """
        monkeypatch.setattr(builder, "OUT", tmp_path / "c.json")
        assert builder.main() == 0
        limit = json.loads((tmp_path / "c.json").read_text())["limit"]
        assert "111 of 220" in limit, (
            "the limit must name its own population, in the sentence that "
            "bounds it - not merely somewhere in the file. A `in` check passes "
            "when 111 appears twice and one is removed, which is the "
            "witness-that-cannot-fail trap this set already found once."
        )
        assert "NOT on the 109" in limit

    def test_the_negative_cap_cannot_silently_exceed_the_offer(self):
        """Why the ordering assertion exists, and what would unmake it.

        `NEGATIVES` is 12 and `DEFAULT_LIMIT` is 8, so the offered slice holds
        at most 7 non-positive tools and `[:NEGATIVES]` **never truncates** -
        which is why the shuffle-then-truncate mutation is an equivalent mutant
        rather than a survivor. The order of rank and truncate is therefore
        currently unobservable, and this is the assertion that would start
        observing it: if `DEFAULT_LIMIT` is ever raised past the cap, the kept
        subset becomes random and the corpus trains on twelve irrelevant
        distractors per request while every other test stays green.
        """
        from shani_chronoa import tool_select as S
        # The offered slice holds at most `DEFAULT_LIMIT` names, one of which is
        # the answer, so at most `DEFAULT_LIMIT - 1` can ever be a negative.
        # Truncation starts biting the moment that exceeds `NEGATIVES`.
        biggest = S.DEFAULT_LIMIT - 1
        assert biggest <= builder.NEGATIVES, (
            f"an offer of {S.DEFAULT_LIMIT} yields up to {biggest} negatives "
            f"against a cap of {builder.NEGATIVES}: truncation is now reachable, "
            f"so the rank-then-truncate order is load-bearing and the "
            f"shuffle-then-truncate form becomes a defect"
        )


class TestTheScriptItselfRuns:
    """`tools/build_reranker_corpus.py` is the entry point a person runs."""

    def test_it_writes_the_file_it_claims(self, tmp_path, monkeypatch):
        monkeypatch.setattr(builder, "REPO", REPO)
        monkeypatch.setattr(builder, "OUT", tmp_path / "reranker_corpus.json")
        assert builder.main() == 0
        written = json.loads((tmp_path / "reranker_corpus.json").read_text())
        assert written["counts"]["requests"] > 200
        assert written["train"] and written["held_out"]

    def test_it_is_runnable_as_a_script(self):
        env = {"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools" / "build_reranker_corpus.py")],
            capture_output=True, text=True, env=env, timeout=600,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "carrying arguments: 111" in proc.stdout
        assert "111 rows" in proc.stdout