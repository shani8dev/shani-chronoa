"""Skill: flip a coin, roll dice, pick a number or one of several things.
Uses the operating system's randomness (secrets), not a guessable sequence."""

import re
import secrets

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "random_pick",
        "description": "Flip a coin, roll dice ('2d6'), pick a random number in a range, or "
                       "choose one of a list of options.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["coin", "dice", "number", "choose"]},
            "dice": {"type": "string", "description": "For dice: e.g. '1d6' or '2d20'."},
            "low": {"type": "integer"}, "high": {"type": "integer"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "For choose."},
        }, "required": ["kind"]},
    },
}


def _run(arguments: dict) -> str:
    kind = arguments.get("kind")
    if kind == "coin":
        return f"It's {secrets.choice(['heads', 'tails'])}."
    if kind == "dice":
        m = re.fullmatch(r"\s*(\d*)\s*d\s*(\d+)\s*", str(arguments.get("dice") or "1d6").lower())
        if not m:
            return "Dice look like '2d6': how many, then sides."
        n, sides = int(m.group(1) or 1), int(m.group(2))
        if not (1 <= n <= 100 and 2 <= sides <= 1000):
            return "Up to 100 dice of 2 to 1000 sides."
        rolls = [secrets.randbelow(sides) + 1 for _ in range(n)]
        return f"Rolled {', '.join(map(str, rolls))}" + (f" - total {sum(rolls)}." if n > 1 else ".")
    if kind == "number":
        low, high = int(arguments.get("low", 1)), int(arguments.get("high", 100))
        if low > high:
            low, high = high, low
        return f"{low + secrets.randbelow(high - low + 1)}."
    if kind == "choose":
        options = [str(o).strip() for o in (arguments.get("options") or []) if str(o).strip()]
        if len(options) < 2:
            return "Give me at least two options to choose from."
        return f"{secrets.choice(options)}."
    return "Coin, dice, number or choose?"


SKILLS = [Skill(name="random_pick", schema=_SCHEMA, run=_run)]
