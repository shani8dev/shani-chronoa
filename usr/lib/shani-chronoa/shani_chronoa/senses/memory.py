"""Durable memory: the one sense whose percepts outlive the session.

Every other sense is perception of the *present* - what is on screen right
now, what a file says right now - and `PerceptStore` correctly keeps those
in memory only, because a screen grab from three sessions ago is not
context, it is a surveillance record. Memory is the opposite end of the
lifetime axis: a fact the user states once and expects Chronoa to still
know next week. Without this module the assistant's entire model of the
user is `Assistant._history`, which is per-process and dies on restart.

`ttl_seconds is None` is the load-bearing line
----------------------------------------------
`PerceptStore.add()` routes on exactly one field: `ttl_seconds is None`
means the durable tier (a JSON line on disk), anything else means the
bounded in-memory deque. A fact written with a numeric TTL would silently
stop being recalled a few minutes later, and nothing would raise - the
store just drops it on read. So the value is written out literally in
`_memory_percept()` rather than inherited from anywhere that could drift.

Redaction happens BEFORE the write, not after
---------------------------------------------
`sanitize_text_for_llm()` is wired into the path *to* an LLM provider.
Persistence sits upstream of that, so a secret typed once and remembered
verbatim would be re-read from disk and re-emitted to a cloud provider on
some later turn. `_redacted()` therefore sanitizes first and only then
hands the percept to the store. It fails closed: `sanitize_percepts`
swallows a sanitizer exception and returns the *unredacted* percept, so we
wrap the sanitizer in a call-recorder and refuse the write if it never ran
(`MemoryRedactionBlocked`). A silent no-op is the failure mode worth
guarding here, given the P0 where an empty secrets vault made sanitization
redact nothing at all.

What `recall` is, and is not
----------------------------
There is no embedding model, vector index, or similarity code anywhere in
this codebase and adding one is out of scope. `recall` is **weighted
keyword overlap**: it scores a fact by how much of the query's
*information* it shares, breaks ties by confidence then newest-first, and
returns nothing for a query with no shared word. It is still a literal
filter, not semantic ranking - "what do I drink?" will not find "The user's
order: a flat white", because they share no tokens. The cost of that
honesty is that a good paraphrase is invisible; the alternative was to call
substring matching "semantic search", which reads as a feature and behaves as
a no-op.

What stemming does, and what it cannot
--------------------------------------
`_stem` closes the *cheap* half of that gap: both sides of a match are
reduced to a root first, so "editors" finds "my editor is neovim" and
"cities" finds "city". It cannot do the expensive half, and it is worth being
precise about why, because "the assistant now understands what you mean"
would be a false description of it. "What do I drink?" and "a flat white"
share no root in any language, so no suffix rule reaches across that gap and
a larger rule set would only add wrong matches without closing it. Stemming
answers *did you say this word, differently*, never *did you mean this*.

That is the same limit the rest of the module states, at a different
boundary, and the two together are what a user can rely on: if the words
overlap, the ranking is meaningful; if they do not, the answer is an honest
"no stored fact shares a content word" rather than a confident wrong one.
`forget_facts` deliberately does **not** stem, because the same widening that
makes recall one candidate more useful makes an irreversible delete one fact
too wide.

What the score is, and why it is not a count
---------------------------------------------
It used to be `len(wanted & keywords)`, and the number that came out was
*not* a relevance score. Two properties of a bare set size made it
useless as one:

- **A count cannot be divided.** For a fixed query, `|Q and D|` and
  `|Q and D| / |Q|` order candidates identically - so normalising by the
  query alone rescales the numbers and reorders nothing. A threshold
  written against it would mean a different thing for every query, which
  is why there was no threshold to write.
- **It does not know which word matched.** "The user's editor: neovim"
  and "The user prefers: filter coffee" both score 1 for
  "the user's coffee", because both happen to contain one of the query's
  two words, and the score cannot tell that one of those words is in
  nearly every stored fact and the other is in exactly one.

The second one is not hypothetical here: a fact written by `extract_fact`
starts with the label "The user...", so a store fed by the extraction path
is seeded with its own hub words. And because a raw count ties constantly
in a store this small, `created_at` was doing the actual ranking - the top
five of a nine-way tie were simply the five most recent.

So each token carries an IDF weight, and a candidate's score is the
**Sørensen-Dice coefficient over those weights** - twice the shared mass
over the combined mass of the query and the fact. The divisor is derived
per candidate from the query and the fact themselves rather than being a
constant, which is what keeps every score in [0, 1] and comparable to
every other: a fact matching the query exactly scores 1.0, one sharing
nothing scores 0.0, and adding a further signal later widens the same
denominator instead of rescaling what is already there.

That choice is a trade and the losing side is real: Dice is
precision-weighted, so a long, thoroughly on-topic fact now ranks *below*
a terse fact that answers the same question outright. Coverage-weighted
BM25 was measured against the same fixtures and rejected - with matching
done on token *sets* there is no term frequency to saturate, so its
length prior gives a long fact a multiplier above 1.0 and it ranked the
verbose fact first. See `tests/test_memory_recall_ranking.py`.

Three operations, one lifetime contract
---------------------------------------
`remember` persists a fact and is the only operation here that writes
memory, so it is the only one whose result is durable. `recall` and
`forget` *return text to the model*, and text returned from a sense gets
wrapped in a Percept - so if their result were left durable, merely asking
"what do you remember?" would append a new memory, and a forget
confirmation would sit on disk forever. All three declare
`ttl_seconds = None` because they are all the memory sense, but `recall`
and `forget` return explicitly short-lived Percepts so that cannot happen.
"""

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import NamedTuple, Optional

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.redaction import redactor
from shani_chronoa.senses import (
    CONFIDENCE_UNSTATED,
    MEMORY_KIND_FACT,
    SENSITIVITY_PRIVATE,
    Percept,
    Sense,
)
from shani_chronoa.senses.context import sanitize_percepts
from shani_chronoa.senses.store import PerceptStore

