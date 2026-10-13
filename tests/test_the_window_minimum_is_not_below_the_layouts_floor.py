"""The window's minimum size leaves no conversation at all, and width is not the lever.

`ChronoaWindow.set_size_request(380, 480)` declares the smallest window the app
will occupy. Measured on the real window, the transcript's height is an exact
linear function of the window's height, with a floor where the chrome's own
minimums bind:

| window width | chrome | transcript                          |
|--------------|--------|-------------------------------------|
| <= 560       | 448px  | `max(58, height - 448)`             |
| >= 760       | 411px  | `max(69, height - 411)`             |

The 37px between the two is the **organ strip compacting** - eight status
lights that stop needing a row of their own once there is width for them.

**The defect.** The minimum used to be declared 380x480, so `480 - 448 = 32`, and
the floor answered **58px** - about two lines of conversation in a window the app
chose to permit. The minimum was not a tight fit for a small window; it was a
window with no conversation in it. **`set_size_request` now declares 380x570**,
because 448 + 120 = 568 and 120 is the smallest transcript worth calling a
conversation, so the declaration is finally true of the window it permits.

**The root cause is larger than the number, and is recorded rather than fixed:**
448px of fixed chrome in a 480px window, of which an 88px orb is a fifth. Raising
the minimum makes the promise honest; it does not make the chrome smaller.

**The retraction.** This file also records why the window's *default width* is
being left alone. `transcript_h` measured **192 at every width from 380 to 560**
at 640 tall: narrowing the window bought nothing and only took space from the
conversation, while the height moved it linearly. A planned change to the
default width was dropped on this measurement, and this file holds it so the
next pass re-measures rather than re-derives.

The measurements need a display and a present loop, so they are **recorded here
rather than re-taken per run** - a table of numbers this file asserts is a
fixture, and saying so is the difference between a fixture and a claim.
"""
import inspect
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.gui import window as W  # noqa: E402


# --------------------------------------------------------------------------
# the recorded measurements. `(window_width, window_height, transcript_height)`,
# read as data rather than asserted as a count - a test that only counts says
# nothing about *which* values they are.


#: Narrow layout: the organ strip needs two rows, so the chrome is taller.
#:
#: **Every row here is a window the product will actually produce.** The rows
#: this file used to carry at 480-560 tall are gone because `set_size_request`
#: will not let a window be shorter than its minimum: asked for 480 tall, the
#: window measures 570 and the transcript reads 119. A row recorded as
#: `(380, 480, 58)` was the floor of the law, not a measurement of anything.
#:
#: Re-measured 2026-10-14, one process per window. The chrome is **451 at every
#: one of them**: 580-451=129, 600-451=149, 640-451=189, 700-451=249,
#: 768-451=317.
NARROW = [
    (380, 580, 129),
    (380, 600, 149),
    (380, 640, 189),
    (380, 700, 249),
    (380, 768, 317),
]

#: Wide layout: the strip fits on one row, so the chrome is **36px less** - and
#: 36 is exactly the second row the strip stops needing (74 - 38).
WIDE = [
    (700, 640, 225),
    (760, 640, 225),
    (760, 700, 285),
    (760, 768, 353),
]

#: Width is free **within** a layout and not across the breakpoint. Measured at
#: 640 tall, one process per width: 380 and 460 give 189; 520 and 560 give
#: **225**. This table used to claim 192 at all four widths, which is how the
#: default-width decision was justified - and it was already stale.
WIDTH_IS_FREE = [(380, 189), (460, 189), (520, 225), (560, 225)]

CHROME_NARROW = 451
CHROME_WIDE = 415

#: **The width at which the strip stops wrapping, measured.**
#:
#: This file said 760. `SIDEBAR_COLLAPSE_WIDTH` is 720. Both are wrong for this
#: question: the chrome flips at **495**, which is where the eight cells stop
#: fitting on one row. The strip's own natural width is 466 - at 490 wide it is
#: allocated 462 and wraps to two rows, at 495 it is allocated 466 and does not.
#: The sidebar collapsing is a different breakpoint at a different width and
#: says nothing about the conversation.
WIDTH_BREAKPOINT = 495

FLOOR_NARROW = 58
FLOOR_WIDE = 69

#: Where the law's linear part overtakes the floor: `451 + 58 + 1`.
LINE_FROM = 510

#: The smallest conversation this file will call a conversation. Below roughly
#: six lines the transcript is a peephole rather than a place a reply is read.
MIN_USABLE_CONVERSATION = 120


def predicted(width, height):
    """The transcript height the measured curve says this window should have."""
    if width >= WIDTH_BREAKPOINT:
        return max(FLOOR_WIDE, height - CHROME_WIDE)
    return max(FLOOR_NARROW, height - CHROME_NARROW)


