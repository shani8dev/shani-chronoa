"""Skill: convert money between currencies at today's reference rate.

Rates are the European Central Bank's daily reference rates, served by
Frankfurter (https://frankfurter.app; free, no key, ~30 currencies including
INR, USD, EUR, GBP, JPY). A web request: only with the web sense on and
privacy mode off, and recorded in the egress log. The rate's date is said,
because ECB rates are set once a working day.
"""

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

RATES = "https://api.frankfurter.app/latest"

#: Words people say for the ISO codes.
NAMES = {"rupee": "INR", "rupees": "INR", "inr": "INR", "dollar": "USD", "dollars": "USD",
         "usd": "USD", "euro": "EUR", "euros": "EUR", "eur": "EUR", "pound": "GBP", "pounds": "GBP",
         "gbp": "GBP", "yen": "JPY", "jpy": "JPY", "yuan": "CNY", "cny": "CNY", "franc": "CHF",
         "francs": "CHF", "dirham": "AED", "dirhams": "AED", "aed": "AED", "singapore dollar": "SGD",
         "australian dollar": "AUD", "canadian dollar": "CAD"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_currency",
        "description": "Convert an amount of money between currencies (rupees, dollars, euros, pounds, yen, ...) "
                       "at today's ECB reference rate. Requires the web sense and privacy mode off.",
        "parameters": {"type": "object", "properties": {
            "amount": {"type": "number", "description": "How much money."},
            "from_currency": {"type": "string", "description": "e.g. 'USD' or 'dollars'."},
            "to_currency": {"type": "string", "description": "e.g. 'INR' or 'rupees'."},
        }, "required": ["amount", "from_currency", "to_currency"]},
    },
}


def code(name: str) -> str:
    n = " ".join((name or "").lower().split())
    return NAMES.get(n, n.upper())


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"Currency rates are not available: {config.sense_allowed_reason('web')}."
    try:
        amount = float(arguments.get("amount"))
    except (TypeError, ValueError):
        return "How much?"
    a, b = code(arguments.get("from_currency", "")), code(arguments.get("to_currency", ""))
    if a == b:
        return f"{amount:g} {a} is {amount:g} {b}."
    from shani_chronoa.netjson import get_json
    try:
        data = get_json("skill:convert_currency", RATES, {"amount": amount, "from": a, "to": b})
    except Exception as e:  # noqa: BLE001 - an unknown code is a 404 here
        return f"Could not convert {a} to {b}: {e.__class__.__name__}. Frankfurter knows about 30 ECB currencies."
    value = (data.get("rates") or {}).get(b)
    if value is None:
        return f"No rate for {a} to {b}."
    return f"{amount:g} {a} is {value:,.2f} {b} (ECB reference rate of {data.get('date')}, via Frankfurter)."


SKILLS = [Skill(name="convert_currency", schema=_SCHEMA, run=_run)]
