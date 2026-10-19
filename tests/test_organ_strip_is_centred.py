"""The organ lights sit at the end of the status row, measured on the cells.

`tests/test_now_rail.py::test_the_organ_lights_are_separated_by_a_gap` exists
because a width test passed while the strip was one unreadable run-on string.
This is the same class of defect from the other side, and it is why this file
measures rather than asserts a property.

**The defect it records, and the change that superseded it.** AGENTS.md carried
the strip as an open lead: *"is the row aligned? labels span x=296..791 (centre
543) while the mode chips below span 477..782 (centre 629) - **86px off**"*, with
two attempted fixes *"measurably no-ops"*. The first repair was
`OrganStrip.do_measure` reporting the real sum of the cells' widths instead of
`Gtk.FlowBox`'s widest-child-x-count, so the strip stopped claiming 815px for
479px of lights.

**The row was then redesigned, and this file's premise went with it.** The two
strips are one row now - "what Chronoa *is* on the left, what it is *doing* on
the right" - so the lights are beside the mode chips rather than above them,
and there is no second row for them to be misaligned with. The lights are at
the **end** of that row, and that is the property measured here.

**Why the cells and not the strip's box** - the mistake this file's first
version made, and it took a mutation to catch. A strip's own box is centred by
construction under `halign=FILL` while its *content* sits at one end, so a
check that reads the box reports "centred" for a layout that is not. The
measurements below are the cells' own allocations.
"""

from __future__ import annotations

import os

import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")


def _broadway_display() -> str | None:
    """An already-listening broadway socket, or None.

    `render_ui.py` and the harness start their own; this reads rather than
    starting one, because a daemon left behind by a test outlives the run that
    wanted it. `/run/user/<uid>/broadway<N>.socket` is the only place the answer
    is written down.
    """
    for directory in (os.environ.get("XDG_RUNTIME_DIR"),
                      f"/run/user/{os.getuid()}"):
        if not directory or not os.path.isdir(directory):
            continue
        for entry in sorted(os.listdir(directory)):
            if entry.startswith("broadway") and entry.endswith(".socket"):
                return entry[len("broadway"):-len(".socket")]
    return None


@pytest.fixture
def measure_strip(monkeypatch):
    """Measure the strip's alignment in a real window, at a forced `halign`.

    Parameterised rather than fixed so a test can ask what the measurement says
    about a *broken* strip. A measurement that has only ever reported "centred"
    is not known to be able to see anything else, which is the whole reason
    `test_the_measurement_can_see_a_broken_strip` exists.
    """
    display = _broadway_display()
    if display is None:
        if os.environ.get("GDK_BACKEND") != "broadway":
            pytest.skip("no broadway display; GTK4 alignment needs an allocated window")
        display = os.environ.get("BROADWAY_DISPLAY")
        if not display:
            pytest.skip("GDK_BACKEND=broadway without BROADWAY_DISPLAY")
    monkeypatch.setenv("GDK_BACKEND", "broadway")
    monkeypatch.setenv("BROADWAY_DISPLAY", display)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    from gi.repository import Adw

    Adw.init()
    from shani_chronoa.gui.window import ChronoaWindow
    from shani_chronoa.gui.organs import OrganStrip

    _shipped_init = OrganStrip.__init__

    def run(margin_end=0):
        from shani_chronoa.gui import organs

        # **Reset on every call, not only when patching.** `monkeypatch` undoes
        # at test teardown, so a second `run()` inside one test inherited the
        # previous call's patch and the control measured the broken strip
        # twice - reporting a broken layout against the *shipped* build. The
        # original is captured here, once, and restored first so that
        # `margin_end=0` always means "as shipped".
        #
        # **A margin, and not an alignment.** The row is exactly full at the
        # default width - the mode chips and the lights together fill it - so
        # moving the strip within the leftover space changes no pixel, and a
        # control built on an alignment would measure the shipped build twice
        # and call it broken. A right margin pushes the *content* away from
        # the row's end, which is the thing the property is about.
        monkeypatch.setattr(organs.OrganStrip, "__init__", _shipped_init)
        if margin_end:

            def patched(self, *a, **k):
                _shipped_init(self, *a, **k)
                self.set_margin_end(margin_end)

            monkeypatch.setattr(organs.OrganStrip, "__init__", patched)

        measured: dict = {}
        outcome: list = []
        app = Gtk.Application(
            application_id=f"dev.shani.chronoa.OrganAlign{int(margin_end)}")

        def activate(application):
            window = ChronoaWindow(application)
            window.set_default_size(1024, 768)
            window.present()

            def measure():
                strip = None
                pending = [window.get_root() or window]
                while pending:
                    node = pending.pop()
                    classes = (node.get_css_classes()
                               if hasattr(node, "get_css_classes") else [])
                    if "organ-strip" in classes:
                        strip = node
                        break
                    child = node.get_first_child()
                    while child is not None:
                        pending.append(child)
                        child = child.get_next_sibling()
                if strip is None:
                    outcome.append(False)
                    application.quit()
                    return False
                box = strip.get_allocation()
                column = strip.get_parent().get_allocation()
                cells = []
                child = strip.get_first_child()
                while child is not None:
                    if child.get_visible():
                        cells.append(child.get_allocation())
                    child = child.get_next_sibling()
                content = sum(c.width for c in cells)
                used = content + (len(cells) - 1) * strip.get_column_spacing()
                first = box.x + (cells[0].x if cells else 0)
                measured.update({
                    "cells": len(cells),
                    "used": used,
                    "natural": strip.measure(Gtk.Orientation.HORIZONTAL, -1)[1],
                    "left": first,
                    "right": column.width - (first + used),
                    "column": column.width,
                })
                outcome.append(True)
                application.quit()
                return False

            from gi.repository import GLib

            GLib.timeout_add(400, measure)
            return True

        app.connect("activate", activate)
        try:
            app.run([])
        finally:
            app.quit()
        if not outcome or not outcome[0]:
            pytest.skip("the window did not present under broadway; nothing was measured")
        return measured

    return run


