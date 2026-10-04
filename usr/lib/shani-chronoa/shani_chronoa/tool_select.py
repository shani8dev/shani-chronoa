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
    "weather": "weather", "forecast": "weather", "temperature": "weather",
    "rain": "weather", "umbrella": "weather", "hot": "weather", "cold": "weather",
    "where am i": "location", "where is this": "location", "gps": "location",
    "news": "web search", "travel": "web search",
    "music": "media player", "song": "media player", "pause": "media", "play": "media",
    "skip": "media next", "track": "media", "playing": "media player status",
    "who is": "wikipedia", "who was": "wikipedia", "tell me about": "wikipedia",
    "meaning": "define word", "define": "define word", "mean": "define word",
    "rupee": "currency", "rupees": "currency", "dollar": "currency", "dollars": "currency",
    "euro": "currency", "euros": "currency", "money": "currency", "exchange": "currency",
    "convert": "convert", "miles": "convert units", "km": "convert units",
    "celsius": "convert units", "fahrenheit": "convert units", "pounds": "convert units",
    "time in": "time city zone",
    "derivative": "derivative", "differentiate": "derivative", "integral": "integral",
    "integrate": "integral", "limit": "limit", "equation": "solve equation",
    "solve": "solve equation", "simplify": "simplify", "series": "series",
    "factorial": "factorial", "combinations": "comb", "permutations": "perm",
    "choose": "comb", "prime": "prime",
    "spell": "spell word", "spelling": "spell word", "spelled": "spell word",
    "command": "command manual", "man page": "command manual", "terminal": "command",
    "scan": "scan scanner", "scanner": "scan", "internet": "internet web search", "online": "internet",
    "connection": "internet", "slow": "internet", "data": "data usage", "bandwidth": "data usage",
    "screen reader": "accessibility screen reader", "zoom": "accessibility magnifier",
    "contrast": "accessibility", "bigger text": "accessibility large text",
    "keyboard on screen": "accessibility", "blind": "accessibility screen reader",
    "days until": "date", "how many days": "date", "what day": "date weekday", "from now": "date add",
    "coin": "coin random", "dice": "dice random", "roll": "dice random", "random": "random",
    "password": "password", "passphrase": "password", "ip address": "ip address", "my ip": "ip address",
    "do not disturb": "do not disturb notifications", "silence notifications": "do not disturb",
    "pdf": "read document pdf", "read this": "read document", "convert to": "convert media",
    "mp3": "convert media", "video": "convert media video", "gif": "convert media",
    "shut down": "power shutdown", "shutdown": "power shutdown", "restart": "power restart",
    "reboot": "power restart", "sleep": "power suspend", "suspend": "power suspend",
    "hibernate": "power hibernate",
    "compress": "picture compress", "resize": "picture resize", "smaller": "picture resize compress",
    "crop": "picture crop", "rotate": "picture rotate", "black and white": "picture grayscale",
    "grayscale": "picture grayscale", "metadata": "picture metadata", "photo": "picture image wallpaper",
    "image": "picture", "jpg": "picture", "png": "picture", "webp": "picture",
    "base64": "encode", "encode": "encode", "decode": "encode decode", "morse": "morse encode",
    "binary": "encode binary", "hex": "encode colour hex", "colour": "colour", "color": "colour",
    "rgb": "colour", "emoji": "emoji", "symbol": "emoji symbol", "calendar": "calendar month",
    "stopwatch": "stopwatch", "lap": "stopwatch", "clean up": "cleanup", "free space": "cleanup",
    "free up": "cleanup", "headphones": "bluetooth devices connect", "earbuds": "bluetooth devices connect",
    "speaker": "bluetooth devices", "vpn": "vpn", "tailscale": "tailscale", "install": "install app",
    "uninstall": "install app remove", "flathub": "install app",
    "sunset": "weather", "sunrise": "weather", "humid": "weather", "wind": "weather",
    "route": "web search", "train": "web search", "flight": "web search",
    "look": "search", "google": "web search",
    "louder": "volume", "quieter": "volume", "sound": "volume", "loud": "volume",
    "dim": "brightness", "brighter": "brightness", "darker": "brightness",
    "wifi": "wifi network", "internet connection": "wifi",
    "time": "datetime clock", "date": "datetime", "day": "datetime", "today": "datetime",
    "remind": "reminder", "alarm": "timer", "countdown": "timer",
    "launch": "open application", "start": "open application",
    "picture": "screenshot", "background": "wallpaper photo video",
    "dark": "theme", "light": "theme", "battery": "battery power",
    "charge": "battery", "lock": "lock screen", "translate": "translate",
    "math": "calculate", "plus": "calculate", "minus": "calculate", "times": "calculate",
    "sum": "calculate", "percent": "calculate",
    "draw": "generate image", "paint": "generate image", "sketch": "generate image",
    "illustration": "generate image", "imagine": "generate image", "picture of": "generate image",
    "image of": "generate image", "make a picture": "generate image",
    "faces": "photo video faces", "blur": "photo video effect", "cartoon": "photo video effect",
    "sepia": "photo video effect", "objects": "photo video identify",
    "what is in this video": "photo video describe", "happens in": "photo video describe",
    "frames": "photo video keyframes", "clip": "photo video", "footage": "photo video",
    "subtitles": "recording subtitles", "captions": "recording subtitles", "transcribe": "recording text",
    "transcript": "recording text", "who said": "recording speakers", "meeting": "recording speakers",
    "noise": "recording clean", "noisy": "recording clean", "podcast": "recording", "voice note": "recording",
    "recording": "recording", "what do you hear": "recording listen sounds", "can you hear": "recording listen sounds",
    "my photos": "photos search", "pictures of": "photos search", "photo of": "photos search",
    "receipt": "photos search", "find the photo": "photos search", "find the picture": "photos search",
    "talk about": "conversations search", "talked about": "conversations search", "discussed": "conversations search",
    "did we": "conversations search", "we said": "conversations search", "last time": "conversations search",
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
    # "15%" is a percentage, and the word boundary in the synonym match never sees a bare "%"
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
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


