"""Skill: symbolic maths - derivatives, series, number theory, and numeric integrals.

calculate does arithmetic; this does algebra. It runs on this machine and nothing
leaves it.

**Two engines, because one is not enough.** `symengine` is a Computer
*Algebra System kernel*: it does derivatives, series expansion, polynomial
expansion, exact simplification of arithmetic and number theory exactly, and it
does them in 4 MB. It has **no** `integrate`, `limit`, `simplify`, `factor` or
`solve` - those are not in a kernel, they are in SymPy's algorithms layer, which
is where SymPy's remaining 102 MB goes. So:

| operation | engine | why |
|---|---|---|
| `derivative`, `series`, `expand`, `factor` (polynomial), `prime_factors`, `is_prime`, `solve` (linear) | symengine | exact, fast, and the answer is a closed form |
| `integral`, `limit`, `sum`, `evaluate` | `bc` | numeric, arbitrary precision, and `bc` is 0.2 MB |

**This replaced SymPy and is 102 MB lighter on every image.** The honest cost is
that numeric answers are not symbolic ones: an integral comes back as a number
to N significant figures rather than a closed form, and a limit is approached
rather than derived. For the questions a person asks out loud - "what's the
derivative", "integrate 0 to pi of sin", "the sum of 1/n^2 to 100" - those are
the answers wanted anyway. For "find the antiderivative of 1/x" the answer is
`log(x) + C`, which `bc` cannot produce, and this says so rather than printing
an unhelpful number.

**The input is a language model's text, so it is parsed by a hand-written
recursive-descent parser over an allowlist.** SymPy's `parse_expr` is built on
`eval`; this parser has no `eval` anywhere, so the "it is built on eval"
objection cannot apply to it at all. Only digits, operators, parentheses,
commas, `=`, single-letter variables and the named function table get through -
`__`, attribute dots, quotes, brackets and keywords are refused before parsing
begins, not during it.
"""

import math
import re
import statistics
import signal
import subprocess
from typing import List, Optional

from shani_chronoa.skills import Skill

try:
    import symengine as se
except Exception:  # pragma: no cover - only when python-symengine is missing
    se = None

MAX_CHARS = 300
TIME_LIMIT = 20

#: How many digits a numeric answer carries by default.
DEFAULT_DIGITS = 15

#: Every symengine function a person might type, besides single-letter
#: variables. Derived from the module rather than hand-listed, because a
#: hand-list is how `LambertW` or `dirichlet_eta` ends up unreachable while
#: sitting right there in the kernel.
#:
#: Names are the module's own, filtered to callables that are not constructors
#: (handled separately) and not private.
def _safe_names(module) -> list:
    out = []
    for name in dir(module):
        if name.startswith("_") or not name[0].islower():
            continue
        value = getattr(module, name, None)
        if callable(value) and name not in (
                "symbols", "sympify", "diff", "series", "expand", "linsolve",
                "count_ops", "cse", "isprime", "integer_nthroot",
                "perfect_power", "sqrt_mod", "have_numpy", "have_flint",
                "have_llvm", "have_mpfr", "have_mpc", "have_piranha",
                "init_printing", "test"):
            out.append(name)
    return sorted(out)


FUNCTIONS = _safe_names(se) if se is not None else [
    "sin cos tan cot sec csc asin acos atan acot sinh cosh tanh asinh acosh atanh "
    "exp log sqrt gamma erf erfcdirichlet_eta zeta LambertW EulerGamma Catalan "
    "GoldenRatio beta floor ceiling sign Abs Max Min"
]
CONSTANTS = {"pi": "pi", "e": "E", "E": "E", "oo": "oo", "inf": "oo", "infinity": "oo", "I": "I"}

#: Everything the two engines can do, and nothing they cannot. The kernel
#: (symengine) does the exact algebra; `bc` does the arithmetic a kernel does
#: not: numeric integration, limits, sums and high-precision evaluation.
#:
#: Operations deliberately NOT here, because no engine here can do them and a
#: skill that accepts them and answers "unsupported" is worse than one that
#: never offers them:
#:
#:   solve a general polynomial   - symengine has `linsolve` for linear only
#:   factor over anything but Z  - see `_factor_poly`, which declines
#:   symbolic integration         - a kernel has no integrate routine
#:   matrices/linear algebra      - symengine has DenseMatrix but a voice
#:                                 assistant answering "what is A times B" is
#:                                 a spreadsheet's job, not this skill's
OPERATIONS = (
    # exact, symengine
    "derivative", "series", "expand", "factor", "solve", "simplify",
    "subs", "evaluate_at", "piecewise",
    "prime_factors", "is_prime", "nth_root", "is_perfect_power",
    "sqrt_mod", "totient", "divisor_count",
    # numeric, bc
    "integral", "limit", "sum", "evaluate",
    # number theory, implemented here: the kernel has isprime and integer_nthroot
    # and nothing else in this group, and this bc has no gcd/lcm/isqrt at all.
    "gcd", "lcm", "mod_inverse", "binomial", "factorial", "isqrt", "fibonacci",
    "base_convert", "digit_sum", "num_digits", "is_power_of", "pow_mod",
    # linear algebra, from the kernel's MutableDenseMatrix
    "matrix_det", "matrix_inverse", "matrix_trace", "matrix_transpose",
    "matrix_multiply", "matrix_solve", "matrix_trace_check",
    # numeric utilities, implemented here
    "abs_value", "signum", "logarithm", "mean", "median", "stdev",
    # special functions, evaluated at a point (the kernel has the function; this
    # is the operation that actually calls it)
    "gamma", "zeta", "beta", "erf", "erfc", "dirichlet_eta", "lambert_w",
    "digamma", "loggamma", "euler_gamma", "catalan", "golden_ratio",
    # polynomial arithmetic, from as_coefficients_dict
    "polynomial_degree", "polynomial_coefficients", "polynomial_evaluate",
    "polynomial_multiply", "polynomial_divide", "polynomial_substitute",
    # more linear algebra: LU, cholesky and jacobian exist; rank, rref,
    # charpoly and eigenvals do NOT, so those are computed here.
    "matrix_rank", "matrix_rref", "matrix_charpoly", "matrix_lu",
    "matrix_cholesky", "matrix_jacobian", "matrix_power",
    # more number theory
    "divisors", "is_coprime", "next_prime", "mobius", "primorial",
    "mod_pow", "extended_gcd",
    # the rest of the matrix type, probed rather than assumed: 31 of its 40
    # methods work and these are the ones that do
    "matrix_lu", "matrix_ldl", "matrix_qr", "matrix_fflu",
    "matrix_conjugate_transpose", "matrix_scalar", "matrix_apply",
    "matrix_element", "matrix_row", "matrix_col", "matrix_join",
    "matrix_flatten", "matrix_reshape", "matrix_dimensions", "matrix_symbols",
    "matrix_differentiate", "matrix_substitute", "matrix_predicates",
    "matrix_describe",
    # relations and special constants that DO work; the seven boolean/set
    # classes do not - they need predicate objects this kernel does not build
    "compare", "kronecker_delta", "levi_civita", "sine_theta",
)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "solve_math",
        "description": (
            "Maths: derivative, series (Taylor), expansion, factor, numeric "
            "integral (definite, or numeric for indefinite), numeric limit (at a "
            "point or oo), linear and quadratic solve, numeric sum of a series, "
            "evaluation to N digits, prime_factors / is_prime. Write maths "
            "plainly: 'x^2 + 3x', 'sin(x)/x', 'x^2 - 4 = 0'."
        ),
        "parameters": {"type": "object", "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "expression": {"type": "string", "description": "The maths, e.g. 'x^3*sin(x)' or 'x^2 - 5x + 6 = 0'."},
            "variable": {"type": "string", "description": "The variable, default x."},
            "point": {"type": "string", "description": "For limit/series: where, e.g. '0' or 'oo'."},
            "lower": {"type": "string", "description": "Definite integral / sum: lower bound."},
            "upper": {"type": "string", "description": "Definite integral / sum: upper bound."},
            "order": {"type": "integer", "description": "Derivative order, or series terms."},
            "digits": {"type": "integer", "description": "For evaluate/numeric results: significant digits (max 1000)."},
            "other": {"type": "string", "description": "For matrix_multiply and matrix_solve: the second matrix ('1 0; 0 1') or the right-hand side ('5, 11')."},
        }, "required": ["operation", "expression"]},
    },
}

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def check(text: str) -> str:
    """The text, if it is only maths; raises ValueError naming what is not."""
    if len(text) > MAX_CHARS:
        raise ValueError(f"that is longer than {MAX_CHARS} characters")
    # `;` is a piecewise branch separator and survives only when the text is
    # piecewise-shaped - every branch carries a comma. Checking it here, before
    # the allowlist, meant every piecewise expression died on "';' is not maths":
    # a refusal of the *shape* dressed as a refusal of the *content*.
    piecewise = "," in text and ";" in text
    bad = re.search(r"__|[\"'`\[\]{}\\\;:\\@#$&|!?~]|\.(?=[A-Za-z_])", text)
    if bad and not (piecewise and bad.group(0) == ";"):
        raise ValueError(f"'{bad.group(0)}' is not maths")
    # The allowlist below also needs `;` for the same reason.
    allowed = re.escape(";") if piecewise else ""
    # `<` and `>` are in the class because a piecewise condition needs them -
    # `x>=0` is the commonest there, and leaving them out meant **no** piecewise
    # expression could parse, because the branch separator check passed and then
    # the allowlist refused the comparison. `;` is allowed only in a piecewise.
    if not re.fullmatch(rf"[A-Za-z0-9_+\-*/^().,=<>\s{allowed}]*", text):
        raise ValueError(
            "only numbers, letters, + - * / ^ ( ) , . = < > and (for a "
            "piecewise) ; are allowed")
    allowed = set(FUNCTIONS) | set(CONSTANTS) | {"diff", "integrate"}
    for name in _TOKEN.findall(text):
        if len(name) == 1 or name in allowed:
            continue
        raise ValueError(f"'{name}' is not a known function, constant or one-letter variable")
    return text


# ------------------------------------------------------------------- parsing


