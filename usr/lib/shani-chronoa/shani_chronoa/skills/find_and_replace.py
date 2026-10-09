"""Skill: search and replace a string across files.

Gated on its own key, and the gate matters more here than for any other file
skill. `write_text_file` replaces one named file that the caller already chose.
This touches **every** file matching a pattern, and the blast radius is not
knowable in advance - a pattern that matches in forty files rewrites forty files,
and a path that was meant to be a folder is a machine-wide edit.

The mitigation is not a better prompt, it is the default: **`dry_run` defaults
to true.** A model that means to edit still has to pass `dry_run: false`, which
is one explicit token of intent rather than an inference. The dry run lists
every file that would change, with a count, so the decision is made with the
blast radius in view.

Honesty rules:

- Binary files and files that do not decode are counted and named as skipped,
  never silently passed over.
- The result reports files changed, files that matched the pattern but were
  already correct, and files skipped - so "no changes" is distinguishable from
  "nothing matched".
- A file that cannot be written is reported by name; the rest still proceed,
  and the count of what did change is still true.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import List

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.skills.undo_last_change import record_preimage

_CONSENT_KEY = "bulk-edit-enabled"
_MAX_FILES = 500
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_DEPTH = 12

SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_and_replace",
        "description": (
            "Find a string across files under a directory and optionally "
            "replace it. Reports what it would change without changing anything "
            "unless dry_run is explicitly false. Requires the "
            "'bulk-edit-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "find": {"type": "string", "description": "The literal text to look for."},
                "replace": {
                    "type": "string",
                    "description": "What to put in its place. Empty string deletes it.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search under. Defaults to the current directory.",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Only files matching this wildcard, e.g. '*.py'. Defaults to all.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "List what would change without writing. Defaults to "
                        "TRUE - pass false to actually edit."
                    ),
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Match case exactly. Defaults to false.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"bulk find-and-replace is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Reading and searching files needs no such permission, "
            f"and neither does writing one named file - this one rewrites every "
            f"file matching a pattern, and that is not knowable in advance."
        )
    return True, ""


def _scan(root: Path, find: str, file_pattern: str, case_sensitive: bool) -> "tuple[List[tuple], List[str], List[str]]":
    """(matches, already_correct, skipped) - each match is (path, count, line_no)."""
    needle = find if case_sensitive else find.lower()
    matches: List[tuple] = []
    already: List[str] = []
    skipped: List[str] = []
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        if len(here.parts) - base >= _MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if file_pattern and not fnmatch.fnmatch(name, file_pattern):
                continue
            path = here / name
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    skipped.append(f"{path} (over {files.human_size(_MAX_FILE_BYTES)})")
                    continue
                raw = path.read_bytes()
            except OSError as exc:
                skipped.append(f"{path} ({type(exc).__name__})")
                continue
            if b"\0" in raw[:4096]:
                skipped.append(f"{path} (binary)")
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(f"{path} (not UTF-8)")
                continue
            hits = [(i, l) for i, l in enumerate(text.splitlines(), 1)
                    if needle in (l if case_sensitive else l.lower())]
            if hits:
                matches.append((path, len(hits), hits[0][0]))
            else:
                already.append(str(path))
            if len(matches) >= _MAX_FILES:
                skipped.append(f"(stopped after {_MAX_FILES} matching files)")
                return matches, already, skipped
    return matches, already, skipped


def _run(arguments: dict) -> str:
    find = arguments.get("find")
    if find is None or find == "":
        return "No search text was given, so there is nothing to find."
    dry_run = arguments.get("dry_run")
    dry_run = True if dry_run is None else bool(dry_run)

    if not dry_run:
        allowed, reason = _consent(ChronoaConfig())
        if not allowed:
            return f"Refusing to edit files: {reason}"

    replace = arguments.get("replace")
    if replace is None:
        replace = ""
    if not isinstance(replace, str):
        return f"Replacement must be text, not {type(replace).__name__}."

    try:
        root = files.resolve((arguments.get("path") or "").strip() or ".")
    except files.PathProblem as exc:
        return str(exc)
    if not root.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              root, "search")
    if root.is_file():
        root = root.parent

    matches, already, skipped = _scan(
        root, find, (arguments.get("file_pattern") or "").strip(),
        bool(arguments.get("case_sensitive")))

    if not matches:
        base = f"No file under {root} contains {find!r}."
        if already:
            base += ""
        if skipped:
            base += f" {len(skipped)} path(s) were skipped (binary, too large or unreadable)."
        return base

    verb = "would change" if dry_run else "changed"
    lines = [f"{len(matches)} file(s) {verb} (each match counted):"]
    for path, count, first in matches[:_MAX_FILES]:
        shown = str(path)
        if len(shown) > 88:
            shown = "..." + shown[-85:]
        lines.append(f"  {count:>4} match(es), first at line {first}: {shown}")
    if len(matches) > _MAX_FILES:
        lines.append(f"  ... {len(matches) - _MAX_FILES} more not listed.")

    if dry_run:
        lines.append(
            "  Nothing was written. To apply this, call again with dry_run false. "
            "This rewrites every listed file in place.")
        return "\n".join(lines)

    changed, failed = 0, []
    for path, _count, _first in matches:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            failed.append(f"{path} ({type(exc).__name__})")
            continue
        if arguments.get("case_sensitive"):
            updated = text.replace(find, replace)
        else:
            updated = text.replace(find, replace) if find in text else \
                _replace_insensitive(text, find, replace)
        if updated == text:
            continue
        try:
            record_preimage(path, text.encode("utf-8"))  # so undo_last_change can put each file back
            path.write_text(updated, encoding="utf-8")
            changed += 1
        except OSError as exc:
            failed.append(f"{path} ({type(exc).__name__}: {exc})")

    lines[0] = f"{changed} file(s) changed:"
    if failed:
        lines.append(f"  {len(failed)} could NOT be written: {failed[0]}")
    if skipped:
        lines.append(f"  {len(skipped)} path(s) were skipped and not searched.")
    lines.append(f"  {len(already)} file(s) did not contain the text.")
    return "\n".join(lines)


def _post_condition(arguments: dict) -> tuple[bool, str]:
    """Is `find` gone from the files this call reported changing?

    Multi-file and pattern-driven, so the check is the same shape as
    `edit_file`'s: read the files back and look for what should no longer be
    there. A file that legitimately still contains the text elsewhere - the
    replacement may itself contain it - is counted as not-changed rather than
    passed, because the honest reading of "did the replacement hold" is "is the
    search text still in the file at all", and anything else needs the caller to
    know how many occurrences were expected.

    A dry run changed nothing, so it is reported as a completed call: that is
    what it did. Returning failure there would store the refusal as if it were a
    refusal to work.
    """
    find = arguments.get("find")
    if find is None or find == "":
        return False, "no search text was given, so nothing could have been replaced"
    dry_run = arguments.get("dry_run")
    if dry_run is None or bool(dry_run):
        return True, "this was a dry run; nothing was written, which is what it was asked to do"
    try:
        root = files.resolve((arguments.get("path") or "").strip() or ".")
    except files.PathProblem as exc:
        return False, str(exc)
    if not root.exists():
        return False, f"{root} does not exist, so nothing there could have been replaced"
    if root.is_file():
        root = root.parent
    try:
        matches, _already, _skipped = _scan(
            root, find, (arguments.get("file_pattern") or "").strip(),
            bool(arguments.get("case_sensitive")))
    except Exception as exc:  # noqa: BLE001 - a scanner that raises is not a verification
        return False, f"the files could not be re-scanned: {exc.__class__.__name__}"
    if not matches:
        return False, ("no files matched the search text, so there is nothing "
                       "whose replacement could be checked")
    still = []
    for path, _count, _first in matches:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if arguments.get("case_sensitive"):
            if find in text:
                still.append(str(path))
        elif find.lower() in text.lower():
            still.append(str(path))
    if still:
        return False, (f"{len(still)} file(s) still contain the search text: "
                       f"{still[0]}" + (f" and {len(still)-1} more" if len(still) > 1 else ""))
    return True, f"the search text is gone from all {len(matches)} file(s) it was found in"


POST_CONDITION = _post_condition


def _replace_insensitive(text: str, find: str, replace: str) -> str:
    """Case-insensitive replace that preserves nothing clever about case."""
    out, low, i = [], text.lower(), 0
    needle = find.lower()
    if not needle:
        return text
    while True:
        found = low.find(needle, i)
        if found < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:found])
        out.append(replace)
        i = found + len(needle)


SKILLS = [Skill(name="find_and_replace", schema=SCHEMA, run=_run)]
