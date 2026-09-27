"""Tests: web retrieval returns real page text, and the consent gate holds.

No network. `httpx.Client` in `shani_chronoa.webtext` is replaced with a
factory that injects an `httpx.MockTransport` - the same technique
`conftest.py`'s `mock_httpx` fixture already uses for `cloud_llm`.

The point these lock down is the one the old skill failed at: a tool result
must carry the page's text, not a claim that a search happened. Each test
asserts on the extracted *content*, never on wording, so the messages can be
reworded freely.
"""

import httpx
import pytest

from shani_chronoa.senses.web import SENSES
from shani_chronoa.webtext import MAX_RESPONSE_BYTES, MAX_TEXT_CHARS, retrieve

CANNED_HTML = """<!doctype html>
<html>
  <head>
    <title>Shanios release notes</title>
    <script>var tracker = "ignore this script text";</script>
    <style>body { color: red; }</style>
  </head>
  <body>
    <h1>Shanios 1.2 released</h1>
    <p>The blue-green deployment now survives a failed health check.</p>
    <p>Rollback keeps the previous snapshot for 14 days.</p>
    <nav><a href="/x">navigation noise</a></nav>
  </body>
</html>
"""


# Captured before any monkeypatching: `webtext.httpx` *is* the httpx
# module, so a factory that called `httpx.Client` would recurse into itself.
_RealClient = httpx.Client


def _install_transport(monkeypatch, handler):
    """Point every httpx.Client built in webtext at a MockTransport."""
    transport = httpx.MockTransport(handler)

    def _factory(**kwargs):
        kwargs.pop("transport", None)
        return _RealClient(**kwargs, transport=transport)

    monkeypatch.setattr("shani_chronoa.webtext.httpx.Client", _factory)
    return transport


def _allow_web(chronoa_config):
    """Open both consent gates: the sense key and privacy mode."""
    chronoa_config.set("privacy-mode", "false")
    chronoa_config.set("web-sense-enabled", "true")
    assert chronoa_config.web_sense_enabled


@pytest.fixture
def canned_page(monkeypatch):
    """Serve CANNED_HTML for any request, recording what was asked for."""
    requested: list = []

    def _handler(request):
        requested.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=CANNED_HTML,
        )

    _install_transport(monkeypatch, _handler)
    return requested


class TestSkillReturnsRealContent:
    """The tool result must contain the page's text."""

    def test_skill_result_contains_page_body_text(self, chronoa_config, canned_page):
        # Given: web retrieval is permitted and a page is reachable
        _allow_web(chronoa_config)
        from shani_chronoa.skills.web_search import _run
        # When: the model asks for that page
        result = _run({"query": "shanios release notes", "url": "https://example.org/notes"})
        # Then: the actual sentences are in the result
        assert "blue-green deployment now survives a failed health check" in result
        assert "Rollback keeps the previous snapshot for 14 days" in result
        assert canned_page == ["https://example.org/notes"]

    def test_skill_result_drops_script_and_style_text(self, chronoa_config, canned_page):
        # Given: the same page, which carries script and style content
        _allow_web(chronoa_config)
        from shani_chronoa.skills.web_search import _run
        # When: it is fetched
        result = _run({"query": "x", "url": "https://example.org/notes"})
        # Then: markup and code are not passed to the model as prose
        assert "ignore this script text" not in result
        assert "color: red" not in result
        assert "<" not in result

    def test_skill_result_names_the_source_url_and_title(self, chronoa_config, canned_page):
        # Given: a permitted fetch
        _allow_web(chronoa_config)
        from shani_chronoa.skills.web_search import _run
        # When: the page is read
        result = _run({"query": "x", "url": "https://example.org/notes"})
        # Then: provenance is stated, so a later turn can tell what was read
        assert "https://example.org/notes" in result
        assert "Shanios release notes" in result

    def test_query_alone_hits_the_search_endpoint(self, chronoa_config, canned_page):
        # Given: only a query, no URL
        _allow_web(chronoa_config)
        from shani_chronoa.skills.web_search import _run
        # When: the skill runs
        result = _run({"query": "btrfs snapshot layout"})
        # Then: the search endpoint was actually requested, quoted
        assert canned_page == ["https://lite.duckduckgo.com/lite/?q=btrfs%20snapshot%20layout"]
        # And the results page's text, not a fixed string, comes back
        assert "blue-green deployment" in result
        assert "Searching the web for" not in result

    def test_no_browser_is_ever_launched(self, chronoa_config, canned_page, mock_launch_uri):
        # Given: every gate open and a reachable page
        _allow_web(chronoa_config)
        from shani_chronoa.skills.web_search import _run
        # When: the skill retrieves
        _run({"query": "hello", "url": "https://example.org/notes"})
        # Then: retrieval replaced the browser launch, not augmented it
        assert mock_launch_uri == []


