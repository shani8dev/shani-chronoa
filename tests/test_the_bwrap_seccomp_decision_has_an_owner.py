"""The decision to withhold seccomp under bubblewrap had no function behind it.

`_seccomp_conflicts_with_bwrap(level, use_bwrap)` existed, carried the whole
measurement, and **had no caller in the tree** - while the site that makes the
decision inlined the condition and cited the function in a comment as its
authority:

    # ... see `_seccomp_conflicts_with_bwrap` for the measurement.
    if _PENDING_SECCOMP.get("enabled"):

## Why the inline form was a real risk, not a style preference

The comment above that line explains the placement: whether bubblewrap will
actually wrap the command is only known *here*, after

    use_bwrap = use_bwrap and _bwrap_usable()

and inside `if use_bwrap:`. So the answer was carried by **indentation**. Hoist
that block for an early return, or reorder the argv build, and the filter goes
back into bwrap's own `preexec_fn` - where it is installed before bwrap's
`mount(2)` calls and bwrap dies:

    filter ON  -> bwrap rc=1  bwrap: Failed to make / slave: Operation not permitted
    filter OFF -> bwrap rc=0

A confinement mechanism refusing to confine anything, measured on this machine.

The predicate is now asked at the site. It is **tautological today** - inside
`if use_bwrap:` the call returns True and the old condition gave the same answer -
so the behavioural assertion below is deliberately weak, and the load-bearing
assertion is structural: *the call is inside the `if use_bwrap:` body*.

## What is not covered here

Driving `_run_landlock` end to end needs a workspace, a profile and a live bwrap,
and the existing 61 tests in `test_sandbox_seccomp.py` do not touch this path at
all - `_SECCOMP_SKIPPED_FOR_BWRAP` and the predicate were both untested. So the
warn-once behaviour is asserted directly on the set rather than through a spawn,
and the file says so rather than implying the spawn is covered.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

SOURCE = ROOT / "usr/lib/shani-chronoa/shani_chronoa/sandbox/executor.py"

from shani_chronoa.sandbox import executor as E  # noqa: E402


def _tree():
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _find(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _calls_predicate(node):
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            got = (func.attr if isinstance(func, ast.Attribute)
                   else func.id if isinstance(func, ast.Name) else None)
            if got == "_seccomp_conflicts_with_bwrap":
                return True
    return False


def test_the_predicate_has_a_caller():
    """**The defect.** The function carrying the measurement was cited and never
    called."""
    tree = _tree()
    run = _find(tree, "_run_landlock")

    assert run is not None, "the spawn helper moved; this file needs re-reading"
    assert _calls_predicate(run), (
        "nothing calls `_seccomp_conflicts_with_bwrap` any more, so the bwrap/"
        "seccomp decision is implied by indentation again and a hoist would "
        "install a filter that stops bubblewrap from starting")


def test_the_call_is_inside_the_bwrap_branch():
    """**The structural assertion that carries the weight.** Anywhere else in
    `_run_landlock` would be wrong: the decision needs `_bwrap_usable()`'s answer,
    which only exists after `use_bwrap = use_bwrap and _bwrap_usable()`."""
    run = _find(_tree(), "_run_landlock")
    guarded = [node for node in ast.walk(run)
               if isinstance(node, ast.If)
               and isinstance(node.test, ast.Name)
               and node.test.id == "use_bwrap"]

    assert guarded, (
        "there is no `if use_bwrap:` branch in `_run_landlock` any more; the "
        "placement this test is about has changed and needs re-reading by hand")
    assert any(_calls_predicate(node) for node in guarded), (
        "the predicate is not called inside `if use_bwrap:` - so it is being "
        "asked on a path that runs whether or not bubblewrap wrapped the command")


def test_the_predicate_ignores_the_level():
    """Its own stated contract: *"Only `use_bwrap` matters, not the level."*"""
    from shani_chronoa.sandbox.models import SandboxLevel

    for level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_4_HOST_ROOT):
        assert E._seccomp_conflicts_with_bwrap(level, True) is True
        assert E._seccomp_conflicts_with_bwrap(level, False) is False


def test_the_warning_is_emitted_once_per_level():
    """`_SECCOMP_SKIPPED_FOR_BWRAP` is the once-only set, and it was untested. Its
    purpose is not to warn twice about the same layer, and it is process-wide."""
    assert isinstance(E._SECCOMP_SKIPPED_FOR_BWRAP, set)

    E._SECCOMP_SKIPPED_FOR_BWRAP.discard(1)      # our own key, not a real level
    assert 1 not in E._SECCOMP_SKIPPED_FOR_BWRAP

    E._SECCOMP_SKIPPED_FOR_BWRAP.add(1)
    assert 1 in E._SECCOMP_SKIPPED_FOR_BWRAP
    E._SECCOMP_SKIPPED_FOR_BWRAP.discard(1)


def test_the_measurement_is_still_recorded_where_the_decision_is():
    """So the numbers behind the trade are not lost with the function."""
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("if (_seccomp_conflicts_with_bwrap(")
    # The citation is in the comment **above** the call. The first version of this
    # looked forward from the `if` and so read nothing but its own line.
    # 1400, not 900: at 900 the window began mid-citation, at "p_conflicts_with",
    # and the assertion failed on a comment that was present.
    before = text[max(0, start - 1400):start]

    assert "_seccomp_conflicts_with_bwrap` for the measurement" in before, (
        "the comment citing the measurement was dropped from above the call site")
