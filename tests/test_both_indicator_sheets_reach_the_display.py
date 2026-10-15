"""Both indicator sheets reach the display, and each test is the other's control.

`window.py:_apply_organ_css` loaded two sheets into **one** provider:

    provider = Gtk.CssProvider()
    provider.load_from_data(ORGAN_IDLE_CSS.encode())
    provider.load_from_data(MODE_STRIP_CSS.encode())

`load_from_data` **replaces**, so the second call discarded the first. The
window ran on `MODE_STRIP_CSS` alone, and everything `ORGAN_IDLE_CSS` declares
was inert: `.organ`'s padding, `.organ-idle`'s background, `.organ-active`'s
red pill, and the icon/label opacities. `OrganStrip.refresh()` kept toggling
`organ-active`/`organ-idle` correctly - there was simply no rule left that read
them, so **the strip could not show which organ was active.**

**Why nothing caught it.** `tests/test_mode_strip.py:741` loads `MODE_STRIP_CSS`
into a *fresh* provider and asserts a class is in the sheet. That is the one
test in the tree covering the sheet, and it cannot see the bug by
construction: it never goes through the code that drops it. Every other test
asserted the strip was centred, the lights were separated, or the modes were
selectable - all of which held with the sheet discarded.

**The measurement that found it, on a real mapped window**, by allocation only:

| case | organ cell - its label |
|---|---|
| shipped (two loads, one provider) | **0** |
| one provider per sheet | **12** |

12 is exactly what `.organ { padding: 2px 6px }` predicts, so the discriminator
is unambiguous. Strip width was tried first and **cannot** see this: it moved
479 -> 474 while its contents changed by 96px, because a font change is not
linear in total width. Padding is the discriminator; width is not.

**Why two tests rather than one.** They are each other's negative case. With
the shipped single provider, `MODE_STRIP_CSS` wins and the organ test fails
while the mode test passes. With the loads reversed, the organ test passes and
the mode test fails. **Neither can pass vacuously**: both assert a specific
non-zero number derived from a *different* sheet, so a broken instrument - one
that measured nothing, or read the wrong widget - fails both rather than
letting one cover the other.

Measured on this box, 2026-10-13: organ `cell - label == 12` and mode
`chip - child == 14` at every window width, 8 organ cells and 5 mode chips.
"""

from __future__ import annotations

import os
import sys
import time as _t

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")

from gi.repository import Adw, GLib  # noqa: E402

#: `.organ { padding: 3px 4px }` - the cell's own padding, not the strip's.
#: Was 2px 6px (12) until the 2026-10-14 strip/chip row had to fit one line at
#: 460px; see MAIN-UI-TODO.md section 8.
ORGAN_PADDING = 8
#: `.mode-chip { padding: 1px 2px }` - was 1px 7px (14); the outer padding is
#: drawn transparent around the inner button, so it read as a 14px gap.
MODE_CHIP_PADDING = 4
#: `organs.ORGANS` has eight; a walk that finds fewer is a broken instrument
#: and must not read as "nothing to complain about".
ORGAN_CELLS = 8

#: 3.5s, measured: the window seeds every panel's health dot on idle after it
#: maps, and that chain measures at 5.1-5.4s on a loaded box. Well below that
#: the tree is mapped but the allocations under test are not final.
_SETTLE_MS = 5000


def _broadway_socket() -> str | None:
    """The broadway socket to measure on: the caller's, else any live one.

    **`BROADWAY_DISPLAY` wins, unlike the copy in
    `test_organ_strip_is_centred.py`, which scans first.** Scanning returns
    whichever socket file sorts first, which on this box is not the one the
    caller asked for - so a run pinned to `:121` could quietly measure on
    `:90` and report a different machine's layout. Honouring an explicit
    request is what makes the run reproducible; the scan is the fallback for a
    bare `pytest` on a machine that already has a daemon listening.

    It reads rather than starts one, for the reason that file records: a daemon
    left behind by a test outlives the run that wanted it. `broadwayd :N`
    listens on socket `N+1`, so the socket file is named after the daemon's
    argument plus one.
    """
    given = os.environ.get("BROADWAY_DISPLAY", "").strip()
    if given:
        return given
    for directory in (os.environ.get("XDG_RUNTIME_DIR"),
                      f"/run/user/{os.getuid()}"):
        if not directory or not os.path.isdir(directory):
            continue
        for entry in sorted(os.listdir(directory)):
            if entry.startswith("broadway") and entry.endswith(".socket"):
                return entry[len("broadway"):-len(".socket")]
    return None


def _walk(root, css_class):
    """Every widget under `root` carrying `css_class`, in tree order."""
    found, stack = [], [root]
    while stack:
        node = stack.pop(0)
        classes = (node.get_css_classes()
                   if hasattr(node, "get_css_classes") else [])
        if css_class in classes:
            found.append(node)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def _pump(ms):
    """Drain the main context for `ms`, the way `render_ui.py` does.

    `Gtk.events_pending` is a GTK 3 name that does not exist in GTK 4 and
    `Gdk.Display.get_default_context` is not a method either.
    """
    context = GLib.MainContext.default()
    end = _t.monotonic() + ms / 1000.0
    while _t.monotonic() < end:
        while context.pending():
            context.iteration(False)
        _t.sleep(0.01)