class TestConsentGate:
    """Nothing reaches the network unless both gates are open."""

    def test_refused_when_web_sense_is_off(self, chronoa_config, canned_page):
        # Given: privacy mode off but the web sense left at its default
        chronoa_config.set("privacy-mode", "false")
        assert not chronoa_config.web_sense_enabled
        from shani_chronoa.skills.web_search import _run
        # When: the skill is invoked
        result = _run({"query": "secret query", "url": "https://example.org/notes"})
        # Then: it refuses, naming the key, and no request was made
        assert "web-sense-enabled" in result
        assert canned_page == []

    def test_refused_when_privacy_mode_is_on_even_with_sense_on(self, chronoa_config, canned_page):
        # Given: the sense enabled but privacy mode back on
        _allow_web(chronoa_config)
        chronoa_config.set("privacy-mode", "true")
        assert not chronoa_config.web_sense_enabled
        from shani_chronoa.skills.web_search import _run
        # When: the skill is invoked
        result = _run({"query": "secret query", "url": "https://example.org/notes"})
        # Then: it refuses for the privacy reason, and nothing left the machine
        assert "privacy mode" in result
        assert canned_page == []

    def test_sense_run_refused_when_web_sense_is_off(self, chronoa_config, canned_page):
        # Given: the sense's own entry point, with consent denied
        assert not chronoa_config.web_sense_enabled
        from shani_chronoa.senses.web import _run
        # When: it is asked to fetch
        result = _run({"url": "https://example.org/notes"})
        # Then: it refuses and makes no request
        assert isinstance(result, str)
        assert "not permitted" in result
        assert canned_page == []


class TestBounds:
    """Untrusted pages are capped, and the cap is visible."""

    def test_response_larger_than_the_byte_cap_is_cut(self, monkeypatch, chronoa_config):
        # Given: a page far larger than the read cap
        _allow_web(chronoa_config)
        huge = "<p>padding paragraph</p>" * ((MAX_RESPONSE_BYTES // 10) + 500)
        assert len(huge) > MAX_RESPONSE_BYTES

        def _handler(request):
            return httpx.Response(200, headers={"content-type": "text/html"}, text=huge)

        _install_transport(monkeypatch, _handler)
        from shani_chronoa.skills.web_search import _run
        # When: it is fetched
        result = _run({"query": "x", "url": "https://example.org/huge"})
        # Then: the result is bounded and says it was cut
        assert len(result) < MAX_TEXT_CHARS + 2000
        assert "larger than" in result and "cut off" in result

    def test_text_longer_than_the_char_cap_is_truncated_visibly(self, monkeypatch, chronoa_config):
        # Given: a small response whose text alone exceeds the char cap
        _allow_web(chronoa_config)
        long_text = "<p>" + ("word " * (MAX_TEXT_CHARS + 500)) + "</p>"

        def _handler(request):
            return httpx.Response(200, headers={"content-type": "text/html"}, text=long_text)

        _install_transport(monkeypatch, _handler)
        from shani_chronoa.skills.web_search import _run
        # When: it is fetched
        result = _run({"query": "x", "url": "https://example.org/long"})
        # Then: the cap holds and the truncation is stated, never silent
        assert f"truncated after {MAX_TEXT_CHARS} characters" in result
        assert "characters were dropped" in result

    def test_http_error_status_is_reported(self, monkeypatch, chronoa_config):
        # Given: a server that refuses
        _allow_web(chronoa_config)

        def _handler(request):
            return httpx.Response(404, text="not found")

        _install_transport(monkeypatch, _handler)
        from shani_chronoa.skills.web_search import _run
        # When: the skill fetches
        result = _run({"query": "x", "url": "https://example.org/missing"})
        # Then: the status is in the message
        assert "404" in result

    def test_timeout_is_reported_not_hung(self, monkeypatch, chronoa_config):
        # Given: a server that never answers
        _allow_web(chronoa_config)

        def _handler(request):
            raise httpx.ConnectTimeout("too slow")

        _install_transport(monkeypatch, _handler)
        from shani_chronoa.skills.web_search import _run
        # When: the skill fetches
        result = _run({"query": "x", "url": "https://example.org/slow"})
        # Then: a plain explanation comes back
        assert "timed out" in result

    def test_non_text_response_type_is_refused(self, monkeypatch, chronoa_config):
        # Given: a 200 that is a binary document, not a page
        _allow_web(chronoa_config)

        def _handler(request):
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")

        _install_transport(monkeypatch, _handler)
        from shani_chronoa.skills.web_search import _run
        # When: the skill fetches
        result = _run({"query": "x", "url": "https://example.org/doc.pdf"})
        # Then: it says the type is unreadable rather than returning mojibake
        assert "application/pdf" in result
        assert "not text this can read" in result

    def test_html_with_no_text_is_reported(self, monkeypatch, chronoa_config):
        # Given: a valid but empty page - what a JS-rendered site returns
        _allow_web(chronoa_config)

        def _handler(request):
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><head><title>x</title></head><body><div id=app></div></body></html>",
            )

        _install_transport(monkeypatch, _handler)
        from shani_chronoa.skills.web_search import _run
        # When: the skill fetches
        result = _run({"query": "x", "url": "https://example.org/spa"})
        # Then: it reports nothing readable instead of claiming a search
        assert "no readable text" in result

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "ftp://example.org/x", "not a url", "javascript:alert(1)"],
    )
    def test_non_http_urls_are_refused_before_any_request(self, url, chronoa_config, canned_page):
        # Given: consent granted, and a URL that is not a web address
        _allow_web(chronoa_config)
        from shani_chronoa.skills.web_search import _run
        # When: the model supplies it
        result = _run({"query": "x", "url": url})
        # Then: it is refused and nothing was requested
        assert "http" in result
        assert canned_page == []


