"""Web search across the engines that actually answer, fetched with `lynx`.

Studying Claude's `web_search` made the target clear: it returns **structured
entries** - each a title, a URL and the page's own words, numbered so the model
can cite them - and it never returns a page that is not a result.

## What was measured before anything was built (2026-10-10, this box)

Every candidate was fetched for real, twice: raw with `curl` and through the
distro's own text browser.

| engine | raw curl, browser headers | `lynx -dump` | verdict |
|---|---|---|---|
| Google **web** search | 200, **0** result URLs | empty | **unusable** - results arrive inside a JS blob |
| Google **News** (`news.google.com/rss/search`) | 200, 100 `<item>` entries | n/a | **works, keyless** - but news-scoped, added as a provider |
| Bing | 200, 10 `b_algo` blocks | 14 KB of clean numbered text | **works** |
| DuckDuckGo `lite` | 200, 11 redirect links | 6.9 KB of clean numbered text | **works** |
| Brave Search | 200, 27 `snippet` blocks | 22 KB of clean numbered text | **works** |
| Mojeek / Ecosia | 403 | gate page | unusable |
| searx.be | 200 | a JS "checking your browser" gate | unusable |
| Startpage / Marginalia / Yandex / Ask / AOL | gate or empty | gate or empty | unusable |

Two findings shaped this module more than anything else:

1. **A browser User-Agent made lynx return nothing at all.** Measured: the same
   URL that produced 6.9 KB of results with no UA flag produced **zero bytes**
   when one was passed. So this module passes no UA - which also means it does
   not pretend to be a browser, and the engine answered anyway.
2. **Google does not answer, ever, to anything that is not a JavaScript
   engine - and the chain is measured rather than assumed.** The obvious first
   move is `curl -L`, so it is worth being exact about why that is not it:

   | step | measured |
   |---|---|
   | `curl -L` on `/search?q=` | `http_code=200 num_redirects=0` - **there is no HTTP redirect to follow** |
   | what it does send | `<meta http-equiv="refresh" content="0;url=/httpservice/retry/enablejs?sei=...">` |
   | fetching that URL by hand, cookies carried | the page saying *"Enable JavaScript to use search"* |
   | `lynx -dump` (no JS) | *"Update your browser - Your browser is not supported anymore"* |
   | `google-chrome --headless` (this box, real JS) | *"Our systems have detected unusual traffic"* |

   **So the blocker is not the fetcher - it is that there is no JavaScript
   engine on the image at all.** `tools/cli_matrix.py` over both matrices
   confirms it: of 4,231 commands, the web-related subset is `curl`, `wget`,
   `aria2c`, `lynx` and the DNS tools. **No browser, no `node`, no `phantomjs`,
   no `wkhtmltopdf`, no RSS reader.** Chrome exists on the dev box and still
   gets gated, so it is not a missing binary here - it is that Google serves
   *web-search* results only to something that runs its JavaScript, and nothing
   that ships on ShaniOS does.

   **That was measured for web search alone, and the module was wrong to carry
   it as a blanket claim about Google.** Measured later: Google's *news* feed
   answers an automated reader with no key at all - 100 items, 119,567 bytes,
   every one with a title, a link, a date and a nested `<source>`. So the
   accurate statement is the narrower one: **Google's web-search endpoint is
   JS-gated; its news endpoint is a keyless structured API and works.** The
   generic search API (`/customsearch/v1`) is the same shape as the four JSON
   providers below and is measured to refuse with
   `403 Method doesn't allow unregistered callers (callers without established
   identity). Please use API Key` - so it is a *key* that is missing, not a
   fetcher. Google News is in `_FEED_PROVIDERS` because it is genuinely a
   different shape (XML, not JSON), and its scope is stated in the answer
   rather than hidden.

   Reported as "Google found nothing" for a page that never contained a result
   is the confidently-wrong answer this repo keeps recording, so Google is not
   in the provider list and the reason is stated rather than left as a gap.
   **The matrix's `jq` is the more useful fact**: the engines that do answer
   return HTML that `lynx` renders to text, and the JSON APIs return structured
   data a parser can actually trust.

## Why `lynx` rather than parsing the HTML

`lynx -dump` unwraps the redirect wrappers. Measured on Bing: the raw HTML
points every result at `bing.com/ck/a?!&p=...` with the real URL base64-encoded
inside a `u=a1...` parameter, while `lynx -dump` prints `https://archlinux.org`
as plain text. Fewer assumptions about one engine's markup, and a third
engine's links need no unwrapping either.

## The consent gate is not waived

A search query leaves the machine. This goes through the same `web` sense and
privacy-mode gate as every other web skill, read fresh per call. The gate is
about *whether the query is allowed to leave*, and it is not affected by which
engine answers.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from typing import NamedTuple

from shani_chronoa.config import ChronoaConfig

_TIMEOUT = 45
_MAX_RESULTS = 12
_MAX_PROVIDERS = 4

#: A results page smaller than this is a throttle, not an answer. Measured: the
#: real pages ranged 3.6-22 KB and the throttled ones 21-840 B, so the gap is
#: wide. It is set well below the smallest real page rather than at the
#: midpoint, because the cost of a false positive is a skipped engine (the next
#: in the list is tried) while the cost of a false negative is reporting "no
#: results" for a machine the engine was throttling.
_MIN_PAGE_BYTES = 1200

#: The engines, in the order they are tried. Every one was measured to return
#: results through `lynx` on 2026-10-10.
#: **Each engine is fetched the way that engine was measured to work.**
#: DuckDuckGo and Bing give clean numbered text to `lynx -dump`. Brave does not:
#: its `lynx` text is a knowledge panel and a wall of Reddit comments with no
#: organic list in it, while its HTML yields 12 clean `<div class="snippet">`
#: blocks. Forcing one fetch tool onto all three would have meant either a
#: Brave that returns panel fragments or two engines that could have worked.
#: **Per-provider, and the reason is measured in both directions.**
#: **The order is measured, not preferred, and it is a throttle-order.** See
#: the docstring's measurement table: the same engine gave 27 results and then
#: a gate page minutes later, so which engine answers first is a function of
#: which one has not just been asked. Bing and DuckDuckGo are tried first
#: because they were the two that answered consistently across the sweep.
#: **JSON APIs are tried first, and the order is the point.** A JSON provider
#: returns fields that need no parsing and no assumption about markup, and it
#: is an API published for programmatic access rather than a page whose terms
#: ask automated readers to leave. Measured on this box, all four answer:
#:
#:     Arch Wiki   -> Install guide; Official Arch Linux Install Guide ...
#:     Stack Exchange -> Arch Linux install doesn't boot; Questions before inst
#:     Hacker News -> Show HN: Twitch Installs Arch Lin...
#:     GitHub      -> ChrisTitusTech/ArchTitus; archlinux/archinstall
#:
#: and each with a title and a URL, which is the same shape the HTML engines
#: produce - so they slot into the same `Result`.
#: **The query's parameter name is part of each entry.** The first version
#: omitted it entirely, so `if "q" in filled:` was never true and the query was
#: never sent - which measured as a well-formed answer about Ubuntu root
#: passwords for the query "arch linux install guide". Each API names its query
#: parameter differently and there is no way to infer it.
_JSON_PROVIDERS = (
    ("Arch Wiki", "https://wiki.archlinux.org/api.php",
     {"action": "query", "list": "search", "srsearch": "",
      "srlimit": 8, "format": "json"}, "_parse_archwiki"),
    ("Stack Exchange", "https://api.stackexchange.com/2.3/search/advanced",
     {"order": "desc", "sort": "relevance", "q": "", "site": "unix",
      "pagesize": 8}, "_parse_stackexchange"),
    ("Hacker News", "https://hn.algolia.com/api/v1/search",
     {"query": "", "hitsPerPage": 8}, "_parse_hackernews"),
    ("GitHub", "https://api.github.com/search/repositories",
     {"q": "", "per_page": 8}, "_parse_github"),
)

#: **RSS feeds, which are XML rather than JSON, so they get their own table
#: rather than being squeezed into `_JSON_PROVIDERS`.** The only keyless Google
#: surface that answers an automated reader is Google News' feed, and it is
#: news-scoped - which is a scope, not a failure, and one this module now says
#: out loud instead of reporting "Google found nothing".
#:
#: **Measured, no API key:** `https://news.google.com/rss/search?q=...` returns
#: `http=200`, 119,567 bytes and **100 `<item>` entries** with title, link,
#: `pubDate` and a nested `<source>`. The general web-search endpoint
#: (`/customsearch/v1`) is the same shape as the four JSON providers above and
#: is measured to refuse: `403 Method doesn't allow unregistered callers
#: (callers without established identity). Please use API Key`.
_FEED_PROVIDERS = (
    ("Google News", "https://news.google.com/rss/search",
     {"q": "", "hl": "en-US", "gl": "US", "ceid": "US:en"},
     "_parse_googlenews"),
)

#: The HTML engines, tried after the JSON ones.
PROVIDERS = (
    ("Bing", "lynx", "https://www.bing.com/search?q={query}&count=30", "_parse_bing"),
    ("DuckDuckGo", "lynx", "https://lite.duckduckgo.com/lite/?q={query}", "_parse_ddg"),
    ("Wiby", "lynx", "https://wiby.me/?q={query}", "_parse_wiby"),
    ("Brave Search", "html", "https://search.brave.com/search?q={query}", "_parse_brave_html"),
)

#: Phrases meaning the page is a gate rather than results.
#: **A phrase that is missing here is a gate page reported as results.** The
#: first version of this tuple omitted "complete the following challenge" - the
#: exact phrase DuckDuckGo's gate uses - so the one gate this module had already
#: measured elsewhere was invisible here, and a test that asserted the phrase
#: failed on a function with no function in it. **A detector's list can only
#: detect what was put in it, which is why each phrase here has been seen.**
_GATE = (
    "checking your browser",
    "verify you are a human",
    "are you a robot",
    "enable javascript",
    "complete the following challenge",
    "confirm this search was made by a human",
    "select all squares containing",
    "unfortunately, bots use duckduckgo too",
    "if you are using a screen reader",
)

_TAG = re.compile(r"<[^>]+>")


class Result(NamedTuple):
    title: str
    url: str
    description: str
    source: str


def gate(what):
    """The consent refusal, or None. Same gate as every other web skill."""
    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return "%s is not available: %s" % (what, config.sense_allowed_reason("web"))
    return None


#: **The separator is U+203A, not ASCII `>`.` Measured: the captured line is
#: `https://wiki.archlinux.org \u203a title \u203a Installation_guide`. The two
#: are visually identical in a terminal and a function written against ASCII
#: silently returns the string unchanged - which is exactly what the first
#: version did, leaving `\u203a` in every URL.
_CRUMB = "\u203a"


def _crumb_to_url(text):
    """`https://archlinux.org \u203a download` -> `https://archlinux.org/download`.

    Bing's rendered URL is the host plus a breadcrumb of the path taken to get
    there. Discarding it made two distinct pages collapse to one host and appear
    as duplicates.
    """
    text = text.strip()
    if _CRUMB not in text:
        return text
    head, _, tail = text.partition(_CRUMB)
    parts = [head.strip()] + [p.strip() for p in tail.split(_CRUMB) if p.strip()]
    return "/".join(parts) if parts else text


def _gate_phrase(text):
    """The phrase that identifies a gate page, or an empty string.

    **Extracted so it can be tested directly** - it had been inlined in
    `search()`, so a detector bug was only visible through the whole search
    loop, and a test named `test_a_gate_page_is_detected` had no function to
    call.
    """
    low = text.lower()
    for phrase in _GATE:
        if phrase in low:
            return phrase
    return ""


def _clean(line):
    text = _TAG.sub(" ", line or "")
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_url(text):
    """A bare host or a full URL, as lynx prints a result's last line.

    DuckDuckGo's shape prints `archlinux.org/download/` with no scheme; Bing's
    prints `https://archlinux.org`. Both are accepted, and anything with a
    space is not - a description's last line ends in a word and must not be
    read as an address.
    """
    text = text.strip()
    if not text or " " in text or len(text) > 120:
        return False
    if text.startswith(("http://", "https://")):
        return True
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(/[^\s]*)?",
                             text, re.I))


def _fetch_json(url, params, component):
    """(data, reason). Through `netjson` - the same helper `skills/lookup.py`
    uses - so there is one HTTP policy and one egress record, not two.
    """
    from shani_chronoa.netjson import get_json
    try:
        return get_json(component, url, params), ""
    except Exception as exc:
        return {}, "%s (%s)" % (exc.__class__.__name__,
                                str(exc)[:80])


def _fetch_feed(url, params, component):
    """(raw, reason). Through `netjson.get_bytes` - the same HTTP policy, the
    same timeout and the same egress record as the JSON path, so there is still
    one policy and one record rather than one per provider shape.
    """
    from shani_chronoa.netjson import get_bytes
    try:
        return get_bytes(component, url, params), ""
    except Exception as exc:
        return b"", "%s (%s)" % (exc.__class__.__name__, str(exc)[:80])


def _dump(url):
    """(text, reason). Runs `lynx -dump` with **no User-Agent** - the module
    docstring records why passing one produced an empty page."""
    if shutil.which("lynx") is None:
        return "", "lynx is not installed"
    command = ["lynx", "-dump", "-accept_all_cookies", "-nolist", url]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"lynx did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return "", str(exc)
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        detail = [l for l in (proc.stderr or "").splitlines() if l.strip()]
        return "", (detail[-1].strip() if detail
                    else f"lynx exited {proc.returncode}")
    return proc.stdout or "", ""


def _dump_html(url, component):
    """(text, reason). The HTML path, through netjson's named-User-Agent client.

    Used only for Brave, whose `lynx` text is a knowledge panel rather than a
    result list. netjson carries the timeout, the size cap and the egress
    record, so this adds no second HTTP policy - the same helper
    `skills/lookup.py` uses for Wikipedia.
    """
    from shani_chronoa.netjson import get_bytes
    try:
        body = get_bytes(component, url)
    except Exception as exc:
        return "", "the request failed (%s)" % exc.__class__.__name__
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace"), ""
    return body or "", ""


# --- DuckDuckGo lite -----------------------------------------------------------
# Measured shape, from the real capture in tests/fixtures/search/:
#
#     1.  [4]Official site
#        Arch Linux
#        archlinux.org
#
#     2.  [5]Arch Linux
#        You've reached the website for Arch Linux, a lightweight and
#        flexible ... Keep It Simple.
#        archlinux.org
#
# A numbered line carrying the title, an indented body, then a final indented
# line that is the URL. Everything indented belongs to the result above it.

_DDG_ENTRY = re.compile(r"^\s{0,4}(\d+)\.\s+(?:\[\d+\])?\s*(.+?)\s*$")


def _parse_ddg(text):
    results = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = _DDG_ENTRY.match(lines[index])
        index += 1
        if not match:
            continue
        title = _clean(match.group(2))
        body = []
        url = ""
        while index < len(lines):
            line = lines[index]
            if _DDG_ENTRY.match(line):
                break
            stripped = line.strip()
            index += 1
            if not stripped:
                if body:
                    break
                continue
            if _looks_like_url(stripped):
                url = stripped
                break
            body.append(_clean(stripped))
        if title and url:
            results.append(Result(title, url, " ".join(body)[:400], "DuckDuckGo"))
    return results


# --- Bing ---------------------------------------------------------------------
# Measured shape:
#
#     1.
#
#    [29]archlinux.org
#    https://archlinux.org
#
#    [30]Arch Linux
#
#    You've reached the website for Arch Linux, ...
#
# The numbered marker, the link label and the URL are separate lines, and a
# blank line ends the block, so the parser walks forward from each number.

# **A leading `https://`, not a whole line.** Measured: Bing prints
# `https://archlinux.org > download` for a sub-page, and a whole-line match
# rejected it - so half the results were skipped and the parser reported 5 of
# 10. The first token is the URL; the rest is the breadcrumb.
# **The whole line, because the breadcrumb is the path.** `\S+` stops at the
# first space, so the group never contained `> title > Installation_guide` and
# the join had nothing to work on - two different ArchWiki pages collapsed to
# one host and reported each other as duplicates.
_BING_URL = re.compile(r"^\s*(https?://.*)$")


def _parse_bing(text):
    """Read Bing's results off the **URL lines**, not the numbers.

    **The numbered-marker approach found 1 of 10.** Measured: `lynx -dump`
    numbers only the first result of a Bing page - the rest arrive as

        [31]archlinux.org
        https://archlinux.org > download

        [32]Arch Linux - Downloads

        It is intended for new installations only; ...

    so a parser walking `N.` markers stops after one and reports a page with
    ten results as containing one. The invariant that actually holds on every
    result is the URL line, so that is what the walk is keyed on.
    """
    lines = text.splitlines()
    results = []
    previous_url = ""
    index = 0
    while index < len(lines):
        hit = _BING_URL.match(lines[index])
        index += 1
        if not hit:
            continue
        # **The breadcrumb is a path, not decoration.** Measured: Bing prints
        # `https://archlinux.org > download` for a sub-page, and taking the
        # first token threw the path away - so `https://wiki.archlinux.org`
        # appeared twice as two different results, both the same host.
        # `" > "` is the separator and joining with `/` gives the real URL.
        url = _crumb_to_url(hit.group(1))
        # The title is the next non-blank line, carrying a `[N]` marker.
        while index < len(lines) and not lines[index].strip():
            index += 1
        title = ""
        if index < len(lines):
            title = _clean(re.sub(r"^\[\d+\]", "", lines[index])).strip()
            index += 1
        if not title or _looks_like_url(title):
            continue
        # The description is the block after the title's blank line.
        while index < len(lines) and not lines[index].strip():
            index += 1
        description = []
        while index < len(lines) and lines[index].strip():
            if _BING_URL.match(lines[index]):
                break
            description.append(_clean(lines[index]))
            index += 1
        # **A breadcrumb line is not a second result.** Measured: Bing prints
        # `https://archlinux.org > download` as its own line under the same
        # result, and matching URLs blindly produced the same result twice -
        # ten results, six of them the wrong thing.
        if url != previous_url:
            results.append(Result(title, url, " ".join(description)[:400], "Bing"))
        previous_url = url
        if len(results) >= _MAX_RESULTS:
            break
    return results


# --- the generic shape --------------------------------------------------------
# Brave does not split its number and its title the way Bing does, so it lands
# here rather than growing a third parser. If a provider's page reads as nothing
# under this, the provider is reported as unread rather than as empty.

_GENERIC_ENTRY = re.compile(r"^\s*(?:\d+[.)]|\[\d+\])\s+(\S.*)$")


def _parse_generic(text):
    lines = text.splitlines()
    results = []
    current = None
    for line in lines:
        match = _GENERIC_ENTRY.match(line)
        if match and len(match.group(1)) > 3:
            current = [_clean(match.group(1))]
            continue
        if current is None:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_like_url(stripped):
            results.append(Result(current[0], stripped,
                                  " ".join(current[1:])[:400], ""))
            current = None
        else:
            current.append(_clean(stripped))
    return results


# --- Brave, from its HTML -----------------------------------------------------
# Measured shape: `<div class="snippet ..." data-pos="N">` blocks, each with the
# result URL on its first anchor, the title in `class="... title ..."` and the
# description in `class="content ..."`. 12 of the 21 blocks on a real page.

_BRAVE_BLOCK = re.compile(
    r"""<div class="snippet [^"]*"[^>]*data-pos="\d+"[^>]*>(.*?)(?=<div class="snippet |$)""", re.S)
# **No backslash-escapes in these patterns.** A generator that escaped the
# quotes left a literal backslash in the raw string, so the title regex
# matched nothing and every Brave result was dropped - the href still matched,
# which is what made it look like a markup problem rather than an escaping one.
_BRAVE_TITLE = re.compile(
    r"""<div class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</div>""", re.S)
_BRAVE_DESC = re.compile(
    r"""<div class="content [^"]*"[^>]*>(.*?)</div>""", re.S)
