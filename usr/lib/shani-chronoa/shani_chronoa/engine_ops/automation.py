"""Routines, trigger rules and notices as plain data.

Everything here reads or expands what the engine already stores. It never runs
a tool: a routine expands to the request a person would have typed, so the
usual consent gates still decide what - if anything - happens next. A store
that cannot be read is one error row, never a crashed surface or an empty list
that reads as "nothing configured".
"""

from __future__ import annotations

from shani_chronoa import notices as _notices, routines as _routines
from shani_chronoa.triggers import rules as _rules

_RULE_FIELDS = (
    "name",
    "sense",
    "match_mode",
    "substring",
    "keywords",
    "actuator",
    "arguments",
    "cooldown_seconds",
    "enabled",
    "allow_destructive",
    "created_at",
    "last_fired_at",
)


def _problem(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return (text or type(exc).__name__)[:180]


def routines() -> list[dict]:
    """Saved routines: name, saved request, and every phrase that runs it."""
    try:
        saved = _routines.load()
    except Exception as exc:  # noqa: BLE001 - a broken store is one row
        return [{"error": f"the routines could not be read: {_problem(exc)}"}]
    rows = []
    for name in sorted(saved):
        routine = saved[name] if isinstance(saved[name], dict) else {}
        extra = routine.get("phrases")
        phrases = (
            [name] + [p for p in extra if isinstance(p, str)]
            if isinstance(extra, list)
            else [name]
        )
        rows.append(
            {
                "name": str(name),
                "request": str(routine.get("request") or "").strip(),
                "phrases": [str(p).strip() for p in phrases if str(p).strip()],
            }
        )
    return rows


def run_routine(name) -> dict:
    """Expand a routine into the request it stands for. Never execute it."""
    said = str(name or "")
    try:
        found = _routines.expand(said)
    except Exception as exc:  # noqa: BLE001 - errors cross the boundary as data
        return {"error": f"the routine could not be expanded: {_problem(exc)}"}
    if not isinstance(found, (tuple, list)) or len(found) < 2:
        return {"error": f"no routine matches {said!r}"}
    return {"name": found[0], "request": found[1]}


def triggers() -> list[dict]:
    """Armed sense rules, from the same store the firing engine reads."""
    try:
        armed = _rules.RuleStore().all()
    except Exception as exc:  # noqa: BLE001 - a corrupt file is reported
        return [{"error": f"the trigger rules could not be read: {_problem(exc)}"}]
    rows = []
    for rule in armed:
        if callable(getattr(rule, "to_dict", None)):
            row = dict(rule.to_dict())
        else:  # stand-ins and future rule types carry the same attributes
            row = {key: getattr(rule, key, None) for key in _RULE_FIELDS}
        for key in _RULE_FIELDS:
            row.setdefault(key, None)
        rows.append(row)
    return rows


def notices() -> list[dict]:
    """Every kept notice, newest first, dismissed ones included."""
    try:
        return [dict(entry) for entry in _notices.list_all() if isinstance(entry, dict)]
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"the notices could not be read: {_problem(exc)}"}]


def dismiss_notice(notice_id) -> dict:
    """Dismiss one open notice. A second dismiss is an error, not a success."""
    wanted = str(notice_id or "")
    if not wanted:
        return {"error": "a notice id is required"}
    try:
        changed = _notices.dismiss(wanted)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"the notice could not be dismissed: {_problem(exc)}"}
    if not changed:
        return {"error": f"no open notice with id {wanted!r}"}
    return {"dismissed": wanted}
