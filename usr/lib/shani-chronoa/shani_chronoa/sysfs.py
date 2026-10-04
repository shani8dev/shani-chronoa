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
