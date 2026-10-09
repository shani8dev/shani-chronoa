"""A helper nothing imports is a module that is green, tested by nobody, and
reached by nothing.

`skills/window_atspi.py` sat in the skills package for its whole life carrying
`_CHRONOA_NOT_A_SKILL = True` - the opt-out marker that keeps a helper from
logging `Skipping 'builtin:X'` on every startup - and **nothing imported it
anywhere**. 303 lines: AT-SPI focus and close, an availability probe, and its
own xdotool fallback. It was a *superseded duplicate* of
`windows/atspi.py`, which is imported by three skills and by the backend
registry, does the same jobs under the names `focus`/`close`/`available`, and
matches on `title_contains` through `windows/__init__.py:find()`. Removed on
2026-10-09.

**The existing guard could not have seen it.** `test_skill_discovery_noise.py`
asserts that no built-in *logs a skip warning* - which is the marker's whole
purpose and is correct. But a module carrying the marker is silent by
definition, so "no warning" and "nothing imports it" look identical from there.
A helper is exactly the case where the noise test is blind, and it is the only
kind of file in this package that is *designed* to be invisible at startup.

So this is the reverse check: every `NOT_A_SKILL` helper must be imported by
something. It reads the **AST** for the marker rather than grepping the text,
because a module that merely *mentions* the marker in its docstring - which is
what this very file's problem was in a different form, and what
`test_skill_gates_are_enforced.py`'s source-reader had to be taught - would
satisfy a substring search while carrying no marker at all.

**A negative control that cannot fail is not a control**, and the control here
is the shape of the scan rather than a fixture: the check is on *collection
literals assigned at module level*, so a `NOT_A_SKILL = True` written inside a
function cannot make a module look marked. `test_the_scan_can_still_find_a_marked_helper`
proves the detector answers True for a real marked module and False for one
that only mentions the name, so a scanner that quietly stopped matching would
be caught here rather than making this file pass for the wrong reason.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))


def _marked_helpers() -> dict:
    """Modules carrying the opt-out marker, from the AST.

    **Module-level assignment only.** `NOT_A_SKILL = True` inside a function is
    not a marker at all - the loader reads it when it imports the module - so
    accepting one would let a module look exempt without being exempt.
    """
    found = {}
    for path in sorted(_PKG.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in tree.body:  # module level only
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("NOT_A_SKILL"):
                    found[path.relative_to(_PKG)] = target.id
    return found


def _importers(relative: Path) -> list:
    """Modules whose source imports `relative`'s name, by AST not by substring.

    `relative` is `skills/window_atspi.py`, so the dotted name it could be
    imported as is `shani_chronoa.skills.window_atspi` - read off the path
    rather than guessed, because a `from . import window_atspi` relative form
    names the same thing differently.
    """
    dotted = "shani_chronoa." + relative.with_suffix("").as_posix().replace("/", ".")
    short = relative.stem
    hits = []
    for path in sorted(_REPO.glob("**/*.py")):
        if "__pycache__" in path.parts or path == _PKG / relative:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == dotted or node.module.endswith("." + short):
                    hits.append(path)
                    break
                if any(a.name == short for a in node.names):
                    hits.append(path)
                    break
            elif isinstance(node, ast.Import):
                if any(a.name == dotted for a in node.names):
                    hits.append(path)
                    break
            elif isinstance(node, ast.Call):
                # `importlib.import_module("shani_chronoa.skills.window_atspi")`
                func = node.func
                name = getattr(func, "attr", "") or getattr(func, "id", "")
                if name == "import_module" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and first.value == dotted:
                        hits.append(path)
                        break
    return hits


class TestEveryOptedOutHelperIsActuallyImported:
    def test_no_helper_is_marked_and_unused(self):
        """The check `test_skill_discovery_noise.py` structurally cannot make."""
        marked = _marked_helpers()
        assert marked, (
            "no NOT_A_SKILL helper was found at all, so this file is not "
            "checking anything - either the marker was renamed or the scan "
            "stopped matching. A check that cannot fail is the same defect as "
            "a check that always passes")
        # `skills/__init__.py` *defines* the marker rather than carrying it, so
        # it is excluded here rather than above - putting the exemption in the
        # `marked` comprehension instead makes the orphan filter below miss it
        # entirely, which is where my first version put it.
        orphans = {path: name for path, name in marked.items()
                   if path.name != "__init__.py" and not _importers(path)}
        assert not orphans, (
            "these carry the NOT_A_SKILL marker and nothing imports them, so "
            "they are invisible at startup *and* unreachable at runtime - the "
            "exact shape of skills/window_atspi.py, removed 2026-10-09:\n"
            + "\n".join(f"  {p} ({n})" for p, n in sorted(orphans.items())))

    def test_the_scan_can_still_find_a_marked_helper(self):
        """The control: the detector answers True, and answers False too.

        Written as two parses of inline source rather than against the package,
        so it is not asking the tree to contain a marker in order to prove the
        tree can be scanned for one.
        """
        marked = ast.parse("_CHRONOA_NOT_A_SKILL = True\n")
        mentioned = ast.parse(
            '"""This docstring explains what NOT_A_SKILL is for."""\n'
            "def f():\n    return 1\n")
        local = ast.parse("def f():\n    NOT_A_SKILL = True\n    return NOT_A_SKILL\n")

        def names(tree):
            out = set()
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            out.add(target.id)
            return out

        assert "_CHRONOA_NOT_A_SKILL" in names(marked), (
            "the scan missed a real module-level marker")
        assert not names(mentioned), (
            "the scan counted a docstring mentioning the marker as a marker - "
            "which is how a source-reading check passes for the wrong reason")
        assert not names(local), (
            "the scan counted a marker assigned inside a function; the loader "
            "reads it at import, so such an assignment exempts nothing")

    def test_the_removed_module_is_not_importable(self):
        """Named so a reintroduced copy fails here rather than passing quietly.

        If someone adds a second AT-SPI backend, this says so at the point it
        happens instead of leaving two implementations of window focus, which
        is the failure `scan_archive.py` was deleted for.
        """
        assert not (_PKG / "skills" / "window_atspi.py").exists(), (
            "skills/window_atspi.py is back. windows/atspi.py is the live "
            "backend; if this needs its own behaviour, it belongs there, "
            "because two implementations of window focus is how one of them "
            "gets fixed and the other does not")
        with pytest.raises(ImportError):
            __import__("shani_chronoa.skills.window_atspi")
