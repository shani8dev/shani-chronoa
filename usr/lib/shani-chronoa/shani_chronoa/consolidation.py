"""Sleep: what Chronoa does with a conversation once nobody is talking to it.

This is the `Sleep` / `Consolidate` organ, and until now it was a line in
`ARCHITECTURE-TARGET.md` reading ❌. The gap was real: a long conversation was
stored in full and never turned into anything smaller or more useful, so the
tenth session was no cheaper for the tenth than the first.

What it does, and what it deliberately does not:

- **Summarise** a conversation into a short, quotable digest, so the next session
  can be told what happened without replaying everything.
- **Propose facts** - candidate memories - extracted from what was said.
- **Never write a proposed fact to memory by itself.** Candidates go to a queue
  and stay there until a person accepts them. A background process that edits
  what Chronoa "knows" about you, unsupervised, is the exact thing
  `AGENTS.md`'s consent model exists to prevent, and sleeping is precisely when
  nobody is watching. `Sleep` can therefore only ever *propose*.

No model is required. Consolidation runs from a timer and rules, on a machine
that may be a laptop on battery with no model server up; a summariser that needs
an LLM is a summariser that never runs. The digest is extractive - it picks the
sentences that carry the most new information - and it says so, rather than
dressing a heuristic up as understanding. A caller may supply an `enrich`
callable backed by the *already-running* local server; when it is absent or
fails, the extractive digest stands. That callable is built in
`conversation_learning`, not here, so the timer path has no model import.

Entities and subject-relation-object links are extracted as *proposals* beside
the facts, and the conversation search index is refreshed after each digest.
Both stay inside this data directory; neither writes a memory. Proposals become
memory only through the consent-gated acceptor in the memory sense; this module
never imports it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Below this, a conversation is not worth summarising and the work is wasted.
MIN_MESSAGES = 6
#: Above this, the digest stops being a digest.
MAX_SUMMARY_SENTENCES = 6
#: A "fact" has to look like a statement someone could confirm. Anything shorter
#: is noise from the extractive pass.
MIN_FACT_CHARS = 12
MAX_FACT_CHARS = 200

#: Words that make a sentence a fact-shaped claim about the user, rather than a
#: question, a command to Chronoa, or small talk. Kept short on purpose: a
#: longer list starts matching things nobody would call a memory.
_FACT_MARKERS = (
    "i am ", "i'm ", "my ", "we ", "our ", "i live", "i work", "i use",
    "i prefer", "i like", "i need", "i want", "call me", "remember that",
    "note that", "keep in mind",
)
#: First-person statements that are about *this session*, not about the user.
#: "I'm going to shut the window" is not a durable fact and must never become one.
_TRANSIENT = (
    "i'm going to", "i am going to", "i'll ", "i will ", "let me", "let's ",
    "one moment", "give me a", "can you", "could you", "please ",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9']+")

#: A name is a capitalised word after the first word of a sentence, which is
#: where English puts an ordinary sentence opener. Kept deliberately naive:
#: this is a *candidate* for a person to confirm, never a fact written while
#: nobody is watching.
_ENTITY = re.compile(r"\b([A-Z][\w'-]+)\b")
_ENTITY_STOP = frozenset("""
    I My We Our The A An And But So If Then This That These Those There Their
    It Its He She They You Your Please Can Could Would Should Do Does Did Not
    No Yes One Two Three First Next Last Now Today Tomorrow Yesterday Also
""".split())

#: Relations a sentence can state about the user. Each pattern's whole match
#: is kept verbatim as the proposal text, so a person can check it against the
#: conversation; the groups become the (subject, relation, object) triple that
#: `senses.memory.link_entities` understands. A relation is stored only after a
#: person accepts the proposal, through that consent-gated function.
_LINK_PATTERNS = (
    (re.compile(r"\bmy name is ([A-Z][\w'-]+)", re.I), "named", False),
    (re.compile(r"\bi (?:live|lives?) in ([A-Z][\w'-]+)", re.I), "lives_in", False),
    (re.compile(r"\bi work (?:at|on|in) ([A-Z][\w'-]+)", re.I), "works_on", False),
    (re.compile(r"\b([A-Z][\w'-]+) (?:works at|lives in|owns|likes) ([A-Z][\w'-]+)"),
     "", True),
)

#: English stopwords, for scoring. Short on purpose: this is a frequency filter,
#: not an attempt at understanding.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of to in on at for with
from by as is are was were be been being do does did doing have has had having
i you he she it we they me him her them my your his its our their what which who
whom when where why how all any both each few more most other some such no nor
not only own same so too very can will just should now about into over under
""".split())


