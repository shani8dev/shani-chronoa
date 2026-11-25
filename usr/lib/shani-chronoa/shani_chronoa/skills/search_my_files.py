"""Search your own files by meaning - or by keyword, and say which.

**The gap this fills.** `local_embed` already indexes conversation turns, and
the desktop keeps its own index (LocalSearch on GNOME, Baloo on Plasma).
Neither answers "what do my notes say about X" over files Chronoa did not
write - which is the question a knowledge base exists for, and the one that
fails on a machine whose desktop index is not running at all.

Both backends answer the same question ("find a file by what is in it"), so
they are one skill with two doors: `action=search` uses Chronoa's own index,
`action=desktop` asks the desktop's. `search_documents` used to be a second
skill; its handler lives here now and its module moved to shani-sysadmin.

**The answer always says which search it was.** With the embedding model, it is
keyword *and* vector, merged by reciprocal rank. Without it - a fresh Arch box,
or this dev machine with no CUDA - it is keyword only, and the sentence says so
rather than returning results that look semantic and are not.

**Unreadable files are reported, never skipped quietly.** `.bin`, an encrypted
PDF, a file with no permission: each is named with the reason. A search over an
index that silently omitted half a directory would be answering a question the
person did not ask.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from urllib.parse import unquote, urlparse

from shani_chronoa import file_index
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_CONSENT_KEY = "file-index-enabled"
_DESKTOP_CONSENT_KEY = "document-search-enabled"

_ACTIONS = ("status", "search", "reindex", "desktop")

# The desktop-index backend, folded in from the old `search_documents` skill.
_DESKTOP_TIMEOUT = 20
_DESKTOP_MAX = 20
_DESKTOP_KINDS = {
    "any": ([], ""),
    "documents": (["-t"], "Document"),
    "images": (["-i"], "Image"),
    "audio": (["--audio"], "Audio"),
    "videos": (["-v"], "Video"),
    "folders": (["-s"], "Folder"),
}


def _consent(config) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"Indexing your files is turned off. Nothing was read. "
            f"Enable '{_CONSENT_KEY}' in Settings to allow it."
        )
    return True, ""


def _desktop_consent(config) -> "tuple[bool, str]":
    if not config.get_bool(_DESKTOP_CONSENT_KEY, False):
        return False, (
            f"searching inside your files is turned off "
            f"(enable '{_DESKTOP_CONSENT_KEY}' in Settings). "
            f"Searching file names (find_files) needs no permission."
        )
    return True, ""


def _desktop_backend():
    """(name, argv prefix) for the index this desktop keeps, or None."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    order = (
        ["baloosearch6", "localsearch", "tracker3"]
        if "kde" in desktop or "plasma" in desktop
        else ["localsearch", "tracker3", "baloosearch6"]
    )
    for tool in order:
        if shutil.which(tool):
            return tool, ([tool] if tool == "baloosearch6" else [tool, "search"])
    return None


