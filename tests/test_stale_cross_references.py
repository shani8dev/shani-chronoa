"""No comment or docstring may cite a symbol that does not exist.

**Why this gate exists.** Two comments were found citing code that is gone, both
in *security* docstrings - which is exactly the prose that must not rot, because
a reader who cannot follow the citation cannot check the claim:

- `distill.py`'s `Router` promised "Nothing here can authorise a call ... the
  whitelist that permits them is `tools._HANDLERS`, and it is checked there and
  nowhere else." **`_HANDLERS` does not exist in the repo.** The whitelist is
  `tools._HANDLER_FNS` (255 entries, measured) and the check is in
  `_dispatch_inner`. The promise still holds - `test_..._router_docstring...`
  proves it by running - but the citation could not be followed, and a promise
  nobody can check is a promise nobody can rely on.
- `audio.py`'s `AudioPlayer.play_file` justified why two replies can overlap by
  pointing at `` `app._speak` ``, a method with **no caller anywhere in the
  package** (AST-verified, see the test at the end). The mechanism that replaced
  it is `speech.SpeechQueue`'s two dedicated threads.

**Scope, and what is deliberately not a failure.** A citation is a violation only
when it resolves *nowhere*:

- `` `Class._member` `` where `Class` is defined in the citing file - checked
  against that class's members.
- `` `module._member` `` where `module` names a module in this package -
  checked against that module, **searched recursively**, because the package is
  laid out in subpackages (`skills/`, `senses/`, ...) and `sing._verify_sung`
  lives in `skills/sing.py`.

Everything else is **skipped, not failed**: a module name that matches no file, a
CamelCase part (a cross-file class citation), and `self._attr` where the
attribute is only ever assigned inside a method body. Those are undecidable
without flow analysis, and a gate that cries wolf on them gets deleted rather
than obeyed. Two earlier versions of this file failed exactly that way - the
first silently skipped `app._speak` because `app` is a package with no
`app.py`, and the second reported **68 violations on a tree with none** because
`ast.walk` descends into function bodies, so a *local variable* named `config`
looked like a name the file defined.

The citation pattern is deliberately narrow: backtick-delimited `x.y` whose last
segment starts with an underscore. Public names are linters' business; widening
this to them produces noise, not signal.
"""

from __future__ import annotations

import ast
import re
import tokenize
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"

