"""A surface method that nothing reaches is a panel that does nothing.

**This is the class that just cost two panels in one feature.** `_tick` in
`meetings.py` shipped with a docstring about not looking hung and was never
scheduled, so the recording clock read its opening sentence and then never moved.
`_recording_possible` in the same file read "Whether the Record button can act,
and the sentence if it cannot" and was never called, so a greyed Record button
that names the setting sat beside a live one that refused you on click. Both were
written, documented, and reachable by nothing.

The scan is **text-based on purpose.** An AST-only scan finds neither
`phone_link.show_devices` - which `phone_link.py:370` dispatches through
`getattr` with the name in a string tuple - nor `_refresh_status`, whose
`page.show_devices(...)` call lives inside a subprocess harness held as a string
in `tests/test_phone_link_panel.py`. Both of those are live seams, and a scanner
that calls them dead is a scanner that gets deleted. So a name mentioned anywhere
in the package or the tests, as a word, counts as reachable.

Measured on the fixed tree: 309 surface methods across 35 modules, none
unreachable - `common.rows_of`, recorded below. Those counts are asserted too,
because a scan over a path that does not
exist examines zero modules, reports zero orphans, and passes forever - the
negative-control failure this repo has recorded more than once.
"""

from __future__ import annotations

import ast
import collections
import functools
import io
import pathlib
import re
import tokenize

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
PKG = REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
TESTS = REPO / "tests"


def surface_modules(pkg: pathlib.Path = PKG):
    surfaces = pkg / "gui" / "surfaces"
    if not surfaces.is_dir():
        return []
    return sorted(surfaces.glob("*.py"))


@functools.lru_cache(maxsize=None)
def every_source(pkg: pathlib.Path = PKG, tests: pathlib.Path = TESTS):
    """Everything a method name could be used by, as one string.

    Cached: the first run read all ~400 files once per assertion and took 163
    seconds for five tests. A test that slow is a test that gets deselected.
    """
    files = list(sorted(pkg.rglob("*.py"))) if pkg.is_dir() else []
    files += list(sorted(tests.rglob("*.py"))) if tests.is_dir() else []
    return "\n".join(f.read_text() for f in files)


@functools.lru_cache(maxsize=None)
def _all_files(pkg: pathlib.Path = PKG, tests: pathlib.Path = TESTS):
    out = list(sorted(pkg.rglob("*.py"))) if pkg.is_dir() else []
    out += list(sorted(tests.rglob("*.py"))) if tests.is_dir() else []
    return tuple(out)


