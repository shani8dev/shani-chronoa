"""Mutation set for the art's live channels - `tilt` and `mouth`.

`gaze_iris.py` covers the iris. This covers the two channels added with the
drag engine: the lean that shows the follow's velocity, and the mouth that
shows the catch. Both are **computed by `mascot_drag` and drawn by
`mascot_art`**, so the property under test is the seam - the number the engine
produces is the number the picture uses, and a channel that computes and does
not draw reads as *working* to every test that stops at the seam.

**Six decisions, each mutated in both directions.**

- **`_TILT_MAX` is the engine's `TILT_CLAMP`, not a second number.** The engine
  has already bounded what it will ask for, so the art is the second reader of
  one number. The failure mode is a *drift*: someone tunes the drag to feel
  livelier, raises `TILT_CLAMP`, and the art silently clamps - so the extra lean
  never appears and the engine reads as though its tuning did nothing. Both
  directions are mutated because the drift is only visible in one of them: a
  *looser* art bound still draws, a *tighter* one draws a truncated lean.
- **The lean is a rotation about the orb's centre, `256 250`.** Not about the
  origin, and not about the ring's centre at `256 258` - a pivot 8px away swings
  the character across the screen instead of leaning it in place, and the
  difference is invisible in a still and obvious at 60fps.
- **The shadow is outside the lean group.** The shadow is what fixes the mascot
  to the desktop; rotating it lifts the character off the screen, which is the
  one thing an always-on-top overlay must never look like. This is a *position*
  property (`shadow < lean < orb`) and is asserted as one, because both orderings
  that matter are invisible to any single-element check.
- **`_MOUTH_MAX` is `MOUTH_MAX`, and not `MOUTH_OPEN`.** `MOUTH_OPEN` is a
  spring *target* the spring passes - a real grab/release/suck peaks at
  **0.53987**, above both - so naming it here would pin a target as a limit. The
  mutation is exactly the value the art is most likely to be "corrected" to, and
  it is the one the tests above it had once asserted.
- **A closed mouth draws the empty string.** `rx=0` is a line across the face and
  a zero-height ellipse is a rendering warning in some renderers, so the guard is
  `<= 0.0` and not `< 0.0`: at exactly `0.0` the interior must vanish rather
  than become a degenerate ellipse that renders as a scar.
- **The speaking mouth scales; it is not replaced.** It is already open, so the
  amount grows its own ellipse and at `0.0` it is the 7x9 it has always been.
  Replacing it instead would redraw a different mouth per frame, and the resting
  picture would change for every caller that passes `mouth=0.0`.

**Three things are deliberately *not* mutated, and each is a finding rather than
an omission.**

- **The three mouth clamps are a redundant set, and no single one is
  observable.** `svg()` -> `_live_mouth` clamps to `[0, _MOUTH_MAX]`; then
  `_open_mouth` divides by `_MOUTH_MAX` and clamps to `1.0`; and the speaking
  branch in `_eyes` does the same. Any *one* upper bound is therefore absorbed
  before it reaches a number of pixels. Measured by removing each on its own and
  running this suite:

  | removed | result |
  |---|---|
  | `_live_mouth`'s upper clamp | 79 passed |
  | `_open_mouth`'s `min(1.0, ...)` | 79 passed |
  | the speaking branch's `min(1.0, ...)` | 79 passed |
  | `_live_mouth`'s **and** `_open_mouth`'s | **1 failed, 78 passed** |

  The one mutation for this that is shipped is labelled as the survivor it is,
  with the measurement in its `why`, because a survivor list is a to-do list and
  this entry has no possible fix: `mutate.py` rewrites one anchor at a time and
  the property is a property of the *pair*. The lower halves are load-bearing and
  separately witnessed - `_live_mouth(-0.5)` is 0.0 through `max(0.0, ...)`,
  because `sysfs.finite` accepts a negative float.
- **`_live_tilt`, `_live_mouth` and `_live_gaze` each carry a
  `try: float(value) except (TypeError, ValueError)` that cannot fire for any
  value a caller produces.** `sysfs.finite` is
  `isinstance(value, bool) or not isinstance(value, (int, float))`, so after it
  returns True the value is an `int`/`float` that is neither bool, NaN nor
  infinity, and `float()` on either cannot raise. The fourth validator in the same
  file, `_live_level`, carries no `try` **and says why in its own docstring**:
  *"which is why there is no `try` here, and why this cannot become a second copy
  of the finite check the repo consolidated into `sysfs`."* So the file holds two
  idioms for one validation and states the rule once.
- **...and the arms are still not worth removing.** Measured, the only input that
  reaches them is an `int`/`float` *subclass* overriding `__float__` to raise,
  and there the three guards return `0.0` while `_live_level` **raises
  `ValueError`**. Deleting the arms would make three sites raise where one
  already does. So this is a documentation divergence, not a defect, and the code
  is left as it is found.

Run on a snapshot at `/tmp/opencode/snap-coucou`; anchors asserted `count == 1`;
witnesses are bare method names, for the reason `gaze_iris.py` records.
"""

