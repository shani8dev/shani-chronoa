"""The Inventory panel: what Chronoa is, function by function.

Eight lights in the window answer *what is it doing right now*. This answers a
different and larger question: **what is it, and how much of that is real?** It
renders `organism.INVENTORY` - every function Chronoa has, the human function
each one is the equivalent of, and the files that implement it.

It is deliberately not a marketing page. The whole value of mapping a machine to
a body is that you can hold the machine against your own: "amygdala - fast
evaluation of danger" is checkable, so the panel says `built` and names
`guardrail.py`, and where the answer is `part` it says *which part*, because
"partly implemented" without a part named is the one sentence in this program
that is worse than silence. Two organs are `absent` and the panel shows them
anyway, in grey, next to what is missing - an inventory that hid its gaps could
be believed into looking complete.

Nothing here polls, schedules, or writes. The inventory is static data by design;
the *live* half of the same story is the strip, and this panel is where the two
are reconciled - an organ whose state is `part` shows a status icon and names
the organ whose light covers what does work.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # type: ignore

from ... import organism
from . import common

TITLE = "Inventory"
ICON = "applications-utilities-symbolic"
SECTION = "Health and trust"

#: One icon per state, and its accessible name. A colour alone would fail
#: anyone who cannot see it, and a grey box would fail anyone trying to skim.
_STATE_ICON = {
    organism.BUILT: ("object-select-symbolic", "Built"),
    organism.PART: ("dialog-warning-symbolic", "Partly built"),
    organism.ABSENT: ("view-conceal-symbolic", "Not built"),
}

#: What the light covers this organ, in words. Only shown when the organ is not
#: fully built, because otherwise it is noise on a row that already says "built".
_COVER = {
    "skin": "the skin light",
    "immune": None,
    "barrier": None,
    "ears": "the ears light",
    "eyes": "the eyes light",
    "nose": "the nose light",
    "skin_touch": "the nose light",
    "tongue": None,
    "balance": None,
    "mouth": "the mouth light",
    "hands": "the hands light",
    "legs": None,
    "lungs": None,
    "brain": "the brain light",
    "prefrontal": None,
    "cerebellum": None,
    "amygdala": None,
    "attention": None,
    "heart": None,
    "temperature": "the nose light",
    "homeostasis": None,
    "circadian": None,
    "memory": "the memory light",
    "consolidation": None,
    "learning": None,
    "endocrine": None,
}


def _add(group: Gtk.Widget, row: Gtk.Widget) -> None:
    """`Adw.PreferencesGroup.add` when there is one, `Gtk.Box.append` when not.

    The same two-line answer `surfaces.voice` gives: asking which is one line,
    and it is why the plain-GTK fallback is a fallback that works.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(row)
    else:
        group.append(row)


def _static_icon(icon_name: str) -> Gtk.Widget:
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_valign(Gtk.Align.CENTER)
    return icon


def _subtitle(organ: "organism.Organ") -> str:
    parts = [organ.function]
    if organ.state == organism.BUILT:
        covered = _COVER.get(organ.key)
        if covered:
            parts.append(f"Visible as {covered} while it happens.")
        return " ".join(parts)
    # The honest sentence: what it is, then exactly what is missing.
    return " ".join([organ.function, organ.missing])


def _organ_row(organ: "organism.Organ") -> Gtk.Widget:
    icon_name, state_label = _STATE_ICON[organ.state]
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_valign(Gtk.Align.CENTER)
    try:
        icon.update_property([Gtk.AccessibleProperty.DESCRIPTION], [state_label])
    except Exception:
        pass
    row = common.row(organ.anatomy, _subtitle(organ), suffix=icon)
    # Whether the organ has a status light, and where it is - the panel's whole
    # claim is that a built organ and an instrumented one are different things,
    # and it asserted that only in the summary group. Per row, an organ with no
    # indicator says so, so "the panel says a light means something is happening"
    # and "this row has no light" are reconciled on the same screen. `is_live`
    # was the existing answer and had no caller.
    light = ("The strip has a light for this one: " + organ.indicator
             if organ.is_live else
             "No status light for this one yet - a gap in the instrument, not a "
             "claim that the function is idle.")
    row.set_tooltip_text(
        f"{organ.anatomy} - {state_label}\n"
        f"{organ.function}\n\n"
        f"{light}\n\n"
        f"Implemented in: {', '.join(organ.code)}")
    try:
        row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [state_label])
    except Exception:
        pass
    if organ.state != organism.BUILT:
        # Dimmed, not hidden: a gap you cannot see is a gap you do not fix. The
        # rows are `Adw.ActionRow`s, whose title labels are private, so the dim
        # goes on the row - which is what makes the whole entry recede rather
        # than only the icon.
        row.add_css_class("dim-label")
    return row


