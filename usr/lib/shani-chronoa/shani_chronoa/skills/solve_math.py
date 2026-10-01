"""Skill: symbolic maths - derivatives, integrals, limits, equations, series, sums.

calculate does arithmetic; this does algebra, with SymPy (python-sympy, in
Arch's extra; an optdepend). It runs on this machine and nothing leaves it.

**The input is a language model's text, so it is allowlisted before SymPy
sees it.** SymPy's parse_expr is built on eval, so this never hands it a
string that could name anything but maths: only digits, operators,
parentheses, commas, '=', single-letter variables and the function and
constant names below get through, and '__', attribute dots, quotes,
brackets and keywords are refused outright. Parsing then uses an explicit
name table with no builtins. The skill sandbox's own time and CPU limits
bound a hard integral, and an alarm gives up sooner with a plain answer.
"""

import re
import signal

from shani_chronoa.skills import Skill

try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application,
                                            parse_expr, standard_transformations)
except Exception:  # pragma: no cover - exercised only when python-sympy is missing
    sp = None

MAX_CHARS = 300
TIME_LIMIT = 20

#: Names an expression may use, besides single-letter variables.
FUNCTIONS = ("sin cos tan cot sec csc asin acos atan acot sinh cosh tanh asinh acosh atanh "
             "exp log ln sqrt cbrt root abs sign floor ceiling factorial binomial gamma "
             "erf re im arg conjugate Max Min").split()
CONSTANTS = {"pi": "pi", "e": "E", "E": "E", "oo": "oo", "inf": "oo", "infinity": "oo", "I": "I"}

OPERATIONS = ("derivative", "integral", "limit", "solve", "simplify", "factor", "expand",
              "series", "sum", "evaluate", "prime_factors", "is_prime")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "solve_math",
        "description": (
            "Symbolic maths: derivative, integral (indefinite, or definite with lower and "
            "upper), limit (at a point, or oo for infinity), solve an equation (use '='), "
            "simplify, factor, expand, series (Taylor), sum of a series, evaluate to N "
            "digits, prime_factors / is_prime of an integer. Write maths plainly: "
            "'x^2 + 3x', 'sin(x)/x', 'x^2 - 4 = 0'."
        ),
        "parameters": {"type": "object", "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "expression": {"type": "string", "description": "The maths, e.g. 'x^3*sin(x)' or 'x^2 - 5x + 6 = 0'."},
            "variable": {"type": "string", "description": "The variable, default x."},
            "point": {"type": "string", "description": "For limit/series: where, e.g. '0' or 'oo'."},
            "lower": {"type": "string", "description": "Definite integral / sum: lower bound."},
            "upper": {"type": "string", "description": "Definite integral / sum: upper bound."},
            "order": {"type": "integer", "description": "Derivative order, or series terms."},
            "digits": {"type": "integer", "description": "For evaluate: significant digits (max 1000)."},
        }, "required": ["operation", "expression"]},
    },
}

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def check(text: str) -> str:
    """The text, if it is only maths; raises ValueError naming what is not."""
    if len(text) > MAX_CHARS:
        raise ValueError(f"that is longer than {MAX_CHARS} characters")
    bad = re.search(r"__|[\"'`\[\]{};:\\@#$&|!?~]|\.(?=[A-Za-z_])", text)
    if bad:
        raise ValueError(f"'{bad.group(0)}' is not maths")
    if not re.fullmatch(r"[A-Za-z0-9_+\-*/^().,=\s]*", text):
        raise ValueError("only numbers, letters, + - * / ^ ( ) , . and = are allowed")
    allowed = set(FUNCTIONS) | set(CONSTANTS) | {"diff", "integrate"}
    for name in _TOKEN.findall(text):
        if len(name) == 1 or name in allowed:
            continue
        raise ValueError(f"'{name}' is not a known function, constant or one-letter variable")
    return text


def _names() -> dict:
    table = {f: getattr(sp, f) for f in FUNCTIONS if hasattr(sp, f)}
    table.update({"ln": sp.log, "cbrt": sp.cbrt, "abs": sp.Abs})
    table.update({k: getattr(sp, v) for k, v in CONSTANTS.items()})
    return table


def parse(text: str):
    text = check(text).replace("^", "**")
    local = _names()
    for letter in set(re.findall(r"(?<![A-Za-z_])[A-Za-z](?![A-Za-z_0-9])", text)):
        if letter not in local:
            local[letter] = sp.Symbol(letter)
    return parse_expr(text, local_dict=local, global_dict={"__builtins__": {}, "Integer": sp.Integer,
                                                           "Float": sp.Float, "Rational": sp.Rational,
                                                           "Symbol": sp.Symbol},
                      transformations=standard_transformations + (implicit_multiplication_application, convert_xor))


