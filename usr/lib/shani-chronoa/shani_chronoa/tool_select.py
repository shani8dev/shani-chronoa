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

import json
import math
import re
from typing import Dict, Iterable, List, Optional, Set

#: How many scored tools to send, besides the core and the ones in use.
DEFAULT_LIMIT = 8

#: The smallest context window any supported tier has, and the share of it a
#: tool selection may take. The rest is the conversation - the request, the
#: reply, and the system prompt - and a request that is rejected before the
#: model reads a word is the failure selection exists to prevent.
WINDOW_TOKENS = 2048
SELECTION_SHARE = 0.75

#: Chars per token used **for the size cap**, deliberately the pessimistic 4
#: rather than `local_llm.CHARS_PER_TOKEN`'s measured 3. The cap has to hold on
#: the machine that tokenises most favourably, or the guarantee it makes is one
#: this particular box happens to satisfy.
_CAP_CHARS_PER_TOKEN = 4


def _fits(request_tools: List[dict], budget_tokens: int) -> bool:
    """Whether this selection's schemas fit in the budget.

    Measured the same way the test that guards this measures it - `len(json.dumps(...))`
    over 4 chars per token - so the cap and its guard cannot disagree about what
    "fits" means, which is the shape of failure this function is here to remove.
    """
    return len(json.dumps(request_tools)) // _CAP_CHARS_PER_TOKEN <= budget_tokens


def _selection_chars(kept_names: List[str], chars_by_name: Dict[str, int]) -> int:
    """**Exactly** `len(json.dumps([the schemas for kept_names]))`, in O(k).

    `_fits` measures the selection by serialising it, and the cap called it once
    per candidate to drop - 25 `json.dumps` of a ~17 KB selection per request, to
    produce a number that only changes when a tool leaves. Every schema's own
    serialised size is known from `_corpus`, so the total is arithmetic:

    * `2` for the `[` and `]`,
    * `2` per separator - `json.dumps` writes `", "` between items, not `","`,
    * plus each schema's own length, wrapper included.

    A separate function rather than an expression inline in `_cap_to_window`
    because **a test that re-derives this arithmetic proves nothing**: it checks
    its own copy against `json.dumps` and passes whatever the code does. Measured
    that way, breaking the brackets here - dropping the leading `2` - left the
    test green. The test calls *this*, so it cannot.
    """
    sizes = [chars_by_name[name] for name in kept_names]
    return 2 + sum(sizes) + 2 * max(len(sizes) - 1, 0)


def _cap_to_window(keep: Set[str], tools: List[dict], scored: List[tuple],
                   budget_tokens: int, protected: Set[str]) -> Set[str]:
    """Drop the lowest-scoring tools until the selection fits the window.

    **The cap the selection never had.** `keep` is assembled from a score
    threshold, a cue regex, the distilled router and `wants_a_browser`, and
    nothing anywhere measured the result: `browse` alone is ~850 tokens and is
    *added* rather than matched, so a request that trips the browser rule could
    push a selection past what the smallest tier can accept. The test asserting
    this (`test_a_selection_fits_the_smallest_window`) passed in isolation and
    failed in a full run, because `ranked()` re-sorts through learned weights -
    which is the same code path a real machine takes once it has trained
    anything. So the failure was order-dependent for the same reason it was real.

    Dropped by ascending score, so the *worst* match goes first rather than an
    arbitrary one, and it stops the moment the selection fits rather than
    trimming to a target.

    **`protected` is `CORE | in_use`, and it is honoured on both lists.** That
    detail was a real bug in the first version and is why the parameter exists:
    the scored list was filtered against `CORE` but the *unscored* list was not,
    so a tool that scored nothing - which is exactly what `in_use` tools and
    the core look like on a request matching neither - was the first thing
    dropped. `read_text_file`, mid-task, went missing from a turn that had
    already started using it.

    If the protected set alone exceeds the budget the answer is to send it
    anyway: dropping `ask_user` to fit leaves a turn with no way to ask the
    person anything, and the resulting rejection says nothing about why. An
    over-budget core fails visibly; a starved one fails invisibly.
    """
    # **The cap re-serialised the whole selection on every drop.** `_fits` is
    # `len(json.dumps(request_tools))`, and the loop called it once per candidate
    # with a freshly rebuilt list - 25 `json.dumps` of a ~17 KB selection per
    # request, measured, for a number that only changes when a tool leaves.
    # Each schema's own serialised size is now known from `_corpus`, and the
    # arithmetic now lives in `_selection_chars`, which is where the test pins
    # it - see that docstring for why a test cannot re-derive it.
    corpus = _corpus(tools)
    chars = corpus.chars_by_name
    names = corpus.names

    def fits_now() -> bool:
        kept = [n for n in names if n in keep]
        return _selection_chars(kept, chars) // _CAP_CHARS_PER_TOKEN <= budget_tokens

    if fits_now():
        return keep
    scored_names = {name for _s, name, _h in scored}
    droppable = [name for _s, name, _h in sorted(scored, key=lambda x: x[0])
                 if name not in protected]
    # Added by a cue, by `in_use`, or by the distilled router, and scored zero:
    # there is no evidence for it, so it is dropped after the scored ones.
    unscored = sorted(n for n in keep if n not in scored_names and n not in protected)
    for name in droppable + unscored:
        if fits_now():
            break
        keep.discard(name)
    return keep

