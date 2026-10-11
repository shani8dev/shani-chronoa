"""Read a small kernel or config file: sysfs, procfs, /etc - the one implementation the senses share.

Sixteen senses and skills carried their own copy of these two functions (eleven
identical, five that differed only in whether they stripped); a fix to one -
such as decoding with `errors="replace"`, without which a single stray byte
in a sysfs label raised an exception the callers did not catch - reached one
copy at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


def read_raw(path: PathLike) -> Optional[str]:
    """The file's text exactly as it is, or None when it could not be read at all."""
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def read_text(path: PathLike) -> Optional[str]:
    """The file's text with surrounding whitespace removed, or None when unreadable."""
    raw = read_raw(path)
    return None if raw is None else raw.strip()


def read_int(path: PathLike) -> Optional[int]:
    """An integer file (`charge_now`, `brightness`), or None if absent, unreadable, empty or not a number.

    Empty is None, not 0: a `charge_now` with no coulomb counter exists and
    reads as "", and zero would report a flat battery instead of an unmeasurable one.
    """
    raw = read_text(path)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def read_float(path: PathLike, field: int = 0) -> Optional[float]:
    """A float from a file, or None if absent, empty, unreadable or not a number.

    **The float sibling `read_int` never had.** `/proc/uptime` is
    `"12345.67 890.12"`, `hwmon`'s `temp1_input` is `"45000"`, and every caller
    wrote its own `try: float(...) except (ValueError, IndexError): return None`
    - `cpu.read_uptime` was one of them. Same None-on-bad contract as
    `read_int`, so the two cannot disagree about what an unreadable value means.

    `field` is which whitespace-separated token, because `/proc/uptime` carries
    uptime *and* idle time in one line and only the first is what a caller asked
    for. Default 0, which is every single-value file.

    A NaN or an infinity is None, not a number: `float("nan")` succeeds, and a
    core temperature of `nan` renders as the string "nan" in a reading a person
    is meant to act on.
    """
    raw = read_text(path)
    if not raw:
        return None
    try:
        value = float(raw.split()[field])
    except (ValueError, IndexError):
        return None
    return value if finite(value) else None


def finite(value: object) -> bool:
    """True when `value` is a real, finite number.

    **`float("nan")` and `float("inf")` both succeed**, which is the whole reason
    this exists: a caller that caught only `ValueError` accepted them, and a NaN
    then renders as `nan` in every sentence it reaches and as
    `around:5000,nan,nan` in the query `maps` builds - which returns nothing and
    reads as "there is no petrol pump near you" rather than as a broken position.

    A `bool` is refused on purpose: `isinstance(True, int)` is True in Python,
    so `float(True)` is 1.0 and a JSON `true` becomes a temperature of one
    degree. Measured, gpsd's own allowed TPV shapes include `"lat": null`, and a
    caller that trusted it raised `TypeError` out of the whole sense.

    **This is the one implementation, and it was two before it was one.**
    `media_control._seconds` had carried this exact test since before
    `senses/location.py` gained it on 2026-10-11, and the location fix was
    written by reading `media_control` - so the second copy was *added* while
    the first existed, which is the `wpctl` triplication this module's own
    docstring records: "the same logic is copied per skill... a divergence
    nobody notices until one of them is wrong." Two copies of a three-line test
    is two chances to get one of them wrong, and the honest finding is that the
    author of the second copy had read the first and duplicated it anyway.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))
