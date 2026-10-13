"""Three classes were applied to widgets and styled by nothing at all.

`composer-model-row`, `cajita-question` and `command-palette` were each applied
exactly once in the tree and matched by no selector in any stylesheet in the
package. Asking the installed library as well - `hasattr` over `Gtk` and `Adw` -
answers False for all three, so none of them is a toolkit class either. A class
that styles nothing is an unfulfilled claim about how a thing looks, not
documentation; what the model row or the question row *should* look like is a
design decision, and inventing a rule here would have been a second answer to a
question nobody has asked.

**And the general version of this check is deliberately not here**, which is the
interesting half. The obvious gate is "every class applied under `gui/` resolves
to a selector in our stylesheet or is a documented toolkit class". Measured on
this tree:

| | count |
|---|---:|
| classes applied under `gui/` | **109** |
| resolved by a selector in this package | **74** |
| unresolved, applied in exactly one file | **26** |
| unresolved, applied in two or more files | **9** |

**Neither half of the unresolved 35 is separable.** `title-2` and `title-3` are
libadwaita's own classes on `Adw.PreferencesGroup` and are applied once each, so
"applied once" does not mean "ours"; and `model-picker` is ours, applied in two
files, so "applied in many" does not mean "the toolkit's". Deciding the toolkit
half **cannot be done on this box at all**: libadwaita compiles its stylesheet
into a `GResource` and ships no `.css` on disk, and `hasattr` only answers for
class names that happen to be Python attributes.

**So this file guards the three removals and the detector, and refuses to guard
the population.** A gate that reported 26 findings, most of them unverifiable on
the machine that runs it, is the shape this repository records three times
already - *"a scanner that reports 54 files is worse than no scanner"* - and it
would be a permanently noisy suite that nobody reads.

**The detector carries a planted violation, because a scan that cannot find the
thing it is looking for reports the same clean result as one that works.**
"""
import ast
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.gui import palette as palette_module  # noqa: E402

PKG = (pathlib.Path(__file__).resolve().parents[1]
       / "usr" / "lib" / "shani-chronoa" / "shani_chronoa")

#: The three that were applied and styled by nothing. Named so the regression is a
#: *specific* class coming back rather than a count moving - a count is a claim
#: about the whole tree and goes quiet when anything else lands.
REMOVED = ("composer-model-row", "cajita-question", "command-palette")


def _strings(tree):
    """Every `str` **and** `bytes` constant in a tree.

    The `bytes` half is load-bearing, and its absence was the bug in the first
    version of this scan: `style.py`'s stylesheet is a `b`-prefixed triple-quoted
    literal assigned to `css_data`, so a `str`-only walk reported all 114 classes
    as unstyled. It read exactly like a finding and it was the instrument.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            yield (node.value if isinstance(node.value, str)
                   else node.value.decode("utf-8", "replace"))


def selectors(root=None):
    """Every `.class` selector in any string or bytes literal under `root`."""
    out = set()
    for path in sorted((root or PKG).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for text in _strings(tree):
            out.update(re.findall(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)", text))
    return out


def applied_classes(root=None):
    """Every class name passed to `add_css_class`/`remove_css_class`, or to a
    `class_`/`css_class` keyword, anywhere under `root`. Maps name -> the sites."""
    out = {}
    for path in sorted((root or (PKG / "gui")).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
                    "add_css_class", "remove_css_class"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        out.setdefault(arg.value, []).append(
                            f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (kw.arg in {"class_", "css_class"}
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)):
                        out.setdefault(kw.value.value, []).append(
                            f"{path.name}:{node.lineno}")
    return out


class TestTheThreeAreGone:

    def test_none_of_them_is_applied_anywhere_in_the_gui(self):
        applied = applied_classes()
        back = {name: applied[name] for name in REMOVED if name in applied}
        assert not back, (
            f"a class that no stylesheet matches is applied again: {back}. "
            f"Either give it a rule or take the application out - a class that "
            f"styles nothing is a claim, not a label.")

    def test_the_palette_no_longer_declares_the_class_it_applied_once(self):
        """`CommandPalette.CSS` existed only to be applied on the next line.

        Deleting the *application* and leaving the constant would have kept the
        claim alive with nothing behind it, and reading the class would still say
        the palette styles itself.
        """
        assert not hasattr(palette_module.CommandPalette, "CSS"), (
            "CommandPalette.CSS is back, and its only reader was the "
            "add_css_class call that was deleted with it")

    def test_nothing_styles_them_either(self):
        """The other half, so the assertion above is not satisfied by renaming.

        A class that keeps a selector nobody applies is dead in the other
        direction, and this is the check that would notice.
        """
        left = [n for n in REMOVED if n in selectors()]
        assert not left, (
            f"a rule exists for a class nothing applies: {left} - dead CSS reads "
            f"as styling to the next person who greps for the name")


class TestTheDetectorCanFind:

    def test_a_planted_class_with_no_rule_is_found(self, tmp_path):
        """**A scan that cannot find the thing it is looking for reports the same
        clean result as one that works.**

        Planted in a scratch tree, so the real one is untouched. The scratch also
        carries the **bytes** stylesheet, which pins the `str`/`bytes` half of
        `_strings` - the bug that made this same scan report 114 dead classes on a
        tree where 74 are styled.
        """
        (tmp_path / "gui").mkdir()
        (tmp_path / "gui" / "widget.py").write_text(
            'w.add_css_class("planted-nothing-styles-this")\n'
            'w.add_css_class("planted-and-styled")\n')
        (tmp_path / "style.py").write_text(
            'css_data = b"""\n'
            '.planted-and-styled { color: red; }\n'
            '.something-else { color: blue; }\n"""\n')

        applied = applied_classes(tmp_path / "gui")
        styled = selectors(tmp_path)
        unstyled = sorted(c for c in applied if c not in styled)

        assert unstyled == ["planted-nothing-styles-this"], (
            f"the scan found {unstyled} rather than exactly the planted class. "
            f"A detector that cannot separate them would report the same clean "
            f"result on the real tree.")


class TestTheUnresolvablePopulationIsRecorded:

    def test_the_counts_this_file_says_are_still_the_counts(self):
        """**Recorded, not re-derived** - a test asserting an exact count of the
        unresolved population goes red every time a panel lands and reports nothing
        about the regression it was written for.

        What it does hold is the *shape* the refusal rests on: that both halves of
        the unresolved set exist, so neither "applied once" nor "applied in many"
        can be the predicate. If a future libadwaita, or a future stylesheet,
        collapsed one half, the claim this file refuses to guard would be worth
        re-deriving - so this test is what tells the next reader to go and look
        rather than to quote the number.
        """
        applied = applied_classes()
        styled = selectors()
        unresolved = {c: sites for c, sites in applied.items() if c not in styled}
        one_file = [c for c, s in unresolved.items() if len(s) == 1]
        many_files = [c for c, s in unresolved.items() if len(s) > 1]

        assert one_file and many_files, (
            "the unresolved population no longer has both halves - "
            f"{len(one_file)} applied in one file, {len(many_files)} in several. "
            "Either half alone would make one of those a usable predicate, so the "
            "reason this file guards nothing should be re-derived before it is "
            "still quoted as the reason.")