#: Always sent: asking back, the time, and the web.
#:
#: `list_capabilities` used to be in here too, and the tool-selection eval
#: (2026-10-09, Qwen3-1.7B, 67 cases) showed exactly what an unconditional floor
#: costs: on "thanks!" the model called it. A small model handed a tool list
#: treats every turn as an instruction to use one, so each unconditional member
#: is a tool it may reach for when the right answer is to just reply. It is a
#: fine tool and it ranks on merit - it just must not be free.
#:
#: **And the size of that floor was measured as 40% of the whole budget, which
#: looks like the dilution fix and is not.** Across the 220 scorable cases
#: `select_tools` offers 1,390 schemas and **553 score no word match at all** -
#: `get_datetime` on 212, `ask_user` on 177, `web_search` on 163. Almost none of
#: it comes from ranking; it is this constant.
#:
#: Reclaiming it was tried and measured, and it costs 2 of 8 cases
#: (`tools/probe_core_cost.py`, same server, same cases, CORE admitted only when
#: it ranks): **7/8 as shipped against 5/8 conditional**. The two that break are
#: `chat-joke` and `vague-delete`, and they break *towards* the floor - the
#: model asks a question instead of replying, and tries to delete instead of
#: asking. `ask_user` is not overhead, it is the answer to a vague request, and a
#: model that is not offered it guesses. So the 40% is the price of the fallback,
#: not waste to reclaim, and **a budget figure is not a defect** - which is the
#: third time this file has been right about the arithmetic and wrong about what
#: it meant.
CORE = ("ask_user", "get_datetime", "web_search")

#: How strongly a tool must outscore the conversation path before that path
#: yields to it. **Chosen from the measurement on both sides, and the margin is
#: narrow, which is the honest part.**
#:
#: | turn | top score | must it yield? |
#: |---|---|---|
#: | "thanks!", "hello" | 0.00 | no - it is small talk |
#: | "good morning" | 9.72 (`routines`) | no - a greeting |
#: | "What is plugged in, and Thunderbolt docks." | 13.21 (`usb_devices`) | **yes** |
#: | "Explain a command." | 29.01 (`explain_command`) | **yes** |
#: | "What is conflicting in a git repository..." | 33.98 (`resolve_conflict`) | **yes** |
#:
#: 11.0 sits in the only gap that exists, and that gap is **3.5 points wide**.
#: A `name_hits` discriminator was tried instead and does not hold: it cleanly
#: separates two of the three (`explain_command` and `resolve_conflict` each have
#: 2 request words in the tool's own name, `routines` has 0) but `usb_devices`
#: also has 0 and still scores 13.21. So the separating quantity is the score,
#: and the constant is a measurement rather than a derivation - which is why both
#: sides are recorded here and a test pins both ends.
#:
#: Being wrong in the *safe* direction is the failure that matters: too low and
#: small talk gets eight tools again, which is what this path was built to stop.
CONVERSATION_YIELD = 11.0


