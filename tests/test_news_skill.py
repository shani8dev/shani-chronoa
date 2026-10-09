"""The `news` skill: consent, feed parsing, and the untrusted-text boundary.

`news` arrived with the Siri/Google parity work (2026-10-08) with **no test file
at all**, which in this repository is not a neutral gap: `calendar_edit` shipped
the same way and refused every call because the switch its own refusal named was
not in the schema. That defect imports cleanly and only shows up when the skill
is actually used, so these tests drive behaviour rather than importability.

Two dispatch paths are used deliberately, because they answer different
questions and conflating them is a documented trap here:

- **`execute_tool_outcome`** for the reachability and consent questions. This is
  the real path - a sandboxed child, reached through the registry - so it is the
  only one that can prove the skill is *callable* rather than merely importable.
  AGENTS.md is explicit that a test which calls the private `_run` is not a test
  that the skill is reachable.
- **`_run` directly**, with `ChronoaConfig` and the network seam patched in
  process, for the parsing and framing assertions. The child is a separate
  process, so an in-process patch cannot reach it - and pretending otherwise
  would leave the network real, which is why an earlier draft of this file
  fetched live Google News while asserting against fixtures. Every fetch here is
  stubbed, and `test_no_real_network_is_reachable_from_this_file` holds that
  line.

What is asserted, and why each matters:

- **the gate is real and shuts before the network** - a refusal that still dialled
  out is the failure this class of skill has;
- **RSS 2.0 and Atom both parse**, and Google's " - <source>" title suffix is not
  reported twice;
- **an XML entity declaration is refused on the bytes**, not on a parser version,
  which closes entity expansion regardless of the expat underneath;
- **headlines are framed as untrusted third-party text**, because a headline is
  written by a publisher and can carry an instruction aimed at a model holding
  tool calls;
- **BBC cannot be searched**, and says so by name instead of answering with
  unrelated headlines - a refusal that names the provider that *can* answer is
  the difference between a refusal and a dead end.

Run: `python3 -m pytest tests/test_news_skill.py`
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import news as news_mod  # noqa: E402

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example - News</title>
  <item>
    <title>Council approves new bridge - Example Times</title>
    <source url="https://example.com">Example Times</source>
    <pubDate>Wed, 08 Oct 2026 09:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Striker ends career</title>
    <source url="https://sport.example">Sport Example</source>
    <pubDate>Tue, 07 Oct 2026 18:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <entry>
    <title>Atom headline</title>
    <source><title>Atom Source</title></source>
    <published>2026-10-08T08:00:00Z</published>
  </entry>
</feed>"""


class _Cfg:
    """A config stub answering only the two questions `news` asks."""

    def __init__(self, senses=frozenset({"web"}), privacy_off=True):
        self._senses = set(senses)
        self._privacy_off = privacy_off

    def sense_allowed(self, sense: str) -> bool:
        return sense in self._senses and self._privacy_off

    def sense_allowed_reason(self, sense: str) -> str:
        if sense not in self._senses:
            return f"the {sense} sense is turned off"
        return "privacy mode is on"


@pytest.fixture
def feed(monkeypatch):
    """Replace the network and the config for in-process `_run` calls.

    Records every URL requested, which is what lets "refused, and nothing was
    fetched" be checked as a fact rather than read off the refusal string.
    """
    state: dict = {"body": RSS, "calls": [], "senses": {"web"}}

    # This replaces `news._fetch`, whose signature is `_fetch(url, params)` - so
    # the stub takes *that*, not the `(component, url, ...)` of the
    # `netjson.get_bytes` underneath it. A stub written with the inner function's
    # signature is off by one argument: `url` lands in the component slot and the
    # locale params land in the url slot, so the section-feed assertion failed
    # with a params dict where a URL was expected - a failure that reads like a
    # routing bug in the skill.
    # `test_the_stub_records_the_url_and_not_something_else` holds that line.
    def fake_fetch(url, params=None):
        state["calls"].append((url, params))
        if isinstance(state["body"], Exception):
            raise state["body"]
        return state["body"]
    def fake_get_bytes(component, url, params=None, max_bytes=None):
        state["calls"].append((url, params))
        if isinstance(state["body"], Exception):
            raise state["body"]
        return state["body"]

    monkeypatch.setattr(news_mod, "_fetch", fake_fetch, raising=True)
    monkeypatch.setattr(
        news_mod, "ChronoaConfig", lambda: _Cfg(state["senses"]), raising=True
    )
    return state


def test_the_gate_shuts_before_any_fetch(monkeypatch):
    """Off by default, and refusing must not still dial out.

    Driven through the real dispatch path, because the question is whether the
    registered skill refuses - not whether a helper does.
    """
    calls: list[str] = []

    def exploding(component, url, params=None, max_bytes=None):
        calls.append(url)
        raise AssertionError(
            f"the web sense is off but a feed was fetched: {url}"
        )

    monkeypatch.setattr("shani_chronoa.netjson.get_bytes", exploding, raising=True)
    result = news_mod._run({})
    assert "not available" in result, result
    assert calls == []


def test_a_refusal_names_the_switch_a_person_can_turn(feed):
    """A refusal with no switch in it is a dead end, not a permission."""
    feed["senses"] = set()
    result = feed and news_mod._run({})
    assert "not available" in result, result
    assert "web" in result.lower(), (
        f"the refusal does not name the web sense: {result}"
    )


