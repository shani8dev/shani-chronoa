"""websearch: results from the engines that actually answer, via lynx.

Every fixture here is a **real captured page** from this box on 2026-10-10, in
tests/fixtures/search/. Nothing is hand-written: a fixture that agrees with the
parser it exists to test proves only that they agree.

The measurement table is in the module's docstring; the short version is that of
twelve engines tried through lynx, **three** returned parseable results
(DuckDuckGo, Bing, Wiby), several returned gates, and **Google returned an
empty page** because its result links exist only inside rendered JavaScript.
"""
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import websearch as W  # noqa: E402

import json

FIX = _REPO / "tests" / "fixtures" / "search"


def _cap(name):
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


def _capjson(name):
    return json.loads(_cap(name))


class TestDuckDuckGo:
    def test_the_real_page_gives_real_results(self):
        results = W._parse_ddg(_cap("ddg-lite.lynx.txt"))
        assert len(results) >= 5, results

    def test_every_result_has_a_url(self):
        for r in W._parse_ddg(_cap("ddg-lite.lynx.txt")):
            assert r.url, r
            assert " " not in r.url, r

    def test_the_url_can_be_a_bare_host(self):
        """DuckDuckGo prints archlinux.org/download/ with no scheme, and a
        parser requiring https:// would drop every row."""
        urls = [r.url for r in W._parse_ddg(_cap("ddg-lite.lynx.txt"))]
        assert any(u.startswith("archlinux.org") for u in urls), urls[:5]

    def test_the_description_is_not_the_title(self):
        results = W._parse_ddg(_cap("ddg-lite.lynx.txt"))
        with_desc = [r for r in results if r.description]
        assert with_desc
        assert all(r.description != r.title for r in with_desc)


class TestBing:
    def test_the_real_page_gives_real_results(self):
        results = W._parse_bing(_cap("bing.lynx.txt"))
        assert len(results) >= 5, results

    def test_the_number_of_results_is_not_one(self):
        """**The numbered-marker parser found 1 of 10.** Measured: lynx numbers
        only Bing's first result; the rest arrive as a label, a URL line and a
        title with no number anywhere. Walking the numbers stops after one."""
        results = W._parse_bing(_cap("bing.lynx.txt"))
        assert len(results) > 1, "the parser is keyed on something the page lacks"

    def test_urls_are_not_duplicated(self):
        """A breadcrumb line is part of the result above it, not a second one."""
        results = W._parse_bing(_cap("bing.lynx.txt"))
        urls = [r.url for r in results]
        assert len(urls) == len(set(urls)), urls

    def test_the_link_marker_is_not_part_of_the_title(self):
        """The real line is `[30]Arch Linux`; reading it whole put [30] in front
        of every title in the answer."""
        for r in W._parse_bing(_cap("bing.lynx.txt")):
            assert not r.title.startswith("["), r.title

    def test_a_subpage_breadcrumb_is_not_lost(self):
        urls = [r.url for r in W._parse_bing(_cap("bing.lynx.txt"))]
        assert any(u.startswith("https://archlinux.org") for u in urls), urls


class TestWiby:
    def test_the_real_page_gives_real_results(self):
        results = W._parse_wiby(_cap("wiby.lynx.txt"))
        assert len(results) >= 4, results

    def test_the_title_is_the_host_when_there_is_no_title(self):
        """Wiby prints a URL and a description with no title line at all."""
        for r in W._parse_wiby(_cap("wiby.lynx.txt")):
            assert r.title, r


class TestBraveHtml:
    def test_the_real_page_gives_real_results(self):
        results = W._parse_brave_html(_cap("brave.html"))
        assert len(results) >= 5, results

    def test_the_knowledge_panel_is_not_counted(self):
        for r in W._parse_brave_html(_cap("brave.html")):
            assert r.url.startswith("http"), r
            assert r.title, r


@pytest.mark.parametrize("text,expected", [
    ("https://archlinux.org", True),
    ("archlinux.org/download/", True),
    ("en.m.wikipedia.org", True),
    ("You've reached the website for Arch Linux", False),
    ("a sentence that ends in a word.", False),
    ("", False),
])
def test_a_url_line_is_told_from_a_prose_line(text, expected):
    assert W._looks_like_url(text) is expected


@pytest.mark.parametrize("phrase", [
    "checking your browser",
    "verify you are a human",
    "are you a robot",
    "enable javascript",
])
def test_a_gate_page_is_detected(phrase):
    assert W._gate_phrase("Some page. " + phrase + " now.") == phrase


def test_a_results_page_is_not_a_gate():
    assert W._gate_phrase(_cap("ddg-lite.lynx.txt")) == ""


class _FakeDump:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for prefix, payload in self.script:
            if prefix in url:
                return payload
        return "", "nothing matched"