CITE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*\._[A-Za-z0-9_]+)`")

#: Module roots that prose legitimately cites but that are **not in this
#: package**. Both entries were found by the gate flagging them, and both were
#: checked rather than assumed - each is a real reference to real code, so
#: dropping the citation would delete true information:
#:
#: - `a11y_client` - `ui_driver.py` says "`shani-testbed`'s
#:   `a11y_client._bring_into_view`", and the file exists at
#:   `../shani-testbed/lib/a11y_client.py`. A sibling repository, not this one.
#: - `downloads_watchdog` - `gui/browser.py` says it is "ported in spirit from
#:   browser-use's `downloads_watchdog.py`". A different project entirely.
#:
#: Anything added here must come with that kind of evidence. The alternative -
#: dropping the lowercase rule because it flags these two - was rejected, because
#: it would also stop the gate noticing a module of *this* package being deleted,
#: which is the commonest way a citation rots.
EXTERNAL_ROOTS = frozenset({"a11y_client", "downloads_watchdog"})

#: Instance attributes assigned as `self._x = ...` inside any method body. Found
#: by walking method bodies deliberately - they are not module-level names, so
#: the module-level scan in `_defined_names` cannot see them.
_ASSIGNED_ON_SELF = re.compile(r"^self\.[A-Za-z_][A-Za-z0-9_]*$")


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _defined_names(tree: ast.AST) -> set:
    """Names a module defines **at module level only**.

    A first version used `ast.walk` here, which descends into function bodies,
    so a *local variable* named `config` anywhere in `senses/ocr.py` made
    `config` look like a name that file defined. The class-member branch then
    fired, found no class called `config`, and flagged a perfectly correct
    citation of `config._NETWORKED_SENSES`. That single bug was 68 of the 68
    reported violations.
    """
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _class_members(tree: ast.AST, class_name: str) -> set:
    """Methods and attributes defined in one class, plus `self._x = ` anywhere
    in its methods (which is where most private state actually lives)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            out = set()
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef)):
                    out.add(sub.name)
                elif isinstance(sub, ast.Assign):
                    out.update(t.id for t in sub.targets if isinstance(t, ast.Name))
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    out.add(sub.target.id)
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                    targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                    for t in targets:
                        if (isinstance(t, ast.Attribute)
                                and isinstance(t.value, ast.Name)
                                and t.value.id == "self"):
                            out.add(t.attr)
            return out
    return set()


def _is_class(tree: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.ClassDef) and n.name == name for n in tree.body)


def _module_index() -> dict:
    """Every module in the package, keyed by every name it can be cited under.

    Recursive, and keyed three ways, because prose cites packages the way Python
    imports them rather than by file path, and an earlier version got each of
    these wrong:

    - by stem, so `sing.py` in `skills/` answers `sing._verify_sung`;
    - a package's `__init__.py` **also** under the package name, so
      `skills._USER_SKILLS_DIR` resolves to `skills/__init__.py` rather than
      being filed under `__init__`, which it was;
    - a package's submodules under the package name too, so
      `triggers._ensure_state_dir` resolves to `triggers/common.py`.
    """
    index: dict = {}
    for path in sorted(SITE.rglob("*.py")):
        index.setdefault(path.stem, []).append(path)
        parts = path.relative_to(SITE).parts
        if parts[-1] == "__init__.py" and len(parts) > 1:
            index.setdefault(parts[-2], []).append(path)
        for depth in range(1, len(parts)):
            index.setdefault(parts[depth - 1], []).append(path)
    return index


def _assigned_attributes() -> set:
    """Every private attribute *assigned* on any object anywhere in the package.

    This is the escape hatch for runtime attributes. `page._search` in
    `gui/surfaces/activity.py` cites a page **object** built by `build()`, not a
    module: `page._search = view._search`. Statically that is indistinguishable
    from a module citation, and treating it as one produced two false alarms.
    An attribute that is assigned somewhere is a runtime attribute, and a
    citation of one is a runtime claim this gate cannot check.

    It does not blunt the gate: `_HANDLERS` is neither defined nor assigned
    anywhere, which is exactly why it is still reported.
    """
    assigned = set()
    for path in SITE.rglob("*.py"):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, (ast.AugAssign, ast.NamedExpr)):
                targets = [node.target]
            else:
                continue
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr.startswith("_"):
                    assigned.add(t.attr)
    return assigned


def _citable_text(path: Path):
    """(lineno, text) for docstrings and `#` comments - prose only, never code.

    Code is excluded on purpose: a real `` self._speak `` call is a *call*, and
    this gate is about prose pointing at one. Reading only prose also keeps it
    from becoming a second linter.
    """
    tree = _parse(path)
    if tree is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                base = node.body[0].lineno
                for offset, line in enumerate(doc.splitlines()):
                    yield base + offset, line
    try:
        with open(path, "rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type == tokenize.COMMENT:
                    yield tok.start[0], tok.string.lstrip("#").strip()
    except (tokenize.TokenError, OSError):
        return


def _stale_citations():
    index = _module_index()
    assigned = _assigned_attributes()
    found = []
    for path in sorted(SITE.rglob("*.py")):
        prose = list(_citable_text(path))
        if not prose:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for lineno, text in prose:
            for cited in CITE.findall(text):
                module_part, _, attr = cited.rpartition(".")
                leaf = module_part.split(".")[-1]

                # Assigned somewhere => a runtime attribute on an object, not a
                # symbol in a module. Undecidable statically, so not a failure.
                if attr in assigned:
                    continue

                # `Class._member` cited from the file that defines the class.
                if _is_class(tree, leaf):
                    if attr in _class_members(tree, leaf):
                        continue
                    found.append((path, lineno, cited))
                    continue

                # `module._member`: the codebase also cites `module._method` for
                # a method of the one obvious class in that module
                # (`application._init_components`), so a class member counts.
                targets = index.get(leaf)
                if targets:
                    resolved = False
                    for target in targets:
                        tree_t = _parse(target)
                        if tree_t is None:
                            continue
                        if attr in _defined_names(tree_t):
                            resolved = True
                            break
                        if any(attr in _class_members(tree_t, n.name)
                               for n in tree_t.body if isinstance(n, ast.ClassDef)):
                            resolved = True
                            break
                    if resolved:
                        continue
                    found.append((path, lineno, cited))
                    continue

                # No module by that name. A lowercase-initial part is written the
                # way a module is imported, so a missing one is a module that was
                # renamed or deleted - the commonest rot there is, and worth
                # reporting. An uppercase-initial part is a **class**
                # (`WhisperSTT._get_model_path`, `Adw.NavigationPage._x`), which
                # may live in any other file or outside this package entirely, so
                # it is undecidable here and is skipped.
                if leaf[:1].islower() and leaf not in EXTERNAL_ROOTS:
                    found.append((path, lineno, cited))
    return found


STALE = _stale_citations()


def test_no_prose_cites_a_symbol_that_does_not_exist():
    """The gate.

    Asserting against a hard-coded list of the two known offenders would go green
    the moment a third appeared, so this asks the question every run.
    """
    report = "\n".join(f"  {p.relative_to(REPO)}:{line}  cites `{c}`"
                       for p, line, c in STALE)
    assert not STALE, (
        "a comment or docstring cites a symbol that no longer exists; the claim "
        "it supports cannot be followed, and the first of these was a security "
        "docstring:\n" + report)


@pytest.mark.parametrize("prose,expected", [
    ("# see `voice._definitely_absent` for the shape", ["voice._definitely_absent"]),
    ("`app._speak`", ["app._speak"]),
    ("`tool_select._query_words`", ["tool_select._query_words"]),
    ("plain prose with no citation", []),
    ("`tools.execute_tool`", []),          # public: out of scope by design
    ("`markdown_lite`", []),               # a module, not a private member
])
def test_the_citation_pattern_matches_prose_and_ignores_the_rest(prose, expected):
    """Non-vacuity for the extractor itself.

    Without this the gate could stop finding citations entirely and report a
    clean tree forever - green while looking at nothing.
    """
    assert CITE.findall(prose) == expected


def test_a_package_citation_resolves_inside_its_directory():
    """The bug that made the first version useless: `app` has no `app.py`.

    `app/voice.py` is a *module inside a package*, and prose cites `app._speak`.
    A resolver that only tried `<name>.py` called that undecidable and skipped it,
    which is how a gate can miss the exact thing it was written for.
    """
    index = _module_index()
    assert "voice" in index, "app/voice.py must be indexed by its stem"
    assert any(p.parent.name == "app" for p in index["voice"]), (
        "a module inside a package must be indexed, or every `app._name` "
        "citation is silently skipped")


def test_a_subpackage_citation_resolves():
    """`sing._verify_sung` is `skills/sing.py` - one level down.

    Only the subpackage layout makes this interesting: an index that looked in
    the package root alone would call every `skills/` citation undecidable.
    """
    index = _module_index()
    assert "sing" in index, "skills/sing.py must be indexed"
    assert "_verify_sung" in _defined_names(_parse(index["sing"][0]))


def test_a_local_variable_is_not_mistaken_for_a_defined_name():
    """The bug behind all 68 false positives.

    `senses/ocr.py` uses a local named `config`. A `ast.walk`-based definition
    scan sees it, the "is this a class in this file?" branch fires, finds no
    class `config`, and flags the correct citation of
    `config._NETWORKED_SENSES`. Pinned because the regression is invisible: the
    gate still finds real citations, it just finds dozens of imaginary ones.
    """
    index = _module_index()
    assert "config" in index, "config.py must be indexed"
    ocr = SITE / "senses" / "ocr.py"
    tree = _parse(ocr)
    local_names = {n.id for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                   for n in ast.walk(fn) if isinstance(n, ast.Name)}
    if "config" in local_names:
        assert "config" not in _defined_names(tree), (
            "_defined_names must be module-level only; a local named `config` "
            "was enough to make every `config._X` citation report as stale")
    # and the citation resolves, which is the part that was wrong
    assert "_NETWORKED_SENSES" in _defined_names(_parse(index["config"][0]))


def test_the_router_docstring_whitelist_promise_is_true_at_runtime():
    """`distill.py`'s claim, made executable rather than asserted.

    Its docstring promises a ranking model "cannot authorise a call" because a
    whitelist is checked at dispatch. That is only worth something if it is true,
    and it is checkable in both directions:

    - an unlisted name is refused and never runs - the claim itself;
    - the registry the docstring should cite exists, and the name it wrongly
      cites does not - which is what keeps the citation from rotting back.
    """
    from shani_chronoa import tools

    assert hasattr(tools, "_HANDLER_FNS"), "the whitelist registry is gone"
    assert tools._HANDLER_FNS, "the whitelist is empty, so nothing could run"
    assert not hasattr(tools, "_HANDLERS"), (
        "`_HANDLERS` exists again, so `distill.py`'s citation is no longer "
        "stale and this test's premise needs rechecking")

    outcome = tools.execute_tool_outcome("definitely_not_a_skill", {"x": 1})
    assert outcome.ran is False, "an unlisted tool was executed"
    assert "Unknown tool" in outcome.text, outcome.text


def test_the_dead_method_the_stale_comment_pointed_at_is_really_dead():
    """`app._speak` has no caller - AST-verified over the whole package.

    `audio.py`'s docstring cited it as the reason two replies can overlap. If a
    caller ever reappears, the citation stops being stale, and this should fail
    so the comment is re-examined rather than rotting again in the other
    direction.
    """
    hits = []
    for path in SITE.rglob("*.py"):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_speak":
                hits.append(f"{path}:{node.lineno}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"):
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and arg.value == "_speak":
                        hits.append(f"{path}:{node.lineno}")
    assert not hits, (
        f"`_speak` is called again, so its citation is stale the other way: {hits}")