def declared_minimum():
    """The `(width, height)` pair `set_size_request` is given.

    Read out of the module rather than restated, so a change to the product is a
    different answer here instead of this file asserting a number it copied.
    """
    source = inspect.getsource(W.ChronoaWindow._setup_window)
    for line in source.splitlines():
        if "set_size_request" in line:
            digits = "".join(c if c.isdigit() else " " for c in line).split()
            if len(digits) == 2:
                return int(digits[0]), int(digits[1])
    raise AssertionError(
        "set_size_request is no longer a literal (width, height) pair; teach "
        "this test the new shape rather than letting it guess")


class TestTheMeasurementsAreTheCurveTheyClaimToBe:

    def test_every_narrow_measurement_matches_the_narrow_law(self):
        wrong = [(w, h, t, predicted(w, h))
                 for w, h, t in NARROW if predicted(w, h) != t]
        assert not wrong, (
            f"the narrow measurements disagree with the law: {wrong}")

    def test_every_wide_measurement_matches_the_wide_law(self):
        wrong = [(w, h, t, predicted(w, h))
                 for w, h, t in WIDE if predicted(w, h) != t]
        assert not wrong, (
            f"the wide measurements disagree with the law: {wrong}")

    def test_the_two_layouts_differ_by_the_organ_strip_and_nothing_else(self):
        """**36px, and it is `OrganStrip` - measured by walking both trees.**

        At 640 tall, every row of the chat column is the same height at 380 and
        at 760 except two: `OrganStrip` **74 -> 38** and the transcript box
        **189 -> 225**. Exactly -36 and exactly +36, and every other row
        identical - 74 - 38 being the row the strip stops needing once its
        eight cells fit on one line.

        **This file said 37, off a width breakpoint of 760 that is not where
        the chrome changes at all.** Measured, the strip stops wrapping at
        **495**: its own natural width is 466, and at 490 wide it is allocated
        462 and wraps to two rows while at 495 it is allocated 466 and does not.
        `SIDEBAR_COLLAPSE_WIDTH` is 720, which is a third number and also
        irrelevant - the sidebar collapsing costs the conversation nothing.
        """
        narrow_by_height = {h: predicted(w, h) for w, h, _t in NARROW}

        for w, h, _t in WIDE:
            wide = predicted(w, h)
            narrow = narrow_by_height[h]

            assert wide - narrow == 36, (
                f"at {h} tall the wide layout gives the conversation "
                f"{wide - narrow}px more than the narrow one, not 36")

    def test_every_recorded_row_is_above_its_floor(self):
        """**The premise the 36 rests on, checked rather than branched around.**

        The first version guarded the difference with
        `expected = 37 if both_above_floor else wide - narrow` - a second path
        that no row of either table could reach, which is a control that cannot
        fire. The premise it was guarding is now an assertion: if a row below
        the floor is ever added back, this goes red and the 36 is re-examined
        rather than silently excused.
        """
        assert all(t > FLOOR_NARROW for _w, _h, t in NARROW), (
            "a narrow row is at or under its floor, so the two layouts' "
            "difference would be the floors disagreeing rather than the strip")
        assert all(t > FLOOR_WIDE for _w, _h, t in WIDE)

    def test_the_floor_is_a_floor_and_not_just_the_line(self):
        """The distinction the sweep established, held as a property.

        Below `LINE_FROM` the law would predict *less* than the floor. Without
        this assertion `CHROME_NARROW` could be wrong in a direction that still
        satisfies every row of every table.
        """
        assert predicted(380, LINE_FROM - 1) == FLOOR_NARROW
        assert predicted(380, LINE_FROM) > FLOOR_NARROW

    def test_one_pixel_of_window_is_one_pixel_of_conversation(self):
        """**Why height is the lever and width is not.**

        Above the floor the transcript absorbs every pixel of extra height
        exactly. A chrome that grew without the transcript noticing would be a
        different bug, and this is what would catch it.
        """
        assert predicted(380, 640) - predicted(380, 620) == 20


