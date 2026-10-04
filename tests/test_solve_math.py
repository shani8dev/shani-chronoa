"""solve_math: SymPy behind an allowlist, and calculate's exact integer functions.

The allowlist is checked without SymPy - it is the part that must hold
whatever SymPy does. The maths runs only where python-sympy is installed
(Arch extra); it was also run against Arch's python-sympy 1.14 in a container.
"""

import pytest

from shani_chronoa.skills import calculate
from shani_chronoa.skills import solve_math as m


@pytest.mark.parametrize("bad", [
    "__import__('os').system('id')", "x.__class__", "().__class__.__bases__", "exec(1)",
    "open(x)", "lambda: 1", "eval(x)", "x; import os", "Symbol('x')", "sympify(x)",
    "getattr(x, y)", "[x for x in y]", "{x}", "x" * 400,
])
def test_anything_but_maths_is_refused_before_sympy(bad):
    with pytest.raises(ValueError):
        m.check(bad)


@pytest.mark.parametrize("ok", ["x^3*sin(x)", "e^(2x)", "(1 + 1/n)^n", "x^2 - 5x + 6 = 0",
                                "sqrt(2)*pi", "2.5*x + 1", "exp(-x^2)", "log(x)/x"])
def test_ordinary_maths_passes_the_allowlist(ok):
    assert m.check(ok) == ok


@pytest.mark.parametrize("expr, want", [
    ("factorial(25)", "15511210043330985984000000"), ("comb(52, 5)", "2598960"),
    ("nCr(10,3)", "120"), ("perm(5, 2)", "20"), ("gcd(48, 180)", "12"), ("lcm(4, 6, 10)", "60"),
])
def test_calculate_integer_functions_are_exact(expr, want):
    assert calculate._run({"expression": expr}) == want


def test_calculate_bounds_huge_factorials():
    assert "limited" in calculate._run({"expression": "factorial(100000)"})


# The maths engine is symengine plus bc, not SymPy (tests/test_solve_math.py
# and shani-pkgbuilds/shani-chronoa/PKGBUILD). These expectations describe what
# that pairing actually guarantees, which is deliberately not everything the old
# SymPy-backed list claimed.
pytest.importorskip("symengine")


@pytest.mark.parametrize("args, want", [
    # exact, from the kernel
    (dict(operation="derivative", expression="x^3"), "3*x^2"),
    (dict(operation="derivative", expression="x^4", order="2"), "12*x^2"),
    (dict(operation="expand", expression="(x+1)^3"), "1 + 3*x + 3*x^2 + x^3"),
    (dict(operation="factor", expression="x^2-4"), "(2 + x)*(-2 + x)"),
    (dict(operation="prime_factors", expression="360"), "2^3 × 3^2 × 5"),
    (dict(operation="is_prime", expression="97"), "a prime"),
    (dict(operation="is_prime", expression="91"), "not a prime"),
    (dict(operation="gcd", expression="12, 18"), "6"),
    (dict(operation="binomial", expression="52, 5"), "2598960"),
    (dict(operation="matrix_det", expression="1 2; 3 4"), "-2"),
    # linear solve is exact
    (dict(operation="solve", expression="2x+4=0"), "x = -2"),
    # numeric, from bc - so approximate rather than a closed form
    (dict(operation="integral", expression="x^2", lower="0", upper="3"), "8.99"),
    (dict(operation="integral", expression="sin(x)", lower="0",
           upper="3.14159265358979"), "1.99"),
    (dict(operation="limit", expression="sin(x)/x", point="0"), ".999"),
    (dict(operation="evaluate", expression="sqrt(2)"), "1.41421"),
])
def test_the_maths(args, want):
    assert want in m._run(args)


@pytest.mark.parametrize("args, why", [
    # A kernel has no polynomial solver, so this DECLINES rather than
    # returning a wrong root. Pinning the decline is the point: it is what
    # separates this engine from one that guesses.
    (dict(operation="solve", expression="x^2 - 5x + 6 = 0"),
     "square root"),
    # and no symbolic antiderivative
    (dict(operation="integral", expression="1/x"),
     "closed-form"),
])
def test_what_the_engine_cannot_do_says_so(args, why):
    out = m._run(args)
    assert ("I could not find a root" in out
            or "I can't find a root" in out
            or "closed-form" in out), out
