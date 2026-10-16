"""Mutation set for the gaze: the iris amplitude, the refusal, and the sclera clip.

Three properties, and **the first two are decisions rather than defects** - which
is why each is mutated in both directions rather than only the way that breaks.

- **The amplitude is 5/4 and stays 5/4.** The iris (`rx=12 ry=16` about `cy=2`)
  reaches 17 right and 22 down at full travel, against a sclera of 15 and 20, so
  it genuinely overflows. The repair taken was a `clipPath` rather than a smaller
  amplitude, because shrinking to a fully-contained `(3.0, 2.0)` would have been
  an unevaluable visual change made to fix a containment problem clipping solves
  without touching how far the eyes move. **Mutation 1 is that decision made the
  other way**, and `test_the_iris_really_would_overflow_without_the_clip` is what
  fails when somebody quietly shrinks the amplitude instead. Mutation 2 is the
  opposite over-fix - the eyes stop moving at all - so the property is "the
  amplitude is this, not that", not "the iris is big".

- **Six expressions deliberately do not track.** `happy`, `excited`, `wink`,
  `sleepy`, `surprised` and `love` draw their own eye geometry - arcs, stars, a
  lid, hearts - and following the mouse with a closed lid or a heart reads as a
  glitch rather than as attention. `concerned` **does** draw the hero eye and is
  the control in both directions: a refusal that starts tracking, and a tracking
  eye that stops.

- **The clip is load-bearing and is shaped like the sclera it confines.** A
  typo'd `clip-path` reference clips nothing, renders nothing wrong-looking, and
  puts the eye back exactly where it was before the fix - on a document that
  reads correctly in a diff. The rasterised containment test is the only thing
  that catches it, which is why it exists rather than a markup assertion.

**The measured defect this set exists for**, from diffing two rasterised renders
and classifying changed pixels against the sclera ellipse: **158 px** outside at
idle (203 listening, 136 thinking), worst contrast **3.10:1** - the iris's teal on
the body's near-white. At the real render size that is a band about 2 SVG units
wide, 4 px at 1024 px and 8 px at 2048 px.

**Every anchor below was re-read from the live file and asserted `count == 1`
before the first run.** An earlier version of this set aimed at `_NO_PUPIL` and at
a two-line `gx = ...` / `gy = ...`; neither exists - the refusal is a chain of
early `if expression == ...` returns and the geometry is one tuple-assignment
line - so all thirteen mutations below were false greens against anchors that
never matched.

**Witnesses here are bare method names, not `Class::method`.** `mutate.py` builds
its verdict set from `line.split("::")[-1]` and its `--check-witnesses`
collection from `rsplit("::", 1)[-1]`, so a qualified name compares a full id
against a bare one and marks **every** witness in the set stale. Its own comment
records this as "two representations of one thing compared as strings" - and the
first run of this set produced sixteen STALE WITNESS lines and two SURVIVORs whose
failures were both the right tests, which is exactly what the runner reports when
a witness cannot be resolved.
"""

TEST_FILE = "tests/test_mascot_art.py"

_ART = "usr/lib/shani-chronoa/shani_chronoa/mascot_art.py"

# The sclera clip as `svg()`'s template spells it. Anchored on this line only:
# anchoring on the drawn ellipse as well would make one mutation touch two
# anchors for one edit, which `tools/mutate.py` reports as a pair rather than as
# one.
_CLIP_DEF = (
    '<clipPath id="sclera"><ellipse rx="{_SCLERA_RX:g}" ry="{_SCLERA_RY:g}"/></clipPath>')

# The eye group opens on one line and the clip is the second, eleven spaces in.
_CLIP_USE = "           f'clip-path=\"url(#sclera)\">'"

#: One number, two places. The drawn ellipse and the clip both read it, so this
#: anchor alone cannot see the pair drift apart - that is a separate mutation.
_SCLERA_DRAW = (
    "           f'<ellipse rx=\"{_SCLERA_RX:g}\" ry=\"{_SCLERA_RY:g}\" fill=\"#06313a\"/>'")