def _outranks_conversation(scored: "List[tuple]") -> bool:
    """Whether the best match is strong enough to beat the conversation path."""
    return bool(scored) and scored[0][0] >= CONVERSATION_YIELD

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
    # Scheduled work. "scheduled"/"cron"/"timer" are the words for it and the
    # schema's own words are "source", so without these the router offered this
    # for "what cron jobs do I have" and sent "what is scheduled to run" to
    # `ask_user` - measured over four phrasings, 2 before.
    "scheduled": "cron jobs systemd timers", "schedule": "cron jobs systemd timers",
    "cron job": "cron jobs systemd timers", "cron jobs": "cron jobs systemd timers",
    "crontab": "cron jobs systemd timers", "timer": "cron jobs systemd timers",
    "timers": "cron jobs systemd timers", "scheduled task": "cron jobs systemd timers",
    "runs automatically": "cron jobs systemd timers",
    "shortcut": "keyboard shortcuts", "shortcuts": "keyboard shortcuts",
    "keybinding": "keyboard shortcuts", "keybindings": "keyboard shortcuts",
    "hotkey": "keyboard shortcuts", "hotkeys": "keyboard shortcuts",
    "bound to": "keyboard shortcuts", "what key": "keyboard shortcuts",
    "which key": "keyboard shortcuts", "what does this key": "keyboard shortcuts",
    # Containers and driving a window. The words a person uses are "container",
    # "docker", "click the Save button" - and the schemas' own words are "image",
    # "run", "drive", "controls". Without these, a request to click inside a
    # container's app reaches nothing: the skill exists, is gated, has a
    # numbered-control observation shaped for a small model, and the router has
    # no reason to offer it. Measured over five phrasings after adding these.
    "container": "container image run command",
    "containers": "container image run command",
    "docker": "container image run command",
    "podman": "container image run command",
    "inside the container": "container image run command",
    "in the container": "container image run command",
    "click": "click control button window",
    "click on": "click control button window",
    "clicking": "click control button window",
    "type into": "type text window control",
    "type in": "type text window control",
    "typing into": "type text window control",
    "drive": "drive act controls window",
    "automate": "drive act controls window",
    "nspawn": "container image run command",
    "gui": "controls window button app",
    "window": "controls window app",
    "test my app": "drive act controls window",
    "test the app": "drive act controls window",
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

    # **top-1 175 -> 192 of 220 at zero inference cost, and that number is
    # IN-SAMPLE.** recall@4 94.1% -> 95.9%, recall@8 96.4% -> 97.7%, so the extra
    # words sharpened the ranking rather than diluting the candidate list.
    #
    # **These entries were written by reading misses, and the corpora are not
    # independent.** `reranker_corpus.json`'s 165-request `train` split is **100%
    # contained in** `eval_cases.json` - every one of its requests appears there
    # verbatim - so scoring on the 220 proves only that the entries fix the
    # requests they were written from. Claiming otherwise was wrong, and
    # `TestTheSynonymTableBuysAccuracyWithoutAModel` says so where it measures.
    #
    # What they *are* is a list of **diagnosed vocabulary gaps**, each one a real
    # phrase a person uses that the schema does not. Whether they generalise is
    # **unmeasured**; the honest status is "plausible and free", not "+7.7 points".
    #
    # These are **vocabulary gaps, not ranking failures**. `mute the sound` was
    # answered `set_mic_mute` because both descriptions contain "mute"; "internet
    # speed" went to `web_search` because both mention the internet. Nothing about
    # the ranking was wrong - the words the person uses were absent.
    "mute the sound": "mute unmute volume audio speaker",
    "sound": "mute volume audio speaker",
    "internet speed": "speed test bandwidth download upload",
    "my internet working": "check internet connection online",
    "disk space is free": "disk usage free space available",
    # **Narrow, and deliberately so.** The wider expansion
    # `"discover hosts network devices scan"` pulled enough extra schemas into
    # "Who is on this network right now." to take that request's selection from
    # **1288 to 1536 tokens** - exactly the budget - and the cap then finished
    # over it with droppable tools still in hand. Every word added here is a word
    # that can match another schema, so the expansion is kept to the two that
    # carry the meaning.
    "who is on this network": "discover hosts network",
    "join a wifi network": "connect wifi network join",
    "turn the wi-fi radio": "toggle wifi radio on off",
    "using the most cpu": "list processes top running",
    "who logged in": "login history sessions logins",
    "switch speakers": "audio output device speakers headphones",
    "charging this machine": "charger battery power charging",
    "start or stop a service": "control service systemctl start stop",
    "how much battery": "battery status remaining",
    "timers do i have": "set timer timers",
    "search inside files": "search file contents inside",
    "this computer name": "hostname machine name",
    "can this machine roll back": "snapshot btrfs rollback slot",
    "screen blanks and locks": "screensaver blank lock idle",
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