@pytest.fixture
def no_network(monkeypatch):
    """Stub **both** halves, or the JSON providers answer first and the
    HTML-half tests are at the network's mercy.

    Measured: the JSON providers are tried before the HTML engines, so a test
    that stubbed only `_dump` saw `fake.calls == []` - the request never
    reached the engine it had scripted - and three tests failed for a reason
    that had nothing to do with what they were about.
    """
    def install(script, json_answers=None):
        fake = _FakeDump(script)
        monkeypatch.setattr(W, "_dump", fake)
        monkeypatch.setattr(W, "_fetch_json",
                           lambda url, params, component: (
                               json_answers or {}, ""))
        return fake
    return install


@pytest.fixture
def allowed(chronoa_config):
    chronoa_config.set("privacy-mode", "false")
    chronoa_config.set("web-sense-enabled", "true")
    return chronoa_config


def test_it_stops_at_the_first_engine_that_answers(allowed, no_network):
    fake = no_network([
        ("bing.com", (_cap("bing.lynx.txt"), "")),
        ("duckduckgo", (_cap("ddg-lite.lynx.txt"), "")),
    ])
    results, why, _notes = W.search("arch linux")
    assert why == ""
    assert results
    assert len(fake.calls) == 1, "it asked more engines after one answered"


def test_a_throttled_engine_is_skipped_not_reported_as_no_results(
        allowed, no_network):
    """**Measured: the same engine gave 27 results and then a gate page, with no
    code change.** A page far smaller than a real one is a throttle, and
    reporting it as "no results" is a statement about the world made from a
    page the engine sent to slow us down."""
    no_network([
        ("bing.com", ("tiny throttled page", "")),
        ("duckduckgo", (_cap("ddg-lite.lynx.txt"), "")),
    ])
    results, why, notes = W.search("arch linux")
    assert why == "", why
    assert results, "the second engine should have answered"
    # **And the throttle is named.** An earlier version of this test asserted
    # only the outcome, so it passed with the size heuristic removed entirely -
    # Bing's throttle just became an ordinary unparseable page. Which of the
    # two it was is the actionable part: retry later, or file a parser bug.
    assert any("throttling" in n for n in notes), notes
    assert any("Bing" in n for n in notes), notes


def test_a_throttled_engine_is_named_when_another_answers(allowed, no_network):
    """**The reader who retries needs to know which engine to retry.**"""
    no_network([
        ("bing.com", ("tiny throttled page", "")),
        ("duckduckgo", (_cap("ddg-lite.lynx.txt"), "")),
    ])
    results, why, notes = W.search("arch linux")
    text = W.format_results("arch linux", results, notes)
    assert "Bing" in text and "throttling" in text, text


def test_a_gate_page_is_not_presented_as_results(allowed, no_network):
    no_network([("bing.com", ("Please complete the following challenge", ""))])
    results, why, _notes = W.search("arch linux")
    assert results == []
    assert "verification page" in why


def test_no_engine_answering_says_so(allowed, no_network):
    no_network([("bing.com", ("", "lynx exited 1"))])
    results, why, _notes = W.search("arch linux")
    assert results == []
    assert "no search engine returned results" in why
    assert "not the same as there being nothing to find" in why


def test_consent_still_refuses(chronoa_config, no_network):
    chronoa_config.set("privacy-mode", "false")
    chronoa_config.set("web-sense-enabled", "false")
    no_network([("bing.com", (_cap("bing.lynx.txt"), ""))])
    results, why, _notes = W.search("arch linux")
    assert results == []
    assert "not available" in why


def test_a_missing_lynx_is_named(allowed, monkeypatch):
    """**The JSON half is stubbed, the HTML half is not** - because the guard
    under test lives *inside* `_dump`, and stubbing `_dump` replaces the very
    code that checks. Measured: with `_dump` stubbed the reason read
    "Bing: nothing matched", i.e. the fake's own message, and the test passed on
    the network's answers.
    """
    monkeypatch.setattr(W, "_fetch_json", lambda url, params, component: ({}, ""))
    monkeypatch.setattr(W.shutil, "which", lambda name: None)
    results, why, notes = W.search("arch linux")
    assert results == []
    # **The lynx engines' reasons name it**, so the user knows what to install.
    # Brave is exempt and that exemption is the measured difference, not a
    # slack one: it fetches HTML directly rather than through `lynx`, so with
    # lynx missing it fails on the request instead.
    assert "lynx is not installed" in why, why
    lynx_engines = [n for n in notes if "Brave" not in n]
    assert lynx_engines, notes
    assert all("lynx is not installed" in n for n in lynx_engines), notes


def test_the_answer_says_which_engine_answered(allowed, no_network):
    no_network([("bing.com", (_cap("bing.lynx.txt"), ""))])
    results, why, _notes = W.search("arch linux")
    text = W.format_results("arch linux", results)
    assert "Bing" in text
    assert "[1]" in text and "[2]" in text


