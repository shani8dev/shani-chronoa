"""Skill: search inside the user's documents, through the desktop's own index.

`find_files` matches names; this matches *contents* - "the PDF where I wrote
about the lease" - using the index the desktop already keeps, so nothing is
read or indexed by Chronoa itself:

- GNOME: LocalSearch (`localsearch search`; `tracker3 search` on older GNOME);
- Plasma: Baloo (`baloosearch6`).

Gated by `document-search-enabled`, off by default: what is *in* someone's
files is theirs, and an answer quoting a document they forgot was indexed is
the surprise this switch exists to prevent. An index that is empty, disabled
or not running is reported as such - "nothing found" from an index that never
ran would be a confident wrong answer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from urllib.parse import unquote, urlparse

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "document-search-enabled"
_TIMEOUT = 20
MAX_RESULTS = 20
_KINDS = {"any": ([], ""), "documents": (["-t"], "Document"), "images": (["-i"], "Image"),
          "audio": (["--audio"], "Audio"), "videos": (["-v"], "Video"), "folders": (["-s"], "Folder")}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": (
            "Search inside the user's files by their contents, using the desktop's own search index "
            "(LocalSearch on GNOME, Baloo on Plasma). Requires the 'document-search-enabled' consent key."
        ),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words to look for in the files."},
            "kind": {"type": "string", "enum": list(_KINDS), "description": "Limit to a kind of file."},
            "limit": {"type": "integer", "description": f"At most this many results (1-{MAX_RESULTS})."},
        }, "required": ["query"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"searching inside your files is turned off (enable '{_CONSENT_KEY}' in Settings). "
                       f"Searching file names (find_files) needs no permission.")
    return True, ""


def backend() -> "tuple[str, list[str]] | None":
    """(name, argv prefix) for the index this desktop keeps, or None."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    order = (["baloosearch6", "localsearch", "tracker3"] if "kde" in desktop or "plasma" in desktop
             else ["localsearch", "tracker3", "baloosearch6"])
    for tool in order:
        if shutil.which(tool):
            return tool, ([tool] if tool == "baloosearch6" else [tool, "search"])
    return None


def _paths_from_output(tool: str, text: str) -> "list[str]":
    out = []
    for line in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if tool == "baloosearch6":
            if line.startswith("/"):
                out.append(line)
            continue
        m = re.search(r"(file://\S+)", line)
        if m:
            out.append(unquote(urlparse(m.group(1)).path))
    return list(dict.fromkeys(out))


def _baloo_problem() -> str:
    """Why Baloo cannot answer, from balooctl6 status, or '' when it is running with an index."""
    if shutil.which("balooctl6") is None:
        return ""
    try:
        proc = subprocess.run(["balooctl6", "status"], capture_output=True, text=True, timeout=10, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return "balooctl6 did not answer"
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0 or re.search(r"could not be opened|disabled|not running", text.lower()):
        return text.splitlines()[0][:120] if text else f"balooctl6 status exited {proc.returncode}"
    return ""


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to search inside your files: {reason}"
    query = " ".join(str(arguments.get("query") or "").split())
    if not query:
        return "What should I look for?"
    if len(query) > 200 or query.startswith("-"):
        return "That query is too long or looks like an option; give the words to search for."
    kind = (arguments.get("kind") or "any").strip().lower()
    if kind not in _KINDS:
        return f"kind must be one of {', '.join(_KINDS)}, not {kind!r}."
    try:
        limit = max(1, min(int(arguments.get("limit") or 10), MAX_RESULTS))
    except (TypeError, ValueError):
        limit = 10
    found = backend()
    if found is None:
        return ("This desktop has no file-content index to ask: neither LocalSearch (GNOME) nor Baloo "
                "(Plasma) is installed. File names can still be searched with find_files.")
    tool, argv = found
    flags, baloo_type = _KINDS[kind]
    if tool == "baloosearch6":
        argv = argv + ["-l", str(limit)] + (["-t", baloo_type] if baloo_type else []) + ["--", query]
    else:
        argv = argv + flags + ["-l", str(limit), "--", query]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"The {tool} index did not answer within {_TIMEOUT}s."
    except OSError as exc:
        return f"Could not run {tool}: {exc}"
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 or re.search(r"could not connect|not running|disabled|no such|failed", err.lower()):
        return (f"The {tool} index is not available ({err.splitlines()[0][:150] if err else f'exit {proc.returncode}'}). "
                "It may be switched off in the desktop's search settings, or still building. "
                "This is not the same as finding nothing.")
    paths = _paths_from_output(tool, proc.stdout or "")[:limit]
    if not paths:
        problem = _baloo_problem() if tool == "baloosearch6" else ""
        if problem:
            # baloosearch6 exits 0 with no output when there is no index at all
            # (measured on the Plasma image); "nothing found" would then be a guess.
            return (f"The Baloo index is not available ({problem}). File indexing may be switched off in "
                    "System Settings > Search, or not built yet. This is not the same as finding nothing.")
        return f"The {tool} index has nothing matching {query!r}."
    return f"{len(paths)} file(s) whose contents match {query!r} ({tool}):\n" + "\n".join(f"- {p}" for p in paths)


SKILLS = [Skill(name="search_documents", schema=SCHEMA, run=_run)]