#: Per-tool derived data, keyed on **the tool's own content**.
#:
#: Everything in here - the stemmed name, the stemmed body, the option words, the
#: serialised size - is a pure function of one schema, and none of it depends on
#: the request. It was being recomputed on every turn: measured with
#: `cProfile` over this module's own 220-case corpus, `_tool_text` alone was
#: **57,344 calls and 7.8 s of 11.6 s**, because it tokenises all 256
#: descriptions - roughly 68,000 tokens of text - once per request. Selecting
#: tools for 220 requests cost **12.6 ms each** and almost all of it was
#: re-reading data that had not changed.
#:
#: Keyed on content rather than on `id()` **on purpose**: a schema edited in place
#: keeps its identity, so an identity-keyed cache would serve a stale description
#: for it. The key is the description text plus the enum values, which is what
#: every field below is derived from - so an edit lands on a different key.
#:
#: **The space this costs, measured rather than waved at:** 256 entries, **1.86 MB**
#: of Python heap live (1.88 MB peak during the build), held for the life of the
#: process; the per-tool-set statistics add 0.03 MB more. That replaces 220
#: requests' worth of the same allocation churn per turn - so the peak is about
#: the same and the *churn* is what disappears. (7 MB was the guess written here
#: first, then 1.8; 1.86 is what `tracemalloc` says.)
_TOOL_FACTS: Dict[tuple, "_ToolFacts"] = {}
#: **Bounded, because the key is content-derived and contents are not fixed.**
#: A schema edited or installed at runtime, or one generated by the Workbench,
#: lands on a key this has never seen - and an unbounded dict would keep every
#: one of them, ~7 KB of stemmed words each, for the life of the process. The
#: live registry is 256 schemas, so a bound of four times that is never reached
#: by normal use; it exists so a long session against a changing registry costs
#: a bounded amount of memory rather than an unbounded amount. Eviction is
#: oldest-first, and re-tokenising one tool is a few hundred microseconds.
_TOOL_FACTS_MAX = 1024


class _ToolFacts:
    """One tool's request-independent facts. Immutable by convention."""

    __slots__ = ("name", "body", "options", "words", "_frozen")

    def __setattr__(self, name, value):
        """**Frozen once built.** This object is cached and handed out to callers,
        so a write would rewrite the answer for every *later* request rather than
        this one - and there being no path that writes today is a weaker
        guarantee than there being no way to. Populating uses `object.__setattr__`
        so the constructor itself is unaffected.
        """
        # **`getattr` with a default, not `object.__getattribute__`.** The first
        # assignment in `__init__` happens before `_frozen` exists, and
        # `object.__getattribute__` raises on a missing attribute rather than
        # returning one - which made the constructor itself fail. The anchor for
        # this edit appeared **twice**, once per class, so it is replaced in both
        # rather than asserted to be unique.
        if getattr(self, "_frozen", False):
            raise AttributeError(
                "%s is cached and shared; it is frozen after construction"
                % type(self).__name__)
        object.__setattr__(self, name, value)

    def __init__(self, name, body, options):
        #: Stemmed name words, as a `list` - `ranked()` reports the order's first
        #: hit count from it, and it is two words wide, so a set would buy nothing.
        self.name = name
        #: **A frozenset, not a list.** `ranked()` tests `w in body` once per
        #: request word per tool; against a ~200-word list that is a linear scan,
        #: and this module's inner loop is the one place that has to be O(1).
        self.body = body
        self.options = options
        #: `name | body`, cached because `ranked()`'s document-frequency pass
        #: needs exactly this union for every tool on every request.
        self.words = frozenset(name) | body
        self._frozen = True


