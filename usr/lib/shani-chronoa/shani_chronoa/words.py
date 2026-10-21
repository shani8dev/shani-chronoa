"""The small English a sentence needs, shared so it is said one way.

**"24 fact(s) remembered" is a placeholder, not English.** It is what
code writes when it cannot decide between one and many, and a person
reading it sees the machine's working rather than the answer they came
for. The same shape was written out in **seventy-five places** across
the GUI - measured by walking the package's own string literals, so a
comment cannot be counted and a panel that only shows the sentence when
it has data is still counted.

`plural()` is the one answer, so the shape is said one way rather than
seventy-five ways.
"""

from __future__ import annotations

__all__ = ["plural"]


def plural(count: int, word: str, prefix: str = "") -> str:
    """The counted phrase: "1 fact", "2 facts".

    `prefix` puts a word in front of the count - "3 more files" - which
    is the shape a few sentences need ("more changed lines").

    **Only regular plurals are guessed at.** Every word this is called
    with in this tree is one of them; an irregular noun is spelled with
    both of its forms by the caller, so nothing here has to know a
    list of exceptions.
    """
    if count == 1:
        shown = word
    elif word.endswith("y") and not word.endswith(("ay", "ey", "iy", "oy", "uy")):
        shown = word[:-1] + "ies"
    elif word.endswith(("s", "x", "z", "ch", "sh")):
        shown = word + "es"
    else:
        shown = word + "s"
    if prefix:
        return f"{count} {prefix} {shown}"
    return f"{count} {shown}"
