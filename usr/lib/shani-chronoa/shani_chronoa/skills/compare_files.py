"""Skill: show what differs between two files.

**There is no diff capability in this package at all** - `search_file_contents`
finds a line in many files, `find_and_replace` changes a string, and nothing
answers "are these two versions of the same file the same, and if not where".
That is the question behind every "did my config get overwritten" and every
"which of these two is newer", and it is the one an assistant is worst at
answering from memory.

Built on `difflib.unified_diff` with a real context window, so the output is
the same shape `diff -u` produces and a model has seen it thousands of times.

Honesty rules, each one a way a naive comparison lies:

- **A file that could not be read is named, and the other side is not
  compared.** Silently treating an unreadable file as empty would report a
  diff that is a fiction - every line would appear as added.
- **A binary file is not diffed as text.** `difflib` on decoded-with-replacement
  bytes produces a plausible-looking diff full of meaningless changes, so a
  NUL byte means the answer is "binary, not compared" rather than a bad diff.
- **"Identical" is a real, checked answer** - same bytes, not "no lines
  looked different" - and it prints the size both agreed on, because "they are
  the same" over an empty pair of files is a claim about zero content.
- **A truncated diff says so.** A very large difference is elided past a line
  budget with a count of what was withheld, never presented as complete.
"""

from __future__ import annotations

import difflib

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_DIFF_LINES = 400
_MAX_BYTES = 4 * 1024 * 1024

SCHEMA = {
    "type": "function",
    "function": {
        "name": "compare_files",
        "description": (
            "Show what is different between two text files, as a unified diff. "
            "Reports 'identical' when they match. Use this to compare two "
            "versions of a config, or to see what a change actually did."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "The first file."},
                "path_b": {"type": "string", "description": "The second file."},
                "context_lines": {
                    "type": "integer",
                    "description": "How many unchanged lines to show around each change. Defaults to 3.",
                },
            },
            "required": ["path_a", "path_b"],
        },
    },
}


def _read(path) -> "tuple[bytes | None, str]":
    """(content, problem). Never returns partial content as if it were whole."""
    try:
        return path.read_bytes(), ""
    except FileNotFoundError:
        return None, "it does not exist"
    except IsADirectoryError:
        return None, "it is a directory, not a file"
    except PermissionError:
        return None, ("permission denied - it exists, but this user may not "
                      "read it, so its contents are unknown")
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _run(arguments: dict) -> str:
    try:
        left = files.resolve_in_home(arguments.get("path_a") or "")
        right = files.resolve_in_home(arguments.get("path_b") or "")
    except files.PathProblem as exc:
        return str(exc)

    try:
        context = int(arguments.get("context_lines") or 3)
    except (TypeError, ValueError):
        context = 3
    context = max(0, min(context, 20))

    raw_a, problem_a = _read(left)
    if raw_a is None:
        return f"Could not compare {left}: {problem_a}. Nothing was compared."
    raw_b, problem_b = _read(right)
    if raw_b is None:
        return f"Could not compare {right}: {problem_b}. Nothing was compared."

    for raw, path in ((raw_a, left), (raw_b, right)):
        if len(raw) > _MAX_BYTES:
            return (
                f"{path} is {files.human_size(len(raw))}, over the "
                f"{files.human_size(_MAX_BYTES)} a text comparison reads. "
                f"Nothing was compared."
            )
        if b"\0" in raw[:4096]:
            return (
                f"{path} holds binary data, so a line-by-line text diff would "
                f"be meaningless. Nothing was compared."
            )

    if raw_a == raw_b:
        return (
            f"{left} and {right} are identical: {files.human_size(len(raw_a))}, "
            f"byte for byte."
        )

    try:
        text_a = raw_a.decode("utf-8")
        text_b = raw_b.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (
            f"Could not compare: {'a' if exc.object is raw_a else 'b'} file is not "
            f"UTF-8 text (byte {exc.start}), and decoding it with replacement "
            f"characters would produce a diff of the replacements rather than "
            f"of the files. Nothing was compared."
        )

    diff = list(difflib.unified_diff(
        text_a.splitlines(keepends=True), text_b.splitlines(keepends=True),
        fromfile=str(left), tofile=str(right), n=context,
    ))
    if not diff:
        return (
            f"{left} and {right} have the same bytes and the same lines, so "
            f"nothing differs."
        )

    lines_a = text_a.splitlines()
    lines_b = text_b.splitlines()
    header = (
        f"{left} vs {right} ({files.human_size(len(raw_a))} vs "
        f"{files.human_size(len(raw_b))}, {len(lines_a)} vs {len(lines_b)} line(s)):"
    )
    body = [line.rstrip("\n") for line in diff]
    if len(body) <= _MAX_DIFF_LINES:
        return "\n".join([header, *body])
    withheld = len(body) - _MAX_DIFF_LINES
    note = (
        f"\n... {withheld} more diff line(s) not shown (limit is "
        f"{_MAX_DIFF_LINES}). This is not the whole difference - narrow the "
        f"files or compare a line range."
    )
    return "\n".join([header, *body[:_MAX_DIFF_LINES], note])


SKILLS = [Skill(name="compare_files", schema=SCHEMA, run=_run)]