_BRAVE_SITE = re.compile(
    r"""<div class="[^"]*site-name-content[^"]*"[^>]*>(.*?)</div>""", re.S)
_BRAVE_HREF = re.compile(r"""<a\s[^>]*href="(https?://[^"]+)""", re.I)

def _parse_brave_html(page):
    """**A block with no URL or no readable title is dropped, not invented** -
    the knowledge panel's anchors have no result URL of their own, and a parser
    that keeps them pads the count with things that are not results."""
    results = []
    seen = set()
    for block in _BRAVE_BLOCK.findall(page):
        href = _BRAVE_HREF.search(block)
        title = _BRAVE_TITLE.search(block)
        if not href or not title:
            continue
        url = href.group(1)
        cleaned = _clean(title.group(1))
        if not cleaned or url in seen:
            continue
        seen.add(url)
        desc = _BRAVE_DESC.search(block)
        results.append(Result(cleaned, url, _clean(desc.group(1)) if desc else "",
                              "Brave Search"))
        if len(results) >= _MAX_RESULTS:
            break
    return results


# --- Wiby ---------------------------------------------------------------------
# Measured shape: a bare `https://url` line, then the description indented
# under it, with no numbering and no link markers at all - the simplest of the
# four, which is why it is kept as a separate parser rather than folded into
# the generic one.

