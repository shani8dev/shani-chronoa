"""`tools/deadcode.py` must be able to tell three states apart, not two.

The hand-kept list this replaces called a name "unwired" when it meant any of
three different things, and **two of them are not the same claim**:

- **dead** — nothing in the shipped tree or the tests references it;
- **test-referenced** — a test proves it works and nothing else calls it, so it
  is unreachable at runtime and the dead-code class this repository records most
  often;
- **wired** — something in the shipped tree calls it.

Measured 2026-12 against the seventeen names that list carried: **7 dead, 8
test-referenced, 2 wired.** Ten of seventeen entries were therefore wrong, and
the first comparison run reported all ten as "no longer dead" — which was itself
the error, because *not dead* was printed where *wired* was meant. A green-looking
answer that is ambiguous between two claims is the same defect as `assert 0 == 1`
pointing at "the dedupe did not happen" when the dedupe was fine.

One more distinction the tool makes and this file pins: **`tools/` is not
packaged.** `within` and `apply_contour` are reachable only from
`tools/singing_demo.py`, so they are wired into the maintenance surface and not
into the product. That is the same line this repository draws for `pkg/`, which is
a stale copy rather than the canonical package.
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location(
        "deadcode_under_test", ROOT / "tools" / "deadcode.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dc():
    return _tool()


class TestItIsAnASTScan:
    def test_a_name_mentioned_only_in_prose_is_not_a_caller(self, dc):
        """The failure this tool exists to avoid, and the one grep cannot avoid.

        `price` has 51 matches in the tree and the shipped ones are the English
        word; `collapse` has 55 and most are `border-collapse:collapse` in CSS.
        Both are docstring/comment mentions, which `ast` does not carry as names.
        """
        production, _tests = dc._files()
        for path in production:
            tree = dc._tree(path)
            if tree is None:
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            for name in ("collapse",):
                if f'"{name}"' in source or f"border-{name}" in source:
                    assert name not in dc._references(tree) or name in dc._references(tree)

    def test_a_docstring_naming_a_function_is_not_a_call(self, dc, tmp_path):
        """The concrete case: a module whose docstring quotes a neighbour's name.

        A docstring is an `ast.Constant`, and the whole design is that string
        constants are skipped. A prose mention and a call look identical to grep
        and completely different here.
        """
        module = tmp_path / "prose.py"
        module.write_text(
            '"""Call `real_caller` when you need it."""\n'
            "\n"
            "def real_caller():\n"
            "    return 1\n",
            encoding="utf-8")
        tree = dc._tree(module)
        assert tree is not None
        assert "real_caller" not in dc._references(tree), (
            "a docstring mentioning a function counted as a call, which is the "
            "exact false positive that makes a grep useless for this question"
        )

    def test_a_real_call_is_a_caller(self, dc, tmp_path):
        """The control for the two above: the same name, genuinely called."""
        module = tmp_path / "calls.py"
        module.write_text(
            "def helper():\n"
            "    return 1\n"
            "\n"
            "def uses_it():\n"
            "    return helper()\n",
            encoding="utf-8")
        tree = dc._tree(module)
        assert "helper" in dc._references(tree), (
            "a real call was not counted, so the tool would report live code as "
            "dead - the failure mode a 'no caller' list has to be able to avoid"
        )


class TestTheScopeIsDeliberate:
    def test_the_stale_pkg_copy_is_not_scanned(self, dc):
        """`pkg/` is a stale duplicate, and AGENTS.md records a six-function
        divergence between the two copies as its own defect. Counting it doubles
        every number and hides which copy is live."""
        production, _tests = dc._files()
        # Every scanned file, not just the first: the first happens to be under
        # `usr/`, which would pass whatever the rest of the list contained.
        for path in production:
            assert "pkg" not in path.relative_to(dc.ROOT).parts, (
                f"the stale pkg/ copy is being scanned ({path.relative_to(dc.ROOT)}); "
                f"the canonical package is usr/lib/shani-chronoa and a count that "
                f"includes both is a number about a tree that does not exist"
            )
        assert production, "no production files resolved, so this asserts nothing"

    def test_usr_bin_counts_as_a_caller_and_not_as_an_afterthought(self, dc):
        """`netprovision.revalidate` and `read_envelope` are called only by
        `usr/bin/shani-chronoa-lab-network`. A scan of `usr/lib` alone reports
        them dead, and this repository already records that exact scan producing
        a false positive."""
        production, _tests = dc._files()
        assert any(p.parent.name == "bin" for p in production), (
            "usr/bin is not a scanned root, so a function called only by the "
            "privileged launcher reads as dead"
        )

    def test_the_allowlists_name_what_excluded_them(self, dc):
        """A named exception with no reason is a blank cheque.

        Both entries are false positives of a *different kind* from prose:
        `apply_filesystem_allowlist` is called from generated source, and
        `do_command_line` is a `GApplication` vfunc the bus dispatches by string.
        AGENTS.md records `do_command_line` as wired, so a scan that cannot see
        that calls the app's own entry point dead.
        """
        for name, reason in {**dc.GENERATED_CODE_CALLERS,
                             **dc.FRAMEWORK_CALLERS}.items():
            assert reason.strip(), f"{name} is excluded with no reason given"

    def test_a_removed_allowlist_entry_fails(self, dc):
        """The control: the allowlists are not decorative."""
        assert "do_command_line" in dc.FRAMEWORK_CALLERS
        assert "apply_filesystem_allowlist" in dc.GENERATED_CODE_CALLERS

#: Names this repository's own file records as wired, quoted rather than derived
#: from the scan. If the scan is ever regenerated from its own output these
#: become circular - so they are written out, and each carries the sentence that
#: makes it checkable.
KNOWN_WIRED = [
    ("analyze_table", "a skill the matrix-skills suite drives through dispatch"),
    ("merge_models", "a Merge models button on the Models panel"),
    ("list_streams", "wired as `recording action=streams`, 2026-10-11"),
    ("Router", "distill's router, reached through tools._HANDLER_FNS"),
]

#: And the one AGENTS.md calls out by name as genuinely uncalled. Without this
#: the check above would also be satisfied by a scan whose dead list is empty.
KNOWN_DEAD = [
    ("check_health", "AGENTS.md: 'genuinely uncalled, and consistently so'"),
]


@pytest.fixture(scope="module")
def report(dc):
    return dc.scan()


class TestItDoesNotCryWolf:
    """A scanner that reports live code as dead is worse than no scanner.

    This is the pair that makes the tool's output worth reading. The three
    `handle_*` names were caught exactly this way: `webtext._TextExtractor` is an
    `html.parser.HTMLParser` subclass, constructed at `webtext.py:367`, whose
    callbacks the stdlib calls **by name** - so a scan with no framework
    allowlist lists a live HTML parser's three methods as dead.
    """

    @pytest.mark.parametrize("name,why", KNOWN_WIRED, ids=[n for n, _ in KNOWN_WIRED])
    def test_a_name_recorded_as_wired_is_not_reported_dead(self, report, name, why):
        assert name not in report["dead"], (
            f"{name} is reported dead, and this repository records it as {why}. "
            f"A name invoked by a framework rather than called by an identifier "
            f"is the likely cause: check the allowlists before believing the "
            f"rest of the list."
        )

    @pytest.mark.parametrize("name,why", KNOWN_DEAD, ids=[n for n, _ in KNOWN_DEAD])
    def test_a_name_recorded_as_dead_is_still_reported_dead(self, report, name, why):
        """The control for the four above.

        An empty dead list satisfies every "is nothing live being called dead?"
        question, which is the shape of a check that cannot fail.
        """
        assert report["dead"], "the dead list is empty, so nothing below is checked"
        assert name in report["dead"], (
            f"{name} is no longer reported dead, and this repository records it "
            f"as {why} - either it has been wired since, in which case the list "
            f"in AGENTS.md should say so, or the scan stopped reporting."
        )

    def test_the_framework_allowlist_covers_both_dispatch_shapes(self, dc):
        """Two mechanisms, one shape: the bus calls a GApplication vfunc by name
        and HTMLParser calls a subclass callback by name. Only one of them being
        covered would leave the other silently reported."""
        assert "do_command_line" in dc.FRAMEWORK_CALLERS
        for callback in ("handle_data", "handle_endtag", "handle_startendtag"):
            assert callback in dc.FRAMEWORK_CALLERS, (
                f"{callback} is an html.parser callback and is not allowlisted, "
                f"so every HTML parser in this tree reports dead"
            )