logger = logging.getLogger(__name__)

# The *consent* key is `memory`, not the sense name: the GSettings surface
# answers "may Chronoa remember anything at all?", which is one question
# for all three operations rather than one per operation, and
# `config.py` cannot be asked about "remember-sense-enabled" without
# editing a verified file.
CONSENT_SENSE = "memory"

# How much each write path trusts its own output, and the reason `Percept.
# confidence` is a tie-break at all: `extract_fact` is a set of regular
# expressions over one sentence, while `remember` takes the user's own words
# verbatim. Both can be right, and they are not equally good evidence, so
# ranking them by `created_at` alone would let a regex guess from this turn
# outrank a statement the user made last month about the same subject - the
# same "recency is doing the ranking" failure the scoring section below
# describes, one level up.
CONFIDENCE_STATED = 0.9
CONFIDENCE_EXTRACTED = 0.6

# Recall results and forget confirmations are rendered text handed to the
# model, not new facts about the user. See the module docstring.
_TRANSIENT_TTL = 120.0

_OPERATIONS = ("remember", "recall", "forget", "link", "about", "history")

# A relation names how two things the user mentioned stand to each other -
# "Priya works_at the bike co-op". Short, lowercase, underscores: it is a
# label the model chooses and the user reads back, not free text.
_RELATION_RE = re.compile(r"^[a-z][a-z0-9_ ]{0,39}$")

#: Name of the audit file kept next to the durable store.
HISTORY_FILE = "memory-history.jsonl"

# Set once per process so an empty secrets vault is reported without
# turning every `remember` into a repeated warning.
_warned_empty_vault = False

_TOKEN_RE = re.compile(r"[a-z0-9']+")

_STOPWORDS = frozenset(
    """a an the and or but if then that this it i me my we our you your
    he she they do does did not no so as by from with please remember
    forget note dont let's about""".split()
)

# Deliberately conservative extraction: first-person *stative* patterns -
# the shapes a person uses when telling an assistant a standing fact about
# themselves - and nothing else. This is a documented heuristic, not
# semantic extraction; there is no model here, so the alternatives are
# storing every sentence (a transcript, which the store's own
# transient/durable split exists to avoid) or pretending to understand
# meaning. A user who wants something remembered that these patterns miss
# says so through the `remember` sense, which takes the fact verbatim.
#
# Patterns are ordered because the broad `attribute` rule would otherwise
# swallow "my name is ..." and file it under a key that supersedes wrongly.
# The key doubles as the supersession key, so remembering "my cat is rex"
# twice replaces the first record instead of appending a second, and a
# later "my cat is mo" replaces it cleanly rather than leaving two
# true-looking facts the model may quote either of.
# A fact's value stops at punctuation or at a clause boundary. Without the
# boundary, "my name is Priya and I work at a bike co-op" files the second
# clause into the name.
_VALUE = r"(?P<value>[^.,;!?\n]{2,60}?)(?=\s+(?:and|but|because|so|then|while)\b|[.,;!?\n]|$)"

_FACT_PATTERNS = (
    ("name", re.compile(rf"\bmy name(?:'s| is)\s+{_VALUE}", re.I)),
    ("preference", re.compile(rf"\bi (?:really )?(?:prefer|like|love|hate|dislike)\s+{_VALUE}", re.I)),
    ("place", re.compile(rf"\bi (?:live in|work at|study at)\s+{_VALUE}", re.I)),
    ("attribute", re.compile(rf"\bmy (?P<key>\w+)\s+is\s+{_VALUE}", re.I)),
    ("imperative", re.compile(rf"\b(?:remember|note) that\s+{_VALUE}", re.I)),
)

# How each static pattern is phrased back to the model; `attribute` uses its own key.
_FACT_LABEL = {
    "name": "The user's name",
    "preference": "The user prefers",
    "place": "The user's place",
    "imperative": "The user asked to remember",
}


class MemoryRedactionBlocked(RuntimeError):
    """Raised when a fact cannot be proven secret-free, so it is not written."""


class Fact(NamedTuple):
    """One candidate memory, still in the user's own words.

    A value object rather than four parameters because `store_fact` must
    receive all of them together: every field here is redacted before
    anything is persisted, and splitting them across arguments is how one
    of them ends up derived from unredacted text.

    `text` is what the model reads back; `span` is the user's original
    wording, which keyword search needs and the label phrasing drops;
    `key` supersedes an earlier fact about the same subject; `source` is
    provenance; `confidence` is how much this path trusts itself; and
    `valid_until` is when the claim itself stops being true, which is
    orthogonal to how long the record is kept.
    """

    text: str
    span: str
    key: str
    source: str
    confidence: float = CONFIDENCE_UNSTATED
    valid_until: Optional[float] = None
    #: (subject, relation, object) when this fact is a link between two
    #: things, so `about` can walk from one to the other. None for a plain fact.
    relation: Optional[tuple] = None


_STORE: Optional[PerceptStore] = None


def get_store(durable_path: Optional[Path] = None) -> PerceptStore:
    """The process-wide durable store, or a fresh one over `durable_path`.

    The explicit-path form is what makes this testable: verification
    writes to a temp file and reads it back through a second, independent
    `PerceptStore`, which is what proves the fact reached disk rather than
    sitting in one instance's deque.
    """
    global _STORE
    if durable_path is not None:
        return PerceptStore(durable_path=Path(durable_path))
    if _STORE is None:
        _STORE = PerceptStore()
    return _STORE


def _target(store: Optional[PerceptStore]) -> PerceptStore:
    return get_store() if store is None else store