def _tool_facts(tool: dict) -> "_ToolFacts":
    """One tool's request-independent facts, cached on the tool's own content."""
    fn = tool.get("function", {}) or {}
    name = fn.get("name", "") or ""
    props = ((fn.get("parameters") or {}).get("properties") or {})
    parts = [fn.get("description", "") or ""]
    enums = []
    for prop in props.values():
        if isinstance(prop, dict):
            parts.append(str(prop.get("description", "")))
            for value in (prop.get("enum") or []):
                enums.append(str(value))
    body = " ".join(parts)
    key = (name, body, tuple(enums))
    facts = _TOOL_FACTS.get(key)
    if facts is None:
        option_words: Set[str] = set()
        for value in enums:
            option_words.update(_words(value))
        facts = _ToolFacts(_words(name), frozenset(_words(body)),
                           frozenset(option_words))
        if len(_TOOL_FACTS) >= _TOOL_FACTS_MAX:
            _TOOL_FACTS.pop(next(iter(_TOOL_FACTS)))
        _TOOL_FACTS[key] = facts
    return facts


def _tool_text(tool: dict) -> "tuple[List[str], List[str]]":
    """`(name words, body words)` - kept for the tests that read it.

    The body is a **frozenset** now rather than a list. Membership is what every
    caller does with it, and `ranked()` does it once per request word per tool.
    """
    facts = _tool_facts(tool)
    return facts.name, sorted(facts.body)
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


class _Corpus:
    """Everything about a *tool set* that does not depend on the request.

    Measured, not assumed: `df` is **2,133** distinct words and `option_df` is
    **378**, together **0.07 MB** of heap, built in 0.9 ms. They were being
    rebuilt on every request - 8,599 dictionary operations each time - to produce
    numbers that only change when a schema does.
    """

    __slots__ = ("facts", "names", "df", "option_df", "n", "chars_by_name", "_frozen")

    def __setattr__(self, name, value):
        """**Frozen once built.** This object is cached and handed out to callers,
        so a write would rewrite the answer for every *later* request rather than
        this one - and there being no path that writes today is a weaker
        guarantee than there being no way to. Populating uses `object.__setattr__`
        so the constructor itself is unaffected.
        """
        # **`getattr` with a default, not `object.__getattribute__`.** The first
        # assignment in `__init__` happens before `_frozen` exists, and
        # `object.__getattribute__` raises on a missing attribute rather than
        # returning one - which made the constructor itself fail. The anchor for
        # this edit appeared **twice**, once per class, so it is replaced in both
        # rather than asserted to be unique.
        if getattr(self, "_frozen", False):
            raise AttributeError(
                "%s is cached and shared; it is frozen after construction"
                % type(self).__name__)
        object.__setattr__(self, name, value)

    def __init__(self, tools, facts):
        self.facts = facts
        self.names = [tool.get("function", {}).get("name", "") for tool in tools]
        self.n = len(tools)
        self.df = {}
        for facts_one in facts:
            for word in facts_one.words:
                self.df[word] = self.df.get(word, 0) + 1
        self.option_df = {}
        for facts_one in facts:
            for word in facts_one.options:
                self.option_df[word] = self.option_df.get(word, 0) + 1
        # **The whole schema, wrapper included - not the inner `function` dict.**
        # Measuring `fn` was short by 34 chars per tool, 95.4% of the true size,
        # and a selection of eight handed back ~68 tokens of window it had already
        # spent: `test_a_selection_fits_the_smallest_window` caught it. The
        # wrapper is part of the tool set, not of one tool's content, which is
        # why this is here and not in `_ToolFacts`.
        self.chars_by_name = {name: len(json.dumps(tool))
                              for name, tool in zip(self.names, tools)}
        self._frozen = True


