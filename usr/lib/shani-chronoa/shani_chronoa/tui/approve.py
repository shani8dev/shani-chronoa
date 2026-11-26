"""Numbered approval picking: choose an option label, never a code.

The Agent contract resolves a question with the chosen option *label*, because
a decision like "Allow this session" only means anything if the engine sees
that exact string. This module owns the keystrokes; it does not decide what an
approval means. Arrow keys move, a number selects, Enter confirms, Ctrl+C or q
refuses with the empty string (which the engine reads as no answer).
"""

from __future__ import annotations

import os
import sys
import termios
import tty
from typing import Optional, TextIO

_CLEAR = "\r" + " " * 72 + "\r"


def _numbered(options: list, stream: TextIO) -> None:
    for index, label in enumerate(options, 1):
        print(f"  {index}) {label}", file=stream, flush=True)


def _from_line(line: str, options: list) -> str:
    """A plain (non-TTY) answer: a number, or an exact option label."""
    text = line.strip()
    if not text:
        return ""
    if text.isdigit():
        number = int(text)
        return options[number - 1] if 1 <= number <= len(options) else ""
    if text in options:
        return text
    return ""


def _read_keys(fd: int, options: list, stream: TextIO) -> str:
    index, digits = 0, ""
    _status(stream, index, len(options), digits)
    while True:
        byte = os.read(fd, 1)
        if not byte or byte in (b"\r", b"\n"):
            break
        if byte == b"\x03":  # Ctrl+C: a refusal, not an exception
            _clear(stream)
            return ""
        if byte in (b"q", b"Q", b"\x1b"):
            # Esc may start an arrow sequence; only treat a lone Esc as quit.
            if byte == b"\x1b":
                following = _arrow(fd)
                if following is not None:
                    index = (index + following) % len(options)
                    _status(stream, index, len(options), "")
                    continue
            _clear(stream)
            return ""
        if byte.isdigit():
            digits += byte.decode("ascii", "replace")
            number = int(digits)
            if 1 <= number <= len(options):
                index = number - 1
            _status(stream, index, len(options), digits)
            continue
        if byte in (b"h", b"\x7f"):  # backspace, also vim-style left
            digits = digits[:-1]
            if digits and 1 <= int(digits) <= len(options):
                index = int(digits) - 1
            _status(stream, index, len(options), digits)
    _clear(stream)
    return options[index]


def _arrow(fd: int) -> Optional[int]:
    """Return -1/+1 for Up/Down; consume the rest of a non-arrow escape."""
    import select

    if not select.select([fd], [], [], 0.05)[0]:
        return None
    second = os.read(fd, 1)
    if second != b"[":
        return None
    third = os.read(fd, 1)
    return {b"A": -1, b"B": 1}.get(third)


def _status(stream: TextIO, index: int, count: int, digits: str) -> None:
    chosen = digits or str(index + 1)
    print(
        f"\r  choice [{chosen}]: {options_safe(index, count)}",
        end="",
        file=stream,
        flush=True,
    )


def _clear(stream: TextIO) -> None:
    print(_CLEAR, end="", file=stream, flush=True)


def options_safe(index: int, count: int) -> str:
    """The one-line hint; kept separate so it can be tested without a TTY."""
    return f"{index + 1}/{count} (Up/Down, number, Enter, q to refuse)"


def choose(
    options, *, stream: TextIO = None, stdin=None, show_options: bool = True
) -> str:
    """Show numbered options and return the selected label (or '' to refuse).

    `stream` defaults to sys.stdout and `stdin` to sys.stdin. A non-TTY stdin
    is read as one line, so scripts and the stand-in can answer by number too.
    Pass show_options=False when the renderer already printed the choices.
    """
    options = [str(option) for option in (options or [])]
    out = stream if stream is not None else sys.stdout
    if not options:
        return ""
    if show_options:
        _numbered(options, out)
    source = stdin if stdin is not None else sys.stdin
    if not getattr(source, "isatty", lambda: False)():
        return _from_line(source.readline(), options)
    fd = source.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return _read_keys(fd, options, out)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