TEST_FILE = "tests/test_mascot_art.py"

_ART = "usr/lib/shani-chronoa/shani_chronoa/mascot_art.py"

_MUTATIONS = [

    # ── the lean: bound, direction, units, pivot, and what it does not include ──
    {
        "label": "the art's lean bound drifts from the engine's",
        "file": _ART,
        "old": "_TILT_MAX = 0.18",
        "new": "_TILT_MAX = 0.25",
        "why": "**The drift, in the direction that is invisible.** The engine "
               "clamps to `TILT_CLAMP` before it ever reaches the art, so a "
               "*looser* bound here still draws correctly at every value the "
               "engine produces - and the test that matters cannot see it, "
               "because nothing changed on screen. What it hides is the next "
               "edit: someone raises `TILT_CLAMP` to make a drag livelier, the "
               "art accepts the new value silently, and until someone happens "
               "to lower this number the tuning appears to do nothing.",
        "expect": [
            "test_the_art_and_the_engine_agree_on_the_bound",
            "test_a_bounded_lean_is_the_reference_constant",
        ],
    },
    {
        "label": "the art's lean bound is tighter than the engine's",
        "file": _ART,
        "old": "_TILT_MAX = 0.18",
        "new": "_TILT_MAX = 0.15",
        "why": "The same drift the other way, and **this is the direction that "
               "shows**. Measured: the engine's live peak lean is "
               "`TILT_CLAMP` exactly, so a 0.15 bound truncates 17% off every "
               "fast drag - a mascot that stops leaning mid-slide, which reads "
               "as the animation stuttering rather than as a clamp. Both "
               "directions are mutated because the property is that the two "
               "numbers are one number, not that they are close.",
        "expect": [
            "test_the_art_and_the_engine_agree_on_the_bound",
            "test_a_bounded_lean_is_the_reference_constant",
        ],
    },
    {
        "label": "the lean is clamped one-sided, so a leftward drag never leans left",
        "file": _ART,
        "old": "    return max(-_TILT_MAX, min(_TILT_MAX, degrees))",
        "new": "    return max(0.0, min(_TILT_MAX, degrees))",
        "why": "`max(-_TILT_MAX, ...)` is the whole of the lower bound, and "
               "`max(0.0, ...)` reads as the same guard until somebody drags "
               "left. The engine's own clamp is two-sided for a reason it "
               "documents - the character leans *into* the drag, whichever way "
               "it goes - so a one-sided art clamp turns half of all drags "
               "into a mascot that stays bolt upright and looks stuck.",
        "expect": [
            "test_a_tilt_of_each_sign_leans_its_own_way",
        ],
    },
    {
        "label": "radians are handed straight to `rotate()`, which wants degrees",
        "file": _ART,
        "old": "    lean = f'<g transform=\"rotate({math.degrees(tilt):.2f} 256 250)\">'",
        "new": "    lean = f'<g transform=\"rotate({tilt:.2f} 256 250)\">'",
        "why": "**The trap the two comment lines above it name, restored.** "
               "`mascot_drag` works in radians and SVG's `rotate()` takes "
               "degrees, and the conversion sits here so it cannot be forgotten "
               "at the call site - so deleting it is *legal* and produces no "
               "error. Measured: at full lean the picture carries "
               "`rotate(10.31 256 250)`, and without the conversion it carries "
               "`rotate(0.18 256 250)` - **57x too little**, a lean so small it "
               "is indistinguishable from upright. Every test that asks "
               "'does the tilt rotate anything' still passes.",
        "expect": [
            "test_a_tilt_rotates_the_character",
            "test_a_tilt_of_each_sign_leans_its_own_way",
        ],
    },
    {
        "label": "the lean pivots about the origin, so the character swings instead of leaning",
        "file": _ART,
        "old": "    lean = f'<g transform=\"rotate({math.degrees(tilt):.2f} 256 250)\">'",
        "new": "    lean = f'<g transform=\"rotate({math.degrees(tilt):.2f} 0 0)\">'",
        "why": "A rotation without a pivot rotates about `0 0`, so a 10.31 degree "
               "lean throws the orb - 250px down and 250px right of the pivot - "
               "into a **100px arc**. The picture is still a valid mascot, "
               "merely 400px from where it belongs, and the document is still "
               "well-formed XML. The pivot is the difference between leaning in "
               "place and falling over, and `256 250` is the orb's own centre.",
        "expect": [
            "test_a_tilt_rotates_the_character",
            "test_the_shadow_stays_level_while_the_orb_leans",
        ],
    },
    {
        "label": "the lean group is never closed, so the mascot renders as nothing",
        "file": _ART,
        "old": "    unlean = \"</g>\"",
        "new": "    unlean = \"\"",
        "why": "An unbalanced `<g>` is not a visible defect, it is **no "
               "document at all**: a renderer given unbalanced markup draws "
               "nothing, so the mascot vanishes and the result is "
               "indistinguishable from a broken window. This is the reason "
               "`test_a_tilted_mascot_is_still_well_formed_xml` parses rather "
               "than searching for a string - a string check cannot tell a "
               "closed group from an open one.",
        "expect": [
            "test_a_tilted_mascot_is_still_well_formed_xml",
        ],
    },
    {
        "label": "the shadow is swept into the lean, so a tilted mascot lifts off the desktop",
        "file": _ART,
        "old": "<ellipse cx=\"256\" cy=\"433\" rx=\"86\" ry=\"15\" fill=\"none\" stroke=\"{p['halo']}\" stroke-width=\"2\" opacity=\"0.32\"/></g>\n{lean}\n",
        "new": "{lean}\n<g opacity=\"0.8\">\n<ellipse cx=\"256\" cy=\"430\" rx=\"120\" ry=\"22\" fill=\"none\" stroke=\"{p['halo']}\" stroke-width=\"2.5\" opacity=\"0.5\"/>\n<ellipse cx=\"256\" cy=\"433\" rx=\"86\" ry=\"15\" fill=\"none\" stroke=\"{p['halo']}\" stroke-width=\"2\" opacity=\"0.32\"/></g>\n",
        "why": "**A position property, and the only reason it is one.** Moving "
               "`{lean}` above the shadow is a one-line change that produces no "
               "error, keeps the document well-formed, and leaves every element "
               "present - so a test that checks 'the shadow is still drawn' "
               "passes. The shadow is what fixes the mascot to the desktop; "
               "rotating it swings the ellipse 100px and the character appears "
               "to tumble across the screen, which is the one thing an "
               "always-on-top overlay must never look like. Asserted as "
               "`shadow < lean < orb`, because both orderings matter and no "
               "single-element check sees either.",
        "expect": [
            "test_the_shadow_stays_level_while_the_orb_leans",
        ],
    },

    # ── the mouth: bound, closed, behind the stroke, and scaling while speaking ─
    {
        "label": "the mouth's ceiling is `MOUTH_OPEN`, a spring target rather than a limit",
        "file": _ART,
        "old": "_MOUTH_MAX = 0.5",
        "new": "_MOUTH_MAX = 0.42",
        "why": "**The value the art is most likely to be 'corrected' to, and it "
               "is wrong for a measured reason.** `MOUTH_OPEN = 0.42` is what "
               "the mouth spring is *aimed at* while a lock is held, so the "
               "spring passes it: a real grab -> release -> suck peaks at "
               "**0.53987**, 12% above `MOUTH_OPEN` and 8% above `MOUTH_MAX`. "
               "Naming it here makes the art's widest mouth unreachable and "
               "holds the peak at the clamp for over a second. `MOUTH_MAX` is "
               "the ceiling because the post-drop branch *assigns* it rather "
               "than stepping toward it, so it is reached exactly - measured "
               "0.49997 at `t = T_SUCK_END`. This test asserted the wrong "
               "constant once; the correction is in `mascot_art` beside "
               "`_MOUTH_MAX`, not only here.",
        "expect": [
            "test_the_mouth_is_bounded_by_the_engine_own_ceiling",
        ],
    },
    {
        "label": "the closed-mouth guard is strict, so a mouth at exactly zero draws a scar",
        "file": _ART,
        "old": "    if amount <= 0.0:\n        return \"\"",
        "new": "    if amount < 0.0:\n        return \"\"",
        "why": "`<` against `<=`, one character, and the resting mascot carries "
               "it: `mouth=0.0` is what every caller passes when nothing is "
               "happening. At exactly `0.0` the guard now falls through and "
               "draws `rx=6.0 ry=2.0` - a flat dark ellipse across the lower "
               "face, which is not an open mouth at all. The docstring's stated "
               "reason is the rendering one: `rx=0` is a warning in some "
               "renderers, so a closed mouth must draw *nothing*, not a "
               "degenerate ellipse.",
        "expect": [
            "test_a_closed_mouth_draws_no_interior",
            "test_the_two_rest_values_change_nothing",
        ],
    },
    {
        "label": "the mouth interior is drawn over the lip curve instead of behind it",
        "file": _ART,
        "old": "    smile = (cheeks + _open_mouth(mouth) +\n"
               "             '<path d=\"M-11 28 Q0 38 11 28\" fill=\"none\" stroke=\"#07343a\" stroke-width=\"3.4\" '\n"
               "             'stroke-linecap=\"round\"/>')",
        "new": "    smile = (cheeks +\n"
               "             '<path d=\"M-11 28 Q0 38 11 28\" fill=\"none\" stroke=\"#07343a\" stroke-width=\"3.4\" '\n"
               "             'stroke-linecap=\"round\"/>' + _open_mouth(mouth))",
        "why": "The interior is **behind** the stroke so that an opening mouth "
               "keeps the same lower-lip curve and merely gains a dark interior "
               "behind it - 'the same mouth rather than a different one'. "
               "Reversed, the interior covers the lip: the mouth loses its "
               "smile entirely and becomes a filled oval with a line across it. "
               "Both elements are still present and the document is still "
               "well-formed, so this is invisible to any presence check and only "
               "the order says it - which is why the witness is "
               "`test_an_open_mouth_is_drawn_behind_the_smile_stroke`, a pair of "
               "`index()` calls.",
        "expect": [
            "test_an_open_mouth_is_drawn_behind_the_smile_stroke",
        ],
    },
    {
        "label": "the speaking mouth stops growing, so a caught mascot looks shut",
        "file": _ART,
        "old": "        mouth = (f'<ellipse cx=\"0\" cy=\"30\" rx=\"{7.0 + 2.0 * open_frac:.1f}\" '",
        "new": "        mouth = (f'<ellipse cx=\"0\" cy=\"30\" rx=\"7.0\" '",
        "why": "The speaking mouth is *already* open, so the amount is a scale "
               "on its own ellipse rather than an interior drawn behind a "
               "smile - and at `0.0` it is the 7x9 it has always been, which is "
               "what keeps every pre-existing caller byte-identical. Pinning the "
               "radius drops the whole channel for that state: the mouth is "
               "drawn, it is in the right place, it is the right colour, and it "
               "is the same size at every frame of a catch.",
        "expect": [
            "test_the_speaking_mouth_scales_rather_than_being_replaced",
        ],
    },
    {
        "label": "the mouth's upper clamp is dropped, and nothing observes it",
        "file": _ART,
        "old": "    return max(0.0, min(_MOUTH_MAX, amount))",
        "new": "    return max(0.0, amount)",
        "why": "**A survivor by construction, and that is the finding rather than "
               "a gap in the suite.** Read in isolation this clamp looks "
               "load-bearing: it is the only place `_MOUTH_MAX` is enforced on "
               "the value the engine hands over, and the engine's live peak "
               "(0.53987) does exceed it. But `open_frac` is "
               "`min(1.0, amount / _MOUTH_MAX)` one function down, so **any** "
               "upper bound here is absorbed before it reaches a number of "
               "pixels - not just this one. Measured, by removing each clamp on "
               "its own and running the art suite:\n\n"
               "    outer clamp removed      79 passed\n"
               "    inner clamp removed      79 passed\n"
               "    speaking clamp removed   79 passed\n"
               "    outer + inner removed     1 failed, 78 passed\n\n"
               "So the three are a redundant set in which **no single member is "
               "observable and only the outer+inner pair is**, and `mutate.py` "
               "cannot express a two-anchor mutation - which is why this is "
               "recorded as a documented survivor instead of being quietly "
               "dropped from the set. The clamp's *lower* half is load-bearing "
               "and does have a witness: `_live_mouth(-0.5)` is 0.0 through "
               "`max(0.0, ...)`, since `finite` accepts a negative float.",
        "expect": [
            "test_the_mouth_is_bounded_by_the_engine_own_ceiling",
        ],
    },
    {
        "label": "the mouth loses its non-number guard, so a NaN reading pins the halo",
        "file": _ART,
        "old": "    if not sysfs.finite(value):\n        return 0.0\n    try:\n        amount = float(value)",
        "new": "    try:\n        amount = float(value)",
        "why": "**The quiet one.** `float('nan')` succeeds, so dropping this "
               "guard lets NaN through to `max(0.0, min(_MOUTH_MAX, nan))`, and "
               "`min(0.5, nan)` is **0.5** - so a bad reading renders as the "
               "widest mouth the art can draw rather than as a closed one. That "
               "is the same failure `sysfs.finite`'s own docstring records for "
               "the petrol-pump query, in a different place: a NaN that reads "
               "as a loud, working indicator is worse than a NaN that reads as "
               "a broken one, because nothing looks broken.",
        "expect": [
            "test_a_mouth_that_is_not_a_number_or_a_negative_is_a_closed_mouth",
        ],
    },
    {
        "label": "the lean loses its non-number guard, so a NaN reaches `rotate()`",
        "file": _ART,
        "old": "    if not sysfs.finite(value):\n        return 0.0\n    try:\n        degrees = float(value)",
        "new": "    try:\n        degrees = float(value)",
        "why": "The same drop on the lean. `min(_TILT_MAX, nan)` is `_TILT_MAX`, "
               "so the failure is bounded - which is exactly why it is easy to "
               "ship: the picture still renders, the mascot simply leans as far "
               "as it can. The `TypeError` arm underneath covers the `str` case, "
               "but `float('nan')` raises neither, so NaN is the value that "
               "gets through and it is the value this guard exists for.",
        "expect": [
            "test_a_tilt_that_is_not_a_number_stands_the_character_upright",
        ],
    },
]

MUTATIONS = _MUTATIONS