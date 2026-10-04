"""Skill: search inside files for a string.

`find_files` searches names; this searches contents. It is the skill that
actually reads bytes off disk, so it has the strictest limits in the set: a
per-file size ceiling, a total-bytes ceiling, a binary-file refusal, and a hard
cap on matches.

Honesty rules:

- A file that cannot be decoded is reported as skipped, never as a file with
  zero matches. Those are opposite statements and conflating them is how a
  search silently misses the file you cared about.
- Binary files are skipped and counted. Searching them would produce noise, but
  dropping them without saying so would understate what was actually read.
- Reaching a cap says which cap and how to narrow, rather than returning a
  short list that looks complete.
"""

from __future__ import annotations

import os
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_FILE_BYTES = 2 * 1024 * 1024      # 2 MiB
_MAX_TOTAL_BYTES = 40 * 1024 * 1024   # 40 MiB
_MAX_MATCHES = 60
_MAX_DEPTH = 10

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_file_contents",
        "description": (
            "Search inside text files under a directory for a literal string, "
            "and report which files contain it and on which lines. Skips binary "
            "files and very large files, and says how many it skipped."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The literal text to look for."},
                "path": {
                    "type": "string",
                    "description": "Directory to search under. Defaults to the current directory.",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Only search files matching this wildcard, e.g. '*.py'. Defaults to all.",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Match case exactly. Defaults to false.",
                },
                "max_matches": {
                    "type": "integer",
                    "description": f"Stop after this many matching lines. Defaults to {_MAX_MATCHES}.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    needle = (arguments.get("text") or "")
    if not needle:
        return "No search text was given, so there is nothing to look for."
    raw_path = (arguments.get("path") or "").strip() or "."
    try:
        root = files.resolve(raw_path)
    except files.PathProblem as exc:
        return str(exc)
    if not root.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              root, "search")
    if root.is_file():
        candidates = [root]
    elif root.is_dir():
        candidates = None
    else:
        return f"{root} is neither a file nor a directory."

    case_sensitive = bool(arguments.get("case_sensitive"))
    file_pattern = (arguments.get("file_pattern") or "").strip()
    try:
        limit = max(1, min(int(arguments.get("max_matches") or _MAX_MATCHES), 500))
    except (TypeError, ValueError):
        limit = _MAX_MATCHES

    import fnmatch

    found: list[str] = []
    searched = 0
    skipped_binary = 0
    skipped_big = 0
    unreadable: list[str] = []
    total_bytes = 0
    truncated = False
    haystack = needle if case_sensitive else needle.lower()

    def consider(path: Path) -> bool:
        """Return False to stop the whole search."""
        nonlocal searched, skipped_binary, skipped_big, total_bytes, truncated
        if file_pattern and not fnmatch.fnmatch(path.name, file_pattern):
            return True
        try:
            size = path.stat().st_size
        except OSError:
            unreadable.append(str(path))
            return True
        if size > _MAX_FILE_BYTES:
            skipped_big += 1
            return True
        if total_bytes + size > _MAX_TOTAL_BYTES:
            truncated = True
            return False
        try:
            data = path.read_bytes()
        except OSError:
            unreadable.append(str(path))
            return True
        if b"\0" in data[:4096]:
            skipped_binary += 1
            return True
        total_bytes += len(data)
        searched += 1
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")
        probe = text if case_sensitive else text.lower()
        for number, line in enumerate(probe.splitlines(), 1):
            if haystack in line:
                shown = text.splitlines()[number - 1].strip()
                found.append(f"  {path}:{number}: {shown[:160]}")
                if len(found) >= limit:
                    truncated = True
                    return False
        return True

    if candidates is not None:
        consider(candidates[0])
    else:
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: unreadable.append(str(e))):
            here = Path(dirpath)
            if len(here.parts) - len(root.parts) >= _MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(filenames):
                if not consider(here / name):
                    break
            if truncated and len(found) >= limit:
                break

    if not found:
        return (
            f"No text file under {root} contains {needle!r}. "
            f"{searched} file(s) were read; {skipped_binary} binary and "
            f"{skipped_big} oversized file(s) were skipped, and "
            f"{len(unreadable)} path(s) could not be read. A skip is not a "
            f"file with no match."
        )

    lines = [f"{len(found)}{'+' if truncated else ''} match(es) for {needle!r}:"]
    lines.extend(found)
    if truncated:
        lines.append(
            "  ... stopped early (match or byte limit). Narrow the path, raise "
            "max_matches, or set file_pattern to search less."
        )
    if skipped_binary or skipped_big or unreadable:
        lines.append(
            f"  note: read {searched} file(s); skipped {skipped_binary} binary, "
            f"{skipped_big} over {files.human_size(_MAX_FILE_BYTES)}, and could "
            f"not read {len(unreadable)}."
        )
    return "\n".join(lines)


SKILLS = [Skill(name="search_file_contents", schema=SCHEMA, run=_run)]
