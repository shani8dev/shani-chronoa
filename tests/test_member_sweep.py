"""`tools/member_sweep.py` — and the only thing worth reading about it.

The defect it exists for: `_model_row` was written on `_ModeStrip` while
`ChronoaWindow.__init__` called it, so **every window construction raised
`AttributeError`**. pyflakes cannot see it — `self.foo()` inside a method is a
name, and which class owns it is a question of inheritance rather than of
syntax. It was found by building the row and asking for its label.

So the sweep is only worth having if it *can* find that, and a clean result is
only worth reading if it is reachable. Both are asserted here, in that order,
because a checker that has stopped looking prints exactly what a working one
prints when there is nothing to find.

**And it found a second one on its first run**: `gui/surfaces/artifact_store.py`
walked a `Gtk.Box` with `get_children()`, which GTK4 removed, from the **Clear**
button's handler — so pressing Clear raised on every press. That one is fixed;
its test asserts the *button*, not the helper.
"""

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import member_sweep  # noqa: E402


def _package_copy(tmp_path, source="gui/window.py"):
    """A package tree with one file replaced or edited, preserving the layout."""
    import shutil

    root = tmp_path / "pkg"
    shutil.copytree(REPO / "usr/lib/shani-chronoa/shani_chronoa", root)
    return root


def _missing(root, relative="gui/window.py"):
    findings, _unresolved = member_sweep.sweep(root)
    return {label: set(names) for label, names, _chain, _ext in findings}


class TestTheSweepCanFindTheDefectItWasWrittenFor:
    """The positive control. Without it, "clean" is unreadable."""

    def test_a_method_moved_to_the_wrong_class_is_named(self, tmp_path):
        """Put `_model_row` back on `_ModeStrip` and the sweep must say so.

        Done by editing the text, the way the defect arrived - not by building a
        fixture shaped like the bug, which is this repository's own rule.
        """
        root = _package_copy(tmp_path)
        window = root / "gui/window.py"
        lines = window.read_text(encoding="utf-8").splitlines(keepends=True)

        start = next(n for n, line in enumerate(lines)
                     if line.strip() == "def _model_row(self) -> Gtk.Widget:")
        stop = next(n for n in range(start, len(lines))
                    if lines[n].strip().startswith("def _model_word"))
        block = lines[start - 1:stop]           # with its blank line
        del lines[start - 1:stop]
        onto_strip = next(n for n, line in enumerate(lines)
                          if line.startswith("    def _model_chip"))
        lines[onto_strip:onto_strip] = block
        window.write_text("".join(lines), encoding="utf-8")

        found = _missing(root)
        named = [names for label, names in found.items()
                 if label.endswith("::ChronoaWindow")]
        assert named, (
            f"the sweep reported nothing for a class calling a method it does "
            f"not have - it reported: {sorted(found)}")
        assert "_model_row" in named[0], (
            f"the sweep found the class but not the method: {named[0]}")

    def test_the_tree_it_is_run_on_is_clean(self):
        """**And the same sweep, unchanged, on the real tree, finds nothing.**

        This is the other half and it is what the first test is for: a checker
        that names everything is as useless as one that names nothing, and only
        a known-good method and a known-bad one distinguish them.
        """
        findings, _unresolved = member_sweep.sweep()
        assert findings == [], [f[0] for f in findings]


class TestItDoesNotReportTheThreeThingsThatAreNotDefects:

    def test_a_base_the_tool_cannot_ask_is_not_a_finding(self):
        """`BaseHTTPRequestHandler` is stdlib, and the tool says so.

        Reported as a finding it would be crying wolf about names nobody
        checked; dropped silently it would be an absence read as a pass. It is
        a third thing: **asked, and could not answer.**
        """
        findings, unresolved = member_sweep.sweep()
        assert any("BaseHTTPRequestHandler" in bases
                   for _label, bases in unresolved), (
            "an unresolvable base should be reported as unresolvable")
        for label, _bases in unresolved:
            assert label not in {f[0] for f in findings}, (
                f"{label} is both unresolved and a finding")

    def test_an_attribute_read_is_not_a_method_call(self, tmp_path):
        """`self.mac.replace(...)` is a method of a MAC address.

        The first version reported `mac` as a method `BluezWatch` must define.
        A name read off `self` is not a name called on `self`.
        """
        root = tmp_path / "pkg"
        (root / "sub").mkdir(parents=True)
        (root / "sub/__init__.py").write_text("", encoding="utf-8")
        (root / "sub/watch.py").write_text(
            'class Watch:\n'
            '    def __init__(self, mac):\n'
            '        self.mac = mac\n'
            '    def use(self):\n'
            '        return self.mac.replace(":", "-")\n'
            'class Child(Watch):\n'
            '    def use(self):\n'
            '        return self.mac.upper()\n',
            encoding="utf-8")
        findings, _unresolved = member_sweep.sweep(root)
        assert findings == [], [f for f in findings]

    def test_a_static_methods_first_parameter_is_not_self(self, tmp_path):
        """`_should_toast(text, ...)` made `text.startswith` a member."""
        root = tmp_path / "pkg"
        (root / "sub").mkdir(parents=True)
        (root / "sub/__init__.py").write_text("", encoding="utf-8")
        (root / "sub/m.py").write_text(
            'import gi\n'
            'gi.require_version("Gtk", "4.0")\n'
            'from gi.repository import Gtk\n'
            'class W(Gtk.Box):\n'
            '    @staticmethod\n'
            '    def _should_toast(text):\n'
            '        return text.startswith("x")\n',
            encoding="utf-8")
        findings, _unresolved = member_sweep.sweep(root)
        assert findings == [], [f for f in findings]