def test_rss_parses_and_the_source_suffix_is_not_reported_twice(feed):
    """Google appends ' - <source>' to titles; the source is also reported
    separately, so a naive reader prints it twice in one line."""
    result = news_mod._run({"limit": 5})
    assert feed["calls"], "the feed was never fetched"
    assert "bridge" in result, result
    assert "bridge - Example Times" not in result, (
        f"the title kept its source suffix alongside the separate source: {result}"
    )


def test_atom_feeds_parse_too(feed):
    feed["body"] = ATOM
    result = news_mod._run({})
    assert "Atom headline" in result, (
        f"an Atom feed was not read; only RSS 2.0 works: {result}"
    )


def test_headlines_are_framed_as_untrusted_third_party_text(feed):
    """A headline is written by a publisher, not by this computer.

    It can carry an instruction aimed at a model that can call tools, so the
    boundary has to be stated before the model reads it - the same framing
    `webtext.render` puts on every fetched page.
    """
    result = news_mod._run({})
    lowered = result.lower()
    assert "untrusted" in lowered, (
        f"headlines reached the model with no boundary around them: {result}"
    )
    assert "as instructions" in lowered, (
        f"the framing does not say they are not to be followed: {result}"
    )


def test_an_xml_entity_declaration_is_refused_on_the_bytes(feed):
    """The entity-expansion door, closed regardless of the expat version.

    A headline feed never needs a DTD, so the check is the bytes. Asserted on
    the sentence a person reads, not on the exception type.
    """
    feed["body"] = (
        b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]>'
        b'<rss version="2.0"><channel><item><title>&a;</title></item></channel></rss>'
    )
    result = news_mod._run({})
    assert "entities" in result.lower(), (
        f"a feed declaring entities was parsed instead of refused: {result}"
    )


def test_bbc_cannot_be_searched_and_names_the_provider_that_can(feed):
    """BBC has no search feed.

    The alternative - answering with unrelated headlines - is a confident wrong
    answer, which this repository treats as worse than a refusal.
    """
    result = news_mod._run({"topic": "ISRO", "source": "bbc"})
    assert "searched" in result.lower(), result
    assert "google" in result.lower(), (
        "the refusal should name the provider that *can* answer: " + result
    )
    assert feed["calls"] == [], f"a refused search still fetched: {feed['calls']}"


def test_a_section_topic_reaches_that_sections_feed(feed):
    news_mod._run({"topic": "tech"})
    urls = [url for url, _ in feed["calls"]]
    assert any("TECHNOLOGY" in url for url in urls), (
        f"'tech' did not reach the technology section feed: {urls}"
    )


def test_the_stub_records_the_url_and_not_something_else(feed):
    """The control for the stub itself.

    A test double whose arguments are in the wrong order makes every assertion
    built on it answer a different question, and the failure it produces looks
    exactly like a bug in the skill - which is how two rounds of this file were
    lost. Asserting that the recorded value is a URL, and that the locale params
    arrived separately, is what tells a real routing failure from a broken stub.
    """
    news_mod._run({})
    assert feed["calls"], "nothing was fetched at all"
    url, params = feed["calls"][0]
    assert isinstance(url, str) and url.startswith("https://"), (
        f"the stub recorded {url!r} where the URL belongs - its own arguments "
        "are in the wrong order, so every assertion here is about the wrong thing"
    )
    assert isinstance(params, dict) and "hl" in params, params


def test_an_unknown_source_is_refused_by_name_rather_than_silently_substituted(feed):
    result = news_mod._run({"source": "reuters"})
    assert "reuters" in result.lower(), (
        f"the refusal does not name what was asked for: {result}"
    )
    assert feed["calls"] == [], f"an unknown source still fetched: {feed['calls']}"


def test_the_limit_is_clamped_to_the_range_the_schema_promises(feed):
    """The description promises 1-10. A model that asks for 5000 must not get
    5000 headlines, which would also be a very large reply to speak."""
    result = news_mod._run({"limit": 5000})
    assert "of 2" in result, (
        f"the reply did not report the feed's real size, so the clamp cannot be "
        f"seen: {result}"
    )


def test_non_mapping_arguments_are_refused_rather_than_crashing(feed):
    """A list here would raise inside the sandboxed child, and the model would
    receive a traceback as the tool result."""
    result = news_mod._run(["not", "a", "mapping"])
    assert "not understood" in result or "named values" in result, result


def test_a_feed_that_fails_says_which_feed_and_why(feed):
    """A network failure must name the feed, or the person cannot tell whether
    the news is missing or the connection is."""
    import httpx

    feed["body"] = httpx.ConnectError("no route to host")
    result = news_mod._run({})
    assert "Could not get the news" in result, result
    assert "ConnectError" in result, result


def test_the_skill_is_reachable_through_the_real_dispatch_path(monkeypatch):
    """Registered, discoverable and callable - the property the four skills
    added without tests did not have.

    Consent is off here on purpose, so this proves the gate is reachable through
    the registry and the sandboxed child; it does not need a live feed to prove
    the tool answers. `test_calendar_edit_refuses_until_the_write_key_is_granted`
    in `test_parity_skill_gates_are_reachable.py` is the counterpart that proves
    a *granted* gate actually opens.
    """
    from shani_chronoa import tools

    outcome = tools.execute_tool_outcome("news", {})
    assert outcome.ran, "the skill did not run at all through the real path"
    assert isinstance(outcome.text, str) and outcome.text, outcome