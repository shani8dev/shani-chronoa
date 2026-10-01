"""Skills: who or what something is (Wikipedia), and what a word means (Wiktionary).

Both are Wikimedia's public REST APIs - no key, no account - and both are
web requests: only with the web sense on and privacy mode off, and recorded
in the egress log. The source is named in every answer, so a summary is not
mistaken for Chronoa's own knowledge.
"""

import re
import urllib.parse

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
SEARCH = "https://en.wikipedia.org/w/api.php"
DEFINE = "https://en.wiktionary.org/api/rest_v1/page/definition/"


def _gate(what: str):
    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"{what} is not available: {config.sense_allowed_reason('web')}."
    return None


def _wiki(arguments: dict) -> str:
    refused = _gate("Looking that up")
    if refused:
        return refused
    topic = (arguments.get("topic") or "").strip()
    if not topic:
        return "Look up what?"
    from shani_chronoa.netjson import get_json
    try:
        hits = get_json("skill:lookup_wikipedia", SEARCH, {"action": "opensearch", "search": topic, "limit": 1, "namespace": 0, "format": "json"})
        title = hits[1][0] if len(hits) > 1 and hits[1] else topic
        page = get_json("skill:lookup_wikipedia", SUMMARY + urllib.parse.quote(title.replace(" ", "_")))
    except Exception as e:  # noqa: BLE001
        return f"Could not look up '{topic}' on Wikipedia: {e.__class__.__name__}."
    extract = (page.get("extract") or "").strip()
    if not extract:
        return f"Wikipedia has no summary for '{topic}'."
    return f"{extract[:900]} (Wikipedia: {page.get('title', title)})"


def _define(arguments: dict) -> str:
    refused = _gate("The dictionary")
    if refused:
        return refused
    word = (arguments.get("word") or "").strip().lower()
    if not word:
        return "Which word?"
    from shani_chronoa.netjson import get_json
    try:
        data = get_json("skill:define_word", DEFINE + urllib.parse.quote(word))
    except Exception as e:  # noqa: BLE001 - a word Wiktionary lacks is a 404
        return f"Wiktionary has no definition for '{word}' ({e.__class__.__name__})."
    out = []
    for entry in (data.get("en") or [])[:3]:
        for d in (entry.get("definitions") or [])[:2]:
            text = re.sub(r"<[^>]+>", "", d.get("definition", "")).strip()
            if text:
                out.append(f"({entry.get('partOfSpeech', '').lower()}) {text}")
    return (f"{word}: " + "; ".join(out[:4]) + " (Wiktionary)") if out else f"Wiktionary has no English definition for '{word}'."


SKILLS = [
    Skill(name="lookup_wikipedia", run=_wiki, schema={
        "type": "function", "function": {
            "name": "lookup_wikipedia",
            "description": "Who or what something is - a person, place, thing or event - from Wikipedia's summary. "
                           "Requires the web sense and privacy mode off.",
            "parameters": {"type": "object", "properties": {
                "topic": {"type": "string", "description": "e.g. 'Shivaji' or 'Pune'."}}, "required": ["topic"]}}}),
    Skill(name="define_word", run=_define, schema={
        "type": "function", "function": {
            "name": "define_word",
            "description": "What a word means, from Wiktionary. Requires the web sense and privacy mode off.",
            "parameters": {"type": "object", "properties": {
                "word": {"type": "string", "description": "The word to define."}}, "required": ["word"]}}}),
]
