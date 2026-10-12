"""Which public names in the package does nothing in the shipped tree call?

**Why this exists.** AGENTS.md carries a list of unwired functions and warns
about itself: *"note it still rots, and the scan has to be re-run rather than
believed."* The scan that produced it was an AST pass run from a scratchpad, so
it is gone — and the list is now a hand-kept list, which is the failure this
repository records repeatedly (`capabilities._GROUPS`, `GATED`, `GATE_NAMES`,
`test_skill_gates_are_enforced._IMPL`: four lists, and a duplicate key once
silently killed a label).

So the fact is derived here instead, the way `gen_capabilities.py`,
`gen_eval_cases.py` and `gen_organism_doc.py` already derive theirs.

**The scan that produced the old list cannot be re-run by grep, and this run
demonstrated why.** Grepping the sixteen recorded names produced five false
"wired" answers, every one of a kind this tool is built not to fall for:

| name | what grep found |
|---|---|
| `price` | 51 hits, all the English word |
| `collapse` | 55 hits, mostly `border-collapse:collapse` in CSS |
| `describe_captured` | a **local variable** in `senses/vision.py:285` |
| `capability_for` | a string in `capabilities.py:40`, not a call |
| `within` | `tools/singing_demo.py` only, which is not packaged |

A bare word match cannot tell a call from prose, and prose is what a name like
`price` mostly is.

## What counts as a caller

An `ast.Name` or `ast.Attribute` anywhere in the shipped tree. **String
constants are not scanned**, which is what removes the prose false positives
above — and that has a known cost, recorded rather than hidden:

- **`sandbox/landlock.py:apply_filesystem_allowlist`** is called from *inside the
  source string* `get_landlock_wrapper()` returns, which the executor writes to
  the workspace and runs. It reads as dead and is not.
- **`usr/bin/` is a root, not an afterthought.** `netprovision.revalidate` and
  `read_envelope` are called only by `usr/bin/shani-chronoa-lab-network`, and a
  scan of `usr/lib` alone calls them dead. The scripts there import the package
  and are the privileged half.

**`pkg/` is excluded.** It is a stale copy of the tree, not the canonical
package, and including it doubles every count — AGENTS.md already records a
six-function divergence between the two copies as its own defect.

Run it: `python3 tools/deadcode.py` for the list, `--json` to write it somewhere.
It is a maintenance surface and nothing here is packaged, like the rest of
`tools/`.
"""

import argparse
import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "usr" / "lib" / "shani-chronoa"

#: Production roots. `tests/` is scanned separately and never counts as wiring —
#: a test can prove a name works and still leave it unreachable, which is the
#: dead-code class this repository records most often.
PRODUCTION_ROOTS = (PACKAGE, ROOT / "usr" / "bin", ROOT / "tools")
EXTRA_FILES = (ROOT / "render_ui.py",)

#: Names whose only "caller" is a string the product builds and runs. Two
#: entries, each with its reason, and the test fails if either is deleted —
#: an allowlist nobody checks is a blank cheque.
#: Names the *framework* calls by string, so they are never referenced by an
#: identifier in our own code. `GApplication` dispatches `do_command_line`,
#: `do_startup`, `do_activate` and `do_shutdown` by name; a scan that cannot see
#: that reports the app's entry point dead, and AGENTS.md records
#: `do_command_line` as *wired* (it attaches `%U` file arguments and `--ask=`).
FRAMEWORK_CALLERS = {
    "do_command_line": "a GApplication vfunc, dispatched by name from the bus",
    "do_startup": "a GApplication vfunc, dispatched by name from the bus",
    "do_activate": "a GApplication vfunc, dispatched by name from the bus",
    "do_shutdown": "a GApplication vfunc, dispatched by name from the bus",
}

GENERATED_CODE_CALLERS = {
    "apply_filesystem_allowlist":
        "called from the source string get_landlock_wrapper() returns, which "
        "the executor writes to the workspace and runs",
}


def _python_files(root: pathlib.Path) -> "list[pathlib.Path]":
    """Python sources under `root`, **including the extensionless launchers**.

    `usr/bin/` holds ten scripts with no `.py` suffix at all -
    `shani-chronoa-lab-network`, `shani-chronoa-mcp` and the rest - and they are
    the privileged half: `netprovision.revalidate` and `read_envelope` are called
    by exactly one of them and by nothing else. A `rglob("*.py")` scan reports
    both dead, which is the false positive this repository already records from
    an earlier scan that walked `usr/lib` and not `usr/bin`.

    Caught by `test_usr_bin_counts_as_a_caller_and_not_as_an_afterthought`, which
    is the only reason this is not still broken.
    """
    found = [p for p in root.rglob("*.py")
             if "pkg" not in p.relative_to(ROOT).parts[:1]]
    if root.name == "bin":
        found += [p for p in root.iterdir()
                  if p.is_file() and not p.suffix and p.name != "__pycache__"]
    return sorted(set(found))


