"""The GUI does not say "2 thing(s)".

**"24 fact(s) remembered" is a placeholder, not English.** It is what
code writes when it cannot decide between one and many, and a person
reading it sees the machine's working rather than the answer they came
for. The same shape was written out in **seventy-five places** across
the GUI - measured by walking the package's own string literals.

**The scan walks the AST, so it is stronger than a runtime sweep.**
A comment cannot be counted (a docstring is not a sentence anybody
reads), and a panel that only shows its sentence when it has data - the
Memory panel with a live store, the Calendar with events, the Export
panel with a conversation open - is still counted, which a sweep that
builds every panel on an empty machine cannot do.

**The senses' and the skills' own wording is not in scope here**, and
the reason is worth stating: a sense's summary and a skill's answer are
written for the *model*, which reads them through the percept store.
Bending them for the person would change the model's own contract for
the sake of a screen they only pass through - and the Memory panel that
shows them raw is a separate piece of work (the split into "what you
told it" and "what its senses took").
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

_PACKAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr", "lib", "shani-chronoa", "shani_chronoa")
_GUI = pathlib.Path(_PACKAGE) / "gui"

#: A count with a bracketed plural left on it: "2 file(s)".
PLACEHOLDER = re.compile(r"\b[\w'-]+\(s\)")

#: The URL scheme, which is spelled that way on purpose.
_ALLOWED = {"http(s)"}


def _strings(path: pathlib.Path):
    """Every string literal in a file that is not a docstring.

    **A docstring is not a sentence anybody reads** - it is the module's
    own note to the next reader - so counting it would be counting
    prose about the code rather than prose for the person.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node


def test_the_gui_says_how_many_things_rather_than_two_of_anything():
    """**Every sentence the GUI shows says how many things there are.**

    The count is spelled out by one helper (`shani_chronoa.words.plural`),
    so the shape is said one way rather than seventy-five ways.
    """
    offenders = []
    for path in sorted(_GUI.rglob("*.py")):
        for node in _strings(path):
            found = [hit for hit in PLACEHOLDER.findall(node.value)
                     if hit not in _ALLOWED]
            if found:
                offenders.append(
                    f"{path.relative_to(_PACKAGE)}:{node.lineno}: {found} "
                    f"in {node.value.strip()[:70]!r}")
    assert not offenders, (
        "the GUI says '2 thing(s)' at "
        f"{len(offenders)} place(s):\n  " + "\n  ".join(offenders))


def test_the_helper_says_one_and_many_correctly():
    """**The helper is the one answer, so it is worth a witness of its
    own** - and it has to be right about the awkward cases: one, none,
    a word ending in a y, and a word that already ends in an s."""
    from shani_chronoa.words import plural

    assert plural(1, "fact") == "1 fact"
    assert plural(2, "fact") == "2 facts"
    assert plural(0, "fact") == "0 facts"
    assert plural(1, "tool call") == "1 tool call"
    assert plural(3, "tool call") == "3 tool calls"
    assert plural(2, "entry") == "2 entries"
    assert plural(2, "class") == "2 classes"
    assert plural(2, "day") == "2 days"
    assert plural(2, "file", prefix="more") == "2 more files"
    assert plural(1, "file", prefix="more") == "1 more file"
