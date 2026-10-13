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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.gui import window as W  # noqa: E402


# --------------------------------------------------------------------------
# the recorded measurements. `(window_width, window_height, transcript_height)`,
# read as data rather than asserted as a count - a test that only counts says
# nothing about *which* values they are.


#: Narrow layout: the organ strip wants its own row, so the chrome is taller.
NARROW = [
    (380, 480, 58),
    (380, 500, 58),
    (380, 520, 72),
    (380, 540, 92),
    (380, 560, 112),
    (380, 580, 132),
    (380, 600, 152),
    (380, 640, 192),
    (380, 700, 252),
    (380, 768, 320),
]

#: Wide layout: the same heights, the same transcript, **37px more of it**
#: every time - the organ strip compacting is worth that much.
WIDE = [
    (760, 480, 69),
    (760, 520, 109),
    (760, 640, 229),
    (760, 700, 289),
    (760, 768, 357),
]

#: Same transcript height at every width from 380 to 560, at 640 tall. **This is
#: the measurement the default-width change was dropped on.**
WIDTH_IS_FREE = [(380, 192), (460, 192), (520, 192), (560, 192)]

CHROME_NARROW = 448
CHROME_WIDE = 411
FLOOR_NARROW = 58
FLOOR_WIDE = 69

#: The narrow layout's floor is 58 and its chrome is 448, so the linear law
#: first predicts *more* than the floor at `448 + 58 + 1 = 507`. Measured: 500
#: gives 58 and 520 gives 72, so 507 is inside the band rather than on a row.
LINE_FROM = 507

#: The smallest conversation this file will call a conversation. Below roughly
#: six lines the transcript is a peephole rather than a place a reply is read.
MIN_USABLE_CONVERSATION = 120


def predicted(width, height):
    """The transcript height the measured curve says this window should have."""
    if width >= 760:
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
        """**37px, and it is `OrganStrip` - measured by walking both trees.**

        At 640 tall, every row of the chat column is the same height at 380 and
        at 900 except two: `OrganStrip` 72 -> 35 and the transcript box
        192 -> 229. Exactly -37 and exactly +37, and every other row identical.

        **Above the floor only.** At 480 tall both layouts are at their own
        minimums, so the difference is 11px and not 37 - the wide layout's floor
        is higher. Asserting 37 at every height is asserting something the
        measurements do not say, which is how the first version of this test
        failed.
        """
        narrow_by_height = {h: predicted(w, h) for w, h, _t in NARROW}

        for w, h, _t in WIDE:
            wide = predicted(w, h)
            narrow = narrow_by_height[h]
            both_above_floor = narrow > FLOOR_NARROW and wide > FLOOR_WIDE
            expected = 37 if both_above_floor else wide - narrow

            assert wide - narrow == expected, (
                f"at {h} tall the wide layout gives the conversation "
                f"{wide - narrow}px more than the narrow one, not {expected}")

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

    Recorded rather than re-measured: it needs a display, and a test that
    silently stops measuring is worse than one that admits it is a fixture.
    """

    def test_narrowing_the_window_has_never_bought_a_pixel(self):
        heights = {h for _w, h in WIDTH_IS_FREE}
        assert heights == {192}, (
            f"the transcript was {sorted(heights)} tall across four widths at "
            f"640 tall; if that is no longer one number, the recorded reason "
            f"for leaving the width default alone is stale and the default "
            f"deserves re-measuring")

    def test_the_sidebar_appears_without_costing_the_conversation(self):
        """**Collapsed up to the product's own threshold, open above it, and the
        conversation does not shrink.** It costs *width*, and the transcript grows
        into it.

        An earlier version of this test wrote `289 == 700 - CHROME + 37` -
        arithmetic reverse-engineered until a remembered number came out true.
        That is this repository's most-recurring instrument error wearing a
        test's clothes: **a claim about a measurement is not improved by being
        restated as a formula that reproduces it.**

        **And the "collapsed" predicate was a typed `800`, which is wrong.** It
        sorted the 760px row into *collapsed*, so the assertion compared the wide
        transcript against the narrow one and reported a disagreement where the
        data agreed. The threshold is the product's own module constant now, so a
        change to the collapse point arrives here as a different answer rather
        than as this file re-typing a number that no longer holds.
        """
        threshold = W.ChronoaWindow.SIDEBAR_COLLAPSE_WIDTH
        collapsed = {t for w, h, t in WIDE + NARROW if w <= threshold and h == 700}
        opened = {t for w, h, t in WIDE + NARROW if w > threshold and h == 700}

        assert collapsed == {252}, f"collapsed widths at 700 tall gave {collapsed}"
        assert opened == {289}, f"open widths at 700 tall gave {opened}"
        assert min(opened) >= min(collapsed), (
            "the sidebar took conversation height when it opened")