def test_the_lights_sit_at_the_end_of_the_status_row(measure_strip):
    """The row's shape, as a property rather than a screenshot.

    **The status row is one row: the mode chips on the left, the organ lights
    on the right.** "What Chronoa *is* on the left, what it is *doing* on the
    right" - so the lights end where the row ends, and there is no second row
    for them to be misaligned with.

    Asserted on the **content's** margins, because a strip's own box is placed
    by construction while its *content* sits at one end. A check that reads the
    box reports "in place" for a layout that is not, and the first version of
    this file was one.
    """
    m = measure_strip()
    assert m["cells"] == 8, (
        f"the strip rendered {m['cells']} cells, so the row being measured is "
        f"not the row of eight lights")
    assert m["right"] <= 4, (
        f"the lights end {m['right']}px short of the row's right edge (they "
        f"start {m['left']}px into a {m['column']}px row) - the row is meant to "
        f"put them at the end, beside the mode chips")


def test_the_strip_does_not_claim_more_width_than_it_uses(measure_strip):
    """`do_measure`'s own job, which is what gave `halign` something to do.

    `Gtk.FlowBox` reports natural width as widest-child x count - 815px for
    479px of lights, the exact figure AGENTS.md recorded. A strip measured wider
    than it is fills its column and leaves the lights at the left, so this is
    the upstream cause rather than a symptom of it.
    """
    m = measure_strip()
    assert m["natural"] <= m["used"] + 2, (
        f"the strip reports {m['natural']}px of natural width for {m['used']}px "
        f"of lights, so it is allocated more than it needs and has no slack to "
        f"centre in - this is `Gtk.FlowBox`'s widest-child-x-count measure, "
        f"which `do_measure` exists to correct")


def test_the_measurement_can_see_a_broken_strip(measure_strip):
    """The control, and it is the reason the other two are worth anything.

    **A right margin** is the one-word change that reproduces a plausible
    defect: the lights are pushed away from the end of the row, which is
    exactly what the property above forbids. Measured here it reads a hundred
    pixels clear of the edge, against the shipped row's 0.

    So the property is not a check that cannot fail - and had it been written
    against the strip's own *box* rather than its content, it would have
    reported "in place" for this layout and passed. That version existed and is
    why the assertion is on the cells.
    """
    broken = measure_strip(margin_end=120)
    shipped = measure_strip()
    assert broken["right"] > 40, (
        f"the strip with a right margin measures {broken['right']}px clear of "
        f"the row's edge, which the property above would have to call fine - so "
        f"this control has stopped discriminating")
    assert shipped["right"] <= 4, (
        f"the shipped strip ends {shipped['right']}px short of the row's edge, "
        f"so the row above is not measuring the fix it claims to measure")
