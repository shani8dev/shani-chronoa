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
this codebase and adding one is out of scope. `recall` is **keyword
overlap plus recency**: it scores a fact by how many of the query's
content words appear in it, breaks ties newest-first, and returns nothing
for a query with no shared word. That is a literal filter, not relevance
ranking - "what do I drink?" will not find "The user's order: a flat
white", because they share no tokens. The cost of that honesty is that a
good paraphrase is invisible; the alternative was to call substring
matching "semantic search", which reads as a feature and behaves as a
no-op.

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

import logging
import re
import time
from pathlib import Path
from typing import NamedTuple, Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.secrets_manager import secrets_manager
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Percept, Sense
from shani_chronoa.senses.context import sanitize_percepts
from shani_chronoa.senses.store import PerceptStore

logger = logging.getLogger(__name__)

# The *consent* key is `memory`, not the sense name: the GSettings surface
# answers "may Chronoa remember anything at all?", which is one question
# for all three operations rather than one per operation, and
# `config.py` cannot be asked about "remember-sense-enabled" without
# editing a verified file.
CONSENT_SENSE = "memory"

# Recall results and forget confirmations are rendered text handed to the
# model, not new facts about the user. See the module docstring.
_TRANSIENT_TTL = 120.0

_OPERATIONS = ("remember", "recall", "forget")

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
    provenance.
    """

    text: str
    span: str
    key: str
    source: str


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


def _tokens(text: str) -> "set[str]":
    """Content words in `text`, lowercased, with stopwords and short tokens out."""
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _keywords(percept: Percept) -> "set[str]":
    """A fact's searchable vocabulary: its recorded keywords plus its own text."""
    stored = (percept.metadata or {}).get("keywords") or ()
    return set(stored) | _tokens(percept.content)


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
    if _warned_empty_vault or secrets_manager.list_secrets():
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
        result = secrets_manager.sanitize_text_for_llm(candidate)
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
        kind="fact",
        content=fact.text,
        created_at=time.time(),
        ttl_seconds=None,
        source=fact.source,
        sensitivity=SENSITIVITY_PRIVATE,
        metadata={"key": fact.key, "keywords": sorted(_tokens(fact.span))},
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
        return Fact(f"{label}: {value}", span, supersession, "turn-extract")
    return None


def store_fact(fact: Fact, store: Optional[PerceptStore] = None) -> Optional[Percept]:
    """Redact, supersede any earlier record of the same key, then persist.

    Every field that reaches disk is derived from the redacted text, never
    from the raw one. This is the single write path: the consent gate and
    the redaction both live here, so no caller can reach the store around
    them.

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
    except MemoryRedactionBlocked as e:
        logger.error("Refusing to remember %r: %s", fact.text, e)
        return None
    if not content:
        return None
    target = _target(store)
    if key:
        target.forget(lambda p: (p.metadata or {}).get("key") == key)
    percept = _memory_percept(Fact(content, span, key, fact.source))
    target.add(percept)
    return percept


def remember_fact(
    fact: str, source: str = "user-stated", store: Optional[PerceptStore] = None
) -> Optional[Percept]:
    """Persist `fact` verbatim, as asked. None if refused.

    The key is passed through in its ORIGINAL case and lowercased only
    inside `store_fact`, after redaction. Lowercasing first is a silent
    leak: `sanitize_text_for_llm()` replaces the secret by exact substring,
    so an already-lowercased key no longer matches `sk-...-AbC` and sails
    through unredacted. That was a real bug here, caught by reading the raw
    file rather than by a passing assertion on the returned content.
    """
    content = fact.strip()
    if not content:
        return None
    return store_fact(Fact(content, content, content, source), store)


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


def recall(
    query: str, store: Optional[PerceptStore] = None, limit: int = 5
) -> list[Percept]:
    """Stored facts sharing content words with `query`, best overlap first.

    Literal keyword overlap, then newest-first for ties. NOT semantic search
    and NOT relevance ranking - see the module docstring for what that
    costs. A query sharing no content word returns nothing rather than
    everything, because an assistant that answers every question with its
    whole database is worse than one that admits it does not know.
    """
    wanted = _tokens(query)
    if not wanted or limit <= 0:
        return []
    scored = [
        (len(wanted & _keywords(percept)), percept)
        for percept in _target(store).durable()
    ]
    ranked = sorted(
        (pair for pair in scored if pair[0]),
        key=lambda pair: (pair[0], pair[1].created_at),
        reverse=True,
    )
    return [percept for _, percept in ranked[:limit]]


def forget_facts(query: str, store: Optional[PerceptStore] = None) -> int:
    """Delete every stored fact matching `query` from disk; return the count.

    Matching requires *all* of the query's content words to appear in the
    fact, or the whole query to appear verbatim - deliberately narrower than
    recall's "any word" rule. Forgetting is destructive and irreversible
    here, so the weakest match that would remove a memory is not the
    weakest one worth honouring. `PerceptStore.forget()` rewrites the
    durable file rather than filtering a read, which is the only acceptable
    behaviour for "forget this"; see its own docstring.
    """
    phrase = query.strip().lower()
    wanted = _tokens(query)
    if not phrase and not wanted:
        return 0

    def matches(percept: Percept) -> bool:
        content = percept.content.lower()
        if phrase and phrase in content:
            return True
        return bool(wanted) and wanted <= _keywords(percept)

    return _target(store).forget(matches)



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
    match operation:
        case "remember":
            stored = remember_fact(
                fact, source=str(arguments.get("source") or "user-stated")
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
            found = recall(query, limit=int(limit) if limit else 5)
            body = (
                "\n".join(f"- {p.content}" for p in found)
                if found
                else "No stored fact shares a content word with that query."
            )
            return _note(f"What is remembered about that:\n{body}")
        case "forget":
            removed = forget_facts(query)
            said = (
                f"Forgot {removed} stored fact(s); erased from disk."
                if removed
                else "No stored fact matched that."
            )
            return _note(said)
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
                    "stored facts up by keyword - literal word overlap, so "
                    "rephrase the query using words the fact actually "
                    "contains; operation='forget' erases matching facts from "
                    "disk permanently."
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
                    },
                    "required": ["operation"],
                },
            },
        },
        run=_run,
    )
]
