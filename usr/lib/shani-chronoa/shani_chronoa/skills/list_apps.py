"""Skill: list the applications this machine can actually launch.

`open_application` can start an app by name, but nothing could tell the
assistant what exists to start - so "open my browser" either worked or was a
guess. This is the discovery half of that pair.

The list is built from `Gio.AppInfo.get_all()`, the *same* source
`open_application` launches from, and filtered with the same
`app_info.should_show()` test. That is deliberate: hand-parsing `.desktop` files
would be easy to get subtly wrong (localised `Name`, `NoDisplay`, `Hidden`,
duplicate ids across `XDG_DATA_DIRS`, a `TryExec` that does not exist) and would
drift from the launcher, producing apps this skill offers and
`open_application` then refuses to open. One source of truth means the offered
list and the launchable list are the same list by construction.

Two honesty rules, because the empty answer is the dangerous one:

- `should_show()` False means the entry is `NoDisplay`/`Hidden`, and
  `open_application` skips those too - so they are not offered. Their count is
  reported instead, since a user who knows an app is installed and does not see
  it deserves an explanation rather than a confident "it is not installed".
- An empty result is not reported as "this machine has no applications". Gio
  returning nothing usually means the desktop-entry database is missing, which
  looks identical to an empty machine from here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # type: ignore

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_apps",
        "description": (
            "List the applications installed on this machine, optionally "
            "filtered by a search term, so a request to open something can be "
            "answered with what actually exists rather than a guess."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": (
                        "Optional text to match against application names, "
                        "desktop ids and their search keywords, e.g. 'browser' "
                        "or 'terminal'. Omit to list everything."
                    ),
                },
            },
        },
    },
}

_MAX_SHOWN = 40


@dataclass(frozen=True)
class AppEntry:
    display: str
    app_id: str
    keywords: tuple[str, ...]


def _entries() -> tuple[list[AppEntry], int]:
    """Every launchable app, plus how many were hidden by NoDisplay/Hidden."""
    shown: list[AppEntry] = []
    hidden = 0
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            hidden += 1
            continue
        display = info.get_display_name() or info.get_id() or ""
        if not display:
            continue
        try:
            keywords = tuple(info.get_keywords() or ())
        except (AttributeError, TypeError):
            keywords = ()
        shown.append(AppEntry(
            display=display,
            app_id=(info.get_id() or "").removesuffix(".desktop"),
            keywords=keywords,
        ))
    return shown, hidden


def _matches(entry: AppEntry, needle: str) -> bool:
    return (
        needle in entry.display.lower()
        or needle in entry.app_id.lower()
        or any(needle in k.lower() for k in entry.keywords)
    )


def _run(arguments: dict) -> str:
    search = str(arguments.get("search") or "").strip().lower()
    apps, hidden = _entries()
    if not apps:
        return (
            "No launchable applications were found. That usually means the "
            "desktop-entry database is missing or unreadable rather than that "
            "this machine has no applications installed - I cannot tell the "
            "difference from here."
        )

    if search:
        matches = sorted(
            (a for a in apps if _matches(a, search)), key=lambda a: a.display.lower())
        if not matches:
            return (
                f"No installed application matches '{search}'. "
                f"{len(apps)} are installed; ask again with a broader term, or "
                f"without one to see the whole list."
            )
    else:
        matches = sorted(apps, key=lambda a: a.display.lower())

    lines = [
        f"{m.display} ({m.app_id})" for m in matches[:_MAX_SHOWN]
    ]
    if len(matches) > _MAX_SHOWN:
        lines.append(f"... and {len(matches) - _MAX_SHOWN} more.")
    if hidden:
        lines.append(
            f"({hidden} further entries are installed but marked not-show-in-menu "
            f"and are not offered, because the launcher skips those too.)"
        )
    return "\n".join(lines)


SKILLS = [Skill(name="list_apps", schema=_SCHEMA, run=_run)]
