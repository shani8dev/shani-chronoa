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

from shani_chronoa import websearch
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
            "Give a search query, a specific URL, or both. For a scholarly "
            "question - a paper, a preprint, prior work, 'what does the "
            "research say' - set source=arxiv and it searches the arXiv preprint "
            "archive instead of the web. For news headlines use news; to click, "
            "fill in or book something on a site use browse. Requires the web "
            "sense to be enabled and privacy mode to be off."
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
                "source": {
                    "type": "string",
                    "enum": ["web", "arxiv"],
                    "description": (
                        "Where to look. 'web' (default) searches the usual "
                        "engines. 'arxiv' searches the arXiv preprint archive - "
                        "use it for papers, research and prior work."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

#: **arXiv is a preprint archive, and the answer says so on every result.**
#: A preprint is not peer review, not a web page, and not evidence that anything
#: was published - so a reader who gets twelve titles with no such sentence can
#: cite one as though it were a journal paper. It is one line, and it is the
#: whole difference between "here are papers" and "here are things on the
#: internet".
_ARXIV_SCOPE = (
    "These are arXiv preprints: not peer-reviewed, and not the whole of what "
    "has been published. Open one with web_search before relying on it."
)


def _run(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    url = (arguments.get("url") or "").strip()
    if not query and not url:
        return "No search query or URL given."

    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"Web search is not permitted: {config.sense_allowed_reason('web')}."

    # **The arXiv branch first, and it is the only one that honours `url`.**
    # Asking arXiv about a URL is not a thing arXiv does, so it is refused by
    # name rather than answered by searching for the URL's characters - which is
    # the confidently-wrong answer this skill's own history is about.
    if (arguments.get("source") or "").strip().lower() == "arxiv":
        if url:
            return ("arXiv searches papers, not pages, so a URL cannot be given "
                    "to it. Use this with a query instead, or fetch the URL "
                    "itself by leaving source at 'web'.")
        results, reason, notes = websearch.arxiv_search(query)
        if reason:
            return reason
        return websearch.format_results(query, results, notes) + "\n\n" + _ARXIV_SCOPE

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
        # **`websearch.search`, not a fixed string.** Three of the engines this
        # DuckDuckGo path used to depend on still answer, so a challenge on the
        # one that was tried is a reason to try the others rather than a dead
        # end - the fixed-string answer that used to be returned here is exactly
        # what made the old skill useless.
        results, _why, notes = websearch.search(query)
        if results:
            return websearch.format_results(query, results, notes)
        return (
            "The search engine did not return results, and no other engine "
            f"answered either (it said \"{hit}\")). That is the engines "
            "refusing an automated reader, and it is not a statement that there "
            "is nothing to find.\n\n"
            "Two paths that still work:\n"
            "- fetch a **specific URL** instead - give me the address and I will "
            "read it;\n"
            "- use news, lookup_wikipedia or maps, which query services that do "
            "allow automated readers.")
    return body


SKILLS = [Skill(name="web_search", schema=_SCHEMA, run=_run)]