_MUTATIONS = [

    # --- the amplitude decision, both directions ------------------------------
    {
        "label": "the amplitude is shrunk to the fully-contained value instead of clipping",
        "file": _ART,
        "old": "_GAZE_X, _GAZE_Y = 5.0, 4.0",
        "new": "_GAZE_X, _GAZE_Y = 3.0, 2.0",
        "why": "The wrong repair for the overflow, and the one that looks like the "
               "right one: shrink the movement until it fits. `(3.0, 2.0)` is the "
               "amplitude at which the iris is fully inside the sclera, so this "
               "makes the overflow test's own arithmetic true without the clip "
               "doing anything.",
        "expect": [
            "test_the_iris_really_would_overflow_without_the_clip",
        ],
    },
    {
        "label": "the amplitude is zero, so the eyes never move at all",
        "file": _ART,
        "old": "_GAZE_X, _GAZE_Y = 5.0, 4.0",
        "new": "_GAZE_X, _GAZE_Y = 0.0, 0.0",
        "why": "The over-fix in the other direction, and the reason the amplitude "
               "test is a measured-travel assertion rather than an overflow one: "
               "'the iris is contained' is also what a mascot that never looks at "
               "anything reports.",
        "expect": [
            "test_the_iris_travels_exactly_the_declared_amplitude",
        ],
    },
    {
        "label": "the sclera is enlarged until the real amplitude fits inside it",
        "file": _ART,
        "old": "_SCLERA_RX, _SCLERA_RY = 15.0, 20.0",
        "new": "_SCLERA_RX, _SCLERA_RY = 18.0, 24.0",
        "why": "The third way to 'fix' the same overflow - grow the eye instead - "
               "and it moves the drawn sclera and the clip together, so a test "
               "comparing the two against each other sees nothing. Only the "
               "overflow arithmetic catches it, which is why that test reads the "
               "module's own numbers rather than a literal.",
        "expect": [
            "test_the_iris_really_would_overflow_without_the_clip",
        ],
    },

    # --- the geometry: which axis moves where --------------------------------
    {
        "label": "the two gaze axes are swapped, so a diagonal looks the wrong way",
        "file": _ART,
        "old": "    gx, gy = gaze[0] * _GAZE_X, gaze[1] * _GAZE_Y",
        "new": "    gx, gy = gaze[1] * _GAZE_X, gaze[0] * _GAZE_Y",
        "why": "Reads correctly and is invisible on either axis alone, because each "
               "axis still travels its own declared distance. Only a diagonal "
               "bearing separates them - the same shape as `RPP`/`QPP`'s critique "
               "of a latency proxy: the harness measures one dimension and the "
               "defect lives in two.",
        "expect": [
            "test_the_iris_travels_exactly_the_declared_amplitude",
        ],
    },
    {
        "label": "the bearing is applied without the amplitude, so the iris leaves the eye",
        "file": _ART,
        "old": "    gx, gy = gaze[0] * _GAZE_X, gaze[1] * _GAZE_Y",
        "new": "    gx, gy = gaze[0], gaze[1]",
        "why": "Loses the declared amplitude entirely. A unit bearing is now a "
               "whole SVG unit of travel rather than 5/4 of one, so the pupil "
               "crosses the sclera and the clip has to hide more than it should.",
        "expect": [
            "test_the_iris_travels_exactly_the_declared_amplitude",
        ],
    },

    # --- the clip --------------------------------------------------------------
    {
        "label": "the eye group carries no clip, so the iris spills again",
        "file": _ART,
        "old": _CLIP_USE,
        "new": "           f'>'",
        "why": "**The shipped defect this whole feature is about**, restored "
               "verbatim. Nothing about the resulting document looks wrong: the eye "
               "draws, the iris draws, and the overflow is the 158 px at 3.10:1 "
               "contrast measured on this file before the clip landed.",
        "expect": [
            "test_the_eye_group_carries_the_clip",
            "test_the_iris_never_leaves_the_sclera",
        ],
    },
    {
        "label": "the clip points at the ball's id, so two clipPaths share one name",
        "file": _ART,
        "old": _CLIP_DEF,
        "new": '<clipPath id="ball"><ellipse rx="{_SCLERA_RX:g}" '
               'ry="{_SCLERA_RY:g}"/></clipPath>',
        "why": "A typo'd reference clips nothing and renders nothing "
               "wrong-looking. It is also not a *missing* clip, so a test asserting "
               "the attribute is present passes; and both ids still exist in the "
               "document, so a test asserting `ball` is present also passes. Only "
               "the two-ids assertion separates them.\n\n"
               "        `test_the_eye_group_carries_the_clip` was named here and "
               "**removed as an over-claim**: it asserts only that the "
               "`clip-path` *attribute* is present, and this mutation keeps it "
               "present - it is the reference's *target* that moved. The runner's "
               "own \"NOT caught by\" line is what made the over-claim visible, "
               "which is the mechanism that exists for it.",
        "expect": [
            "test_the_two_clip_paths_have_two_ids",
        ],
    },
    {
        "label": "the clip's ry is a hand-typed 12, so it drifts from the drawn sclera",
        "file": _ART,
        "old": _CLIP_DEF,
        "new": '<clipPath id="sclera"><ellipse rx="{_SCLERA_RX:g}" ry="12"/></clipPath>',
        "why": "The `wpctl`-three-copies shape: one number written twice instead of "
               "read once. It clips - so the containment test still passes for a "
               "gaze that happens not to reach the bottom - and the drawn eye is a "
               "different shape from the clip that confines it, which is only "
               "visible by comparing the two.",
        "expect": [
            "test_the_clip_is_shaped_like_the_sclera_it_confines",
        ],
    },
    {
        "label": "the drawn sclera is a hand-typed pair, drifting from the clip",
        "file": _ART,
        "old": _SCLERA_DRAW,
        "new": "           f'<ellipse rx=\"14\" ry=\"19\" fill=\"#06313a\"/>'",
        "why": "The mirror of the mutation above, and the one that is a visible "
               "defect rather than a latent one: the eye is drawn at 14/19 and "
               "clipped at 15/20, so a band of iris can sit inside the clip and "
               "outside the eye the person is looking at.",
        "expect": [
            "test_the_clip_is_shaped_like_the_sclera_it_confines",
        ],
    },

    # --- `_live_gaze`: the normalisation gate -----------------------------------
    {
        "label": "a bearing beyond the range is not clamped, so it walks up the eye",
        "file": _ART,
        "old": "    return (max(-1.0, min(1.0, float(dx))), max(-1.0, min(1.0, float(dy))))",
        "new": "    return (float(dx), float(dy))",
        "why": "The clamp is what turns 'wherever the pointer is' into '-1..1'. "
               "Without it a pointer near a window's corner answers a bearing far "
               "outside the range, and the pupil leaves the eye by an arbitrary "
               "distance rather than by the declared amplitude.",
        "expect": [
            "test_a_finite_pair_is_carried_and_clamped",
            "test_a_bearing_beyond_the_range_clamps_at_the_amplitude",
        ],
    },
    {
        "label": "only the x axis is clamped, so a pointer below the mascot walks it off",
        "file": _ART,
        "old": "    return (max(-1.0, min(1.0, float(dx))), max(-1.0, min(1.0, float(dy))))",
        "new": "    return (max(-1.0, min(1.0, float(dx))), float(dy))",
        "why": "The over-fix in one axis, and it is a plausible edit rather than a "
               "silly one: a reader 'fixing' an axis by hand touches one. The test "
               "has to carry a case where **only** y is out of range for this to be "
               "visible at all - which is why the table is over pairs and not over "
               "scalars.",
        "expect": [
            "test_a_finite_pair_is_carried_and_clamped",
        ],
    },
    {
        "label": "the finite refusal is gone, so a NaN bearing moves the pupils off the face",
        "file": _ART,
        "old": "    if not (sysfs.finite(dx) and sysfs.finite(dy)):\n"
               "        return (0.0, 0.0)",
        "new": "    if False:\n"
               "        return (0.0, 0.0)",
        "why": "The guard that made this feature safe to call from a `GLib` "
               "callback, and it had **no test at all** until this pass: "
               "`float('nan')` passes `max(-1.0, min(1.0, nan))` unchanged in the "
               "direction that matters, so the iris goes somewhere arbitrary on a "
               "face nobody asked about. A mutation against an untested guard is a "
               "false green - which is what this one was on the first run.",
        "expect": [
            "test_something_that_is_not_a_finite_pair_looks_forward",
            "test_a_refused_gaze_and_a_forward_gaze_draw_the_same_picture",
        ],
    },
    {
        "label": "the non-pair refusal is gone, so a stray value raises inside the callback",
        "file": _ART,
        "old": "    except (TypeError, ValueError):\n        return (0.0, 0.0)",
        "new": "    except (ValueError,):\n        return (0.0, 0.0)",
        "why": "The `TypeError` half is the load-bearing one: `dx, dy = value` on "
               "a string or a one-element tuple raises `TypeError`, and this is "
               "documented as the function that keeps that from happening where the "
               "exception would be **swallowed**. Narrowing the tuple to "
               "`ValueError` looks tidier and removes exactly the case a caller can "
               "actually hand it.",
        "expect": [
            "test_something_that_is_not_a_finite_pair_looks_forward",
        ],
    },
    {
        "label": "a refused bearing is drawn anyway, and the drawing test stops meaning anything",
        "file": _ART,
        "old": "    gaze = _live_gaze(gaze)",
        "new": "    gaze = gaze if isinstance(gaze, tuple) and len(gaze) == 2 else (0.0, 0.0)",
        "why": "**The reachability mutation.** `_live_gaze` can be perfectly "
               "tested while `svg()` stops calling it, and every test in the "
               "normalisation class would still pass - which is the 'a test that "
               "reaches past the call site is testing the function' trap. This "
               "keeps the arity check and drops the finite one, so it is caught "
               "only by the pair that goes through `svg`.",
        "expect": [
            "test_the_refusal_is_reachable_through_svg_not_only_the_helper",
            "test_a_refused_gaze_and_a_forward_gaze_draw_the_same_picture",
        ],
    },

    # --- the faces that refuse to track, and the one that does not -------------
    {
        "label": "happy stops refusing to track, so the arcs gain a pupil behind them",
        "file": _ART,
        "old": "    if expression == \"happy\":",
        "new": "    if expression == \"__never__\":",
        "why": "The refusal is a chain of early returns, not a set membership - "
               "which is why an earlier version of this set aimed at a symbol that "
               "does not exist. `happy` draws arcs; falling through gives it a "
               "hero eye behind them, and the face is neither of the two it claims "
               "to be.",
        "expect": [
            "test_the_faces_that_refuse_to_track_draw_no_hero_eye",
        ],
    },
    {
        "label": "love stops refusing to track, so the hearts gain a pupil",
        "file": _ART,
        "old": "    if expression == \"love\":",
        "new": "    if expression == \"__never__\":",
        "why": "The same refusal on the one expression whose geometry is most "
               "clearly not an eye. One mutation per refusing expression would be "
               "thirteen entries of the same assertion; two at opposite ends of the "
               "chain pin that the refusals are per-expression and not one shared "
               "flag, and the test enumerates all six.",
        "expect": [
            "test_the_faces_that_refuse_to_track_draw_no_hero_eye",
        ],
    },
]

MUTATIONS = _MUTATIONS