"""arXiv search: the Atom feed, the query builder, and the widening ladder.

The fixture is a **real captured response** from `export.arxiv.org`, in
`tests/fixtures/search/arxiv.atom`, beside the real captures of every other
engine this module reads. Nothing here is hand-written: a fixture that agrees
with the parser it exists to test proves only that they agree.

**Every expectation in the builder tests is a number this repository measured
against the live API on 2026-10-12**, and the measurement is the reason the
builder is shaped the way it is - a title-scoped conjunction over framing-word-
free terms, having measured that the alternatives (whole-phrase quoting,
disjunction, and a full-text `all:` scope) each fail in a different direction.
Those figures are in `websearch.arxiv_query`'s docstring.
"""
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import websearch as W  # noqa: E402

FIX = _REPO / "tests" / "fixtures" / "search"


def _cap():
    return (FIX / "arxiv.atom").read_bytes()


def _capempty():
    """A real capture of a query that matches nothing: a **well-formed** feed,
    `totalResults 0`, no `<entry>`. Not an empty body - see `_run_with`."""
    return (FIX / "arxiv-empty.atom").read_bytes()


def _terms(built):
    """The bare terms of a built query, scope prefixes removed.

    **Split on terms, never searched as a substring.** The first version of the
    framing-word check asked whether `all:in` was *in* the query - and `all:in`
    is a substring of `all:inference`, so the assertion passed whether or not
    the word had been dropped. This is the "a control that cannot fail" shape,
    and it is why the term is compared by equality.
    """
    return [part.split(":", 1)[1] for part in built.split(" AND ") if part]


class TestTheFixtureIsReal:
    """A guard on the fixture itself, because every parser test rests on it."""

    def test_the_capture_is_atom_with_arxs_namespace(self):
        raw = _cap()
        assert b"<feed" in raw
        assert b"xmlns:arxiv=" in raw, "the arXiv extension namespace is missing"

    def test_the_capture_has_the_entries_the_parser_claims(self):
        assert _cap().count(b"<entry>") >= 3


class TestParsing:
    def test_the_real_feed_gives_real_results(self):
        results, why = W._parse_arxiv(_cap())
        assert why == ""
        assert len(results) == 3, results

    def test_every_result_has_a_title_and_a_url(self):
        results, _ = W._parse_arxiv(_cap())
        for r in results:
            assert r.title
            assert r.url.startswith("http")

    def test_the_url_is_the_alternate_link_over_https_not_the_http_id(self):
        """`<id>` is `http://` and the alternate link is `https://`.

        Measured on the real feed: `<id>http://arxiv.org/abs/2510.05846v2`
        against `<link href="https://arxiv.org/abs/2510.05846v2" rel="alternate">`.
        A feed that answered the id would put an insecure URL into an answer
        whose whole job is to be citable.
        """
        results, _ = W._parse_arxiv(_cap())
        for r in results:
            assert r.url.startswith("https://"), r.url
        assert any("v" in r.url.rsplit("/", 1)[-1] for r in results), results

    def test_the_url_is_the_abstract_page_and_not_the_pdf(self):
        """The feed carries **two** links per entry and both are https.

        Answering `rel="related"` instead of `rel="alternate"` yields
        `https://arxiv.org/pdf/2412.10418v2` - measured, by running the mutation -
        and a scheme-and-suffix assertion cannot tell the two apart, because both
        are https and both end in `v2`. It matters because these URLs are handed
        to a text reader: `webtext.retrieve` on a PDF returns no readable text,
        so a paper list would be a list of links that each fail when followed.
        """
        results, _ = W._parse_arxiv(_cap())
        for r in results:
            assert "/abs/" in r.url, r.url
            assert "/pdf/" not in r.url, r.url

    def test_the_source_is_arxiv_not_the_generic_one(self):
        results, _ = W._parse_arxiv(_cap())
        assert {r.source for r in results} == {"arXiv"}

    def test_the_description_carries_year_category_and_authors(self):
        """The facts that decide whether a paper is worth reading."""
        results, _ = W._parse_arxiv(_cap())
        first = results[0].description
        assert "cs." in first, first           # the arXiv category
        assert " · " in first, first           # the separator between them
        assert " — " in first, first           # then the abstract
        assert first[:4].isdigit(), first      # the year leads

    def test_the_description_is_bounded(self):
        results, _ = W._parse_arxiv(_cap())
        for r in results:
            assert len(r.description) <= W._ARXIV_DESC_CHARS + 3, len(r.description)

    def test_an_empty_feed_is_a_reason_not_a_crash(self):
        results, why = W._parse_arxiv("")
        assert results == []
        assert "empty" in why

    def test_a_document_that_is_not_xml_is_a_reason_not_a_crash(self):
        results, why = W._parse_arxiv(b"<feed><entry>")
        assert results == []
        assert "XML" in why

    def test_the_parser_does_not_assume_rss(self):
        """arXiv is Atom: `<entry>` under the Atom namespace, not `<item>`.

        A parser that reached for `item` the way the Google News one does would
        return zero results from a perfectly good feed, which reads as "arXiv
        has no paper matching this" - the confident wrong answer.
        """
        assert W._parse_arxiv(b"<rss><channel><item><title>x</title></item></channel></rss>")[0] == []
        assert len(W._parse_arxiv(_cap())[0]) == 3