class TestTheMinimumLeavesNoConversation:

    def test_the_declared_minimum_is_read_from_the_product(self):
        """**The number under test is the product's, not a copy of it.**

        `declared_minimum()` parses `_setup_window`, so a change to the minimum
        arrives here as a different answer rather than as a file still asserting
        what it remembered.
        """
        width, height = declared_minimum()
        assert width >= 380, (
            f"the declared minimum width is {width}px; the narrow law below was "
            f"measured at 380-560 and no longer describes a narrower window")

    def test_the_minimum_leaves_a_conversation_someone_can_read(self):
        """**The defect, stated as arithmetic rather than as a judgement.**

        480 - 448 = 32, and the chrome's own minimum answers 58. That is the
        height of roughly two lines, in a window the app chose to permit -
        which is not a small window, it is a window with no conversation in it.
        """
        width, height = declared_minimum()
        allowed = predicted(width, height)

        assert allowed >= MIN_USABLE_CONVERSATION, (
            f"the declared minimum of {width}x{height} leaves {allowed}px of "
            f"conversation ({100 * allowed // height}% of the window's height, "
            f"about {allowed // 20} lines); the chrome needs "
            f"{predicted(width, height) - allowed}px more than that before the "
            f"law applies")


class TestWidthIsNotTheLever:

    """Why the default *width* is being left alone.

    Re-measured 2026-10-14, one process per width. **The recorded reason was
    stale, and in the direction that argues for the decision more strongly than
    the reason it replaced.**
    """

    def test_width_is_free_within_a_layout_and_the_breakpoint_is_the_boundary(
            self):
        """**What the measurement says, which is not what the file said.**

        This asserted one number across 380-560: *"narrowing the window has
        never bought a pixel"*. Measured: 380 and 460 give 189, and 520 and 560
        give **225**. So crossing the strip's breakpoint at 495 is worth 36px
        and narrowing back below it costs that again.

        **Width is free inside a layout and not across the breakpoint**, which
        is a better reason to leave the default alone than "narrowing is free"
        was: the width is not a free parameter, it is a second lever with a
        step in it.

        Recorded rather than re-measured per run - it needs a display, and the
        date is on the table.
        """
        below = {t for w, t in WIDTH_IS_FREE if w < WIDTH_BREAKPOINT}
        above = {t for w, t in WIDTH_IS_FREE if w >= WIDTH_BREAKPOINT}

        assert len(below) == 1, (
            f"widths below the breakpoint gave {sorted(below)} at 640 tall; "
            f"within a layout the transcript does not move with width")
        assert len(above) == 1, (
            f"widths above the breakpoint gave {sorted(above)} at 640 tall")

        gained = next(iter(above)) - next(iter(below))
        assert gained == CHROME_NARROW - CHROME_WIDE, (
            f"crossing the breakpoint is worth {gained}px, and the strip's one "
            f"row is worth {CHROME_NARROW - CHROME_WIDE}px - so if these differ "
            f"something other than the strip is moving")

    def test_the_sidebar_appears_without_costing_the_conversation(self):
        """**Now measured across the threshold, not inferred from two distant
        widths.**

        The first version compared 380 with 760, which straddle *both*
        breakpoints, so it could only ever have shown that the conversation
        does not shrink. Measured at 640 tall: the chrome is **415 at every
        width from 495 upward**, across the sidebar's own collapse at 720. So
        opening the sidebar costs the conversation nothing at all, and the only
        step in the width is the strip's.

        The non-empty guard is load-bearing: with a table that had drifted to
        one side of the threshold, `below == above` would be `set() == set()`
        and pass.
        """
        threshold = W.ChronoaWindow.SIDEBAR_COLLAPSE_WIDTH
        below = {t for w, h, t in WIDE if w < threshold and h == 640}
        above = {t for w, h, t in WIDE if w > threshold and h == 640}

        assert below and above, (
            f"the wide table needs a measured width on each side of the "
            f"sidebar's collapse threshold ({threshold}) for this to mean "
            f"anything; it has {sorted(WIDE)}")

        assert below == above, (
            f"opening the sidebar changed the transcript at 640 tall: "
            f"{sorted(below)} collapsed, {sorted(above)} open - it is supposed "
            f"to cost width and nothing else")


# --------------------------------------------------------------------------
# the one place in this file that asks the product instead of the file.