# Suffix rules, longest first. Deliberately a stripper and nothing more - no
# Porter stemmer, no dictionary, no model - for one reason stated plainly:
# the module docstring's standing limit is that "what do I drink?" does not
# find "flat white", and no amount of suffix stripping changes that, because
# the two share no root. What this buys is the cheap half of the recall gap
# ("editor"/"editors", "editing"/"edit", "cities"/"city") and it buys only
# that. Every rule is a miss if it is too timid and a false match if it is too
# bold, and of those only one is a correctness bug.
#
# `or` is the boldest entry and is here for the "editor"/"editors"/"edit"
# case specifically: without it, `editing` reduces to `edit` while `editors`
# reduces to `editor`, and the two halves of one word stop meeting. It also
# merges author/auth and vendor/vend, which is a false match - the same root
# meaning two things - and is accepted as the cheaper of the two errors.
_SUFFIX_RULES = (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("or", ""), ("s", ""))

# A trailing "s" that is part of the word rather than a plural. "analysis" is
# not "analysi" and "address" is not "addres".
_NOT_A_PLURAL = ("ss", "us", "is")

# Below this, a stripped remainder is not a word: "thing" -> "th", "ed" -> "e".
# This is also what stops the "s" rule turning "gas" into "ga".
_MIN_STEM = 3

# A remainder with no vowel is a fragment, not a word. This is what stops
# "string" -> "str" and "ring" -> "r".
_VOWELS = frozenset("aeiouy")


def _stem(token: str) -> str:
    """`token` reduced to a root by suffix stripping, for word-shape matching.

    Applied to a fixpoint, and that is the whole design rather than an
    implementation detail: a single-application stripper is not closed under
    composition, so `editors` would become `editor` while `editor` became
    `edit`, and the two words a person would call the same would stop
    matching. Iterating until nothing changes makes the function idempotent -
    the stem of a stem is the stem - which is what lets stored keywords be
    stems already without a second, different reduction on the way out.
    """
    current = token
    for _ in range(4):  # bounded: no English word is four suffixes deep, and
                        # the fixpoint makes the bound unreachable in practice
        for suffix, replacement in _SUFFIX_RULES:
            if not current.endswith(suffix):
                continue
            if suffix == "s" and current[-2:] in _NOT_A_PLURAL:
                continue
            if len(current) - len(suffix) < _MIN_STEM:
                continue
            candidate = current[: -len(suffix)] + replacement
            if not _VOWELS & set(candidate):
                continue
            current = candidate
            break
        else:
            break
    return current