@pytest.fixture(scope="module")
def mapped_window():
    """One real, mapped `ChronoaWindow` for both measurements.

    Module-scoped on purpose. A `Gtk.Application` is built once per process:
    a second one registering the same id aborts inside
    `Gtk.Application.__init__`, and an autouse fixture must not leave GTK
    initialised behind it either - so the environment is set and restored by
    hand rather than through `monkeypatch`, which is function-scoped.
    """
    display = _broadway_socket() or os.environ.get("BROADWAY_DISPLAY", "")
    if not display:
        if os.environ.get("GDK_BACKEND") != "broadway":
            pytest.skip("no broadway display; CSS padding needs an allocated "
                        "window - constructing a widget is not measuring it")
    saved = {k: os.environ.get(k) for k in
             ("GDK_BACKEND", "BROADWAY_DISPLAY", "DISPLAY", "WAYLAND_DISPLAY")}
    os.environ["GDK_BACKEND"] = "broadway"
    os.environ["BROADWAY_DISPLAY"] = display
    os.environ.pop("DISPLAY", None)
    os.environ.pop("WAYLAND_DISPLAY", None)

    app = None
    try:
        Adw.init()
        from shani_chronoa.gui.window import ChronoaWindow

        app = Gtk.Application(application_id="dev.shani.chronoa.SheetsReach")
        holder: dict = {}

        def activate(application):
            window = ChronoaWindow(application)
            window.set_default_size(1024, 768)
            window.present()
            holder["window"] = window
            application.quit()

        app.connect("activate", activate)
        app.register(None)
        app.activate()
        _pump(_SETTLE_MS)
        window = holder.get("window")
        if window is None:
            pytest.fail("the window did not build, so neither sheet was measured")
        yield window
    finally:
        if app is not None:
            try:
                app.quit()
            except Exception:  # noqa: BLE001 - teardown must not mask a failure
                pass
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _organ_padding(window):
    """`cell - its label` for every organ cell, as a sorted list.

    A cell is a `Gtk.FlowBoxChild` wrapping the `.organ` box, which holds a
    vertical box with the icon above the label. The width of the box minus the
    width of the label *is* the cell's horizontal padding, once both are laid
    out - which is why this reads allocations and nothing else.
    """
    strips = _walk(window, "organ-strip")
    if not strips:
        pytest.fail("no organ-strip in the window: the instrument found nothing")
    widths = set()
    found = 0
    child = strips[0].get_first_child()
    while child is not None:
        cell = child.get_child()
        if cell is not None:
            labels = _walk(cell, "organ-label")
            if labels:
                found += 1
                # **The widest visible child, not the label.** An idle light is
                # icon-only since 2026-10-14 - its label is hidden and allocated
                # 0px - so `cell - label` would measure the icon as padding.
                inner = [c.get_allocation().width
                         for c in (cell.get_first_child(), labels[0])
                         if c is not None and c.get_visible()]
                widths.add(cell.get_allocation().width - max(inner))
        child = child.get_next_sibling()
    if found != ORGAN_CELLS:
        pytest.fail(f"measured {found} organ cells, expected {ORGAN_CELLS}: "
                    f"a walk that misses cells must not read as no padding")
    return sorted(widths)


def _mode_chip_padding(window):
    """`chip - its label` for every mode chip.

    The chip is a `Gtk.Button` and its only child is the `.mode-chip-label`, so
    the first child is the label - measured on the built window rather than
    assumed from the builder.
    """
    chips = _walk(window, "mode-chip")
    if not chips:
        pytest.fail("no mode chips in the window: the instrument found nothing")
    widths = set()
    for chip in chips:
        label = chip.get_first_child()
        if label is not None:
            widths.add(chip.get_allocation().width
                       - label.get_allocation().width)
    return sorted(widths)


def test_the_organ_cells_carry_the_padding_organ_idle_css_declares(mapped_window):
    """`ORGAN_IDLE_CSS` is in effect - the sheet that declares the active pill.

    Fails against the shipped defect (one provider, two loads), where this
    reads `[0]`: the cells are exactly as wide as their labels because
    `.organ { padding: 2px 6px }` never reached the display.
    """
    assert _organ_padding(mapped_window) == [ORGAN_PADDING], (
        f"organ cells report {_organ_padding(mapped_window)}, expected "
        f"[{ORGAN_PADDING}] - ORGAN_IDLE_CSS is not reaching the display")


def test_the_mode_chips_carry_the_padding_mode_strip_css_declares(mapped_window):
    """`MODE_STRIP_CSS` is in effect - and this is the control for the other.

    `MODE_STRIP_CSS` is the sheet that *survived* the double load, so it passes
    against the shipped defect while the test above fails. That asymmetry is
    the point: together the two prove neither load displaces the other, and
    this one fails if the fix is made by reversing the order.
    """
    assert _mode_chip_padding(mapped_window) == [MODE_CHIP_PADDING], (
        f"mode chips report {_mode_chip_padding(mapped_window)}, expected "
        f"[{MODE_CHIP_PADDING}] - MODE_STRIP_CSS is not reaching the display")