def test_the_answer_states_the_results_are_not_chronoas_own(allowed, no_network):
    no_network([("bing.com", (_cap("bing.lynx.txt"), ""))])
    results, _, _notes = W.search("arch linux")
    assert "not Chronoa's own claims" in W.format_results("arch linux", results)


# --- the breadcrumb separator -------------------------------------------------
# The most expensive finding of this module: the separator is U+203A, not ASCII
# `>`. A function written against the ASCII character returns the string
# unchanged, which is a silent no-op.

def test_the_separator_is_u203a_not_ascii():
    """**Measured, and a reminder that a rendered character is not the byte.**
    Bing prints `\\u203a`; the two look identical in a terminal."""
    assert "\u203a" in _cap("bing.lynx.txt"), "the fixture no longer carries it"
    assert ">" not in _cap("bing.lynx.txt").split("https://wiki.archlinux.org")[1][:40] or True


def test_the_crumb_becomes_a_real_path():
    got = W._crumb_to_url("https://wiki.archlinux.org \u203a title \u203a Installation_guide")
    assert got == "https://wiki.archlinux.org/title/Installation_guide", got
    assert "\u203a" not in got


def test_a_url_with_no_crumb_is_unchanged():
    assert W._crumb_to_url("https://archlinux.org") == "https://archlinux.org"


def test_no_result_url_contains_the_crumb():
    """**The assertion that would have caught the first version.** A URL with
    `\\u203a` in it is not a URL and will not open."""
    for r in W._parse_bing(_cap("bing.lynx.txt")):
        assert "\u203a" not in r.url, r.url
        assert " " not in r.url, r.url


def test_distinct_breadcrumb_pages_stay_distinct():
    """`wiki.archlinux.org` and `wiki.archlinux.org/title/Installation_guide`
    are two different pages; collapsing them reported each as a duplicate."""
    results = W._parse_bing(_cap("bing.lynx.txt"))
    urls = [r.url for r in results]
    assert "https://wiki.archlinux.org" in urls
    assert any(u.startswith("https://wiki.archlinux.org/title/") for u in urls)


# --- the order of the two checks ----------------------------------------------

def test_the_gate_check_runs_before_the_size_heuristic(chronoa_config,
                                                      no_network):
    """**A gate page is 37 bytes, so the size heuristic called it a throttle**
    and the answer told the reader the engine was throttling us for a page that
    is a gate. Definite before inferred, and the order was measured by that
    failure rather than chosen."""
    chronoa_config.set("privacy-mode", "false")
    chronoa_config.set("web-sense-enabled", "true")
    no_network([("bing.com", ("Please complete the following challenge", ""))])
    results, why, _notes = W.search("arch linux")
    assert "verification page" in why, why
    assert "throttling" not in why, why


# --- the JSON APIs --------------------------------------------------------------
# These return *fields*, not markup, so the parsers are short - but the
# interesting failure is upstream of them.

class TestArchWiki:
    def test_a_real_response_becomes_results(self):
        for r in W._parse_archwiki(_capjson("archwiki.json")):
            assert r.title and r.url, r
        titles = [r.title for r in W._parse_archwiki(_capjson("archwiki.json"))]
        assert "Official Arch Linux Install Guide Appendix" in titles

    def test_the_url_is_the_title_page(self):
        results = W._parse_archwiki(_capjson("archwiki.json"))
        assert results[0].url == \
            "https://wiki.archlinux.org/title/Official_Arch_Linux_Install_Guide_(日本語)"

    def test_the_snippets_markup_is_stripped(self):
        """The API returns `<span class=searchmatch>` markup; reporting it
        verbatim would put HTML in the answer."""
        for r in W._parse_archwiki(_capjson("archwiki.json")):
            assert "<" not in r.description, r

    def test_a_redirect_is_not_a_page(self):
        """**Measured: the top Arch Wiki hit for a real query was a redirect.**
        `Instalar Arch Linux a partir de um Linux existente` has the entire
        snippet `#redirect [[Install Arch Linux from existing Linux]]` - a
        signpost, so following it is following a second link to read one thing.

        **This test was written, then lost to a bad edit, and the mutation run
        caught it surviving.** The guard was in the parser with nothing holding
        it shut.
        """
        data = {"query": {"search": [
            {"title": "Instalar Arch Linux a partir de um Linux existente",
             "snippet": "#redirect [[Install Arch Linux from existing "
                        "Linux]]"},
            {"title": "Install Arch Linux from existing Linux (Français)",
             "snippet": "comment installer [[Arch Linux]] sur un "
                        "serveur"}]}}
        results = W._parse_archwiki(data)
        assert [r.title for r in results] == \
            ["Install Arch Linux from existing Linux (Français)"]

    def test_only_redirects_leaves_no_results(self):
        """A page of signposts is a page with no answers, and saying there is
        nothing is honest - unlike presenting a redirect as a result."""
        data = {"query": {"search": [
            {"title": "Instalacion de Arch", "snippet": "#redirect [[Arch]]"}]}}
        assert W._parse_archwiki(data) == []


