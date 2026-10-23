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
    "phone": "privacy",
    "daemon": "privacy",
    "memory": "privacy",
    # Both are shut by default and both switches are Privacy rows, so a gear
    # here goes where the person can actually turn them on.
    "my_files": "privacy",
    "meetings": "privacy",
    "phone_link": "privacy",
    # `bluetooth-gatt-enabled` is the same privacy switch `devices.py` and
    # `phone.py` route to, so this panel routes there too.
    "wearables": "privacy",
    # `phone-control-enabled`, the same switch the phone panel routes to.
    # A phone reached over adb is not a wearable and not a Bluetooth peer,
    # so it is its own panel - and neither desktop has one.
    "android": "privacy",
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
    # The goals-enabled switch that lets Chronoa save a goal.
    "goals": "privacy",
    "machine": "senses",
    "activity": "tool-activity",
    # The seccomp switch is a Privacy row; it is also on the page itself.
    "sandbox": "privacy",
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
    # Compare two conversations by the questions they asked. A model asked
    # "what did I ask about last week" answers from the conversation it is
    # holding, which is not that question; this needs both sets of questions in
    # hand at once.
    "compare",
    # Your own files, searchable, and the meetings it has recorded. Both were
    # skills with no panel at all until 2026-10-11: reachable only by a model
    # deciding to call them, which is the wrong shape for a box and the wrong
    # shape for a transcript somebody wants to read again.
    "my_files",
    "meetings",
    # The phone over this computer's own LAN link (`lan_link`) and over
    # Bluetooth both live here. The LAN one had one reachable path: a model
    # deciding to call `lan_phone` - there was no way to see what was on the
    # network, or to learn that pairing is specified and not written.
    "phone_link",
    # And the app-driver: whether a mouse and a keyboard can reach a
    # window on *this* desktop. The answer was four measured facts kept
    # in prose, and the panel asks each of them rather than claiming one.
    "driving",
    # This machine
    "senses",
    "models",
    "machine",
    # Everything Chronoa left running, and the button that stops each one. Placed
    # with "This machine" on purpose: it is the panel a person opens when the
    # machine feels slow, and it answers "what is it holding" - not "what
    # features exist". Until 2026-10-11 six background watchers started at
    # startup with a written `stop()` that **nothing in the tree called**, and
    # none of them was named anywhere on screen.
    #
    # **`processes`, not `background`.** The panel was first written as
    # `background.py`, which collides with `shani_chronoa.background` - the module
    # that owns the inventory it draws. `from shani_chronoa.gui.surfaces import
    # background` inside it resolved to **itself**, so `background.inventory` did
    # not exist and the whole layout suite came back as errors. The same shape as
    # `calendar.py` shadowing the stdlib: a name is not just a label, and a
    # collision is not visible until something imports by it.
    "processes",
    # What Chronoa knows
    "memory",
    "learning",
    "voice",
    "listening",
    # One phrase that stands for a whole request. A skill since 2026-10-02, and
    # reachable only by a phrase you had to already remember until 2026-10-11:
    # you could say "good morning" and have it work, and no screen listed the
    # ones you had, named their trigger phrases, or showed what each asks for.
    "routines",
    # This computer's screen on a paired phone. The skill shipped the whole
    # pipeline and reached no GUI at all - the sixth dead module the "who calls
    # this" scan has found here.
    "screen_share",
    # What Chronoa is, function by function. First in its section on purpose:
    # it is the answer to "what is this thing", which is the question a person
    # has before any of the others.
    "inventory",
    # What Chronoa did
    "skills",
    "triggers",
    # Saved multi-step plans and the controls that run them (2026-10-08): the
    # goal queue had no producer or consumer until manage_goals and this.
    "goals",
    "activity",
    "workbench",
    # The editor: open a file, change it, save it. Between `code_hl` (which
    # could colour code) and `write_text_file` (which could change it) there
    # was no editable buffer anywhere in the package - see the module docstring.
    "editor",
    "diff",
    "artifact_store",
    # The dashboards Chronoa has built (the `dashboard` skill), each one a page
    # of charts opened in Chronoa's own browser. The skill shipped with no pane,
    # so a built dashboard was a path to remember.
    "dashboards",
    # Other apps and chat channels: the MCP server and the channel bridge,
    # both complete and both with no UI until 2026-10-08.
    "connections",
    # Desktop and system
    "privacy",
    "model",
    "daemon",
    "calendar",
    "devices",
    "phone",
    # Paired Bluetooth LE devices and what they say about themselves. The
    # reader existed with no panel: `bluetooth_gatt` was reachable only by a
    # model deciding to call it, which is the wrong shape for a person who
    # owns the strap.
    "wearables",
    # A USB-debugging phone over adb. `android_device` was found by the
    # CLI matrix - both images ship adb and nothing used it - and had no
    # panel of any kind.
    "android",
    "desktop",
    "diagnostics",
    # Where commands run, and a probe that shows what the sandbox holds.
    "sandbox",
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
SECTION_ORDER = ("Remembering", "Acting", "Phone and remote", "Sensing",
                 "Thinking", "Health and trust", "Everything else")


#: **`"Phone and remote"`, added 2026-10-12.** Measured on the real sidebar,
#: `"Acting"` held 14 of 33 panels and was not one category: four of them
#: (`phone_link`, `phone`, `screen_share`, `connections`) are about reaching this
#: computer from a phone or over the network, which is a question about *reach*,
#: not about anything Chronoa was asked to do. Putting them under "Acting" meant
#: the only heading that could describe them also described the four verbs, so a
#: person looking for the phone panel had to read past seven things that were not
#: it. One new name, four panels moved, and "Acting" is now nine things that all
#: answer "what can it do for me".
#:
#: `processes` moved the other way, into "Health and trust", beside "Background
#: mode" and "Diagnostics": "What's running" is what the machine is holding, and
#: it is the panel somebody opens when the machine feels slow.
SECTION_PHONE = "Phone and remote"

#: Where a panel that declares no `SECTION` of its own belongs. A table rather
#: than a guess: the panels that predate sections would otherwise all land in
#: "Everything else", which is what happened the first time - and a sidebar
#: showing three of fourteen panels looks like a sidebar with three panels.
DEFAULT_SECTION = {
    "conversations": "Remembering",
    "compare": "Remembering",
    "memory": "Remembering",
    "activity": "Remembering",
    "skills": "Acting",
    "triggers": "Acting",
    "senses": "Sensing",
    "machine": "Sensing",
    "model": "Thinking",
    "privacy": "Health and trust",
    "daemon": "Health and trust",
    "editor": "Acting",
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
