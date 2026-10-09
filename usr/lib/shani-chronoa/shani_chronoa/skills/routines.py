"""Skill: save a routine - one phrase that runs a whole request ("good morning").

Saving runs nothing. When the phrase is said later, the assistant replaces it
with the saved request (`shani_chronoa.routines.expand`), and that request runs
like any other: same tool choice, same consent gates and confirmations.
"""

from __future__ import annotations

from shani_chronoa import routines
from shani_chronoa.skills import Skill

_ACTIONS = ("save", "list", "delete")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "routines",
        "description": (
            "Save a routine: a short phrase (e.g. 'good morning', 'leaving work') that later runs a "
            "whole request, e.g. 'tell me the weather, my calendar today and the top news'. Also "
            "list or delete routines. Saving runs nothing; saying the phrase later runs the request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS), "description": "What to do."},
                "name": {"type": "string", "description": "The phrase that runs it, e.g. 'good morning'."},
                "request": {"type": "string", "description": "For save: what to do when the phrase is said, "
                                                             "written as a request to Chronoa."},
                "phrases": {"type": "array", "items": {"type": "string"},
                            "description": "Other phrases that should run it too."},
            },
            "required": ["action"],
        },
    },
}


def _run(arguments: dict) -> str:
    arguments = arguments if isinstance(arguments, dict) else {}
    action = str(arguments.get("action") or "list").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}, not {action!r}."
    saved = routines.load()
    if action == "list":
        if not saved:
            return "No routines are saved. Save one with a phrase and the request it should run."
        return "\n".join(f"'{name}': {r.get('request', '')}"
                         + (f"  (also: {', '.join(r.get('phrases', []))})" if r.get("phrases") else "")
                         for name, r in sorted(saved.items()))
    name = " ".join(str(arguments.get("name") or "").split()).lower()
    if not name or not isinstance(arguments.get("name"), str):
        return f"{action} needs the routine's name, the phrase that runs it."
    if action == "delete":
        if name not in saved:
            return f"No routine is called {name!r}; saved: {', '.join(sorted(saved)) or 'none'}."
        del saved[name]
        routines.save_all(saved)
        return f"Deleted the {name!r} routine."
    request = arguments.get("request")
    if not isinstance(request, str) or len(request.split()) < 2:
        return "save needs the request the routine runs, written out, e.g. 'tell me the weather and my calendar'."
    extra = [p for p in (arguments.get("phrases") or []) if isinstance(p, str) and p.strip()]
    saved[name] = {"request": request.strip(), "phrases": extra}
    routines.save_all(saved)
    return (f"Saved the {name!r} routine. Saying '{name}' will now run: {request.strip()} "
            "Nothing has run yet.")


def _verify_routine(arguments: dict, tool=None):
    """Post-condition: does the saved store hold what this call claimed?

    Read back from the file rather than from the dict the handler just built -
    the whole failure mode of a writer is that it reports success for a store it
    never managed to write, and that only shows up on a second read.

    `list` has nothing to verify and returns None: nothing checkable, not a
    manufactured pass.
    """
    if not isinstance(arguments, dict):
        return None
    action = str(arguments.get("action") or "list").strip().lower()
    name = " ".join(str(arguments.get("name") or "").split()).lower()
    if action == "save" and name:
        stored = routines.load()
        if name not in stored:
            return (False, f"the routine store holds nothing called {name!r}")
        wanted = arguments.get("request")
        if isinstance(wanted, str) and wanted.strip() and stored[name].get("request") != wanted.strip():
            return (False, f"{name!r} was saved with a different request than the one asked for")
        return (True, f"{name!r} is in the saved routines and expands to its request")
    if action == "delete" and name:
        gone = name not in routines.load()
        return (gone, f"{name!r} is {'absent from' if gone else 'still in'} the saved routines")
    return None


POST_CONDITION = _verify_routine

SKILLS = [Skill(name="routines", schema=SCHEMA, run=_run)]