def _files() -> "tuple[list[pathlib.Path], list[pathlib.Path]]":
    production = []
    for root in PRODUCTION_ROOTS:
        if root.is_dir():
            production += _python_files(root)
    production += [p for p in EXTRA_FILES if p.is_file()]
    tests = sorted((ROOT / "tests").rglob("*.py")) if (ROOT / "tests").is_dir() else []
    # A test file is a test file even if it sits outside tests/.
    production = [p for p in production if "tests" not in p.relative_to(ROOT).parts[:1]]
    return sorted(set(production)), tests


def _tree(path: pathlib.Path) -> "ast.Module | None":
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        # A file that will not parse is reported by the suite; here it simply
        # contributes no names, and `unparsed` says so rather than hiding it.
        return None


def _defines(tree: ast.Module, path: pathlib.Path) -> "set[str]":
    """Module-level `def`/`class`, and methods of a class defined in that module.

    Not `ast.walk`: a local variable named `config` inside a function body is not
    a definition, and treating it as one is what made an earlier version of the
    stale-cross-reference gate report sixty-eight violations on a tree with none.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(child.name)
    # **Public names only**, which is what makes the count comparable to the
    # figure AGENTS.md records. Two things fall out of that one filter:
    #
    # - **Dunders are excluded, and they must be.** `__add__`, `__repr__`,
    #   `__call__` and `__post_init__` are invoked by the interpreter, never by
    #   name, so the first run reported all of them dead - a list that reads as a
    #   finding about the tree when it is a statement about Python.
    # - Private helpers drop out too, which is the point: `_trim_history` being
    #   called only from its own module is normal and uninteresting.
    return {n for n in names if not n.startswith("_")}


def _references(tree: ast.Module) -> "set[str]":
    """Every identifier the code mentions - and, crucially, not every word.

    `ast.Constant` strings are skipped, which is the whole point: a docstring
    saying "see `price`" is prose about a name, not a use of it.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def scan() -> "dict[str, object]":
    production, tests = _files()
    defined: dict[str, list[str]] = {}
    used_in_production: set[str] = set()
    used_in_tests: set[str] = set()
    unparsed: list[str] = []

    for path in production:
        tree = _tree(path)
        if tree is None:
            unparsed.append(str(path.relative_to(ROOT)))
            continue
        rel = str(path.relative_to(ROOT))
        for name in _defines(tree, path):
            defined.setdefault(name, []).append(rel)
        used_in_production |= _references(tree)

    for path in tests:
        tree = _tree(path)
        if tree is None:
            unparsed.append(str(path.relative_to(ROOT)))
            continue
        used_in_tests |= _references(tree)

    wired, test_only, dead = [], [], []
    for name in sorted(defined):
        if name in used_in_production:
            wired.append(name)
        elif name in used_in_tests:
            test_only.append(name)
        else:
            dead.append(name)

    excluded = {**GENERATED_CODE_CALLERS, **FRAMEWORK_CALLERS}
    generated = {n: r for n, r in GENERATED_CODE_CALLERS.items() if n in dead}
    framework = {n: r for n, r in FRAMEWORK_CALLERS.items() if n in dead}
    reported_dead = [n for n in dead if n not in excluded]

    return {
        "defined": len(defined),
        "production_files": len(production),
        "test_files": len(tests),
        "wired": len(wired),
        "test_only": len(test_only),
        "dead": reported_dead,
        "generated_code_callers": generated,
        "framework_callers": framework,
        "unparsed": unparsed,
    }


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", help="write the result to this path")
    args = parser.parse_args(argv)

    report = scan()
    print(f"  {report['defined']} public names in {report['production_files']} "
          f"shipped file(s)")
    print(f"    {report['wired']} referenced in the shipped tree")
    print(f"    {report['test_only']} referenced by a test but not by the "
          f"shipped tree - proven to work, unreachable at runtime")
    print(f"    {len(report['dead'])} referenced by nothing at all")
    for name, reason in sorted(report["generated_code_callers"].items()):
        print(f"    ({name} reads as dead and is not: {reason})")
    for name, reason in sorted(report["framework_callers"].items()):
        print(f"    ({name} reads as dead and is not: {reason})")
    if report["unparsed"]:
        print(f"    !! {len(report['unparsed'])} file(s) did not parse and "
              f"contributed no names: {', '.join(report['unparsed'])}")
    print("\n  dead:")
    for name in report["dead"]:
        print(f"    {name}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
        print(f"\n  written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())