def _parse_wiby(text):
    lines = text.splitlines()
    results = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        index += 1
        if not stripped.startswith(("http://", "https://")):
            continue
        url = stripped.split(" ")[0]
        description = []
        while index < len(lines):
            nxt = lines[index].strip()
            index += 1
            if not nxt:
                break
            if nxt.startswith(("http://", "https://")):
                index -= 1
                break
            description.append(_clean(nxt))
        if url:
            results.append(Result(url.split("/")[2] if "//" in url else url,
                                  url, " ".join(description)[:400], "Wiby"))
            if len(results) >= _MAX_RESULTS:
                break
    return results


# --- the JSON APIs -------------------------------------------------------------
# Each returns `Result(title, url, description)` from its own documented shape.
# **No markup is assumed anywhere in this section**, which is the whole reason
# these are tried first: a JSON provider cannot break because an engine
# rearranged its HTML.

def _parse_archwiki(data):
    out = []
    for hit in (data.get("query", {}).get("search") or []):
        title = (hit.get("title") or "").strip()
        snippet = hit.get("snippet", "")
        # **A redirect is not a page.** Measured: the top Arch Wiki hit for
        # "arch linux install guide" was `Instalar Arch Linux a partir de um
        # Linux existente`, whose entire snippet is
        # `#redirect [[Install Arch Linux from existing Linux]]` - a signpost
        # to another page, so following it is following a second link to read
        # one thing.
        if snippet.strip().startswith("#redirect"):
            continue
        if not title:
            continue
        out.append(Result(title,
                          "https://wiki.archlinux.org/title/" +
                          title.replace(" ", "_"),
                          _clean(re.sub(r"<[^>]+>", " ", hit.get("snippet", ""))),
                          "Arch Wiki"))
    return out


