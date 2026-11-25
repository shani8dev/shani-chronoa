"""RSS and Atom as a trigger source.

Parsing is stdlib `xml.etree`; fetching is the one JSON/bytes helper this tree
already uses, so the User-Agent, size cap, timeout and egress record are the
same as every other network read. There is **no network egress by default**:
`read_feed_signal` first asks the existing web sense, which is off by default
and additionally refuses in privacy mode. A rule can only fetch a feed after a
person has turned on the switch that already governs web reads.

The signal shape is the trigger engine's `EventSignal`: a fingerprint of the
newest entry, unchanged on a quiet poll. A feed that cannot be fetched is
`unavailable`, never "no new posts" - those are opposite claims and the engine
checks the status before the fingerprint.
"""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from typing import Callable, NamedTuple, Optional

from shani_chronoa.triggers.common import (
    SIGNAL_OK,
    SIGNAL_UNAVAILABLE,
    Event,
    EventSignal,
)

#: Opt-in param on the existing `schedule` event type. No new event type and no
#: new consent key: the gate is the web sense this module already asks.
FEED_PARAM = "feed_url"


class FeedEntry(NamedTuple):
    title: str
    link: str
    summary: str
    published: str

    @property
    def fingerprint(self) -> str:
        # Link is the durable identity; a title can be edited after publishing.
        basis = self.link or (self.title + "\x00" + self.published)
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _text(node: Optional[ET.Element], tag: str) -> str:
    if node is None:
        return ""
    child = node.find(tag)
    return "" if child is None or child.text is None else child.text.strip()


def parse_feed(data: bytes) -> "list[FeedEntry]":
    """Entries from RSS 2.0 or Atom. An unreadable document raises ET.ParseError.

    Keeping the parse pure is what makes it testable without a network. The
    two formats differ in where they put the link: RSS carries it as item text,
    Atom as an `<link href=...>` attribute.
    """
    root = ET.fromstring(data)
    entries: list[FeedEntry] = []
    if str(root.tag).endswith("feed"):  # Atom (namespaced root)
        ns = "{http://www.w3.org/2005/Atom}"
        for node in root.findall(f"{ns}entry"):
            link = ""
            for candidate in node.findall(f"{ns}link"):
                if candidate.get("rel", "alternate") == "alternate":
                    link = candidate.get("href", "")
                    break
            entries.append(
                FeedEntry(
                    title=_text(node, f"{ns}title"),
                    link=link,
                    summary=_text(node, f"{ns}summary"),
                    published=_text(node, f"{ns}updated"),
                )
            )
    else:  # RSS 2.0 / RDF
        for node in root.iter("item"):
            entries.append(
                FeedEntry(
                    title=_text(node, "title"),
                    link=_text(node, "link"),
                    summary=_text(node, "description"),
                    published=_text(node, "pubDate"),
                )
            )
    return entries


def _web_allowed() -> bool:
    from shani_chronoa.config import ChronoaConfig

    try:
        return ChronoaConfig().sense_allowed("web")
    except Exception:  # noqa: BLE001
        return False


def read_feed_signal(
    url: str,
    *,
    allowed: Optional[Callable[[], bool]] = None,
    fetch: Optional[Callable[[str], bytes]] = None,
    now: Optional[float] = None,
) -> EventSignal:
    """One poll of a feed, under the existing web gate.

    `fetch` and `allowed` are seams for tests; production uses `netjson` and
    the real config. A refusal is `SIGNAL_UNAVAILABLE` with the reason named,
    never a quiet empty feed.
    """
    moment = time.time() if now is None else now
    if not url or not isinstance(url, str):
        return EventSignal(
            SIGNAL_UNAVAILABLE,
            detail="feed_url must be a URL",
            payload={"kind": "feed", "subject": str(url)},
        )
    if (allowed or _web_allowed)() is False:
        return EventSignal(
            SIGNAL_UNAVAILABLE,
            detail="reading a feed needs the Web search switch, and privacy mode off",
            payload={"kind": "feed", "subject": url},
        )

    if fetch is None:
        from shani_chronoa import netjson

        def fetch(target: str) -> bytes:  # noqa: E306
            return netjson.get_bytes("feed", target)

    try:
        data = fetch(url)
    except Exception as exc:  # noqa: BLE001
        return EventSignal(
            SIGNAL_UNAVAILABLE,
            detail=f"could not read the feed: {type(exc).__name__}: {exc}",
            payload={"kind": "feed", "subject": url},
        )
    try:
        entries = parse_feed(data)
    except ET.ParseError as exc:
        return EventSignal(
            SIGNAL_UNAVAILABLE,
            detail=f"the feed is not readable XML: {exc}",
            payload={"kind": "feed", "subject": url},
        )
    if not entries:
        # A well-formed feed with no entries is an answer: "nothing published".
        return EventSignal(
            status=SIGNAL_OK,
            fingerprint="",
            payload={"kind": "feed", "subject": url},
        )
    newest = max(entries, key=lambda e: e.published)
    event = Event(
        kind="feed",
        subject=url,
        summary=(newest.title or newest.link)[:160],
        fingerprint=newest.fingerprint,
        detail={
            "title": newest.title,
            "link": newest.link,
            "published": newest.published,
            "entries": len(entries),
        },
        created_at=moment,
    )
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=newest.fingerprint,
        event=event,
        detail=newest.title,
        payload={"kind": "feed", "subject": url},
    )
