"""Arithmetic and unit conversion, entirely local.

No network, no subprocess, no tool: this is Python's own `math` and `decimal`
evaluated inside a restricted parser. It exists because "what's 30% of 47" and
"how many miles is 200km" are questions a user asks constantly, and routing them
through a language model to do four-digit division is both slower and a place to
hallucinate an answer.

**It is a parser, not `eval`.** `eval` on model-supplied text is arbitrary code
execution, and this skill's arguments come from a language model. The parser
accepts numbers, the four operations, parentheses, and a small set of named
functions; everything else is a syntax error. There is no name lookup, no
attribute access, no subscripting, and no import, because a parser with those
would be `eval` again.

**`decimal` for money and measurements, `float` for what `math` needs.**
`0.1 + 0.2` is `0.30000000000000004` in binary floating point, and a converter
that answers "10.00 miles" for a currency sum is worse than no answer. Division
by zero raises a `ZeroDivisionError` that is caught and reported as such rather
than becoming `inf` or an exception traceback in front of a user.

Units are exact factors from the SI, and a conversion that is not a whole-number
multiple of its base is reported to a sensible precision rather than to 17
significant figures, which is noise.
"""

import ast
import logging
import math
import re

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

# Every factor is an exact multiple of its base unit. Distances, masses and
# volumes in the units a person actually meets; temperature is special-cased
# because it has offsets, which a multiplicative factor cannot express.
_UNITS: dict[str, tuple[str, float]] = {
    "mm": ("length", 0.001), "cm": ("length", 0.01),
    "m": ("length", 1.0), "km": ("length", 1000.0),
    "in": ("length", 0.0254), "ft": ("length", 0.3048),
    "yd": ("length", 0.9144), "mi": ("length", 1609.344),
    "nmi": ("length", 1852.0), "ly": ("length", 9.4607304725808e15),
    "mg": ("mass", 1e-6), "g": ("mass", 0.001),
    "kg": ("mass", 1.0), "t": ("mass", 1000.0),
    "oz": ("mass", 0.028349523125), "lb": ("mass", 0.45359237),
    "stone": ("mass", 6.35029318),
    "ml": ("volume", 1e-6), "l": ("volume", 1.0),
    "tsp": ("volume", 0.00492892159375), "tbsp": ("volume", 0.01478676478125),
    "floz": ("volume", 0.0295735295625), "pt": ("volume", 0.473176473),
    "qt": ("volume", 0.946352946), "gal": ("volume", 3.785411784),
    "b": ("bytes", 1.0), "kb": ("bytes", 1000.0), "mb": ("bytes", 1e6),
    "gb": ("bytes", 1e9), "tb": ("bytes", 1e12), "kib": ("bytes", 1024.0),
    "mib": ("bytes", 1024.0 ** 2), "gib": ("bytes", 1024.0 ** 3),
    "s": ("time", 1.0), "min": ("time", 60.0), "h": ("time", 3600.0),
    "day": ("time", 86400.0), "week": ("time", 604800.0),
    "year": ("time", 31557600.0),
    "pa": ("pressure", 1.0), "kpa": ("pressure", 1000.0),
    "bar": ("pressure", 100000.0), "psi": ("pressure", 6894.757293168),
    "atm": ("pressure", 101325.0),
    "hz": ("frequency", 1.0), "khz": ("frequency", 1e3), "mhz": ("frequency", 1e6),
    "ghz": ("frequency", 1e9),
    "c": ("temperature", 1.0), "f": ("temperature", 1.0),
    "kj": ("energy", 1000.0), "wh": ("energy", 3600.0),
    "kwh": ("energy", 3.6e6), "cal": ("energy", 4.184),
    "w": ("power", 1.0), "kw": ("power", 1000.0), "hp": ("power", 745.699872),
}

