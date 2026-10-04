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

The model is not called. Consolidation runs from a timer and rules, on a machine
that may be a laptop on battery with no model server up; a summariser that needs
an LLM is a summariser that never runs. The digest is extractive - it picks the
sentences that carry the most new information - and it says so, rather than
dressing a heuristic up as understanding.
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

    def to_dict(self) -> Dict:
        return {"text": self.text, "source_index": self.source_index,
                "source_role": self.source_role, "accepted": self.accepted,
                "rejected": self.rejected}


@dataclass
class Digest:
    """What a conversation was about, small enough to keep."""

    session_id: str
    summary: str
    candidates: List[Candidate] = field(default_factory=list)
    message_count: int = 0
    #: When this was produced, so a stale digest can be recognised as stale.
    created: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {"session_id": self.session_id, "summary": self.summary,
                "candidates": [c.to_dict() for c in self.candidates],
                "message_count": self.message_count, "created": self.created}

    @classmethod
    def from_dict(cls, data: Dict) -> "Digest":
        return cls(
            session_id=str(data.get("session_id", "")),
            summary=str(data.get("summary", "")),
            candidates=[Candidate(**{k: v for k, v in c.items()
                                     if k in Candidate.__dataclass_fields__})
                        for c in data.get("candidates", [])],
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


def consolidate(session_id: str, messages: Sequence[Dict]) -> Optional[Digest]:
    """Turn one conversation into a digest and a queue of proposed facts.

    Returns None for a conversation too short to be worth the work, rather than
    a digest of "hi" / "hello".
    """
    if len(messages) < MIN_MESSAGES:
        return None
    summary = summarise(messages)
    if not summary:
        return None
    return Digest(session_id=session_id, summary=summary,
                  candidates=propose_facts(messages),
                  message_count=len(messages))


def store_dir(config: Optional[object] = None) -> Path:
    """Where digests live: beside the conversations they came from."""
    from . import config as config_module
    root = Path(getattr(config_module, "data_dir", Path.home() / ".local/share"
                        / "shani-chronoa"))
    return root / "sleep"


def save_digest(digest: Digest, directory: Optional[Path] = None) -> Path:
    """Write a digest, refusing a session id that could escape the directory.

    The id comes from the conversation index, and `conversation_store.valid_id`
    already exists to check exactly this; using it here means a crafted index
    cannot write outside `sleep/`.
    """
    from . import conversation_store
    if not conversation_store.valid_id(digest.session_id):
        raise ValueError(f"not a session id: {digest.session_id!r}")
    target = store_dir() if directory is None else Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{digest.session_id}.json"
    path.write_text(json.dumps(digest.to_dict(), indent=2))
    return path


def load_digest(session_id: str, directory: Optional[Path] = None) -> Optional[Digest]:
    from . import conversation_store
    if not conversation_store.valid_id(session_id):
        return None
    target = store_dir() if directory is None else Path(directory)
    path = target / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return Digest.from_dict(json.loads(path.read_text()))
    except (ValueError, TypeError, OSError):
        logger.warning("digest for %s is unreadable; ignoring it", session_id)
        return None


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


def sleep_now(root: Optional[Path] = None, minimum_age: float = 3600.0,
              on_digest: Optional[Callable[[Digest], None]] = None,
              directory: Optional[Path] = None) -> List[Digest]:
    """Consolidate every conversation that is over. Returns what it produced.

    This is the entry point a timer calls. It reads and writes only inside
    Chronoa's own data directory, calls no model, and sends nothing anywhere.
    """
    produced: List[Digest] = []
    for session_id in sessions_needing_sleep(root, minimum_age, directory):
        from . import conversation_store
        path = (Path(root) if root is not None else conversation_store.session_dir())
        messages = conversation_store.load(path / f"{session_id}.jsonl")
        digest = consolidate(session_id, messages)
        if digest is None:
            continue
        try:
            save_digest(digest, directory)
        except (OSError, ValueError):
            logger.warning("could not save the digest for %s", session_id,
                           exc_info=True)
            continue
        produced.append(digest)
        logger.info("slept on %s: %d messages, %d proposed facts",
                    session_id, digest.message_count, len(digest.candidates))
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