def _words(text: str) -> List[str]:
    return [word for word in _WORD.findall(text.lower())
            if word not in _STOPWORDS and len(word) > 2]


def _sentences(text: str) -> List[str]:
    return [piece.strip() for piece in _SENTENCE_SPLIT.split(text or "")
            if piece and piece.strip()]


def _message_text(message: Dict) -> str:
    """The readable part of one stored message, whatever shape it is in.

    `conversation_store` messages have varied over the years and a summariser
    that assumes one key is a summariser that raises on someone else's data.
    """
    if not isinstance(message, dict):
        return str(message or "")
    content = message.get("content", message.get("text", ""))
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


@dataclass
class Candidate:
    """A fact Chronoa noticed, which nobody has agreed to yet."""

    text: str
    #: Where in the conversation it came from, so accepting one is checkable
    #: rather than a matter of trust in a summary.
    source_index: int
    source_role: str = "user"
    accepted: bool = False
    #: Set when a person rejects it, so the same sentence is not proposed again
    #: every night for the rest of the conversation's life.
    rejected: bool = False
    #: When this is a memory *link* rather than a free-standing fact, the
    #: (subject, relation, object) it becomes if a person agrees. `text` stays
    #: the verbatim sentence so accepting one is checkable against the source.
    relation: Optional[tuple] = None

    def to_dict(self) -> Dict:
        return {"text": self.text, "source_index": self.source_index,
                "source_role": self.source_role, "accepted": self.accepted,
                "rejected": self.rejected,
                "relation": list(self.relation) if self.relation else None}


@dataclass
class Digest:
    """What a conversation was about, small enough to keep."""

    session_id: str
    summary: str
    candidates: List[Candidate] = field(default_factory=list)
    #: Names noticed in what the person said. Not facts, and never written to
    #: memory on their own - they are the entities a future "about X" can name.
    entities: List[str] = field(default_factory=list)
    message_count: int = 0
    #: When this was produced, so a stale digest can be recognised as stale.
    created: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {"session_id": self.session_id, "summary": self.summary,
                "candidates": [c.to_dict() for c in self.candidates],
                "entities": list(self.entities),
                "message_count": self.message_count, "created": self.created}

    @classmethod
    def from_dict(cls, data: Dict) -> "Digest":
        return cls(
            session_id=str(data.get("session_id", "")),
            summary=str(data.get("summary", "")),
            candidates=[Candidate(**{k: v for k, v in c.items()
                                     if k in Candidate.__dataclass_fields__})
                        for c in data.get("candidates", [])],
            entities=[str(e) for e in data.get("entities", [])],
            message_count=int(data.get("message_count", 0)),
            created=float(data.get("created", 0.0)))


def score_sentence(sentence: str, frequencies: Dict[str, float]) -> float:
    """How much new information a sentence carries, cheaply.

    Sum of the corpus frequencies of its content words. Crude, and stated here
    as crude: it picks the sentence that repeats the conversation's own
    vocabulary, which is a decent proxy for "the part that was about something".
    """
    return sum(frequencies.get(word, 0.0) for word in _words(sentence))


def _frequencies(messages: Sequence[Dict]) -> Dict[str, float]:
    counts: Dict[str, int] = {}
    for message in messages:
        for word in _words(_message_text(message)):
            counts[word] = counts.get(word, 0) + 1
    if not counts:
        return {}
    highest = max(counts.values())
    return {word: count / highest for word, count in counts.items()}


