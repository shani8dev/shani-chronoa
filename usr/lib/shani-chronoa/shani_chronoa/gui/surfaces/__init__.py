"""Every place Chronoa has something to show, in one registry.

The sidebar lists these; choosing one swaps the center of the window to the
widget its `build` returns. `chat` is built by the window itself, everything
here is built by a `surfaces.<name>` module. What "built" means is the same for
all of them: a widget that says, for whatever it shows, what it is showing it
from - so that an answer on any of these can be checked against the thing
producing it rather than believed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

#: name -> (title, icon, build). `build(app)` returns a widget that must
#: not crash the window when the data underneath is missing, corrupt or stale -
#: an `Adw.StatusPage`-shaped box with the honest reason is a success case, an
#: exception is a bug.
Surfaces = Dict[str, Tuple[str, str, Callable[[Any], Any]]]


#: Panel id -> the settings section that holds the controls for what it reports.
#:
#: **Only where the settings window genuinely has a control.** A panel with no
#: entry here gets no gear on its row, and that is the point: the review this came
#: from wanted one-click access to Settings from every panel, but a gear on a row
#: whose subject Settings does not govern is a button that opens a page of
#: unrelated switches - which is the same dead end in a different costume, and
#: harder to recognise because it *does* open something.
#:
#: Read from the settings window's own declaration rather than from a list of its
#: group titles: it registers fixed ids (`senses`, `privacy`, `approvals`,
#: `tool-activity`, `voice`, `models`, `system`) and derives them from titles that
#: may be reworded. `tests/test_settings_targets_resolve.py` resolves every entry
#: here against the real registry, so a section that is renamed or dropped fails
#: there rather than producing a gear that goes nowhere.
SETTINGS_TARGETS = {
    # The panel *is* the list; the switches that grant each of its senses are in
    # Senses, and a person looking at a refused sense wants the switch beside it.
    "senses": "senses",
    # Consent keys are all in Privacy, whatever else the panel is about: the
    # calendar's, the phone's, memory's and the trigger gate all live there.
    "calendar": "privacy",
    "devices": "privacy",
    "daemon": "privacy",
    "memory": "privacy",
    "triggers": "tool-activity",
    "skills": "tool-activity",
    "voice": "voice",
    "model": "models",
    "models": "models",
    "privacy": "privacy",
    # The `gateways` setting that decides which chat channels are accepted.
    "connections": "privacy",
    # Panels that named a setting they had no route to (found 2026-10-08 by
    # matching each gearless panel's `*-enabled` mentions against Settings):
    # Desktop reports the global-shortcut and document-search switches, which
    # live in Privacy; Machine's refused rows are the Senses switches; Activity
    # is the log whose settings are Tool activity.
    "desktop": "privacy",
    "machine": "senses",
    "activity": "tool-activity",
}


def settings_target(name: str) -> "str | None":
    """The `pages.show` target for a panel's settings, or None if it has none.

    `settings:<section>` rather than a bare section id, so the whole of the
    navigation goes through one registry and an ambiguous bare id cannot resolve
    to whichever window happens to have answered first.
    """
    section = SETTINGS_TARGETS.get(name)
    return f"settings:{section}" if section else None


def all_surfaces() -> "Surfaces":
    """Every surface whose module imports, and the reason one did not.

    A panel that is missing or broken is a hole in the sidebar, not a window
    that will not open - which is what a bare `import` of all eight would make
    it. The failure is logged with its traceback rather than swallowed, because
    a surface that silently never appears is the dead-code class this repo keeps
    paying for.
    """
    import importlib
    import logging

    logger = logging.getLogger(__name__)
    out: "Surfaces" = {}
    for name in SURFACE_IDS:
        try:
            module = importlib.import_module(f"shani_chronoa.gui.surfaces.{name}")
        except Exception:
            logger.warning("Surface %r is unavailable", name, exc_info=True)
            continue
        out[name] = (module.TITLE, module.ICON, module.build)
    return out


#: The surfaces the sidebar offers, in the order it offers them. Chat first:
#: it is what the window opens on, and it is not a surface module.
SURFACE_IDS = (
    # Conversation
    "conversations",
    # This machine
    "senses",
    "models",
    "machine",
    # What Chronoa knows
    "memory",
    "learning",
    "voice",
    # What Chronoa is, function by function. First in its section on purpose:
    # it is the answer to "what is this thing", which is the question a person
    # has before any of the others.
    "inventory",
    # What Chronoa did
    "skills",
    "triggers",
    "activity",
    "workbench",
    "diff",
    "artifact_store",
    # Other apps and chat channels: the MCP server and the channel bridge,
    # both complete and both with no UI until 2026-10-08.
    "connections",
    # Desktop and system
    "privacy",
    "model",
    "daemon",
    "calendar",
    "devices",
    "desktop",
    "diagnostics",
    "export",
)

#: The order the sidebar groups its sections in, named for the organism's own
#: organs - the same words the organ lights above the composer use (remembering,
#: acting, sensing, thinking), so the sidebar and the strip describe one thing.
#:
#: The old headings - "This machine", "What Chronoa knows", "What Chronoa did",
#: "Desktop and system" - had stopped meaning anything by the time 22 panels were
#: filed under them: Models sat under "This machine", Voice under "What Chronoa
#: knows", Skills under "What Chronoa did", Diagnostics beside Artifacts. Each
#: heading now answers one question, in the order a person asks them: what were
#: we saying and what does it keep, what has it done and can do, what does it
#: perceive, what is it thinking with, and is it healthy and trustworthy.
#:
#: There is no "Conversation" section, and there never should be: the sidebar
#: opens with an untitled "Conversation" row for the live chat, and a heading by
#: that name above a "Conversations" panel would give it two things called
#: Conversation. The saved list leads "Remembering" instead.
SECTION_ORDER = ("Remembering", "Acting", "Sensing", "Thinking",
                 "Health and trust", "Everything else")


#: Where a panel that declares no `SECTION` of its own belongs. A table rather
#: than a guess: the panels that predate sections would otherwise all land in
#: "Everything else", which is what happened the first time - and a sidebar
#: showing three of fourteen panels looks like a sidebar with three panels.
DEFAULT_SECTION = {
    "conversations": "Remembering",
    "memory": "Remembering",
    "activity": "Remembering",
    "skills": "Acting",
    "triggers": "Acting",
    "senses": "Sensing",
    "machine": "Sensing",
    "model": "Thinking",
    "privacy": "Health and trust",
    "daemon": "Health and trust",
}


def sections() -> "Dict[str, str]":
    """id -> the section it belongs in, read from the module's own `SECTION`.

    A panel that declares no `SECTION` of its own falls back to
    `DEFAULT_SECTION`. That fallback is a deliberate table rather than a guess:
    the ten panels that predate sections would otherwise all land in "Everything
    else", which is exactly what happened the first time - and a sidebar that
    showed three of fourteen panels looked like a sidebar with three panels.
    """
    import importlib

    out: "Dict[str, str]" = {}
    for name in SURFACE_IDS:
        try:
            module = importlib.import_module(f"shani_chronoa.gui.surfaces.{name}")
            declared = getattr(module, "SECTION", "") or ""
        except Exception:
            declared = ""
        out[name] = declared or DEFAULT_SECTION.get(name, "Everything else")
    return out


def available_surfaces() -> "Surfaces":
    return all_surfaces()