#: Keyed on the **identity of the per-tool facts**, and holding them. That is what
#: makes an identity key safe here: a bundle keeps every facts object it was built
#: from alive, so a freed object's id cannot be handed to a different one while a
#: bundle that claims it is still in the cache. Without that reference this would
#: be the classic id-reuse bug, and it would return another tool set's statistics
#: for no visible reason.
#:
#: Bounded because the key is content-derived: a test that rebuilds schemas makes
#: a new key, and an unbounded dict would keep every one of them.
_CORPUS_CACHE: Dict[tuple, "_Corpus"] = {}
_CORPUS_CACHE_MAX = 8


#: The last corpus built, **holding the list it was built from**. A one-entry
#: memo on list identity, and holding the list is what makes that safe: a freed
#: list's id could otherwise be handed to a different list, and this would serve
#: another tool set's statistics. `select_tools` builds one `tools` list and
#: hands the same object to `ranked()` and `_cap_to_window()`, which each asked
#: for a corpus - 512 `_tool_facts` lookups per request to build the same one
#: twice, 42% of what was left after the per-tool cache landed.
_CORPUS_LAST: "Optional[tuple]" = None


def _corpus(tools: List[dict]) -> "_Corpus":
    global _CORPUS_LAST
    last = _CORPUS_LAST
    if last is not None and last[0] is tools:
        return last[1]
    facts = [_tool_facts(tool) for tool in tools]
    key = tuple(id(one) for one in facts)
    corpus = _CORPUS_CACHE.get(key)
    if corpus is None:
        corpus = _Corpus(tools, facts)
        if len(_CORPUS_CACHE) >= _CORPUS_CACHE_MAX:
            _CORPUS_CACHE.pop(next(iter(_CORPUS_CACHE)))
        _CORPUS_CACHE[key] = corpus
    _CORPUS_LAST = (tools, corpus)
    return corpus


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
    # Everything below depends on the tool set and not on the request, so it is
    # built once per set and kept: measured at 8,599 dictionary operations and a
    # full pass over every description, on every request, to produce constants.
    corpus = _corpus(tools)
    df, option_df, n = corpus.df, corpus.option_df, corpus.n
    # **The idf depends on the request word, not on the tool.** It was computed
    # inside the per-tool loop, so each of ~10 words had its `math.log` and its
    # dictionary lookup done **256 times** - about 2,560 of each per request, to
    # produce ten numbers. The same mistake as the per-tool cache one level up,
    # in miniature: constant-per-word work inside the loop that consumes it.
    idfs = {w: math.log(1 + n / (1 + df.get(w, 0))) for w in query}
    scored = []
    for facts, tool_name in zip(corpus.facts, corpus.names):
        score, name_hits = 0.0, 0
        name, body, opts = facts.name, facts.body, facts.options
        for w in query:
            idf = idfs[w]
            if w in name:
                score += 3 * idf
                name_hits += 1
            elif w in opts and w in said and option_df.get(w, 0) <= 3:
                score += 2 * idf
            elif w in body:
                score += idf
        if score > 0:
            scored.append((score, tool_name, name_hits))
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