class TestTheQueryBuilder:
    def test_a_phrase_of_ordinary_words_is_and_ed_not_quoted(self):
        """Measured: quoting the whole phrase gives **1** hit, AND-ing gives
        **1,024** with every top result on topic."""
        assert W.arxiv_query("small language models tool use") == (
            "ti:small AND ti:language AND ti:models AND ti:tool")

    def test_the_scope_is_the_title_by_default(self):
        """Measured: `ti:` gave 107 on-topic hits where `all:` gave 2,365 topped
        by a paper about NER taggers."""
        assert W.arxiv_query("constrained decoding") == "ti:constrained AND ti:decoding"
        assert W.arxiv_query("constrained decoding", scope="all") == (
            "all:constrained AND all:decoding")

    def test_framing_words_are_dropped(self):
        """Measured: `all:what AND all:new AND all:in AND all:quantized AND
        all:inference` returns **0** while `all:quantized AND all:inference`
        returns 2,954. No paper contains all five words, and requiring all five
        is what a question sounds like but not what it means."""
        built = W.arxiv_query("what's new in quantized inference?", scope="all")
        assert built == "all:quantized AND all:inference"
        for word in ("what", "whats", "in", "new"):
            assert word not in _terms(built), word

    def test_a_contraction_is_its_stem(self):
        """`what's` is `what` with a possessive; treating it as a content word is
        what let a greeting reach the API intact."""
        assert "what" not in W.arxiv_query("what's the deal", scope="all")
        assert W.arxiv_query("what's the deal", scope="all") == "all:deal"

    def test_a_greeting_is_not_a_content_word(self):
        """`hi` is matched by arXiv's full-text index against astrophysics -
        measured top result *"Cool Outflows and HI absorbers with SKA"*. It is
        in the list for that reason and not for tidiness."""
        assert "hi" not in W.arxiv_query("hi there", scope="all")

    def test_trailing_punctuation_is_stripped(self):
        """Measured: `all:inference` returns 2,954 and `all:inference?` returns
        **0**. One question mark silently empties the result set."""
        assert W.arxiv_query("quantized inference?", scope="all") == (
            "all:quantized AND all:inference")

    def test_lower_case_and_is_not_the_operator(self):
        """The operators are upper-case, so `and` in "cats and dogs" is a word.

        Tested against the original string and never an upper-cased copy: the
        uppercase test *manufactures* the operator, so every question containing
        the word "and" was passed through to arXiv as a boolean query and
        answered with a disjunction - 406,821 hits topped by a paper about HI
        absorbers.
        """
        assert W.arxiv_query("cats and dogs") == "ti:cats AND ti:dogs"
        assert W.arxiv_query("cats AND dogs") == "cats AND dogs"

    def test_a_query_already_in_arxiv_syntax_passes_through_unchanged(self):
        for q in ('ti:"model merging"', "quantization AND all:llama",
                  "au:hinton", "cat:cs.CL"):
            assert W.arxiv_query(q) == q

    def test_a_pass_through_is_the_same_at_both_scopes(self):
        """Which is what makes it a pass-through: there is nothing to widen, so
        the second request would be the identical question sent twice."""
        assert W.arxiv_query('ti:"model merging"', "ti") == W.arxiv_query(
            'ti:"model merging"', "all")

    def test_a_phrase_the_caller_quoted_is_kept_as_one_term(self):
        assert W.arxiv_query('"model merging" small') == 'ti:"model merging" AND ti:small'

    def test_arxiv_reserved_characters_never_reach_a_term(self):
        """A term carrying `:` is a field name, `"` opens a phrase and a paren
        opens a group, so one reaching arXiv changes what was asked.

        Non-stopword words, so the built query is never empty and the terms are
        actually there to inspect; and the scope prefix is removed first, since
        `all:` carries a colon of its own and a whole-query assertion would fail
        on it while telling you nothing about the terms.
        """
        for ch in ':()\\"':
            built = W.arxiv_query("quantization%sgguf" % ch, scope="all")
            terms = _terms(built)
            assert terms, (ch, built)
            assert all(ch not in term for term in terms), (ch, built)

    def test_an_empty_query_is_empty_not_a_bare_scope(self):
        assert W.arxiv_query("") == ""
        assert W.arxiv_query(None) == ""
        assert W.arxiv_query("   ") == ""