def learned_weights() -> "Dict[str, float]":
    """Per-tool multipliers from what happened last time, if there is a history.

    Read through `learning`, which cannot grant a tool - only reorder the ones
    this function already returned. A failure while reading it is a failure to
    *improve* selection, not a reason to refuse to answer, so it returns nothing
    rather than raising.
    """
    try:
        from .learning import weights_from_tracker
        return weights_from_tracker(_tracker())
    except Exception:
        return {}


def _tracker():
    """`tools._TRACKER`, the one live tracker, or None if there is not one.

    Reached through the module rather than by constructing a second one: the
    ring buffer is a process-wide record of what actually ran, and a fresh
    `ToolTracker()` would be an empty history - learning from nothing and
    concluding every tool is unproven.
    """
    try:
        from . import tools
        return getattr(tools, "_TRACKER", None)
    except Exception:
        return None


def ranked(request: str, tools: Iterable[dict],
           learned: Optional[Dict[str, float]] = None) -> "List[tuple]":
    """(score, tool name, how many request words are in the tool's own name), best first; only tools that matched."""
    tools = list(tools)
    query = set(_query_words(request))
    if not query:
        return []
    texts = [_tool_text(t) for t in tools]
    # rarer words say more: idf over the tools' own text
    df: Dict[str, int] = {}
    for name, body in texts:
        for w in set(name) | set(body):
            df[w] = df.get(w, 0) + 1
    n = len(tools)
    scored = []
    for tool, (name, body) in zip(tools, texts):
        score, name_hits = 0.0, 0
        for w in query:
            idf = math.log(1 + n / (1 + df.get(w, 0)))
            if w in name:
                score += 3 * idf
                name_hits += 1
            elif w in body:
                score += idf
        if score > 0:
            scored.append((score, _name(tool), name_hits))
    if learned is None:
        learned = learned_weights()
    if learned:
        # Applied after the matcher's own sort, and re-sorted stably, so a
        # learned demotion outranks a slightly better text match while two
        # tools the matcher liked equally keep the matcher's order.
        from .learning import reorder
        return [(score, name, hits) for score, name, hits in
                reorder([(s, n, h) for s, n, h in scored], learned)]
    scored.sort(key=lambda x: -x[0])
    return scored


def select_tools(request: str, tools: Iterable[dict], limit: int = DEFAULT_LIMIT,
                 in_use: Optional[Iterable[str]] = None) -> List[dict]:
    """The schemas to send for `request`, in the order `tools` lists them."""
    tools = list(tools)
    keep: Set[str] = set(CORE) | set(in_use or ())
    scored = ranked(request, tools)
    # a tool matching only a generic word ("set", "file") beside a strong
    # match is noise that costs tokens
    floor = scored[0][0] / 3 if scored else 0
    keep |= {name for score, name, _hits in scored[:limit] if score >= floor}
    return [t for t in tools if _name(t) in keep]


def confident(request: str, tools: Iterable[dict], top: int = 3) -> List[str]:
    """The few tools a request almost certainly wants, or [] when it is not that clear.

    Clear means the best match has a request word in the tool's own *name* -
    "lock my screen" and `lock_screen`, "mute" and `set_mute` - which is the
    strongest signal the scoring has; a match on description words alone is
    not enough. Used to give a small model a second, narrower chance when it
    answered in words to what was plainly a command (see local_llm).
    """
    scored = ranked(request, tools)
    if not scored or scored[0][2] == 0:
        return []
    best = scored[0][0]
    return [name for score, name, hits in scored[:top] if hits and score >= best / 2]
