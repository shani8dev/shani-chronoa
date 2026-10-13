"""Nothing public in the package is called by nothing, without a written reason.

`AGENTS.md` holds the repo's standard: **no *undocumented* orphans**. This file is
what makes that checkable, and it exists because three separate blind spots in a
hand-run version of this audit produced four wrong answers in a row.

## The three blind spots, each now a control

**1. Dotted calls were discarded.** A candidate was declared unreached because the
audit searched `[^a-z_.]name(` — and that character class *excludes the dot*, so
`screen_stream.gstreamer_ready()` looked exactly like a definition. Every
module-qualified call in the tree was invisible.

**2. `usr/bin/` was invisible.** The launchers have **no `.py` extension**, so a
`--include=*.py` scan skipped all of them. Four functions were reported unreached
that the launchers call: `gstreamer_ready`, `render_for_log`, `evaluate_many`,
`fire_all`. The correct count for `gstreamer_ready` is one caller, in
`usr/bin/shani-chronoa-screen-stream`.

**3. The defining file was subtracted.** Subtracting `{own_file}` from the
references throws away *intra-module* calls, which is where most of a small
module's helpers are used. This was the largest single error: **267 apparent
orphans collapsed to 40** once it was dropped, because an `ast.FunctionDef` keeps
its name as a string and is therefore never an `ast.Name` node — so any hit at all
is a real reference, and no subtraction is needed.

A fourth was found by running this: **code embedded in a string**. A test that
executes a child process passes the child's source as a triple-quoted string, so
`prctl_set_seccomp_attempt` was called at `tests/test_sandbox_seccomp.py:384` and
invisible to AST walking. String constants are now recorded twice — whole, for
table dispatch and `getattr` — and tokenised, for code inside them. That removed
thirteen more false positives, finishing at **26**.

## What this does and does not claim

It does not claim the list is complete. It claims that **this set** is the set of
unreferenced public callables **this method** finds, and that a new one fails. A
dispatch mechanism outside AST reach — `exec`, a plugin looked up by string at
runtime in another process, C code — would still hide here.

**The scan method is itself tested.** `test_the_scan_can_still_see_*` below are
negative controls: each one *fails* if the corresponding blind spot comes back. A
guard that silently stops detecting is worse than no guard, because it reads as
green.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "usr/lib/shani-chronoa"

sys.path.insert(0, str(PKG))

#: Directories that are not this project's source.
_SKIP = ("cache/", ".git/", "__pycache__", "node_modules", "harness-study/")

_TOKEN = re.compile(r"[A-Za-z_]\w*")

#: A `def NAME` inside an embedded script is a *definition*, not a reference.
#: Found by running this: two tests embed a child that defines its own
#: `log_message`, the tokeniser counted it, and `log_message` stopped being an
#: orphan for no reason. The same rule that makes a real `FunctionDef` invisible
#: has to apply inside a string.
_DEFINITION = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)")

#: A string is treated as **code** - and therefore tokenised - only when it carries
#: a marker that prose does not have. Found by running this: `distill.distil()` is
#: called by nothing anywhere, and the guard did not report it, because the runtime
#: message `"...there is no teacher to distil from"` is an ordinary string constant
#: and tokenising it produced a reference to `distil`.
#:
#: So tokenising *every* string trades a false negative for prose that says a
#: function's name out loud. The markers below are what an embedded script or a
#: table lookup actually contains; a sentence to a person contains none of them.
_CODE_MARKER = re.compile(r"(?:\b(?:def|class|import|lambda)\s|\(\s*\)"
                          r"|\w+\s*=\s*\w+\s*\(|^\s*\w+\.\w+\()")


def _tokens_of(source):
    """Identifier tokens in a string, minus any that are *only* definitions.

    Returns nothing for a string with no code marker in it, so prose naming a
    function is not a reference to it.

    "Only" matters: a name that is both defined and used in the same embedded
    script is a reference, and dropping it would be a false negative.
    """
    if not _CODE_MARKER.search(source):
        return set()
    counts = {}
    for match in _TOKEN.finditer(source):
        counts[match.group()] = counts.get(match.group(), 0) + 1
    defined = {}
    for match in _DEFINITION.finditer(source):
        defined[match.group(1)] = defined.get(match.group(1), 0) + 1
    return {name for name, seen in counts.items()
            if defined.get(name, 0) < seen}

#: Decorators whose function body is not the interesting part.
_SKIP_DECORATORS = ("property", "cached_property", "abstractmethod", "override")

#: Names a **framework resolves by name**, so no code in this tree can refer to
#: them and no scanner can see that they are called.
#:
#: `http.server.BaseHTTPRequestHandler.handle_one_request` builds
#: `mname = "do_" + self.command` and then does `getattr(self, mname)` - so a
#: `do_GET` is called by the *request method string*, and `log_message` is called
#: by `log_request`. Verified against the stdlib source, not inferred.
#:
#: These were three entries in `ALLOWED`, each carrying that explanation in prose.
#: A prose reason is the right shape for "nobody calls this and here is why"; it is
#: the wrong shape for "this is called and the calling is invisible". The second
#: kind belongs in a declared exemption like `_SKIP_DECORATORS`, where it cannot
#: quietly absorb a genuine orphan: `test_the_framework_dispatch_exemption_names_
#: only_real_handlers` requires every name here to exist on a class that inherits
#: `BaseHTTPRequestHandler`.
_FRAMEWORK_DISPATCH = ("do_GET", "do_POST", "log_message")

#: Unreferenced public callables, each with why it is allowed to stay.
#:
#: **Every entry carries a reason, and a reason of "" is a failure.** An allowlist
#: of bare names is how an audit becomes a rubber stamp: the names stop being
#: examined, and the next genuine orphan joins them by being copied.
#:
#: **Empty, and empty on purpose.** It held 26 names once; 18 were deleted, 2 were
#: wired, and the last 6 (`fence_all`, `highpass`, `list_voices`, `load_dict`,
#: `section_ids`, `verify_digest`) went with the rest of that batch.
#: `test_the_allowlist_is_not_stale` is what forced it: a name that has become
#: reachable has to leave, so an allowlist permitted to survive its own entries is
#: not tracking anything.
#:
#: The reasons that were here each failed in one of two ways, and both are worth
#: recording because both look sound when written: the thing was reachable by a
#: route nobody had grepped (`load_dict` - a bare `load(` inside the defining
#: module, which a `module.func(` search drops), or it was a duplicate the guard
#: could count because the name sat in a *string* (`show_devices` in a `getattr`
#: tuple, `context_report` in `getattr(assistant, "context_report", None)`).
#:
#: `learning.py` is the remainder, and it is deliberately **not** folded in here:
#: its toolbox exclusion is derived from that file's own classes, and the controls
#: below assert it still yields something, so it cannot silently empty itself.
ALLOWED: dict = {}


#: This file. Excluded from the *reference* pass, for a reason found by running it:
#: `ALLOWED` is a dict of string keys, so every name on the list appeared
#: "referenced" by the list itself, and `test_the_allowlist_is_not_stale` failed
#: with all 26 names in it. An audit that counts itself is not an audit.
_SELF = "test_no_unwired_public_helper.py"



#: Unreferenced **private** callables. Kept separate from `ALLOWED` because the
#: bar is different: a private name is allowed to be spare far more often, and
#: merging the two lists would make the public one look arbitrary.
#:
#: **Empty, and empty for the same reason as `ALLOWED`.** Eleven names were here;
#: 8 were deleted and 3 were wired (`_remote`, `_lsusb`, `_numeric`), and wiring
#: them closed three real defects rather than tidying a list.
#:
#: Probed by asking what the guard deliberately skips: **0 unreferenced module
#: constants** of 3557, and 14 unreferenced private functions of 3652.
PRIVATE_ALLOWED: dict = {}



#: Unreferenced **public classes**, with the same two properties as `ALLOWED`.
#:
#: **Empty.** The two names that were here (`IndexError_`, `Task`) were deleted;
#: `Task` was a vestige of the upstream framework `goals.py` was written against,
#: and its two jobs were already done by `PlannedStep` and `Phase`.
#:
#: Probed after the callables and the constants: **0 unreferenced enum members** of
#: 33, and 22 unreferenced classes of 380 - **19 of them in `learning.py`**, the
#: concurrent session's area. So three were real candidates and one of those
#: (`speech_gate.Busy`) turned out to document a raise nothing performed.
CLASS_ALLOWED: dict = {}



#: `learning.py`'s own classes - the documented statistics toolbox.
#:
#: **This is not an allowlist and does not claim to be.** The three allowlists above
#: (`ALLOWED`, `PRIVATE_ALLOWED`, `CLASS_ALLOWED`) are now empty; every entry in them
#: was wired or deleted. What remains is a *documented deliberate* population:
#: `AGENTS.md` records it as a toolbox kept on purpose - *"keep learning even when
#: the result is not yet actionable"* - and `learning.py`'s own `__all__` exports the
#: top of it as library surface.
#:
#: It replaced a 29-name hand-kept list that was *correct* - every orphan was in it,
#: and all 29 names were real classes - but which nothing could check. Seven of its
#: entries subtracted names the class scan never reports anyway, so a name added or
#: mis-spelled there would have been invisible in both directions.
#:
#: So it is derived now: the classes of one module, read out of the source. The
#: controls that keep it honest are `test_the_toolbox_exclusion_cannot_reach_
#: another_file` (it must never name a class declared outside `learning.py`, which
#: is what an allowlist does the moment somebody adds a name to it) and
#: `test_the_toolbox_exclusion_never_covers_a_live_class` (every name it subtracts
#: from the orphans must actually be an orphan, so it cannot absorb live code).
_TOOLBOX_MODULE = "learning.py"


def _toolbox_class_names():
    """Class names declared in the toolbox module, read out of the source.

    **Read from the file, never typed.** A name that stops existing disappears on
    the next run rather than sitting in a list nothing re-derives.
    """
    roots, nested = _toolbox_roots(), set()
    for path in sorted(PKG.rglob(_TOOLBOX_MODULE)):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            up = parents.get(node)
            while up is not None:
                if isinstance(up, ast.ClassDef):
                    if up.name in roots:
                        nested.add(node.name)
                        break
                up = parents.get(up)
    # **A class a seam reaches is not in the toolbox, nested or not.**
    # `estimators.py` re-exports `EMA`, `KMeans` and `KNN` from here (the
    # split-modules commit), so three classes inside the sections are live - and an
    # exclusion that covers them has promised not to report a class that something
    # outside this file reaches. That is the over-claiming the emptied allowlists
    # were made of, arrived at by a different route: it costs nothing today,
    # because none of the three is an orphan, so dropping them changed no audit
    # result at all. The measurement is in the failure this makes catchable, not in
    # a number that moved.
    return {n for n in roots | nested if not _seam_reaches(n)}


def _toolbox_roots():
    """The toolbox's module-level sections: lowercase classes no seam reaches.

    **Not every class in the module.** `learning.py` also holds live machinery -
    `OutcomeModel`, `Report`, `decision` - and an exclusion covering the whole
    file would absorb those too, silently, the day any of them came unwired. The
    sections are the lowercase module-level classes, and which of those count is
    decided by `_seam_reaches`, the non-blind reader: a token scan sees `stats` as
    a local variable in three other modules and would call every section wired.

    **`_toolbox_module_classes` was deleted on 2026-10-13 and did not need to be.**
    It computed the same eleven names by a second route - "module-level classes
    that appear in `_toolbox_class_names()`" - which is the same set as this
    function, because a nested class is never module-level. Two derivations of one
    number is the drift this file records for `capabilities._GROUPS` and for the
    `skills_total` figure: one is checked and the other rots. This is the
    primitive the whole derivation builds on, so it is the one that stays.
    """
    names = set()
    for path in sorted(PKG.rglob(_TOOLBOX_MODULE)):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name[:1].islower():
                names.add(node.name)
    return {n for n in names if not _seam_reaches(n)}


#: Unreferenced **`@property` / `@cached_property` members**.
#:
#: These are the last declaration class the audit skipped, and skipping them was
#: how `guardrail.should_reask` and `should_stop` survived - a property is read as
#: an attribute (`result.should_stop`), never called, so reference *counting*
#: against `name(` never sees it.
#:
#: No separate allowlist: all 117 are read. The dict exists so a future entry has
#: to carry a reason, and so the empty result is an assertion rather than a shrug.
#: The six toolbox classes the class audit **cannot** see, and why.
#:
#: Their names are ordinary English words, so the reference pass finds them as
#: local variables in other modules - `memory`, `online`, `stats`, `regression`,
#: `clustering` and `rl` - and reports no unreferenced class for any of them.
#: A named list rather than six inline strings, so the audit control and the
#: blind-spot control cannot disagree about which classes are invisible.
_BLIND_TOOLBOX = {
    "memory", "online", "stats", "regression", "clustering", "rl",
}


PROPERTY_ALLOWED: dict = {}


def _sources():
    """Every file that could hold a reference.

    **`.py` *and* the extension-less `usr/bin/` launchers.** Blind spot 2.
    """
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        text = str(path)
        if any(skip in text for skip in _SKIP):
            continue
        if path.name == _SELF:
            continue
        if path.suffix == ".py" or (path.parent.name == "bin" and "usr/" in text):
            yield path


def _docstring_lines(tree):
    """Line numbers occupied by a docstring, so prose is not a reference."""
    spans = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                spans.update(range(body[0].lineno, body[0].end_lineno + 1))
    return spans


def _all_lines(tree):
    """Line numbers belonging to an `__all__` assignment, and its elements.

    Both halves: the `__all__ = [...]` statement and the string elements inside
    it. Skipping only the elements would leave the assignment itself, but the
    names there are the strings, so the elements are what matter - both are
    returned so neither can leak through.
    """
    lines = set()
    for node in ast.walk(tree):
        targets = getattr(node, "targets", None)
        if not isinstance(node, ast.Assign) or not targets:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def _toolbox_methods_per_class():
    """`{toolbox class: excluded public callables}` from the source.

    Not from the audit: a count the audit derives about the audit is the
    over-the-shoulder agreement this file records for a hand-kept number.
    """
    import ast as _ast

    per = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts or path.name != "learning.py":
            continue
        try:
            tree = _ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parents = {}
        for node in _ast.walk(tree):
            for child in _ast.iter_child_nodes(node):
                parents[child] = node
        for node in _ast.walk(tree):
            if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                continue
            up = parents.get(node)
            while up is not None:
                if isinstance(up, _ast.ClassDef):
                    per.setdefault(up.name, []).append(node.name)
                    break
                up = parents.get(up)
    return {name: calls for name, calls in per.items()
            if name in _toolbox_class_names()}


def _toolbox_locations():
    """`"file:line"` for every public callable defined inside the toolbox.

    **Derived from the AST at test time, and deliberately not a name list.** The
    three allowlists this file used to carry all became rubber stamps - a name
    that only grows, absorbing whatever is near it - and emptying them was the
    point. A toolbox exclusion that works has to be the same idea expressed the
    other way round: *only ever* `learning.py`, only ever the classes the guard
    already documents, and computed from the source each run so a rename shows up
    as a change rather than as a stale entry.

    A nested class counts, because the majority of the toolbox is nested: measured
    2026-10-13 at **11 module-level sections and 25 classes inside them**, 36 in
    total. That number is hand-written here and will drift - what does not drift is
    the rule, so the count is only ever a description of the derivation above.
    """
    out = set()
    # **One module, and only that one.** A name-keyed exclusion matched `Report`
    # in `context_meter.py` and `Example` beside it, because those files declare
    # classes with the same names - so a toolbox entry would have silently
    # absorbed orphans in another module. The derivation walks the toolbox module
    # and nothing else, so a location it returns is in that file by construction.
    for path in sorted(PKG.rglob(_TOOLBOX_MODULE)):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            chain = []
            up = parents.get(node)
            while up is not None:
                if isinstance(up, ast.ClassDef):
                    chain.append(up.name)
                up = parents.get(up)
            if any(name in _toolbox_class_names() for name in chain):
                out.add("%s:%d" % (path.relative_to(ROOT), node.lineno))
    return out


def _referenced_names():
    """`{name: {files}}` for every name the tree refers to."""
    used = {}
    for path in _sources():
        try:
            text = path.read_text(errors="replace")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            names = set(_TOKEN.findall(path.read_text(errors="replace")))
            for name in names:
                used.setdefault(name, set()).add(str(path))
            continue

        # **`__all__` is an export list, not a use.** A name that appears only
        # there has been *offered* to importers and *taken up* by nobody, which
        # is the same condition this whole file exists to report - counting it as
        # a reference makes the audit blind to it.
        #
        # Measured: 12 names across the package had `__all__` as their only
        # reference anywhere in the tree - `learning.bandits`, `learning.anomaly`,
        # `learning.timeseries`, `learning.bayes`, `learning.train_and_report`,
        # `learning.vectorise`, `learning.score_predictions`, `bandit.save_bandit`,
        # `capabilities.capability_for`, `code_hl.supports`, and two from
        # `sandbox/__init__.py`. Seven of them are whole learning capabilities.
        skip = _docstring_lines(tree) | _all_lines(tree)
        names = set()
        for node in ast.walk(tree):
            if getattr(node, "lineno", None) in skip:
                continue
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, ast.alias):
                names.add((node.asname or node.name).split(".")[-1])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)                    # getattr / table dispatch
                names.update(_tokens_of(node.value))  # code inside a string
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                names.update(node.names)
        for name in names:
            used.setdefault(name, set()).add(str(path))
    return used


def _defined_public(used):
    """`{name: "file:line"}` for public callables nothing refers to."""
    orphans = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            if node.name in _FRAMEWORK_DISPATCH:
                continue
            if any((getattr(d, "id", None)
                    or getattr(getattr(d, "func", None), "id", None))
                   in _SKIP_DECORATORS for d in node.decorator_list):
                continue
            # **No own-file subtraction.** Blind spot 3.
            if used.get(node.name):
                continue
            orphans.setdefault(node.name, []).append(
                f"{path.relative_to(ROOT)}:{node.lineno}")
    return orphans


def _defined_private(used):
    """`{name: "file:line"}` for private callables nothing refers to.

    **One underscore, dunders excluded.** `_read_int` appears in three unrelated
    modules, so a private name is only ever a candidate within its own file - the
    collision is the point of the convention.
    """
    orphans = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if not name.startswith("_") or name.startswith("__"):
                continue
            if any((getattr(d, "id", None)
                    or getattr(getattr(d, "func", None), "id", None))
                   in _SKIP_DECORATORS for d in node.decorator_list):
                continue
            if used.get(name):
                continue
            orphans.setdefault(name, []).append(
                f"{path.relative_to(ROOT)}:{node.lineno}")
    return orphans


def _defined_classes(used):
    """`{name: "file:line"}` for public classes nothing refers to."""
    orphans = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith("_") or used.get(node.name):
                continue
            orphans.setdefault(node.name, []).append(
                f"{path.relative_to(ROOT)}:{node.lineno}")
    return orphans


def _defined_properties(used):
    """`{name: "file:line"}` for property members nothing reads.

    **A property is read, never called**, so the name is looked up bare: the
    reference pass already collects `ast.Attribute` attrs, so `result.should_stop`
    is found the same way a bare mention would be. A reference-count against
    `name(` finds none of them, which is how this class was missed.
    """
    orphans = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for stmt in cls.body:
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not any((getattr(d, "id", None)
                            or getattr(getattr(d, "func", None), "id", None))
                           in ("property", "cached_property")
                           for d in stmt.decorator_list):
                    continue
                if used.get(stmt.name):
                    continue
                orphans.setdefault(stmt.name, []).append(
                    f"{path.relative_to(ROOT)}:{stmt.lineno}")
    return orphans


def _orphans():
    return _defined_public(_referenced_names())


def _property_orphans():
    return _defined_properties(_referenced_names())


def _class_orphans():
    return _defined_classes(_referenced_names())


_SEAM_NAMES = None


def _seam_reaches(name):
    """True when a real call seam reaches `name` in the toolbox module.

    **The instrument the blind cannot use.** A bare-token scan sees `stats` as a
    local variable in three other modules; this asks the two ways the tree can
    actually reach a name it did not define in that file - an attribute on the
    module, or an import from it. Six classes are invisible to the token scan, so
    saying they are unwired needs a second reader that is not blind the same way.

    The names are collected **once** and cached: `_toolbox_roots` asks this about
    every section, and a per-name re-walk of the tree turned a 167s guard run into
    a 563s one. The cache is filled on first use and cleared when a plant rebinds
    the package, so a scratch tree can ask about itself.
    """
    global _SEAM_NAMES
    if _SEAM_NAMES is None:
        _SEAM_NAMES = set()
        for path in _sources():
            if path.name == _SELF:
                continue
            try:
                tree = ast.parse(path.read_text(errors="replace"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) \
                        and isinstance(node.value, ast.Name) \
                        and node.value.id == "learning":
                    _SEAM_NAMES.add(node.attr)
                if isinstance(node, ast.ImportFrom) \
                        and node.module == "shani_chronoa.learning":
                    for alias in node.names:
                        _SEAM_NAMES.add(alias.name)
    return name in _SEAM_NAMES


def _forget_seam_cache():
    """Drop the cached seam set, so a rebound package is re-read."""
    global _SEAM_NAMES
    _SEAM_NAMES = None


def _planted_toolbox_location_stays_in_its_module():
    """False when the toolbox exclusion reports a callable outside its own module.

    **This is the control for `other` in
    `test_the_toolbox_exclusion_cannot_reach_another_file`, which measured as
    unable to fail.** A scratch tree is built holding two modules: the toolbox
    module, with a root class `stats` carrying one public method and one
    module-level function beside it, and a *second* module declaring its own
    `class stats` with a public method.

    A derivation confined to the toolbox module reports exactly one location - the
    method inside `stats` - and nothing else: the second module is invisible, and
    so is the module-level function, which has no enclosing class to hang the
    exclusion on. Widened to `*.py`, it reports both modules.

    Both halves matter. Reporting nothing at all satisfies "not outside the
    module" trivially, which is why the count of one is part of the answer rather
    than only the absence of the second module.
    """
    import pathlib as _pathlib
    import tempfile as _tempfile

    global PKG, ROOT
    real_pkg, real_root = PKG, ROOT
    try:
        with _tempfile.TemporaryDirectory(prefix="plant-toolbox-") as tmp:
            base = _pathlib.Path(tmp)
            pkg = base / "pkg"
            pkg.mkdir()
            (pkg / "learning.py").write_text(
                "class stats:\n"
                "    def mean():\n"
                "        return 0\n"
                "def not_inside_a_class():\n"
                "    return 0\n"
            )
            (pkg / "other.py").write_text(
                "class stats:\n"
                "    def mean():\n"
                "        return 0\n"
            )
            PKG, ROOT = pkg, base
            _forget_seam_cache()
            locations = _toolbox_locations()
            in_module = [l for l in locations if "learning.py" in l]
            outside = [l for l in locations if "learning.py" not in l]
            return len(locations) == 1 and bool(in_module) and not outside
    finally:
        PKG, ROOT = real_pkg, real_root
        _forget_seam_cache()


def _planted_class_orphan_is_found():
    """True when `_class_orphans` reports a class planted in a scratch package.

    The public counterpart of `_planted_private_orphan_is_found`: one public class,
    referenced nowhere, must be reported. Both are needed because a scan that has
    stopped reading the tree returns the same empty dict it would return for a
    clean tree, so only a plant tells the two apart.
    """
    import pathlib as _pathlib
    import tempfile as _tempfile

    global PKG, ROOT
    real_pkg, real_root = PKG, ROOT
    try:
        with _tempfile.TemporaryDirectory(prefix="plant-class-") as tmp:
            base = _pathlib.Path(tmp)
            (base / "pkg").mkdir()
            (base / "pkg" / "_planted.py").write_text(
                "class NothingNamesThis:\n"
                "    pass\n\n\n"
                "class ThisOneIsUsed:\n"
                "    pass\n\n\n"
                "PUBLIC = ThisOneIsUsed\n"
            )
            PKG = base / "pkg"
            ROOT = base
            found = _class_orphans()
            return "NothingNamesThis" in found
    finally:
        PKG, ROOT = real_pkg, real_root


def _planted_private_orphan_is_found():
    """True when `_defined_private` reports a helper planted in a scratch package.

    Writes a module holding one private function that nothing refers to, points
    `PKG` at it, and asks the real scanner. `PKG` and `ROOT` are restored in a
    `finally` because the failure this guards against is a scan that returns an
    empty dict - and a scan pointed at the wrong tree returns exactly that.
    """
    import pathlib as _pathlib
    import tempfile as _tempfile

    global PKG, ROOT
    real_pkg, real_root = PKG, ROOT
    try:
        with _tempfile.TemporaryDirectory(prefix="plant-private-") as tmp:
            base = _pathlib.Path(tmp)
            (base / "pkg").mkdir()
            (base / "pkg" / "_planted.py").write_text(
                "def _nothing_calls_this_anywhere():\n"
                "    return 0\n\n\n"
                "def _this_one_is_used():\n"
                "    return 1\n\n\n"
                "PUBLIC = _this_one_is_used()\n"
            )
            # point the scanner at the scratch package, and the reference pass at
            # the same tree only - so the planted name is genuinely unreferenced
            PKG = base / "pkg"
            ROOT = base
            referenced = _referenced_names()
            found = _defined_private(referenced)
            return "_nothing_calls_this_anywhere" in found
    finally:
        PKG, ROOT = real_pkg, real_root


def _private_orphans():
    return _defined_private(_referenced_names())


# --- the audit ----------------------------------------------------------------

def test_no_public_helper_is_called_by_nothing():
    orphans = _orphans()
    # **The toolbox is excluded by definition site, never by name.** A name would
    # be the allowlist this file just emptied, one file down: it would absorb a
    # method of `decision` or `Bandit` on the strength of a name someone typed.
    # `_toolbox_locations()` can only ever point at `learning.py`, and
    # `test_the_toolbox_exclusion_cannot_reach_another_file` asserts exactly that.
    toolbox = _toolbox_locations()
    unexpected = {name: [loc for loc in locs if loc not in toolbox]
                  for name, locs in orphans.items()}
    unexpected = {n: l for n, l in unexpected.items() if l}

    assert not unexpected, (
        f"{len(unexpected)} public callable(s) that nothing in the tree "
        f"references:\n"
        + "\n".join(f"  {name}  ({', '.join(locs)})"
                    for name, locs in sorted(unexpected.items()))
        + "\n\nEither wire it, or delete it. (There is no allowlist to add it to "
          "any more - that is the point of this file now.)")


def test_every_allowed_name_carries_a_reason():
    """A bare-name allowlist is a rubber stamp: the names stop being examined and
    the next genuine orphan joins them by being copied."""
    without = sorted(name for name, why in ALLOWED.items() if not why.strip())

    assert not without, (
        f"ALLOWED entries with no reason: {without}. An allowlist entry is a claim "
        f"that someone looked; an empty string is not that.")


def test_the_allowlist_is_not_stale():
    """**And in the other direction.** A name that has become reachable should
    leave the list, or the list stops being evidence of anything."""
    orphans = _orphans()
    gone = sorted(set(ALLOWED) - set(orphans))

    assert not gone, (
        f"ALLOWED names that are now referenced again: {gone}. Remove them - an "
        f"allowlist that only ever grows stops tracking reality.")


# --- the controls: each fails if its blind spot returns ----------------------

def test_the_scan_can_still_see_a_dotted_call():
    """Blind spot 1. `gstreamer_ready` is called as
    `screen_stream.gstreamer_ready()` - dotted, so a `[^a-z_.]name(` search misses
    it and reports it unreached, which is exactly what happened."""
    orphans = _orphans()

    assert "gstreamer_ready" not in orphans, (
        "a dotted call is invisible again: the scan is treating `module.name(` as "
        "a definition")


def test_the_scan_can_still_see_the_launchers():
    """Blind spot 2. The `usr/bin/` launchers have no `.py` extension."""
    sources = [str(p) for p in _sources()]
    launchers = [s for s in sources if s.endswith("shani-chronoa-screen-stream")]

    assert launchers, (
        "the extension-less launchers are not being scanned; four functions are "
        "called only from there")


def test_the_scan_can_still_see_code_embedded_in_a_string():
    """Blind spot 4, found by running this. A test executes a child whose source
    is a triple-quoted string, so `prctl_set_seccomp_attempt` is called from
    `tests/test_sandbox_seccomp.py:384` and AST walking alone cannot see it."""
    orphans = _orphans()

    assert "prctl_set_seccomp_attempt" not in orphans, (
        "code inside a string is invisible again: string constants are no longer "
        "being tokenised")


def test_the_scan_does_not_count_prose_for_a_reference():
    """Blind spot 5, found by running this. `distill.distil()` is called by
    `tools/distill_run.py`, and before the string tokeniser learned to look for a
    code marker the guard reported `distil` as *uncalled* - because the runtime
    message `"...there is no teacher to distil from"` is an ordinary string
    constant, and every token in a string was being read as a reference.

    So tokenising every string trades a false negative for prose that happens to
    say a function's name. `distil` is the case that proved it.

    **This tests the tokeniser, not `distil`.** Asserting "`distil` is not an
    orphan" has the wrong polarity: with the marker gate removed, prose would
    manufacture a reference for `distil` and the assertion would still pass -
    which is precisely the failure it is meant to catch.
    """
    assert _tokens_of("no language model is configured, so there is no teacher "
                      "to distil from. ") == set(), (
        "a sentence naming a function is being read as a reference to it")

    embedded = ("\nimport ctypes, errno\n"
                "from shani_chronoa.sandbox import seccomp\n"
                "rc, err = seccomp.prctl_set_seccomp_attempt()\n")
    tokens = _tokens_of(embedded)

    assert "prctl_set_seccomp_attempt" in tokens, tokens
    assert "seccomp" in tokens, tokens

    # A definition inside an embedded script is not a reference to it - which is
    # what let `log_message` hide behind seven test-local definitions.
    only_defining = "class H:\n    def log_message(self, fmt):\n        pass\n"

    assert "log_message" not in _tokens_of(only_defining), \
        _tokens_of(only_defining)

    # ... but a name that is both defined and used still counts.
    both = "def log_message(self):\n    pass\nhandler.log_message = log_message\n"

    assert "log_message" in _tokens_of(both), _tokens_of(both)


def test_the_scan_reaches_the_rest_of_the_repository():
    """`distil()` is called from `tools/distill_run.py` - outside `usr/` and
    `tests/`, which is where the hand-run version of this audit kept stopping."""
    assert "distil" in _referenced_names(), (
        "the reference pass no longer sees tools/, so it would report `distil` - "
        "which distil_run.py calls - as uncalled")


def test_the_scan_can_still_see_an_intra_module_call():
    """Blind spot 3. `modelfmt.role_for` is called only by `modelfmt.catalog`, in
    its own module - which is why subtracting the defining file reported 267
    orphans instead of 26."""
    orphans = _orphans()

    for name in ("role_for", "backend_for", "dominant_tone", "read_iq_wav"):
        assert name not in orphans, (
            f"{name} is referenced only from its own module, and the scan is "
            f"discarding intra-module references again")

def test_no_private_helper_is_called_by_nothing():
    """**The gap this audit found.** Probing what the public check deliberately
    skips: 0 unreferenced module constants of 3557, and 14 unreferenced private
    functions of 3652. The first of those 14 was the bwrap/seccomp decision whose
    answer was carried by indentation alone."""
    orphans = _private_orphans()
    unexpected = sorted(set(orphans) - set(PRIVATE_ALLOWED))

    assert not unexpected, (
        f"{len(unexpected)} private callable(s) that nothing references:\n"
        + "\n".join(f"  {name}  ({', '.join(orphans[name])})" for name in unexpected)
        + "\n\nEither wire it, delete it, or add it to PRIVATE_ALLOWED with a "
          "reason.")


def test_every_private_allowlist_entry_carries_a_reason():
    without = sorted(name for name, why in PRIVATE_ALLOWED.items()
                     if not why.strip())

    assert not without, f"PRIVATE_ALLOWED entries with no reason: {without}"


def test_both_audits_actually_find_their_allowlisted_orphans():
    """**The control for a scan that goes blind.**

    `test_no_private_helper_is_called_by_nothing` computes `unexpected` inside the
    test and asserts it is empty - so neutering that computation to `[]` made the
    test pass. Worse, the realistic version of the same failure is a *scan* that
    quietly returns `{}`: the test would then be asserting nothing at all and read
    as green.

    So both scans are required to find the orphans they are known to contain. If
    the reference pass loses a directory, the definition walk stops matching, or
    the dunder filter changes shape, this fails instead of passing quietly.

    **And when the allowlists emptied, the first half of this control became
    trivially true** - `set(ALLOWED) - set(public)` is `set()` over an empty
    `ALLOWED`, so the blindness this test exists to catch would have arrived in
    the exact edit that removed it. The declared known-orphan population is now
    the derived toolbox, so that is what the scan is required to find.
    """
    public = _orphans()

    # **Not the six the scan provably cannot see.** `_BLIND_TOOLBOX` names them,
    # and `test_the_class_audit_cannot_see_the_english_word_classes` asserts both
    # halves of that fact. Demanding the scan find them here would assert the blind
    # spot away - which is the same error this control exists to catch, aimed at a
    # control instead of at the code.
    # **The class half is proved by planting one too.** It used to demand that the
    # scan find every toolbox class minus the six it cannot see - which meant
    # maintaining a second hand-kept set describing a blind spot, to prove a
    # control that a planted class proves outright. A class dropped into a
    # scratch package and referenced nowhere must come back; a blind scan answers
    # the same empty dict for the tree and for the plant.
    assert _planted_class_orphan_is_found(), (
        "the class scan did not find a public class planted in a scratch package "
        "and referenced nowhere, so an empty result proves nothing")

    # And both are non-trivial. **The floor is a claim about a file, not a number.
    # It used to be a bare `20`, calibrated while the three allowlists still held
    # their 26 names, and it failed by one the day the last allowlist entry was
    # wired. What it was actually guarding against is "found nothing at all", and
    # what this tree really contains is a documented toolbox: **one orphaned
    # method per module-level toolbox class**. Derived from that population rather
    # than fitted to the audit's own output, and it fails if the reference pass
    # loses `learning.py`.
    # **The floor counts only what the scan can see.** It is `_toolbox_roots()` -
    # 11 module-level sections - and 6 of those are the English-word blind spot
    # `_BLIND_TOOLBOX` names, which the scan never reports at all. Asking one
    # orphan of each demanded 11 of a population that could only ever produce 4,
    # so the floor was the *visible* classes; the number used here is the honest
    # form of "this file is still being read".
    toolbox = _toolbox_roots()
    found = {n for n, where in public.items() if any("learning.py" in w for w in where)}

    # `found` is already a subset of `public` - every name in it came from the
    # orphans dict filtered to that file, so the intersection would be `found`.
    assert len(found) >= len(toolbox), (
        f"the public scan found {len(found)} orphan(s) in learning.py "
        f"against {len(toolbox)} module-level toolbox classes - it has stopped "
        f"reading that file")
    # **And the private half is proved by planting one.** It used to carry a bare
    # `len(private) >= 10`, which was true only while 11 entries sat in
    # `PRIVATE_ALLOWED`. They were wired this batch - `_numeric`, `_lsusb`,
    # `_remote`, `_sidecars` and the rest - so the private scan now finds nothing,
    # and a count floor cannot tell that from a scan that stopped reading the tree.
    #
    # So the floor is a plant: a private helper dropped into a scratch package and
    # referenced nowhere must come back. A blind scan reports the same empty dict
    # for the tree and for the plant, which is what makes this the half that bites.
    assert _planted_private_orphan_is_found(), (
        "the private scan did not find a private helper planted in a scratch "
        "package and referenced nowhere, so an empty result proves nothing")


def test_the_private_allowlist_is_not_stale():
    orphans = _private_orphans()
    gone = sorted(set(PRIVATE_ALLOWED) - set(orphans))

    assert not gone, (
        f"PRIVATE_ALLOWED names that are referenced again: {gone}. Remove them.")

def test_no_public_class_is_referenced_by_nothing():
    """**The class the speech-gate fix came from.** 22 unreferenced of 380, 19 of
    them in `learning.py` - so the list is small, and one of the three real
    candidates (`speech_gate.Busy`) was a docstring promising a raise that nothing
    performed."""
    orphans = _class_orphans()
    unexpected = sorted(set(orphans) - set(CLASS_ALLOWED)
                        - _toolbox_class_names())

    assert not unexpected, (
        f"{len(unexpected)} public class(es) nothing references:\n"
        + "\n".join(f"  {name}  ({', '.join(orphans[name])})"
                    for name in unexpected))


def test_every_class_allowlist_entry_carries_a_reason():
    without = sorted(name for name, why in CLASS_ALLOWED.items() if not why.strip())

    assert not without, f"CLASS_ALLOWED entries with no reason: {without}"


def test_the_class_allowlist_is_not_stale():
    orphans = _class_orphans()
    gone = sorted(set(CLASS_ALLOWED) - set(orphans))

    assert not gone, f"CLASS_ALLOWED names referenced again: {gone}. Remove them."


def test_the_toolbox_exclusion_cannot_reach_another_file():
    """**The control that keeps the toolbox exclusion from being an allowlist.**

    A name list would have been the simplest way to finish this task and would
    have re-created exactly what the three emptied allowlists used to be: a list
    that only grows, absorbing whatever is near it. The exclusion is derived from
    the source instead, and the two properties that make that difference real are
    asserted here rather than promised:

    1. **It can only ever point at `learning.py`.** A definition site anywhere
       else is never subtracted, so an orphan in any other file still fails. A
       wide glob or a relaxed class test would let this exclusion spread, and this
       is the assertion that names it.
    2. **It is not empty.** An empty derivation would make the public audit pass
       while orphans sit in `learning.py`, which is the same blindness as a scan
       that finds nothing - so the count is asserted, not assumed.
    """
    locations = _toolbox_locations()

    assert locations, (
        "the toolbox exclusion derived nothing at all - the public audit would "
        "now pass while orphans sit in learning.py")

    # **And the constant it shares has to name classes that exist.** An entry in a
    # hand-kept set that no longer parses is the stale-entry failure: the set would
    # keep asserting a blind spot about a class that was wired or deleted.
    import ast as _ast
    declared = set()
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts or path.name != "learning.py":
            continue
        try:
            for node in _ast.walk(_ast.parse(path.read_text(errors="replace"))):
                if isinstance(node, _ast.ClassDef):
                    declared.add(node.name)
        except (SyntaxError, UnicodeDecodeError):
            continue
    missing = sorted(_BLIND_TOOLBOX - declared)
    assert not missing, (
        f"_BLIND_TOOLBOX names class(es) learning.py does not declare: {missing} - "
        f"a stale entry in a blind-spot list is a blind spot that cannot close")

    other = {loc for loc in locations if "learning.py" not in loc}
    assert not other, (
        f"the toolbox exclusion reached outside learning.py: {sorted(other)} - "
        f"it has become an allowlist and will silently absorb orphans elsewhere")

    # **And the assertion above is backed by a plant, because measured it cannot
    # fail on this tree.** Every file outside `learning.py` that nests a class
    # inside a toolbox-named class is under `cache/` - `tqdm`'s `EMA`, `torch`'s
    # `SGD`, `sympy`'s `Gaussian` - and `_SKIP` drops all of it. So `other` is
    # empty by construction: when the derivation was widened to every `.py` in the
    # package it walked the whole tree, took **453 s against 170**, and still
    # reported only `learning.py` locations. Slower and just as green is the shape
    # of a control that has stopped being able to fail.
    assert _planted_toolbox_location_stays_in_its_module(), (
        "a public callable declared in a module that is not the toolbox module was "
        "reported by the toolbox exclusion - the derivation is no longer confined "
        "to learning.py, so an orphan anywhere else can be absorbed by it")


def test_the_toolbox_exclusion_covers_the_documented_classes():
    """The population it names is the one the derived toolbox documents, so the two
    cannot drift apart: the class exclusion and the callable exclusion are two
    views of one deliberately-kept toolbox."""
    per_class = _toolbox_methods_per_class()

    assert per_class, "no declared toolbox class contributed an excluded callable"
    empty = sorted(name for name, n in per_class.items() if n == 0)
    assert not empty, (
        f"declared toolbox classes contributing no excluded callable: {empty} - "
        f"the class exclusion and the callable derivation have drifted apart")


def test_the_toolbox_exclusion_covers_no_seam_reachable_root():
    """`decision` is the class the roots filter exists to keep out.

    `_toolbox_roots()` takes every lowercase module-level class in `learning.py`
    and drops the ones `_seam_reaches` finds a caller for. With the filter
    removed, `decision` joins the toolbox - and with it its own `RewardModel` and
    `ActionSelector`. All three are live and none is an orphan today, so the
    audit's result is identical either way. That is why the mutation removing the
    filter **survived** when it was run, and it is recorded rather than shrugged
    at.

    **A control that only bites in the future is a control that cannot fail now.**
    So this one bites now: it asserts the roots against a derivation that does not
    use the filter - every lowercase module-level class, minus exactly the ones a
    seam reaches - and then asserts the whole box holds no seam-reachable class
    either. A filter that is deleted, or a box that starts keeping a class
    something outside this file reaches, changes the answer here.

    The second half is the one that keeps the exclusion honest as it is used:
    `estimators.py` re-exports `EMA`, `KMeans` and `KNN` out of the toolbox (the
    split-modules commit), so three classes inside the sections are live - and a
    box holding them has promised not to report a class with a caller.
    """
    roots = _toolbox_roots()
    all_names = set()
    for path in sorted(PKG.rglob(_TOOLBOX_MODULE)):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        all_names |= {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert roots, "no toolbox root derived at all"

    lower = {n for n in all_names if n[:1].islower()}
    reached = {n for n in lower if _seam_reaches(n)}
    assert lower - roots == reached, (
        "the roots filter does not keep exactly the lowercase class with no seam: "
        f"expected it to drop {sorted(reached)}, it dropped "
        f"{sorted(lower - roots)}")

    # `decision` is the live one: it has a caller and is not a toolbox section.
    assert "decision" not in roots, (
        "decision is a toolbox root - it is live, a seam reaches it")

    # And the box as a whole holds nothing a seam reaches, nested or not.
    box = _toolbox_class_names()
    live = sorted(n for n in box if _seam_reaches(n))
    assert not live, (
        f"the toolbox exclusion covers class(es) a real seam reaches: {live} - "
        f"so live code is inside the box, which is the over-claiming the emptied "
        f"allowlists were made of arrived at by another route")


def test_the_class_audit_cannot_see_the_english_word_classes():
    """Six of the eleven toolbox classes are invisible to the class audit, and not
    for a reason anybody would guess.

    **`memory`, `online`, `stats`, `regression`, `clustering` and `rl` are ordinary
    English words.** The reference pass counts a name as used when it appears
    anywhere in the tree - including as a local variable in another module - so
    those six are never reported as unreferenced however little refers to them.
    That is the `price`/`collapse` trap inside the *scanner* rather than inside the
    code, and it means a clean class audit is not a covered toolbox.

    The assertion is two-sided, and the second half uses an *independent*
    instrument. "The audit must not see them" alone is satisfiable by a scanner
    that has stopped looking altogether; what makes it real is that these six are
    unwired by a route the blind scanner does not use - **no real seam** reaches
    them. `learning.stats`, `from shani_chronoa.learning import stats`: if any
    exists, the class is live and the blind spot was a caller nobody counted.
    """
    orphans = _class_orphans()

    hidden = _BLIND_TOOLBOX

    for name in sorted(hidden):
        assert name not in orphans, (
            f"{name} is now reported by the class audit - either it was wired or "
            f"the English-word blind spot closed; this control has to be re-read")
        assert not _seam_reaches(name), (
            f"a real seam reaches {name}, so the class is live and the blind "
            f"spot was a caller nobody counted - this control has to be re-read")
    assert hidden <= _toolbox_class_names(), (
        f"toolbox classes the audit cannot see are missing from the derived "
        f"toolbox: {sorted(hidden - _toolbox_class_names())}")


def test_the_class_audit_can_still_see_learning_py():
    """`learning.py` is excluded by name, so the exclusion is asserted rather than
    assumed - a rename would otherwise quietly stop excluding it, or start
    excluding something else."""
    orphans = _class_orphans()
    in_learning = {n for n, where in orphans.items()
                   if any("learning.py" in w for w in where)}

    assert in_learning, (
        "no unreferenced class in learning.py at all - either the concurrent "
        "session wired them, or this audit has stopped reading that file")

def test_no_property_member_is_unread():
    """**The class that hid two verdicts.** `guardrail.should_reask` and
    `should_stop` were both declared, both reachable in principle, and both read
    by nobody - because a property is read as an attribute, so nothing that counts
    `name(` ever saw them.

    All 117 are read today, so this asserts an empty allowlist rather than
    tolerating a list.
    """
    orphans = _property_orphans()
    unexpected = sorted(set(orphans) - set(PROPERTY_ALLOWED))

    assert not unexpected, (
        f"{len(unexpected)} property member(s) nothing reads:\n"
        + "\n".join(f"  {name}  ({', '.join(orphans[name])})" for name in unexpected))


def test_the_property_scan_finds_a_property_nothing_reads(tmp_path, monkeypatch):
    """**The control that makes an empty result mean something - and it has to
    drive the scan itself.**

    The first version of this parsed `guardrail.py` directly and only compared
    its findings against the scan's. **Three mutations survived that**: the scan
    skipping `guardrail.py` entirely, the scan returning `{}`, and the allowlist
    gaining an entry the scan could not justify - all three passed, because
    nothing here asked the scan to find anything. Reading a known-good file by
    hand proves the file has properties, not that the scan looks.

    So this builds a package with a property nothing references, points `PKG` at
    it, and requires the scan to report that property by name. A scan that is
    blind, or that skips a file, or that returns nothing, fails here.
    """
    module = tmp_path / "shani_chronoa" / "probe.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "class Probe:\n"
        "    @property\n"
        "    def a_property_nothing_reads(self):\n"
        "        return 1\n"
        "\n"
        "    @property\n"
        "    def also_unread(self):\n"
        "        return 2\n",
        encoding="utf-8")
    # **A second module, named after the file the first version of this control
    # read by hand.** A scan carrying `if path.name == "guardrail.py": continue`
    # passes everything else here, because nothing else mentions that name - so
    # the file that was the observed blind spot has to be the one the scan is
    # asked about. Two modules, and the scan has to see both.
    named = tmp_path / "shani_chronoa" / "guardrail.py"
    named.write_text(
        "class Probe:\n"
        "    @property\n"
        "    def behind_the_skipped_name(self):\n"
        "        return 3\n",
        encoding="utf-8")
    # `_defined_properties` formats each finding as `path.relative_to(ROOT)`, so
    # ROOT has to move with PKG - pointing only PKG at a temp tree raises
    # ValueError rather than reporting anything.
    monkeypatch.setattr(sys.modules[__name__], "PKG", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)

    # An empty reference map: nothing is read, so both properties are orphans.
    found = _defined_properties({})

    assert found, (
        "the property scan found nothing in a package that declares two unread "
        "properties - it is blind, and an empty result would read as clean")
    assert "a_property_nothing_reads" in found, (
        "the scan walked the package but missed a plain @property: %s" % sorted(found))
    assert "also_unread" in found, sorted(found)
    assert "behind_the_skipped_name" in found, (
        "the scan skipped a module by name - %s" % sorted(found))


def test_the_property_scan_skips_nothing_it_should_see(tmp_path, monkeypatch):
    """The negative control for the one above: a property that *is* referenced
    must not be reported.

    Without this, "the scan finds a property" would also be satisfied by a scan
    that reports every property it sees, unread or not - which would make the
    real test vacuous in the other direction.
    """
    module = tmp_path / "shani_chronoa" / "probe.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "class Probe:\n"
        "    @property\n"
        "    def read_by_someone(self):\n"
        "        return 1\n",
        encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "PKG", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)

    assert _defined_properties({"read_by_someone": ["probe.py"]}) == {}, (
        "a property that is referenced was reported as unread")
    assert _defined_properties({}) , "the control above needs the scan to see it"


def test_every_property_allowlist_entry_is_still_needed():
    """**An allowlist entry nothing reports is a lie about why.**

    `PROPERTY_ALLOWED` is empty today, so this passes - and it is the test that
    stops it from becoming a place to park names. An entry added there silences
    the real scan without any check that there was ever a finding, which is the
    failure a reason-required list still allows.
    """
    orphans = _property_orphans()
    stale = sorted(name for name in PROPERTY_ALLOWED if name not in orphans)

    assert not stale, (
        "these PROPERTY_ALLOWED entries correspond to nothing the scan reports, "
        "so they suppress nothing and only misdescribe the tree: %s" % stale)

def test_every_property_allowlist_entry_carries_a_reason():
    without = sorted(name for name, why in PROPERTY_ALLOWED.items()
                     if not why.strip())

    assert not without, f"PROPERTY_ALLOWED entries with no reason: {without}"

def test_the_framework_dispatch_exemption_names_only_real_handlers():
    """**The exemption cannot absorb a genuine orphan.**

    `_FRAMEWORK_DISPATCH` says "the framework calls these by name", which is a
    claim that can rot: a name could be added that no `BaseHTTPRequestHandler`
    subclass implements, and then the exemption would silence a real orphan.

    So every name in it must exist on a class that inherits
    `BaseHTTPRequestHandler` - the class whose `handle_one_request` does the
    `getattr(self, "do_" + command)` that makes the exemption true at all.
    """
    handlers = {}
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            bases = {b.id if isinstance(b, ast.Name)
                     else getattr(b, "attr", "") for b in cls.bases}
            if not any("HTTPRequestHandler" in b for b in bases):
                continue
            for member in cls.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    handlers.setdefault(member.name, []).append(
                        "%s:%d" % (path.relative_to(ROOT), member.lineno))

    assert handlers, (
        "no class inheriting BaseHTTPRequestHandler was found, so the exemption "
        "cannot be checked - and it may no longer be true")
    for name in _FRAMEWORK_DISPATCH:
        assert name in handlers, (
            "%s is exempted as framework-dispatched but no "
            "BaseHTTPRequestHandler subclass defines it, so the exemption is "
            "hiding a genuine orphan" % name)


def test_the_exempted_handlers_are_really_resolved_by_the_framework():
    """The exemption rests on one stdlib line, so that line is asserted.

    `BaseHTTPRequestHandler.handle_one_request` builds `mname = "do_" +
    self.command` and then `getattr(self, mname)`. If a Python version ever stops
    doing that - or a subclass is expected to define `handle_one_request` itself -
    every name in `_FRAMEWORK_DISPATCH` becomes an orphan and the guard would go
    quietly blind instead of going red.
    """
    import http.server
    import inspect

    source = inspect.getsource(http.server.BaseHTTPRequestHandler)

    assert "'do_' + self.command" in source or '"do_" + self.command' in source, (
        'BaseHTTPRequestHandler no longer resolves a handler as "do_" + command, '
        "so do_GET/do_POST are not framework-dispatched and the exemption is no "
        "longer true")
    assert "getattr(self, mname)" in source, (
        "the resolved handler is no longer fetched by name")
    assert "self.log_message(" in source, (
        "BaseHTTPRequestHandler no longer calls log_message, so the exemption "
        "for it is no longer true")
