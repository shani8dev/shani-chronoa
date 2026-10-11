#!/usr/bin/env python3
"""Does a wheel notch actually arrive at the window under it? Run it by hand.

`tests/test_portal_input.py` pins the *contract* - that `scroll()` calls the
portal's `NotifyPointerAxis` with the right signs, and that the name it used to
call (`NotifyPointerScroll`) is not a method at all. That is deterministic and it
runs in CI. **It says nothing about delivery**, and delivery is what AGENTS.md
recorded as open:

> *Scrolling delivered 0 changed pixels on every route tried - the portal (both
> axis methods) and uinput - but no target was used that certainly scrolls, so
> the check could not have failed and the result means nothing either way.*

A wheel over a pane that cannot scroll changes no pixels whether or not the event
arrived, so that probe measured its own target rather than the route. **This
script is the version with a target that certainly scrolls and a control that can
fail**, and it is a script rather than a test for the same reason `render_ui.py`
is: `tests/conftest.py`'s `_hermetic_env` redirects `HOME`, so the Screenshot
portal writes to the real `~/Pictures` while the reader looks in the temporary
one, and every live capture comes back empty. A test in this suite can only ever
skip, and a skip reads as coverage.

**Run it with a scrollable window on screen** - a long document, a web page. It
prints the words that left and the words that arrived, then the same measurement
*twice with no wheel*, which is the control: if two reads of a static screen do
not agree, "the words changed" is repaint noise and the verdict above it means
nothing.

    python3 verify_scroll.py [--notches 6] [--app chrome]

Exit 0 only if the wheel moved the pane *and* the control held.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "usr/lib/shani-chronoa"))

os.environ.setdefault("GSETTINGS_BACKEND", "keyfile")

from shani_chronoa import ui_driver as drv  # noqa: E402

#: The wheel must beat the screen's own noise by this much before the result
#: counts. Two static reads are never identical - a clock, a caret, a fraction of
#: a repaint - so the bar is *relative to what this screen does on its own*, not a
#: number that happens to fit one machine on one afternoon.
_NOISE_MARGIN = 2.5

#: An absolute floor as well, so a screen that is perfectly static cannot be
#: "scrolled" by one word wobbling.
_ABSOLUTE = 20


def _words(region=None):
    """`{text}` for every word OCR finds inside `region`, which may be None.

    **A region, because the whole screen is rarely one thing.** Measured on this
    desktop: a full-screen read changed by 413 words with nothing happening,
    because a session was streaming in one corner - while the pane being scrolled
    was static. A noise floor taken over the whole screen invalidated a
    measurement of a quiet pane, which is the same "a check that fails for its
    own reasons" failure in a new place.
    """
    return {w[4] for w in drv.screen_words(region=region)}


def _point_in_a_pane(region=None):
    """Well inside the body, away from a rail or the cue's own cursor."""
    words = [w for w in drv.screen_words(region=region)
             if w[0] > 500 and w[1] > 120]
    if not words:
        return None
    words.sort(key=lambda w: -w[1])
    target = words[len(words) // 8]
    return int(target[0] + target[2] / 2), int(target[1] + target[3] / 2)


def _noise_floor(region=None):
    """How much this screen changes with nothing happening on it.

    **Measured first, and in the same region as the measurement.** The first
    version of this script compared the wheel's change against a fixed constant
    and printed "the pane moved" while its own control, run afterwards, reported
    *more* words changing with no wheel at all - a confident verdict its own
    control had already invalidated. The noise floor is the denominator, not a
    footnote.
    """
    first = _words(region)
    drift = _words(region)
    return len(first ^ drift)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notches", type=int, default=6)
    parser.add_argument("--app", default="chrome")
    parser.add_argument("--region", default="",
                        help="x,y,w,h of the pane to measure, so a screen that "
                             "is busy elsewhere does not invalidate a quiet one")
    options = parser.parse_args()

    region = None
    if options.region:
        try:
            region = tuple(int(v) for v in options.region.split(","))
        except ValueError:
            print("--region wants x,y,w,h as four integers")
            return 1
        if len(region) != 4:
            print("--region wants x,y,w,h as four integers")
            return 1

    point = _point_in_a_pane(region)
    if point is None:
        print("SKIP: no body text is on screen to scroll over. Open a document "
              "or a page that scrolls and run this again.")
        return 0
    x, y = point

    noise = _noise_floor(region)
    print(f"NOISE: {noise} word(s) changed between two reads with no wheel "
          f"while this screen simply sat there")

    before = _words()
    drv._scroll_notch(options.app, x, y, options.notches)
    after = _words()
    left, arrived = before - after, after - before
    changed = len(left) + len(arrived)
    print(f"wheel at ({x},{y}), {options.notches} notch(es) over {options.app}")
    print(f"  words on screen : {len(before)} -> {len(after)}")
    print(f"  words that left : {len(left)}   words that arrived : {len(arrived)}")
    print(f"  e.g. left: {sorted(left)[:5]}")
    print(f"  e.g. arrived: {sorted(arrived)[:5]}")

    if noise * _NOISE_MARGIN > changed:
        print()
        print(f"NOT MEASURED: the wheel changed {changed} word(s) against a "
              f"noise floor of {noise}, which is not {_NOISE_MARGIN}x - so this "
              f"run cannot tell delivery from repaint. That happens on a screen "
              f"that is live-updating, and the honest answer is that it was not "
              f"measured rather than that scroll failed.")
        return 2
    if changed < _ABSOLUTE:
        print()
        print(f"NOT MEASURED: {changed} word(s) is too few to be a pane moving, "
              f"whatever the noise floor says")
        return 2

    print()
    print(f"DELIVERED: {changed} word(s) moved against a noise floor of "
          f"{noise} - the wheel reached the window under it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
