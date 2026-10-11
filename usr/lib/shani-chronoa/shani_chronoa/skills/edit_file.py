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


def _tight(text: str) -> str:
    """`text` with every run of whitespace removed.

    **The comparison, never the edit.** oh-my-openagent's hashline core calls this
    `stripAllWhitespace` and uses it for `restoreOldWrappedLines`: a model
    re-typing a block strips trailing spaces, re-wraps, or drops a continuation
    token, and its transcription is then not the bytes on disk.
    """
    return "".join(text.split())


def _match_ignoring_whitespace(text: str, old: str) -> "tuple[str, int, int]":
    """`old` as the file's own bytes, how many times it matches, and where.

    **Line-aligned, and that is the whole reason this is safe.** The match is a
    run of *whole lines* whose joined tight form equals the needle's, so the span
    it returns always begins and ends on a line boundary. Matching on characters
    instead stops wherever the tight characters happen to run out - measured, a
    needle whose last line ends in `)` matched a span one character short of the
    newline, and `text.replace(old, new)` then left the original newline behind:
    the edit came out `...items)\n\n`.

    The retry is only reached when the exact match already failed, and it is
    O(lines * needle-lines), bounded by the file on one side and
    `_MAX_NEW_BYTES` on the other.

    **What it returns on ambiguity.** `count > 1` comes back unchanged, so the
    refusal below names the alternatives exactly as it did before - a span that
    could be two places is never used, and `replace_all` still means "all of
    them". The single-match case returns the **file's own span**, which is what
    makes the replacement exact rather than a normalisation of the model's text.

    A needle shorter than six tight characters is never retried: `return`, `x` or
    `if` occur everywhere, so a whitespace-insensitive match on one of them is
    guaranteed ambiguous and the walk would only find it. That is the same floor
    `restoreOldWrappedLines` uses for a restorable span.

    The offset is what confines the *other* half of the correction: a span of
    `new_string` can only be put back to the file's bytes if it came from the
    region being replaced, which is where the model was transcribing from.
    """
    if len(_tight(old)) < 6:
        return old, 0, -1
    wanted = _tight(old)
    file_lines = text.split("\n")
    # The offsets each line starts at, so a run can be sliced as whole lines.
    starts = []
    at = 0
    for line in file_lines:
        starts.append(at)
        at += len(line) + 1

    hits = 0
    first_start = -1
    first_span = ""
    for index in range(len(file_lines)):
        length = 0
        for end in range(index, len(file_lines)):
            length += len(_tight(file_lines[end]))
            if length > len(wanted):
                break
            if length == len(wanted):
                if _tight("\n".join(file_lines[index:end + 1])) == wanted:
                    hits += 1
                    if hits == 1:
                        first_start = starts[index]
                        first_span = "\n".join(file_lines[index:end + 1])
                        # Keep the trailing newline the run consumed, so the
                        # substitution never leaves the original one behind.
                        if end + 1 < len(file_lines):
                            first_span += "\n"
                break
    if hits != 1:
        return old, (0 if hits == 0 else hits), -1
    return first_span, 1, first_start