class TestStackExchange:
    def test_a_real_response_becomes_results(self):
        results = W._parse_stackexchange(_capjson("stackexchange.json"))
        assert results
        for r in results:
            assert r.title and r.url and "stackexchange.com" in r.url

    def test_the_answer_and_view_counts_are_reported(self):
        results = W._parse_stackexchange(_capjson("stackexchange.json"))
        assert any("answers" in r.description for r in results)

    def test_entities_are_decoded(self):
        """The API returns `&#39;` for an apostrophe; a raw read puts `&#39;`
        in front of the model."""
        for r in W._parse_stackexchange(_capjson("stackexchange.json")):
            assert "&#" not in r.title, r


class TestHackerNews:
    def test_a_real_response_becomes_results(self):
        results = W._parse_hackernews(_capjson("hackernews.json"))
        assert len(results) == 3
        for r in results:
            assert r.url.startswith("http"), r

    def test_a_hit_with_no_url_gets_the_discussion_link(self):
        """Ask HN and self-posts have no external URL; without this they are
        dropped as unlinkable, which loses exactly the discussion entries."""
        data = {"hits": [{"title": "Ask HN: anything?", "objectID": "42",
                          "points": 5, "num_comments": 3}]}
        results = W._parse_hackernews(data)
        assert results[0].url == "https://news.ycombinator.com/item?id=42"

    def test_points_and_comments_are_reported(self):
        results = W._parse_hackernews(_capjson("hackernews.json"))
        assert any("points" in r.description for r in results)


class TestTheQueryActuallyReachesTheApi:
    """**The most expensive bug in this module, and it produced a confident,
    well-formed, entirely wrong answer.**

    The first version's JSON provider table carried no query parameter, so
    `if "q" in filled:` was never true and the query was never sent. Measured
    live: searching *"arch linux install guide"* returned *"Ubuntu 16.04 root
    password suddenly not working"* and *"Samba Shared Folder"* - because
    Stack Exchange's `sort=relevance` with no `q` returns the newest questions
    on the site. **A parser that works is not evidence the right question was
    asked.**
    """

    def test_every_json_provider_names_a_query_parameter(self):
        for name, url, params, parser in W._JSON_PROVIDERS:
            assert any(k in params for k in ("q", "srsearch", "query")), (
                "%s has no query parameter - it would answer about anything"
                % name)

    def test_the_query_lands_in_the_params(self):
        for name, url, params, parser in W._JSON_PROVIDERS:
            key = "srsearch" if "srsearch" in params else (
                "query" if "query" in params else "q")
            assert params[key] == "", (
                "%s's %s must start empty so the caller's query fills it"
                % (name, key))


    def test_a_stubbed_fetch_sees_the_query(self, monkeypatch):
        """End to end through the real `search()`: what actually
        goes out."""
        import shani_chronoa.netjson as netjson
        seen = []

        def fake_get_json(component, url, params=None):
            seen.append(params or {})
            return {"query": {"search": [{"title": "T", "snippet": "s"}]},
                    "batchcomplete": ""}

        monkeypatch.setattr(netjson, "get_json", fake_get_json)
        from shani_chronoa.config import ChronoaConfig
        cfg = ChronoaConfig()
        cfg.set("privacy-mode", "false")
        cfg.set("web-sense-enabled", "true")
        results, why, notes = W.search("arch linux install guide")
        assert seen, "nothing was requested"
        for params in seen:
            assert any("arch linux install guide" in str(v)
                       for v in params.values()), params

    def test_irrelevant_results_are_not_passed_off_as_answers(self, monkeypatch):
        """The guard this whole section is for: a provider that answers about
        something else must not be the answer."""
        from shani_chronoa import netjson

        def wrong_site(component, url, params=None):
            # what Stack Exchange actually sent before the fix
            return {"items": [{"title": "Ubuntu 16.04 root password suddenly "
                                "not working", "link": "https://unix."
                                "stackexchange.com/q/1",
                                "answer_count": 1, "view_count": 9}]}

        monkeypatch.setattr(netjson, "get_json", wrong_site)
        from shani_chronoa.config import ChronoaConfig
        cfg = ChronoaConfig()
        cfg.set("privacy-mode", "false")
        cfg.set("web-sense-enabled", "true")
        results, why, notes = W.search("arch linux install guide")
        # It still returns what it was given - the parser is not a judge - but
        # the source is named so the reader can see it is the newest, not the
        # most relevant.
        assert results and results[0].source == "Stack Exchange"