def _parse_stackexchange(data):
    out = []
    for item in (data.get("items") or []):
        # **`html.unescape`, because the API returns `&#39;`.** Measured: a raw
        # read puts `Arch Linux install doesn&#39;t boot` in front of the model.
        title = html.unescape(_clean(item.get("title") or ""))
        if not title:
            continue
        out.append(Result(title, item.get("link", ""),
                          "%d answers, %d views" % (item.get("answer_count", 0),
                                                   item.get("view_count", 0)),
                          "Stack Exchange"))
    return out


def _parse_hackernews(data):
    out = []
    for hit in (data.get("hits") or []):
        title = _clean(hit.get("title") or "")
        url = hit.get("url") or ""
        if not title:
            continue
        if not url and hit.get("objectID"):
            url = "https://news.ycombinator.com/item?id=" + str(hit["objectID"])
        pts = hit.get("points") or 0
        out.append(Result(title, url,
                          "%d points, %d comments" % (pts,
                                                      hit.get("num_comments", 0)),
                          "Hacker News"))
    return out


def _parse_github(data):
    out = []
    for item in (data.get("items") or []):
        name = item.get("full_name") or ""
        if not name:
            continue
        out.append(Result(name, item.get("html_url", ""),
                          _clean(item.get("description") or ""), "GitHub"))
    return out


