"""Skill: look something up on the web and actually read the answer.

The previous version of this skill opened DuckDuckGo in the default browser
and returned the fixed string "Searching the web for 'X'." The model was
told the user to go and look, and then had no idea what was there - it could
only answer from its own weights or invent something. This version fetches a
page over HTTP and returns the text of it, so the tool result carries real
content into the next turn of `assistant.py`'s tool loop.

The retrieval machinery lives in `shani_chronoa.webtext`; the same consent
gate and the Percept-shaped entry point live in `shani_chronoa.senses.web`,
because a web fetch is a *perception* (it has a lifetime, a source and a sensitivity) and
that contract is the senses' job, not a skill's. This module is the
tool-call surface over it: the model's entry point, shaped as an Ollama
function schema.

Consent: every path checks `ChronoaConfig().sense_allowed("web")`, which
requires `web-sense-enabled` (default false) *and* privacy mode off, and
refuses with `sense_allowed_reason()` when denied - the same authoritative
policy the rest of the codebase uses, read fresh per call so a mid-session
toggle takes effect immediately. A fresh `ChronoaConfig()` is built on every
call for that reason; nothing here is cached.

The query path fetches DuckDuckGo's no-JavaScript `lite` endpoint rather
than the main site, whose results page renders client-side and would
therefore extract to near-nothing. That endpoint is undocumented and may
change or rate-limit; when it does, this tool says what actually came back
(empty text is reported as an error) instead of pretending to have searched.
Direct `url` fetches carry no such dependency and are the reliable path.

No browser is launched, and nothing here launches a process: `tools.py`
already runs this module in a sandboxed subprocess, and the LLM-issued
arguments are the only untrusted input (defensively re-read with `.get()`,
as every other skill does).
"""

import logging
import urllib.parse

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.webtext import RetrievalError, render, retrieve
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

# The no-JavaScript DuckDuckGo results page. The main site's results are
# rendered in the browser, so fetching its HTML yields a shell.
SEARCH_ENDPOINT = "https://lite.duckduckgo.com/lite/?q="

#: **Phrases that mean the page is a challenge, not a result.** Measured live on
#: 2026-10-10: this endpoint answers HTTP 200 with
#:
#:     Unfortunately, bots use DuckDuckGo too.
#:     Please complete the following challenge to confirm this search was made
#:     by a human.
#:     Select all squares containing a duck:
#:
#: and the previous version of this skill handed that to the model as the search
#: result - so it answered "what did the search say" with "select all squares
#: containing a duck", a confident wrong answer with nothing marking it as one.
#: **A 200 is not evidence of results.**
_CHALLENGE = (
    "complete the following challenge",
    "confirm this search was made by a human",
    "select all squares containing",
    "unfortunately, bots use duckduckgo too",
    "are you a robot",
    "cf-challenge",
    "checking your browser before",
)


def _challenge_phrase(text: str) -> str:
    """The phrase that identified a challenge page, or an empty string."""
    low = text.lower()
    for phrase in _CHALLENGE:
        if phrase in low:
            return phrase
    return ""

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Look something up on the web and return what the pages actually "
            "say: facts, prices, how-tos, anything no more specific tool covers. "
            "Give a search query, a specific URL, or both. For news headlines "
            "use news; to click, fill in or book something on a site use browse. "
            "Requires the web sense to be enabled and privacy mode to be off."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for, if not fetching a specific page.",
                },
                "url": {
                    "type": "string",
                    "description": "An absolute http(s) URL to read directly, if you have one.",
                },
            },
            "required": ["query"],
        },
    },
}


def _run(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    url = (arguments.get("url") or "").strip()
    if not query and not url:
        return "No search query or URL given."

    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"Web search is not permitted: {config.sense_allowed_reason('web')}."

    if url:
        target = url
    else:
        target = SEARCH_ENDPOINT + urllib.parse.quote(query)

    try:
        page = retrieve(target)
    except RetrievalError as e:
        # Say what failed and why. Returning a fixed "Searching the web..."
        # string is what made this skill useless in the first place.
        return f"Could not search the web for '{query}': {e}"

    try:
        body = render(page)
    except Exception as exc:
        # A rendering failure must not look like an empty result.
        return (f"The page came back but could not be shown: {exc}. That is a "
                "problem here, not an empty result.")

    hit = _challenge_phrase(body)
    if hit:
        return (
            "The search engine did not return results: it returned a "
            f"human-verification challenge instead (it said \"{hit}\"). That "
            "is the engine refusing an automated reader, and it is not a "
            "statement that there is nothing to find.\n\n"
            "Two paths that still work:\n"
            "- fetch a **specific URL** instead - give me the address and I will "
            "read it;\n"
            "- use news, lookup_wikipedia or maps, which query services that do "
            "allow automated readers.")
    return body


SKILLS = [Skill(name="web_search", schema=_SCHEMA, run=_run)]
