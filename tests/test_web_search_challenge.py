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


def test_a_challenge_falls_through_to_another_engine(challenged_page,
                                                     chronoa_config, monkeypatch):
    """**The dead end was the bug.** Three engines still answer, so a challenge
    on the one endpoint tried first is a reason to ask the others - the
    fixed-string answer that used to be returned here is what made the old
    skill useless."""
    _allow_web(chronoa_config)
    from shani_chronoa import websearch
    monkeypatch.setattr(websearch, "search",
                        lambda q, **k: ([websearch.Result(
                            "Arch Linux", "https://archlinux.org",
                            "The website.", "Bing")], "", []))
    out = _run({"query": "arch linux"})
    assert "Arch Linux" in out
    assert "Bing" in out
    assert "not Chronoa's own claims" in out


def test_a_challenge_never_presents_the_gate_as_content(challenged_page,
                                                        chronoa_config,
                                                        monkeypatch):
    """**The invariant that did not change.** Whatever the fallback, the model
    must not be told the search said "select all squares containing a duck"."""
    _allow_web(chronoa_config)
    from shani_chronoa import websearch
    monkeypatch.setattr(websearch, "search", lambda q, **k: (
        [], "the engine returned a verification page", []))
    out = _run({"query": "arch linux"})
    assert "no other engine answered either" in out
    assert "squares containing" not in out.lower()


def test_a_challenge_says_it_is_not_an_empty_result(challenged_page,
                                                   chronoa_config, monkeypatch):
    _allow_web(chronoa_config)
    from shani_chronoa import websearch
    monkeypatch.setattr(websearch, "search", lambda q, **k: ([], "nope", []))
    out = _run({"query": "arch linux"})
    assert "is not a statement that there is nothing to find" in out


def test_the_refusal_names_the_paths_that_work(challenged_page, chronoa_config,
                                               monkeypatch):
    """"The search is blocked" and stopping is a dead end."""
    _allow_web(chronoa_config)
    from shani_chronoa import websearch
    monkeypatch.setattr(websearch, "search", lambda q, **k: ([], "nope", []))
    out = _run({"query": "arch linux"})
    assert "specific URL" in out
    assert "news" in out and "lookup_wikipedia" in out


def test_the_detector_still_names_the_phrase():
    """The gate detector is unchanged and still tested directly."""
    assert _challenge_phrase("Please complete the following challenge") == \
        "complete the following challenge"


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