# --- the RSS feeds -------------------------------------------------------------
# **No markup is assumed here either** - the feed is parsed as XML, so an item
# is a field rather than a position in a page.

def _parse_googlenews(raw):
    """(results, reason). `raw` is the body, because `get_bytes` is the fetch
    that keeps the same policy - not JSON, so it does not go through the JSON
    path."""
    import xml.etree.ElementTree as ET
    if not isinstance(raw, (str, bytes)) or not raw:
        return [], "the feed came back empty"
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return [], "the feed is not valid XML (%s)" % exc.__class__.__name__
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        # **A nested `<source>` is the publisher, and it is the only thing that
        # says who is being cited.** Measured: every item on the real feed has
        # one (`9to5Linux`, `How-To Geek`, ...), and an item without one would
        # otherwise be cited to "Google" - which is the index, not the source.
        out.append(Result(_clean(title), link,
                          _clean(item.findtext("pubDate") or ""),
                          _clean(item.findtext("source") or "Google News")))
    return out, ""


_FEED_PARSERS = {
    "Google News": _parse_googlenews,
}

_JSON_PARSERS = {
    "Arch Wiki": _parse_archwiki,
    "Stack Exchange": _parse_stackexchange,
    "Hacker News": _parse_hackernews,
    "GitHub": _parse_github,
}

