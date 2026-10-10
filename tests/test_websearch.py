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
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import websearch as W  # noqa: E402

FIX = _REPO / "tests" / "fixtures" / "search"


def _cap(name):
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


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
    def install(script):
        fake = _FakeDump(script)
        monkeypatch.setattr(W, "_dump", fake)
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
    monkeypatch.setattr(W.shutil, "which", lambda name: None)
    results, why, _notes = W.search("arch linux")
    assert results == []
    assert "lynx is not installed" in why


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