def _say(expr) -> str:
    """Readable: x**2 -> x^2, and a decimal beside an exact non-integer number."""
    text = str(expr).replace("**", "^")
    try:
        if expr.is_number and expr.is_real and not expr.is_Integer:
            approx = sp.N(expr, 10)
            if str(approx) != str(expr):
                text += f" (about {approx})"
    except Exception:  # noqa: BLE001 - the decimal is a courtesy
        pass
    return text


def compute(arguments: dict) -> str:
    op = (arguments.get("operation") or "").strip().lower()
    if op not in OPERATIONS:
        return f"Unknown operation '{op}'. Use one of: {', '.join(OPERATIONS)}."
    raw = str(arguments.get("expression") or "").strip()
    if not raw:
        return "What should I work out?"
    var = sp.Symbol(check(str(arguments.get("variable") or "x").strip() or "x"))
    order = max(1, min(int(arguments.get("order") or 1), 50))

    if op in ("prime_factors", "is_prime"):
        n = parse(raw)
        if not (n.is_Integer and n > 1 and n < 10 ** 30):
            return "That needs a whole number between 2 and 10^30."
        if op == "is_prime":
            return f"{n} is {'a prime' if sp.isprime(n) else 'not a prime'}."
        f = sp.factorint(n)
        return f"{n} = " + " × ".join(f"{p}^{k}" if k > 1 else str(p) for p, k in sorted(f.items()))

    if op == "solve":
        if raw.count("=") != 1:
            return "An equation needs exactly one '=', e.g. 'x^2 - 4 = 0'."
        lhs, rhs = raw.split("=")
        roots = sp.solve(sp.Eq(parse(lhs), parse(rhs)), var)
        return (f"{var} = " + ", ".join(_say(r) for r in roots)) if roots else "No solution."
    if "=" in raw:
        return "Only solve takes an equation with '='."
    expr = parse(raw)
    if op == "derivative":
        return f"d{'^' + str(order) if order > 1 else ''}/d{var}{'^' + str(order) if order > 1 else ''} " \
               f"of {_say(expr)} = {_say(sp.simplify(sp.diff(expr, var, order)))}"
    if op == "integral":
        lo, hi = arguments.get("lower"), arguments.get("upper")
        if lo not in (None, "") and hi not in (None, ""):
            value = sp.integrate(expr, (var, parse(str(lo)), parse(str(hi))))
            return f"Integral of {_say(expr)} from {lo} to {hi} = {_say(value)}"
        value = sp.integrate(expr, var)
        if isinstance(value, sp.Integral):
            return f"I can't find a closed form for the integral of {_say(expr)}."
        return f"Integral of {_say(expr)} d{var} = {_say(value)} + C"
    if op == "limit":
        point = parse(str(arguments.get("point") if arguments.get("point") not in (None, "") else "0"))
        return f"Limit of {_say(expr)} as {var} -> {_say(point)} = {_say(sp.limit(expr, var, point))}"
    if op == "series":
        point = parse(str(arguments.get("point") if arguments.get("point") not in (None, "") else "0"))
        n = max(2, min(int(arguments.get("order") or 6), 20))
        return f"Series of {_say(expr)} around {_say(point)}: {_say(sp.series(expr, var, point, n))}"
    if op == "sum":
        lo, hi = arguments.get("lower", "1"), arguments.get("upper", "oo")
        value = sp.summation(expr, (var, parse(str(lo or 1)), parse(str(hi or "oo"))))
        return f"Sum of {_say(expr)} for {var} from {lo or 1} to {hi or 'oo'} = {_say(value)}"
    if op == "evaluate":
        digits = max(1, min(int(arguments.get("digits") or 15), 1000))
        return f"{str(expr).replace('**', '^')} = {sp.N(expr, digits)}"
    if op == "simplify":
        return f"{_say(expr)} simplifies to {_say(sp.simplify(expr))}"
    if op == "factor":
        return f"{_say(expr)} = {_say(sp.factor(expr))}"
    if op == "expand":
        return f"{_say(expr)} = {_say(sp.expand(expr))}"
    return "Unsupported."


def _timeout(_signum, _frame):
    raise TimeoutError


def _run(arguments: dict) -> str:
    if sp is None:
        return ("Symbolic maths needs SymPy, which is not installed: install python-sympy "
                "(sudo pacman -S python-sympy).")
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
    except (SyntaxError, TypeError, sp.SympifyError if sp else Exception) as e:
        return f"I can't read that as maths ({e.__class__.__name__})."
    except Exception as e:  # noqa: BLE001 - SymPy raises many things; say which
        return f"SymPy could not do that: {e.__class__.__name__}: {str(e)[:120]}"
    finally:
        if use_alarm:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)


SKILLS = [Skill(name="solve_math", schema=_SCHEMA, run=_run)]