class TestTheLadder:
    """`ti:` first, and `all:` only when it found nothing.

    The ordering is the argument for it: a title is *part of* the full text, so
    `all:` can only return a superset, which makes the second request a widening
    rather than a re-ask. Measured, it is also the difference between an answer
    and none: `ti:"llama inference" AND ti:cpu` returns 0 and
    `all:llama AND all:inference AND all:cpu` returns 53, all on topic.
    """

        #: The two questions "llama inference cpu" actually turns into, **asked of
    #: the module's own builder** rather than typed here. The first version
    #: keyed its scripted answers on `ti:llama` and `all:llama`, which are not
    #: the queries either - so every assertion was reading a question the module
    #: never sent, and the failure pointed at the ladder rather than at the test.
    NARROW = W.arxiv_query("llama inference cpu", scope="ti")
    WIDE = W.arxiv_query("llama inference cpu", scope="all")

    def _run_with(self, monkeypatch, answers, empty=None):
        """Drive `arxiv_search` over a scripted `search_query` -> body map.

        Returns the whole `(results, reason, notes)` plus the questions asked,
        so a test can assert on what was *sent* as well as what came back -
        a ladder whose second request never fires passes every result-only
        assertion.

        **`empty` defaults to the real zero-result capture, not an empty body.**
        Measured: a query matching nothing answers **HTTP 200 with a well-formed
        feed** carrying `totalResults 0` and no `<entry>`. An empty body is a
        different event - a response that was not a feed at all - and the module
        refuses it as unreadable rather than reporting "no such paper". Scripting
        the empty body instead would have tested the wrong one of the two, and
        every ladder assertion would have failed for an unrelated reason.
        """
        asked = []

        def fake_fetch(url, params, component):
            asked.append(params["search_query"])
            return answers.get(params["search_query"],
                               _capempty() if empty is None else empty), ""

        monkeypatch.setattr(W, "gate", lambda what: None)
        monkeypatch.setattr(W, "_fetch_feed", fake_fetch)
        return W.arxiv_search("llama inference cpu"), asked

    def test_the_narrow_scope_is_tried_first(self, monkeypatch):
        (found, why, _), asked = self._run_with(monkeypatch, {self.NARROW: _cap()})
        assert asked == [self.NARROW], asked
        assert why == ""
        assert len(found) == 3

    def test_the_widening_runs_only_when_the_first_found_nothing(self, monkeypatch):
        (found, why, _), asked = self._run_with(monkeypatch, {self.WIDE: _cap()})
        assert asked == [self.NARROW, self.WIDE], asked
        assert len(found) == 3
        # The control in the other direction: an answer from the narrow scope
        # must NOT also ask the wide one, or the ladder is a retry rather than a
        # widening and costs a request on every successful search.
        (_f2, _, _), asked2 = self._run_with(monkeypatch, {self.NARROW: _cap()})
        assert asked2 == [self.NARROW], asked2

    def test_a_pass_through_query_is_asked_once(self, monkeypatch):
        """It carries its own field, so there is nothing to widen and a second
        request would be the identical question sent twice - which arXiv's own
        API terms ask callers not to do."""
        asked = []
        monkeypatch.setattr(W, "gate", lambda what: None)

        def fake_fetch(url, params, component):
            asked.append(params["search_query"])
            return _cap(), ""

        monkeypatch.setattr(W, "_fetch_feed", fake_fetch)
        results, why, _ = W.arxiv_search('ti:"model merging"')
        assert asked == ['ti:"model merging"'], asked
        assert why == ""
        assert len(results) == 3

    def test_a_pass_through_matching_nothing_is_also_asked_once(self, monkeypatch):
        """**The control the mutation run found missing.**

        With an early return on the first hit, the widening only shows itself
        when the *first* question finds nothing - so the test above never
        reaches the second iteration, and a change that makes `widened` the
        wide scope unconditionally is invisible to it. Here the scripted answer
        is the real zero-result capture, so the loop runs to its end and the
        question count is the only thing that can show a duplicate.
        """
        asked = []
        monkeypatch.setattr(W, "gate", lambda what: None)

        def fake_fetch(url, params, component):
            asked.append(params["search_query"])
            return _capempty(), ""

        monkeypatch.setattr(W, "_fetch_feed", fake_fetch)
        _results, why, _ = W.arxiv_search('ti:"model merging"')
        assert asked == ['ti:"model merging"'], asked
        # And the refusal names it once, not twice.
        assert why.count('ti:"model merging"') == 1, why

    def test_an_empty_answer_quotes_both_questions_it_asked(self, monkeypatch):
        """A search that returns nothing with no way to see what was asked is the
        least actionable result there is."""
        (results, why, _), asked = self._run_with(monkeypatch, {})
        assert results == []
        assert asked == [self.NARROW, self.WIDE], asked
        assert self.NARROW in why and self.WIDE in why, why

    def test_the_refusal_says_preprint_and_points_at_the_web(self, monkeypatch):
        (_results, why, _), _asked = self._run_with(monkeypatch, {})
        assert "preprint" in why
        assert "web_search" in why

    def test_a_response_that_is_not_a_feed_is_not_reported_as_no_papers(self, monkeypatch):
        """The distinction the zero-result capture exists to make sharp: a
        well-formed feed with no entries is an answer, and anything else is not.
        Reading an unreadable response as "arXiv has no such paper" is the
        confident wrong answer."""
        (results, why, _), _asked = self._run_with(monkeypatch, {}, empty=b"")
        assert results == []
        assert "no claim is made" in why
        assert "preprint" not in why


