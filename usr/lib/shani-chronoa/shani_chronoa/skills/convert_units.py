"""Skill: convert between units - length, mass, volume, speed, area, data, time,
temperature. Pure arithmetic on this machine; nothing leaves it."""

from shani_chronoa.skills import Skill

#: unit -> (dimension, factor to the dimension's base unit)
_U = {}
def _add(dim, factor, *names):
    for n in names:
        _U[n] = (dim, factor)

_add("length", 1, "m", "meter", "meters", "metre", "metres")
_add("length", 1000, "km", "kilometer", "kilometers", "kilometre", "kilometres")
_add("length", 0.01, "cm", "centimeter", "centimeters", "centimetre", "centimetres")
_add("length", 0.001, "mm", "millimeter", "millimeters", "millimetre", "millimetres")
_add("length", 1609.344, "mi", "mile", "miles")
_add("length", 0.3048, "ft", "foot", "feet")
_add("length", 0.0254, "in", "inch", "inches")
_add("length", 0.9144, "yd", "yard", "yards")
_add("mass", 1, "kg", "kilogram", "kilograms", "kilo", "kilos")
_add("mass", 0.001, "g", "gram", "grams")
_add("mass", 0.45359237, "lb", "lbs", "pound", "pounds")
_add("mass", 0.028349523125, "oz", "ounce", "ounces")
_add("mass", 1000, "t", "tonne", "tonnes", "ton", "tons")
_add("volume", 1, "l", "liter", "liters", "litre", "litres")
_add("volume", 0.001, "ml", "milliliter", "milliliters", "millilitre", "millilitres")
_add("volume", 3.785411784, "gal", "gallon", "gallons")
_add("volume", 0.2365882365, "cup", "cups")
_add("speed", 1, "km/h", "kmh", "kph")
_add("speed", 1.609344, "mph", "miles per hour")
_add("speed", 3.6, "m/s", "meters per second")
_add("speed", 1.852, "knot", "knots")
_add("area", 1, "m2", "square meter", "square meters", "sq m")
_add("area", 4046.8564224, "acre", "acres")
_add("area", 10000, "hectare", "hectares", "ha")
_add("area", 0.09290304, "sq ft", "square foot", "square feet")
_add("data", 1, "b", "byte", "bytes")
for i, (short, long_) in enumerate((("kb", "kilobyte"), ("mb", "megabyte"), ("gb", "gigabyte"), ("tb", "terabyte")), 1):
    _add("data", 1000 ** i, short, long_, long_ + "s")
for i, (short, long_) in enumerate((("kib", "kibibyte"), ("mib", "mebibyte"), ("gib", "gibibyte"), ("tib", "tebibyte")), 1):
    _add("data", 1024 ** i, short, long_, long_ + "s")
_add("time", 1, "s", "sec", "second", "seconds")
_add("time", 60, "min", "minute", "minutes")
_add("time", 3600, "h", "hr", "hour", "hours")
_add("time", 86400, "day", "days")
_add("time", 604800, "week", "weeks")
_TEMP = {"c": "C", "celsius": "C", "°c": "C", "f": "F", "fahrenheit": "F", "°f": "F", "k": "K", "kelvin": "K"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "convert_units",
        "description": "Convert a quantity between units: km/miles, kg/pounds, °C/°F, litres/gallons, "
                       "km/h/mph, acres/hectares, GB/GiB, hours/minutes and so on.",
        "parameters": {"type": "object", "properties": {
            "value": {"type": "number", "description": "The amount to convert."},
            "from_unit": {"type": "string", "description": "The unit it is in, e.g. 'km' or 'fahrenheit'."},
            "to_unit": {"type": "string", "description": "The unit wanted, e.g. 'miles' or 'celsius'."},
        }, "required": ["value", "from_unit", "to_unit"]},
    },
}


def _temp(v, a, b):
    c = v if a == "C" else (v - 32) * 5 / 9 if a == "F" else v - 273.15
    return c if b == "C" else c * 9 / 5 + 32 if b == "F" else c + 273.15


def convert(value: float, frm: str, to: str):
    f, t = frm.strip().lower(), to.strip().lower()
    if f in _TEMP and t in _TEMP:
        return _temp(value, _TEMP[f], _TEMP[t]), None
    if f not in _U or t not in _U:
        bad = f if f not in _U else t
        return None, f"I don't know the unit '{bad}'"
    (d1, k1), (d2, k2) = _U[f], _U[t]
    if d1 != d2:
        return None, f"'{frm}' is a {d1} and '{to}' is a {d2}; they don't convert"
    return value * k1 / k2, None


def _run(arguments: dict) -> str:
    try:
        value = float(arguments.get("value"))
    except (TypeError, ValueError):
        return "Which amount?"
    frm, to = str(arguments.get("from_unit") or ""), str(arguments.get("to_unit") or "")
    out, err = convert(value, frm, to)
    if err:
        return err + "."
    shown = f"{out:.6g}" if abs(out) < 1e6 else f"{out:,.0f}"
    return f"{value:g} {frm} is {shown} {to}."


SKILLS = [Skill(name="convert_units", schema=_SCHEMA, run=_run)]
