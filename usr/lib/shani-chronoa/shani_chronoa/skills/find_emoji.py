"""Skill: find an emoji or symbol by name and put it on the clipboard -
'emoji for heart', 'the rupee sign'. From Python's Unicode database on this
machine; nothing leaves it."""

import unicodedata

from shani_chronoa.skills import Skill

_RANGES = [(0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0x2190, 0x21FF), (0x20A0, 0x20CF), (0x2100, 0x214F),
           (0x2200, 0x22FF), (0x25A0, 0x25FF), (0x2B00, 0x2BFF)]

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_emoji",
        "description": "Find an emoji or symbol by name (heart, thumbs up, rupee sign, arrow) and copy "
                       "the best match to the clipboard.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "What it is called, e.g. 'red heart' or 'rupee'."}},
            "required": ["name"]},
    },
}


#: The names people say, which are CLDR's and not Unicode's ('red heart' is
#: U+2764 HEAVY BLACK HEART plus the emoji selector). Checked first.
COMMON = {
    "red heart": "\u2764\ufe0f", "heart": "\u2764\ufe0f", "love": "\U0001F60D", "smile": "\U0001F60A",
    "smiley": "\U0001F603", "laugh": "\U0001F602", "laughing": "\U0001F602", "lol": "\U0001F602",
    "wink": "\U0001F609", "cry": "\U0001F622", "sad": "\U0001F622", "angry": "\U0001F620",
    "thinking": "\U0001F914", "shrug": "\U0001F937", "facepalm": "\U0001F926", "cool": "\U0001F60E",
    "thumbs up": "\U0001F44D", "thumbs down": "\U0001F44E", "ok": "\U0001F44C", "clap": "\U0001F44F",
    "pray": "\U0001F64F", "thank you": "\U0001F64F", "wave": "\U0001F44B", "muscle": "\U0001F4AA",
    "fire": "\U0001F525", "party": "\U0001F389", "celebrate": "\U0001F389", "rocket": "\U0001F680",
    "star": "\u2b50", "sparkles": "\u2728", "check": "\u2705", "tick": "\u2705", "cross": "\u274c",
    "warning": "\u26a0\ufe0f", "hundred": "\U0001F4AF", "100": "\U0001F4AF", "eyes": "\U0001F440",
    "sun": "\u2600\ufe0f", "moon": "\U0001F319", "rain": "\U0001F327\ufe0f", "coffee": "\u2615",
    "cake": "\U0001F382", "gift": "\U0001F381", "flag india": "\U0001F1EE\U0001F1F3", "india": "\U0001F1EE\U0001F1F3",
}


def search(words: str, n: int = 8) -> list:
    key = " ".join(w for w in words.lower().split() if w not in ("emoji", "the", "for", "a"))
    if key in COMMON:
        return [(COMMON[key], key)] + [x for x in _unicode(words, n - 1) if x[0] != COMMON[key]]
    return _unicode(words, n) or _unicode(words, n, any_word=True)


def _unicode(words: str, n: int = 8, any_word: bool = False) -> list:
    want = [w for w in words.upper().split() if w not in ("EMOJI", "SIGN", "SYMBOL", "THE", "FOR")] or words.upper().split()
    hits = []
    for lo, hi in _RANGES:
        for cp in range(lo, hi + 1):
            name = unicodedata.name(chr(cp), "")
            if name and (any if any_word else all)(w in name.split() for w in want):
                exact = sum(w in name.split() for w in want)
                hits.append((-exact, len(name), chr(cp), name.lower()))
    return [(c, nm) for _a, _b, c, nm in sorted(hits)[:n]]


def _run(arguments: dict) -> str:
    q = (arguments.get("name") or "").strip()
    if not q:
        return "Which emoji or symbol?"
    found = search(q)
    if not found:
        return f"No emoji or symbol is called '{q}'."
    best, name = found[0]
    from shani_chronoa.skills.clipboard import _run_set_clipboard
    copied = _run_set_clipboard({"text": best}).startswith("Copied")
    others = "  ".join(f"{c} {n}" for c, n in found[1:6])
    return (f"{best} ({name})" + (" is on your clipboard." if copied else ".")
            + (f" Also: {others}" if others else ""))


SKILLS = [Skill(name="find_emoji", schema=_SCHEMA, run=_run)]
