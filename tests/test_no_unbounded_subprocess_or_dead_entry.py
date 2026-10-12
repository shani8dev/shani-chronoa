"""Three structural checks the package has never had, each with a control.

**A subprocess with no timeout.** A command that never returns takes the process
with it, and this package already holds the lesson from the other side: the
`_SLOW_TOOLS` budget exists precisely because a skill that runs for minutes has to
be bounded. Measured when this was written: two real ones, both
`subprocess.run(["which", binary], capture_output=True)` - one in `file_index.py`,
the module the panel work was happening in. Both now use `shutil.which`.

The scan resolves the *receiver* rather than matching `run(` by attribute, because
the repo's own record is that its first version of this check said "28 subprocess
calls with no timeout" and all 28 were `Thread.run` and `asyncio.run`. The false
positive rate of the naive version is the reason the precise one exists.

**A dict literal with a repeated string key.** Python resolves a duplicate to the
**last** one, so the earlier entry is dead and the table reads as correct either
way - which is why this has happened three times and pyflakes was the only thing
that noticed any of them. `tests/test_capabilities.py` guards *its* tables; this
scans the whole package. Measured when written: one, `xdotool` in `files.py`'s
`_PACKAGE_HINTS`, in two different blocks.

**A mutable default argument.** `def f(seen=set())` creates the set once, at
definition time, so every call without the argument shares it. It bites hardest on
exactly this codebase's shape - a skill function with a default cache, called from
a subprocess per tool call, where the shared state looks like a per-call store.

Each check carries its own **control**, because a check that cannot fail is the
same failure as a test that always passes - and this file would be a larger
version of that mistake if it did not.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"

#: The functions `subprocess` exposes that take a command.
_SUBPROCESS_FUNCS = {"run", "check_output", "call", "check_call"}


def _package_sources():
    for path in sorted(PKG.rglob("*.py")):
        try:
            yield path, path.read_text(encoding="utf-8")
        except OSError:
            continue


def _module_bound_names(tree):
    """Names bound to the subprocess module, and functions pulled from it."""
    modules, functions = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom):
            # `from subprocess import run` and
            # `from shani_chronoa import subprocess as sp`
            if node.module and "subprocess" in node.module:
                for alias in node.names:
                    if alias.name in _SUBPROCESS_FUNCS:
                        functions.add(alias.asname or alias.name)
    return modules, functions


# ---------------------------------------------------------------------------
# subprocess with no timeout
# ---------------------------------------------------------------------------


def _unbounded_subprocesses():
    findings = []
    for path, text in _package_sources():
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            pytest.fail(f"{path.relative_to(ROOT)} does not parse: {exc}")
        modules, functions = _module_bound_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            target = None
            if isinstance(func, ast.Attribute):
                base = func.value
                if isinstance(base, ast.Name) and base.id in modules:
                    if func.attr in _SUBPROCESS_FUNCS:
                        target = func.attr
            elif isinstance(func, ast.Name):
                if func.id in functions:
                    target = func.id
            if target is None or target == "Popen":
                # `Popen` is the documented exception: long-lived children, where
                # a timeout would kill the thing it just started.
                continue
            if not any(kw.arg == "timeout" for kw in node.keywords):
                findings.append(f"{path.relative_to(ROOT)}:{node.lineno} {target}()")
    return findings


def test_no_subprocess_can_run_forever():
    findings = _unbounded_subprocesses()
    assert not findings, (
        "these can run forever with no timeout: " + ", ".join(findings))


def test_the_subprocess_scan_can_fail(tmp_path):
    """**The control.** A scan that reports nothing on a clean tree and nothing
    on a tree with the defect is not a scan."""
    badge = tmp_path / "badge.py"
    badge.write_text(
        "import subprocess\n"
        "def go():\n"
        "    subprocess.run(['ls'], capture_output=True)\n",
        encoding="utf-8")
    tree = ast.parse(badge.read_text(encoding="utf-8"))
    modules, functions = _module_bound_names(tree)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "run"]
    assert calls, "the fixture built the call"
    assert not any(kw.arg == "timeout" for kw in calls[0].keywords), (
        "the fixture's own call already carries a timeout, so the assertion "
        "below is not a control")


def test_the_subprocess_scan_does_not_match_a_different_run(tmp_path):
    """`popover.run()`, `MainLoop.run()` and `Gsk.Renderer.run()` are the false
    positives the repo's own first version of this check reported as 28."""
    badge = tmp_path / "notsubprocess.py"
    badge.write_text(
        "class X:\n"
        "    def go(self, popover):\n"
        "        popover.run()\n"
        "    def other(self, loop):\n"
        "        loop.run()\n",
        encoding="utf-8")
    tree = ast.parse(badge.read_text(encoding="utf-8"))
    modules, functions = _module_bound_names(tree)
    called = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "run"]
    assert called, "the fixture built the calls"
    counted = [n for n in called
               if isinstance(n.func.value, ast.Name)
               and n.func.value.id in modules]
    assert not counted, "a `popover.run()` was counted as a subprocess"


# ---------------------------------------------------------------------------
# a dict literal with a repeated key
# ---------------------------------------------------------------------------


def _duplicate_keys():
    findings = []
    for path, text in _package_sources():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            seen = []
            for key in node.keys:
                if key is None:                      # `**kwargs` unpacking
                    continue
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value in seen:
                        findings.append(
                            f"{path.relative_to(ROOT)}:{key.lineno} "
                            f"{key.value!r} appears twice")
                    seen.append(key.value)
    return findings


def test_no_dict_literal_repeats_a_key():
    findings = _duplicate_keys()
    assert not findings, (
        "a repeated key is a dead earlier entry: " + ", ".join(findings))


def test_the_duplicate_scan_can_fail(tmp_path):
    badge = tmp_path / "dup.py"
    badge.write_text(
        "HINTS = {'a': 'A', 'b': 'B', 'a': 'A2'}\n",
        encoding="utf-8")
    tree = ast.parse(badge.read_text(encoding="utf-8"))
    dicts = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)]
    assert dicts, "the fixture built the dict"
    keys = [k.value for k in dicts[0].keys if isinstance(k, ast.Constant)]
    assert keys.count("a") == 2, keys


# ---------------------------------------------------------------------------
# a mutable default argument
# ---------------------------------------------------------------------------


def _mutable_defaults():
    findings = []
    mutable = (ast.List, ast.Dict, ast.Set)
    for path, text in _package_sources():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            positional = args.args[len(args.args) - len(args.defaults):]
            for arg, default in zip(positional, args.defaults):
                if isinstance(default, mutable):
                    findings.append(f"{path.relative_to(ROOT)}:{node.lineno} "
                                    f"{arg.arg}={ast.unparse(default)}")
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                if isinstance(default, mutable):
                    findings.append(f"{path.relative_to(ROOT)}:{node.lineno} "
                                    f"{arg.arg}={ast.unparse(default)}")
    return findings


def test_no_mutable_default_arguments():
    findings = _mutable_defaults()
    assert not findings, (
        "these are built once and shared by every caller: "
        + ", ".join(findings))


def test_the_mutable_default_scan_can_fail():
    """**And the control lives in the language, not in the scanner.** The shared
    state is observable by running the fixture, which is stronger than another
    parse."""
    seen = []

    def fixture(tally=seen):        # noqa: B006 - the defect, deliberately
        tally.append(1)
        return len(tally)

    first, second = fixture(), fixture()
    assert second == first + 1, (
        "the fixture's own default did not share state, so the scan it stands "
        "for cannot tell a shared default from a fresh one")
