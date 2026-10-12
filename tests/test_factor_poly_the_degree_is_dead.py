"""`_factor_poly` computes a degree and never uses it, and the consequence is a
reading rather than a measurement.

**What is certain, and is asserted:** `degree = len(coefficients) - 1` is assigned
and never read anywhere in the body. That is a source fact, not a claim about
behaviour.

**What is a reading, and is recorded rather than asserted:** the loop `return`s
after its **first** rational root - `return root * _from_coefficients(quotient,
symbol)` is inside the `for candidate in sorted(candidates)` loop - and the
unused `degree` was almost certainly meant to drive the number of roots to pull
out. So a cubic with three rational roots would come back as
`(x-1) * (x²-5x+6)` rather than `(x-1)(x-2)(x-3)`.

**Why that is not asserted here:** `symengine` is not installed on any machine this
code has run on, so the whole path is guarded and unmeasured. A stub *was* built to
close that - a dense polynomial with `Symbol`, `Integer`, `expand`, `subs`,
`__int__`, `__eq__` and structure-preserving factors - and it reached the real
`_factor_poly` for real, which is worth knowing. But its own factor bookkeeping
through `sum()` and `__radd__` was lossy, and the counts it produced were
round-number-suspicious in a way that matched the stub's shape rather than the
code's. **A stub whose arithmetic I do not trust is the "probe that cannot fail"
shape**, and asserting against it would have manufactured a measurement.

So: the source fact is pinned, the consequence is written down as a reading with
the reason it is one, and the honest next step is a real `python-symengine` (Arch
package `python-symengine`, which this repo's own `AGENTS.md` records for the Maths
extra) or a slot run - not this file.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import solve_math  # noqa: E402


def _function_source() -> str:
    return inspect.getsource(solve_math._factor_poly)


def _dead_locals(tree: ast.FunctionDef) -> "set[str]":
    """Locals that are assigned and **never read**.

    `assigned - read`, not `read - assigned`: the second subtracts the assigned
    set from the read set, which removes any name that is *both* assigned and
    read - so a local that is used would be reported as dead, and a local that is
    dead would be invisible the moment anything read it. That direction made the
    first version of this test survive its own mutation, which is the only way
    it was caught.
    """
    assigned, read = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                assigned.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                read.add(node.id)
    return assigned - read


class TestTheDegreeIsDead:
    def test_degree_is_assigned_and_never_read(self):
        """**The certain half.** A local that is written and never read is the
        same dead-code shape `estimators.py`'s unused imports are, and pyflakes
        names unused *imports* but not unused *locals*."""
        tree = ast.parse(_function_source()).body[0]
        asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
        degree_assignments = [a for a in asserts
                              if any(isinstance(t, ast.Name) and t.id == "degree"
                                     for t in a.targets)]
        assert degree_assignments, "`degree` is no longer assigned - the defect this file names has moved"
        # **The defect is present, so this asserts it is.** A test that flipped
        # when the defect was fixed would be a test that could not tell the two
        # states apart - it has to say which state it is describing.
        assert "degree" in _dead_locals(tree), (
            "`degree` is no longer dead - the defect this file names has been "
            "fixed, so the reading below should be measured and this file "
            "rewritten as a behavioural test.")

    def test_the_loop_returns_after_the_first_root(self):
        """**The reading, asserted as a property of the control flow** - not as a
        claim about the answer, which cannot be made here.

        A `return` inside the loop over candidates means one root is pulled out,
        however many there are. A loop that continued would need the degree it was
        given.
        """
        # **Raw lines, not stripped ones.** The first version stripped the
        # indentation and then tried to detect a dedent, which can never happen -
        # the check found no `return` and reported the defect as gone while the
        # code had not moved. A check whose parser cannot see the thing it is
        # looking for is the same defect as a check that cannot fail.
        source = _function_source()
        raw = source.splitlines()
        loop_at = next(i for i, l in enumerate(raw)
                       if l.strip().startswith("for candidate in sorted(candidates)"))
        loop_indent = len(loop_line_indent := raw[loop_at])\
            - len(loop_line_indent.lstrip())
        # the loop's body is everything more indented than the `for`
        body = raw[loop_at + 1:]
        end = next((i for i, l in enumerate(body)
                    if l.strip() and
                    (len(l) - len(l.lstrip())) <= loop_indent), len(body))
        returns = [l.strip() for l in body[:end] if l.strip().startswith("return ")]
        assert returns, (
            "the candidate loop no longer returns from inside itself, so more "
            "than one root is being pulled out and this file's subject is gone")
        assert len(returns) == 1, returns

    def test_the_consequence_is_recorded_not_claimed(self):
        """**The docstring carries the reading.** A file whose subject is a
        measurement nobody has made is only honest if it says so, which is why
        the module's own note and this one exist."""
        source = _function_source()
        # the module's own note that the return is deliberately not expanded,
        # which is the sentence that tells a reader the shape is intentional
        note = [l for l in source.splitlines() if "Not expanded" in l]
        assert note, (
            "the module's own note about the return shape is gone, so a reader "
            "would take the expanded form for a defect rather than a choice")


class TestTheMissingDependencyIsNamed:
    def test_symengine_is_absent_and_the_module_says_so(self):
        """The reason this is a reading: the engine is optional and is not here.
        A test that skipped on that would read as coverage, so it is recorded
        instead - and the package that would fix it is named in the schema."""
        try:
            present = True
            __import__("symengine")
        except Exception:
            present = False
        # either state is asserted, because the point is that the *test* knows
        assert present == bool(solve_math.se), (
            "the module's guard and this interpreter disagree about symengine")
        assert not present, (
            "symengine IS installed here - so the behavioural half of this file "
            "should now be measured, and this assertion is what says so")