class TestRefusals:
    def test_the_web_sense_gate_is_consulted_first(self, monkeypatch):
        """A query leaves the machine. Measured: with the web sense off this
        returns the gate's own sentence, and no request is made."""
        sent = []
        monkeypatch.setattr(W, "_fetch_feed", lambda *a: (sent.append(a), (b"", ""))[1])
        results, why, _ = W.arxiv_search("anything")
        assert results == []
        assert "not available" in why
        assert sent == [], "a refused search still made a request"

    def test_a_question_with_no_content_words_is_refused_before_any_request(self, monkeypatch):
        """Measured: falling back to the raw query returned **83,903** hits
        topped by an unrelated binary-neutron-star paper. Presenting that as
        "papers matching your question" is the confident wrong answer, so the
        builder says no instead."""
        sent = []
        monkeypatch.setattr(W, "gate", lambda what: None)
        monkeypatch.setattr(W, "_fetch_feed", lambda *a: (sent.append(a), (b"", ""))[1])
        results, why, _ = W.arxiv_search("what is the")
        assert results == []
        assert "no words" in why
        assert sent == [], "a question with nothing to search for still made a request"

    def test_an_unreachable_archive_is_not_absence_of_papers(self, monkeypatch):
        monkeypatch.setattr(W, "gate", lambda what: None)
        monkeypatch.setattr(W, "_fetch_feed", lambda *a: (b"", "ConnectTimeout"))
        results, why, _ = W.arxiv_search("quantization")
        assert results == []
        assert "unreachable" in why
        assert "ConnectTimeout" in why

    def test_an_unreadable_answer_is_not_turned_into_a_verdict(self, monkeypatch):
        """HTTP 200 with a body we cannot parse is not "arXiv found nothing"."""
        monkeypatch.setattr(W, "gate", lambda what: None)
        monkeypatch.setattr(W, "_fetch_feed", lambda *a: (b"<feed><entry>", ""))
        results, why, _ = W.arxiv_search("quantization")
        assert results == []
        assert "no claim is made" in why