def protected_for(request: str, keep: Set[str],
                  in_use: Optional[Iterable[str]] = None) -> Set[str]:
    """The tools the cap must not drop, however far over budget they leave it.

    **The budget is not a hard ceiling, and this is why.** A request that cannot
    be answered without `browse` - ~850 tokens on its own - is still sent with
    `browse`, because dropping it would answer a different question than the one
    asked. `browse` plus `CORE` alone exceeds a 1536-token budget, so on "Click."
    the selection finishes at 1936 tokens and stays there. That is measured
    behaviour on HEAD, not a regression: the cap drops everything *droppable* and
    stops at the protected set.

    Which makes the honest invariant the one worth testing - *either* the
    selection fits, *or* the protected tools alone already exceed the budget.
    A test that asserted "always fits" would have been red on this machine before
    a single line changed, and a test that re-derived `protected` here would be
    testing its own copy. So it is a named function that both call.
    """
    protected = set(CORE) | set(in_use or ())
    # A request to drive a control cannot be answered without the tool that
    # drives one, so the cap must not take it either - the same zero-sum the
    # lines below describe for `browse`. Measured: with the cue firing,
    # `_cap_to_window` still dropped both `ui_elements` and `container` on
    # "click Fishbowl in the container", because their schemas rank below
    # `ask_user`/`get_datetime` in `scored`.
    if wants_to_drive_an_app(request):
        protected |= {n for n in ("ui_elements",) if n in keep}
    if "browse" in keep and not _FILE_OR_APP.search(request or ""):
        # The site rule fired and the request is not about a file: what the
        # person asked for cannot be done without a browser, so the cap must not
        # take it. Measured on "Open blaze demo dot com and find the cheapest
        # flight": `keep` held 11 tools, `_cap_to_window` returned 7 and
        # `browse` was among the four dropped - the rule that asked for it,
        # silently undone by the budget.
        protected.add("browse")
    return protected


