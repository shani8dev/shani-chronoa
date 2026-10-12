"""What changed between two conversations.

**Assistant text is not comparable and comparing it is worse than useless.** The
same question answered twice gives two different answers, so a diff over the
assistant's half of a conversation reports that every turn is new every time -
a panel that says "everything is different" on every comparison is a panel
nobody reads twice.

What *is* comparable is what the person asked. Two conversations about the same
job share a handful of questions, and the interesting fact is which questions
appear in one and not the other: "have I already asked this?" is the question
this answers, and it is the question a conversation list cannot answer.

**Matching is on normalised text, not on identity.** Whitespace, case and
trailing punctuation do not make a new question, and a diff that reports
"What's the weather?" and "what's the weather ?" as two different questions is
teaching the reader to distrust it.

**Repeats are counted, not collapsed.** A set-based diff hides the fact that a
question was asked three times, which is exactly what someone re-reading an old
conversation wants to know.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

#: Whitespace runs collapse to one space, so a wrapped question and a typed one
#: are the same question.
_WHITESPACE = re.compile(r"\s+")

#: Trailing sentence punctuation, which people add and omit inconsistently.
_TRAILING = " .,:;!?-"

#: A prompt longer than this is truncated for *display*. Matching always uses
#: the whole normalised string, never the truncated one - two questions sharing
#: a 400-character prefix are two different questions, and treating them as one
#: because they both got shortened is a wrong answer rather than a rough one.
DISPLAY_LIMIT = 90


def normalise(text: Any) -> str:
    """The form two questions are compared in.

    Casefolded, NFKC-normalised, whitespace collapsed, trailing punctuation
    dropped. NFKC rather than NFC because a question typed on a phone and the
    same question pasted from a web page routinely differ by more than one
    accent form, and both are the same question.
    """
    if not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _WHITESPACE.sub(" ", folded).strip()
    return folded.strip(_TRAILING).strip()


def content_text(value: Any) -> str:
    """The text of a message's `content`, which is not always a string.

    **A multimodal message stores a list of parts**, `[{...}, {...}]`, and a
    question sent with a screenshot is stored that way. `normalise` returns ""
    for a list, so without this every multimodal question vanishes from the
    comparison and a conversation of them reads as one that asked nothing -
    which is the confident-wrong-answer shape with a comparison panel's face on
    it. The store has the same problem and the same answer in
    `conversation_store._text_of`; this is the same rule, spelled out here so
    this module does not reach for a private name.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(
            str(part.get("text") or "")
            for part in value if isinstance(part, dict))
    return ""


def prompts(messages: "List[Dict[str, Any]]") -> "List[str]":
    """What the person asked, in order, normalised.

    **Only `user` turns.** System prompts are the application's own, tool turns
    are results the model asked for, and assistant turns are the non-
    deterministic half this module exists to avoid.
    """
    out: "List[str]" = []
    for record in messages or ():
        if not isinstance(record, dict) or record.get("role") != "user":
            continue
        text = normalise(content_text(record.get("content")))
        if text:
            out.append(text)
    return out


def repeats(asked: "List[str]") -> "List[str]":
    """The questions asked more than once, most-asked first.

    Sorted by count rather than left in conversation order, because the question
    this answers is "what did I keep asking", and the order they were typed in
    does not contribute to that.
    """
    counts: "Dict[str, int]" = {}
    for text in asked:
        counts[text] = counts.get(text, 0) + 1
    repeated = [(n, t) for t, n in counts.items() if n > 1]
    repeated.sort(key=lambda pair: (-pair[0], pair[1]))
    return [text for _n, text in repeated]


@dataclass(frozen=True)
class PromptDiff:
    """Two conversations' questions, compared.

    `shared` is sorted rather than in conversation order: a set of questions
    both conversations contain has no meaningful order, and sorting makes the
    panel stable between two runs over the same data.
    """

    shared: "Tuple[str, ...]"
    only_old: "Tuple[str, ...]"
    only_new: "Tuple[str, ...]"
    old_count: int
    new_count: int
    old_repeats: "Tuple[str, ...]" = ()
    new_repeats: "Tuple[str, ...]" = ()

    @property
    def identical(self) -> bool:
        """Whether the two asked exactly the same questions.

        Counted, not compared as sets: asking one question twice is not the same
        conversation as asking it once, and a panel that says "identical" for
        those is the confident-wrong-answer shape.

        **Order deliberately does not count.** Two conversations that asked the
        same two questions in the opposite order are the same *questions*, and a
        panel reporting that as four changes would be reporting on a reordering -
        which is not what anyone opens this panel to find out. The order is
        still preserved within `only_old` and `only_new`, where it is the order
        of the work.
        """
        return (not self.only_old and not self.only_new
                and self.old_count == self.new_count)


def diff_prompts(old: "List[str]", new: "List[str]") -> PromptDiff:
    """Compare two lists of already-normalised questions.

    Takes the questions rather than the conversations, so the comparison can be
    tested against numbers whose answer is obvious, and so this module holds no
    knowledge of how a conversation is stored.
    """
    old = [t for t in (old or ()) if t]
    new = [t for t in (new or ()) if t]
    old_set, new_set = set(old), set(new)
    return PromptDiff(
        shared=tuple(sorted(old_set & new_set)),
        only_old=tuple(t for t in old if t not in new_set),
        only_new=tuple(t for t in new if t not in old_set),
        old_count=len(old),
        new_count=len(new),
        old_repeats=tuple(repeats(old)),
        new_repeats=tuple(repeats(new)),
    )


def diff_conversations(old_messages, new_messages) -> PromptDiff:
    """Compare two loaded conversations, by the questions they asked."""
    return diff_prompts(prompts(old_messages), prompts(new_messages))


def for_display(text: str) -> str:
    """One question, shortened for a row.

    An ellipsis is added so a truncated question is visibly truncated - a row
    that ends mid-word with no mark reads as the whole question.
    """
    if len(text) <= DISPLAY_LIMIT:
        return text
    return text[:DISPLAY_LIMIT].rstrip() + "…"