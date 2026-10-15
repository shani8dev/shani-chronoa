"""Build mode: work on the files, and keep the file tools in reach.

The third mode, and the smallest one that earns its place. General turns ask
for `read_document` and `edit_file` by name or not at all; `select_tools`
scores 256 schemas against the request and hands over `DEFAULT_LIMIT` of them.
A turn about a diff will usually reach a file tool and a turn about a log
usually will not, because the request said "diff" and not "file".

Build mode does not remove anything. It **guarantees the file tools are in the
set the model is shown**, so a turn about a bug that happens to be in a file
does not lose the file tool to a ranking that happened to prefer something
else.

**This is the weakest of the three modes and is written as such.** Plan mode is
a check in the dispatch path; auto mode is a refusal in it. Build mode changes
which schemas are *offered* - a strong nudge, not a gate. Nothing refuses a
general tool because build mode is on, because that would make a code turn
unable to open a browser, and the failure would arrive as "I cannot do that"
rather than as the reason. This module says "keeps in reach", never "blocks".
"""

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_enabled = False
#: The project folder, or "". Set from the `project-dir` setting by the app.
_project = ""

#: The tools a code turn always has. **Named from what exists** rather than from
#: what a coding assistant ought to have: `select_tools` ignores a name that is
#: not in `tools.TOOLS`, so naming a retired tool costs nothing and does not
#: pretend. Verified present in this build: all seven.
BUILD_TOOLS = (
    "list_directory",
    "read_document",
    "search_documents",
    "find_files",
    "write_text_file",
    "edit_file",
    "find_and_replace",
    # Closing the loop: run the tests the edit was for. Gated by its own key.
    "run_command",
    "git_inspect",
)


def is_enabled() -> bool:
    return _enabled


def set_enabled(on: bool) -> bool:
    """Turn build mode on or off. Returns the state in force afterwards."""
    global _enabled
    with _lock:
        _enabled = bool(on)
    return _enabled


def set_project(path: str) -> str:
    """Set the project folder ("" for none). Returns what is in force."""
    global _project
    with _lock:
        _project = str(path or "").strip()
    return _project


def project() -> str:
    """The project folder while Build mode is on, else "".

    **Only in Build mode.** A project chosen once must not quietly redirect
    "open notes.txt" in a general conversation into a code folder.
    """
    with _lock:
        return _project if _enabled else ""


def prompt_clause() -> str:
    """What the model is told about the project, or "" without one."""
    folder = project()
    if not folder:
        return ""
    return (f"You are working on the project in {folder}. Relative paths are "
            "inside it. After changing code, check it: run the project's tests "
            "or build with run_command and read the result before saying it is "
            "done.")


def extra_tools() -> "tuple":
    """The file tools to keep in front of the model this turn.

    **`()` when off**, so a caller can add this to `in_use` unconditionally
    rather than branching - and a caller that forgets the branch then gets the
    ordinary behaviour rather than a crash.
    """
    with _lock:
        if not _enabled:
            return ()
        return tuple(BUILD_TOOLS)


def describe() -> str:
    """One sentence for the strip's tooltip."""
    if _enabled:
        return ("Code: it is working on your files, and the file tools are "
                "always in reach for it. It may still use anything else it "
                "needs.")
    return ("General: it answers anything, using whatever tools fit the "
            "question.")


def read_only_note() -> str:
    """What became of the Read only chip, for anyone looking for it.

    Read-only is still a posture - `permissions.EXPLORE` - and still in
    Settings. It left the strip because the chip it shared a word with ("what it
    may change") was also plan mode's, so one control was standing in for a
    permission and a workflow at once and could report either. Build mode took
    its place because "read only" and "work on my code" are the same request
    said two ways, and the permission-layer version of it is the stricter one a
    person can still choose in Settings.
    """
    return ("Read only is not on the strip any more - it is Settings > Privacy "
            "> Explore first, which refuses anything that is not a read-only "
            "tool.")