class TestHtmlStripper:
    """The stdlib stripper must survive real-world markup, not just tidy HTML."""

    def test_inline_svg_in_head_does_not_swallow_the_body(self):
        # Given: a page with an unclosed inline SVG in <head>, which is
        # ordinary markup - </head> closes while the <svg> is still open
        from shani_chronoa.webtext import _TextExtractor
        extractor = _TextExtractor()
        extractor.feed(
            "<html><head><svg width=10><title>icon</title></head>"
            "<body><p>real article text</p></body></html>"
        )
        extractor.close()
        # Then: the body text survives (a flat skip counter loses it)
        assert "real article text" in extractor.text()

    def test_nested_skip_subtrees_unwind_correctly(self):
        # Given: an svg containing a style element
        from shani_chronoa.webtext import _TextExtractor
        extractor = _TextExtractor()
        extractor.feed("<body><svg><style>x{}</style><g></g></svg><p>after svg</p></body>")
        extractor.close()
        # Then: text after the svg is extracted
        assert extractor.text() == "after svg"

    def test_unclosed_p_tags_still_separate_lines(self):
        # Given: HTML that omits </p>, which is not invalid
        from shani_chronoa.webtext import _TextExtractor
        extractor = _TextExtractor()
        extractor.feed("<html><body><p>alpha<p>beta<p>gamma</body></html>")
        extractor.close()
        # Then: block boundaries come from the start tags, so all three survive
        assert extractor.text() == "alpha\nbeta\ngamma"

    def test_entities_and_nbsp_are_decoded(self):
        # Given: character references in body text
        from shani_chronoa.webtext import _TextExtractor
        extractor = _TextExtractor()
        extractor.feed("<p>a &amp; b&#45;c&nbsp;d</p>")
        extractor.close()
        # Then: they are resolved, not passed through as entities
        assert extractor.text() == "a & b-c d"


class TestSenseShape:
    """The sense must be registrable and produce a properly-shaped Percept."""

    def test_sense_is_discovered_by_the_registry(self):
        # Given: a fresh builtin scan
        from shani_chronoa.senses import discover_senses
        # When: senses are loaded
        found = discover_senses()
        # Then: web is present, so it passed _sense_problem() validation
        assert "web" in found
        assert found["web"].name == "web"

    def test_sense_run_returns_a_timed_public_percept(self, chronoa_config, canned_page):
        # Given: consent granted and a reachable page
        _allow_web(chronoa_config)
        from shani_chronoa.senses import Percept
        from shani_chronoa.senses.web import _run
        # When: the sense perceives the page
        percept = _run({"url": "https://example.org/notes"})
        # Then: it is a Percept with real content, a source, a TTL and public sensitivity
        assert isinstance(percept, Percept)
        assert "blue-green deployment" in percept.content
        assert percept.sense == "web"
        assert percept.source == "https://example.org/notes"
        assert percept.ttl_seconds is not None and percept.ttl_seconds > 0
        assert percept.sensitivity == "public"
        assert not percept.is_expired()

    def test_retrieve_is_reusable_directly(self, canned_page):
        # Given: a reachable page
        # When: retrieve() is called without going through the gate
        page = retrieve("https://example.org/notes")
        # Then: it returns the parsed page, with its title and text
        assert page.title == "Shanios release notes"
        assert page.text.startswith("Shanios 1.2 released")
        assert page.chars_dropped == 0
        assert page.bytes_truncated is False

    def test_sense_is_reactive_only(self):
        # Given: the declared sense
        sense = SENSES[0]
        # When: ambient capability is asked about
        # Then: it declares no poll interval - egress is never scheduled
        assert sense.poll_interval is None
        assert not sense.is_ambient()