def _tokens(text: str) -> "set[str]":
    """Content *stems* in `text`: lowercased, stopworded, suffix-reduced.

    The name is historical; what it returns is a root, not a surface form.
    Every caller is either matching against it or persisting it, and both need
    "editor" and "editors" to be the same thing, so stemming lives here rather
    than being sprinkled over the two call sites that need it.
    """
    return {
        _stem(token)
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _words(text: str) -> "set[str]":
    """Content words in `text`, unstemmed.

    Kept separate from `_tokens` for exactly one caller, `forget_facts`, and
    the reason is that forgetting is irreversible. A query of "my editor"
    stemmed matches a stored "I edited the deck", which is a fact the user did
    not ask to have deleted; a recall that returns one extra candidate is a
    ranking nuisance, a forget that matches one extra fact is data loss.
    """
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _keywords(percept: Percept) -> "set[str]":
    """A fact's searchable vocabulary as stems: its recorded keywords plus its text.

    The stored keywords are run through `_stem` as well, so a fact written
    before stemming existed - whose `metadata["keywords"]` hold surface forms -
    reduces to the same root as one written after it. Without that, the
    expansion would only work for facts stored after the deploy that introduced
    it, and the older half of a user's memory would be silently unreachable by
    a differently-inflected query.
    """
    return {_stem(word) for word in _raw_keywords(percept)}


def _raw_keywords(percept: Percept) -> "set[str]":
    """A fact's vocabulary as the words were actually written, unstemmed.

    The counterpart to `_keywords` for `forget_facts`, which must not widen
    its match. It matters that this is a *union* of the recorded keywords and
    the content's own words: `extract_fact` drops the user's wording in
    favour of a label ("my espresso order" becomes "The user's order"), so the
    recorded keywords are the only place a query word like "espresso" survives
    - and after stemming they hold a root, which is why a query word is
    compared here as the user typed it and not reduced.
    """
    stored = (percept.metadata or {}).get("keywords") or ()
    return set(stored) | _words(percept.content)


def _idf(doc_count: int, doc_freq: int) -> float:
    """How much a token distinguishes facts, given how many contain it.

    Lucene's variant of inverse document frequency, `ln(1 + (N - df + 0.5) /
    (df + 0.5))`, chosen over the textbook `ln(N / df)` for one concrete
    reason: it is **never zero and never negative**, including for a token
    every single fact contains. A token in all N facts therefore still
    carries a small positive weight instead of collapsing the divisor to
    zero - and the `ln(N / df)` form does divide by zero on exactly the
    query such a store is most prone to, because a fact written by
    `extract_fact` is prefixed "The user's ..." and that prefix is in all
    of them. (`ln(N / df)` also goes *negative* for a token in more than
    half the store, which is not a weight at all.) The explicit `remember`
    operation stores the fact verbatim and carries no prefix; the label
    belongs to the extraction path, and this module is its ranking.

    The corollary is the point of the whole exercise: a hub word is not
    erased, it is made nearly worthless, so a fact that matches a query
    only on such a word scores near zero instead of tying with a fact that
    matched on a word unique to it.
    """
    return math.log(1.0 + (doc_count - doc_freq + 0.5) / (doc_freq + 0.5))


def _relevance(shared: float, query_mass: float, document_mass: float) -> float:
    """IDF-weighted Sørensen-Dice coefficient: 2 x shared / (query + document).

    The Sørensen-Dice coefficient is the one similarity measure that is
    symmetric and bounded by 1 without a parameter: it is 1 exactly when
    the two sides carry the same mass and every query token is present.
    The factor 2 is not a fudge - without it a query and a fact that are
    identical would score 0.5, and "1.0 means the fact is exactly what
    was asked for" is the only reading of the ceiling that survives
    someone adding a threshold later.

    **The divisor is the point.** It is computed from the query and the
    candidate, not fixed, which is what makes scores comparable: a
    one-word query and a twelve-word query, a two-fact store and a
    two-hundred-fact store, all land in the same [0, 1] and mean the same
    thing. `shared` cannot exceed either side by construction, so the
    result is at most 1.0; the `min` is not a correction, it is a guard
    against a float rounding a hair over on an exact match.

    Adding a signal later means adding its mass to *both* sides rather
    than adding a constant to a raw count, so the ceiling stays 1.0 and
    the ordering of already-comparable scores does not silently rescale.
    """
    total = query_mass + document_mass
    if shared <= 0.0 or total <= 0.0:
        return 0.0
    return min(1.0, 2.0 * shared / total)


def _consent_error() -> str:
    """Why the memory sense is refusing to operate, or "" if it may.

    `ChronoaConfig` is constructed per call on purpose: it reads GSettings
    fresh, so toggling `memory-sense-enabled` takes effect on the next
    operation without a restart.

    Privacy mode does not gate this sense, and that is deliberate rather
    than an oversight. `config._NETWORKED_SENSES` omits `memory` because
    remembering is local-only - it writes a file on this machine and calls
    nothing - so the sense stays allowed under privacy mode, which is
    exactly the case privacy mode is meant to permit. What privacy mode
    does govern is the cloud fallback, and that is enforced where the model
    is chosen, not here.
    """
    return ChronoaConfig().sense_allowed_reason(CONSENT_SENSE)


def _warn_if_no_vault() -> None:
    """Report once that redaction has nothing to work with, if that is so.

    Not a fix - a fix would mean refusing all memory, which is worse than
    useless. A user with no cloud keys genuinely has nothing to redact, so
    this is information for the case where they believe they do.
    """
    global _warned_empty_vault
    if _warned_empty_vault or redactor.names():
        return
    _warned_empty_vault = True
    logger.warning(
        "No secrets are registered, so nothing can be redacted from a remembered "
        "fact. If a memory ever contains an API key, it will reach disk in the clear."
    )


def _redacted_text(text: str) -> str:
    """`text` with registered secrets replaced, or raise if that can't be proven.

    This runs BEFORE anything at all is derived from a fact. Sanitizing
    only `Percept.content` is not sufficient and was the first version of
    this module's bug: `metadata["keywords"]` and `metadata["key"]` are
    written to the same JSON line verbatim by `store._as_dict()`, so
    deriving them from unsanitized text puts the secret on disk just as
    thoroughly as the content would have.

    `sanitize_percepts` treats a sanitizer exception as "leave this
    percept alone" and hands back the *unredacted* content - the right
    call on a read path, exactly wrong immediately before a write. The
    wrapper records that the sanitizer actually ran, so a sanitizer that
    never completed aborts the write instead of allowing it through.
    """
    ran = False

    def sanitizer(candidate: str) -> str:
        nonlocal ran
        result = redactor.sanitize(candidate)
        ran = True
        return result

    probe = Percept(
        sense=CONSENT_SENSE,
        kind="fact",
        content=text,
        created_at=0.0,
        ttl_seconds=None,
    )
    [clean] = sanitize_percepts([probe], sanitizer)
    if not ran:
        raise MemoryRedactionBlocked(
            "secret sanitizer did not complete; refusing to persist the fact"
        )
    _warn_if_no_vault()
    return clean.content


def _memory_percept(fact: Fact) -> Percept:
    """Build the durable Percept for one already-redacted fact.

    Takes a `Fact` rather than its four fields separately so that every
    value reaching disk arrives through the one function that decides what
    is persisted, and so nothing can be passed in already-lowercased or
    otherwise transformed ahead of redaction.

    `ttl_seconds=None` is written out here rather than defaulted anywhere,
    because this one argument is what routes the fact to the durable tier
    in `PerceptStore.add()`. See the module docstring.
    """
    return Percept(
        sense=CONSENT_SENSE,
        kind=MEMORY_KIND_FACT,
        content=fact.text,
        created_at=time.time(),
        ttl_seconds=None,
        source=fact.source,
        sensitivity=SENSITIVITY_PRIVATE,
        metadata={"key": fact.key, "keywords": sorted(_tokens(fact.span)),
                  **({"relation": list(fact.relation)} if fact.relation else {})},
        valid_until=fact.valid_until,
        confidence=fact.confidence,
    )


def _note(content: str) -> Percept:
    """A short-lived Percept for text the sense returns rather than stores.

    Never `ttl_seconds=None`: an ephemeral answer to "what do you
    remember?" is not a memory, and persisting one would make every
    question about memory grow memory. The durable tier is reached only
    through `store_fact`, never through a sense's return value.
    """
    return Percept(
        sense=CONSENT_SENSE,
        kind="confirmation",
        content=content,
        created_at=time.time(),
        ttl_seconds=_TRANSIENT_TTL,
        source=CONSENT_SENSE,
        sensitivity=SENSITIVITY_PRIVATE,
        # A rendered answer is text this module just composed from facts the
        # user asked for, not a claim about the world, so it carries no
        # opinion of its own. Stating that explicitly beats leaving it to the
        # default and hoping the two agree.
        confidence=CONFIDENCE_UNSTATED,
    )


def extract_fact(text: str) -> Optional[Fact]:
    """Extract one durable-worthy fact from a completed turn, or None.

    The returned `span` is the user's own wording, kept alongside the
    rephrased `text` because keyword search needs it: the label phrasing
    drops exactly the words a user would search by later ("my espresso
    order" becomes "The user's order").

    See `_FACT_PATTERNS` for why the pattern set is this narrow.
    """
    for key, pattern in _FACT_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        value = match.group("value").strip()
        if len(value) < 3:
            continue
        supersession = match.groupdict().get("key") or key
        label = (
            f"The user's {supersession}" if key == "attribute" else _FACT_LABEL[key]
        )
        span = text[match.start() : match.end()].strip()
        return Fact(
            f"{label}: {value}",
            span,
            supersession,
            "turn-extract",
            CONFIDENCE_EXTRACTED,
        )
    return None


def store_fact(fact: Fact, store: Optional[PerceptStore] = None) -> Optional[Percept]:
    """Redact, supersede any earlier record of the same key, then persist.

    Every field that reaches disk is derived from the redacted text, never
    from the raw one. This is the single write path: the consent gate and
    the redaction both live here, so no caller can reach the store around
    them.

    `valid_until` passes through unredacted, and that is correct rather than an
    oversight: it is a number the caller supplied, not text derived from
    anything the user wrote, so there is nothing in it for a substring
    sanitizer to find.

    Returns the persisted percept, or None when the sense is not permitted
    or redaction could not be proven.
    """
    denied = _consent_error()
    if denied:
        logger.warning("Not remembering anything: %s", denied)
        return None
    try:
        content = _redacted_text(fact.text).strip()
        span = _redacted_text(fact.span)
        key = _redacted_text(fact.key).strip().lower()
        relation = (
            tuple(_redacted_text(part).strip() for part in fact.relation)
            if fact.relation else None
        )
    except MemoryRedactionBlocked as e:
        logger.error("Refusing to remember %r: %s", fact.text, e)
        return None
    if not content:
        return None
    target = _target(store)
    replaced: list[Percept] = []
    if key:
        def same_key(p: Percept) -> bool:
            if (p.metadata or {}).get("key") == key:
                replaced.append(p)
                return True
            return False
        target.forget(same_key)
    percept = _memory_percept(
        Fact(content, span, key, fact.source, fact.confidence, fact.valid_until, relation)
    )
    target.add(percept)
    changed = [p for p in replaced if p.content != content]
    if changed:
        for old in changed:
            _record(target, "UPDATE", key, old.content, content, fact.source)
    elif not replaced:
        _record(target, "ADD", key, "", content, fact.source)
    return percept


# --- History --------------------------------------------------------------
#
# Every add, replacement and forget is logged to a 0600 JSONL file next to the
# durable store, so "why do you think that?" and "what did I tell you before?"
# have an answer. The text in it is the already-redacted text that reached the
# durable store, so the history cannot hold a secret the store did not.
#
# A forget **scrubs the history too**. "Forget that" is documented as erasing
# from disk, and an audit trail that kept the forgotten words would quietly
# break the promise. What a forget leaves is one line saying how many facts
# went, with no text from any of them.


def _history_path(store: PerceptStore) -> Path:
    return store.durable_path.with_name(HISTORY_FILE)


def _read_history(store: PerceptStore) -> list[dict]:
    path = _history_path(store)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _write_history(store: PerceptStore, events: list[dict]) -> None:
    path = _history_path(store)
    try:
        files.ensure_private_dir(path.parent)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        files.restrict_file(tmp)
        os.replace(tmp, path)
    except OSError as e:
        logger.error("Could not rewrite memory history %s: %s", path, e)


def _record(store: PerceptStore, event: str, key: str, old: str, new: str, source: str) -> None:
    path = _history_path(store)
    entry = {"at": time.time(), "event": event, "key": key, "old": old, "new": new, "source": source}
    try:
        files.ensure_private_dir(path.parent)
        existed = path.exists()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        if not existed:
            files.restrict_file(path)
    except OSError as e:
        logger.error("Could not append memory history %s: %s", path, e)


def _scrub_history(store: PerceptStore, removed: list[Percept]) -> None:
    """Drop every history line that quotes a forgotten fact, then log the forget without text."""
    texts = {p.content for p in removed}
    keys = {(p.metadata or {}).get("key") for p in removed} - {None, ""}
    kept = [
        e for e in _read_history(store)
        if e.get("old") not in texts and e.get("new") not in texts and e.get("key") not in keys
    ]
    kept.append({"at": time.time(), "event": "DELETE", "key": "", "old": "", "new": "",
                 "source": f"forgot {len(removed)} fact(s); their text was erased from this history too"})
    _write_history(store, kept)


def memory_history(query: str = "", store: Optional[PerceptStore] = None, limit: int = 10) -> list[dict]:
    """The newest history events, optionally only those sharing a word root with `query`."""
    events = _read_history(_target(store))
    wanted = _tokens(query)
    if wanted:
        events = [e for e in events
                  if wanted & _tokens(f"{e.get('old', '')} {e.get('new', '')} {e.get('key', '')}")]
    return events[-max(1, limit):][::-1]


# --- Links between things -------------------------------------------------
#
# The flat facts above cannot say how two things relate, so "who does Priya
# work with?" has nothing to walk. A link is stored as an ordinary durable
# fact ("Priya works at bike co-op") - so recall finds it, forget erases it,
# and the same consent and redaction apply - plus its (subject, relation,
# object) triple in metadata, which is what `about` follows. The design is the
# MCP reference memory server's entities/relations graph, kept in the store
# Chronoa already has instead of a second database.


def _norm(name: str) -> str:
    return " ".join(name.split()).lower()


def link_entities(
    subject: str, relation: str, obj: str, replace: bool = False,
    store: Optional[PerceptStore] = None, valid_until: Optional[float] = None,
) -> "tuple[Optional[Percept], str]":
    """Store "subject relation object"; with `replace`, drop other objects of the same relation first."""
    subject, obj = " ".join(subject.split()), " ".join(obj.split())
    relation = "_".join(relation.strip().lower().split())
    if not subject or not obj:
        return None, "link needs both a subject and an object"
    if not _RELATION_RE.match(relation):
        return None, "relation must be a short lowercase label such as works_at, sister_of or owns"
    if _norm(subject) == _norm(obj):
        return None, "a thing cannot be linked to itself"
    target = _target(store)
    if replace:
        prefix = f"rel:{_norm(subject)}|{relation}|"
        removed: list[Percept] = []

        def other_object(p: Percept) -> bool:
            k = (p.metadata or {}).get("key") or ""
            if k.startswith(prefix) and k != prefix + _norm(obj):
                removed.append(p)
                return True
            return False
        if target.forget(other_object):
            for old in removed:
                _record(target, "UPDATE", prefix.rstrip("|"), old.content,
                        f"{subject} {relation.replace('_', ' ')} {obj}", "user-stated")
    text = f"{subject} {relation.replace('_', ' ')} {obj}"
    stored = store_fact(
        Fact(text, text, f"rel:{_norm(subject)}|{relation}|{_norm(obj)}", "user-stated",
             CONFIDENCE_STATED, valid_until, (subject, relation, obj)),
        target,
    )
    return stored, "" if stored else (_consent_error() or "the write was refused")


def about(entity: str, store: Optional[PerceptStore] = None) -> "tuple[list[Percept], list[Percept], list[str]]":
    """Everything held about `entity`: its links, other facts naming it, and the things one link away.

    Returns (links, facts, neighbours). A fact "names" the entity when every
    word of the name appears in it, so "Priya" finds "Priya's birthday is in
    May" but not "Priyanka". Expired facts are left out here, as recall does.
    """
    name = _norm(entity)
    words = {w[:-2] if w.endswith("'s") else w for w in _words(entity)}
    if not name or not words:
        return [], [], []
    target = _target(store)
    now = time.time()
    links, facts, neighbours = [], [], []
    for p in target.durable():
        if p.is_expired(now):
            continue
        triple = (p.metadata or {}).get("relation")
        if isinstance(triple, list) and len(triple) == 3:
            s, _, o = (str(x) for x in triple)
            if _norm(s) == name or _norm(o) == name:
                links.append(p)
                other = o if _norm(s) == name else s
                if _norm(other) not in (_norm(n) for n in neighbours):
                    neighbours.append(other)
                continue
        if words <= {w[:-2] if w.endswith("'s") else w for w in _words(p.content)}:
            facts.append(p)
    target.mark_accessed(links + facts, now=now)
    return links, facts, neighbours


def remember_fact(
    fact: str,
    source: str = "user-stated",
    store: Optional[PerceptStore] = None,
    valid_until: Optional[float] = None,
) -> Optional[Percept]:
    """Persist `fact` verbatim, as asked. None if refused.

    The key is passed through in its ORIGINAL case and lowercased only
    inside `store_fact`, after redaction. Lowercasing first is a silent
    leak: `sanitize_text_for_llm()` replaces the secret by exact substring,
    so an already-lowercased key no longer matches `sk-...-AbC` and sails
    through unredacted. That was a real bug here, caught by reading the raw
    file rather than by a passing assertion on the returned content.

    `valid_until` is an absolute epoch instant, not a length, for the reason
    `Percept.is_expired` gives: a fact's validity ends at a moment, and a
    duration measured from whenever the record was last rewritten would move
    every time `forget()` touched the file.
    """
    content = fact.strip()
    if not content:
        return None
    return store_fact(
        Fact(content, content, content, source, CONFIDENCE_STATED, valid_until),
        store,
    )


def remember_from_turn(text: str, store: Optional[PerceptStore] = None) -> Optional[Percept]:
    """Pull a salient fact out of a completed turn and persist it.

    The entry point for automatic memory: durable memory is only useful if
    something writes to it, and this is that something. It stores at most
    one fact per turn, and only when `_FACT_PATTERNS` matches. Returns None
    when nothing matched, so the caller can tell "nothing worth keeping"
    from "kept".
    """
    found = extract_fact(text)
    if found is None:
        return None
    return store_fact(found, store)


class RecallReport(NamedTuple):
    """What one recall found, and what it found but refused to quote.

    `expired` is the reason this is a NamedTuple instead of the bare list
    `recall()` returns. A fact past its `valid_until` matches the query
    exactly as well as one inside its window, and the only difference between
    them is that quoting the first as current is a lie the model then repeats.
    Dropping it silently makes "your standup moved to the annex" and "you
    never told me where standup is" answer the user identically, and the
    second of those is the one that sends them off to ask again.
    """

    facts: list[Percept]
    expired: list[Percept]


def recall_report(
    query: str, store: Optional[PerceptStore] = None, limit: int = 5
) -> RecallReport:
    """`recall()` plus the matching facts whose validity window has closed.

    Expiry is evaluated on read, exactly as `PerceptStore.active()` does it,
    and for the same reason: a store nobody is polling still has to answer
    correctly, so there is no timer and no thread here. The expired facts stay
    on disk - this is relevance, not permission - and a fact whose window is
    reopened comes back.

    Recalled facts are stamped as accessed. That is the one read in the whole
    senses layer that means "the user asked for this and got it", so it is
    the only signal the durable tier's eviction order can honestly use.
    """
    wanted = _tokens(query)
    if not wanted or limit <= 0:
        return RecallReport([], [])
    target = _target(store)
    candidates = target.durable()
    if not candidates:
        return RecallReport([], [])
    vocabularies = [_keywords(percept) for percept in candidates]
    total_docs = len(vocabularies)

    # One pass for document frequencies, then a memoised IDF lookup, so the
    # log() is evaluated once per distinct token rather than once per
    # (token, fact) pair. Both are O(tokens in the store), which is a few
    # hundred for any memory store a person could plausibly accumulate.
    doc_freq: dict[str, int] = {}
    for vocabulary in vocabularies:
        for token in vocabulary:
            doc_freq[token] = doc_freq.get(token, 0) + 1
    weights: dict[str, float] = {}

    def weight(token: str) -> float:
        if token not in weights:
            weights[token] = _idf(total_docs, doc_freq.get(token, 0))
        return weights[token]

    now = time.time()
    query_mass = sum(weight(token) for token in wanted)
    scored, expired = [], []
    for percept, vocabulary in zip(candidates, vocabularies):
        shared = sum(weight(token) for token in wanted if token in vocabulary)
        if not shared:
            continue
        if percept.is_expired(now):
            expired.append(percept)
            continue
        document_mass = sum(weight(token) for token in vocabulary)
        scored.append(
            (_relevance(shared, query_mass, document_mass), percept)
        )
    ranked = sorted(
        scored,
        key=lambda pair: (pair[0], pair[1].confidence, pair[1].created_at),
        reverse=True,
    )
    found = [percept for _, percept in ranked[:limit]]
    target.mark_accessed(found, now=now)
    return RecallReport(found, expired)


def recall(
    query: str, store: Optional[PerceptStore] = None, limit: int = 5
) -> list[Percept]:
    """Stored facts sharing content words with `query`, most relevant first.

    Ranked by the IDF-weighted Dice coefficient in `_relevance`, NOT by how
    many of the query's words a fact happens to contain: see the module
    docstring for why a count was not a score. `confidence` breaks the ties
    that relevance leaves, and `created_at` breaks the ones confidence leaves;
    the module docstring's scoring section is what makes the second of those
    honest rather than the ranker in disguise.

    Query and fact are both stemmed to a root before anything is compared, so
    "editors" finds "my editor is neovim". That is word-shape matching and
    nothing more - see the module docstring for the limit it does not cross.

    NOT semantic search. Nothing here can see that "what do I drink?" and
    "The user's order: a flat white" are about the same thing, and no
    embedding model is being added to fix that. What a query word found in
    no stored fact does is worth stating precisely, because it looks like a
    bug and is not: it raises the query's mass and therefore lowers every
    candidate's score equally. The *order* is unaffected; only the number
    moves, which is the honest reading of "most of this question is not
    something I have stored".

    A query sharing no content word returns nothing rather than everything,
    because an assistant that answers every question with its whole
    database is worse than one that admits it does not know. Callers that can
    report *why* they got nothing should use `recall_report()`.
    """
    return recall_report(query, store, limit).facts


def forget_facts(query: str, store: Optional[PerceptStore] = None) -> int:
    """Delete every stored fact matching `query` from disk; return the count.

    Matching requires *all* of the query's content words to appear in the
    fact, or the whole query to appear verbatim - deliberately narrower than
    recall's "any word" rule, and unlike recall it does **not** stem. Both of
    those narrowings exist for the same reason: forgetting is destructive and
    irreversible here, so the weakest match that would remove a memory is not
    the weakest one worth honouring. Stemming would widen this query from
    "editor" to also match "I edited the deck", which is a fact nobody asked
    to lose. `PerceptStore.forget()` rewrites the durable file rather than
    filtering a read, which is the only acceptable behaviour for "forget
    this"; see its own docstring.
    """
    phrase = query.strip().lower()
    wanted = _words(query)
    if not phrase and not wanted:
        return 0

    removed: list[Percept] = []

    def matches(percept: Percept) -> bool:
        content = percept.content.lower()
        hit = bool(phrase and phrase in content) or (bool(wanted) and wanted <= _raw_keywords(percept))
        if hit:
            removed.append(percept)
        return hit

    target = _target(store)
    count = target.forget(matches)
    if count:
        _scrub_history(target, removed)
    return count



def _recall_body(report: RecallReport) -> str:
    """The body of a recall answer, which must never conflate three outcomes.

    "Found nothing", "found something and will not quote it any more" and
    "never had it" are three different states of the world, and a caller
    acting on the answer cannot recover the difference from a single line. The
    middle one is the one this function exists for: an expired fact is *in*
    the store, so reporting it as absent sends the user off to state again
    something they already said, while reporting it as current is a lie the
    model then repeats back.
    """
    if report.facts:
        body = "\n".join(f"- {p.content}" for p in report.facts)
        if not report.expired:
            return body
        return (
            f"{body}\n\nNot quoted, because the fact itself is no longer "
            f"stated to be true ({len(report.expired)} of them, still on "
            f"disk): {'; '.join(p.content for p in report.expired[:3])}"
            f"{'; ...' if len(report.expired) > 3 else ''}"
        )
    if report.expired:
        return (
            f"Nothing current. {len(report.expired)} stored fact(s) matched "
            f"but their stated validity has run out, so they are not quoted as "
            f"true: {'; '.join(p.content for p in report.expired[:3])}"
            f"{'; ...' if len(report.expired) > 3 else ''}"
        )
    return "Nothing stored.\nNo stored fact shares a content word with that query."


def _run(arguments: dict) -> Percept:
    """Dispatch one `memory` call to its operation.

    One sense with an `operation` parameter rather than three senses,
    because a sense's name is also its consent key: the loader and
    `ChronoaConfig.sense_allowed()` both key on `f"{name}-sense-enabled"`,
    so three separately-named senses would need three new GSettings keys
    for what is one user decision - "may Chronoa remember anything?".
    Splitting them would also split `memory-sense-enabled`, the one key
    that is on by default and deliberately not gated by privacy mode.
    """
    operation = str(arguments.get("operation") or "").strip().lower()
    query = str(arguments.get("query") or "").strip()
    fact = str(arguments.get("fact") or "").strip()

    # The gate is here, at the top, rather than only on the write paths.
    # It used to sit inside `remember_fact` and `link_entities`, which left
    # `recall`, `about`, `history` and `forget` ungated: with
    # `memory-sense-enabled` false the sense still disclosed the whole durable
    # store and still erased from disk, because the one key that says "may
    # Chronoa remember anything?" governed only the writing half. A read is a
    # disclosure of the same data the key covers, and `forget` is a deletion,
    # which is more than a read - so both belong behind the same switch.
    #
    # This does not make the per-write checks redundant: they are reached
    # directly by other callers, and a gate at the entry point is not a gate on
    # the function.
    denied = _consent_error()
    if denied:
        return _note(f"Did not {operation or 'use memory'}. {denied}")
    match operation:
        case "remember":
            valid_for = arguments.get("valid_for_minutes")
            try:
                minutes = float(valid_for) if valid_for else None
            except (TypeError, ValueError):
                minutes = None
            stored = remember_fact(
                fact,
                source=str(arguments.get("source") or "user-stated"),
                valid_until=(
                    None if minutes is None else time.time() + minutes * 60.0
                ),
            )
            if stored is not None:
                # The stored fact itself, not a transient acknowledgement of
                # it. Returning `_note(...)` here made the CLI print "transient
                # only - lost when this process exits" immediately after
                # durably writing the fact to disk, so the one operation whose
                # whole purpose is persistence reported itself as the one
                # thing that does not persist.
                return stored
            return _note(
                f"Did not remember that. "
                f"{_consent_error() or 'the write was refused.'}"
            )
        case "recall":
            limit = arguments.get("limit")
            report = recall_report(query, limit=int(limit) if limit else 5)
            return _note(
                f"What is remembered about that:\n{_recall_body(report)}"
            )
        case "forget":
            removed = forget_facts(query)
            said = (
                f"Forgot {removed} stored fact(s); erased from disk."
                if removed
                else "No stored fact matched that."
            )
            return _note(said)
        case "link":
            valid_for = arguments.get("valid_for_minutes")
            try:
                minutes = float(valid_for) if valid_for else None
            except (TypeError, ValueError):
                minutes = None
            stored, why = link_entities(
                str(arguments.get("subject") or ""), str(arguments.get("relation") or ""),
                str(arguments.get("object") or ""), replace=bool(arguments.get("replace")),
                valid_until=None if minutes is None else time.time() + minutes * 60.0,
            )
            return stored if stored is not None else _note(f"Did not link that: {why}.")
        case "about":
            links, facts, neighbours = about(query)
            if not links and not facts:
                return _note(f"Nothing stored about {query!r}." if query else "Say what to look up with query.")
            lines = [f"About {query}:"]
            lines += [f"- {p.content}" for p in links + facts]
            if neighbours:
                lines.append("Linked to: " + ", ".join(neighbours) + " (ask about any of them for more).")
            return _note("\n".join(lines))
        case "history":
            limit = arguments.get("limit")
            events = memory_history(query, limit=int(limit) if limit else 10)
            if not events:
                return _note("No memory changes recorded" + (f" about {query!r}." if query else "."))
            lines = ["Memory changes, newest first:"]
            for e in events:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(e.get("at") or 0)))
                if e.get("event") == "UPDATE":
                    lines.append(f"- {when} changed: {e.get('old')!r} -> {e.get('new')!r}")
                elif e.get("event") == "ADD":
                    lines.append(f"- {when} remembered: {e.get('new')!r} ({e.get('source')})")
                else:
                    lines.append(f"- {when} {e.get('source')}")
            return _note("\n".join(lines))
        case _:
            return _note(
                f"Unknown operation {operation!r}. Use one of: "
                f"{', '.join(_OPERATIONS)}."
            )


