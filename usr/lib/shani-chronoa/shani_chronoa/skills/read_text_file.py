"""Skill: read a text file, with an optional line range.

The other half of the assistant's blindness. `list_directory` shows names,
`search_file_contents` finds lines, but nothing could actually *show* the file -
and a person asking "what's in my notes" is asking for the contents.

Honesty rules:

- A file that is not valid UTF-8 is reported as such rather than mangled through
  `errors="replace"`, because replaced characters hide exactly the corruption
  the reader needed to know about.
- A truncation says how many lines were withheld and how to get them.
- An empty file says it is empty. That one is genuinely empty - a zero-byte file
  is a real, verifiable fact, not an unknown.
"""

from __future__ import annotations

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_BYTES = 512 * 1024
_MAX_LINES = 400

SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_text_file",
        "description": (
            "Read a text file and return its contents, optionally a range of "
            "lines. Use this to see what a file actually says."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file to read."},
                "start_line": {
                    "type": "integer",
                    "description": "1-based first line to return. Defaults to the first line.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": f"How many lines to return. Defaults to {_MAX_LINES}.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    try:
        target = files.resolve(arguments.get("path") or "")
        files.refuse_sensitive(target, "read")
    except files.PathProblem as exc:
        return str(exc)
    if target.is_dir():
        return (
            f"{target} is a directory, not a file. Use list_directory to see "
            f"what is in it."
        )
    try:
        raw = target.read_bytes()
    except OSError as exc:
        return files.describe(exc, target, "read")

    if len(raw) > _MAX_BYTES:
        return (
            f"{target} is {files.human_size(len(raw))}, over the "
            f"{files.human_size(_MAX_BYTES)} limit for reading in one go. "
            f"Nothing was read; use search_file_contents with a pattern, or "
            f"read a line range of a smaller file."
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (
            f"{target} is not UTF-8 text (byte {exc.start}), so it is not a text "
            f"file this can show. It is {files.human_size(len(raw))} and exists."
        )
    if not text:
        return f"{target} is empty (0 bytes)."

    lines = text.splitlines()
    try:
        start = int(arguments.get("start_line") or 1)
    except (TypeError, ValueError):
        start = 1
    if start < 1:
        start = 1
    try:
        want = max(1, min(int(arguments.get("max_lines") or _MAX_LINES), 2000))
    except (TypeError, ValueError):
        want = _MAX_LINES

    if start > len(lines):
        return (
            f"{target} has {len(lines)} line(s); line {start} is past the end. "
            f"Nothing was returned."
        )
    chosen = lines[start - 1: start - 1 + want]
    header = f"{target}  ({len(lines)} line(s), showing {len(chosen)} from line {start})"
    body = "\n".join(chosen)
    if len(chosen) < len(lines) - (start - 1):
        body += (
            f"\n... {len(lines) - (start - 1) - len(chosen)} more line(s) not "
            f"shown. Raise max_lines or pass start_line to continue."
        )
    return f"{header}\n{body}"


SKILLS = [Skill(name="read_text_file", schema=SCHEMA, run=_run)]