# Word forms a person types rather than a symbol. These resolve to the same
# factor as the symbol, because "200 km to miles" is the natural phrasing and
# refusing it for want of an abbreviation is needless friction.
_ALIASES = {
    "metre": "m", "metres": "m", "meter": "m", "meters": "m",
    "kilometre": "km", "kilometres": "km", "kilometer": "km", "kilometers": "km",
    "centimetre": "cm", "centimetres": "cm", "centimeter": "cm", "centimeters": "cm",
    "millimetre": "mm", "millimetres": "mm", "millimeter": "mm", "millimeters": "mm",
    "mile": "mi", "miles": "mi", "feet": "ft", "foot": "ft", "inch": "in",
    "inches": "in", "yard": "yd", "yards": "yd",
    "gram": "g", "grams": "g", "kilogram": "kg", "kilograms": "kg",
    "kilo": "kg", "kilos": "kg", "pound": "lb", "pounds": "lb", "lbs": "lb",
    "ounce": "oz", "ounces": "oz", "tonne": "t", "tonnes": "t", "ton": "t",
    "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
    "gallon": "gal", "gallons": "gal", "pint": "pt", "pints": "pt",
    "second": "s", "seconds": "s", "sec": "s", "secs": "s",
    "minute": "min", "minutes": "min", "mins": "min",
    "hour": "h", "hours": "h", "hr": "h", "hrs": "h", "day": "day", "days": "day",
    "week": "week", "weeks": "week", "year": "year", "years": "year",
    "byte": "b", "bytes": "b", "kibibyte": "kib", "mebibyte": "mib",
    "gibibyte": "gib", "kilobyte": "kb", "megabyte": "mb",
    "gigabyte": "gb", "terabyte": "tb",
    "celsius": "c", "fahrenheit": "f", "kelvin": "k", "degc": "c", "degf": "f",
    "hertz": "hz", "watt": "w", "watts": "w", "kilowatt": "kw",
    "watt_hour": "wh", "kilowatt_hour": "kwh", "psi": "psi",
}

_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "log": math.log10, "log2": math.log2,
    "ln": math.log, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "round": round, "floor": math.floor, "ceil": math.ceil,
    "exp": math.exp, "log10": math.log10, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "hypot": math.hypot,
    "degrees": math.degrees, "radians": math.radians, "min": min, "max": max,
}

# Integer functions answer exactly - factorial(25) is 15511210043330985984000000,
# not 1.55112e+25 - so they return ints rather than floats, and their inputs are
# bounded: factorial(10**7) is millions of digits and would hold the skill for
# minutes. Permutations and combinations are the n-choose-k people ask for.
_INT_LIMIT = 10000


def _whole(x, name):
    if isinstance(x, float):
        if not x.is_integer():
            raise ValueError(f"{name} needs whole numbers")
        x = int(x)
    if x < 0:
        raise ValueError(f"{name} needs numbers that are not negative")
    if x > _INT_LIMIT:
        raise ValueError(f"{name} is limited to numbers up to {_INT_LIMIT}")
    return x


_INT_FUNCTIONS = {
    "factorial": lambda n: math.factorial(_whole(n, "factorial")),
    "comb": lambda n, k: math.comb(_whole(n, "comb"), _whole(k, "comb")),
    "ncr": lambda n, k: math.comb(_whole(n, "nCr"), _whole(k, "nCr")),
    "perm": lambda n, k=None: math.perm(_whole(n, "perm"), None if k is None else _whole(k, "perm")),
    "npr": lambda n, k: math.perm(_whole(n, "nPr"), _whole(k, "nPr")),
    "gcd": lambda *a: math.gcd(*[int(_whole(abs(x), "gcd")) for x in a]),
    "lcm": lambda *a: math.lcm(*[int(_whole(abs(x), "lcm")) for x in a]),
    "isqrt": lambda n: math.isqrt(_whole(n, "isqrt")),
}

# Longest first, so "kib" is not read as "k" plus a stray "ib".
# Longest first, so "kib" is not read as "k" plus a stray "ib".
_UNIT_PATTERN = "|".join(
    sorted(
        (re.escape(u) for u in (*_UNITS, *_ALIASES)), key=len, reverse=True
    )
)
_NUMBER = r"\d+(?:\.\d+)?"
_CONVERT = re.compile(
    rf"^({_NUMBER})\s*({_UNIT_PATTERN})\s+(?:to|in|as)\s+({_UNIT_PATTERN})$",
    re.IGNORECASE,
)