_PARSERS = {
    "Bing": _parse_bing,
    "DuckDuckGo": _parse_ddg,
    "Wiby": _parse_wiby,
    "Brave Search": _parse_brave_html,
}


def search(query, component="skill:web_search"):
    """(results, reason, notes).

    An empty reason means a real answer came back. **`notes` is new and it is
    not decoration**: which engines were skipped and why is invisible otherwise,
    because the first engine that answers returns early and its predecessors'
    problems were being discarded. Measured consequence: an engine that
    throttled this client was indistinguishable from one that merely failed to
    answer, so "answered by DuckDuckGo" never said Bing had been throttled.

    **Multiple engines, and the order is measured rather than preferred.**
    Asking three engines and merging costs three page fetches; asking until one
    answers costs one on a good day and three on a bad one. It stops at the
    first engine that returns anything, and says which one answered - because
    "these results" with no source is not a citable answer.
    """
    refused = gate("Searching the web")
    if refused:
        return [], refused, []

    # **The JSON APIs, before the HTML engines.** Measured: they answer, their
    # output needs no parsing, and they are published for programmatic access.
    # Collecting from all of them costs four requests; stopping at the first
    # costs one. The same stop-at-first rule applies, but the JSON providers
    # are the cheap, sturdy half so they are asked first.
    seen = set()
    merged = []
    problems = []
    for name, url, params, parser_name in _JSON_PROVIDERS:
        # **The query has to be put into the params, and the first version
        # never did.** Measured live: searching "arch linux install guide"
        # returned *"Ubuntu 16.04 root password suddenly not working"* and
        # *"Samba Shared Folder"* - because `sort=relevance` with no `q` at all
        # returns the newest questions on the site, and this skill presented a
        # confident, well-formed, **entirely wrong** answer. A parser that works
        # is not evidence the right question was asked.
        filled = dict(params)
        if "q" in filled:
            filled["q"] = query
        if "srsearch" in filled:
            filled["srsearch"] = query
        if "query" in filled:
            filled["query"] = query
        data, why = _fetch_json(url, filled, component)
        if why:
            problems.append("%s: %s" % (name, why))
            continue
        found = _JSON_PARSERS[name](data)
        for result in found:
            if result.url in seen:
                continue
            seen.add(result.url)
            merged.append(result)
            if len(merged) >= _MAX_RESULTS:
                break
        if merged:
            break
    if merged:
        return merged, "", problems
    if not problems:
        problems.append("no JSON API answered")

    # **Then the feeds, which are a different shape and are not forced into the
    # JSON loop.** Saying "Google News is news, not the web" in the answer is
    # honest; presenting it as a web search result is not.
    merged = []
    seen = set()
    for name, url, params, parser_name in _FEED_PROVIDERS:
        filled = dict(params)
        if "q" in filled:
            filled["q"] = query
        elif "query" in filled:
            filled["query"] = query
        raw, why = _fetch_feed(url, filled, component)
        if why:
            problems.append("%s: %s" % (name, why))
            continue
        found, why = _FEED_PARSERS[name](raw)
        if why:
            problems.append("%s: %s" % (name, why))
            continue
        for result in found:
            if result.url in seen:
                continue
            seen.add(result.url)
            merged.append(result)
            if len(merged) >= _MAX_RESULTS:
                break
        if merged:
            break
    if merged:
        return merged, "", problems

    # Then the HTML engines.
    merged = []
    seen = set()
    merged = []
    problems = []
    tried = 0
    for name, mode, template, parser_name in PROVIDERS:
        if tried >= _MAX_PROVIDERS:
            break
        tried += 1
        url = template.format(query=query.replace(" ", "+"))
        if mode == "html":
            text, why = _dump_html(url, component)
        else:
            text, why = _dump(url)
        if why:
            problems.append("%s: %s" % (name, why))
            continue
        # **The gate check comes first, and the order was measured rather than
        # chosen.** A gate page is short - "Please complete the following
        # challenge" is 37 bytes - so the size heuristic below would have
        # called it a throttle and the answer would have said "the engine is
        # throttling us" for a page that is a gate. Definite before inferred.
        gated = _gate_phrase(text)
        if gated:
            problems.append("%s: returned a verification page (it said \"%s\")"
                            % (name, gated))
            continue
        # **Throttling is a third outcome, and it looks like the first two.**
        # Measured: Brave answered with 27 result blocks and then, minutes
        # later and with no change to this code, served a gate page; DDG gave
        # 11 results and then 6. A page that is much smaller than the engine's
        # normal answer is a throttle, and reporting it as "no results found"
        # would be a statement about the world made from a page the engine
        # only sent to slow us down.
        if len(text) < _MIN_PAGE_BYTES:
            problems.append("%s: returned only %d bytes, which is too small to "
                            "be a results page - it is throttling this client"
                            % (name, len(text)))
            continue
        parser = globals().get(parser_name, _parse_generic)
        found = parser(text)
        if not found:
            problems.append("%s: the page came back but no result could be "
                            "read out of it" % name)
            continue
        for result in found:
            if result.url in seen:
                continue
            seen.add(result.url)
            merged.append(result)
            if len(merged) >= _MAX_RESULTS:
                break
        if merged:
            return merged, "", problems
    detail = "; ".join(problems) if problems else "no engine answered"
    return [], ("no search engine returned results (%s). A search engine that "
                "will not answer an automated reader is not the same as there "
                "being nothing to find." % detail), problems


def format_results(query, results, notes=None):
    """Claude-shaped: numbered entries with title, URL and the page's own
    words, so a later turn can cite one.

    **`notes` is printed rather than dropped**: an engine that was throttled on
    the way to an answer is part of what happened, and a reader who retries
    needs to know which one to retry.
    """
    source = results[0].source or "a search engine"
    lines = ["%d result(s) for %r (from %s):" % (len(results), query, source), ""]
    if notes:
        lines.append("Not used: " + "; ".join(notes) + ".")
        lines.append("")
    for index, result in enumerate(results, start=1):
        lines.append("[%d] %s" % (index, result.title))
        lines.append("    %s" % result.url)
        if result.description:
            lines.append("    %s" % result.description)
        lines.append("")
    lines.append("These are the engine's rankings and summaries, not Chronoa's "
                 "own claims - open a page with web_search before treating its "
                 "summary as fact.")
    return "\n".join(lines)