def summarise(messages: Sequence[Dict], limit: int = MAX_SUMMARY_SENTENCES) -> str:
    """An extractive digest: the sentences that carried the conversation.

    Extractive on purpose, and this returns prose only from the user's own
    sentences - it does not attempt to describe Chronoa's replies, because a
    summary of what the assistant said is a summary of its own confidence.
    """
    user_text = [_message_text(m) for m in messages
                 if isinstance(m, dict) and m.get("role", "user") == "user"]
    sentences: List[str] = []
    for text in user_text:
        sentences.extend(_sentences(text))
    if not sentences:
        return ""
    frequencies = _frequencies(messages)
    # First occurrence wins on ties, so the digest reads in the order things
    # were said rather than in score order.
    best: Dict[int, float] = {}
    for index, sentence in enumerate(sentences):
        best[index] = score_sentence(sentence, frequencies)
    ranked = sorted(best.items(), key=lambda pair: (-pair[1], pair[0]))
    chosen = sorted(index for index, _score in ranked[:max(1, limit)])
    return " ".join(sentences[index] for index in chosen)


def propose_facts(messages: Sequence[Dict],
                  limit: int = 8) -> List[Candidate]:
    """First-person statements worth asking a person to confirm.

    Every one of these is a *proposal*. The filters are narrow because the cost
    of a bad memory is not symmetric: a missed candidate is a sentence someone
    can state again, and a wrong one is something Chronoa now believes about you
    because it inferred it while you were not looking.
    """
    found: List[Candidate] = []
    seen = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role", "user") != "user":
            continue
        for sentence in _sentences(_message_text(message)):
            lowered = sentence.lower().strip()
            if not (MIN_FACT_CHARS <= len(sentence) <= MAX_FACT_CHARS):
                continue
            if any(marker in lowered for marker in _TRANSIENT):
                continue
            if not any(marker in lowered for marker in _FACT_MARKERS):
                continue
            key = " ".join(_words(lowered))
            if not key or key in seen:
                continue
            seen.add(key)
            found.append(Candidate(text=sentence.strip(), source_index=index))
            if len(found) >= limit:
                return found
    return found


def extract_entities(messages: Sequence[Dict], limit: int = 10) -> List[str]:
    """Names the person used, in the order they first appeared.

    Only user messages are read: a name the assistant invented in its own
    answer is not evidence that the person ever said it. A capitalised word
    after the first word of a sentence is the cheap signal; a word list drops
    the ordinary sentence openers that would otherwise swamp it.
    """
    found: List[str] = []
    seen = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role", "user") != "user":
            continue
        for sentence in _sentences(_message_text(message)):
            for word in _ENTITY.findall(sentence)[1:]:
                if word in seen or word in _ENTITY_STOP or len(word) < 2:
                    continue
                seen.add(word)
                found.append(word)
                if len(found) >= limit:
                    return found
    return found


def extract_links(messages: Sequence[Dict], limit: int = 8) -> List[Candidate]:
    """Subject-relation-object candidates, as proposals rather than writes.

    Each `Candidate.text` is the sentence fragment the pattern actually
    matched, so "where did this come from?" has an answer the person can check.
    The triple is kept beside it and is only ever used by the consent-gated
    `senses.memory.link_entities` after a person agrees.
    """
    found: List[Candidate] = []
    seen = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role", "user") != "user":
            continue
        for sentence in _sentences(_message_text(message)):
            for pattern, fixed, two in _LINK_PATTERNS:
                for match in pattern.finditer(sentence):
                    span = match.group(0).strip()
                    groups = [g for g in match.groups() if g]
                    if not groups:
                        continue
                    subject = "user" if not two else groups[0]
                    relation = fixed if fixed else (
                        "lives_in" if "lives in" in span.lower()
                        else "works_at" if "works at" in span.lower()
                        else "owns" if "owns" in span.lower() else "likes")
                    obj = groups[0] if not two else groups[-1]
                    triple = (subject, relation, obj)
                    key = tuple(part.lower() for part in triple)
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(Candidate(text=span, source_index=index,
                                           relation=triple))
                    if len(found) >= limit:
                        return found
    return found


def consolidate(session_id: str, messages: Sequence[Dict],
                enrich=None) -> Optional[Digest]:
    """Turn one conversation into a digest and a queue of proposed facts.

    `enrich`, when given, is an optional local-model callable that returns a
    short summary. It never replaces the extractive fallback: consolidation
    runs from a timer on a machine that may have no model server up, and the
    caller supplies the callable only when one is available. Whatever it
    returns is still a proposal, and the links it extracts still need a person.

    Returns None for a conversation too short to be worth the work, rather than
    a digest of "hi" / "hello".
    """
    if len(messages) < MIN_MESSAGES:
        return None
    summary = summarise(messages)
    if not summary:
        return None
    if enrich is not None:
        try:
            better = enrich(messages, summary)
        except Exception:  # noqa: BLE001 - the model is optional, sleep is not
            logger.info("the local enricher failed; keeping the extractive digest",
                        exc_info=True)
            better = ""
        if isinstance(better, str) and better.strip():
            summary = better.strip()
    facts = propose_facts(messages)
    links = [c for c in extract_links(messages)
             if c.text not in {f.text for f in facts}]
    return Digest(session_id=session_id, summary=summary,
                  candidates=facts + links,
                  entities=extract_entities(messages),
                  message_count=len(messages))