@functools.lru_cache(maxsize=None)
def _index(pkg: pathlib.Path = PKG, tests: pathlib.Path = TESTS):
    """Per file: the identifiers it uses, and the method names it defines.

    **A mention is not automatically a use, and this is the part the first
    version got wrong twice.** meetings' `_tick` was dead and the first version
    called it alive, because `_tick` is also the name of a live, scheduled
    method on a class in `gui/widgets.py` - three mentions in the tree, two of
    them something else's. Verified against the pre-fix tree: the scan caught
    `_recording_possible` and missed `_tick` for exactly that reason.

    So a file that *defines* a name is not evidence about anyone else's method of
    that name, and is excluded from the count of its uses.

    **And a mention in prose is not a use either.** A name named in a comment or
    a docstring reads as a caller, which is the mirror-image mistake: measured, a
    `TranscriptView` handler is named exactly once outside its own definition - in
    a `window.py` docstring explaining a pattern - and a raw-text scan calls it
    live. It is dead. **Comments and docstrings are dropped here; every other
    string literal is kept**, because that is where the live seams actually are:
    `phone_link.py:370` dispatches `show_devices` through a `getattr` string
    tuple, `voice.py`'s `_FORWARDED` carries `speaking_row` into `setattr`, and
    `webkit_version` / `activity_text` are called from inside subprocess
    harnesses held as strings in two test files. Dropping strings - which is
    `tools/deadcode.py`'s deliberate rule - calls all four dead.

    **And this file excludes itself.** A reachability scan that lives inside the
    tree it measures will always find itself: naming a finding in a docstring
    puts the name in the source, `_all_files` reads it, and the dead thing reads
    as live. Measured **three times in this one file** - `set_suggestion_handler`
    named in the control, then in the control's expected value, then in
    `_index`'s own docstring; and `common.rows_of` named four times when the
    module-level population was added. Each time the assertion it was written to
    make passed for the wrong reason, which is worse than failing.

    So the fix is structural rather than per-name: **this file is not in the
    index.** Composing names at runtime was the first attempt and it was the
    wrong fix - it made the docstrings unreadable to defend against something the
    exclusion handles for every future name at once.
    """
    skip = {pathlib.Path(__file__).resolve()}
    per_file = []
    for f in _all_files(pkg, tests):
        if f.resolve() in skip:
            continue
        try:
            text = f.read_text()
        except (OSError, ValueError):
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        defined = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defined.add(item.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
        per_file.append((f, collections.Counter(_code_words(text, tree)), defined,
                         _definition_lines(text)))
    return tuple(per_file)


def _docstring_positions(tree: ast.AST) -> set:
    """(lineno, col_offset) of every module, class and function docstring.

    A docstring is the first statement of one of those bodies and is prose by
    construction, so a name appearing in one is documentation and not a caller.
    """
    spots = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    spots.add((body[0].lineno, body[0].col_offset))
    return spots


def _code_words(text: str, tree: ast.AST):
    """Every identifier in the file's **code**: comments and docstrings dropped,
    all other string literals kept.

    Tokenised rather than regexed over the raw text, because the raw text is
    where the prose lives. Keeping non-docstring strings is load-bearing - see
    `_index`'s docstring for the four names that would otherwise read as dead.
    """
    prose = _docstring_positions(tree)
    words = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and (tok.start[0], tok.start[1]) in prose:
                continue
            words.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # An unparseable file is not evidence either way; its raw words are the
        # only thing available and are counted, which fails toward "live".
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
    return words


_DEF_LINE = re.compile(
    r"^\s*(?:async\s+)?(?:def\s+(\w+)\s*\(|class\s+(\w+))", re.MULTILINE)


def _definition_lines(text: str) -> collections.Counter:
    """Names whose **own declaration line** carries them - `def` and `class` alike.

    The `class` half is load-bearing, and its absence made a whole gate
    unreachable. `_is_used` subtracts a name's own definition lines from its
    total, so a `class Foo:` line that was not counted here read as a *use* of
    `Foo` - the declaration was its own evidence of being live. Measured: a class
    named by nothing anywhere reported clean, so the class half of
    `unreachable_gui_classes_and_vfuncs` could not fail at all; it was 121
    vacuous passes.

    Adding it leaves the method gates alone. A class and a method sharing a
    name is the case to check: `defs` counts both declaration lines and `uses`
    counts both of them too, so the subtraction removes exactly the declarations
    and any real reference still counts.
    """
    return collections.Counter(m.group(1) or m.group(2)
                               for m in _DEF_LINE.finditer(text))


def _is_used(name: str, own: pathlib.Path, index) -> bool:
    """Anything outside the definition line, counted once over the whole tree.

    Precomputed per-file counters keep it one lookup per method instead of a scan
    per method: the first version ran 309 regexes over the same 13 MB and took
    97 seconds for this question, which is why the whole suite took 100.
    """
    total = 0
    for f, uses, defined, defs in index:
        if f != own and name in defined:
            continue                    # a different method of the same name
        total += uses.get(name, 0)
        if f == own:
            total -= defs.get(name, 0)  # its own `def` line
    return total > 0


@functools.lru_cache(maxsize=None)
def surface_methods(pkg: pathlib.Path = PKG):
    """(module, line, name, docstring) for every non-dunder method on a class
    defined in a surface module."""
    for path in surface_modules(pkg):
        for cls in [n for n in ast.walk(ast.parse(path.read_text()))
                    if isinstance(n, ast.ClassDef)]:
            for fn in [n for n in cls.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                if fn.name.startswith("__"):
                    continue
                yield path, fn.lineno, fn.name, (ast.get_docstring(fn) or "")


@functools.lru_cache(maxsize=None)
def surface_helpers(pkg: pathlib.Path = PKG):
    """(module, line, name, docstring) for every **module-level** public function
    in a surface module - the population the class scan above cannot see.

    **This was a real blind spot, and it hid a dead helper.** `common.rows_of`
    had no caller of any kind and no test, and the first version of this file
    judged only class methods, so it never appeared. Measured: 85 module-level
    public helpers across the 35 surface modules, exactly one unreachable.

    `build` in every module is dispatched by *string* - the surfaces registry
    looks it up by name - so it has to still count as reachable. It does: 769
    mentions against 43 definition lines. That is the same string-dispatch case
    `show_devices` and `speaking_row` established, and it is why this scan reads
    strings at all.
    """
    for path in surface_modules(pkg):
        for fn in [n for n in ast.parse(path.read_text()).body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if fn.name.startswith("_"):
                continue
            yield path, fn.lineno, fn.name, (ast.get_docstring(fn) or "")


def unreachable_methods(pkg: pathlib.Path = PKG, tests: pathlib.Path = TESTS):
    """Names whose only mention in the whole tree is their own definition."""
    index = _index(pkg, tests)
    found = []
    for path, lineno, name, _doc in list(surface_methods(pkg)) + list(surface_helpers(pkg)):
        if not _is_used(name, path, index):
            found.append(f"{path.name}:{lineno} {name}()")
    return found


def gui_modules(pkg: pathlib.Path = PKG):
    """`gui/` proper - the modules beside `surfaces/`, not inside it.

    **`glob("gui/*.py")` is non-recursive on purpose**, so `surfaces/` is not
    double-scanned by two gates that could then disagree. The two populations
    are disjoint by construction rather than by a filter somebody remembers.
    """
    gui = pkg / "gui"
    if not gui.is_dir():
        return []
    return sorted(gui.glob("*.py"))


@functools.lru_cache(maxsize=None)
def gui_names(pkg: pathlib.Path = PKG):
    """(module, line, name) for every class method and module-level public
    function defined in `gui/` outside `surfaces/`."""
    for path in gui_modules(pkg):
        tree = ast.parse(path.read_text())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in [n for n in cls.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                if not fn.name.startswith("__"):
                    yield path, fn.lineno, fn.name
        for fn in [n for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if not fn.name.startswith("_"):
                yield path, fn.lineno, fn.name


def unreachable_gui_names(pkg: pathlib.Path = PKG, tests: pathlib.Path = TESTS):
    """The same scan, over `gui/` outside `surfaces/`.

    **This population had no gate at all**, which is why the vestigial pair in
    `window.py` survived: a `_regenerate` field written by a live setter and read
    by a method nothing called, plus the setter-shaped method beside it. Neither
    is a surface, and a gate that stopped at `gui/surfaces/` could not see either.

    **It is not extended to the whole shipped tree, and the measurement is why.**
    Run over every module outside `gui/`, the same scan reports 27 orphans, and
    most are a documented false-positive class: `triggers/rules.py`'s `fire_all`
    is called by exactly one caller, a `usr/bin/` launcher, which this index does
    not walk; `ibus_input.py`'s `do_*` are GObject vfuncs the framework
    dispatches by name. A gate over a population that cries wolf gets deleted,
    and this one was written because its predecessors were not.
    """
    index = _index(pkg, tests)
    return [f"{path.name}:{lineno} {name}()"
            for path, lineno, name in gui_names(pkg)
            if not _is_used(name, path, index)]


# ---------------------------------------------------------------------------
# The three populations the gates above skip by construction.
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _imported_names(pkg: pathlib.Path = PKG, tests: pathlib.Path = TESTS):
    """Per file, the names it imports **by name** (`from x import _y`).

    A fourth question neither existing index answers. Parsed with `ast` and
    nothing else: re-running `_code_words` over the whole tree is the 97 seconds
    this file spent on exactly that in its first version, and a question about
    import statements does not need a tokeniser.
    """
    rows = []
    for path in _all_files(pkg, tests):
        imported = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.asname or alias.name)
        rows.append((path.resolve(), frozenset(imported)))
    return rows


def _private_is_used(name: str, own: pathlib.Path, index, imports) -> bool:
    """**Per module**: called where it is defined, or imported by name elsewhere.

    This is the rule `_is_used` cannot express, and it is why three orphans
    survived a gate that reported the tree clean:

    - `android._in_thread` was masked by **phone.py's identically-named twin**
      plus a test's `panel._in_thread` monkeypatch;
    - `wearables._gatt_read` was masked by a test calling `w._gatt_read(...)`,
      an attribute access on an object that has nothing to do with it.

    Both are the same mistake in a new costume: `_is_used` counts the bare word
    wherever it appears, and a module-private name is private **to its own
    module**, so a qualified reference through any other object is not a call of
    it. Counting those is how a helper kept alive by nothing but its own test
    reads as live - and one of the two above was exactly that.
    """
    for f, uses, _defined, defs in index:
        if f == own and uses.get(name, 0) - defs.get(name, 0) > 0:
            return True
    for f, names in imports:
        if f != own and name in names:
            return True
    return False


@functools.lru_cache(maxsize=None)
def gui_private_functions(pkg: pathlib.Path = PKG):
    """(module, line, name) for every **module-private module-level function**
    in `gui/` and `gui/surfaces/`.

    Both existing gates filter these out: `gui_names` skips a module-level
    function whose name starts with `_`, and `surface_helpers` does the same.
    So the population was invisible in both directions - the 592 functions here
    had never been scanned, and they hold a dead builder written the same way
    `_tick` was.
    """
    gui = pkg / "gui"
    if not gui.is_dir():
        return []
    paths = sorted(gui.glob("*.py")) + sorted((gui / "surfaces").glob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text())
        for fn in tree.body:
            if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and fn.name.startswith("_")
                    and not fn.name.startswith("__")):
                yield path, fn.lineno, fn.name


@functools.lru_cache(maxsize=None)
def gui_classes(pkg: pathlib.Path = PKG):
    """(module, line, name) for every **class** in `gui/` and `gui/surfaces/`.

    A population no gate covered: only a class's *methods* were ever scanned, so
    a class nothing named read as clean. 121 classes over the two directories.
    """
    gui = pkg / "gui"
    if not gui.is_dir():
        return []
    paths = sorted(gui.glob("*.py")) + sorted((gui / "surfaces").glob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            yield path, cls.lineno, cls.name


@functools.lru_cache(maxsize=None)
def gui_vfuncs(pkg: pathlib.Path = PKG):
    """(module, line, vfunc name, **owning class**) for every `do_*` in
    `gui/` and `gui/surfaces/`.

    The owning class is carried because it is the only thing a reachability rule
    can soundly ask about. A `do_*` is dispatched by the framework *by name*, so
    no source-level caller exists and none is expected - which is why this
    population needs its own rule rather than `_is_used`. See
    `unreachable_gui_vfuncs` for the four oracles measured and refused.

    Eight vfuncs across the two directories, and `do_snapshot` alone is six of
    them - all six in `charts.py`. That concentration is what makes a bare-name
    rule look like it works.
    """
    gui = pkg / "gui"
    if not gui.is_dir():
        return []
    paths = sorted(gui.glob("*.py")) + sorted((gui / "surfaces").glob("*.py"))
    for path in paths:
        tree = ast.parse(path.read_text())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and fn.name.startswith("do_")):
                    yield path, fn.lineno, fn.name, cls.name


def unreachable_gui_private_functions(pkg: pathlib.Path = PKG,
                                      tests: pathlib.Path = TESTS):
    index = _index(pkg, tests)
    imports = _imported_names(pkg, tests)
    return [f"{path.name}:{lineno} {name}()"
            for path, lineno, name in gui_private_functions(pkg)
            if not _private_is_used(name, path, index, imports)]


def unreachable_gui_classes(pkg: pathlib.Path = PKG,
                           tests: pathlib.Path = TESTS):
    index = _index(pkg, tests)
    return [f"{path.name}:{lineno} {name}"
            for path, lineno, name in gui_classes(pkg)
            if not _is_used(name, path, index)]


def unreachable_gui_vfuncs(pkg: pathlib.Path = PKG,
                           tests: pathlib.Path = TESTS):
    """A `do_*` is reachable **iff the class that owns it is**.

    **Not** "iff something mentions its name", which is what this gate did
    before, and the reason it could not fail correctly in either direction. Two
    measured facts make the bare-name rule wrong rather than merely loose:

    - **A vfunc has no source-level caller and is not supposed to.** GObject
      dispatches `do_snapshot` by name out of the class struct, so the only
      honest question is whether the framework *knows* that name - which is a
      question about GTK, not about our tree.
    - **On this tree the bare-name rule answered right by collision.** All six
      `do_snapshot` in `charts.py` are live, and exactly **one** mention of the
      name exists outside a `def` line: `automation_overlay.py` has a method of
      its own with that name. That single mention carried all six. Delete it and
      the gate reports six live vfuncs as dead.

    **Four oracles for "does the framework dispatch this name" were measured and
    all four are unsound**, so none of them is the rule:

    | instrument | why it cannot be it |
    |---|---|
    | `hasattr` over base classes | `do_button_press_event` is real GTK and is absent from every base tried, including `Gtk.EventController` - it would cry wolf on a legitimate vfunc |
    | the `.typelib` string blob | strings are deduped: `size-allocate` is a real vfunc and **absent** from the blob, so a missing string means nothing |
    | `GIRepository` declared methods | non-introspectable vfuncs are not in it either - of the same probe list only `measure` was declared |
    | PyGObject instance attributes | an unmatched `do_*` is kept as a plain Python method, so `hasattr` is True for a typo'd override exactly as for a real one |

    What is left is **sound in the direction that matters**: a vfunc whose class
    is never named can never be dispatched, so the class gate above is a strict
    implication and this one can only under-report. It cannot cry wolf, and that
    asymmetry is the point - a gate that cries wolf gets deleted.

    The defect it *does* catch is a `do_` on a class nothing reaches, which is
    the `_tick` shape in a widget. It does **not** catch a misspelled vfunc name
    - a `do_snapshpot` is dead code that would read as live - and no instrument
    available here can tell it apart from a real one. Recorded rather than
    pretended at.
    """
    index = _index(pkg, tests)
    return [f"{path.name}:{lineno} {name}"
            for path, lineno, name, owner in gui_vfuncs(pkg)
            if not _is_used(owner, path, index)]


class TestNoSurfaceMethodIsUnreachable:

    def test_the_scan_actually_looked_at_something(self):
        """The control that stops this test being vacuously green. A scan over a
        path that does not exist examines zero modules, reports zero orphans, and
        passes forever - exactly the negative-control failure this repo records.
        """
        modules = surface_modules()
        assert len(modules) > 10, (
            f"only {len(modules)} surface modules found under "
            f"{PKG / 'gui' / 'surfaces'} - a green result below would mean nothing")
        methods = list(surface_methods())
        helpers = list(surface_helpers())
        assert len(methods) > 100, f"only {len(methods)} surface methods examined"
        # **The module-level half is what hid `common.rows_of`.** Without this
        # assertion the scan could silently stop looking at module functions and
        # the test would stay green, which is the same defect one population down.
        assert len(helpers) > 50, (
            f"only {len(helpers)} module-level helpers examined - the scan is "
            f"not looking at the population `common.rows_of` was hidden in")
        assert len(methods) + len(helpers) > 350, (
            f"{len(methods)} methods + {len(helpers)} helpers is fewer than the "
            f"390 this scan measured; one of the two populations is being missed")

    def test_no_surface_method_is_reachable_by_nothing(self):
        orphans = unreachable_methods()
        assert not orphans, (
            "surface methods nothing reaches - each is a panel that does "
            "something its docstring says and no one calls:\n  "
            + "\n  ".join(orphans))

    def test_the_gui_scan_actually_looked_at_something(self):
        """The same non-vacuity control for the wider population.

        A `gui/` that moved, or a `glob` that stopped matching, examines zero
        modules, reports zero orphans and passes forever - which is how
        `common.rows_of` stayed hidden for as long as it did. The number is a
        floor well under the measured 517, so ordinary growth cannot trip it and
        a broken glob cannot reach it.
        """
        modules = gui_modules()
        assert len(modules) > 15, (
            f"only {len(modules)} gui/ modules found outside surfaces/ - a "
            f"green result below would mean nothing")
        names = list(gui_names())
        assert len(names) > 400, (
            f"only {len(names)} names examined in gui/ outside surfaces/; the "
            f"scan is not looking at the population the vestigial pair hid in")

    def test_no_gui_name_outside_surfaces_is_reachable_by_nothing(self):
        orphans = unreachable_gui_names()
        assert not orphans, (
            "names in gui/ outside surfaces/ that nothing reaches:\n  "
            + "\n  ".join(orphans))

    def test_a_prose_only_name_in_gui_would_be_reported(self, tmp_path):
        """**The live control for the wider gate.** Without it this is a second
        assertion that cannot fail, and a gate nobody can prove bites is a gate
        that gets deleted.

        The planted orphan is in `gui/` and **not** in `surfaces/`, so it can
        only be found by the scan this test is claiming to exercise.
        """
        body = (
            "class _Probe:\n"
            "    def _never_scheduled(self):\n"
            "        \"\"\"Documented, and reached by nothing.\"\"\"\n"
            "        return 1\n"
            "    def caller(self):\n"
            "        return self._live()\n"
            "    def _live(self):\n"
            "        return 1\n")
        pkg, tests = self._synthetic(tmp_path, body, sub="gui")
        tests.mkdir()
        (tests / "test_probe.py").write_text("# no reference to the dead method\n")
        found = unreachable_gui_names(pkg, tests)
        assert "probe.py:2 _never_scheduled()" in found, (
            f"a method nothing reaches was not reported in gui/ - this gate "
            f"cannot fail: {found}")
        assert not any("_live()" in f for f in found), (
            f"a method called in code was reported dead: {found}")

    def test_the_string_seams_in_gui_are_not_orphans(self):
        """The wider gate is only sound **because strings are kept**, and two of
        the four live seams sit inside this population - so this is a claim the
        gate depends on, not a detail.

        Both are called only from inside a subprocess harness held as a string in
        a test file. An AST scan cannot see either, and `tools/deadcode.py` -
        which deliberately does not read string constants - reports both dead.
        Asserted here because the failure mode if it ever changes is the gate
        going red on two *live* names, which reads like a product defect.
        """
        index = _index()
        for name in ("activity_text", "webkit_version"):
            total = sum(u.get(name, 0) - d.get(name, 0)
                        for _f, u, _defined, d in index)
            assert total > 0, (
                f"{name} is dispatched through a string and reads as dead - the "
                f"extractor is dropping the strings this gate depends on")

    # -- the controls -------------------------------------------------------

    def _synthetic(self, tmp_path, body: str, sub: str = "gui/surfaces"):
        """A tiny package-shaped tree, so a mutation never touches the real one.

        Editing a real surface file to prove this catches orphans, then reverting
        it, is a path operation in a tree another session is working in - and the
        exact thing that destroyed uncommitted work on 2026-10-11. So the control
        runs the same scan against a module written outside the repo.

        `sub` picks which directory the probe lands in, because the two gates
        scan different populations: `gui/surfaces/` for the surface gate, and
        `gui/` proper for the wider one. The default is the original, so the
        existing controls are untouched by the widening.
        """
        pkg = tmp_path / "shani_chronoa"
        surfaces = pkg / sub
        surfaces.mkdir(parents=True)
        (surfaces / "probe.py").write_text(body)
        return pkg, tmp_path / "tests"

    def test_a_method_named_only_at_its_definition_is_flagged(self, tmp_path):
        """**The live control, run on the real scan.** Before: `_tick` was named
        once in the whole tree - at `def _tick` - and this is that situation. The
        scan must go from clean to reporting it, or the assertion above cannot
        fail and this file is decoration.
        """
        body = (
            "class _Probe:\n"
            "    def _clock_never_scheduled(self):\n"
            "        \"\"\"Documented about not looking hung, scheduled by nothing.\"\"\"\n"
            "        return 1\n")
        pkg, tests = self._synthetic(tmp_path, body)
        tests.mkdir()
        (tests / "test_probe.py").write_text("# no reference to the dead method\n")
        found = unreachable_methods(pkg, tests)
        assert found == ["probe.py:2 _clock_never_scheduled()"], found

    def test_a_method_used_anywhere_is_not_flagged(self, tmp_path):
        """The other side: one extra mention anywhere clears it. Otherwise the
        control above would pass for the wrong reason - a scanner that calls
        everything dead also reports the dead one.

        **The assertion is on the specific method.** The first version of this
        control expected the whole synthetic module to be clean, and failed
        because `caller` - which really is called by nothing in that module - is
        also flagged. That is the scan working, not the scan breaking; the
        control was just too loose to tell the difference.
        """
        body = (
            "class _Probe:\n"
            "    def _live_method(self):\n"
            "        return 1\n"
            "    def caller(self):\n"
            "        return self._live_method()\n")
        pkg, tests = self._synthetic(tmp_path, body)
        tests.mkdir()
        (tests / "test_probe.py").write_text("# nothing\n")
        found = unreachable_methods(pkg, tests)
        assert "_live_method" not in " ".join(found), found
        # and the caller is still caught, because it is genuinely dead here
        assert any("caller" in f for f in found), found

    def test_a_name_used_only_by_a_test_string_counts_as_live(self, tmp_path):
        """**The reason the scan is text-based at all.** `phone_link.show_devices`
        is called only from inside a string-held subprocess harness; an AST scan
        sees a string, not a call, and nets a live seam. Named in a test's source
        is a use.
        """
        body = (
            "class _Probe:\n"
            "    def show_devices(self, entries):\n"
            "        return list(entries)\n")
        pkg, tests = self._synthetic(tmp_path, body)
        tests.mkdir()
        (tests / "test_probe.py").write_text(
            "HARNESS = r'''\n    page.show_devices([{'name': 'Pixel'}])\n'''\n")
        assert unreachable_methods(pkg, tests) == []

    def test_a_name_dispatched_through_getattr_counts_as_live(self, tmp_path):
        """`phone_link.py:370` does exactly this: the name sits in a **string
        tuple** handed to `getattr`. Unreachable to an attribute scanner, and
        live.

        Also `voice.py`'s `_FORWARDED`, which is what the background AST scan
        actually reported - `speaking_row` named once in a tuple of strings, and
        forwarded onto the built page by `setattr(page, name,
        getattr(content, name))`. The AST scan called it an orphan; it is one of
        the panel's public accessors. That false positive is the whole reason this
        scan reads text instead of attributes.

        **The tuple is literal, not a variable.** The first version of this
        control wrote `getattr(self, name)` - the name appears once, in its def,
        and the scan rightly flags it. The real code puts the *string* `"find"`
        in a tuple, which is a second mention and therefore a use.
        """
        body = (
            "_FORWARDED = (\"find\",)\n"
            "\n"
            "class _Probe:\n"
            "    def find(self):\n"
            "        return 1\n"
            "    def dispatch(self, name):\n"
            "        return getattr(self, name)()\n")
        pkg, tests = self._synthetic(tmp_path, body)
        tests.mkdir()
        (tests / "test_probe.py").write_text("# nothing\n")
        found = unreachable_methods(pkg, tests)
        assert "probe.py:4 find()" not in found and "probe.py:3 find()" not in found, found
        # dispatch really is dead in this module, and is still caught
        assert any("dispatch()" in f for f in found), found

    def test_the_real_tree_is_clean(self):
        """Stated separately so a failure names which half broke."""
        assert unreachable_methods() == []

    def test_a_name_named_only_in_a_docstring_is_not_a_use(self, tmp_path):
        """**The control for the prose false positive, and the reason this file
        can also gate `gui/`.**

        A dead `TranscriptView.set_suggestion_handler` was the original subject
        of this control: `tools/deadcode.py` reported it, it had no caller of any
        kind, and it was named exactly once outside its own definition - in a
        `window.py` docstring explaining the pattern `None` follows. A raw-text
        scan counts that word and calls the method live, which is the
        mirror-image of the `show_devices` mistake this file exists to avoid.

        **The subject is a fixture now, and that is a consequence rather than a
        preference.** Extending the gate from `gui/surfaces/` to the rest of
        `gui/` demands that no prose-only orphan *exist* there, so a control that
        required one to exist could not coexist with the gate - the gate would
        have to allowlist its own witness, and an allowlisting gate cannot notice
        the day its entry rots. A fixture is also better on its own terms: a real
        subject makes this control go **red when somebody wires the method up**,
        which is the one thing a control must never punish.

        **Both directions, because either alone is satisfiable by an extractor
        that is simply broken.** One that keeps every file reports both methods
        live and fails for the wrong reason; one that drops every file reports
        both dead and passes.
        """
        # The exclusion is load-bearing for the *real* tree scan, and it is
        # asserted rather than claimed - naming a finding in this file's own
        # prose puts it in the source, which is what happened three times while
        # this file was written.
        assert all(f.name != pathlib.Path(__file__).name
                   for f, _u, _d, _x in _index()), (
            "this test file is in its own index, so its docstrings count as "
            "callers and the real-tree gate cannot fail")
        body = (
            "class _Probe:\n"
            "    def prose_only_method(self):\n"
            "        return 1\n"
            "    def code_only_method(self):\n"
            "        return 1\n"
            "    def caller(self):\n"
            "        \"\"\"prose_only_method is discussed here, and nowhere that "
            "counts.\"\"\"\n"
            "        return self.code_only_method()\n")
        pkg, tests = self._synthetic(tmp_path, body)
        tests.mkdir()
        (tests / "test_probe.py").write_text("# no reference to either method\n")
        found = unreachable_methods(pkg, tests)
        assert "probe.py:2 prose_only_method()" in found, (
            f"a name mentioned only in a docstring was not reported dead - "
            f"prose is being read as a caller: {found}")
        assert not any("code_only_method" in f for f in found), (
            f"a name called in code was reported dead, so dropping prose is not "
            f"what this scan does - it is dropping callers: {found}")

    def test_a_string_dispatched_name_is_still_a_use(self):
        """**The other side, and the reason strings are kept at all.**

        Dropping *all* string literals - which is `tools/deadcode.py`'s
        deliberate rule - calls four live methods dead: `show_devices` and
        `speaking_row` are dispatched through string tuples, and `webkit_version`
        and `activity_text` are called from inside subprocess harnesses held as
        strings. Every one of them is live and none appears as a code token.

        Both directions are asserted because either alone can be satisfied by a
        scanner that is simply broken.
        """
        index = _index()
        for name in ("show_devices", "speaking_row",
                     "webkit_version", "activity_text"):
            total = sum(u.get(name, 0) - d.get(name, 0)
                        for _f, u, _defined, d in index)
            assert total > 0, (
                f"{name} is dispatched through a string and reads as dead - "
                f"the extractor is dropping the strings it needs")

    @pytest.mark.parametrize("name", ["show_devices", "_refresh_status", "find"])
    def test_three_known_live_seams_are_not_flagged(self, name):
        """The three seams an AST scan would net. If any of these ever becomes
        genuinely dead, this file fails on the seam first, which is the honest
        place for it to fail."""
        index = _index()
        # a live seam is used somewhere other than at its own def line
        total = sum(uses.get(name, 0) - defs.get(name, 0)
                    for _f, uses, _defined, defs in index)
        assert total > 1, (
            f"{name} is a known live seam but the tree now mentions it once - "
            f"either it became dead, or the scan is looking in the wrong place")
