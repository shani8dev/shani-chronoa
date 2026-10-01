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


sympy = pytest.importorskip("sympy")


@pytest.mark.parametrize("args, want", [
    (dict(operation="derivative", expression="x^3"), "3*x^2"),
    (dict(operation="integral", expression="sin(x)", lower="0", upper="pi"), "= 2"),
    (dict(operation="limit", expression="sin(x)/x", point="0"), "= 1"),
    (dict(operation="solve", expression="x^2 - 5x + 6 = 0"), "x = 2, 3"),
    (dict(operation="sum", expression="1/n^2", variable="n", lower="1", upper="oo"), "pi^2/6"),
    (dict(operation="prime_factors", expression="360"), "2^3 × 3^2 × 5"),
])
def test_the_maths(args, want):
    assert want in m._run(args)