def _desktop_paths(tool: str, text: str) -> list:
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
    """Why Baloo cannot answer, from balooctl6 status, or '' when it is running."""
    if shutil.which("balooctl6") is None:
        return ""
    try:
        proc = subprocess.run(
            ["balooctl6", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "balooctl6 did not answer"
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0 or re.search(
        r"could not be opened|disabled|not running", text.lower()
    ):
        return (
            text.splitlines()[0][:120]
            if text
            else f"balooctl6 status exited {proc.returncode}"
        )
    return ""


def _desktop_search(arguments: dict, config) -> str:
    allowed, reason = _desktop_consent(config)
    if not allowed:
        return f"Refusing to search inside your files: {reason}"
    query = " ".join(str(arguments.get("query") or "").split())
    if not query:
        return "What should I look for?"
    if len(query) > 200 or query.startswith("-"):
        return "That query is too long or looks like an option; give the words to search for."
    kind = (arguments.get("kind") or "any").strip().lower()
    if kind not in _DESKTOP_KINDS:
        return f"kind must be one of {', '.join(_DESKTOP_KINDS)}, not {kind!r}."
    try:
        limit = max(1, min(int(arguments.get("limit") or 10), _DESKTOP_MAX))
    except (TypeError, ValueError):
        limit = 10
    found = _desktop_backend()
    if found is None:
        return (
            "This desktop has no file-content index to ask: neither LocalSearch (GNOME) nor Baloo "
            "(Plasma) is installed. File names can still be searched with find_files."
        )
    tool, argv = found
    flags, baloo_type = _DESKTOP_KINDS[kind]
    if tool == "baloosearch6":
        argv = (
            argv
            + ["-l", str(limit)]
            + (["-t", baloo_type] if baloo_type else [])
            + ["--", query]
        )
    else:
        argv = argv + flags + ["-l", str(limit), "--", query]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_DESKTOP_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        return f"The {tool} index did not answer within {_DESKTOP_TIMEOUT}s."
    except OSError as exc:
        return f"Could not run {tool}: {exc}"
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 or re.search(
        r"could not connect|not running|disabled|no such|failed", err.lower()
    ):
        return (
            f"The {tool} index is not available ({err.splitlines()[0][:150] if err else f'exit {proc.returncode}'}). "
            "It may be switched off in the desktop's search settings, or still building. "
            "This is not the same as finding nothing."
        )
    paths = _desktop_paths(tool, proc.stdout or "")[:limit]
    if not paths:
        problem = _baloo_problem() if tool == "baloosearch6" else ""
        if problem:
            return (
                f"The Baloo index is not available ({problem}). File indexing may be switched off in "
                "System Settings > Search, or not built yet. This is not the same as finding nothing."
            )
        return f"The {tool} index has nothing matching {query!r}."
    return (
        f"{len(paths)} file(s) whose contents match {query!r} ({tool}):\n"
        + "\n".join(f"- {p}" for p in paths)
    )


def _root(arguments: dict) -> str:
    return str(arguments.get("path") or "").strip() or str(
        __import__("pathlib").Path.home()
    )


def _run(arguments: dict):
    config = arguments.get("_config")
    if config is None:
        from shani_chronoa.config import ChronoaConfig

        config = ChronoaConfig()
    action = (arguments.get("action") or "search").strip().lower()
    if action == "desktop":
        return _desktop_search(arguments, config)
    allowed, refusal = _consent(config)
    if not allowed:
        return refusal

    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}; {action!r} is not one"

    root = _root(arguments)

    if action == "status":
        try:
            import pathlib

            target = pathlib.Path(root)
        except OSError as exc:
            return f"{root} cannot be read ({exc})."
        if not target.is_dir():
            return f"{root} is not a directory, so there is nothing to index."
        from shani_chronoa import local_embed

        if local_embed.installed():
            embed_note = "the embedding model is here, so this searches by meaning"
        else:
            embed_note = (
                "the embedding model is not installed, so this is "
                "keyword-only - it will not find a file that says "
                '"the wifi box" when you ask about routers'
            )
        return (
            f"{root} would be indexed. The index sits beside Chronoa's other "
            f"state. Text files and PDFs are read; a file that cannot be read "
            f"is named in the report rather than skipped silently. {embed_note}."
        )

    if action == "reindex":
        report = file_index.index_directory(root, embed=True)
        if "error" in report:
            return report["error"]
        skipped = report.get("skipped", 0)
        problems = report.get("problems") or []
        head = (
            f"Indexed {report.get('indexed', 0)} file(s) from {root}: "
            f"{report.get('added', 0)} new, {report.get('updated', 0)} changed, "
            f"{skipped} not readable."
        )
        if report.get("embedded"):
            head += " Embeddings were written, so this searches by meaning."
        else:
            head += (
                " No embeddings were written - the embedding model did not "
                "answer, so this is keyword-only."
            )
        if problems:
            shown = "; ".join(problems[:6])
            more = f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""
            head += f" Not readable: {shown}{more}."
        # **Degraded is not failed.** `problems` means a file that could not be
        # read; `degraded` means a file that was read and indexed without an
        # embedding. Rendering the second under the first's heading reported
        # perfectly-indexed files as unreadable - measured, a 20-file directory
        # produced 20 identical "Not readable: <name>: no embedding server"
        # lines, because the old code put one entry per file into `problems`.
        degraded = report.get("degraded") or []
        if degraded:
            head += " " + ". ".join(degraded[:2]) + ". "
        declined = report.get("skipped_directories") or []
        if declined:
            # **Named, so "not readable" and "not entered" cannot be confused.**
            # Before this the sentence read `2 new, 0 changed, 0 not readable`
            # about a directory holding five files in cache-shaped directories,
            # and a reader concluded there had been two.
            shown = ", ".join(declined[:6])
            more = f" and {len(declined) - 6} more" if len(declined) > 6 else ""
            head += (
                f" Left out by design: {shown}{more} - cache, build and "
                "repository directories, which are the machine's and not "
                "yours. Name one directly to index it."
            )
        return head

    # action == "search"
    query = str(arguments.get("query") or "").strip()
    if not query:
        return "What should I look for? Give a few words."
    try:
        limit = int(arguments.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    limit = min(max(limit, 1), 25)

    hits, how = file_index.search(root, query, limit=limit, embed=True)
    if not hits:
        # **An empty answer names the reason it is empty**, because "nothing
        # matched" and "nothing is indexed" are different and only one of them
        # is this caller's fault.
        return (
            f"Nothing found in {root} for {query!r}. {how.capitalize()}. "
            f"If the directory has never been indexed, run 'reindex' first."
        )
    lines = [f"{len(hits)} file(s) in {root} match {query!r} ({how}):"]
    for hit in hits:
        lines.append(f"- {hit['path']} [{hit['kind']}, {hit['size']} bytes]")
        lines.append(f"    {hit['excerpt']}")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_my_files",
        "description": (
            "Search your own files by their contents - documents, notes, code - "
            "either in a directory you choose with Chronoa's own index, or "
            "through the desktop's index (LocalSearch on GNOME, Baloo on Plasma). "
            "'search' (default): what matches a few words. 'desktop': ask the "
            "desktop's index instead, needing 'document-search-enabled'. "
            "'reindex': read the directory and build or refresh Chronoa's index "
            "(needed before the first search). 'status': what would be indexed "
            "and whether this machine can search by meaning. Needs the "
            "'file-index-enabled' consent key for its own index."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": (
                        "search (default), desktop (the desktop's own index), "
                        "reindex, or status."
                    ),
                    "default": "search",
                },
                "query": {
                    "type": "string",
                    "description": "For search/desktop: a few words about what "
                    "you are looking for.",
                },
                "kind": {
                    "type": "string",
                    "enum": list(_DESKTOP_KINDS),
                    "description": "For desktop: limit to a kind of file.",
                },
                "path": {
                    "type": "string",
                    "description": "Which directory to search. Defaults to your "
                    "home directory.",
                },
                "limit": {
                    "type": "integer",
                    "description": "For search: how many files to return, 1-25 "
                    "(default 8).",
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="search_my_files", schema=SCHEMA, run=_run)]
