"""The organ strip is centred - measured on the cells, not on its own box.

`tests/test_now_rail.py::test_the_organ_lights_are_separated_by_a_gap` exists
because a width test passed while the strip was one unreadable run-on string.
This is the same class of defect from the other side, and it is why this file
measures rather than asserts a property.

**The defect it records.** AGENTS.md carried the strip as an open lead: *"is the
row aligned? labels span x=296..791 (centre 543) while the mode chips below span
477..782 (centre 629) - **86px off**"*, with two attempted fixes *"measurably
no-ops"*. Both were true **and later superseded**: `OrganStrip.do_measure` now
reports the real sum of the cells' widths instead of `Gtk.FlowBox`'s
widest-child-x-count, so the strip is allocated exactly what its lights need and
`halign=CENTER` finally has slack to work with. The lead was never closed, so it
sat in the file describing a bug that had been fixed - which is the reading that
costs somebody an afternoon.

**Two mutations reproduce it exactly**, and both were run before this test
existed:

| change | `reported_natural` | content left | content right | verdict |
|---|---|---|---|---|
| as shipped | 479 | 130 | 131 | centred |
| `halign` -> `FILL` | 479 | 0 | 258 | 129px off |
| `do_measure` removed | **815** | 1 | 260 | 130px off |

815 is the exact figure AGENTS.md recorded for the un-overridden flow box, so
the historical root cause is reproduced, not approximated.

**Why the cells and not the strip's box** - the mistake this file's first version
made, and it took a mutation to catch. With `halign=FILL` the strip's *box* is
trivially centred: left margin 0, right margin 0, `off_by` 0. Its lights are
hard against the column's left edge with 258px of nothing on the right, which is
the original defect exactly. A check that reports "centred" for the broken
layout is worse than no check, and it was green under mutation before this
sentence was written.
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

    def run(halign=None):
        from shani_chronoa.gui import organs

        # **Reset on every call, not only when patching.** `monkeypatch` undoes
        # at test teardown, so a second `run()` inside one test inherited the
        # previous call's `FILL` and the control measured the broken strip
        # twice - reporting "not centred" against the *shipped* build. The
        # original is captured here, once, and restored first so that
        # `halign=None` always means "as shipped".
        monkeypatch.setattr(organs.OrganStrip, "__init__", _shipped_init)
        if halign is not None:

            def patched(self, *a, **k):
                _shipped_init(self, *a, **k)
                self.set_halign(halign)

            monkeypatch.setattr(organs.OrganStrip, "__init__", patched)

        measured: dict = {}
        outcome: list = []
        app = Gtk.Application(
            application_id=f"dev.shani.chronoa.OrganAlign{int(halign or 0)}")

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


def test_the_lights_are_centred_in_the_chat_column(measure_strip):
    """The 86px misalignment, as a property rather than a screenshot.

    Asserted on the **content's** margins, because a strip's own box is centred
    by construction under `halign=FILL` while its lights sit at the left. A
    check that passes for the broken layout is a negative control that cannot
    fail, and the first version of this file was one.
    """
    m = measure_strip()
    assert m["cells"] == 8, (
        f"the strip rendered {m['cells']} cells, so the row being centred is "
        f"not the row of eight lights")
    assert abs(m["left"] - m["right"]) <= 4, (
        f"the lights sit {m['left']}px from the left of a {m['column']}px column "
        f"and {m['right']}px from the right - the row is "
        f"{abs(m['left'] - m['right'])}px off centre, which is what the strip "
        f"looked like before `do_measure` reported the real sum")


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

    `halign=FILL` is the one-word change that reproduces the original
    appearance. Measured here it reads **129px off centre**, against the
    shipped strip's 0.5. So the property above is not a check that cannot fail -
    and had it been written against the strip's own *box* rather than its
    content, it would have reported "centred" for this layout and passed. That
    version existed and is why the assertion is on the margins.
    """
    broken = measure_strip(halign=Gtk.Align.FILL)
    shipped = measure_strip()
    assert abs(broken["left"] - broken["right"]) > 40, (
        f"the forced-FILL strip measures {broken['left']}px left and "
        f"{broken['right']}px right, which the centred property would have to "
        f"call fine - so this control has stopped discriminating")
    assert abs(shipped["left"] - shipped["right"]) <= 4, (
        "the shipped strip is not centred, so the row above is not measuring "
        "the fix it claims to measure")
