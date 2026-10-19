"""No enforcement-shaped function is silently unwired.

AGENTS.md records what this scan found once: **three functions that looked like
enforcement had no caller, and one of them was `revalidate`** - the only function
that could make a `lab-network` plan file safe, reachable only through
`usr/bin/shani-chronoa-lab-network`, which was mode **644** and so could not be
executed at all. "Code that reads as a control nothing can reach" is worse than
no control, because it reads like one.

The scan re-run here, over `usr/lib` **and `usr/bin`** - the second half is the
lesson of that same finding, because a scan limited to `usr/lib` misses the
privileged entry points entirely. Three names come back, and all three are
**documented** now: `cloud_llm.check_health` and `ollama_llm.check_health` are
dead convenience methods whose question `is_available()` answers, and
`validate_against_profile` is the dict-shaped predicate `refusal_for` shares.

**The rule this file enforces is not "no dead functions" - it is "no dead
function that does not say so".** `gateway.may_execute` had no caller and read
like a security control it was not; its docstring was corrected to say so
plainly. An unwired enforcement-shaped function must name that in its own
docstring, or this fails.

An **orphan** is a function whose name is never *loaded* anywhere in the tree -
not called, not passed as a name, not built into a string a caller executes. The
`def` line is a `FunctionDef` name and not an `ast.Name` load, so it is excluded
by its own context rather than by a subtraction that would also remove every
intra-file call.

`TestTheCheckItself` proves the detector fails on an undocumented orphan and
passes a declared one, because a gate that cannot fail is the "green looks like
coverage" failure this whole file is for.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

#: The name prefixes this repo uses for something that *decides* rather than
#: answers. `check_destination` is not on the list on purpose: it raises on a
#: refusal rather than returning a boolean, and it is called.
_ENFORCEMENT = re.compile(
    r"^(may_|can_|enforce|require|check_|deny|refuse|consent|validate|verify|"
    r"guard|is_allowed|is_permitted|approve|reject|block|allow)", re.I)

#: Words a docstring may use to say "this one is not wired". Synonyms, not a
#: sentence: a reason is a reason however it is phrased.
#:
#: **"only the test server" is in the list** because the shipped tree holds
#: helpers that exist for the test suite - the SIP digest challenge is one,
#: and `tests/fake_jio_router.py` is what calls it. The scan covers
#: `usr/lib` and `usr/bin` only, so a function the *tests* call reads as
#: uncalled unless its docstring says what it is for. The phrase is kept
#: narrow on purpose: "only the test server" says who calls it, where
#: "not wired" would not.
_DECLARED = ("no caller", "never called", "nobody calls", "has no callers",
             "only the test server", "only the test suite")

#: **Exceptions we have decided to accept *undocumented*.** Empty on purpose.
#
# The three unwired enforcement-shaped functions in the tree - both `check_health`s
# and `validate_against_profile` - are all documented now, so this is empty and
# the gate is the strict version: every orphan must declare itself.
#
# It is kept as a mechanism rather than deleted, because the right answer for a
# function that cannot be documented *here* is not to weaken the check. Adding an
# entry is a decision, and `test_an_allowlist_entry_must_not_mask_the_check`
# below is what stops the list from silently becoming the answer - with an entry
# in it, the first version of this gate accepted anything listed whether or not
# its docstring said a word, so removing the honest phrase from
# `validate_against_profile` changed nothing and the control could not fail.
_UNDERSTOOD: "set[tuple[str, str]]" = set()


def _sources() -> "list[pathlib.Path]":
    roots = [pathlib.Path("usr/lib/shani-chronoa"), pathlib.Path("usr/bin")]
    out: "list[pathlib.Path]" = []
    for root in roots:
        if not root.exists():
            continue
        out += [f for f in root.rglob("*.py")]
    return out


def _loaded_names() -> "set[str]":
    """Every name something *loads* anywhere in the tree.

    **String constants count.** `get_landlock_wrapper()` writes a program that
    calls `apply_filesystem_allowlist`, so a generated caller is a string in a
    template - and AGENTS.md records that a call-expression-only scan reported
    exactly that function as dead while it was live.
    """
    loaded: "set[str]" = set()
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
            elif isinstance(node, ast.Attribute):
                loaded.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                loaded.add(node.value)
    return loaded


def _definitions() -> "list[tuple[pathlib.Path, ast.FunctionDef]]":
    out: "list[tuple[pathlib.Path, ast.FunctionDef]]" = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((path, node))
    return out


def _key(path, name) -> "tuple[str, str]":
    return (str(path).replace("usr/lib/shani-chronoa/", ""), name)

def _declares_its_state(node) -> bool:
    """Does the docstring say, in so many words, that nothing calls it.

    **The phrase list is deliberately short.** The first version included
    `"unused"`, which matches incidental prose - removing *"This has no caller"*
    from `validate_against_profile` left the word "unused" in the sentence
    *"must not be left looking like an unused one"*, so the check passed and the
    negative control could not fail. Only the unambiguous forms remain.
    """
    text = (ast.get_docstring(node) or "").lower()
    return any(word in text for word in _DECLARED)


class TestNoEnforcementControlIsSilentlyUnwired:
    def test_every_unwired_one_says_so(self):
        loaded = _loaded_names()
        orphans, silent = [], []
        for path, node in _definitions():
            if not _ENFORCEMENT.match(node.name) or node.name in loaded:
                continue
            if _declares_its_state(node):
                orphans.append(_key(path, node.name))
            else:
                silent.append(_key(path, node.name))
        # `orphans` are the documented ones; `silent` are the defect. `silent`
        # minus the allowlist must be empty, and the allowlist is asserted empty
        # by its own test, so in practice `silent` itself must be empty.
        unexpected = [key for key in silent if key not in _UNDERSTOOD]
        assert not unexpected, (
            f"{len(unexpected)} function(s) read as enforcement, have no caller "
            f"anywhere in usr/lib or usr/bin, and do not say so in their own "
            f"docstring. Each reads like a control that is running when nothing "
            f"is - the `revalidate` defect, where the only caller was a launcher "
            f"in mode 644 and so neither was enforcing anything: {unexpected}")

    def test_the_understood_ones_are_still_the_understood_ones(self):
        """The allowlist is checked against the scan, not trusted.

        If `check_health` gains a caller it stops being an orphan, its entry here
        becomes a lie, and a reader trusting the list keeps believing a function
        is dead after it went live.
        """
        loaded = _loaded_names()
        orphans = {_key(p, n.name) for p, n in _definitions()
                   if _ENFORCEMENT.match(n.name) and n.name not in loaded}
        stale = [key for key in _UNDERSTOOD if key not in orphans]
        assert not stale, (
            f"{len(stale)} allowlist entr(ies) are recorded as understood-and-"
            f"unwired but something now calls them, so the note in their "
            f"docstring is stale and this list should drop them: {stale}")

    def test_an_allowlist_entry_must_not_mask_the_check(self, tmp_path):
        """An entry in `_UNDERSTOOD` does not excuse an undocumented docstring.

        **The failure this file shipped with.** With the three names listed, the
        first gate accepted them whatever their docstring said - so removing
        *"This has no caller"* from `validate_against_profile` changed nothing
        and the negative control could not fail. A list that answers the
        question for you is the same defect as a check that cannot fail.
        """
        understood = set(_UNDERSTOOD)
        assert not understood, (
            f"_UNDERSTOOD still holds {len(understood)} entr(ies) "
            f"({sorted(understood)}). Document them in the module instead - the "
            f"list exists for exceptions nobody wants to write down, and every "
            f"one of these is worth a sentence in the code.")

    def test_the_scan_finds_something(self):
        """A guard against the pattern matching nothing.

        Regex-shaped scans stop matching when the names change, and then this
        file is green having proven nothing.
        """
        total = sum(1 for _, n in _definitions() if _ENFORCEMENT.match(n.name))
        assert total > 20, (
            f"the name pattern matched only {total} function(s) in the tree; "
            f"either the naming convention changed or this gate is blind")


def _orphans_of(module):
    """The scan over one module, for the controls only.

    **The docstring test is part of the predicate, not a filter after it.** The
    first version of this helper only checked whether the name was loaded, so it
    reported the "declared" fixture as an orphan and the control failed on a
    function the rule is meant to accept - which is the direction that would
    have made the rule impossible to satisfy.
    """
    loaded = _loaded_names_for([module])
    out = []
    for node in _module_functions(module):
        if not _ENFORCEMENT.match(node.name):
            continue
        if node.name in loaded:
            continue
        if any(word in (ast.get_docstring(node) or "").lower()
               for word in _DECLARED):
            continue
        out.append(_key(module, node.name))
    return out


def _module_functions(module):
    tree = ast.parse(module.read_text())
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _loaded_names_for(paths):
    loaded = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
            elif isinstance(node, ast.Attribute):
                loaded.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                loaded.add(node.value)
    return loaded


class TestTheCheckItself:
    """The detector must fail on an undocumented orphan and pass a declared one.

    A gate that cannot fail is the same failure as the thing it checks for, so it
    gets the treatment the code it scans gets.
    """

    def test_an_undocumented_orphan_is_reported(self, tmp_path):
        module = tmp_path / "sneaky.py"
        module.write_text(
            "def may_delete_everything(call):\n"
            "    '''Decide whether a call is permitted.'''\n"
            "    return True\n")
        assert [name for _, name in _orphans_of(module)] == [
            "may_delete_everything"], (
            "a function that reads as enforcement, has no caller, and does not "
            "say so was accepted - the exact shape of the `revalidate` defect")

    def test_a_declared_orphan_is_not_reported(self, tmp_path):
        module = tmp_path / "honest.py"
        module.write_text(
            "def may_delete_everything(call):\n"
            "    '''Decide whether a call is permitted. This has no caller: the \"\n"
            "    \"shared predicate is refusal_for, which carries the reason.'''\n"
            "    return True\n")
        assert _orphans_of(module) == [], (
            "a docstring that plainly says it has no caller was still reported, "
            "which would make the rule impossible to satisfy")

    def test_a_used_function_is_not_reported(self, tmp_path):
        module = tmp_path / "live.py"
        module.write_text(
            "def refuse(call):\n"
            "    '''Decide whether a call is permitted.'''\n"
            "    return True\n"
            "def uses_it():\n"
            "    return refuse_caller()\n".replace(
                "refuse_caller()", "refuse({})"))
        assert _orphans_of(module) == []

    def test_a_test_only_helper_may_say_so(self, tmp_path):
        """A helper the *tests* call is not an orphan, it is a helper.

        The scan covers the shipped tree only, so the shipped tree's own
        test-server helpers read as uncalled unless they say who calls them.
        """
        module = tmp_path / "helper.py"
        module.write_text(
            "def check_authorization(header):\n"
            "    '''Whether the header is right. Only the test server calls\n"
            "    this, and it is the answering half of a challenge no shipped\n"
            "    server ever receives.'''\n"
            "    return True\n")
        assert _orphans_of(module) == [], (
            "a helper that says which side calls it was still reported")

    def test_the_test_only_phrase_does_not_admit_an_undocumented_one(self, tmp_path):
        """The new phrase must not become a way past the gate.

        The words have to be *there*; a function that does not say anything
        about being unwired is still reported.
        """
        module = tmp_path / "sneaky.py"
        module.write_text(
            "def check_authorization(header):\n"
            "    '''Whether the header is right.'''\n"
            "    return True\n")
        assert [name for _, name in _orphans_of(module)] == [
            "check_authorization"], (
            "a function that says nothing about being unwired was accepted "
            "because a phrase exists for the other case")
