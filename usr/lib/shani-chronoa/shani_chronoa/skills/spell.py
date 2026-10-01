"""Skill: how is a word spelled - from the word list on this machine.

/usr/share/dict/words (the `words` package, in every Shanios image) is
~100,000 English words. A word in it is spelled right; otherwise the closest
entries are offered, which is also what fixes a word speech recognition heard
wrong. Nothing leaves the machine.
"""

import difflib
from functools import lru_cache

from shani_chronoa.skills import Skill

WORDS = "/usr/share/dict/words"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "spell_word",
        "description": "Check how a word is spelled, offline, and suggest the closest correct "
                       "spellings - 'how do you spell necessary', 'is it recieve or receive'.",
        "parameters": {"type": "object", "properties": {
            "word": {"type": "string", "description": "The word as heard or typed."}}, "required": ["word"]},
    },
}


@lru_cache(maxsize=1)
def _words() -> "tuple[frozenset, dict]":
    with open(WORDS, encoding="utf-8", errors="replace") as f:
        words = [w.strip() for w in f if w.strip() and "'" not in w]
    by_first = {}
    for w in words:
        by_first.setdefault(w[0].lower(), []).append(w)
    _common.update(w for w in words if w.islower())
    return frozenset(w.lower() for w in words), by_first


#: Words the list has in lower case - ordinary words rather than names
#: ('and', not 'Aden'), preferred when two suggestions are equally close.
_common: set = set()


def _distance(a: str, b: str) -> int:
    """Damerau-Levenshtein (optimal string alignment): a swapped pair of
    letters - 'recieve' for 'receive', the commonest misspelling there is -
    is one edit, not two."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def suggest(word: str, n: int = 5) -> list:
    """Close words, fewest edits first; difflib's similarity breaks ties."""
    _, by_first = _words()
    w = word.lower()
    pool = list({x.lower() for x in by_first.get(w[:1], []) if abs(len(x) - len(w)) <= 3})
    short = len(w) <= 4
    near = difflib.get_close_matches(w, pool, n=60 if short else n * 3, cutoff=0.6 if short else 0.7)
    # at equal distance, the same letters in another order wins: that is a slip
    # of the fingers ('teh', 'recieve'), where a different letter is a different word
    return sorted(near, key=lambda x: (_distance(w, x), sorted(x) != sorted(w), x not in _common,
                                       -difflib.SequenceMatcher(None, w, x).ratio()))[:n]


def _run(arguments: dict) -> str:
    word = (arguments.get("word") or "").strip()
    if not word or " " in word:
        return "Which single word?"
    try:
        known, _ = _words()
    except OSError:
        return "The word list (the 'words' package) is not installed."
    if word.lower() in known:
        return f"'{word}' is spelled correctly: {' - '.join(word.upper())}."
    close = suggest(word)
    if not close:
        return f"'{word}' is not in the dictionary, and nothing close is."
    best = close[0]
    return (f"'{word}' is not in the dictionary. Did you mean '{best}' ({' - '.join(best.upper())})?"
            + (f" Other close words: {', '.join(close[1:])}." if len(close) > 1 else ""))


SKILLS = [Skill(name="spell_word", schema=_SCHEMA, run=_run)]