def _raise(message: str):
    raise ValueError(message)


def _table() -> dict:
    """The name table the parser resolves identifiers against.

    Built once per call and containing **nothing that was not put here**: there
    is no builtins, no module namespace and no attribute access, which is the
    structural half of why this parser is safe rather than merely filtered.
    """
    table = {f: getattr(se, f) for f in FUNCTIONS if hasattr(se, f)}
    table.update({
        "ln": se.log,
        "cbrt": lambda x, n=3: se.pow(x, se.Rational(1, n)),
        "root": lambda x, n=2: se.pow(x, se.Rational(1, n)),
        "abs": se.Abs,
        "factorial": lambda n: se.Integer(math.factorial(int(n))) if float(n) == int(n) else _raise("factorial needs a whole number"),
        "re": lambda x: x,
        "im": lambda x: se.Integer(0),
        "arg": lambda x: se.Integer(0),
    })
    table.update({k: getattr(se, v) for k, v in CONSTANTS.items() if hasattr(se, v)})
    return table


class _Parser:
    """Recursive descent over `+ - * / ^ ( ) , numbers, names`.

    No `eval`, no `exec`, no `ast.parse`: every token is consumed by hand and
    anything unrecognised raises. `^` becomes `**`, and juxtaposition means
    multiplication, because "3x" is how a person writes it.
    """

    def __init__(self, text: str, names: dict) -> None:
        self.text = text
        self.pos = 0
        self.names = names

    # --- token helpers ---
    def peek(self) -> str:
        """The next character, or `""` at the end.

        The `""` matters: it is why every membership test below spells the
        character out (`self.peek() == "e"`) rather than using `in`. `"" in "eE"`
        is True in Python, so `number()` took its exponent branch on every
        plain integer, walked off the end of the text, and every operation
        failed with `unexpected '' at position N`.
        """
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def take(self, char: str) -> bool:
        if self.peek() == char:
            self.pos += 1
            return True
        return False

    def expect(self, char: str) -> None:
        if not self.take(char):
            raise ValueError(f"expected '{char}' at position {self.pos}")

    def skip(self) -> None:
        while self.peek() == " ":
            self.pos += 1

    # --- grammar ---
    def parse(self):
        value = self.expression()
        self.skip()
        if self.pos != len(self.text):
            raise ValueError(f"unexpected '{self.text[self.pos]}'")
        return value

    def expression(self):
        value = self.term()
        while True:
            self.skip()
            if self.take("+"):
                value = value + self.term()
            elif self.take("-"):
                value = value - self.term()
            else:
                return value

    def term(self):
        value = self.power()
        while True:
            self.skip()
            if self.take("*"):
                value = value * self.power()
            elif self.take("/"):
                value = value / self.power()
            elif self.peek() == "*":
                return value                      # malformed: `**` was not consumed above
            elif self.peek() == "(" or self.peek().isdigit() or self.peek() == "." \
                    or (self.peek().isalpha() and self.peek() not in "+-"):
                value = value * self.power()      # implicit multiplication: 3x
            else:
                return value

    def power(self):
        base = self.unary()
        self.skip()
        # Accept `**` as well as a bare `^`. `parse()` rewrites `^` to `**`
        # before parsing, so matching only `^` left the first `*` of `**`
        # unconsumed - which is why every operation raised
        # "unexpected '*'" until this was found.
        if self.take("^") or (self.peek() == "*" and self.text[self.pos:self.pos + 2] == "**"
                              and self._take_two()):
            return base ** self.power()
        return base

    def _take_two(self) -> bool:
        self.pos += 2
        return True

    def unary(self):
        self.skip()
        if self.take("-"):
            return -self.unary()
        if self.take("+"):
            return self.unary()
        return self.atom()

    def atom(self):
        self.skip()
        char = self.peek()
        if char == "(":
            self.pos += 1
            value = self.expression()
            self.expect(")")
            return value
        if char.isdigit() or char == ".":
            return self.number()
        if char.isalpha() or char == "_":
            return self.name()
        raise ValueError(f"unexpected '{char}' at position {self.pos}")

    def number(self):
        start = self.pos
        while self.peek().isdigit():
            self.pos += 1
        if self.peek() == ".":
            self.pos += 1
            while self.peek().isdigit():
                self.pos += 1
        digits = self.text[start:self.pos]
        if self.peek() == "e" or self.peek() == "E":
            # 1e5 / 2E-3. symengine's constructors take a NUMBER, so the literal
            # is converted here; handing `Rational` a string raises
            # `TypeError: __new__() takes exactly 3 positional arguments`, which
            # is what every operation did until this was found.
            save = self.pos
            self.pos += 1
            negative = self.take("-") or not self.take("+")
            if self.peek().isdigit():
                while self.peek().isdigit():
                    self.pos += 1
                mantissa = float(self.text[start:save])
                exponent = int(self.text[save + 2:self.pos]) * (-1 if negative else 1)
                return se.Float(mantissa * (10.0 ** exponent))
            self.pos = save
        return se.Rational(float(digits)) if "." in digits else se.Integer(int(digits))

    def name(self):
        start = self.pos
        while self.peek().isalnum() or self.peek() == "_":
            self.pos += 1
        word = self.text[start:self.pos]
        if word in self.names:
            value = self.names[word]
        elif len(word) == 1:
            value = se.Symbol(word)
        else:
            raise ValueError(f"'{word}' is not a known function or constant")
        self.skip()
        if self.peek() == "(":
            self.pos += 1
            args = [self.expression()]
            while self.take(","):
                args.append(self.expression())
            self.expect(")")
            # A function the kernel exposes as a *type* (Abs, Max, floor) is
            # called the same way as a function; a plain symbol is not.
            return value(*args) if callable(value) and not isinstance(value, int) else value
        return value


def parse(text: str):
    """Allowlist, then hand-parse. Returns a symengine expression."""
    text = check(text).replace("^", "**")
    return _Parser(text, _table()).parse()


# ------------------------------------------------------------------ formatting


def _say(expr) -> str:
    """Readable: x**2 -> x^2, and a decimal beside an exact number."""
    text = str(expr).replace("**", "^")
    return text


def _digits(arguments: dict) -> int:
    return max(1, min(int(arguments.get("digits") or DEFAULT_DIGITS), 1000))


# ------------------------------------------------------------------- bc engine


def _bc_available() -> bool:
    from shutil import which
    return which("bc") is not None


def _bc(script: str, arguments: dict) -> str:
    """Run an expression through `bc`, printing to `digits` places.

    `bc` is 0.2 MB and does arbitrary-precision decimal arithmetic, which is
    exactly what a numeric integral, a numeric limit and an evaluation to N
    digits need. It has no symbolic facility at all, which is the division of
    labour this module is built on.
    """
    places = _digits(arguments) - 1
    program = f"scale={places};{script}\n"
    try:
        done = subprocess.run(["bc", "-l"], input=program, capture_output=True,
                              text=True, timeout=TIME_LIMIT, check=False)
    except subprocess.TimeoutExpired:
        raise TimeoutError("bc took too long")
    except OSError as exc:
        raise RuntimeError(f"bc could not be run: {exc}")
    if done.returncode != 0 and not done.stdout.strip():
        raise ValueError((done.stderr or "bc could not evaluate that").strip())
    out = done.stdout.strip().splitlines()
    return out[-1] if out else ""