class TestTheSkillWiring:
    """The skill is what a model actually reaches, so it is what is driven."""

    def _handler(self):
        from shani_chronoa.skills import discover_skills
        _schemas, handlers = discover_skills()
        assert "web_search" in handlers, "web_search did not register"
        return handlers["web_search"]

    def test_the_schema_offers_source_with_arxiv_in_it(self):
        from shani_chronoa.skills import discover_skills
        schemas, _ = discover_skills()
        schema = [s for s in schemas
                  if s["function"]["name"] == "web_search"][0]["function"]
        source = schema["parameters"]["properties"]["source"]
        assert source["enum"] == ["web", "arxiv"]
        assert "arxiv" in schema["description"]

    def test_the_handler_is_importable_by_name(self):
        """The loader dispatches a non-local skill by interpolating the
        handler's `__name__` into an import, so a name that is not an identifier
        is a skill that is advertised and fails on every call."""
        assert self._handler().__name__.isidentifier()

    def test_source_arxiv_reaches_the_arxiv_search(self, monkeypatch):
        seen = []
        monkeypatch.setattr(W, "arxiv_search",
                            lambda q: (seen.append(q) or ([], "refused for a reason", [])))
        from shani_chronoa.config import ChronoaConfig
        monkeypatch.setattr(ChronoaConfig, "sense_allowed", lambda self, s: True)
        out = self._handler()({"query": "quantization", "source": "arxiv"})
        assert seen == ["quantization"]
        assert out == "refused for a reason", out

    def test_the_arxiv_answer_says_what_arxiv_is(self, monkeypatch):
        """Twelve titles with no such sentence are twelve things a reader may
        cite as though they were peer-reviewed journal papers."""
        monkeypatch.setattr(W, "arxiv_search",
                            lambda q: ([W.Result("T", "https://arxiv.org/abs/1", "d", "arXiv")], "", []))
        from shani_chronoa.config import ChronoaConfig
        monkeypatch.setattr(ChronoaConfig, "sense_allowed", lambda self, s: True)
        out = self._handler()({"query": "quantization", "source": "arxiv"})
        assert "preprint" in out
        assert "not peer-reviewed" in out

    def test_a_url_is_refused_for_arxiv_by_name(self, monkeypatch):
        """arXiv searches papers, not pages. Searching for a URL's characters
        instead is the confident wrong answer this skill's history is about."""
        called = []
        monkeypatch.setattr(W, "arxiv_search", lambda q: called.append(q) or ([], "", []))
        from shani_chronoa.config import ChronoaConfig
        monkeypatch.setattr(ChronoaConfig, "sense_allowed", lambda self, s: True)
        out = self._handler()({"query": "q", "url": "https://arxiv.org/abs/2403.01273",
                               "source": "arxiv"})
        assert called == [], "a URL was sent to arXiv as if it were a query"
        assert "papers, not pages" in out

    def test_the_default_path_is_untouched(self, monkeypatch):
        """No `source` means the web branch, which is the measured chain. If this
        passes for the wrong reason - because both branches refused - the
        arXiv-specific assertions above would not catch it, so the web fetch is
        driven and its result is asserted."""
        from shani_chronoa.config import ChronoaConfig
        monkeypatch.setattr(ChronoaConfig, "sense_allowed", lambda self, s: True)
        monkeypatch.setattr("shani_chronoa.skills.web_search.retrieve",
                            lambda target: "PAGE-BODY")
        monkeypatch.setattr("shani_chronoa.skills.web_search.render",
                            lambda page: "PAGE-BODY")
        out = self._handler()({"query": "arch linux"})
        assert out == "PAGE-BODY"
        assert "from arXiv" not in out

    def test_a_source_of_web_is_the_default_path_explicitly(self, monkeypatch):
        """`source='web'` and no source must be the same branch. If this were
        vacuous the assertion above would still pass, so the URL is checked too -
        the web branch honours a URL and the arXiv branch refuses one."""
        from shani_chronoa.config import ChronoaConfig
        monkeypatch.setattr(ChronoaConfig, "sense_allowed", lambda self, s: True)
        monkeypatch.setattr("shani_chronoa.skills.web_search.retrieve",
                            lambda target: target)
        monkeypatch.setattr("shani_chronoa.skills.web_search.render",
                            lambda page: page)
        out = self._handler()({"query": "q", "url": "https://example.org/", "source": "web"})
        assert out == "https://example.org/"