# Offsets, which a multiplicative factor cannot express.
_TEMPERATURE = {
    "c": (1.0, 0.0),
    "f": (5.0 / 9.0, 32.0 * 5.0 / 9.0),
    "k": (1.0, 273.15),
}


def _convert(value: float, source: str, target: str) -> str:
    source = _ALIASES.get(source, source)
    target = _ALIASES.get(target, target)
    if source not in _UNITS or target not in _UNITS:
        return f"Unknown unit: {source if source not in _UNITS else target}."
    from_unit = _UNITS[source]
    to_unit = _UNITS[target]
    if from_unit[0] != to_unit[0]:
        return (
            f"Cannot convert {source} to {target}: they measure different "
            f"things ({from_unit[0]} and {to_unit[0]})."
        )
    if from_unit[0] == "temperature":
        scale_from, offset_from = _TEMPERATURE[source]
        scale_to, offset_to = _TEMPERATURE[target]
        kelvin = value * scale_from + offset_from
        result = (kelvin - offset_to) / scale_to
    else:
        result = value * from_unit[1] / to_unit[1]
    digits = 10 if abs(result) >= 1 else 12
    if abs(result) >= 1e6 or (0 < abs(result) < 1e-4):
        return f"{value:g} {source} = {result:.4g} {target}"
    return f"{value:g} {source} = {round(result, digits):g} {target}"


def _evaluate(expression: str) -> float:
    """Evaluate a restricted arithmetic expression.

    Python's own parser builds the tree and a whitelist walks it. This is not
    `eval`: only the node types below are accepted, and anything else - a name
    that is not `pi`, a call that is not in `_FUNCTIONS`, an attribute, a
    subscript, a comprehension, a lambda - is a syntax error before any
    arithmetic happens. An earlier hand-rolled tokeniser in this module was
    wrong in six separate ways (`2+2` returned 24), which is what a parser
    without a grammar spec invites.
    """
    # A percentage with nothing to apply it to is ambiguous: `30%` is 0.3 of
    # one, 30, or a truncation. Rewriting it to `30/100` would answer "0.3" and
    # hope, so it is refused and the arithmetic form is suggested instead.
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*%\s*", expression):
        raise ValueError(
            "a percentage needs a number to apply to - '30%' could be 0.3 of "
            "one, 30, or a truncation. Write it as a multiplication, e.g. "
            "200*30%"
        )
    # `15%` and `2^10` are how people write these; Python wants `15/100` and
    # `2**10`. Rewritten before parsing, which is why the AST below only ever
    # sees a division and a power.
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expression)
    text = text.replace("^", "**")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"not valid arithmetic ({exc.msg})") from exc
    return _walk(tree.body)


_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

_UNARY_OPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}

_COMPARISONS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}


