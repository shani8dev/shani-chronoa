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

#: Always sent: asking back, the time, and the web.
#:
#: `list_capabilities` used to be in here too, and the tool-selection eval
#: (2026-10-09, Qwen3-1.7B, 67 cases) showed exactly what an unconditional floor
#: costs: on "thanks!" the model called it. A small model handed a tool list
#: treats every turn as an instruction to use one, so each unconditional member
#: is a tool it may reach for when the right answer is to just reply. It is a
#: fine tool and it ranks on merit - it just must not be free.
CORE = ("ask_user", "get_datetime", "web_search")

_STOP = set("""a an the is are was were be been am i me my you your it its this that these those
to of in on at for from by with and or but if then so as do does did can could would should
will shall may might must please what whats which who whom how why where when there here
some any all every no not now just also too very much many more most up down out over
hey hi hello ok okay chronoa tell show give get let make want need like know""".split())

#: Everyday words for what a tool does, where its own text uses others.
_SYNONYMS: Dict[str, str] = {
    # Keyboard shortcuts. The words people actually use for this are not the
    # words the schema uses ("action"/"keys"), and without these the router
    # answered `list_shortcuts` for "what does Super+Tab do" while routing
    # "which keys are bound" and "what are my keyboard shortcuts" to
    # `get_datetime` - the skill existing and unreachable for the plainest
    # phrasing of its own question. Measured over six phrasings: 2 before.
    "shortcut": "keyboard shortcuts", "shortcuts": "keyboard shortcuts",
    "keybinding": "keyboard shortcuts", "keybindings": "keyboard shortcuts",
    "hotkey": "keyboard shortcuts", "hotkeys": "keyboard shortcuts",
    "bound to": "keyboard shortcuts", "what key": "keyboard shortcuts",
    "which key": "keyboard shortcuts", "what does this key": "keyboard shortcuts",
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
    # folder <-> directory: measured 2026-10-08, "what's in my Downloads folder?"
    # matched nothing real, and twelve noise tools were offered instead.
    "folder": "directory list", "folders": "directory list",
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
    "speaker": "bluetooth devices", "find my watch": "find device", "where is my watch": "find device",
    "find my band": "find device", "lost my watch": "find device",
    "steps": "watch", "how far did i walk": "watch", "sleep last night": "watch", "how did i sleep": "watch",
    "spo2": "watch measure", "blood oxygen": "watch measure", "blood pressure": "watch measure",
    "stress": "watch", "workout": "watch", "my watch": "watch", "vpn": "vpn", "tailscale": "tailscale", "install": "install app",
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
            short = word[: -len(suffix)] + ("y" if suffix == "ies" else "")
            # "running" -> "runn", "stopping" -> "stopp". English doubles the
            # final consonant when a suffix goes on and takes one of them off
            # again, so the doubled letter has to go with the suffix - otherwise
            # "running", "run" and "runner" are three different words to
            # everything downstream. Only after the length guard, so "sing" and
            # "bed" are untouched.
            if suffix in ("ing", "ed") and len(short) > 2 \
                    and short[-1] == short[-2] and short[-1] not in "aeiousl":
                short = short[:-1]
            return short
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


def _tool_options(tool: dict) -> "set":
    """The words of a tool's enum values: `subtitles`, `blur_faces` -> blur, faces.

    The most precise words a request can contain - "make subtitles", "blur the
    faces" - are often a tool's literal option values, and they were not read at
    all: the small model picked `read_video` and `edit_image` instead (2026-10-08).
    """
    words = set()
    for prop in (tool.get("function", {}).get("parameters", {}).get("properties", {}) or {}).values():
        for value in (prop.get("enum") or []) if isinstance(prop, dict) else []:
            words.update(_words(str(value)))
    return words


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


#: Loaded once, lazily. Absent is the normal state, exactly as `_outcome_model`
#: is: there is no distilled router until one has been trained from a teacher's
#: agreements and has beaten the majority baseline on held-out requests.
_ROUTER = None
_ROUTER_TRIED = False

#: How far below the best score a skill may be and still be promoted. A log
#: score gap, not a probability: the router's scores are not calibrated
#: frequencies, so converting them into "83% sure" would invent a number.
DISTILLED_MARGIN = 2.0


def distilled_router():
    """The distilled router, or None. Never raises.

    A router whose own report says it never beat the majority baseline, or
    whose file cannot account for itself, is refused at the load path - so
    reaching `None` here means "there is nothing trustworthy to add", not
    "something went wrong".
    """
    global _ROUTER, _ROUTER_TRIED
    if _ROUTER_TRIED:
        return _ROUTER
    _ROUTER_TRIED = True
    try:
        from .distill import load_router
        _ROUTER = load_router()
    except Exception:  # noqa: BLE001 - a missing router must not break selection
        _ROUTER = None
    return _ROUTER


def distilled(request: str, tools: Iterable[dict], limit: int = 2,
              margin: float = DISTILLED_MARGIN) -> List[str]:
    """Skill names a distilled router would have this request sent.

    Two rules, both load-bearing:

    - **Only skills already in `tools` can come back.** The router ranks; it
      never widens what exists, which is the same division the whitelist makes
      and the reason this can be called without re-checking anything.
    - **A router that is not decisive adds nothing.** With no student there is
      nothing; with a student that cannot separate the top skill from the
      runner-up, its opinion is not worth a schema's tokens.
    """
    router = distilled_router()
    if router is None:
        return []
    available = {_name(t) for t in tools}
    scored = router.score(request)
    if not scored:
        return []
    best = scored[0][1]
    return [name for name, score in scored[:limit]
            if name in available and best - score <= margin]


def ranked(request: str, tools: Iterable[dict],
           learned: Optional[Dict[str, float]] = None) -> "List[tuple]":
    """(score, tool name, how many request words are in the tool's own name), best first; only tools that matched."""
    tools = list(tools)
    query = set(_query_words(request))
    if not query:
        return []
    # Option values count only when the person said the word: a synonym's
    # expansion ("travel" -> "web search") hit `search`, an option of three
    # unrelated tools, and inflated a travel question by 550 tokens.
    said = set(_words(request or ""))
    texts = [_tool_text(t) for t in tools]
    options = [_tool_options(t) for t in tools]
    # Only distinctive option words count: "subtitles" is one tool's option,
    # "from" or "press" are many tools' - those pulled 874-token `browse` into
    # "press the enter key" and photos/install_app into a travel question.
    option_df: Dict[str, int] = {}
    for opts in options:
        for w in opts:
            option_df[w] = option_df.get(w, 0) + 1
    # rarer words say more: idf over the tools' own text
    df: Dict[str, int] = {}
    for name, body in texts:
        for w in set(name) | set(body):
            df[w] = df.get(w, 0) + 1
    n = len(tools)
    scored = []
    for tool, (name, body), opts in zip(tools, texts, options):
        score, name_hits = 0.0, 0
        for w in query:
            idf = math.log(1 + n / (1 + df.get(w, 0)))
            if w in name:
                score += 3 * idf
                name_hits += 1
            elif w in opts and w in said and option_df.get(w, 0) <= 3:
                score += 2 * idf
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
    # A turn that is conversation gets the smallest honest set: there is nothing
    # to call, and sending eight tools to "thanks!" is what produced two of the
    # five eval misses (2026-10-09). `in_use` still wins, so a turn mid-task is
    # never starved of the tool it is already using.
    if wants_a_conversation(request):
        return [t for t in tools if _name(t) in {"ask_user"} | set(in_use or ())]
    scored = ranked(request, tools)
    # a tool matching only a generic word ("set", "file") beside a strong
    # match is noise that costs tokens
    floor = scored[0][0] / 3 if scored else 0
    keep |= {name for score, name, _hits in scored[:limit] if score >= floor}
    # A distilled router may add a skill the word matcher scored zero. That is
    # the point of having one: "increase the sound" shares no word with
    # `set_volume`, and the human still meant the volume. It cannot add a skill
    # that is not already in `tools`, so this widens what is *sent*, never what
    # may be *run* - which is the guarantee this function already makes.
    keep |= set(distilled(request, tools))
    held = set(in_use or ())
    lowered = (request or "").lower()
    keep -= {name for name, cue in _CUES.items() if name not in held and not cue.search(lowered)}
    # `browse` is ~850 tokens; it comes in through `wants_a_browser` below, never
    # through a stray verb that happens to be one of its actions.
    if "browse" not in held:
        keep.discard("browse")
    if _SMALL_TALK.search(lowered):
        keep -= {"web_search"} - held
    # "the file manager" is an app, not a file: at temperature 0 Qwen3-1.7B
    # opened an invented path with `open_file` every time (2026-10-08).
    if _FILES_APP.search(lowered):
        keep -= {"open_file"} - held
    if wants_a_browser(request):
        keep.add("browse")
        if not _FILE_OR_APP.search(request or ""):
            keep -= _DISPLACED_BY_A_SITE - set(in_use or ())
    return [t for t in tools if _name(t) in keep]


_TLDS = r"(?:com|org|net|io|dev|in|co|ai|app|uk|de|edu|gov)"
#: A website, written or SAID: "blazedemo.com", and "blaze demo dot com" (what a
#: transcript of speech gives), or a plain "website" / "web page" / "online".
_SITE = re.compile(r"https?://|\bwww\.|\b[a-z0-9-]+\." + _TLDS + r"\b|\bdot " + _TLDS + r"\b"
                   r"|\bweb ?sites?\b|\bweb ?pages?\b|\bonline\b", re.I)
#: Tools that a website request drew in only through a generic verb ("open the
#: site", "find the cheapest flight") - each a wrong first move a small model
#: can make. Measured 2026-10-08: offered all of these and not `browse`,
#: Qwen3-1.7B opened the desktop browser with `open_application`.
_DISPLACED_BY_A_SITE = frozenset({
    "open_application", "open_file", "find_files", "find_and_replace", "find_recently_modified",
    "search_file_contents", "search_documents", "directory_tree", "json_query", "find_emoji"})
#: Words that mean the request really is about files or an app, so nothing is displaced.
_FILE_OR_APP = re.compile(r"\b(file|files|folder|folders|directory|document|documents|pdf|download(?:s|ed)?|"
                          r"desktop|app|application|program)\b|~/|/home/", re.I)
_ACT_ON_A_PAGE = re.compile(r"\b(book|booking|fill (?:in|out)|sign (?:in|up)|log ?in|checkout|check out|"
                            r"add to cart|order|purchase|buy|submit|click|reserve)\b", re.I)


#: Tools offered only when the request carries their cue. Each one was offered
#: on a generic word and taken by a small model when it should not have been
#: (measured 2026-10-08, Qwen3-1.7B): `default_apps` for "open firefox" (which
#: would have changed the default browser), `get_world_time` with an invented
#: city for "what time is it?", `explain_command` for "explain what a black hole
#: is", `android_device` for "what's in my Downloads folder?". The rest were
#: offered as noise in the same runs.
_CUES = {name: re.compile(cue) for name, cue in {
    "default_apps": r"\bdefault",
    "get_world_time": r"\btime\b.*\b(?:in|at|for)\s+[a-z]{3,}|\b(?:in|at)\s+[a-z]{3,}\b.*\btime\b|time ?zones?"
                      r"|\bworld (?:time|clock)",
    "explain_command": r"`|\bcommand|\bshell\b|\bterminal\b|\bflags?\b|(?:^|\s)-{1,2}[a-z]|\b(?:sudo|ls|grep|awk|sed|chmod|"
                       r"chown|tar|curl|wget|git|ssh|rsync|systemctl|journalctl|pacman|apt|dd|ps|kill|df|du|mount)\b",
    "set_scaling": r"\bscal|\btext size|\bfont size|\bzoom|\bbigger|\bsmaller|\bhidpi|\bresolution",
    "android_device": r"\bandroid\b|\badb\b|\bphone\b|\bmobile\b|\btablet\b",
    "install_model": r"\bmodels?\b|\bllm\b|\bqwen|\bllama|\bai\b",
    "recommend_model": r"\bmodels?\b|\bllm\b|\bqwen|\bllama|\bai\b",
    "speed_test": r"\bspeed|\bbandwidth|\bslow|\bfast|\bmbps|\binternet",
    "compute_hash": r"\bhash|\bchecksum|\bsha|\bmd5|\bverif|\bintegrity",
    "undo_last_change": r"\bundo|\brevert|\bput (?:it )?back|\brestore",
}.items()}

_FILES_APP = re.compile(r"\bfiles? (?:manager|browser|explorer|app)\b|\bnautilus\b|\bdolphin\b")

#: Asking for a joke, a story or a poem is a request for words, not a search.
#: `web_search` is otherwise offered on every turn, and a small model called it
#: for "tell me a joke" (2026-10-08). Factual questions keep it.
_SMALL_TALK = re.compile(r"\b(?:tell|say|write|make up|give)\b[^.?!]{0,20}\b(?:joke|story|poem|haiku|riddle|"
                         r"limerick|pun|bedtime story)s?\b")


#: Turns that are conversation, not a task: greetings, thanks, and "explain X"
#: where X is general knowledge. Both eval misses were this shape - "thanks!"
#: called `list_capabilities`, "explain gravity" called `web_search` - and no
#: amount of description trimming reaches them, because the tools were sent at
#: all. The answer is fewer tools, and on these turns the smallest honest set:
#: ask back, and nothing else to press.
#: Arithmetic, kept out of `CONVERSATIONAL` because it is a task even when it is
#: phrased as a question: "what is two plus two" sent only `ask_user`, so
#: `calculate` was never on offer.
#:
#: **A bare number word is not arithmetic.** An earlier version listed `two` on
#: its own, so "explain what a black hole is in two sentences" was read as a sum,
#: offered `web_search`, and the one remaining miss in the 68-case eval was a
#: *length instruction*. A number word only means arithmetic beside an operator
#: ("in two sentences", "in three bullet points" and "give me five ideas" are all
#: requests to be answered in prose), so each shape below names its operator.
#:
#: It is a separate compiled pattern rather than more concatenated fragments
#: because hand-balancing parentheses across string literals is exactly how the
#: first attempt at this arrived as an `unbalanced parenthesis` at import - a
#: module that cannot be imported at all, which no caller would report as
#: "arithmetic is mis-detected".
_NUMBERS = r"(?:two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million)"
_OPERATORS = r"(?:plus|minus|times|divided|multiplied|over|by)"
#: A fraction has no operator at all ("two thirds of six"), so it needs its own.
_FRACTIONS = r"(?:thirds?|quarters?|halves|halfs?|eighths?|sixths?|twelfths?)"

ARITHMETIC = re.compile(
    rf"\d\s*(?:[-+*/x×÷%]|\b(?:plus|minus|times|divided|multiplied|over)\b)\s*\d"
    rf"|\b{_NUMBERS}\s+\b{_OPERATORS}\b"
    rf"|\b(?:two|three|four|five|six|seven|eight|nine|ten)\s+{_FRACTIONS}\b"
    rf"|\b(?:plus|minus|times|divided|multiplied|percent|squared|cubed|root|"
    rf"factorial)\b"
    rf"|\d",
    re.I)

CONVERSATIONAL = re.compile(
    r"^\s*(?:thanks?|thank you|cheers|ok(?:ay)?|cool|nice|great|perfect|got it|"
    r"understood|hi|hey|hello|good (?:morning|afternoon|evening|night)|bye|goodbye|"
    r"yes|no|sure)\b[\s!.?]*$"
    r"|^\s*(?:what can you do|who are you|what are you|help)\s*\??\s*$"
    r"|^\s*(?:explain|describe|what is|what are|how does|why (?:is|do|does))\s+"
    r"(?:a |an |the )?"
    # A request naming one of these is about the machine, not a question to
    # answer in words.
    r"(?!.*\b(?:file|folder|window|app|setting|image|photo|pdf|"
    r"document|this computer|my |weather|forecast|temperature|time|clock|"
    r"timer|alarm|date|calendar|volume|battery|network|wifi|bluetooth|"
    r"music|note|song|call|message|email)\b)",
    re.I)


def wants_a_conversation(request: str) -> bool:
    """Is this a turn to reply to rather than a task to call a tool for?"""
    text = request or ""
    return bool(CONVERSATIONAL.search(text)) and not ARITHMETIC.search(text)


def wants_a_browser(request: str) -> bool:
    """Whether the request names a website or asks for something done on one.

    The word matcher scores tools by shared words, and "book the cheapest flight
    on blazedemo.com" shares none with `browse` - measured 2026-10-08 in the real
    app, the model was never offered it and tried to book with `web_search`,
    which can only fetch a page. A site and an action on a page are the signal.
    """
    text = request or ""
    return bool(_SITE.search(text) or _ACT_ON_A_PAGE.search(text))


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