class TestTheArtifactStoreClearButton:
    """The second defect the sweep found, and the one that was live.

    `_ArtifactStoreView._find_search_entry` walked the widget tree with
    `get_children()` - a GTK3 name a GTK4 `Gtk.Box` does not have - and the
    only caller is the **Clear** button's handler. Measured by pressing it on a
    real view: `AttributeError: '_ArtifactStoreView' object has no attribute
    'get_children'`.
    """

    def test_pressing_clear_does_not_raise(self):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from types import SimpleNamespace

        from shani_chronoa.gui.surfaces import artifact_store

        gi.require_version("Gtk", "4.0")
        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw

            Adw.init()
        except (ValueError, ImportError):
            pass

        view = artifact_store._ArtifactStoreView(SimpleNamespace())
        view._on_search_text_changed(view._search_entry)
        view._on_clear_search(None)              # must not raise
        assert view._search_text == ""

    def test_the_entry_is_held_rather_than_searched_for(self):
        """The fix is a reference, so the test must be about the reference.

        Asserting only "Clear did not raise" passes against any implementation
        that happens not to crash - including one that finds nothing and
        returns `None`. This asserts the entry is the one that was built.
        """
        import gi

        gi.require_version("Gtk", "4.0")
        from types import SimpleNamespace

        from shani_chronoa.gui.surfaces import artifact_store

        view = artifact_store._ArtifactStoreView(SimpleNamespace())
        entry = view._find_search_entry()
        assert entry is not None, "Clear has no entry to clear"
        assert isinstance(entry, gi.repository.Gtk.Entry)

    def test_every_get_children_call_is_guarded_by_an_hasattr(self):
        """**The shape, not the one site.** `get_children` is a GTK3 name and a
        GTK4 container has `get_first_child`/`get_next_sibling` instead, so a
        call on a `Gtk.Box` raises the moment it is reached.

        Asserted over the tree rather than at the file that happened to be
        caught, so a second site cannot arrive quietly.

        **Parsed, not grepped.** The first version searched the text and
        reported **five** sites - of which four were *comments explaining this
        very fix*, in `asking.py`, `artifact_store.py` and `diff.py`. That is
        the trap this repository records more than any other, met again:
        *a scanner whose needle is in its own input reports nothing*. `ast`
        drops docstrings and comments, so this cannot see the prose.

        **And the rule is "guarded", not "absent".** `activity.py` *does* call
        `self._summary.get_children()` and is correct: it guards it with
        `hasattr(self._summary, "get_children")`, because `build_summary`
        returns either a plain `Gtk.Box` or a `common.group`, and only one of
        the two has the method. Requiring the call not to exist would have
        failed correct code and been "fixed" by deleting the working guard -
        which is the over-fix this file records for `connect`/`emit`.
        """
        import ast

        pkg = REPO / "usr/lib/shani-chronoa/shani_chronoa"
        offenders, seen_guards = [], set()
        for path in pkg.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8",
                                                errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "hasattr"
                        and len(node.args) == 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value in ("get_children", "get_child")):
                    seen_guards.add(ast.unparse(node.args[0]))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get_children"):
                    receiver = ast.unparse(node.func.value)
                    if receiver not in seen_guards:
                        offenders.append(f"{path.name}:{node.lineno} "
                                         f"({receiver})")
        assert not offenders, (
            f"a GTK3 container method with no hasattr guard, which raises on "
            f"every GTK4 widget: {offenders}")

    def test_that_test_can_still_see_one(self):
        """**The control for the control.**

        An AST scan that finds nothing is indistinguishable from one that has
        stopped working, so this plants the call in a scratch tree and requires
        it to be named. Without it, "no offenders" could be the scan doing
        nothing at all - and this repository has shipped a check that could not
        fail four separate times.
        """
        import ast

        planted = ast.parse("\n".join([
            "class V:",
            "    def clear(self):",
            "        for c in self.get_children():",
            "            pass",
        ]))
        found = [n.lineno for n in ast.walk(planted)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "get_children"]
        assert found == [3], (
            "the AST scan cannot see a planted `self.get_children()` call, so "
            "the zero it reports on the real tree means nothing")