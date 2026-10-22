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
    "read_text_file",
    "read_document",
    "search_documents",
    "find_files",
    "write_text_file",
    "edit_file",
    # Its own checklist for a several-step job (see `prompt_clause`).
    "todo_list",
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
    # **The loop, as steps.** Measured 2026-10-15 with Qwen3-1.7B: asked to
    # fix a failing test, it ran pytest three times and never opened the file -
    # "after changing code, check it" told it when to test, not how to fix.
    #
    # **The waste, as a rule.** Measured 2026-10-08 (assistant.py MAX_TOOL_ROUNDS
    # note): a free model used ~30 actions where ~18 sufficed, re-reading a file
    # after every edit to confirm the edit landed. edit_file already returns the
    # new text, so the read is pure waste; the two sentences below say so.
    return (f"You are working on the project in {folder}. Relative paths are "
            "inside it, and run_command already runs there. To fix or change "
            "code: 1) read the file with read_text_file, 2) change it with "
            "edit_file, 3) run the tests or build with run_command and read the "
            "result, 4) if it still fails, read the error and repeat from 2. "
            "When edit_file succeeds the file already holds your change - do not "
            "read it back to confirm; running the tests is the only check that "
            "counts. When you need several files or facts that do not depend on "
            "each other, ask for them together in one step, not one call at a time. "
            "Do not say it is done until the result says it passes. For a job "
            "with several steps, first write them with todo_list (action add, "
            "one per step), set each to in_progress when you start it and "
            "completed only after you have seen it work - reading your own "
            "change is not checking it. If a step cannot be done, set it to "
            "blocked and say why; never mark one completed just to finish. "
            "Do not stop while a step is still open.")


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


# ---------------------------------------------------------------------------
# Keeping going: a Build-mode turn does not stop while its own steps are open.
# ---------------------------------------------------------------------------

#: Extra rounds the assistant may start on its own after the model stops.
MAX_CONTINUATIONS = 3


def _items() -> list:
    try:
        from shani_chronoa.skills import todo_list
        items, _problem = todo_list._load()  # noqa: SLF001 - the one reader
        return list(items or [])
    except Exception:  # noqa: BLE001 - an unreadable list is no open steps
        return []


def snapshot() -> set:
    """Ids already on the list when the turn began."""
    return {int(i.get("id") or 0) for i in _items()}


def open_steps(before: set) -> list:
    """Steps written during this turn that are neither completed nor blocked.

    Only this turn's steps: an old personal to-do never keeps a turn running.
    """
    return [str(i.get("content") or "") for i in _items()
            if int(i.get("id") or 0) not in before
            and i.get("status") not in ("completed", "blocked")]


def continuation(steps: list) -> str:
    """What the assistant says to itself to keep going."""
    listed = "; ".join(steps[:6]) + (f"; and {len(steps) - 6} more" if len(steps) > 6 else "")
    return (f"You stopped with {len(steps)} step(s) still open: {listed}. "
            "Continue with the next one. Mark a step completed only after you "
            "have seen it work; if it cannot be done, mark it blocked and say why.")
