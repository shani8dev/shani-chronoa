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

    `prefix` puts a word in front of the count - "3 more files" - and
    it is how a sentence says "more": the noun being counted stays the
    noun, so "more changed lines" pluralises the *line* rather than the
    whole phrase.

    **An irregular noun is spelled with both of its forms** -
    `plural(2, "entry/entries")` - because a list of exceptions here
    would be a second table to keep in step, and the tree has had
    enough of those. Every other word is regular, and the rules below
    cover them.
    """
    singular, _, many = word.partition("/")
    if count == 1:
        shown = singular if many else word
    elif many:
        shown = many
    elif word.endswith("y") and not word.endswith(("ay", "ey", "iy", "oy", "uy")):
        shown = word[:-1] + "ies"
    elif word.endswith(("s", "x", "z", "ch", "sh")):
        shown = word + "es"
    else:
        shown = word + "s"
    if prefix:
        return f"{count} {prefix} {shown}"
    return f"{count} {shown}"
