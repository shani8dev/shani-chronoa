"""Mutation set for the drag/follow engine: the follow, the lock, the hop, the
timeline, and the two lifecycle guards.

The engine is pure arithmetic with no GTK in it, which is the whole point of
porting it - so almost everything here is a *number* rather than a branch, and
the mutation that matters is the one that changes a number by a plausible
amount and leaves every shape intact. A shape assertion cannot see that; a
measurement can. Every witness below is therefore either a measured travel, a
measured band, or a measured timeline point, and the two that are only shape
assertions say so.

**The five decisions this set exists to pin, each in both directions.** The
rest are the load-bearing arithmetic.

- **The lock band is 60/90 per axis, scaled by that axis.** The reference's
  latch is `hypot(dx, dy) < 60` inside a 640x176 island, which is *two different
  tolerances*, and a single radius on a square stage is not the same thing. The
  module's own constant table records the measurement: scaling by the width
  alone gives `0.968 * 132 = 128px` in a 172px stage, the latch fires on frame 1
  of every drag, and **the hysteresis becomes unobservable** - which deletes
  mechanic 2, the one mechanic with a distinct reason to exist. Measured at the
  shipped ratios: in-band 19.88px, out-of-band 29.81px on a 212px stage.
- **`62`-relative vs `stage`-relative is a decision, not a default.** The hop is
  5px on a 62px character (a ratio of `d`); the lock band is 60 of 640 (a ratio
  of the stage). Both directions are mutated because getting either wrong is
  invisible on one scale and broken on the other.
- **The catch lock is on distance alone, and it *stays* on distance alone.** A
  pointer leaving at speed must not unlatch, or it hops again on the way past -
  the reference only breaks the latch on distance. Mutating the speed guard to
  apply to the unlatch is the plausible wrong repair, and it is here.
- **`_t_ref` has two anchors and reads whichever is live.** One anchor plus
  "restart `t` on release" makes the phase clock jump by however long the drag
  lasted, on the one frame that decides whether the work timeline is seen at
  all. Both anchors are mutated, and the over-fix - both anchors reading the
  drop - is mutated too.
- **`_simulate_to` caps the catch-up.** A stalled tab would otherwise spin the
  integrator for however long it was away. The cap is defence in depth against
  a hitch, and deleting it is equivalent on any test that never stalls - which is
  why the set carries the equivalence note rather than pretending a witness.

**Two guards were live defects before this set, and both are restored
verbatim.** `sprite_visible` was `pt >= grow_start`, so the sprite stayed
painted through the whole invisible bar and only reappeared at the grow; and
`MascotMotion.__init__` refused `stage_w <= size` when its own docstring says
*"every mechanic here is travel"*, which **permitted `stage == size`** - a
mascot in a window its own size, with zero travel and every mechanic inert.

**Every anchor below was read from the live file and asserted `count == 1`
before the first run**, and the run is done on a snapshot at
`/tmp/opencode/snap-coucou` rather than on the shared tree - a mutation run that
rewrites product source in this repository has shipped another session's defect
three times.

**Witnesses are bare method names, not `Class::method`**, for the reason
`gaze_iris.py` records: `mutate.py` builds its verdict set from
`line.split("::")[-1]`, so a qualified name compares a full id against a bare
one and marks every witness in the set stale.
"""

TEST_FILE = "tests/test_mascot_drag.py"

_DRAG = "usr/lib/shani-chronoa/shani_chronoa/mascot_drag.py"