def build(_app=None) -> Gtk.Widget:
    counts = organism.tally()
    #: One tally, read once: the row below and the sidebar's dot both come from
    #: this, so the count in the panel and the colour beside its row cannot be
    #: built from two different walks of `organism`.
    recorder = common.StatusRecorder()
    page, _put = common.surface(
        TITLE,
        f"{counts['total']} functions, mapped to what a body does. "
        f"{counts[organism.BUILT]} built, {counts[organism.PART]} partial, "
        f"{counts[organism.ABSENT]} not yet. The lights in the window cover "
        f"{len(organism.live_organs())} of them.",
    )

    # One container, put once. `common.surface`'s `set_content` *replaces* the
    # toolbar's content slot - it is `toolbar.set_content(child)`, not `append` -
    # so calling it per group silently shows only the last group. That is the
    # shape of bug that survives a read-through and dies in a screenshot.
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    column.set_margin_top(12)
    column.set_margin_bottom(18)

    # The panel's own health, above every group: how many
    # functions are built, how many are partial, how many are
    # absent. One row, one dot, one word - the question the
    # panel is opened for, before the rows that hold the organs.
    if counts[organism.ABSENT] == 0:
        status = recorder.row(
            common.STATUS_OK,
            f"{counts[organism.BUILT]} of {counts['total']} functions built",
            f"{counts[organism.PART]} partial, {counts[organism.ABSENT]} "
            "absent")
    elif counts[organism.BUILT] + counts[organism.PART] > 0:
        status = recorder.row(
            common.STATUS_ATTENTION,
            f"{counts[organism.ABSENT]} of {counts['total']} functions absent",
            f"{counts[organism.BUILT]} built, {counts[organism.PART]} "
            f"partial, {counts[organism.ABSENT]} absent")
    else:
        status = recorder.row(
            common.STATUS_UNKNOWN,
            "No functions built",
            f"{counts[organism.ABSENT]} absent, {counts[organism.PART]} "
            "partial")
    _add(column, status)

    summary = common.group("What this is")
    _add(summary, common.row(
        "Chronoa is not trying to be a person",
        "It has the parts of a person that need watching - hearing, sight, a "
        "boundary, memory, judgement - and none of the parts that would need "
        "pretending. Each row below names the function, not the resemblance.",
        _static_icon("dialog-information-symbolic")))
    _add(summary, common.row(
        "A light means something is happening now",
        "An organ with no light has no indicator yet. That is a gap in the "
        "instrument, not a claim that the function is idle.",
        _static_icon("preferences-system-notifications-symbolic")))
    _add(summary, common.row(
        "Where the gaps are",
        f"{counts[organism.PART]} functions are partly there and "
        f"{counts[organism.ABSENT]} are not started. Both are listed below, "
        "with what is missing, rather than left out.",
        _static_icon("dialog-warning-symbolic")))
    column.append(summary)

    for system, members in organism.by_system():
        group = common.group(system, f"{len(members)} functions")
        for organ in members:
            _add(group, _organ_row(organ))
        column.append(group)

    # What will not be built, beside what has been. Part 4 of
    # ARCHITECTURE-TARGET.md lived only in that document, so the app could not
    # tell a person "not yet" from "not ever" - and a request it refuses by
    # design read like a feature that was missing.
    decided = common.group(
        "Deliberately not built",
        "Decisions, not gaps. Each stays as it is until a person decides otherwise.")
    gpu, drivers = organism.compute_gpu()
    for term, what, decision, why in organism.STANDING_DECISIONS:
        icon = "action-unavailable-symbolic"
        subtitle = f"{decision} - {why}"
        if term in organism.HARDWARE_GATED:
            # About this machine, not about Chronoa: a refusal here and only a
            # gap on a machine with a compute GPU.
            if gpu:
                icon = "dialog-information-symbolic"
                subtitle = (f"possible on this machine ({gpu} GPU) - not built yet; "
                            "without such a GPU one item takes minutes on the processor")
            else:
                found = ", ".join(drivers) if drivers else "no GPU"
                subtitle = (f"out of scope on this hardware - {why} "
                            f"(found: {found}; an NVIDIA or AMD GPU would change this)")
        mark = Gtk.Image.new_from_icon_name(icon)
        mark.set_valign(Gtk.Align.CENTER)
        row = common.row(what, subtitle, suffix=mark)
        row.set_tooltip_text(f"{what}: {subtitle}.")
        row.add_css_class("standing-decision")
        _add(decided, row)
    column.append(decided)

    # **In a scroller, like every other surface.** This one was the only panel
    # whose content went straight into the toolbar's content slot, so a window
    # shorter than its twenty-odd rows could not reach the bottom of them - the
    # panel is a list of every function and its state, and the last few are
    # exactly the ones nobody can scroll to. Found by measuring all twenty
    # surfaces for a `Gtk.ScrolledWindow` rather than by looking at them:
    # nineteen had one and this had none.
    _put(common.scrolled(column))
    page.status = recorder.status
    return page