@pytest.fixture(scope="module")
def measured_chrome():
    """`(chrome_narrow, chrome_wide)`, read off real mapped windows.

    **This exists because everything above it is a closed loop.**
    `CHROME_NARROW` and `CHROME_WIDE` are typed in this file, the tables are
    typed in this file, and `test_every_*_measurement_matches_the_*_law` checks
    one hand-typed copy against another. Nothing here asked the product, so the
    three sets of numbers could all be wrong in the same direction and every
    test in the file would be green - which is exactly what happened.

    The organ strip's vertical padding became live for the first time when
    `ORGAN_IDLE_CSS` stopped being discarded (that sheet had been loaded and
    thrown away, so its padding had never applied). The real chrome moved by
    3-4px, the typed constants did not, and this file went on passing - while
    the test below it, which promises the minimum leaves 120px of conversation,
    was describing a window that leaves 119.

    Module-scoped, which is safe **only** because `_file_needs_a_display`
    already refuses to collect this file without a display backend: it matches
    on the text `ChronoaWindow`, and `declared_minimum()` puts that string here.
    A module-scoped fixture would otherwise be set up before the function-scoped
    display guard and raise at setup, which conftest's own docstring records as
    an error wearing the wrong word.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk
    from shani_chronoa.gui.window import ChronoaWindow
    from shani_chronoa.gui.widgets import TranscriptView

    Adw.init()
    application = Gtk.Application(
        application_id="dev.shani.chronoa.ChromeMeasured")

    def pump(ms):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)

    def transcript_of(window):
        pending = [window]
        while pending:
            node = pending.pop(0)
            if isinstance(node, TranscriptView):
                return node
            child = node.get_first_child()
            while child is not None:
                pending.append(child)
                child = child.get_next_sibling()
        pytest.fail("no TranscriptView in the window: this measurement cannot "
                    "be made, and reporting it as a chrome of 0 would be a "
                    "confident wrong answer")

    measured = {}
    for label, (width, height) in {
            "narrow": (380, 700), "wide": (760, 700),
            "narrow2": (380, 768), "wide2": (760, 768)}.items():
        window = ChronoaWindow(application)
        window.set_default_size(width, height)
        window.present()
        pump(4000)
        allocation = transcript_of(window).get_allocation()
        measured[label] = window.get_allocation().height - allocation.height
        window.destroy()
    return measured


class TestTheChromeIsMeasuredAndNotRemembered:

    """The constants, held to the product rather than to this file's memory.

    Each assertion below is a number that used to be typed on both sides.
    """

    def test_the_narrow_chrome_is_what_the_window_actually_spends(
            self, measured_chrome):
        assert measured_chrome["narrow"] == CHROME_NARROW, (
            f"a 380-wide window spends {measured_chrome['narrow']}px on chrome; "
            f"this file says {CHROME_NARROW}. Every table above is checked "
            f"against that constant rather than against the window, so they "
            f"all still pass.")

    def test_the_wide_chrome_is_what_the_window_actually_spends(
            self, measured_chrome):
        assert measured_chrome["wide"] == CHROME_WIDE, (
            f"a 760-wide window spends {measured_chrome['wide']}px on chrome; "
            f"this file says {CHROME_WIDE}")

    def test_the_chrome_is_the_same_at_700_and_at_768(
            self, measured_chrome):
        """**Linearity, measured, and the claim the tables exist to support.**

        If the chrome moved with height then `predicted()` would be wrong about
        the *shape* and not only about the offset, and no amount of adjusting
        the two constants would make the tables fit. Both layouts, two heights,
        one number each.
        """
        assert measured_chrome["narrow"] == measured_chrome["narrow2"]
        assert measured_chrome["wide"] == measured_chrome["wide2"]

    def test_the_declared_minimum_leaves_the_promised_conversation_measured(
            self, measured_chrome):
        """**The promise, asked of the window rather than of the law.**

        `test_the_minimum_leaves_a_conversation_someone_can_read` computes it
        with `predicted()`, which uses `CHROME_NARROW` - so a chrome that grew
        by 4px made that test quieter rather than wrong. This measures the
        minimum-height window itself.
        """
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk
        from shani_chronoa.gui.window import ChronoaWindow
        from shani_chronoa.gui.widgets import TranscriptView
        application = Gtk.Application(
            application_id="dev.shani.chronoa.MinimumMeasured")

        def pump(ms):
            context = GLib.MainContext.default()
            deadline = time.monotonic() + ms / 1000.0
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                time.sleep(0.01)

        width, height = declared_minimum()
        window = ChronoaWindow(application)
        window.set_default_size(width, height)
        window.present()
        pump(4000)
        pending, conversation = [window], None
        while pending:
            node = pending.pop(0)
            if isinstance(node, TranscriptView):
                conversation = node.get_allocation().height
                break
            child = node.get_first_child()
            while child is not None:
                pending.append(child)
                child = child.get_next_sibling()
        window.destroy()

        assert conversation is not None, (
            "no TranscriptView at the declared minimum, so the promise cannot "
            "be measured")
        assert conversation >= MIN_USABLE_CONVERSATION, (
            f"the declared minimum of {width}x{height} leaves {conversation}px "
            f"of conversation and this file calls "
            f"{MIN_USABLE_CONVERSATION}px the least that counts; the chrome "
            f"grew without the minimum following it, which is the defect "
            f"`test_the_minimum_leaves_a_conversation_someone_can_read` "
            f"cannot see because it does the arithmetic itself")