def store_dir(config: Optional[object] = None) -> Path:
    """Where digests live: beside the conversations they came from."""
    from . import config as config_module
    root = Path(getattr(config_module, "data_dir", Path.home() / ".local/share"
                        / "shani-chronoa"))
    return root / "sleep"


def digest_path(session_id: str, directory: Optional[Path] = None) -> Optional[Path]:
    """Where one session's digest lives, or `None` if that is not a session id.

    **One answer to both questions `save_digest` and `load_digest` ask**, which
    is why it is here rather than duplicated: they each computed `store_dir()`
    and then `f"{session_id}.json"` themselves, so a caller that wanted to tell
    "there is no digest for this session" from "the digest is there and cannot be
    read" - the two answers `load_digest` collapses into `None` - had to
    re-derive the path convention and could get it wrong without anything
    failing. It reads `None` for an id `save_digest` refuses, which is the point:
    an unrefusable write and an unanswerable read of the same name.

    The directory is not created. Asking where a file is does not make it.
    """
    from . import conversation_store
    if not conversation_store.valid_id(session_id):
        return None
    target = store_dir() if directory is None else Path(directory)
    return target / f"{session_id}.json"


def save_digest(digest: Digest, directory: Optional[Path] = None) -> Path:
    """Write a digest, refusing a session id that could escape the directory.

    The id comes from the conversation index, and `conversation_store.valid_id`
    already exists to check exactly this; using it here means a crafted index
    cannot write outside `sleep/`.
    """
    path = digest_path(digest.session_id, directory)
    if path is None:
        raise ValueError(f"not a session id: {digest.session_id!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest.to_dict(), indent=2))
    return path


def load_digest(session_id: str, directory: Optional[Path] = None) -> Optional[Digest]:
    path = digest_path(session_id, directory)
    if path is None or not path.exists():
        return None
    try:
        return Digest.from_dict(json.loads(path.read_text()))
    except (ValueError, TypeError, OSError):
        logger.warning("digest for %s is unreadable; ignoring it", session_id)
        return None


def list_digests(directory: Optional[Path] = None) -> List[str]:
    """Session ids that have a digest, newest first.

    **The counterpart to `sessions_needing_sleep`, and it is deliberately not
    that function.** That one names conversations with *no* digest yet - the
    opposite question - so a reader wanting "what has sleep already written"
    cannot use it. It was tried, and the result read as "nothing was ever
    proposed" on a machine holding a full `sleep/` directory, which is the
    absence-shaped wrong answer this repository keeps paying for.

    The path convention stays here rather than being re-derived by the caller,
    which is why `digest_path` was written: every digest is `<session_id>.json`
    under `store_dir()`/`directory`, and a second answer to that is how
    `save_digest` and `load_digest` came to disagree in the first place.

    **Reads nothing and creates nothing**, including the directory - a caller
    asking what sleep has produced is a read, and a read must not bring an
    unused store into existence.

    Ordered by **file mtime, newest first**, which is the cheap proxy for
    "which digests are worth reading at all". It is a proxy: a digest's own
    `created` is the fact, so a caller that has loaded them should re-sort on
    that (which is what `senses.memory.pending_proposals` does). Ordering by
    filesystem name instead would sort by conversation id, which is a
    timestamp and so *roughly* chronological - "roughly" being the part that
    makes a filename order the wrong answer to a "newest first" question.
    """
    from . import conversation_store
    home = store_dir() if directory is None else Path(directory)
    try:
        if not home.is_dir():
            return []
        entries = [(path.stat().st_mtime, path.stem)
                   for path in home.glob("*.json")]
    except OSError as exc:
        logger.warning("the sleep digests could not be listed: %s", exc)
        return []
    fresh = [pair for pair in entries
             if conversation_store.valid_id(pair[1])]
    fresh.sort(reverse=True)
    return [session_id for _mtime, session_id in fresh]


def sessions_needing_sleep(root: Optional[Path] = None,
                           minimum_age: float = 3600.0,
                           directory: Optional[Path] = None) -> List[str]:
    """Sessions with no digest yet, old enough to be worth one.

    `minimum_age` is what stops this running over the conversation someone is
    having right now: consolidation is for conversations that are over.
    """
    from . import conversation_store
    base = Path(root) if root is not None else conversation_store.session_dir()
    if not base.exists():
        return []
    now = time.time()
    out = []
    for path in sorted(base.glob("*.jsonl")):
        session_id = path.stem
        if not conversation_store.valid_id(session_id):
            continue
        if load_digest(session_id, directory) is not None:
            continue
        try:
            if now - path.stat().st_mtime < minimum_age:
                continue
        except OSError:
            continue
        out.append(session_id)
    return out


def refresh_index(session_id: str, root: Optional[Path] = None,
                  embed=None) -> bool:
    """Bring the conversation search index up to date after a sleep pass.

    The JSONL files stay the record; the SQLite index is a cache that can be
    deleted and rebuilt. `_search_db` is incremental, so this does not re-read
    the whole history - it picks up whatever was appended since the last pass.
    Vectors are filled only when Chronoa's own embedding model is configured,
    through the existing `local_embed.embed` path: no new dependency, no new
    server, and no request off the machine. A missing index (SQLite without
    FTS5) is `False`, never an invented success.
    """
    from . import conversation_store
    base = Path(root) if root is not None else conversation_store.session_dir()
    if not base.is_dir():
        return False
    db = conversation_store._search_db(base)
    if db is None:
        return False
    try:
        if embed is None:
            from . import local_embed
            if local_embed.configured():
                embed = local_embed.embed
        if embed is not None:
            conversation_store._fill_vectors(db, embed)
    except Exception:  # noqa: BLE001 - a stale index must not stop the sleep
        logger.info("could not refresh vectors for %s", session_id, exc_info=True)
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
    return True


def sleep_now(root: Optional[Path] = None, minimum_age: float = 3600.0,
              on_digest: Optional[Callable[[Digest], None]] = None,
              directory: Optional[Path] = None,
              enrich=None,
              refresh: bool = True) -> List[Digest]:
    """Consolidate every conversation that is over. Returns what it produced.

    This is the entry point a timer calls. It reads and writes only inside
    Chronoa's own data directory, calls no model unless the caller supplies an
    `enrich` callable backed by the already-running local server, and sends
    nothing anywhere. The index refresh is best effort: a digest that was
    written is not undone because its cache could not be updated.
    """
    produced: List[Digest] = []
    for session_id in sessions_needing_sleep(root, minimum_age, directory):
        from . import conversation_store
        path = (Path(root) if root is not None else conversation_store.session_dir())
        messages = conversation_store.load(path / f"{session_id}.jsonl")
        digest = consolidate(session_id, messages, enrich=enrich)
        if digest is None:
            continue
        try:
            save_digest(digest, directory)
        except (OSError, ValueError):
            logger.warning("could not save the digest for %s", session_id,
                           exc_info=True)
            continue
        produced.append(digest)
        logger.info("slept on %s: %d messages, %d proposed facts, %d entities",
                    session_id, digest.message_count, len(digest.candidates),
                    len(digest.entities))
        if refresh and not refresh_index(session_id, path):
            logger.info("the search index could not be refreshed for %s", session_id)
        if on_digest is not None:
            try:
                on_digest(digest)
            except Exception:
                # A caller's listener is not allowed to stop the sleep: the
                # digest is already written, and losing the rest of the night's
                # work over one bad callback would be the worse bug.
                logger.warning("a digest listener failed", exc_info=True)
    return produced


def organ_status() -> Dict[str, object]:
    """What the Inventory panel shows for this organ, from the filesystem."""
    from . import conversation_store
    base = conversation_store.session_dir()
    total = len(list(base.glob("*.jsonl"))) if base.exists() else 0
    slept = len(list(store_dir().glob("*.json"))) if store_dir().exists() else 0
    return {"sessions": total, "digests": slept,
            "pending": max(0, total - slept)}