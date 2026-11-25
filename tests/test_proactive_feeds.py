"""Feeds: stdlib RSS/Atom, behind the existing web gate."""

import pytest

from shani_chronoa import feeds
from shani_chronoa.triggers.common import SIGNAL_OK, SIGNAL_UNAVAILABLE

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Older</title><link>https://example.org/a</link><pubDate>2026-01-01</pubDate></item>
<item><title>Newest</title><link>https://example.org/b</link><pubDate>2026-02-01</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom one</title><link href="https://example.org/c"/><updated>2026-03-01</updated></entry>
</feed>"""


def test_rss_entries_and_the_durable_fingerprint():
    entries = feeds.parse_feed(RSS)
    assert len(entries) == 2
    assert entries[0].title == "Older"
    assert entries[0].link == "https://example.org/a"
    assert entries[0].fingerprint == entries[0].fingerprint
    assert entries[0].fingerprint != entries[1].fingerprint


def test_atom_link_is_the_attribute_not_the_text():
    entries = feeds.parse_feed(ATOM)
    assert len(entries) == 1
    assert entries[0].link == "https://example.org/c"
    assert entries[0].published == "2026-03-01"


def test_an_unreadable_document_is_a_parse_error_not_an_empty_feed():
    with pytest.raises(Exception):
        feeds.parse_feed(b"not xml at all")


def test_a_refused_web_switch_makes_no_request():
    called = []

    def fetch(url):
        called.append(url)
        return RSS

    got = feeds.read_feed_signal(
        "https://example.org/feed", allowed=lambda: False, fetch=fetch
    )
    assert got.status == SIGNAL_UNAVAILABLE
    assert called == []
    assert "Web search" in got.detail


def test_a_fetched_feed_produces_one_fingerprinted_signal():
    got = feeds.read_feed_signal(
        "https://example.org/feed", allowed=lambda: True, fetch=lambda url: RSS
    )
    assert got.status == SIGNAL_OK
    assert got.event.kind == "feed"
    assert got.event.summary == "Newest"
    assert got.fingerprint == got.event.fingerprint


def test_a_fetch_failure_is_unavailable_not_quiet():
    def fetch(url):
        raise OSError("offline")

    got = feeds.read_feed_signal(
        "https://example.org/feed", allowed=lambda: True, fetch=fetch
    )
    assert got.status == SIGNAL_UNAVAILABLE
    assert got.fingerprint is None
    assert "offline" in got.detail


def test_a_well_formed_empty_feed_has_no_event():
    got = feeds.read_feed_signal(
        "https://example.org/feed",
        allowed=lambda: True,
        fetch=lambda url: b'<rss version="2.0"><channel></channel></rss>',
    )
    assert got.status == SIGNAL_OK and got.event is None and got.fingerprint == ""


def test_a_blank_url_is_refused_before_any_fetch():
    called = []
    got = feeds.read_feed_signal(
        "", allowed=lambda: True, fetch=lambda url: called.append(url)
    )
    assert got.status == SIGNAL_UNAVAILABLE and called == []