class TestTheAbstractIsNotTruncated:
    """A capped abstract is a search that cannot say what it found.

    Measured: a sweep aimed at *why three fine-tunes break tool calling* returned
    twelve results whose `description` was capped at 420 characters, and the one
    paper naming the answer - **arXiv:2604.06066, "The Alignment Tax of
    Constrained Decoding"** - was unreadable through the tool that had found it.
    The field existed in arXiv's feed and was cut before it reached the caller.

    So the facts stay capped for the list, and the paper's own words travel
    beside them untruncated. Tests run against the **real captures** this file
    already holds, so no network is involved and a real feed's shape decides.
    """

    def _capture(self):
        # The same real capture every other test in this file uses. The first
        # version looked in `tools/` and skipped - a test that skips is a test
        # that reports green while proving nothing, which is this repository's
        # most-recorded failure and was caught here by reading the skip count.
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        path = root / "tests" / "fixtures" / "search" / "arxiv.atom"
        if not path.exists():
            import pytest
            pytest.skip("no arxiv capture in tests/fixtures/search/")
        return path.read_bytes()

    def test_the_abstract_travels_whole(self):
        from shani_chronoa import websearch as W
        found, why = W._parse_arxiv(self._capture())
        assert found, why
        for result in found:
            if not result.abstract:
                continue
            # The description is what the list shows and is capped; the abstract
            # is the paper. The first can be strictly shorter, and the second
            # must not be a cut of the first.
            assert "..." not in result.abstract[-3:] or \
                result.abstract.endswith("...") is False, (
                f"{result.url}: the abstract itself ends in an ellipsis")
        whole = [r for r in found if r.abstract]
        assert whole, "no entry carried an abstract at all"
        assert max(len(r.abstract) for r in whole) > 420, (
            "the longest abstract is inside the description's own cap, so the "
            "field is not carrying anything the cap did not already have")

    def test_the_capped_description_is_not_the_abstract(self):
        """Both fields exist, and they are not the same string.

        A single field serving both jobs is why the truncation was invisible: the
        module's docstring called the tail the abstract and nobody could read
        past it. Two fields make the distinction checkable.
        """
        from shani_chronoa import websearch as W
        found, why = W._parse_arxiv(self._capture())
        with_abstract = [r for r in found if r.abstract]
        assert with_abstract, why
        # The cap is `_ARXIV_DESC_CHARS` plus the three characters of ellipsis,
        # so the bound is the constant + 3 and not the bare constant - the first
        # version asserted 420 and a real entry measured 422.
        from shani_chronoa import websearch as W
        for result in with_abstract:
            assert len(result.description) <= W._ARXIV_DESC_CHARS + 3, (
                f"{result.url}: the description is {len(result.description)} "
                f"chars, past the {W._ARXIV_DESC_CHARS} cap it documents")
            assert result.abstract not in result.description, (
                f"{result.url}: the abstract is entirely inside the capped "
                f"description, so this capture cannot show the difference")

    def test_format_results_shows_the_abstract(self):
        """A field that is carried and never shown is the dead-code class one
        level below a module - the abstract must reach the printed answer."""
        from shani_chronoa import websearch as W
        result = W.Result("A paper", "https://arxiv.org/abs/1", "short facts",
                          "arXiv", "the whole abstract, long enough to matter " * 6)
        text = W.format_results("q", [result])
        assert result.abstract in text, (
            "the untruncated abstract is not in the formatted output")
        assert "abstract:" in text

    def test_a_source_with_no_abstract_still_formats(self):
        """The field defaults, so a provider that gives none is not broken by
        one that does."""
        from shani_chronoa import websearch as W
        result = W.Result("A page", "https://example.org", "its words", "Web")
        assert result.abstract == ""
        text = W.format_results("q", [result])
        assert "abstract:" not in text
        assert "its words" in text
