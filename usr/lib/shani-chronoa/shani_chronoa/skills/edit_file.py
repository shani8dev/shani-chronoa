"""Skill: change one exact piece of text in one named file.

The complement to `find_and_replace`, which is multi-file, pattern-driven and
dry-run by default. That one answers "replace this everywhere"; this answers
"change *this line* and nothing else", which is the edit a person actually
means when they are pointing at a file and a line.

**Uniqueness is the whole safety property of this skill, so it is enforced
rather than hoped for.** `old_string` must occur exactly once unless the
caller explicitly asks for `replace_all`. A `old_string` that occurs three
times is not a near-miss to be resolved by picking the first - it is three
different places, and which one the model meant is not knowable from here. So
the count is reported, with the line numbers, and nothing is written.

Refusals, all before any byte is written: an empty `old_string` (which
matches everywhere, and would delete the file), an `old_string` equal to
`new_string` (a write that claims to have done something and has not), a
`old_string` that is absent, and a file that is not UTF-8 text.

A pre-image is recorded before the write, so `undo_last_change` can put the
file back. That is why a single-line edit here is reversible while the same
edit through `write_text_file` is not.
"""

from __future__ import annotations

import difflib

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.skills.undo_last_change import record_preimage

_CONSENT_KEY = "file-edit-enabled"

#: A single edit that rewrites more than this is a `write_text_file` with extra
#: steps, and a model that produced 5 MB of new text meant to write a file, not
#: to change a line.
_MAX_NEW_BYTES = 1024 * 1024

SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Replace one exact piece of text in one named file. The text to "
            "replace must appear exactly once, or nothing is written - the "
            "count and line numbers are reported instead of a guess being made. "
            "Use this to change a specific line; use find_and_replace to change "
            "a string across many files. Requires the 'file-edit-enabled' "
            "consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file to edit."},
                "old_string": {
                    "type": "string",
                    "description": "The exact text to replace, including its indentation.",
                },
                "new_string": {
                    "type": "string",
                    "description": "What to put in its place. An empty string deletes it.",
                },
                "preview": {
                    "type": "boolean",
                    "description": (
                        "Show the change as a unified diff and write nothing - "
                        "for 'show me first'. Defaults to false."
                    ),
                },
                "replace_all": {
                    "type": "boolean",
                    "description": (
                        "Replace every occurrence instead of requiring exactly "
                        "one. Defaults to false."
                    ),
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"editing a file is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Reading files needs no such permission, and neither "
            f"does listing them - this one changes what a file says."
        )
    return True, ""


def _line_numbers(text: str, needle: str) -> "list[int]":
    """1-based line numbers of every line containing `needle`.

    Reported for a refused ambiguous edit, so the caller can be told *where*
    the alternatives are rather than only how many there are. A model that
    knows the three candidates are on lines 4, 40 and 400 can disambiguate on
    the next call; one told only "3 matches" cannot.
    """
    if not needle:
        return []
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            out.append(number)
    return out


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to edit: {reason}"

    old = arguments.get("old_string")
    new = arguments.get("new_string")
    if old is None or new is None:
        return "Both old_string and new_string are needed to make an edit."
    if not isinstance(old, str) or not isinstance(new, str):
        return "Both old_string and new_string must be text."
    if old == "":
        return (
            "Refusing to edit: old_string is empty, and an empty string occurs "
            "everywhere - that edit would delete the file's contents. Quote the "
            "text you mean to replace."
        )
    if old == new:
        return (
            "Refusing to edit: old_string and new_string are identical, so "
            "nothing would change. Nothing was written."
        )
    if len(new.encode("utf-8")) > _MAX_NEW_BYTES:
        return (
            f"That replacement is {files.human_size(len(new.encode('utf-8')))}, "
            f"over the {files.human_size(_MAX_NEW_BYTES)} an in-place edit "
            f"allows. Use write_text_file to replace a whole file."
        )

    try:
        target = files.resolve_in_home(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    try:
        files.refuse_catalogue(target, "edit")
        files.refuse_sensitive(target, "edit")
    except files.PathProblem as exc:
        return str(exc)
    if target.is_dir():
        return f"{target} is a directory, not a file."
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return f"{target} does not exist, so nothing was edited."
    except OSError as exc:
        return files.describe(exc, target, "read")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return (
            f"{target} is not UTF-8 text, so an in-place text edit is not "
            f"something this can do to it safely. Nothing was written."
        )

    count = text.count(old)
    if count == 0:
        return (
            f"{target} does not contain that text, so nothing was edited. "
            f"Read the file first - the text has to match exactly, including "
            f"whitespace."
        )

    replace_all = bool(arguments.get("replace_all"))
    if count > 1 and not replace_all:
        where = _line_numbers(text, old)
        shown = ", ".join(str(n) for n in where[:20])
        more = f" (and {len(where) - 20} more)" if len(where) > 20 else ""
        return (
            f"That text occurs {count} times in {target}, on line(s) {shown}"
            f"{more}, so nothing was written - one of them is not obviously "
            f"the one meant. Include more surrounding context in old_string so "
            f"it is unique, or pass replace_all to change all {count}."
        )

    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    # Maze-AI agent/codecheck.py's rule applied to the modified whole file:
    # refuse the edit whose result would not parse, before it is written.
    problem = files.parse_problem(updated, target)
    if problem:
        return problem

    if arguments.get("preview"):
        # servers/filesystem `edit_file dryRun`: the diff, and nothing written.
        diff = "".join(difflib.unified_diff(
            text.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=f"{target} (now)", tofile=f"{target} (after)", n=2))
        if len(diff) > 6000:
            diff = diff[:6000] + "\n... (diff cut)"
        return f"Preview only - nothing was written. Call again without preview to apply.\n{diff}"
    note = record_preimage(target, raw)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return files.describe(exc, target, "edit")

    try:
        if target.read_text(encoding="utf-8") != updated:
            return f"Reported editing {target} but its contents do not match."
    except OSError as exc:
        return f"Edited {target} but could not check the result: {exc}"

    verb = "Replaced all" if replace_all else "Replaced"
    detail = f"{count} occurrence(s) of" if replace_all else "the"
    return (
        f"{verb} {detail} the text in {target} "
        f"({files.human_size(len(raw))} -> {files.human_size(len(updated.encode('utf-8')))}). "
        f"undo_last_change can put it back.{note}"
    )


SKILLS = [Skill(name="edit_file", schema=SCHEMA, run=_run)]