# `ttl_seconds=None` is the durable declaration, stated literally rather
# than table-driven: it is the one line in this file that decides whether
# a remembered fact lives on disk or dies in a bounded deque.
SENSES = [
    Sense(
        name=CONSENT_SENSE,
        kind="fact",
        ttl_seconds=None,
        sensitivity=SENSITIVITY_PRIVATE,
        schema={
            "type": "function",
            "function": {
                "name": CONSENT_SENSE,
                "description": (
                    "Durable memory about the user, kept on this machine across "
                    "restarts. operation='remember' stores one fact (only what "
                    "the user asked you to keep); operation='recall' looks "
                    "stored facts up by keyword - matching is literal word "
                    "overlap on word roots, so plurals and verb endings match "
                    "but a paraphrase does not, ranked by how much of the "
                    "query's information each fact shares, so rephrase the "
                    "query using words the fact actually contains. A fact "
                    "whose valid_for_minutes has run out is reported as "
                    "expired rather than quoted; operation='forget' erases "
                    "matching facts from disk permanently. operation='link' "
                    "stores how two things relate (subject, relation, object, "
                    "e.g. Priya works_at Acme; replace=true when the old one "
                    "stops being true); operation='about' (query=a name) "
                    "returns every link and fact naming it and what it is "
                    "linked to; operation='history' shows what was "
                    "remembered or changed, and when."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": list(_OPERATIONS),
                            "description": "Which memory operation to perform.",
                        },
                        "fact": {
                            "type": "string",
                            "description": "The fact to store. operation='remember' only.",
                        },
                        "query": {
                            "type": "string",
                            "description": "What to look up, or what to erase.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max facts returned by 'recall' (default 5).",
                        },
                        "source": {
                            "type": "string",
                            "description": "Provenance for 'remember', e.g. 'user-stated'.",
                        },
                        "subject": {"type": "string", "description": "operation='link': the first thing."},
                        "relation": {"type": "string", "description": "operation='link': e.g. works_at, sister_of, owns."},
                        "object": {"type": "string", "description": "operation='link': the second thing."},
                        "replace": {"type": "boolean", "description": "operation='link': drop the subject's other objects for this relation."},
                        "valid_for_minutes": {
                            "type": "number",
                            "description": (
                                "operation='remember' only. How long this fact "
                                "stays true, in minutes - use it for a job "
                                "title, a tool, a meeting room or a standup "
                                "location, anything likely to change without "
                                "being corrected. After this time the fact is "
                                "no longer quoted as current, and recall says "
                                "so rather than pretending it was never "
                                "stored. Omit it for something that does not "
                                "expire."
                            ),
                        },
                    },
                    "required": ["operation"],
                },
            },
        },
        run=_run,
    )
]