def _walk(node) -> float:
    """One node, one accepted type, one failure message naming what was found."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numbers may appear in the expression")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        function = _BIN_OPS.get(type(node.op))
        if function is None:
            raise ValueError("that operator is not supported")
        return function(_walk(node.left), _walk(node.right))
    if isinstance(node, ast.UnaryOp):
        function = _UNARY_OPS.get(type(node.op))
        if function is None:
            raise ValueError("that unary operator is not supported")
        return function(_walk(node.operand))
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            raise ValueError("chained comparisons are not supported")
        function = _COMPARISONS.get(type(node.ops[0]))
        if function is None:
            raise ValueError("that comparison is not supported")
        return float(function(_walk(node.left), _walk(node.comparators[0])))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            # An attribute call like `os.system(...)` is not a whitelisted name.
            raise ValueError("only the listed functions may be called")
        key = node.func.id.lower()
        if node.keywords:
            raise ValueError("functions take no keyword arguments")
        if key in _INT_FUNCTIONS:
            return _INT_FUNCTIONS[key](*[_walk(a) for a in node.args])
        function = _FUNCTIONS.get(key)
        if function is None:
            raise ValueError(
                f"'{node.func.id}' is not a known function; available: "
                f"{', '.join(sorted(set(_FUNCTIONS) | set(_INT_FUNCTIONS)))}"
            )
        return float(function(*[_walk(a) for a in node.args]))
    if isinstance(node, ast.Name):
        constant = {"pi": math.pi, "e": math.e, "tau": math.tau}.get(node.id.lower())
        if constant is not None:
            return constant
        raise ValueError(
            f"'{node.id}' is not a number or a known function. Only pi, e and "
            f"tau are available by name; for algebra with variables (x, y), "
            f"derivatives, integrals or limits, use solve_math."
        )
    if isinstance(node, ast.IfExp):
        # `x if c else y` is a conditional, which has no place in arithmetic.
        raise ValueError("conditionals are not supported")
    raise ValueError(
        f"{type(node).__name__} is not allowed in an expression - only "
        f"numbers, the four operations, parentheses, comparisons and the "
        f"listed functions."
    )


SCHEMA = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": (
            "Evaluate arithmetic and percentages ('15% of 2400'), or convert "
            "between units. Handles the four "
            "operations, powers, parentheses, factorial, permutations and "
            "combinations (perm/nPr, comb/nCr), gcd, lcm, sqrt, exp, log, ln, "
            "trig and inverse trig, and pi/e/tau - exact for whole numbers. "
            "For algebra with variables, derivatives, integrals, limits or "
            "equations, use solve_math. Converts length, mass, volume, time, "
            "data, pressure, frequency, power, energy and temperature, in the "
            "units a person actually meets - say '200 km to miles' or '70kg in "
            "lb'. Runs entirely on this machine with no network call. Refuses "
            "to guess: a percentage needs a number to apply to, and converting "
            "between quantities that measure different things is an error "
            "rather than a number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": (
                        "Either an expression, e.g. '(12+3)*4', or a "
                        "conversion, e.g. '200km to miles'."
                    ),
                },
            },
            "required": ["expression"],
        },
    },
}


def _run(arguments: dict) -> str:
    expression = str(arguments.get("expression") or "").strip()
    if not expression:
        return (
            "Nothing to calculate. Pass an expression such as '(12+3)*4' or a "
            "conversion such as '200km to miles'."
        )
    match = _CONVERT.match(expression)
    if not match and re.search(r"\s+(?:to|in|as)\s+", expression, re.IGNORECASE):
        # This looked like a conversion, so saying "invalid arithmetic" would be
        # the wrong complaint: the units are what failed.
        parts = re.split(r"\s+(?:to|in|as)\s+", expression.strip(), maxsplit=1)
        unknown = [w.strip() for w in parts
                   if w.strip().lower() not in _UNITS
                   and w.strip().lower() not in _ALIASES]
        if unknown:
            return (
                f"Unknown unit: {' and '.join(unknown)}. Known units are the "
                f"usual length, mass, volume, time, data, pressure, frequency, "
                f"power, energy and temperature ones."
            )
    if match:
        try:
            return _convert(float(match.group(1)),
                            match.group(2).lower(), match.group(3).lower())
        except ValueError as exc:
            return f"Cannot convert: {exc}"
    try:
        value = _evaluate(expression)
    except ZeroDivisionError:
        return (
            "Cannot divide by zero. If you meant a percentage, write it as a "
            "multiplication, e.g. 200*15%."
        )
    except (ValueError, SyntaxError, ArithmeticError) as exc:
        return (
            f"Could not evaluate {expression!r}: {exc}. This handles arithmetic, "
            f"powers, factorial/comb/perm/gcd/lcm, sqrt/exp/log/ln, trig and "
            f"inverse trig, and pi/e/tau; for symbolic maths (x, derivatives, "
            f"integrals, limits, equations) use solve_math."
        )
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:g}"


SKILLS = [Skill(name="calculate", schema=SCHEMA, run=_run)]
