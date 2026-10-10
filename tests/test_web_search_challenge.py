"""web_search: a search engine returning a challenge is not a result.

Measured live on 2026-10-10: DuckDuckGo's lite endpoint answers HTTP 200 with

    Unfortunately, bots use DuckDuckGo too.
    Please complete the following challenge to confirm this search was made by a human.
    Select all squares containing a duck:

and the previous version of the skill handed that to the model as the search
result - so it answered "what did the search say" with "select all squares
containing a duck", a confident wrong answer with nothing to mark it as one.
"""

import httpx
import pytest

from shani_chronoa.skills.web_search import _challenge_phrase, _run

CHALLENGE_HTML = """<!doctype html>
<html><head><title>DuckDuckGo</title></head><body>
<div class="anomaly">
<p>Unfortunately, bots use DuckDuckGo too.</p>
<p>Please complete the following challenge to confirm this search was made by a human.</p>
<p>Select all squares containing a duck:</p>
</div>
</body></html>
"""

RESULTS_HTML = """<!doctype html>
<html><head><title>arch linux</title></head><body>
<p>The Arch Wiki is the best documentation on the internet.</p>
</body></html>
"""

_RealClient = httpx.Client


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def _factory(**kwargs):
        kwargs.pop("transport", None)
        return _RealClient(**kwargs, transport=transport)

    monkeypatch.setattr("shani_chronoa.webtext.httpx.Client", _factory)


@pytest.fixture
def challenged_page(monkeypatch):
    from shani_chronoa import webtext
    monkeypatch.setattr(webtext, "_ROBOTS", {})

    def _handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text=CHALLENGE_HTML)

    _install_transport(monkeypatch, _handler)


@pytest.fixture
def results_page(monkeypatch):
    from shani_chronoa import webtext
    monkeypatch.setattr(webtext, "_ROBOTS", {})

    def _handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text=RESULTS_HTML)

    _install_transport(monkeypatch, _handler)


def _allow_web(cfg):
    cfg.set("privacy-mode", "false")
    cfg.set("web-sense-enabled", "true")


def test_detector_names_the_phrase():
    assert _challenge_phrase("Please complete the following challenge now") == "complete the following challenge"


def test_detector_ignores_a_normal_page():
    assert _challenge_phrase("The Arch Wiki is the best documentation.") == ""


def test_detector_is_case_insensitive():
    assert _challenge_phrase("SELECT ALL SQUARES CONTAINING A DUCK")


def test_a_challenge_is_reported_as_a_challenge(challenged_page, chronoa_config):
    _allow_web(chronoa_config)
    out = _run({"query": "arch linux"})
    assert "human-verification challenge" in out
    assert "did not return results" in out


def test_the_challenge_is_not_presented_as_content(challenged_page, chronoa_config):
    _allow_web(chronoa_config)
    out = _run({"query": "arch linux"})
    # **Zero occurrences.** The answer quotes the phrase the detector matched
    # ("complete the following challenge"), so the challenge's own distinctive
    # text never reaches the model at all. The first version of this asserted
    # `count == 1` on the assumption the quote would be the squares phrase,
    # and failed on behaviour that was better than it assumed.
    assert "Select all squares containing a duck" not in out
    assert "select all squares" not in out.lower()


def test_a_challenge_is_not_an_empty_result(challenged_page, chronoa_config):
    _allow_web(chronoa_config)
    out = _run({"query": "arch linux"})
    assert "not a statement that there is nothing to find" in out


def test_the_answer_names_the_paths_that_work(challenged_page, chronoa_config):
    _allow_web(chronoa_config)
    out = _run({"query": "arch linux"})
    assert "specific URL" in out
    assert "news" in out and "lookup_wikipedia" in out
    # **And the reason they are offered.** Asserting only the tool names left a
    # mutation of the clause explaining *why* they are the working paths
    # green - "query services that do allow automated readers" is the part
    # that tells a reader this is a measured distinction, not a guess.
    assert "allow automated readers" in out


def test_the_answer_quotes_the_engines_words(challenged_page, chronoa_config):
    _allow_web(chronoa_config)
    out = _run({"query": "arch linux"})
    assert "complete the following challenge" in out


def test_a_normal_page_is_not_treated_as_a_challenge(results_page, chronoa_config):
    _allow_web(chronoa_config)
    out = _run({"query": "arch linux"})
    assert "challenge" not in out.lower()
    assert "Arch Wiki is the best documentation" in out


def test_a_url_fetch_is_not_blocked_by_the_guard(results_page, chronoa_config):
    _allow_web(chronoa_config)
    out = _run({"query": "x", "url": "https://example.org/notes"})
    assert "Arch Wiki is the best documentation" in out


def test_consent_still_refuses_first(challenged_page, chronoa_config):
    chronoa_config.set("privacy-mode", "false")
    chronoa_config.set("web-sense-enabled", "false")
    assert "not permitted" in _run({"query": "arch linux"})
