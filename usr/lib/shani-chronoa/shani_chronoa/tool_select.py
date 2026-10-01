"""Which tool schemas to SEND with a request: the ones this request could use.

Every request used to carry all 80 tool schemas - 47,329 characters, about
11,800 tokens - while the context window Chronoa asks Ollama for is 8192
tokens on the best hardware tier, 4096 and 2048 below it. Every request
overflowed on every machine, Ollama dropped what did not fit, and the model
answered a prompt it never saw whole; a small model on a CPU also spent
minutes reading the schemas before each turn (found by shani-testbed's
chronoa-voice run: four minutes for "what is two plus two", and a stray
press_key call).

This chooses what is sent, not what may run: execute_tool still checks the
fixed whitelist, so a tool that was not sent cannot be called any more than
before, and one that was sent is no more allowed than it was.

Scoring is plain word overlap between the request and each tool's name,
description and parameter descriptions, weighted toward the name, with a
few everyday synonyms ("weather" -> web search, "louder" -> volume) - no
model, no index, nothing to download. A short core is always sent, and so is
any tool the conversation has already used this turn.
"""

import math
import re
from typing import Dict, Iterable, List, Optional, Set

#: How many scored tools to send, besides the core and the ones in use.
DEFAULT_LIMIT = 8

#: Always sent: asking back, the time, the web, and "what can you do".
CORE = ("ask_user", "get_datetime", "web_search", "list_capabilities")

_STOP = set("""a an the is are was were be been am i me my you your it its this that these those
to of in on at for from by with and or but if then so as do does did can could would should
will shall may might must please what whats which who whom how why where when there here
some any all every no not now just also too very much many more most up down out over
hey hi hello ok okay chronoa tell show give get let make want need like know""".split())

#: Everyday words for what a tool does, where its own text uses others.
_SYNONYMS: Dict[str, str] = {
    "weather": "web search", "forecast": "web search", "temperature": "web search",
    "rain": "web search", "news": "web search", "travel": "web search",
    "route": "web search", "train": "web search", "flight": "web search",
    "look": "search", "google": "web search", "internet": "web search",
    "louder": "volume", "quieter": "volume", "sound": "volume", "loud": "volume",
    "dim": "brightness", "brighter": "brightness", "darker": "brightness",
    "wifi": "wifi network", "internet connection": "wifi",
    "time": "datetime clock", "date": "datetime", "day": "datetime", "today": "datetime",
    "remind": "reminder", "alarm": "timer", "countdown": "timer",
    "launch": "open application", "start": "open application",
    "picture": "screenshot", "photo": "wallpaper", "background": "wallpaper",
    "dark": "theme", "light": "theme", "battery": "battery power",
    "charge": "battery", "lock": "lock screen", "translate": "translate",
    "math": "calculate", "plus": "calculate", "minus": "calculate", "times": "calculate",
    "sum": "calculate", "percent": "calculate",
}


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "es", "s", "ed"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _words(text: str) -> List[str]:
    return [_stem(w) for w in re.findall(r"[a-z][a-z0-9]*", text.lower().replace("_", " "))
            if w not in _STOP and len(w) > 1]


def _query_words(text: str) -> List[str]:
    lowered = text.lower()
    extra = " ".join(v for k, v in _SYNONYMS.items() if re.search(rf"\b{re.escape(k)}\b", lowered))
    return _words(text + " " + extra)


def _tool_text(tool: dict) -> "tuple[List[str], List[str]]":
    fn = tool.get("function", {})
    name = _words(fn.get("name", ""))
    body = fn.get("description", "")
    for prop in (fn.get("parameters", {}).get("properties", {}) or {}).values():
        if isinstance(prop, dict):
            body += " " + str(prop.get("description", ""))
    return name, _words(body)


def _name(tool: dict) -> str:
    return tool.get("function", {}).get("name", "")


def select_tools(request: str, tools: Iterable[dict], limit: int = DEFAULT_LIMIT,
                 in_use: Optional[Iterable[str]] = None) -> List[dict]:
    """The schemas to send for `request`, in the order `tools` lists them."""
    tools = list(tools)
    keep: Set[str] = set(CORE) | set(in_use or ())
    query = set(_query_words(request))
    if query:
        texts = [_tool_text(t) for t in tools]
        # rarer words say more: idf over the tools' own text
        df: Dict[str, int] = {}
        for name, body in texts:
            for w in set(name) | set(body):
                df[w] = df.get(w, 0) + 1
        n = len(tools)
        scored = []
        for tool, (name, body) in zip(tools, texts):
            score = 0.0
            for w in query:
                idf = math.log(1 + n / (1 + df.get(w, 0)))
                if w in name:
                    score += 3 * idf
                elif w in body:
                    score += idf
            if score > 0:
                scored.append((score, _name(tool)))
        scored.sort(key=lambda x: -x[0])
        # a tool matching only a generic word ("set", "file") beside a strong
        # match is noise that costs tokens
        floor = scored[0][0] / 3 if scored else 0
        keep |= {name for score, name in scored[:limit] if score >= floor}
    return [t for t in tools if _name(t) in keep]