def select_tools(request: str, tools: Iterable[dict], limit: int = DEFAULT_LIMIT,
                 in_use: Optional[Iterable[str]] = None) -> List[dict]:
    """The schemas to send for `request`, in the order `tools` lists them."""
    tools = list(tools)
    keep: Set[str] = set(CORE) | set(in_use or ())
    scored = ranked(request, tools)
    # A turn that is conversation gets the smallest honest set: there is nothing
    # to call, and sending eight tools to "thanks!" is what produced two of the
    # five eval misses (2026-10-09). `in_use` still wins, so a turn mid-task is
    # never starved of the tool it is already using.
    #
    # **...unless something outranks it, and that is the second half.** Measured
    # over all 220 scorable cases, exactly three turns take this path and **all
    # three are misses** - "Explain a command.", "What is conflicting in a git
    # repository", "What is plugged in, and Thunderbolt docks." Each had the
    # correct tool ranked **first** and each was answered with a single
    # `ask_user` schema. The cue cannot see that `explain_command` is what
    # "explain a command" asks for, so it threw away the answer.
    if wants_a_conversation(request) and not _outranks_conversation(scored):
        return [t for t in tools if _name(t) in {"ask_user"} | set(in_use or ())]
    # a tool matching only a generic word ("set", "file") beside a strong
    # match is noise that costs tokens
    floor = scored[0][0] / 3 if scored else 0
    # Driving an app's controls is its own thing, as `browse` is for a page: a
    # cue, not a score, because the plainest phrasing ("click Save in the
    # container's app") shares words with page and window tools both. It keeps
    # only `ui_elements`: that tool *is* the driving now (`container` is
    # lifecycle and 2.1k chars of schema for it), and its `drive start` starts
    # the container itself, so a second tool here would be tokens spent on
    # nothing this turn uses.
    if wants_to_drive_an_app(request):
        known = {t["function"]["name"] for t in tools}
        keep |= {n for n in ("ui_elements",) if n in known}
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
        # **`_FILE_OR_APP` decides both halves, and the two were split apart.**
        # A request about a file or an app keeps its file tools even when it
        # also names a site - "download the file from example.com to my
        # Downloads folder" is a download, not a browsing session. So the same
        # condition that decides whether the site displaces the file tools also
        # decides whether the browser is protected from the token cap.
        #
        # The first version protected `browse` unconditionally, which passed the
        # browser test and broke `test_file_and_app_requests_keep_their_tools`:
        # the window is zero-sum - `browse` alone is ~850 tokens of a 1536
        # budget - so protecting it starved `open_file` on exactly the request
        # whose whole subject is a file. Two tests, one budget, and the rule has
        # to say which of them wins.
        about_files = bool(_FILE_OR_APP.search(request or ""))
        if not about_files:
            keep -= _DISPLACED_BY_A_SITE - set(in_use or ())
    protected = protected_for(request, keep, in_use)
    keep = _cap_to_window(keep, tools, scored,
                          int(WINDOW_TOKENS * SELECTION_SHARE), protected)
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
#: **`log ?in` needed the word boundary, and its absence was the most expensive
#: single character in this module.** `log` with no trailing `\b` matches inside
#: **"login"**, so "Is fingerprint **login** set up on this machine?" read as a
#: request to act on a page: `wants_a_browser` fired, `browse` was added *and
#: protected*, and `browse` is 3496 characters against a budget of
#: `WINDOW_TOKENS * SELECTION_SHARE` (1536 tokens at 4 chars each). Protected
#: CORE plus `browse` is **1395 of those 1536**, so `_cap_to_window` had 141
#: tokens left and dropped `fingerprint_status` - which `ranked()` had put
#: **first**. Measured: the correct tool was ranked #1, offered on 0 of 220
#: turns that phrase appeared in, and no layer said why.
#:
#: A false cue here is not merely an extra tool. `browse` is protected because a
#: request to act on a page cannot be answered without it, and that protection
#: is **zero-sum** - the same property this file already records for
#: `ui_elements`. So one over-broad regex spent 91% of the turn's tool budget on
#: a tool the person did not ask for. `\blog\b ?in\b` still matches "log in"
#: and "sign in", and no longer matches "login", "login_history" or "logins".
_ACT_ON_A_PAGE = re.compile(r"\b(book|booking|fill (?:in|out)|sign (?:in|up)|\blog\b ?in|checkout|check out|"
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
    #
    # `cron`, `crontab`, `shortcut`, `keybinding` and `hotkey` were missing,
    # which sent "what is scheduled to run" and "what are my keyboard
    # shortcuts" to `ask_user` alone - both have a tool that answers them
    # exactly, and both were read as things to be talked about. `timer` was
    # already here, so the set was not principled about machine subjects; it
    # was a list that had been added to as gaps appeared rather than derived.
    # `scheduled task|scheduled job|scheduled to run|cron job` is spelled out rather than
    # matching a bare "scheduled", which appears in plenty of ordinary prose.
    r"(?!.*\b(?:file|folder|window|app|setting|image|photo|pdf|"
    r"document|this computer|my |weather|forecast|temperature|time|clock|"
    r"timer|alarm|date|calendar|volume|battery|network|wifi|bluetooth|"
    r"music|note|song|call|message|email|"
    r"cron|crontab|scheduled task|scheduled job|scheduled to run|cron job|"
    r"shortcut|keybinding|hotkey|keyboard)\b)",
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


_ACT_IN_A_WINDOW = re.compile(
    r"\b(click|clicks|clicking|type into|type in|typing into|drive|"
    r"automate|ui test|test the app|test my app|"
    r"in the (?:container|window|app)|inside the (?:container|window|app))\b",
    re.IGNORECASE)
#: A control is named, so the request is about *using* one rather than about a
#: window in the abstract.
_CONTROL_NAMED = re.compile(
    r"\b(button|menu|field|checkbox|tab|dialog|popover|list item|"
    r"dropdown|save|cancel|export|settings|toolbar|sidebar)\b",
    re.IGNORECASE)


def wants_to_drive_an_app(request: str) -> bool:
    """Whether the request names a control in an app, rather than a website.

    `browse` has a cue because word matching could not connect "book a flight on
    blazedemo.com" to `browse`. The same is true here, and measured: "click
    Fishbowl in the container" offered `browse`, `ask_user` and `get_datetime`,
    and never `ui_elements` - because "click" is a page word and "container"
    scores low, while `close_window`/`list_windows` hold the word "window".
    So the driving gets its own cue, and a control named in an app is the
    signal that distinguishes it from managing windows in the abstract.
    """
    text = request or ""
    if _ACT_IN_A_WINDOW.search(text):
        return True
    return bool(_CONTROL_NAMED.search(text)
                and re.search(r"\b(app|window|container|gtk|qt|gui)\b", text, re.IGNORECASE))


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
