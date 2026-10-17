"""Mutation set for the gaze on the `mascot.py` side: the motion wiring and the deadzone.

The art side is `gaze_iris.py`. This half is everything between the pointer and
the art: whether the bearing is read, whether it is normalised, whether it is
worth drawing, and whether it is let go of again.

**Four of these are over-fixes rather than defects**, and that is the point of
writing them out. A deadzone is a threshold with two wrong directions and both
are reachable by an edit that looks like tidying:

- removed (`0.0`) - every flick of the mouse is a full re-rasterise, which is the
  cost the constant exists to avoid, and it is invisible on a machine that is not
  busy;
- raised (`3.0`, past the largest change a bearing can make) - the eyes never move
  at all, which is the same invisible failure in the other direction.

The 1x1 guard has the same shape and is the one this file's own measurements
produced: GTK reports a **1x1** allocation for a window that has not been laid out,
so `half_w <= 0` (the arithmetically-tidy spelling) divides by a half-pixel and
answers `(1.0, 1.0)` - the mascot aims at a corner because of a layout state that
has nothing to do with where the pointer is. Both spellings are below.

**Two mutations exist because a test reached past the call site.** `_gaze_moved`
has its own class and is perfectly testable while `_on_motion` stops consulting
it - so *that* mutation's witness has to be the one that drives the handler, not
the one that calls the helper. The same applies to `_motion_is_reduced` being
answerable correctly while `_on_motion` ignores it. Both are separate mutations
here for that reason: the helper and its call site are two guards, and mutating
one leaves the other's test green.

**The third reachability case is the `_win is None` check**, which is load-bearing
on every path and is mutated here only as part of a compound line rather than on
its own - `test_no_window_is_not_a_crash` is the witness, and it is already in the
file.

**Every anchor below was re-read from the live file and asserted `count == 1`
before the first run.** `str.count` and not `grep`: a needle that matches a
*suffix* of a line is a false green, which is this repository's recorded
`--check-anchors` trap, and eleven-space indentation in a wrapped return is exactly
where it lives.
"""

TEST_FILE = "tests/test_mascot.py"

_MASCOT = "usr/lib/shani-chronoa/shani_chronoa/mascot.py"

# The bearing guard as the function spells it, both lines - one anchor for one
# edit, because `tools/mutate.py` applies a pair in order and would report the
# result as two mutations.
_GAZE_MOVED = ("    return (abs(new[0] - old[0]) >= _GAZE_DEADZONE\n"
               "            or abs(new[1] - old[1]) >= _GAZE_DEADZONE)")