def _restore_kept_lines(region: str, new: str) -> str:
    """`new` with the lines it quoted put back as the file has them.

    **oh-my-openagent's `restoreOldWrappedLines`, narrowed to where the risk
    is zero.** That function restores any run of two or more non-blank lines in
    the replacement whose tight form matches a place in the original exactly
    once. Chronoa's `new_string` is only the replacement text, not the whole file,
    so the search is confined to `region` - the bytes being replaced - which is
    the block the model was transcribing from. A correction can therefore only
    put back a line that was in the text it was asked to change.

    Three rules, each of which makes the correction unable to invent an edit:

    - **two or more non-blank lines.** One line is not evidence of a quote: it is
      as likely to be the model's own new text. A run is a transcription.
    - **six or more tight characters.** `return x` occurs everywhere and a span
      too short to identify a place cannot be restored to one.
    - **exactly one place in the region.** Ambiguity is left alone, and the
      original bytes are used rather than any normalisation of them.
    """
    if not region or len(new) < 2:
        return new
    region_lines = region.split("\n")
    where: dict = {}
    for index in range(len(region_lines)):
        for length in range(2, min(10, len(region_lines) - index) + 1):
            span = region_lines[index:index + length]
            if any(not line.strip() for line in span):
                continue
            tight = _tight("".join(span))
            if len(tight) < 6:
                continue
            where.setdefault(tight, []).append((index, length))
    unique = {k: v[0] for k, v in where.items() if len(v) == 1}
    if not unique:
        return new

    out = new.split("\n")
    start = 0
    while start < len(out):
        if not out[start].strip():
            start += 1
            continue
        for length in range(min(10, len(out) - start), 1, -1):
            span = out[start:start + length]
            if any(not line.strip() for line in span):
                continue
            hit = unique.get(_tight("".join(span)))
            if hit is None:
                continue
            index, _original_length = hit
            out[start:start + length] = ["\n".join(region_lines[index:index + length])]
            start += 1
            break
        else:
            start += 1
    return "\n".join(out)


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
    fuzzy = False
    if count == 0:
        # **A second chance, and it is not a guess.** oh-my-openagent's
        # `restoreOldWrappedLines` exists for the same case: a model re-typing a
        # block strips trailing whitespace or re-wraps, so its transcription of
        # `old_string` is not the bytes on disk and the whole edit is refused.
        # That is the safe answer, and it is a friction the model cannot see past,
        # because the difference is invisible in the text it wrote.
        #
        # So the *match* is retried ignoring whitespace. When exactly one span of
        # the file tightens to what `old` tightens to, that span's own bytes
        # become `old` - **the file's bytes, trailing whitespace included** - and
        # the replacement is exact as before. A span that could be two places is
        # never used, so the ambiguity this rule exists to refuse still refuses.
        old, count, offset = _match_ignoring_whitespace(text, old)
        fuzzy = count == 1
        if fuzzy:
            # The same transcription error is in `new_string`: a line the model
            # meant to keep was re-typed without the file's own whitespace, and
            # writing it would be a change to a line nobody asked to change.
            # Confined to the region being replaced, so it can only put back what
            # was in the text it was asked to change.
            new = _restore_kept_lines(text[offset:offset + len(old)], new)
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
    detail = f"{count} occurrence(s) of the" if replace_all else "the"
    return (
        f"{verb} {detail} text in {target} "
        f"({files.human_size(len(raw))} -> {files.human_size(len(updated.encode('utf-8')))}). "
        f"undo_last_change can put it back.{note}"
    )


def _post_condition(arguments: dict) -> tuple[bool, str]:
    """Does the file now contain the replacement, read back from disk?

    The edit above already re-reads the file and refuses to claim success when
    the bytes differ - so the property exists, it just was not visible to the
    layer that decides whether a call is verified. UNVERIFIED is the only
    verdict the replay guard refuses to store, so an unverifiable edit could be
    replayed by a notification pressed twice.

    The check asks the file, not the write: `new_string` present and
    `old_string` gone is the effect, and reading it back is the only thing that
    can distinguish "written" from "reported as written". A `replace_all` edit
    is the same question and gets the same answer, because the count is not
    what is being verified.
    """
    new = arguments.get("new_string")
    if new is None:
        return False, "no new_string was given, so there is nothing to look for"
    try:
        target = files.resolve_in_home(arguments.get("path") or "")
    except files.PathProblem as exc:
        return False, str(exc)
    if not target.exists():
        return False, f"{target} does not exist, so nothing was edited there"
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"{target} could not be read back: {exc.__class__.__name__}"
    if new == "":
        # A deletion: the old text must be gone, which is the whole effect.
        return True, f"{target} no longer contains the deleted text" \
            if text != arguments.get("old_string") else \
            f"{target} still contains exactly the text that was to be deleted"
    if new not in text:
        return False, f"{target} does not contain the replacement text"
    old = arguments.get("old_string")
    if old and not arguments.get("replace_all") and old in text and old != new:
        return False, f"{target} still contains the original text as well"
    return True, f"{target} contains the replacement and not the original"


POST_CONDITION = _post_condition


SKILLS = [Skill(name="edit_file", schema=SCHEMA, run=_run)]
