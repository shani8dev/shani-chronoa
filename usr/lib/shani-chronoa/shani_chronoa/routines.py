"""Routines: one phrase that stands for a whole request, like "good morning".

Siri Shortcuts and Google Assistant routines both do this: "Good morning" reads
the weather, the day's calendar and the news. Here a routine is a saved
*request*, not a saved list of tool calls: when someone says its name, the
assistant replaces the utterance with the request before anything else sees it
(`Assistant.handle`), so the model plans it like any other request and every
tool still meets its own consent gate, confirmation and sandbox. A routine can
therefore never do more than the same request typed out could.

Stored as JSON under the data dir; `skills/routines.py` saves, lists and deletes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from shani_chronoa import files


def _path() -> Path:
    return files.data_home() / "shani-chronoa" / "routines.json"


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def load() -> Dict[str, dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_all(routines: Dict[str, dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(routines, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def phrases(name: str, routine: dict) -> List[str]:
    """Everything that runs this routine: its name, its extra phrases, "run my <name> routine"."""
    said = [name] + [p for p in routine.get("phrases", []) if isinstance(p, str)]
    return [_norm(p) for p in said if _norm(p)]


_RUN = re.compile(r"^(?:please )?(?:run|start|do|begin)(?: the| my)? (.+?)(?: routine)?(?: please)?$")


def expand(utterance: str) -> Optional[tuple]:
    """(routine name, the request it stands for) when `utterance` names a routine, else None.

    Only a whole-utterance match: "good morning" runs the routine, "is it a good
    morning for a walk" does not.
    """
    said = _norm(utterance)
    if not said:
        return None
    asked = _RUN.match(said)
    candidates = {said} | ({asked.group(1)} if asked else set())
    for name, routine in load().items():
        if candidates & set(phrases(name, routine)):
            request = str(routine.get("request") or "").strip()
            if request:
                return name, request
    return None