_MUTATIONS = [

    # --- the deadzone, both directions -----------------------------------------
    {
        "label": "the deadzone is zero, so every flick of the mouse re-rasterises",
        "file": _MASCOT,
        "old": "_GAZE_DEADZONE = 0.08",
        "new": "_GAZE_DEADZONE = 0.0",
        "why": "The shipped reason for the constant, removed. `_render` rasterises "
               "the whole SVG through a `GdkPixbuf` loader and pointer motion "
               "arrives far faster than a mascot needs to be seen to move, so "
               "`>= 0.0` is true for every event including a pointer that did not "
               "move. The comment above the constant is the measured version of "
               "this.",
        "expect": [
            "test_a_flick_smaller_than_the_deadzone_is_not_worth_a_redraw",
            "test_no_change_at_all_is_never_a_redraw",
        ],
    },
    {
        "label": "the deadzone is past the largest change a bearing can make",
        "file": _MASCOT,
        "old": "_GAZE_DEADZONE = 0.08",
        "new": "_GAZE_DEADZONE = 3.0",
        "why": "The over-fix, and the one that survives a review unnoticed because "
               "it makes the code *quieter* about redrawing. A bearing is clamped to "
               "-1..1, so no change can exceed 2.0 and this makes every "
               "`_gaze_moved` answer False - the mascot never looks at anything "
               "again, and reads as a mascot with no gaze.",
        "expect": [
            "test_the_pointer_moves_the_eyes_and_redraws",
            # `test_a_change_at_the_deadzone_is` was here and was removed on the
            # runner's own "NOT caught by" line, not by a new test. It reads the
            # constant it is checking (`at = mascot._GAZE_DEADZONE`), so a mutant
            # supplies its own value and the assertions hold at 3.0 exactly as they
            # hold at 0.08 - measured by evaluating the test's body at all three
            # values: green at 0.08, green at 3.0, red only at 0.0. It is a good
            # *specification* of the boundary (`>=`, and `at * 0.999` below it)
            # and a witness for nothing about the constant's value. The handler
            # test above is what actually bites, because it moves the bearing by
            # 1.0 and cannot tell a floor of 0.08 from one of 3.0.
        ],
    },
    {
        "label": "only the horizontal axis can trigger a redraw",
        "file": _MASCOT,
        "old": _GAZE_MOVED,
        "new": "    return abs(new[0] - old[0]) >= _GAZE_DEADZONE",
        "why": "Dropping the `or` arm looks like a simplification and is a real "
               "defect: a pointer that moves straight up or down is a bearing "
               "change on the y axis alone, and it stops redrawing. Symmetric with "
               "the axis swap in `gaze_iris.py` - one axis tested, the defect in "
               "two.",
        "expect": [
            "test_either_axis_alone_is_enough",
        ],
    },
    {
        "label": "every bearing change counts as worth drawing",
        "file": _MASCOT,
        "new": "    return True",
        "old": _GAZE_MOVED,
        "why": "The helper-level version of mutation 1, kept as a separate entry "
               "because the two are reached differently: this one is caught by the "
               "class that calls `_gaze_moved` directly, and the call-site mutation "
               "below by the handler test. **Merging them would leave one of the "
               "two guards unwitnessed** - which is the 'a witness that covers half "
               "the readers of a property reads as coverage of all of them' shape.",
        "expect": [
            "test_a_flick_smaller_than_the_deadzone_is_not_worth_a_redraw",
            "test_no_change_at_all_is_never_a_redraw",
        ],
    },

    # --- `_on_motion` consulting the deadzone at all ---------------------------
    {
        "label": "`_on_motion` stops consulting the deadzone entirely",
        "file": _MASCOT,
        "old": "        if not _gaze_moved(self._gaze, bearing):\n            return",
        "new": "        if False:\n            return",
        "why": "**The reachability mutation for the deadzone.** `_gaze_moved` stays "
               "correct and its own class stays green; what is gone is the handler's "
               "use of it. Only a witness that drives `_on_motion` can see this, "
               "which is why `test_a_flick_below_the_deadzone_draws_nothing` is the "
               "named witness and `TestTheDeadzone` is not - that class would pass "
               "with this applied.",
        "expect": [
            "test_a_flick_below_the_deadzone_draws_nothing",
        ],
    },

    # --- the 1x1 guard, both spellings ----------------------------------------
    {
        "label": "the 1x1 guard is loosened to `<= 0`, so an unlaid-out window aims at a corner",
        "file": _MASCOT,
        "old": "    half_w, half_h = width / 2.0, height / 2.0\n"
               "    if half_w < 1 or half_h < 1:\n"
               "        return (0.0, 0.0)",
        "new": "    half_w, half_h = width / 2.0, height / 2.0\n"
               "    if half_w <= 0 or half_h <= 0:\n"
               "        return (0.0, 0.0)",
        "why": "The arithmetically-tidy spelling, and it is wrong for a reason "
               "this repo measured rather than reasoned: GTK reports **1x1** for a "
               "window that has not been laid out, so `half_w` is 0.5, `<= 0` does "
               "not fire, and the bearing divides by half a pixel and answers its "
               "maximum. `width=1` with a pointer at `(5, 5)` answers `(1.0, 1.0)` "
               "without this - the docstring states that measurement.",
        "expect": [
            "test_an_unlaid_out_window_looks_forward",
        ],
    },
    {
        "label": "the 1x1 guard is gone with the layout state it exists for",
        "file": _MASCOT,
        "old": "    half_w, half_h = width / 2.0, height / 2.0\n"
               "    if half_w < 1 or half_h < 1:\n"
               "        return (0.0, 0.0)",
        "new": "    half_w, half_h = width / 2.0, height / 2.0",
        "why": "The same defect with no guard at all rather than a loosened one. A "
               "1x1 window has no centre to be away from, so looking forward is the "
               "honest answer - and the first `motion` event can arrive before "
               "layout finishes, so this is reachable in the shipped app rather "
               "than only in a test.",
        "expect": [
            "test_an_unlaid_out_window_looks_forward",
        ],
    },
    {
        "label": "a pointer outside the window is no longer clamped",
        "file": _MASCOT,
        "old": "    return (max(-1.0, min(1.0, (x - half_w) / half_w)),\n"
               "            max(-1.0, min(1.0, (y - half_h) / half_h)))",
        "new": "    return ((x - half_w) / half_w,\n            (y - half_h) / half_h)",
        "why": "The clamp turns 'wherever the pointer is' into '-1..1'. A pointer "
               "dragged to a window corner is a bearing of 5 or -8, and the iris "
               "travels that many declared amplitudes rather than one - which the "
               "clip then has to hide, so the defect presents as the overflow this "
               "feature fixed rather than as a clamp.",
        "expect": [
            "test_a_pointer_outside_the_window_clamps_rather_than_winding_up",
        ],
    },

    # --- reduce-motion, helper and call site separately -----------------------
    {
        "label": "`_on_motion` ignores the reduce-motion preference",
        "file": _MASCOT,
        # **Re-anchored 2026-10-17: this needle rotted in `1cf655c`, which is
        # mine.** It read the two guards as one line,
        # `if self._win is None or _motion_is_reduced(): return`, and
        # `mascot_events` inserted `self._note_activity()` *between* them - which
        # is the correct behaviour, because presence must wake the mascot even
        # when reduce-motion stops the eyes following, and the comment it left
        # says so. So the split is a fix and the anchor was the thing that
        # described the old shape. Verified against HEAD rather than assumed:
        # `git show HEAD:...mascot.py | grep -c '_win is None or
        # _motion_is_reduced'` is **0**, so this was never rot in the working
        # tree alone - it was committed that way, by me.
        #
        # The new needle is the guard and the line it guards, which is what the
        # claim is about, and it was checked in **both** directions: `count(old)
        # == 1`, the match at a line start, the splice parses, the splice
        # changes the text, and `count(new) == 1` both before and after.
        # The old `new` was `if self._win is None:\n            return`, which the
        # split made a duplicate of text already in the file - so a reverse
        # replace would have been ambiguous, which is the half of the anchor
        # rule that is only checked in one direction by mistake.
        "old": "        if _motion_is_reduced():\n"
               "            return\n"
               "        bearing = _gaze_for(x, y, self._win.get_width(),"
               " self._win.get_height())\n",
        "new": "        bearing = _gaze_for(x, y, self._win.get_width(),"
               " self._win.get_height())\n",
        "why": "**The call site**, and it is a separate mutation from the helper "
               "because both can be removed independently. The gaze is continuous "
               "motion and the bob is the other half of it; a setting honoured for "
               "one and not the other is a setting honoured for whichever one "
               "nobody noticed.",
        "expect": [
            "test_reduce_motion_stops_the_eyes_too",
        ],
    },
    {
        "label": "`_motion_is_reduced` always answers False",
        "file": _MASCOT,
        "old": "        from shani_chronoa.automation_overlay import reduced_motion\n"
               "        return bool(reduced_motion())",
        "new": "        from shani_chronoa.automation_overlay import reduced_motion\n"
               "        return False",
        "why": "**The helper**, reached through the same witness. It also drops the "
               "call, so `reduced_motion` becomes an unused import inside the "
               "function - which is why the import is kept in the replacement: the "
               "mutation has to be about the answer, not about a lint finding. "
               "**Its witness was also over-claimed, in the opposite direction "
               "from the two above.** `test_reduce_motion_stops_the_eyes_too` "
               "replaces the *helper itself* with `lambda: True` in order to reach "
               "`_on_motion`'s call site - which is exactly what makes it blind to "
               "the helper's body: with that patch in place the body never runs, so "
               "a helper answering `False` unconditionally passed. The named "
               "witness is now "
               "`test_the_reduce_motion_reader_answers_what_the_desktop_said`, which "
               "patches `automation_overlay.reduced_motion` - **what the helper "
               "reads** - and so exercises the answer itself. Note that the same "
               "test is still correct on the *call-site* mutation above, where it "
               "does bite: that one removes the call, so a patched-to-True helper "
               "is consulted no longer.",
        "expect": [
            "test_the_reduce_motion_reader_answers_what_the_desktop_said",
        ],
    },

    # --- the wiring -----------------------------------------------------------
    {
        "label": "the motion controller is built but connected to nothing",
        "file": _MASCOT,
        "old": "        motion.connect(\"motion\", self._on_motion)",
        "new": "        motion.connect(\"motion\", lambda *_: None)",
        "why": "The controller, the connect call and the handler all exist and the "
               "mascot has no gaze - the whole-wire-up class this repository "
               "records most often. The handler stays defined and correct, so only "
               "a witness that *presses* something catches it. **The named witness "
               "was `test_the_pointer_moves_the_eyes_and_redraws`, which calls "
               "`mascot.Mascot._on_motion(m, None, ...)` unbound on a `Mascot` "
               "whose `_build()` never ran** - so no controller exists, the "
               "mutation changes nothing it can observe, and this was one of the "
               "set's three survivors. Corrected to the name, not the test: the "
               "witness has to be one that invokes the handler `_build()` "
               "recorded, because that is the only thing which can distinguish "
               "`_on_motion` from a no-op in the connect line.",
        "expect": [
            "test_the_pointer_reaches_the_eyes_through_the_controller_that_is_wired_to_it",
        ],
    },
    {
        "label": "`leave` is wired to a handler that does nothing",
        "file": _MASCOT,
        "old": "        motion.connect(\"leave\", lambda *_: self._recentre())",
        "new": "        motion.connect(\"leave\", lambda *_: None)",
        "why": "Without the boundary event the gaze stays locked on wherever the "
               "pointer went, which reads as the mascot staring at the edge of the "
               "screen for the rest of the session. The long comment above it "
               "explains that the boundary crossing is what lets the eyes return - "
               "and the code underneath is the control. **Same over-claim as the "
               "line above it:** the witness was "
               "`test_leaving_recentres_and_redraws_once`, which calls "
               "`mascot.Mascot._recentre(m)` directly and so never sees the "
               "connect line. Corrected to the recorded-handler witness.",
        "expect": [
            "test_leaving_reaches_the_recentre_through_the_controller_that_is_wired_to_it",
        ],
    },

    # --- `_recentre`, both directions -----------------------------------------
    {
        "label": "leaving redraws even when the eyes are already forward",
        "file": _MASCOT,
        "old": "        if self._win is None or self._gaze == (0.0, 0.0):\n"
               "            return",
        "new": "        if self._win is None:\n            return",
        "why": "The over-fix on the recentre guard, and it is a real "
               "performance defect of the same kind the deadzone exists for: "
               "pointer motion crosses a boundary constantly, so every crossing "
               "rasterises the whole SVG for a change that has not happened.",
        "expect": [
            "test_leaving_when_already_forward_draws_nothing",
        ],
    },
    {
        "label": "leaving no longer puts the eyes back in the middle",
        "file": _MASCOT,
        "old": "        self._gaze = (0.0, 0.0)\n        self._render()",
        "new": "        self._render()",
        "why": "The handler is still called and still returns early when there is no "
               "window, so it looks wired and behaves. The state write is the whole "
               "of it - remove it and the rendered gaze never changes, which is "
               "staring at the edge of the screen for the rest of the session.",
        "expect": [
            "test_leaving_recentres_and_redraws_once",
        ],
    },
]

MUTATIONS = _MUTATIONS