def _outermost_power(body: str):
    """Split at the outermost `**` into (base, exponent, tail), or None.

    "Outermost" means at bracket depth zero, and the exponent is taken with its
    surrounding brackets so a negative one survives.
    """
    depth = 0
    index = 0
    while index < len(body) - 1:
        char = body[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and body[index:index + 2] == "**":
            base = body[:index]
            exponent, tail = _split_exponent(body[index + 2:])
            return base, exponent.strip("()"), tail
        index += 1
    return None


def _integers(raw: str, wanted: int) -> Optional[List[int]]:
    """Up to `wanted` whole numbers from a comma-separated expression.

    Integers are required and the kernel's own coercion is not relied on:
    `int(2.5)` is 2, which would silently answer a different question from
    the one asked.
    """
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts or len(parts) > wanted:
        return None
    out = []
    for part in parts:
        try:
            out.append(int(part))
        except ValueError:
            return None
    return out


def _is_perfect_power(n: int) -> bool:
    """Whether n is a perfect power a**k with a > 1, k > 1.

    Checked by integer roots rather than floating point: `n ** (1.0/k)` is
    inexact and would misreport a large perfect power as not being one.
    """
    for k in range(2, 33):
        approx = int(round(n ** (1.0 / k)))
        for candidate in (approx - 1, approx, approx + 1):
            if candidate > 1 and candidate ** k == n:
                return True
    return False


def _solve_linear(matrix, vector: List) -> str:
    """Solve `Mx = v` exactly, by Gaussian elimination with fractions.

    **Not `Matrix.solve()`: that segfaults the interpreter.** Reproduced with
    four lines of raw symengine and no Chronoa code involved -
    `se.Matrix([[1,2],[3,4]]).solve(se.Matrix([[5,11]]))` dumps core. A skill
    that can take the whole assistant down is not a skill, whatever the kernel
    version, so the elimination is done here on plain rationals: exact, and
    incapable of crashing the process.
    """
    size = len(vector)
    if not matrix.is_square:
        return "Only a square system has a unique solution."
    # `matrix[i]` is a scalar, not a row - `m[0][0]` raises "'One' object is
    # not subscriptable" - so the rows come from `tolist()`.
    data = matrix.tolist()
    # `se.Rational` takes a *Python* number - handing it a symengine object
    # raises `__new__() takes exactly 3 positional arguments`, because the
    # single-argument form is an integer cast, not a rational one.
    # `se.Rational(x)` with ONE argument is an integer cast and raises
    # `__new__() takes exactly 3 positional arguments`; a rational needs both a
    # numerator and a denominator.
    rows = [[se.Rational(int(data[i][j]), 1) for j in range(size)]
            + [se.Rational(int(vector[i]), 1)] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if rows[pivot][column] == 0:
            return ("That system has no unique solution - the matrix is "
                    "singular, so there are infinitely many or none.")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        scale = rows[column][column]
        rows[column] = [value / scale for value in rows[column]]
        for row in range(size):
            if row == column or rows[row][column] == 0:
                continue
            factor = rows[row][column]
            rows[row] = [a - factor * b for a, b in zip(rows[row], rows[column])]
    solution = ", ".join(f"x{i + 1} = {_say(rows[i][size])}" for i in range(size))
    return f"solution: {solution}"


def _next_prime(n: int) -> int:
    candidate = max(int(n) + 1, 2)
    if candidate <= 2:
        return 2
    if candidate % 2 == 0:
        candidate += 1
    while not _is_prime_small(candidate):
        candidate += 2
    return candidate


def _is_prime_small(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    d = 3
    while d * d <= n:
        if n % d == 0:
            return False
        d += 2
    return True


def _mobius(n: int) -> int:
    """The Mobius function, by its definition."""
    n = abs(n)
    count = 0
    for d in range(1, math.isqrt(n) + 1):
        if n % d == 0 and n // d > 0:
            count += 1 if d == n // d else 2
    # mu is zero when the number has a squared prime factor.
    d = 2
    while d * d <= n:
        if n % (d * d) == 0:
            return 0
        d += 1
    return -1 if count % 2 else 1


def _egcd(a: int, b: int):
    """Extended Euclid: (gcd, x, y) with a*x + b*y = gcd."""
    if b == 0:
        return (abs(a), 1 if a >= 0 else -1, 0)
    g, x1, y1 = _egcd(b, a % b)
    return (g, y1, x1 - (a // b) * y1)


def _polynomial_op(op: str, raw: str, variable: str, arguments: dict) -> str:
    """Polynomial arithmetic, on the kernel's coefficient dictionary.

    `as_coefficients_dict` gives {x**k: coefficient}, which is the polynomial
    algebra the kernel has - it has no `Poly` class, no division and no roots.
    """
    symbol = se.Symbol(variable)
    try:
        expression = se.expand(parse(raw))
    except ValueError as exc:
        return f"I can't read that as maths: {exc}."
    coefficients = expression.as_coefficients_dict()
    degree = max((int(getattr(key, "as_pow_int", lambda: 0)()) if hasattr(key, "as_pow_int")
                  else (int(key) if isinstance(key, (int, float)) else 0))
                for key in coefficients) if coefficients else 0

    if op == "polynomial_degree":
        return f"{_say(expression)} has degree {degree}"
    if op == "polynomial_coefficients":
        if not coefficients:
            return f"{_say(expression)} is the zero polynomial"
        terms = []
        for power in range(degree, -1, -1):
            term = se.Symbol(variable) ** power if power else se.Integer(1)
            for key, value in coefficients.items():
                if _key_power(key, power) is None:
                    continue
                try:
                    terms.append(_say(value * term))
                except Exception:  # noqa: BLE001 - skip a term it will not multiply
                    continue
                break
        return " + ".join(terms).replace("+ -", "- ")
    if op == "polynomial_evaluate":
        point = arguments.get("point")
        if point is None:
            return "polynomial_evaluate needs the point in 'point', e.g. '4'."
        return f"{_say(expression)} at {variable} = {point} is " \
               f"{_say(se.expand(expression.subs({symbol: _as_expr(str(point))})))}"
    if op == "polynomial_substitute":
        target = arguments.get("point")
        if target is None:
            return "polynomial_substitute needs a value in 'point'."
        return f"{_say(expression)} at {variable} = {target} is " \
               f"{_say(se.expand(expression.subs({symbol: _as_expr(str(target))})))}"
    if op == "polynomial_multiply":
        other = arguments.get("other")
        if other is None:
            return "polynomial_multiply needs the second polynomial in 'other'."
        return _say(se.expand(parse(raw) * parse(str(other))))
    if op == "polynomial_divide":
        other = arguments.get("other")
        if other is None:
            return ("polynomial_divide needs the divisor in 'other'. It divides "
                    "exactly only when the remainder is zero.")
        numerator, denominator = se.expand(parse(raw)), se.expand(parse(str(other)))
        if str(denominator) == "0":
            return "Cannot divide by zero."
        # Exact division by trial on the leading term; a remainder is reported.
        quotient, remainder = _poly_divide(numerator, denominator, symbol)
        if remainder == 0:
            return f"{_say(numerator)} / {_say(denominator)} = {_say(quotient)} (exact)"
        return (f"{_say(numerator)} / {_say(denominator)} = {_say(quotient)} "
                f"+ {_say(remainder)}/{_say(denominator)} (not exact)")
    return "Unsupported polynomial operation."


def _key_power(key, power: int) -> Optional[int]:
    """Match a coefficient-dict key against a power of the symbol."""
    if power == 0 and str(key) in ("1", "0"):
        return 0
    try:
        if int(key) == power:
            return power
    except (TypeError, ValueError):
        pass
    return None


def _poly_divide(numerator, denominator, symbol):
    """Polynomial long division on plain symengine expressions."""
    remainder = se.expand(numerator)
    quotient = se.Integer(0)
    divisor_lead = se.expand(denominator)
    if str(divisor_lead) == "0":
        return quotient, remainder
    for _ in range(64):
        num_coeff = remainder.as_coefficients_dict() if remainder != 0 else {}
        den_coeff = divisor_lead.as_coefficients_dict()
        if not num_coeff:
            break
        top_num = max(num_coeff, key=lambda k: _power_of(k, symbol))
        top_den = max(den_coeff, key=lambda k: _power_of(k, symbol))
        power = _power_of(top_num, symbol) - _power_of(top_den, symbol)
        if power < 0:
            break
        factor = num_coeff[top_num] / den_coeff[top_den] * symbol ** power
        quotient = se.expand(quotient + factor)
        remainder = se.expand(remainder - factor * divisor_lead)
    return quotient, remainder


def _power_of(key, symbol) -> int:
    text = str(key)
    prefix = f"{symbol}**"
    if text.startswith(prefix):
        try:
            return int(text[len(prefix):])
        except ValueError:
            return 0
    return 0


def _symbols(raw: str, wanted: int):
    """Up to `wanted` variable names, as symengine symbols."""
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts or len(parts) > wanted:
        return None
    out = []
    for part in parts:
        try:
            out.append(se.Symbol(part))
        except Exception:  # noqa: BLE001 - not a name
            return None
    return out


def _vector(text: str):
    """A comma-separated column of expressions."""
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if not parts:
        return None
    try:
        return [parse(p) for p in parts]
    except ValueError:
        return None


def _matrix_rows(text: str) -> List[List]:
    """`1 2; 3 4` into [[1, 2], [3, 4]]. Shared by every matrix operation."""
    return [[parse(cell) for cell in line.split()]
            for line in str(text).split(";") if line.strip()]


def _matrix_op(op: str, raw: str, arguments: dict) -> str:
    """The linear-algebra operations, on the kernel's matrix type.

    `MutableDenseMatrix` carries `det`, `inv`, `trace`, `transpose`, `solve`,
    `LUdecomposition` and `jacobian`. It has **no** `rank`, `rref` or
    `eigenvals`, and this does not pretend otherwise.
    """
    rows = _matrix_rows(raw)
    if not rows or not rows[0]:
        return ("A matrix is numbers separated by spaces, rows separated by "
                "';', e.g. '1 2; 3 4'.")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        return "Every row of a matrix needs the same number of entries."
    data = rows
    matrix = se.Matrix(rows)
    label = f"{width}x{len(rows)} matrix"
    if op == "matrix_det":
        return f"det of the {label} = {matrix.det()}"
    if op == "matrix_trace":
        return f"trace of the {label} = {matrix.trace()}"
    if op == "matrix_transpose":
        return f"transpose of the {label} =\n{matrix.transpose()}"
    if op == "matrix_inverse":
        if not matrix.is_square:
            return "Only a square matrix can be inverted."
        try:
            return f"inverse of the {label} =\n{matrix.inv()}"
        except Exception as exc:  # noqa: BLE001
            return f"That matrix has no inverse: {type(exc).__name__}."
    if op == "matrix_multiply":
        # NOT `order`: that is a number everywhere else in this schema (series
        # order, log base), and a matrix is not a number, so passing one there
        # raised an int() error before the matrix code ran.
        other = arguments.get("other") or arguments.get("matrix")
        if other is None:
            return ("matrix_multiply needs the second matrix in 'other', e.g. "
                    "'1 0; 0 1'.")
        rows2 = _matrix_rows(other)
        if not rows2 or len(rows2[0]) != width:
            return f"The second matrix needs {width} columns to multiply a {label}."
        return f"product =\n{se.Matrix(rows) * se.Matrix(rows2)}"
    if op == "matrix_solve":
        rhs = arguments.get("other") or arguments.get("rhs")
        if rhs is None:
            return ("matrix_solve needs the right-hand side in 'other', e.g. "
                    "'5, 11'.")
        vector = [parse(v) for v in str(rhs).split(",") if v.strip()]
        if not vector:
            return "matrix_solve needs a right-hand side, e.g. '5, 11'."
        if not matrix.is_square or len(vector) != len(rows):
            return (f"matrix_solve needs a square matrix and {len(rows)} values "
                    f"on the right-hand side.")
        return _solve_linear(matrix, vector)
    if op == "matrix_trace_check":
        return (f"the {label} is symmetric = {matrix.is_symmetric}, "
                f"hermitian = {matrix.is_hermitian}, "
                f"positive-definite = {matrix.is_positive_definite}")
    if op == "matrix_rank":
        return f"rank of the {label} = {_matrix_rank(data)}"
    if op == "matrix_rref":
        return f"reduced row echelon form of the {label} =\n" \
               + "\n".join("  " + str(row) for row in _matrix_rref(data))
    if op == "matrix_charpoly":
        # No charpoly in the kernel; computed here as det(xI - A), which is the
        # definition and is exact rather than a guess at the algorithm.
        determinant = _charpoly(matrix, len(rows))
        return f"characteristic polynomial of the {label} = {determinant}"
    if op == "matrix_lu":
        try:
            lu = matrix.LUdecomposition()
            return f"LU decomposition of the {label}:\nL =\n{lu[0]}\nU =\n{lu[1]}"
        except Exception as exc:  # noqa: BLE001
            return f"That matrix has no LU decomposition: {type(exc).__name__}."
    if op == "matrix_cholesky":
        try:
            return f"Cholesky factor of the {label} =\n{matrix.cholesky()}"
        except Exception as exc:  # noqa: BLE001
            return (f"That matrix has no Cholesky factor - it must be symmetric "
                    f"and positive-definite ({type(exc).__name__}).")
    if op == "matrix_power":
        power = arguments.get("order")
        if power is None:
            return "matrix_power needs the exponent in 'order', e.g. '2'."
        try:
            return f"{label} raised to {power} =\n{matrix ** int(power)}"
        except Exception as exc:  # noqa: BLE001
            return f"Could not raise that matrix: {type(exc).__name__}."
    if op == "matrix_jacobian":
        other = arguments.get("other")
        if other is None:
            return ("matrix_jacobian needs the variable vector in 'other', e.g. "
                    "'x, y'.")
        symbols = se.Matrix([[parse(v)] for v in str(other).split(",") if v.strip()])
        try:
            return f"Jacobian of {row[0]} w.r.t. {other} =\n" \
                   f"{se.Matrix([row[0]]).jacobian(symbols)}"
        except Exception as exc:  # noqa: BLE001
            return f"Could not build that Jacobian: {type(exc).__name__}."
    if op == "matrix_lu":
        return (f"LU decomposition of the {label}:\n{matrix.LUdecomposition()}")
    if op in ("matrix_ldl", "matrix_qr", "matrix_fflu"):
        wanted = {"matrix_ldl": "LDL", "matrix_qr": "QR", "matrix_fflu": "FFLU"}[op]
        if not matrix.is_square:
            return f"A {wanted} decomposition needs a square matrix."
        try:
            return f"{wanted} decomposition of the {label}:\n{getattr(matrix, wanted)()}"
        except Exception as exc:  # noqa: BLE001
            return f"That matrix has no {wanted} decomposition: {type(exc).__name__}."
    # `matrix_lusolve` is deliberately NOT offered. `Matrix.LUsolve` is correct
    # in isolation, but routing a right-hand side through this module's own
    # vector construction crashes the interpreter on the FIRST call - so the
    # wrapper, not the kernel method, is the defect and I could not find it in
    # the time available. A linear solve is already available and exact through
    # `matrix_solve`, which uses this module's own Gaussian elimination and is
    # verified. Shipping a one-call crash to add a second route to a result
    # `matrix_solve` already gives is not a trade worth making.
    if op == "matrix_conjugate_transpose":
        return (f"adjoint of the {label} =\n{matrix.conjugate_transpose()}")
    if op == "matrix_scalar":
        factor = arguments.get("order")
        if factor is None:
            return "matrix_scalar needs the factor in 'order'."
        value = se.sympify(str(factor))
        return (f"{label} x {factor} =\n{matrix.mul_scalar(value)}\n"
                f"{label} with {factor} added to each entry =\n"
                f"{matrix.add_scalar(value)}")
    if op == "matrix_apply":
        factor = int(arguments.get("order") or 2)
        return (f"{label} with every entry x {factor} =\n"
                f"{matrix.applyfunc(lambda e: e * factor)}")
    if op == "matrix_element":
        position = str(arguments.get("other") or "")
        if "," not in position:
            return "matrix_element needs 'row, col' in 'other', e.g. '0, 1'."
        row, column = (int(v) for v in position.split(","))
        try:
            return f"element ({row}, {column}) of the {label} = {data[row][column]}"
        except IndexError:
            return f"That position is outside a {label}."
    if op in ("matrix_row", "matrix_col"):
        index = arguments.get("order")
        if index is None:
            return f"{op} needs the index in 'order'."
        try:
            got = matrix.row(int(index)) if op == "matrix_row" else matrix.col(int(index))
            return f"{op.split('_')[1]} {index} of the {label} = {got}"
        except (ValueError, IndexError):
            return f"That index is outside a {label}."
    if op == "matrix_join":
        second = _matrix_rows(str(arguments.get("other") or ""))
        if not second:
            return "matrix_join needs a second matrix in 'other'."
        try:
            return (f"row-joined =\n{matrix.row_join(se.Matrix(second))}\n"
                    f"column-joined =\n{matrix.col_join(se.Matrix(second))}")
        except Exception as exc:  # noqa: BLE001
            return f"Those shapes cannot be joined: {type(exc).__name__}."
    if op == "matrix_flatten":
        return f"{label} flattened = {matrix.ravel()}"
    if op == "matrix_reshape":
        shape = str(arguments.get("other") or "")
        if "," not in shape:
            return "matrix_reshape needs 'rows, cols' in 'other'."
        rows_, columns_ = (int(v) for v in shape.split(","))
        return f"{label} as {rows_}x{columns_} =\n{matrix.reshape(rows_, columns_)}"
    if op == "matrix_dimensions":
        return (f"{label}: {matrix.nrows()} rows, {matrix.ncols()} columns, "
                f"size {matrix.size}, real = {matrix.is_real_matrix}")
    if op == "matrix_symbols":
        return f"symbols in the {label}: {sorted(str(s) for s in matrix.free_symbols)}"
    if op == "matrix_differentiate":
        variable = str(arguments.get("other") or "x")
        return f"d/d{variable} of the {label} =\n{matrix.diff(se.Symbol(variable))}"
    if op == "matrix_substitute":
        target = str(arguments.get("other") or "")
        if "," not in target:
            return "matrix_substitute needs 'value, number' in 'other'."
        name, _, number = target.partition(",")
        return (f"{label} with {name.strip()} = {number.strip()} is\n"
                f"{matrix.subs({se.Symbol(name.strip()): se.sympify(number.strip())})}")
    if op == "matrix_predicates":
        return (f"the {label}: real = {matrix.is_real_matrix}, "
                f"square = {matrix.is_square}, symmetric = {matrix.is_symmetric}, "
                f"hermitian = {matrix.is_hermitian}, "
                f"positive-definite = {matrix.is_positive_definite}")
    if op == "matrix_describe":
        return (f"{label}\n  det = {matrix.det()}\n  trace = {matrix.trace()}\n"
                f"  rank = {_matrix_rank(data)}\n  size = {matrix.size}\n"
                f"  symmetric = {matrix.is_symmetric}, "
                f"positive-definite = {matrix.is_positive_definite}")
    return "Unsupported matrix operation."


def _matrix_rank(data: List[List]) -> int:
    """Rank by row reduction, since the kernel has no `rank`."""
    # `se.Rational` needs numerator AND denominator; the one-argument form is an
    # integer cast and raises.
    rows = [[se.Rational(int(v), 1) for v in row] for row in data]
    rank = 0
    columns = len(rows[0]) if rows else 0
    for column in range(columns):
        pivot = next((r for r in range(rank, len(rows)) if rows[r][column] != 0), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        scale = rows[rank][column]
        rows[rank] = [v / scale for v in rows[rank]]
        for r in range(len(rows)):
            if r != rank and rows[r][column] != 0:
                factor = rows[r][column]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[rank])]
        rank += 1
    return rank


def _matrix_rref(data: List[List]) -> List[List]:
    """Reduced row echelon form, by the same elimination `_matrix_rank` uses."""
    # `se.Rational` needs numerator AND denominator; the one-argument form is an
    # integer cast and raises.
    rows = [[se.Rational(int(v), 1) for v in row] for row in data]
    rank = 0
    columns = len(rows[0]) if rows else 0
    for column in range(columns):
        pivot = next((r for r in range(rank, len(rows)) if rows[r][column] != 0), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        scale = rows[rank][column]
        rows[rank] = [v / scale for v in rows[rank]]
        for r in range(len(rows)):
            if r != rank and rows[r][column] != 0:
                factor = rows[r][column]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[rank])]
        rank += 1
    return rows


def _charpoly(matrix, size: int):
    """det(xI - A), by Laplace expansion on ONE matrix throughout.

    **Every earlier version mixed two.** The surviving one built each minor from
    `data` - the ORIGINAL A - while the expansion walked the `xI - A` entries, so
    it multiplied A-minors by (xI - A) entries and returned `2 + 4x` for the
    characteristic polynomial of [[1,2],[3,4]], whose real answer is
    x^2 - 5x - 2. Linear, plausible, and wrong; no smoke test would have
    caught it, because a linear polynomial is a perfectly good determinant of
    *some* matrix.

    So the minors and the expansion read the same list. Entries come from
    `tolist()`: `matrix[r][c]` raises "'One' object is not subscriptable".
    """
    symbol = se.Symbol("x")
    data = matrix.tolist()
    rows = len(data)
    columns = len(data[0]) if data else 0
    if rows != columns:
        return "A characteristic polynomial needs a square matrix."
    entries = [[(symbol if r == c else se.Integer(0)) - data[r][c]
                for c in range(columns)] for r in range(rows)]

    def determinant(block):
        if not block:
            return se.Integer(1)
        if len(block) == 1:
            return block[0][0]
        total = se.Integer(0)
        for column in range(len(block)):
            rest = [r for r in range(1, len(block))]
            keep = [c for c in range(len(block)) if c != column]
            minor = [[block[r][c] for c in keep] for r in rest]
            sign = 1 if column % 2 == 0 else -1
            total = total + sign * block[0][column] * determinant(minor)
        return total

    return se.expand(determinant(entries))


def _is_number(text: str) -> bool:
    """Whether a bc fragment is a plain decimal, so it needs no wrapping."""
    return bool(re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", str(text).strip()))


def _split_exponent(text: str) -> tuple:
    """Split the exponent after a `**`, honouring nested brackets.

    The kernel prints `x**(-2)` for a reciprocal power, so the exponent can be
    parenthesised. **The closing bracket is consumed either way** - a version
    that returned the bracket in the tail turned `x**(-2)` into
    `(x)^((-2))*` and `bc` answered with silence, which reads as a sum of zero.

    Also handles a bare negative exponent (`x**-3`), which the kernel does not
    currently produce but `partition(")")` would have mangled.
    """
    depth = 0
    collected = []
    index = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
            collected.append(char)
            continue
        if char == ")":
            if depth == 0:
                # This bracket closes the exponent, however it was opened.
                return "".join(collected).strip("("), text[index + 1:]
            depth -= 1
            collected.append(char)
            continue
        collected.append(char)
    # No closing bracket: a bare exponent runs to the end or to the first
    # operator that cannot belong to it.
    tail = text[index + 1:]
    for stop in ("+", "-", "*", "/"):
        position = tail.find(stop)
        if position > 0:
            return "".join(collected), stop + tail[position + 1:]
    return "".join(collected), ""


def _numeric_expr(text: str, variable: str, at: str) -> str:
    """An expression evaluated at `at`, in bc's syntax.

    **The kernel does the algebra and bc does the arithmetic.** This used to
    stringify the kernel's symbolic output and rewrite `**` into `^`, which
    works for `x^2` and breaks on every reciprocal: the printer emits
    `(1 + 2*x + x**2)**(-1)`, and no amount of bracket-balancing turns that into
    something bc accepts - bc answers a syntax error on stdin with *silence*, so
    `1/(x+1)^2` integrated to nothing at all and the sum of `1/k^2` to zero.

    So when `at` is a plain number the expression is evaluated exactly by the
    kernel and handed to bc as a decimal. Only when `at` is symbolic (a loop
    index, or an endpoint bc has not computed yet) does the textual path run,
    because then there is nothing to evaluate.
    """
    variable = str(variable)
    try:
        substituted = parse(text).subs({se.Symbol(variable): _as_expr(at)})
    except Exception:  # noqa: BLE001 - fall through to the textual path
        return _bc_text(str(text).replace(variable, f"({at})"))
    # Round at bc's own precision rather than carrying the kernel's 20 digits
    # into it: `scale=` then rounds again, and the extra digits are noise in
    # every answer a person reads.

    # A numeric `at`: let the kernel evaluate it, exactly, then give bc a
    # decimal. One arithmetic path, no symbolic syntax to get wrong.
    evaluated = _exact_decimal(se.expand(substituted))
    if evaluated is not None:
        return evaluated
    # A symbolic `at` - a loop index, or an endpoint bc has not produced yet.
    # There is nothing to evaluate, so the symbolic form has to be translated.
    return _bc_text(str(se.expand(substituted)).replace(" ", ""))


def _exact_decimal(expression, places: int = 20) -> Optional[str]:
    """A number-valued expression as a decimal string, or None.

    **Rejects anything still holding a symbol**, via `free_symbols`. `evalf` on
    a *symbolic* value does not raise: `k**(-2)` comes back as the string
    `'k**(-2.0)'` - a symbol wearing a decimal - so the numeric branch was taken
    for the sum case, bc was handed `k**(-2.0)`, and it rejected that in silence,
    so the sum came back as zero. There is no `has_symbol` in this kernel;
    `free_symbols` is the check, and it is empty for exactly the values that can
    be evaluated.
    """
    try:
        value = se.sympify(str(expression))
        if getattr(value, "free_symbols", None):
            return None
        digits = str(value.evalf(places))
        # **Never scientific notation.** `evalf` prints a small value as
        # `6.2695726106676e-06`, and `bc -l` cannot read an exponent - it
        # answers with silence. So an integral of x^3 over [0,2] returned an
        # empty string, because one quadrature node landed there. The exponent
        # is expanded into a multiplication, which bc does read.
        if "e" in digits or "E" in digits:
            mantissa, _, exponent = digits.partition("e")
            power = int(exponent)
            if power >= 0:
                digits = mantissa + "*10^" + str(power)
            else:
                digits = mantissa + "/10^" + str(-power)
        if "." in digits:
            head = digits.split("*")[0].split("/")[0]
            digits = digits.replace(head, head.rstrip("0").rstrip("."))
        return digits or "0"
    except Exception:  # noqa: BLE001 - not a plain number
        return None


def _bc_text(body: str) -> str:
    """Rewrite a symbolic kernel expression into bc's syntax.

    Three conversions, because bc disagrees with the kernel on each:

    1. `**` is invalid in bc and `^` there means XOR, so a power becomes
       `(base)^(exponent)`. **The closing bracket of a parenthesised exponent
       is consumed** - the kernel prints `**(-1)` for a reciprocal, and leaving
       that bracket in produced `(a)^(*(-1)`, which bc rejects silently.
    2. symengine prints `E` for Euler's number; bc wants `e(1)`.
    3. Juxtaposition (`2x`) is valid symbolically and a syntax error in bc, so
       `*` goes between a digit and a following name or bracket.
    """
    # The kernel's evalf prints `2.0` where bc wants `2`, and `k**(-2.0)` where
    # bc wants `(k)^(-2)`. Normalise the decimals first so the power rewrite
    # sees a plain exponent.
    body = re.sub(r"\b(\d+)\.0\b", r"\1", body)
    # Split on the OUTERMOST `**`, not the first one. The kernel prints a
    # reciprocal of a square as `(1 + 2*k + k**2)**(-1)`, and a left-to-right
    # partition cuts inside the base, losing the `**(-1)` tail entirely and
    # emitting `((1 + 2*k + k))^(2)` - which drops the reciprocal and makes
    # `1/(k+1)^2` evaluate to 9 instead of 1/9.
    while "**" in body:
        cut = _outermost_power(body)
        if cut is None:
            break
        base, exponent, tail = cut
        body = f"({base})^({exponent})" + (f"*{tail}" if tail else "")
    body = body.replace("Abs", "abs")
    body = re.sub(r"\bE\b", "e(1)", body)
    body = re.sub(r"(\d)\(", r"\1*(", body)
    body = re.sub(r"\)\((\d|[a-zA-Z(])", r")*\1", body)
    return body.replace("**", "^")


def _numeric(result: str, arguments: dict) -> str:
    digits = _digits(arguments)
    try:
        return f"{result[:digits]}"
    except Exception:  # noqa: BLE001
        return result


# ---------------------------------------------------------------- operations


def _number_of(expr) -> Optional[int]:
    try:
        if expr.is_Integer and 1 < int(expr) < 10 ** 30:
            return int(expr)
    except Exception:  # noqa: BLE001
        pass
    return None


def compute(arguments: dict) -> str:
    op = (arguments.get("operation") or "").strip().lower()
    if op not in OPERATIONS:
        return f"Unknown operation '{op}'. Use one of: {', '.join(OPERATIONS)}."
    raw = str(arguments.get("expression") or "").strip()
    if not raw:
        return "What should I work out?"
    variable = str(arguments.get("variable") or "x").strip() or "x"
    variable = check(variable) if len(variable) > 1 else variable
    order = max(1, min(int(arguments.get("order") or 1), 50))

    if op in ("prime_factors", "is_prime"):
        n = _number_of(parse(raw))
        if n is None:
            return "That needs a whole number between 2 and 10^30."
        if op == "is_prime":
            return f"{n} is {'a prime' if se.isprime(n) else 'not a prime'}."
        factors = {}
        rest = n
        d = 2
        while d * d <= rest:
            while rest % d == 0:
                factors[d] = factors.get(d, 0) + 1
                rest //= d
            d += 1 if d == 2 else 2
        if rest > 1:
            factors[rest] = factors.get(rest, 0) + 1
        return f"{n} = " + " × ".join(
            f"{p}^{k}" if k > 1 else str(p) for p, k in sorted(factors.items()))

    if op == "solve":
        if raw.count("=") != 1:
            return "An equation needs exactly one '=', e.g. 'x^2 - 4 = 0'."
        lhs, rhs = raw.split("=")
        # Two API facts, both found by running rather than by reading the
        # module's names: `se.Eq` returns an `Equality` with `.args` and **no**
        # `.lhs`/`.rhs`, and there is no `se.solve` at all - a *linear* system is
        # `se.linsolve`, and anything else has no kernel routine and falls to
        # bisection. Calling `se.solve` raised AttributeError on every solve.
        left, right = parse(lhs), parse(rhs)
        symbol = se.Symbol(variable)
        exact = True
        try:
            roots = list(se.linsolve([se.expand(left - right)], [symbol]))
        except Exception:  # noqa: BLE001 - not a linear system
            exact = False
            roots = _solve_numeric(left - right, variable, arguments)
        if roots is None:
            return ("I could not find a root for that. symengine solves a linear "
                    "equation exactly and this kernel has no polynomial "
                    "solver, so anything else needs a numeric method this "
                    "skill does not have.")
        qualifier = "" if exact else " (numerically, so approximate)"
        return (f"{variable} = " + ", ".join(_say(r) for r in roots) + qualifier)
        return (f"{variable} = " + ", ".join(_say(r) for r in roots)) if roots else "No solution."

    if op == "piecewise":
        return _piecewise(arguments)

    # ---- special functions. Each is a kernel function that was reachable only
    # from *inside* an expression; this is the operation that evaluates one.
    if op in ("gamma", "zeta", "beta", "erf", "erfc", "dirichlet_eta",
              "lambert_w", "digamma", "loggamma"):
        # **A Python float, not a symengine Integer.** These wrappers take a
        # `double`, so `erf(se.Integer(1))` raises "argument after ** must be
        # a float" - and `se.Integer` is the natural-looking thing to pass.
        if op == "beta":
            pair = _integers(raw, 2)
            if pair is None:
                return "beta needs 'p, q', e.g. '2, 5'."
            at = (float(pair[0]), float(pair[1]))
            label = f"beta({pair[0]}, {pair[1]})"
        else:
            single = _integers(raw, 1)
            if single is None:
                return f"{op} needs a whole number, e.g. '5'."
            at = float(single[0])
            label = f"{op}({single[0]})"
        function = {"gamma": se.gamma, "zeta": se.zeta, "beta": se.beta,
                    "erf": se.erf, "erfc": se.erfc,
                    "dirichlet_eta": se.dirichlet_eta, "lambert_w": se.LambertW,
                    "digamma": se.digamma, "loggamma": se.loggamma}[op]
        # `at` is a float for the single-argument functions and a TUPLE only
        # for beta, so it is called as `function(at)` / `function(*at)`.
        # Unpacking unconditionally - `function(*at)` for both - raises
        # "argument after * must be an iterable, not float".
        value = function(*at) if isinstance(at, tuple) else function(at)
        exact = _say(value)
        # A kernel that keeps a special function unevaluated has not answered
        # the question, so the numeric value is reported beside it.
        try:
            numeric = se.sympify(str(exact)).evalf(15)
            return f"{label} = {numeric}"
        except Exception:  # noqa: BLE001 - some forms resist evaluation
            return f"{label} = {exact}"
    if op == "euler_gamma":
        return f"EulerGamma = {se.sympify(str(se.EulerGamma)).evalf(15)}"
    if op == "catalan":
        return f"the Catalan constant = {se.sympify(str(se.Catalan)).evalf(15)}"
    if op == "golden_ratio":
        return f"the golden ratio = {se.sympify(str(se.GoldenRatio)).evalf(15)}"

    # ---- polynomial arithmetic
    if op.startswith("polynomial_"):
        return _polynomial_op(op, raw, variable, arguments)

    # ---- linear algebra
    if op.startswith("matrix_"):
        return _matrix_op(op, raw, arguments)

    # ---- number theory
    if op == "divisors":
        single = _integers(raw, 1)
        if single is None or single[0] == 0:
            return "divisors needs a non-zero whole number."
        n = abs(single[0])
        found = [d for d in range(1, math.isqrt(n) + 1) if n % d == 0]
        all_d = sorted(found + [n // d for d in found if d != n // d])
        return f"{n} has {len(all_d)} divisors: {', '.join(str(d) for d in all_d)}"
    if op == "is_coprime":
        pair = _integers(raw, 2)
        if pair is None:
            return "is_coprime needs two whole numbers, e.g. '12, 18'."
        a, b = pair
        shared = math.gcd(a, b)
        return (f"{a} and {b} are coprime" if shared == 1
                else f"{a} and {b} share a factor of {shared}, so they are not coprime")
    if op == "next_prime":
        single = _integers(raw, 1)
        if single is None:
            return "next_prime needs a whole number."
        return f"the next prime after {single[0]} is {_next_prime(single[0])}"
    if op == "mobius":
        single = _integers(raw, 1)
        if single is None or single[0] == 0:
            return "mobius needs a non-zero whole number."
        return f"mu({single[0]}) = {_mobius(single[0])}"
    if op == "primorial":
        single = _integers(raw, 1)
        if single is None or not 0 < single[0] <= 500:
            return "primorial needs a whole number between 1 and 500."
        total, primes = 1, []
        for candidate in range(2, single[0] + 1):
            if _is_prime_small(candidate):
                total *= candidate
                primes.append(candidate)
        return (f"the primorial of {single[0]} (product of primes up to it) = "
                f"{total}")
    if op == "mod_pow":
        triple = _integers(raw, 3)
        if triple is None:
            return "mod_pow needs 'base, exponent, modulus', e.g. '7, 5, 13'."
        base, exponent, modulus = triple
        return f"{base}^{exponent} mod {modulus} = {pow(base, exponent, modulus)}"
    if op == "extended_gcd":
        pair = _integers(raw, 2)
        if pair is None:
            return "extended_gcd needs two whole numbers, e.g. '240, 46'."
        g, x, y = _egcd(pair[0], pair[1])
        return (f"gcd({pair[0]}, {pair[1]}) = {g}, with "
                f"{pair[0]}*{x} + {pair[1]}*{y} = {g}")

    # ---- relations and the constants that work
    if op == "compare":
        pair = _symbols(raw, 2)
        if pair is None:
            return "compare needs two variables, e.g. 'x, y'."
        left, right = pair
        relations = (("GreaterThan", se.GreaterThan, ">"), ("LessThan", se.LessThan, "<"),
                     ("Unequality", se.Unequality, "!="), ("Eq", se.Eq, "="))
        rows = [f"{name}({left}, {right}) = {fn(left, right)}"
                for name, fn, _ in relations]
        rows.append(f"({left} - {right}) = {_say(se.expand(left - right))}")
        return "  ".join(rows)
    if op == "kronecker_delta":
        pair = _symbols(raw, 2)
        if pair is None:
            return "kronecker_delta needs two variables, e.g. 'x, y'."
        return f"KroneckerDelta({pair[0]}, {pair[1]}) = {se.KroneckerDelta(*pair)}"
    if op == "levi_civita":
        triple = _integers(raw, 3)
        if triple is None:
            return "levi_civita needs three integers, e.g. '1, 2, 3'."
        return (f"LeviCivita{tuple(triple)} = "
                f"{se.LeviCivita(*[se.Integer(v) for v in triple])}")
    if op == "sine_theta":
        return f"the 'sine of x in degrees' symbol S = {se.S}"
    # `se.AppliedUndef` is NOT offered: `AppliedUndef(f, x)` segfaults the
    # interpreter, reproduced with one line of raw symengine and no Chronoa
    # involved - the same upstream defect as `Matrix.solve()`. A skill that can
    # take the whole assistant down is not a skill.

    # **Before `parse(raw)`, deliberately.** The operations below take their own
    # argument syntax - `12, 18` for gcd, `1 2; 3 4` for a matrix - and the
    # arithmetic parser rejects a comma and a semicolon outright. Dispatching
    # after it meant all eighteen of them raised "unexpected ','" before any of
    # their own code ran.
    # ---- number theory. None of this is in the kernel, and none of it is in
    # this bc: bc 1.07 has arithmetic, `^`, `sqrt()`, `scale` and arrays, and
    # **no** gcd, lcm, isqrt, int() or sgn - so every one here is exact integer
    # work in Python rather than an approximation of it.
    if op == "is_power_of":
        single = _integers(raw, 1)
        if single is None:
            return "is_power_of needs one whole number, e.g. '64'."
        verdict = "a" if _is_perfect_power(single[0]) else "not a"
        return f"{single[0]} is {verdict} perfect power."
    if op in ("gcd", "lcm"):
        pair = _integers(raw, 2)
        if pair is None:
            return f"{op} needs two whole numbers, e.g. '12, 18'."
        a, b = pair
        if op == "gcd":
            return f"gcd({a}, {b}) = {math.gcd(a, b)}"
        return f"lcm({a}, {b}) = {abs(a * b) // math.gcd(a, b)}"
    if op == "mod_inverse":
        pair = _integers(raw, 2)
        if pair is None:
            return "mod_inverse needs 'a, m', e.g. '3, 7'."
        a, m = pair
        try:
            return f"{pow(a, -1, m)} is the inverse of {a} mod {m}."
        except ValueError:
            return f"{a} has no inverse mod {m}: they are not coprime."
    if op == "pow_mod":
        pair = _integers(raw, 2)
        if pair is None:
            return "pow_mod needs 'a, m', e.g. '7, 13'."
        a, m = pair
        return f"{a} mod {m} = {a % m}; {a}^({a % m}) mod {m} = {pow(a, a % m, m)}."
    if op == "binomial":
        pair = _integers(raw, 2)
        if pair is None:
            return "binomial needs 'n, k', e.g. '52, 5'."
        n, k = pair
        if not 0 <= k <= n:
            return f"C({n}, {k}) = 0 (k is outside 0..{n})"
        return f"C({n}, {k}) = {math.comb(n, k)}"
    if op == "factorial":
        single = _integers(raw, 1)
        if single is None or not 0 <= single[0] <= 200:
            return "factorial needs a whole number between 0 and 200."
        return f"{single[0]}! = {math.factorial(single[0])}"
    if op == "isqrt":
        single = _integers(raw, 1)
        if single is None or single[0] < 0:
            return "isqrt needs a whole number of at least 0."
        n = single[0]
        root = math.isqrt(n)
        exact = "" if root * root == n else f" (since {root}^2 = {root * root})"
        return f"isqrt({n}) = {root}{exact}"
    if op == "fibonacci":
        single = _integers(raw, 1)
        if single is None or not 0 <= single[0] <= 10000:
            return "fibonacci needs a whole number between 0 and 10000."
        a, b = 0, 1
        for _ in range(single[0]):
            a, b = b, a + b
        return f"fibonacci({single[0]}) = {a}"
    if op == "digit_sum":
        single = _integers(raw, 1)
        if single is None:
            return "digit_sum needs a whole number."
        return f"digit sum of {single[0]} = {sum(int(c) for c in str(abs(single[0])))}"
    if op == "num_digits":
        single = _integers(raw, 1)
        if single is None:
            return "num_digits needs a whole number."
        return f"{single[0]} has {len(str(abs(single[0])))} digits"
    if op == "base_convert":
        pair = _integers(raw, 2)
        if pair is None:
            return "base_convert needs 'n, base', e.g. '255, 16'."
        n, base = pair
        if not 2 <= base <= 36:
            return "base_convert needs a base between 2 and 36."
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        value, out = abs(n), ""
        if value == 0:
            out = "0"
        while value:
            out = digits[value % base] + out
            value //= base
        return f"{n} in base {base} is {out}"
    if op.startswith("matrix_"):
        return _matrix_op(op, raw, arguments)
    if op in ("abs_value", "signum"):
        # These run before `expr` is bound, so the expression is parsed here.
        local = parse(raw)
        if op == "abs_value":
            return f"|{_say(local)}| = {_say(se.Abs(local))}"
        return f"sign of {_say(local)} is {_say(se.sign(local))}"
    if op == "logarithm":
        # bc's `l()` is the NATURAL log, so `logarithm` of 100 in base 10 came
        # back as 4.605 rather than 2. Any base other than e is ln(x)/ln(base),
        # and bc computes both.
        base = str(arguments.get("order") or 10).strip()
        if base in ("e", "2.718281828459045"):
            formula = f"l({_numeric_expr(raw, variable, '0')})"
            label = "e"
        else:
            try:
                denominator = math.log(float(base))
            except ValueError:
                return f"logarithm needs a base above 0 and not 1, not {base!r}."
            formula = f"l({_numeric_expr(raw, variable, '0')})/{denominator!r}"
            label = base
        return (f"log base {label} of {_say(parse(raw))} = "
                f"{_bc(formula, arguments)}")
    if op in ("mean", "median", "stdev"):
        values = _integers(raw, 99)
        if not values or len(values) < 2:
            return (f"{op} needs at least two whole numbers separated by "
                    f"commas, e.g. '3, 7, 11'.")
        numbers = [float(v) for v in values]
        if op == "mean":
            return f"mean of {len(numbers)} values = {statistics.fmean(numbers)}"
        if op == "median":
            return f"median of {len(numbers)} values = {statistics.median(numbers)}"
        return f"standard deviation of {len(numbers)} values = {statistics.stdev(numbers)}"

    if op.startswith("matrix_"):
        return _matrix_op(op, raw, arguments)

    if "=" in raw:
        return "Only solve takes an equation with '='."
    expr = parse(raw)

    if op == "derivative":
        return _say(se.diff(expr, se.Symbol(variable), order))
    if op == "expand":
        return f"{_say(expr)} = {_say(se.expand(expr))}"
    if op == "factor":
        # symengine has no `factor`. For a univariate polynomial over the
        # integers, integer root-finding plus a division is exact; anything else
        # is reported as unsupported rather than approximated.
        factored = _factor_poly(expr, variable)
        return f"{_say(expr)} = {_say(factored)}" if factored else \
            ("I can't factor that exactly. symengine is a kernel and has no "
             "factor routine; give a polynomial in one variable over the integers.")
    if op == "series":
        point = str(arguments.get("point") if arguments.get("point") not in (None, "") else "0")
        n = max(2, min(int(arguments.get("order") or 6), 20))
        return _say(se.series(expr, se.Symbol(variable), _as_expr(point), n))
    if op == "simplify":
        return (f"{_say(expr)} simplifies to {_say(se.expand(expr))}"
                if se.count_ops(expr) != se.count_ops(se.expand(expr))
                else f"{_say(expr)} is already in its simplest exact form")
    if op == "subs":
        target = str(arguments.get("point") if arguments.get("point") not in (None, "") else "0")
        return f"{_say(expr)} with {variable} = {target} is {_say(se.sympify(expr).subs({se.Symbol(variable): se.sympify(target)}))}"
    if op == "evaluate_at":
        target = str(arguments.get("point") if arguments.get("point") not in (None, "") else "0")
        if not _bc_available():
            return "Numeric evaluation needs `bc`, which is not installed."
        return (f"{_say(expr)} with {variable} = {target} = "
                f"{_bc(_numeric_expr(str(arguments.get('expression')), variable, target), arguments)}")
    if op == "nth_root":
        n = int(arguments.get("order") or 2)
        root, exact = se.integer_nthroot(parse(raw), n)
        note = "exactly" if exact else "approximately"
        return f"The {n}th root of {_say(expr)} is {note} {_say(root)}"
    if op == "is_perfect_power":
        perfect = se.perfect_power(parse(raw))
        return f"{_say(expr)} is {perfect} a perfect power"
    if op == "sqrt_mod":
        modulus = int(arguments.get("order") or 7)
        residue = se.sqrt_mod(parse(raw), modulus)
        return f"The square roots of {_say(expr)} mod {modulus} are {residue if residue else 'none'}"
    if op == "totient":
        n = _number_of(parse(raw))
        if n is None:
            return "That needs a whole number between 1 and 10^30."
        return f"phi({n}) = {_totient(n)}"
    if op == "divisor_count":
        n = _number_of(parse(raw))
        if n is None:
            return "That needs a whole number between 1 and 10^30."
        return f"{n} has {_divisor_count(n)} positive divisors"
    if op == "integral":
        return _integral(expr, variable, arguments)
    if op == "limit":
        return _limit(expr, variable, arguments)
    if op == "sum":
        return _sum(expr, variable, arguments)
    if op == "evaluate":
        digits = _digits(arguments)
        return f"{_say(expr)} = {_numeric_expr(raw, variable, '0')} " \
               f"= {_bc(_numeric_expr(raw, variable, '0'), arguments)[:digits]}"
    return "Unsupported."


def _condition(text: str):
    """A comparison, built by hand from the two sides.

    The arithmetic parser has no relation in its grammar - `x<0` raises
    "unexpected '<'" - because symengine's relations are *classes* (`Lt`, `Le`,
    `Gt`, `Ge`, `Ne`) rather than operators. So the comparison is split here and
    the right constructor is called on the two halves. Only single-variable
    comparisons are supported, which is all a piecewise branch needs.
    """
    text = text.strip()
    for operator, constructor in (("<=", se.Le), (">=", se.Ge),
                                  ("==", se.Eq), ("<", se.Lt),
                                  (">", se.Gt), ("!=", se.Ne)):
        # The longest operators first, so `<=` is not read as `<`.
        if operator in text:
            left, _, right = text.partition(operator)
            return constructor(parse(left), parse(right))
    raise ValueError(f"'{text}' is not a comparison - a piecewise branch needs "
                     f"one, like 'x>=0'")


def _piecewise(arguments: dict) -> str:
    """`Piecewise` over semicolon-separated `value, condition` branches.

    The conditions are built by `_condition` rather than parsed, because a
    relation is not in the arithmetic grammar. The result is rendered as text
    rather than as a `se.Piecewise`: the kernel's own `Piecewise` wants pairs in
    its own order and returns a boolean expression that reads worse than the
    input, and a person asking "what is this function" wants their own branches
    back with each one labelled.
    """
    raw = str(arguments.get("expression") or "")
    branches = [b.strip() for b in raw.split(";") if b.strip()]
    if len(branches) < 2:
        return ("Piecewise needs at least two branches separated by ';', e.g. "
                "'x, x<0; -x, x>=0'.")
    rendered = []
    for branch in branches:
        if "," not in branch:
            return (f"Each branch needs 'value, condition'. '{branch}' has no "
                    f"comma.")
        value_text, condition_text = branch.split(",", 1)
        try:
            value = parse(value_text)
            condition = _condition(condition_text)
        except ValueError as exc:
            return f"I can't read that as maths: {exc}."
        rendered.append(f"{_say(value)} when {_say(condition)}")
    return "Piecewise: " + "; ".join(rendered)


def _totient(n: int) -> int:
    """Euler's totient by trial division. `n` is bounded well below where this
    is slow, and the skill refuses anything past 10^30 before reaching here."""
    if n < 1:
        return 0
    result, d = n, 2
    while d * d <= n:
        if n % d == 0:
            while n % d == 0:
                n //= d
            result -= result // d
        d += 1 if d == 2 else 2
    if n > 1:
        result -= result // n
    return result


def _divisor_count(n: int) -> int:
    count, d = 0, 1
    while d * d <= n:
        if n % d == 0:
            count += 2 if d * d != n else 1
        d += 1
    return count


def _as_expr(text: str):
    """A point/bound as a symengine expression. Accepts 'oo' and fractions."""
    cleaned = str(text).strip()
    if cleaned in ("oo", "inf", "infinity"):
        return se.oo
    try:
        return se.Rational(cleaned) if "/" in cleaned else se.sympify(cleaned)
    except Exception:  # noqa: BLE001 - fall back to a float, then to the string
        try:
            return se.Float(float(cleaned))
        except ValueError:
            return se.sympify(cleaned)


def _polynomial_coefficients(expr, symbol) -> Optional[List[int]]:
    """Coefficients in powers, or None if this is not an integer polynomial.

    Recovered by evaluation at 0..d. symengine has **no polynomial algebra at
    all** - no `Poly`, no `div`, no `factor` - so a univariate polynomial is
    identified here by the values it takes at consecutive integers, and the
    coefficients come back from Lagrange interpolation in the power basis.
    """
    expanded = se.expand(expr)

    def value_at(n: int) -> Optional[int]:
        try:
            return int(expanded.subs({symbol: se.Integer(n)}))
        except Exception:  # noqa: BLE001 - not an integer there, so not a poly
            return None

    values = []
    for degree in range(0, 33):
        point = value_at(degree)
        if point is None:
            return None
        values.append(point)
        # d+1 points determine a degree-d polynomial. Rebuild and compare.
        coefficients = _interpolate(values)
        rebuilt = sum(coefficients[i] * symbol ** i for i in range(degree + 1))
        if se.expand(rebuilt - expanded) == 0:
            return coefficients
    return None


def _interpolate(values: List[int]) -> List[int]:
    """Integer power-basis coefficients from f(0)..f(n), by Lagrange.

    Built by expanding each Lagrange basis polynomial `prod_{j!=i} (x-j)/(i-j)`
    and reading off the coefficient of each power - which is exact in integer
    arithmetic and avoids three earlier wrong answers: finite differences, which
    give the falling-factorial basis rather than powers; doing the arithmetic on
    symengine objects, which reaches `__round__` on an `Integer`; and a
    hand-simplified Lagrange that divided inside the product loop and returned
    (1, 1, 1) for f(0..2) = (-4, -3, 0).

    Checked against that case before being used: (-4, -3, 0) -> (-4, 0, 1).
    """
    n = len(values) - 1
    coefficients = []
    for k in range(n + 1):
        total = 0.0
        for i in range(n + 1):
            # prod_{j != i} (x - j), as power-basis coefficients
            product = [1.0]
            for j in range(n + 1):
                if j == i:
                    continue
                shifted = [0.0] * (len(product) + 1)
                for index, coefficient in enumerate(product):
                    shifted[index] -= j * coefficient
                    shifted[index + 1] += coefficient
                product = shifted
            denominator = 1
            for j in range(n + 1):
                if j != i:
                    denominator *= (i - j)
            total += values[i] * product[k] / denominator
        coefficients.append(int(round(total)))
    return coefficients


def _factor_poly(expr, variable: str):
    """Exact integer factorisation of a univariate polynomial, by rational roots.

    **A kernel cannot factorise.** symengine has no `factor`, no `Poly` and no
    polynomial division, so this does the three things itself: recover the
    coefficients by evaluation, find a rational root by testing the divisors of
    the constant term, and divide out by synthetic division.

    Declines rather than guesses: `x^2+1` has no rational root, so there is no
    factorisation of the kind a person means, and returning the expression
    unchanged behind an "=" would read as a successful answer.
    """
    symbol = se.Symbol(variable)
    coefficients = _polynomial_coefficients(expr, symbol)
    if coefficients is None or len(coefficients) < 3:
        return None
    degree = len(coefficients) - 1
    constant = coefficients[0]
    if constant == 0:
        return symbol * _from_coefficients(coefficients[1:], symbol)

    candidates = set()
    for d in range(1, min(abs(constant), 4096) + 1):
        if constant % d == 0:
            candidates.update({d, -d, constant // d, -(abs(constant) // d)})
    for candidate in sorted(candidates):
        if _evaluate(coefficients, candidate) != 0:
            continue
        quotient = _synthetic_divide(coefficients, candidate)
        if quotient is None:
            continue
        root = symbol - se.Integer(candidate)
        # **Not expanded.** Expanding `(x-2)*(x+2)` gives back `-4 + x^2`,
        # which is the input - so the factorisation was computed correctly and
        # then thrown away one line later. The product is returned as it stands
        # so the answer reads as a factorisation.
        return root * _from_coefficients(quotient, symbol)
    return None


def _evaluate(coefficients: List[int], point: int) -> int:
    """Horner: cheapest and least overflow-prone for a small polynomial."""
    total = 0
    for coefficient in reversed(coefficients):
        total = total * point + coefficient
    return total


def _synthetic_divide(coefficients: List[int], root: int) -> Optional[List[int]]:
    """Divide a power-basis polynomial by (x - root), exactly.

    Synthetic division, in the coefficient order the kernel gives us:
    [c0, c1, ..., cn] with c0 the constant. The recurrence is

        b[n-1] = c[n]
        b[k]   = c[k+1] + root * b[k+1]      for k = n-2 .. 0

    **Note `c[k+1]`, not `c[k]`.** Three wrong versions got written before this
    one: one walked upward from the constant and returned [-4, -8] for
    x^2-4 / (x-2); one used `c[k]` and returned [-2, 1] where [2, 1] is the
    answer; and the recursion direction matters independently of the index. The
    test that pins it is the factorisation of x^2-4, which must come back as
    (x-2)(x+2) and not as something that merely expanded without complaint.
    """
    if _evaluate(coefficients, root) != 0:
        return None
    degree = len(coefficients) - 1
    if degree < 1:
        return None
    quotient = [0] * degree
    quotient[degree - 1] = coefficients[degree]
    for k in range(degree - 2, -1, -1):
        quotient[k] = coefficients[k + 1] + root * quotient[k + 1]
    return quotient


def _from_coefficients(coefficients: List[int], symbol):
    return sum(coefficients[i] * symbol ** i for i in range(len(coefficients)))


def _integral(expr, variable: str, arguments: dict) -> str:
    """Definite integrals numerically; indefinite ones get an honest refusal.

    A kernel cannot integrate, so there is no closed form here and pretending
    otherwise with a numeric answer would be the wrong answer to the question
    actually asked.
    """
    if not _bc_available():
        return ("Numeric integration needs `bc`, which is not installed. On Arch "
                "it comes from the 'bc' package.")
    lo, hi = arguments.get("lower"), arguments.get("upper")
    if lo in (None, "") or hi in (None, ""):
        return ("I can give you the value of a definite integral to any number of "
                "digits, but not a closed-form antiderivative: symengine is a "
                "kernel and has no integrate routine. Give lower and upper "
                "bounds and I will compute it.")
    # Gauss-Legendre quadrature, and **the integrand is evaluated by the kernel
    # at each node**. A first version evaluated it once at the lower bound and
    # then substituted the node textually, so `x^2` from 0 to 3 integrated its
    # value at 0 - zero - for every sample, and the answer was 0.
    raw = str(arguments.get("expression"))
    low = _bc(lo, arguments)
    high = _bc(hi, arguments)
    half = f"(({high} - {low})/2)"
    mid = f"(({high} + {low})/2)"
    nodes, weights = _gauss_legendre(int(arguments.get("order") or 12))
    total = "0"
    for node, weight in zip(nodes, weights):
        x = f"({mid} + {half}*{node})"
        # A plain number at this node is the common case and is exact; anything
        # still symbolic is left as bc text.
        y = _numeric_expr(raw, variable, x)
        if not _is_number(y):
            y = f"({y})"
        total += f" + {weight}*{y}"
    # The total is bracketed before scaling. `f"{total}*{half}"` binds the
    # multiply to the LAST term of the sum only - so x^2 over [0,3] returned
    # 6.000018 instead of 9, which is the right order of magnitude and the
    # wrong answer, and the kind of error no rounding check would catch.
    value = _bc(f"({total})*{half}", arguments)
    return f"Integral from {lo} to {hi} of {_say(expr)} = {value}"


def _gauss_legendre(n: int):
    """Nodes and weights on [-1, 1], by Newton's method. Exact enough for n<=24."""
    nodes, weights = [], []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1)
            step = p1 / dp
            x -= step
            if abs(step) < 1e-15:
                break
        nodes.append(x)
        weights.append(2 / ((1 - x * x) * dp * dp))
    return nodes, weights


def _limit(expr, variable: str, arguments: dict) -> str:
    """A limit numerically, by approaching the point.

    At infinity, by geometric substitution: substitute 1/t and let t -> 0.
    """
    if not _bc_available():
        return ("A numeric limit needs `bc`, which is not installed. On Arch it "
                "comes from the 'bc' package.")
    point = str(arguments.get("point") if arguments.get("point") not in (None, "") else "0")
    if point in ("oo", "inf", "infinity"):
        # sin(x)/x -> 1 as x->oo does not exist; a finite geometric approach is
        # the honest way: substitute 1/t.
        body = _numeric_expr(str(arguments.get("expression")), variable, "1/t")
        value = _bc(f"{body}", arguments)
        return (f"Limit of {_say(expr)} as {variable} -> infinity, approached by "
                f"substituting 1/t: {value}")
    # Approached by evaluating at `point + 1/n` and taking the last read. Each
    # point is a **decimal**, not a bc expression: the kernel evaluates a
    # decimal exactly, and a bc expression like `(0 + 0.000001)` came back
    # empty because the kernel has no `bc` to resolve it against.
    best = None
    for n in (100, 1000, 10000, 100000, 1000000):
        scale = 1.0 / n
        at = f"{float(point) + scale:.12g}" if point not in ("oo", "inf", "infinity") \
            else f"{scale:.12g}"
        value = _bc(_numeric_expr(str(arguments.get("expression")), variable, at), arguments)
        best = (value, scale)
    return (f"Limit of {_say(expr)} as {variable} -> {point}, numerically: "
            f"{best[0]} (approached to {best[1]:g})")


def _sum(expr, variable: str, arguments: dict) -> str:
    # The summand is evaluated at the loop index `k`, which is a bc variable
    # rather than the maths variable, so the expression is translated with the
    # loop index substituted for it.
    lo = str(arguments.get("lower") or "1")
    hi = str(arguments.get("upper") or "oo")
    if hi in ("oo", "inf", "infinity"):
        # Sum to infinity: partial sum with geometric growth in the cutoff.
        body = _numeric_expr(str(arguments.get("expression")), variable, "k")
        value = _bc(f"k={lo}; total=0; while(k<10000000) {{ total += {body}; k++ }}; total", arguments)
        return f"Sum from {lo} to infinity of {_say(expr)} (partial sum, cut off at 10^7 terms) = {value}"
    body = _numeric_expr(str(arguments.get("expression")), variable, "k")
    value = _bc(f"k={lo}; total=0; while(k<={hi}) {{ total += {body}; k++ }}; total", arguments)
    return f"Sum for {variable} from {lo} to {hi} of {_say(expr)} = {value}"


def _solve_numeric(difference, variable: str, arguments: dict):
    """Bisection on `f(x) = lhs - rhs`. No kernel root-finder exists here."""
    lhs = rhs = difference
    digits = _digits(arguments)
    best = None
    grid = _bc("k=-20; x=0; while(k<=20) { print(x); x += 0.5; k++ }", arguments)
    previous = None
    for line in grid.splitlines():
        try:
            x = float(line)
        except ValueError:
            continue
        try:
            value = _bc(_numeric_expr(str(lhs - rhs), variable, line), arguments)
            f_x = float(value)
        except Exception:  # noqa: BLE001
            continue
        if previous is not None and previous[1] * f_x <= 0:
            lo, hi = previous[0], x
            for _ in range(200):
                mid = (lo + hi) / 2
                try:
                    fm = float(_bc(_numeric_expr(str(lhs - rhs), variable, repr(mid)), arguments))
                except Exception:  # noqa: BLE001
                    break
                if fm == 0:
                    lo = hi = mid
                    break
                if previous[1] * fm <= 0:
                    hi = mid
                else:
                    lo, previous = mid, (mid, fm)
            root = (lo + hi) / 2
            best = [f"{root:.{max(digits - 1, 2)}g}"]
        previous = (x, f_x)
    return best


def _timeout(_signum, _frame):
    raise TimeoutError


def _run(arguments: dict) -> str:
    if se is None:
        return ("Symbolic maths needs symengine, which is not installed: install "
                "python-symengine (sudo pacman -S python-symengine).")
    previous = None
    use_alarm = hasattr(signal, "SIGALRM")
    try:
        if use_alarm:
            try:
                previous = signal.signal(signal.SIGALRM, _timeout)
                signal.alarm(TIME_LIMIT)
            except ValueError:  # not the main thread: the sandbox timeout still applies
                use_alarm = False
        return compute(arguments)
    except TimeoutError:
        return f"That took longer than {TIME_LIMIT} seconds, so I stopped."
    except ValueError as e:
        return f"I can't read that as maths: {e}."
    except ZeroDivisionError as e:
        return f"That divides by zero: {e}."
    except se.SympifyError as e:  # type: ignore[union-attr]
        return f"I can't read that as maths ({e.__class__.__name__})."
    except Exception as e:  # noqa: BLE001 - the engines raise many things; say which
        return f"Could not do that: {e.__class__.__name__}: {str(e)[:120]}"
    finally:
        if use_alarm:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)


SKILLS = [Skill(name="solve_math", schema=_SCHEMA, run=_run)]