_MUTATIONS = [

    # --- the constructor guard, both directions --------------------------------
    {
        "label": "a stage exactly the sprite's size is permitted, so nothing can travel",
        "file": _DRAG,
        "old": "        if stage_w <= size or stage_h <= size:",
        "new": "        if stage_w < size or stage_h < size:",
        "why": "**The shipped defect, restored verbatim.** The docstring two lines "
               "below says *'a mascot in a window its own size cannot move at all, "
               "and every mechanic here is travel'* - and `<=` is what enforces it. "
               "Relaxing it to `<` permits `stage == size`, where `_follow_max == "
               "_follow_min` and the follow, the lock band and the tilt are all "
               "measured against a stage with zero travel. A guard that permits "
               "the state its own comment forbids is worse than no guard.",
        "expect": [
            "test_a_stage_exactly_the_sprite_size_is_refused_too",
        ],
    },
    {
        "label": "the stage guard is gone entirely",
        "file": _DRAG,
        "old": "        if stage_w <= size or stage_h <= size:\n"
               "            raise ValueError(\n"
               "                \"the stage must hold the sprite with room to travel: a mascot in a \"\n"
               "                \"window its own size cannot move at all, and every mechanic here is \"\n"
               "                \"travel\")",
        "new": "        if False:\n"
               "            raise ValueError(\"unused\")",
        "why": "The constructor is the only place a bad stage is refused, and "
               "`_follow_max` is then *less than* `_follow_min`, so the follow's "
               "`max(min, min(max, x))` clamp inverts and the character is pinned "
               "to the far edge instead of near the near one. Everything still "
               "returns a `Pose`, so a shape assertion sees nothing.",
        "expect": [
            "test_a_stage_that_cannot_hold_the_sprite_is_refused",
            "test_a_stage_that_cannot_hold_the_sprite_at_all_is_refused_too",
        ],
    },

    # --- the lock band: per-axis, and the 60/90 ratio -------------------------
    {
        "label": "the lock band is one radius scaled by the width, on both axes",
        "file": _DRAG,
        "old": "LOCK_IN_RATIO_X = 60.0 / 640.0\nLOCK_OUT_RATIO_X = 90.0 / 640.0\n"
               "LOCK_IN_RATIO_Y = 60.0 / 176.0\nLOCK_OUT_RATIO_Y = 90.0 / 176.0",
        "new": "LOCK_IN_RATIO_X = 60.0 / 640.0\nLOCK_OUT_RATIO_X = 90.0 / 640.0\n"
               "LOCK_IN_RATIO_Y = 60.0 / 640.0\nLOCK_OUT_RATIO_Y = 90.0 / 640.0",
        "why": "**The mistake the constant table is written to prevent**, restored. "
               "`0.09375 * 132 = 12.4px` of horizontal tolerance against "
               "`0.09375 * 212 = 19.9px` of vertical - and `by` is pinned to the "
               "stage centre while the lock distance measures "
               "`cursorY + 0.0795*stage_h`, so the band on y is an order of "
               "magnitude off the one on x. The latch stops being reachable from "
               "most of the stage, which reads as 'the mascot is harder to catch' "
               "rather than as a wrong scale.",
        "expect": [
            "test_each_axis_has_its_own_band_because_the_stage_is_not_square",
        ],
    },
    {
        "label": "the hysteresis is gone, so the in-band and out-of-band thresholds are equal",
        "file": _DRAG,
        "old": "LOCK_IN_RATIO_X = 60.0 / 640.0\nLOCK_OUT_RATIO_X = 90.0 / 640.0",
        "new": "LOCK_IN_RATIO_X = 60.0 / 640.0\nLOCK_OUT_RATIO_X = 60.0 / 640.0",
        "why": "The **60/90** ratio is mechanic 2's entire reason to exist. With "
               "both bands equal, a pointer sitting exactly on the threshold "
               "latches and unlatches on consecutive frames - which is the chatter "
               "the reference's two numbers prevent, and the only thing that "
               "distinguishes a hysteresis from a threshold.",
        "expect": [
            "test_the_two_thresholds_are_different",
            "test_a_pointer_hovering_on_the_threshold_stays_latched",
        ],
    },
    {
        "label": "the out-band is tighter than the in-band, so a catch is instantly dropped",
        "file": _DRAG,
        "old": "LOCK_OUT_RATIO_X = 90.0 / 640.0",
        "new": "LOCK_OUT_RATIO_X = 30.0 / 640.0",
        "why": "The **over-fix in the other direction**, and it looks like a bug fix: "
               "'a wider band is too forgiving' is a reasonable objection to 90/640. "
               "But a band narrower than the in-band means anything that latched "
               "unlatches on the very next frame, so a catch lasts one frame and "
               "the hop fires repeatedly. Both directions are mutated because the "
               "ratio is a two-sided property.",
        "expect": [
            "test_the_two_thresholds_are_different",
            "test_a_held_drag_latches_when_the_pointer_settles_near_the_character",
        ],
    },

    # --- the speed guard, and its one deliberate half --------------------------
    {
        "label": "the speed guard is gone, so a sweep across latches the character",
        "file": _DRAG,
        "old": "            fast_enough = self._speed < LOCK_SPEED_RATIO * self.stage_w",
        "new": "            fast_enough = True",
        "why": "`180/640` px/s is what makes 'passing through' different from 'being "
               "caught'. Without it a drag that crosses the mascot in one frame "
               "latches it, so the character is caught by every gesture that "
               "happens to cross it - which is most of them. Measured at the "
               "shipped ratio: a 59.6 px/s pointer latches, a fast one does not.",
        "expect": [
            "test_a_sweep_that_ends_on_the_character_does_not_latch",
        ],
    },
    {
        "label": "the unlatch also consults the speed, so a pointer leaving at speed drops the catch",
        "file": _DRAG,
        "old": "            if self._locked and (abs(dx) > out_x or abs(dy) > out_y):\n"
               "                self._locked = False",
        "new": "            if self._locked and (abs(dx) > out_x or abs(dy) > out_y\n"
               "                               or not fast_enough):\n"
               "                self._locked = False",
        "why": "**The plausible wrong repair, and the comment above it names why it "
               "is wrong**: *'a fast sweep must not *un*-lock either: the reference "
               "only breaks the latch on distance, and a pointer leaving at speed "
               "would otherwise unlatch on the way past and hop again'*. The "
               "mutation adds the speed condition to the unlatch, so a caught "
               "character is dropped the instant the pointer accelerates away - "
               "and then hops, which is the exact artefact the guard prevents.",
        "expect": [
            "test_a_pointer_hovering_on_the_threshold_stays_latched",
            "test_a_locked_drag_hops",
        ],
    },

    # --- the follow, and its two bounds ----------------------------------------
    {
        "label": "the follow bound is dropped, so the character can be dragged off the stage",
        "file": _DRAG,
        "old": "            target = max(self._follow_min, min(self._follow_max, self._cursor_x))",
        "new": "            target = self._cursor_x",
        "why": "The clamp is what keeps the character inside the window. Without it "
               "the pointer drives it past both edges, and since the stage **is** "
               "the window (the wiring grows the window to `SIZE + 2*TRAVEL` and "
               "the sprite travels inside it), a character outside the stage is "
               "outside the window - clipped away, with nothing reported. The "
               "property is measured as travel being bounded, not as a clamp "
               "existing.",
        "expect": [
            "test_leaving_the_outer_band_unlatches_and_stays_unlatched",
        ],
    },
    {
        "label": "the follow is instant rather than sprung",
        "file": _DRAG,
        "old": "            self._bx.step(target, response, damping, dt)",
        "new": "            self._bx.step(target, 0.001, damping, dt)",
        "why": "The *lag* is the mechanic. A response of 1 ms pins the character to "
               "the cursor, so it never trails - and 'it follows the pointer' is "
               "still true, which is why the follow test measures **distance to the "
               "cursor over time** rather than asserting that the character arrived.",
        "expect": [
            "test_the_character_chases_the_cursor_within_the_travel",
        ],
    },

    # --- tilt: clamped, smoothed, and the scale -------------------------------
    {
        "label": "the tilt clamp is gone, so a fast slide leans the character past upright",
        "file": _DRAG,
        "old": "            tilt_target = max(-TILT_CLAMP, min(TILT_CLAMP, self._bx.vel * TILT_GAIN))",
        "new": "            tilt_target = self._bx.vel * TILT_GAIN",
        "why": "The clamp is the difference between 'leaning into the drag' and "
               "'a sprite lying on its side'. The art's own bound is the same "
               "number (`_TILT_MAX = 0.18`, asserted equal to `TILT_CLAMP`), so "
               "without the clamp the engine asks the art for a tilt the art "
               "silently refuses - and a channel that quietly refuses its input is "
               "the shape this file records most often.",
        "expect": [
            "test_the_tilt_is_clamped",
        ],
    },
    {
        "label": "the tilt smoothing is dropped, so the lean tracks velocity without inertia",
        "file": _DRAG,
        "old": "        self._tilt = lerp(self._tilt, tilt_target, 1.0 - TILT_SMOOTH_BASE ** dt)",
        "new": "        self._tilt = tilt_target",
        "why": "`1 - 0.0005^dt` is a frame-rate-independent exponential, and "
               "dropping it makes the tilt *snap* to the current velocity. The "
               "clamp still holds, so a tilt-shape assertion passes and a person "
               "watching sees the lean change instantly on every mouse event - "
               "which is the difference between a mascot and a cursor decoration.",
        "expect": [
            "test_the_tilt_lags_the_velocity_instead_of_snapping_to_it",
        ],
    },

    # --- the hop ---------------------------------------------------------------
    {
        "label": "the hop amplitude is a ratio of the stage rather than the character",
        "file": _DRAG,
        "old": "HOP_PX_RATIO = 5.0 / 62.0",
        "new": "HOP_PX_RATIO = 5.0 / 640.0",
        "why": "**The `62`-vs-stage decision, the wrong way.** 5/640 * a 212px "
               "stage is 1.66px against 5/62 * 132 = **10.65px** measured - so the "
               "hop becomes invisible rather than absent, and a test that asserts "
               "'the character rose' still passes. The constant table in the module "
               "is explicit that the hop is a ratio of the *character* and the lock "
               "band of the *surface*; this mutation moves it across that line.",
        "expect": [
            "test_the_hop_is_the_declared_share_of_the_character",
        ],
    },
    {
        "label": "the hop window is the full catch rather than 0.15 s",
        "file": _DRAG,
        "old": "HOP_SECONDS = 0.15",
        "new": "HOP_SECONDS = 1.5",
        "why": "The reference's 150 ms catch-hop. At 1.5 s the hop lasts ten times "
               "as long, so a catch reads as a slow drift upward rather than a "
               "single flick - and because it is a `sin`, a longer window also "
               "means the character is *still rising* when a person expects it "
               "settled, which moves the settle timing every other measurement "
               "reads.",
        "expect": [
            "test_the_hop_is_over_and_done_within_its_declared_window",
        ],
    },

    # --- the two lifecycle anchors ---------------------------------------------
    {
        "label": "the pre-release timeline is measured from the drop, so it jumps by the drag's length",
        "file": _DRAG,
        "old": "        if self._drop_wall is not None:\n"
               "            return T_DROP + max(0.0, now - self._drop_wall)\n"
               "        return ENTRY_T_REF + max(0.0, now - self._entry_wall)",
        "new": "        if self._drop_wall is not None:\n"
               "            return T_DROP + max(0.0, now - self._entry_wall)\n"
               "        return T_DROP + max(0.0, now - self._entry_wall)",
        "why": "The common version of this bug: **one anchor, plus 'restart `_t` on "
               "release'**. `self._t = T_DROP` is set in `release()`, so the phase "
               "clock jumps forward by however long the drag lasted on the first "
               "frame after it ends - and that single frame is what decides whether "
               "the work timeline is seen at all. A drag of 3 s lands the first "
               "post-release frame past `T_SUCK_END`, so the catch is never drawn.",
        "expect": [
            "test_the_phase_clock_has_two_anchors_and_reads_whichever_is_live",
            "test_the_timeline_reaches_a_full_progress_bar",
        ],
    },
    {
        "label": "both anchors read the entry, so a drag-free work timeline never starts",
        "file": _DRAG,
        "old": "        if self._drop_wall is not None:\n"
               "            return T_DROP + max(0.0, now - self._drop_wall)\n"
               "        return ENTRY_T_REF + max(0.0, now - self._entry_wall)",
        "new": "        if self._drop_wall is not None:\n"
               "            return ENTRY_T_REF + max(0.0, now - self._drop_wall)\n"
               "        return ENTRY_T_REF + max(0.0, now - self._entry_wall)",
        "why": "**The over-fix in the other direction**, and it is the mirror image: "
               "now the post-drop timeline runs from `ENTRY_T_REF = 1.55` instead "
               "of `T_DROP = 1.95`, so every post-drop phase boundary arrives 0.4 s "
               "early and the whole timeline is 0.4 s short. One anchor cannot be "
               "right for both phases, which is why this property is two "
               "mutations rather than one.",
        "expect": [
            "test_the_phase_clock_has_two_anchors_and_reads_whichever_is_live",
        ],
    },

    # --- the work timeline's own landmarks -------------------------------------
    {
        "label": "the sprite stays painted through the bar, so it is visible when it is not",
        "file": _DRAG,
        "old": "        sprite_visible = pt < T_SUCK_END or pt >= grow_start",
        "new": "        sprite_visible = pt >= grow_start",
        "why": "**The shipped defect, restored verbatim.** `pt >= grow_start` "
               "alone reads correct - the sprite *is* visible from the grow - and "
               "the omission is the other half of the condition: through the whole "
               "suck-and-bar window the reference hides the character because it is "
               "inside the tray, and this leaves it painted there. Measured: "
               "invisible from `t = 2.33`, visible again from `grow_start = 6.50` "
               "for a 3 s work.",
        "expect": [
            "test_the_sprite_is_hidden_through_the_bar_and_not_only_at_the_grow",
        ],
    },
    {
        "label": "the progress curve's middle is a straight line rather than an unhurried drift",
        "file": _DRAG,
        "old": "        return 0.6 + 0.32 * e_in_out((u - 0.4) / 0.45)",
        "new": "        return 0.6 + 0.32 * ((u - 0.4) / 0.45)",
        "why": "'Quick to 60%, an unhurried middle, then a last push' is the "
               "reference's own sentence, and the middle is the part that says "
               "*unhurried*: `e_in_out` leaves the curve almost flat through the "
               "middle and spends the movement at the two ends. Linear through the "
               "same window is monotone, starts at exactly 0.6 and ends at exactly "
               "0.92, and reads as a bar that creeps evenly. The monotony test "
               "cannot tell them apart, so the witness has to sample the middle.",
        "expect": [
            "test_the_middle_branch_is_flat_before_it_moves",
        ],
    },
    {
        "label": "the last push is given to the first phase, so the bar jumps then stalls",
        "file": _DRAG,
        "old": "    if u < 0.4:\n        return 0.6 * e_out(u / 0.4)",
        "new": "    if u < 0.85:\n        return 0.92 * e_out(u / 0.85)",
        "why": "The three branches are 'kept as three branches because the shape of "
               "the middle is the whole point' - so **removing the last branch** is "
               "not a simplification, it is a different curve: 60% arrives at 36% "
               "of the work instead of 40%, and the final 8% push the reference has "
               "is spent on the first 36%. A curve with two branches and no "
               "documented reason is the `wpctl`-three-copies shape with one copy "
               "missing.",
        "expect": [
            "test_the_three_phases_take_the_share_the_claim_names",
            "test_the_middle_is_the_unhurried_one",
        ],
    },

    # --- `move`'s own guard ---------------------------------------------------
    {
        "label": "the `dt > 0.001` guard is gone, so two events in one millisecond read as infinite speed",
        "file": _DRAG,
        "old": "        if dt > 0.001:\n"
               "            self._speed = math.hypot(x - self._prev_x, y - self._prev_y) / dt",
        "new": "        self._speed = math.hypot(x - self._prev_x, y - self._prev_y) / dt",
        "why": "The reference's own guard, and it exists because of the speed guard "
               "one constant away: `dt ~ 0` makes `_speed` enormous, which is "
               "*infinitely fast*, which does **not** latch - except that an exact "
               "`dt == 0` divides by zero outright. A test that moves the pointer "
               "once per frame never reaches `dt < 0.001`, so this is invisible "
               "until two pointer events land inside a millisecond, which is what a "
               "real drag does on a 1 kHz mouse.",
        "expect": [
            "test_two_moves_inside_the_same_millisecond_do_not_divide_by_nothing",
        ],
    },

    # --- the rest pose, which every reset returns ------------------------------
    {
        "label": "the rest radius is the diameter, so the idle mouth rect is twice as wide",
        "file": _DRAG,
        "old": "    r = d / 2.0",
        "new": "    r = d",
        "why": "The rest pose is **one object** returned by every reset and by "
               "`stop()`, which is what lets a test assert equality instead of "
               "comparing twenty fields - and it is also why a wrong radius is easy: "
               "`x`, `y` and `d` are all still correct, and only `mouth_rect` - the "
               "one channel an idle mascot never draws, because `mouth=0.0` at rest "
               "- carries the error. A channel whose only consumer is inactive code "
               "is the shape this file records for a guard nothing exercises.",
        "expect": [
            "test_the_rest_mouth_rect_is_the_characters_own_size",
        ],
    },
]

MUTATIONS = _MUTATIONS