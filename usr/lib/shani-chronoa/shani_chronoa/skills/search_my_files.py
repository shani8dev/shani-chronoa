"""Search your own files by meaning - or by keyword, and say which.

**The gap this fills.** `local_embed` already indexes conversation turns, and
`search_documents` shells out to the desktop's own index (tracker3 on GNOME,
Baloo on Plasma). Neither answers "what do my notes say about X" over files
Chronoa did not write - which is the question a knowledge base exists for, and
the one that fails on a machine whose desktop index is not running at all.

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

from shani_chronoa import file_index
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_CONSENT_KEY = "file-index-enabled"

_ACTIONS = ("status", "search", "reindex")


def _consent(config) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Indexing your files is turned off. Nothing was read. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


def _root(arguments: dict) -> str:
    return str(arguments.get("path") or "").strip() or str(
        __import__("pathlib").Path.home())


def _run(arguments: dict):
    config = arguments.get("_config")
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    allowed, refusal = _consent(config)
    if not allowed:
        return refusal

    action = (arguments.get("action") or "search").strip().lower()
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
            embed_note = ("the embedding model is not installed, so this is "
                          "keyword-only - it will not find a file that says "
                          "\"the wifi box\" when you ask about routers")
        return (f"{root} would be indexed. The index sits beside Chronoa's other "
                f"state. Text files and PDFs are read; a file that cannot be read "
                f"is named in the report rather than skipped silently. {embed_note}.")

    if action == "reindex":
        report = file_index.index_directory(root, embed=True)
        if "error" in report:
            return report["error"]
        skipped = report.get("skipped", 0)
        problems = report.get("problems") or []
        head = (f"Indexed {report.get('indexed', 0)} file(s) from {root}: "
                f"{report.get('added', 0)} new, {report.get('updated', 0)} changed, "
                f"{skipped} not readable.")
        if report.get("embedded"):
            head += " Embeddings were written, so this searches by meaning."
        else:
            head += (" No embeddings were written - the embedding model did not "
                     "answer, so this is keyword-only.")
        if problems:
            shown = "; ".join(problems[:6])
            more = f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""
            head += f" Not readable: {shown}{more}."
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
        return (f"Nothing found in {root} for {query!r}. {how.capitalize()}. "
                f"If the directory has never been indexed, run 'reindex' first.")
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
            "Search your own files by meaning - documents, notes, code - in a "
            "directory you choose, or your home if you name none. 'search': what "
            "matches a few words. 'reindex': read the directory and build or "
            "refresh the index (needed before the first search). 'status': what "
            "would be indexed and whether this machine can search by meaning. "
            "Needs the 'file-index-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "search (default), reindex, or status.",
                    "default": "search",
                },
                "query": {
                    "type": "string",
                    "description": "For search: a few words about what you are "
                                   "looking for.",
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
