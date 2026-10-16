"""The mascot's motion engine - follow, lock, tilt, hop, mouth, work timeline.

Ported from `harness-study/coucou/windows/src/upload/sequence.ts`. There is **no
GTK import here and no widget is constructed**, so every mechanic below is
exercised on a machine with no display - which matters, because
`mascot.available()` is **False** on any compositor that does not speak
`zwlr_layer_shell_v1` (Mutter never implemented it), so the composited result
cannot be seen on a GNOME box at all. These tests are the only place the
arithmetic is observed.

**Everything runs on a supplied clock** (`now=`), never on `time.monotonic`, so
the whole timeline is reproducible: a test can hold the mascot at an exact
second and assert what it drew there, rather than sampling a loop and hoping.

The helpers a companion needs are here too: `frame()` answers every channel the
window applies (position, size, gaze, mouth, tilt), so a wiring change cannot
drop one and leave the rest green.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import mascot_drag as M  # noqa: E402


# A stage bigger than the sprite, so there is somewhere to travel - a 132px
# mascot in a 132px window cannot move, which is the constructor's own refusal.
SIZE = 132
TRAVEL = 40
STAGE = SIZE + 2 * TRAVEL   # 212


class Clock:
    """A hand-cranked clock. `advance()` is the only way time moves."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


def engine(stage_w: int = STAGE, stage_h: int = STAGE, size: int = SIZE):
    """A fresh engine on a fresh clock; returns `(engine, clock)`."""
    clock = Clock()
    return M.MascotMotion(stage_w, stage_h, size, now=clock), clock


def run_drag(eng, clock, points, step=1 / 60):
    """Drive a whole grab/move/release through `points` at a fixed step."""
    eng.grab(*points[0])
    for x, y in points[1:]:
        clock.advance(step)
        eng.move(x, y)
        eng.frame()
    return eng


def settle(eng, clock, frames=30):
    """Hold the pointer on the character until it has latched. Returns the lock.

    Every lock test needs the character *already caught* before it moves the
    pointer somewhere else, and the latch takes a few frames to happen because
    the first `move` is measured against a spring that has not travelled yet.
    Without this helper each test re-derives it, and a test that has to reach
    its own precondition is a test whose failure can be its own setup.
    """
    eng.move(eng._rest_x, eng._rest_y)
    for _ in range(frames):
        clock.advance(1 / 60)
        eng.move(eng._rest_x, eng._rest_y)
        eng.frame()
    return eng.locked


# ── The easing vocabulary, pinned against the reference's values ─────────────

class TestTheEases:
    @pytest.mark.parametrize("t,expected", [
        (0.0, 0.0), (0.5, 0.875), (1.0, 1.0),
    ])
    def test_out_is_the_reference_cubic_out(self, t, expected):
        # 1-(1-t)^3 at 0.5 is 0.875. The exact number is the point: a subtly
        # different ease still *looks* fine and is the wrong port.
        assert M.e_out(t) == pytest.approx(expected)

    def test_in_is_the_mirror_of_out(self):
        for t in (0.0, 0.25, 0.5, 0.9, 1.0):
            assert M.e_in(t) == pytest.approx(1.0 - M.e_out(1.0 - t))

    def test_in_out_is_symmetric_about_the_middle(self):
        for t in (0.1, 0.3, 0.5):
            assert M.e_in_out(t) == pytest.approx(1.0 - M.e_in_out(1.0 - t))
        assert M.e_in_out(0.5) == pytest.approx(0.5)

    def test_back_starts_at_zero_not_negative(self):
        # The reference's own `easeOutBack` returns 0 at t=0 (it is authored for
        # a pop-in, not a classic `easeInBack` which would start negative). A
        # value that starts negative would drag the first frame backwards.
        assert M.e_back(0.0) >= 0.0
        assert M.e_back(0.0) == pytest.approx(0.0, abs=1e-9)

    def test_back_overshoots_before_it_settles(self):
        # The overshoot is the whole point of `back`; a monotone ease here means
        # the morph pop has been flattened into a fade.
        assert max(M.e_back(i / 40) for i in range(41)) > 1.0

    def test_back_uses_the_reference_constant_not_the_rounded_one(self):
        # `upload/sequence.ts` carries c1 = 1.70158; `core/anim.ts` carries the
        # original's 1.7. The harvest's finding is that they disagree. This port
        # took sequence.ts's number, so e_back(0.5) is measured, not asserted
        # against a formula that would accept either.
        assert M.e_back(0.5) == pytest.approx(1.0877, abs=1e-3)


class TestSegAndLerp:
    def test_seg_clamps_to_the_window(self):
        assert M.seg(5, 0, 10) == pytest.approx(0.5)
        assert M.seg(-3, 0, 10) == 0.0
        assert M.seg(99, 0, 10) == 1.0

    def test_seg_of_a_zero_length_window_is_a_step_not_a_division(self):
        # A zero-length upload collapses two later phases onto one; `b == a`
        # would divide by zero and take the window down from a draw tick. The
        # documented reading is a step *at* `a`: before it is 0, from it on is 1.
        assert M.seg(4.9, 5, 5) == 0.0
        assert M.seg(5.0, 5, 5) == 1.0
        assert M.seg(5.1, 5, 5) == 1.0

    def test_lerp_interpolates(self):
        assert M.lerp(10, 20, 0.0) == pytest.approx(10)
        assert M.lerp(10, 20, 1.0) == pytest.approx(20)
        assert M.lerp(10, 20, 0.5) == pytest.approx(15)


class TestTheProgressCurve:
    def test_it_runs_from_zero_to_one(self):
        assert M.upload_progress_curve(0.0) == pytest.approx(0.0, abs=0.01)
        assert M.upload_progress_curve(1.0) == pytest.approx(1.0, abs=0.01)

    def test_it_is_monotone(self):
        # "Quick to 60%, an unhurried middle, then a last push" is only a claim
        # about progress if it never goes backwards.
        values = [M.upload_progress_curve(i / 50) for i in range(51)]
        assert all(b >= a - 1e-9 for a, b in zip(values, values[1:]))

    def test_the_three_phases_take_the_share_the_claim_names(self):
        """**The magnitudes, because monotonicity cannot see a different curve.**

        The reference's sentence is *three claims about how much each phase
        moves*, and the module's own note says the three branches are kept as
        three branches because the shape of the middle is the whole point. A
        monotone curve with the wrong shares is still a monotone curve.

        Measured on the shipped curve: `f(0.4) == 0.6000` exactly, and the
        phase gains are **0.600 / 0.320 / 0.080**.

        | mutation | what it does to these |
        |---|---|
        | the last push folded into the first branch | `f(0.4)` becomes **0.769**, so the 60% landmark fails |
        | the middle made linear | **nothing here** - and that is the point of `test_the_middle_branch_is_flat_before_it_moves`, below |

        The second row is why this class needed a second test rather than a
        stronger version of this one: replacing `e_in_out` with a straight line
        leaves both endpoints and the whole middle total at 0.32, so *no*
        assertion about totals, rates or monotonicity can see it. It is caught
        only by sampling inside the middle, where `e_in_out` is flat at its ends.
        """
        f = M.upload_progress_curve
        assert f(0.4) == pytest.approx(0.60, abs=1e-9), "60% at 40% of the work"
        assert f(0.85) == pytest.approx(0.92, abs=1e-9), "92% at 85% of the work"
        first = f(0.4) - f(0.0)
        middle = f(0.85) - f(0.4)
        last = f(1.0) - f(0.85)
        assert (first, middle, last) == pytest.approx((0.60, 0.32, 0.08), abs=1e-6)

    def test_the_middle_is_the_unhurried_one(self):
        """The half of the sentence that says *middle*, as a rate rather than a
        total. Per unit of work: the first 40% moves 1.50, the middle 45% moves
        0.71, the last 15% moves 0.53."""
        f = M.upload_progress_curve
        rate_first = f(0.4) / 0.4
        rate_middle = (f(0.85) - f(0.4)) / 0.45
        rate_last = (f(1.0) - f(0.85)) / 0.15
        assert rate_first > rate_middle > rate_last
        assert rate_first == pytest.approx(1.50, abs=0.01)
        assert rate_last == pytest.approx(0.53, abs=0.02)

    def test_the_middle_branch_is_flat_before_it_moves(self):
        """**The only assertion that can see a linear middle, and it has to go
        inside the branch.**

        Swapping `e_in_out` for a straight line over `0.4..0.85` keeps every
        landmark: `f(0.4)` is still exactly 0.6000, `f(0.85)` still 0.9200, the
        middle total still 0.320, the curve still monotone and still slower per
        unit than the first phase. Every test in this class passed against it -
        which is what "the shape of the middle is the whole point" means
        literally, and why a comment alone cannot carry the property.

        What `e_in_out` does that a line does not is **sit flat at both ends and
        spend its movement in the middle**. Measured, the gain per 0.05 of `u`
        across the branch:

        | u | 0.45 | 0.50 | 0.55 | 0.60 | **0.65** | 0.70 | 0.75 | 0.80 | 0.85 |
        |---|---|---|---|---|---|---|---|---|---|
        | gain | .0018 | .0123 | .0334 | .0650 | **.0953** | .0650 | .0334 | .0123 | .0018 |

        Symmetric about the peak at `u = 0.65`, and the first step is **0.0018
        against a linear 0.0356** - twenty times flatter. So the test asserts the
        ramp is flat where a line is not, and that it is flat at *both* ends, so
        a curve that is symmetric in neither direction cannot pass either.
        """
        f = M.upload_progress_curve
        steps = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
        gains = [f(b) - f(a) for a, b in zip([0.40] + steps[:-1], steps)]
        linear = 0.32 * (0.05 / 0.45)

        # Twenty times flatter than a line at the start of the branch.
        assert gains[0] < linear / 10, (
            f"the branch does not start flat: {gains[0]:.5f} vs linear "
            f"{linear:.5f}")
        # And flat at the end too, so neither direction can be dropped alone.
        assert gains[-1] == pytest.approx(gains[0], abs=1e-9), (
            f"the branch is flat at one end only: {gains[0]:.5f} vs "
            f"{gains[-1]:.5f}")
        # Rising then falling, peaking in the middle of the branch.
        assert all(b > a for a, b in zip(gains, gains[1:5])), gains
        assert all(b < a for a, b in zip(gains[4:], gains[5:])), gains
        assert gains[4] == max(gains), gains
        # The measured peak, so a re-tuned easing that keeps every ordering
        # property above but flattens the whole branch is still caught.
        assert gains[4] == pytest.approx(0.0953, abs=0.001)


# ── Construction ─────────────────────────────────────────────────────────────

class TestConstruction:
    def test_a_stage_that_cannot_hold_the_sprite_is_refused(self):
        # The refusal carries the reason, not a bare ValueError.
        with pytest.raises(ValueError, match="travel"):
            M.MascotMotion(SIZE, SIZE, SIZE)

    def test_a_stage_exactly_the_sprite_size_is_refused_too(self):
        # **This was a live defect, found by this test.** The guard read
        # `stage_w < size`, which permits `stage == size` - and a stage the
        # sprite's own size leaves zero travel, because `_follow_min` and
        # `_follow_max` both land on `size / 2`. The module's own words are
        # "every mechanic here is travel", so the class accepted a configuration
        # in which every mechanic is inert. The guard is now `<=`.
        # The premise is stated as arithmetic rather than measured by
        # constructing the refused case, because the case this test is about no
        # longer constructs at all - that is the fix.
        assert SIZE / 2 == SIZE - SIZE / 2, "the premise: the bands coincide"
        with pytest.raises(ValueError, match="travel"):
            M.MascotMotion(SIZE, SIZE, SIZE)

    def test_a_stage_that_cannot_hold_the_sprite_at_all_is_refused_too(self):
        with pytest.raises(ValueError, match="travel"):
            M.MascotMotion(SIZE - 1, SIZE - 1, SIZE)

    def test_the_smallest_legal_stage_has_exactly_one_pixel_of_travel(self):
        # `_follow_min` and `_follow_max` are the module's own bounds; their
        # difference is exactly "how far the character can travel", so the test
        # reads the span off the code rather than restating the arithmetic.
        eng = M.MascotMotion(SIZE + 1, SIZE + 1, SIZE)
        assert eng._follow_max - eng._follow_min == pytest.approx(1.0)

    def test_a_frozen_clock_produces_no_motion_at_all(self):
        # `frame()` integrates forward to the clock and stops; with the clock
        # stopped there is nothing to integrate. This is what makes every other
        # test here deterministic, and it is also why a test that calls `frame()`
        # in a loop without advancing the clock is measuring nothing - measured,
        # after two tests here did exactly that and read it as a dead latch.
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        first = eng.frame()
        for _ in range(50):
            assert eng.frame() == first
        assert clock.t == 0.0

    def test_it_starts_at_rest(self):
        eng, _ = engine()
        frame = eng.frame()
        assert frame.sprite_visible
        assert frame.d == pytest.approx(SIZE)
        assert not eng.held and not eng.locked and not eng.working


# ── The drag: follow, lock hysteresis, tilt, hop, mouth ────────────────────

class TestTheDragLifecycle:
    def test_grab_activates_and_holds(self):
        eng, _ = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        assert eng.active and eng.held
        assert not eng.locked and not eng.working

    def test_release_lets_go_and_ends_the_session(self):
        eng, _ = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        eng.release()
        assert not eng.held
        # After release the timeline has not begun (no work_seconds), so the frame
        # returns to the rest pose.
        eng.frame()
        assert eng.frame().d == pytest.approx(SIZE)

    def test_a_release_with_work_seconds_starts_the_work_timeline(self):
        eng, _ = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        eng.release(work_seconds=3.0)
        assert not eng.held and eng.working

    def test_the_phase_clock_has_two_anchors_and_reads_whichever_is_live(self):
        """**The defect this property exists for, and it is a single frame.**

        `release()` sets `self._t = T_DROP`, so if `_t_ref` measured from the
        *entry* all the way through, the phase clock would jump forward by
        however long the drag lasted on the first frame after the release - and
        that frame is what decides whether the work timeline is seen at all. A
        3 s drag lands it past `T_SUCK_END`, which is the end of the catch.

        Measured on the shipped code, after a 1 s drag:

        | | reads | correct |
        |---|---|---|
        | before the release | 2.5500 | `ENTRY_T_REF + 1.0` |
        | after it | **1.9667** | `T_DROP + 1/60` |

        Reading the entry instead gives 2.9500; reading the drop throughout gives
        1.5667. Both are caught, so the property is two-sided rather than a
        preference for one anchor.
        """
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        for _ in range(60):
            clock.advance(1 / 60)
            eng.move(STAGE / 2, STAGE / 2)
            eng.frame()
        assert clock.t == pytest.approx(1.0)
        assert eng._t_ref() == pytest.approx(M.ENTRY_T_REF + clock.t, abs=1e-9)

        eng.release(work_seconds=1.0)
        clock.advance(1 / 60)
        assert eng._t_ref() == pytest.approx(M.T_DROP + 1 / 60, abs=1e-9), (
            "the post-drop timeline is measured from the drop, not the entry")
        assert eng._t_ref() < eng.grow_start, (
            "and it must not have jumped past the phases the drag already consumed")

    def test_move_only_counts_while_held(self):
        # A stray move after release must not resurrect the session.
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        eng.release()
        clock.advance(0.1)
        eng.move(STAGE, STAGE)
        assert not eng.held

    def test_two_moves_inside_the_same_millisecond_do_not_divide_by_nothing(self):
        """**The reference's `dt > 0.001` guard, and what it actually does.**

        My first version of this test asserted the guard *raises* the speed
        above the sweep threshold, on the reasoning that `dt ~ 0` makes the
        velocity enormous. Measured, the guard does the **opposite**: it skips
        the assignment, so `_speed` keeps its previous value. Measured on the
        shipped code: settled 0.0, still **0.0** after a 0.2 ms move covering
        40px, and 0.1 px/s after the same move given 4 ms.

        So the guard's purpose is to keep `_speed` a *measurement* rather than a
        division of two near-zero numbers - and an exact `dt == 0`, which the
        guard does not exclude, is a `ZeroDivisionError` out of a `GLib` pointer
        callback, where it aborts the process rather than reaching a test. Both
        directions are asserted here: the near-zero case leaves the reading
        alone, and the exact case does not raise.
        """
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        clock.advance(1 / 60)
        eng.move(STAGE / 2, STAGE / 2)
        eng.frame()
        assert eng.locked, "the premise: it was caught"
        assert eng._speed == pytest.approx(0.0)

        clock.advance(0.0002)                    # 0.2 ms: below the guard
        eng.move(eng._bx.v + 40.0, STAGE / 2)     # 40 px in that time
        eng.frame()                               # must not raise
        assert eng._speed == pytest.approx(0.0), (
            "below the guard the reading is left alone, not replaced by a "
            "quotient of two near-zero numbers")

        # An exact repeat is the degenerate case: no time passed at all.
        clock2 = Clock()
        other = M.MascotMotion(STAGE, STAGE, SIZE, now=clock2)
        other.grab(STAGE / 2, STAGE / 2)
        clock2.advance(1 / 60)
        other.move(STAGE / 2, STAGE / 2)
        other.frame()
        other.move(STAGE, STAGE / 2)              # dt == 0
        other.frame()                             # must not raise
        assert other._speed == pytest.approx(0.0)


class TestTheFollow:
    def test_a_held_drag_latches_when_the_pointer_settles_near_the_character(self):
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        clock.advance(1 / 60)
        eng.move(STAGE / 2, STAGE / 2)     # pointer still
        eng.frame()
        assert eng.locked

    def test_a_fast_sweep_across_does_not_latch(self):
        # The `speed < 180` guard is the difference between "picked up" and
        # "passed over". A fast sweep must leave the character unlatched.
        eng, clock = engine()
        eng.grab(10, STAGE / 2)
        for i in range(1, 40):
            clock.advance(1 / 240)           # a very fast move
            eng.move(10 + i * 8, STAGE / 2)
            eng.frame()
        assert not eng.locked

    def test_a_sweep_that_ends_on_the_character_does_not_latch(self):
        """**The speed guard's own case, and the first version of this test could
        not measure it.**

        A sweep that crosses the stage in 8px steps passes *every* distance
        check or it does not - the character springs a pixel or two a frame while
        the pointer jumps eight, so `dx` is never inside the band at the instant
        it is sampled, and the test passes with the guard deleted.

        The reference's case is a sweep that **arrives** on the character: the
        pointer is now close, and the only thing that can refuse the latch is the
        speed. Measured: one move of 1/240 s covering 96px reads **23040 px/s**
        against a threshold of `180/640 * 212 = 59.6`, and the character stays
        unlatched; the same arrival spread over 1/60 s reads 0.0 and latches.
        """
        eng, clock = engine()
        eng.grab(10, STAGE / 2)
        clock.advance(1 / 240)
        eng.move(eng._rest_x, eng._rest_y)     # arrived, fast
        eng.frame()
        assert eng._speed > M.LOCK_SPEED_RATIO * STAGE, "the premise: it was fast"
        assert not eng.locked, "a fast arrival must not latch"

        slow, clock2 = engine()
        slow.grab(10, STAGE / 2)
        for _ in range(5):
            clock2.advance(1 / 60)
            slow.move(slow._rest_x, slow._rest_y)
            slow.frame()
        assert slow.locked, "the same arrival, unhurried, latches"

    def test_the_character_chases_the_cursor_within_the_travel(self):
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        clock.advance(1 / 60)
        eng.move(STAGE - 5, STAGE / 2)      # drag hard right
        eng.frame()
        pose = eng.frame()
        # It moved toward the pointer, but never past the stage's follow bound.
        assert pose.x > STAGE / 2
        assert pose.x <= STAGE - SIZE / 2 + 1e-6


class TestTheLockHysteresis:
    def test_the_two_thresholds_are_different(self):
        # This is the entire mechanic: latch below `in`, unlatch only above
        # `out`, and `out > in`, so a pointer resting on the threshold does not
        # chatter. Equal thresholds would be no hysteresis at all.
        assert M.LOCK_OUT_RATIO_X > M.LOCK_IN_RATIO_X
        assert M.LOCK_OUT_RATIO_Y > M.LOCK_IN_RATIO_Y

    def test_each_axis_has_its_own_band_because_the_stage_is_not_square(self):
        """**The `62`-vs-stage decision, measured on both axes.**

        The reference's latch is `hypot(dx, dy) < 60` inside a **640x176**
        island, and one radius in a non-square stage is *two different
        tolerances*: 60 of 640 across is narrow, 60 of 176 down is wide. The
        module's constant table records what happens if that is collapsed onto one
        axis - the latch becomes unreachable or fires on frame 1 of every drag.

        Measured on this 212x212 stage: in-band **19.88px across** and
        **72.27px down**, out-band 29.81 and 108.41. So a pointer 40px above the
        character latches and the same 40px to the left does not - which is the
        property a single radius cannot have on a square stage.

        Both are asserted as measurements rather than as the constants, so a
        ratio changed to a different *plausible* pair is caught as well as one
        collapsed onto a single axis.
        """
        eng, clock = engine()
        assert M.LOCK_IN_RATIO_X * STAGE == pytest.approx(19.88, abs=0.01)
        assert M.LOCK_IN_RATIO_Y * STAGE == pytest.approx(72.27, abs=0.01)
        assert M.LOCK_OUT_RATIO_X * STAGE == pytest.approx(29.81, abs=0.01)
        assert M.LOCK_OUT_RATIO_Y * STAGE == pytest.approx(108.41, abs=0.01)

        # The premise the numbers above are for: 40px is inside the vertical
        # band and outside the horizontal one.
        offset = M.LOCK_Y_OFFSET_RATIO * STAGE
        # Bare call, result discarded: `settle` advances the clock 30 frames and
        # latches the character, and that is the whole point of it. Dropping the
        # *call* here would delete the setup this test's first assertion needs.
        settle(eng, clock)

        # 40px up: `cursorY + offset - by` is 40 - offset, well inside 72.27.
        eng.grab(eng._rest_x, eng._rest_y)
        assert settle(eng, clock)
        for _ in range(3):
            clock.advance(1 / 60)
            eng.move(eng._bx.v, (eng._by.v - offset) - 40.0)
            eng.frame()
        assert eng.locked, "40px up is inside this stage's vertical band"

        # 40px to the left: outside 19.88, so it must not latch.
        other, clock2 = engine()
        other.grab(other._rest_x, other._rest_y)
        assert settle(other, clock2)
        for _ in range(3):
            clock2.advance(1 / 60)
            other.move(other._bx.v - 40.0, (other._by.v - offset))
            other.frame()
        assert not other.locked, "40px across is outside this stage's x band"

    def test_a_pointer_hovering_on_the_threshold_stays_latched(self):
        # Sit between the two bands for many frames; a chattering latch would
        # flip `locked` an odd number of times here.
        #
        # The clock is advanced every frame and that is load-bearing: `frame()`
        # integrates forward to the clock and stops, so a frame on a frozen
        # clock does nothing at all. Two tests here read "the latch never fires"
        # from exactly that mistake before it was found.
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        for _ in range(20):                     # settle onto the character first
            clock.advance(1 / 60)
            eng.move(STAGE / 2, STAGE / 2)
            eng.frame()
        assert eng.locked, "a held, still pointer should latch"
        mid = STAGE / 2 + (M.LOCK_IN_RATIO_X + M.LOCK_OUT_RATIO_X) / 2 * STAGE
        flips = 0
        was = eng.locked
        for _ in range(120):
            clock.advance(1 / 60)
            eng.move(mid, STAGE / 2)
            eng.frame()
            if eng.locked != was:
                flips += 1
                was = eng.locked
        assert flips == 0, f"the lock chattered {flips} times on the threshold"

    def test_leaving_the_outer_band_unlatches_and_stays_unlatched(self):
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        for _ in range(20):
            clock.advance(1 / 60)
            eng.move(STAGE / 2, STAGE / 2)
            eng.frame()
        assert eng.locked
        # The pointer has to go past the **follow bound** and not merely past
        # the outer band. The character is clamped at `_follow_max`, so a
        # pointer just past `out_x` leaves it *inside* `in_x` and it re-latches
        # within a few frames - measured, and it is what made the first version
        # of this test fail: `far` landed 0.06px inside the re-latch band.
        eng._follow_max + M.LOCK_OUT_RATIO_X * STAGE + 40
        far = min(STAGE - 2, eng._follow_max + M.LOCK_IN_RATIO_X * STAGE + 10)
        for _ in range(30):
            clock.advance(1 / 60)
            eng.move(far, STAGE / 2)
            eng.frame()
        assert not eng.locked

    def test_the_follow_bound_makes_the_edge_of_the_stage_re_latch(self):
        # **A measured property, recorded because it is not obvious.** A pointer
        # just outside the outer band does unlatch - and then the character
        # chases it, stops at the follow bound, and is within the *inner* band
        # again, so it latches once more. So "drag it off the edge to let go"
        # does not work: there is a 9.9px strip at the far right of this stage
        # where the hysteresis inverts into chatter-by-travel.
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        for _ in range(20):
            clock.advance(1 / 60)
            eng.move(STAGE / 2, STAGE / 2)
            eng.frame()
        assert eng.locked

        unlatching = eng._follow_max + M.LOCK_OUT_RATIO_X * STAGE + 1
        relatching = eng._follow_max + M.LOCK_IN_RATIO_X * STAGE - 1
        assert unlatching > relatching, "the premise: the strips differ"
        # Unlatches at the first: the character is still near its resting spot,
        # so the pointer is far outside the outer band.
        for _ in range(3):
            clock.advance(1 / 60)
            eng.move(unlatching, STAGE / 2)
            eng.frame()
        assert not eng.locked
        # Re-latches at the second: the character chases and stops at the follow
        # bound, leaving the pointer *inside* the inner band.
        for _ in range(40):
            clock.advance(1 / 60)
            eng.move(relatching, STAGE / 2)
            eng.frame()
        assert eng.locked, "just outside the follow bound it re-latches"


class TestTheHop:
    def test_a_locked_drag_hops(self):
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        hops = []
        for _ in range(20):
            clock.advance(1 / 60)
            eng.move(STAGE / 2, STAGE / 2)
            hops.append(abs(eng.frame().hop))
        assert max(hops) > 0.0, "a caught character should hop"

    def test_the_hop_is_the_declared_share_of_the_character(self):
        """**The magnitude, because `> 0.0` cannot see the scale.**

        The module's constant table is explicit that the hop is a ratio of the
        *character* - 5px on a 62px character, 8% of its size - and that the lock
        band beside it is a ratio of the *stage*. The first version of this test
        asserted only that the character rose, so a hop scaled by the **stage**
        instead measured **1.66px against 10.65px** and passed.

        Measured at the shipped ratio: a peak of **10.6452px**, which is
        `5/62 * 132` to four decimals.
        """
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        peaks = []
        for _ in range(40):
            clock.advance(1 / 240)
            eng.move(STAGE / 2, STAGE / 2)
            peaks.append(abs(eng.frame().hop))
        measured = max(peaks)
        declared = M.HOP_PX_RATIO * SIZE
        assert measured == pytest.approx(declared, abs=0.01), (
            f"hop peaked at {measured:.4f}px, declared {declared:.4f}px")
        # And the ratio is of the character: a stage-relative 5px on this stage
        # would be 1.66px, so the measured 10.65 is 6.4x larger than the
        # mis-scaled reading - which is the mistake the constant table is about.
        assert declared == pytest.approx(10.6452, abs=0.001)
        assert (5.0 / 640.0) * STAGE == pytest.approx(1.6562, abs=0.001)
        assert measured > 6 * (5.0 / 640.0) * STAGE

    def test_the_hop_is_over_and_done_within_its_declared_window(self):
        """`HOP_SECONDS = 0.15` is the reference's 150 ms catch-hop. Measured: 35
        frames of 1/240 are non-zero, **0.1458 s** - so the window is a real
        bound on when the character has finished rising, and a ten-times-longer
        one still rises for every frame a short test looks at."""
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        hops = []
        for _ in range(int(M.HOP_SECONDS * 240) + 40):
            clock.advance(1 / 240)
            eng.move(STAGE / 2, STAGE / 2)
            hops.append(eng.frame().hop)
        nonzero = [i for i, h in enumerate(hops) if abs(h) > 1e-9]
        assert nonzero, "the premise: it hopped"
        assert nonzero[-1] < int(M.HOP_SECONDS * 240), (
            f"still moving {nonzero[-1] / 240.0:.3f}s in, past the "
            f"{M.HOP_SECONDS}s window")
        assert all(abs(hops[i]) == pytest.approx(0.0, abs=1e-9)
                   for i in range(int(M.HOP_SECONDS * 240), len(hops)))


class TestTheMouth:
    def test_the_mouth_opens_when_caught_and_closes_at_rest(self):
        eng, clock = engine()
        assert eng.frame().mouth == pytest.approx(0.0)
        eng.grab(STAGE / 2, STAGE / 2)
        opened = 0.0
        for _ in range(30):
            clock.advance(1 / 60)
            eng.move(STAGE / 2, STAGE / 2)
            opened = max(opened, eng.frame().mouth)
        assert opened > 0.0
        eng.stop()
        # stop() returns to rest; the mouth is shut.
        assert eng.frame().mouth == pytest.approx(0.0)


class TestTheTilt:
    def test_a_fast_movement_tilts_the_character(self):
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        tilts = []
        for i in range(1, 30):
            clock.advance(1 / 60)
            eng.move(STAGE / 2 + i * 4, STAGE / 2)   # moving right
            tilts.append(abs(eng.frame().tilt))
        assert max(tilts) > 0.0

    def test_the_tilt_is_clamped(self):
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        for i in range(1, 60):
            clock.advance(1 / 60)
            eng.move(STAGE / 2 + i * 40, STAGE / 2)   # absurdly fast
            assert abs(eng.frame().tilt) <= M.TILT_CLAMP + 1e-9

    def test_the_tilt_lags_the_velocity_instead_of_snapping_to_it(self):
        """`1 - 0.0005**dt` is a frame-rate-independent exponential, and dropping
        it makes the lean *snap*. The first version of this class asserted only
        `max(tilts) > 0.0`, so a tilt with no smoothing at all passed - and the
        clamp still held, so the clamp test passed too.

        What smoothing changes is the **rate**: at a constant 4px per 1/60 s the
        tilt must keep rising across frames while the velocity target is
        constant. Measured: 0.00217, 0.00856, 0.01981, 0.03565, 0.05283,
        0.06796, 0.08129 - strictly rising, and every one below the raw
        `bx.vel * TILT_GAIN` the same frame would read unsmoothed.
        """
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        tilts, raws = [], []
        for i in range(1, 9):
            clock.advance(1 / 60)
            eng.move(STAGE / 2 + i * 4, STAGE / 2)   # constant velocity
            frame = eng.frame()
            tilts.append(frame.tilt)
            # The velocity the gain would produce on this frame, with no easing.
            raws.append(max(-M.TILT_CLAMP,
                            min(M.TILT_CLAMP, eng._bx.vel * M.TILT_GAIN)))
        assert all(b > a for a, b in zip(tilts, tilts[1:])), (
            f"a constant velocity must not give a constant tilt: {tilts}")
        assert all(t < r for t, r in zip(tilts, raws)), (
            f"the tilt must lag its own target: {tilts} vs {raws}")


# ── The work timeline (a narrated turn) ─────────────────────────────────────

class TestTheWorkTimeline:
    def test_begin_work_starts_without_a_drag(self):
        eng, _ = engine()
        eng.begin_work(3.0)
        assert eng.active and eng.working and not eng.held
        assert eng.work_seconds == pytest.approx(3.0)

    def test_the_timeline_reaches_a_full_progress_bar(self):
        eng, clock = engine()
        eng.begin_work(3.0)
        peak = 0.0
        for _ in range(int((eng.grow_end + 1) * 30)):
            clock.advance(1 / 30)
            peak = max(peak, eng.frame().progress)
        assert peak == pytest.approx(1.0, abs=0.02)

    def test_the_character_returns_home_at_the_end(self):
        eng, clock = engine()
        eng.begin_work(2.0)
        for _ in range(int((eng.grow_end + 1.0) * 30)):
            clock.advance(1 / 30)
            eng.frame()
        pose = eng.frame()
        assert pose.d == pytest.approx(SIZE, abs=1.0)
        assert pose.x == pytest.approx(STAGE / 2, abs=2.0)

    def test_the_sprite_comes_back_after_the_bar(self):
        # The defect this module's own docstring records as *found by measuring*:
        # `sprite_visible` used to be `pt < T_SUCK_END` alone, so the character
        # never came back and stayed invisible for the whole grow-home phase.
        eng, clock = engine()
        eng.begin_work(1.0)
        visible_again = False
        for _ in range(int((eng.grow_end + 0.5) * 30)):
            clock.advance(1 / 30)
            if eng.frame().sprite_visible:
                visible_again = True
        assert visible_again, "the mascot stayed invisible for the whole timeline"

    def test_the_sprite_is_hidden_through_the_bar_and_not_only_at_the_grow(self):
        """**The other half of the condition, restored verbatim as a defect.**

        `sprite_visible = pt >= grow_start` on its own reads correct - the sprite
        *is* visible from the grow - and this test above passes with it, because
        it only asks whether it came back. What it omits is that the character is
        hidden through the **whole suck-and-bar window**, because it is inside
        the tray; the mutation paints it there for the entire bar.

        Measured on the shipped code: **131 of 233 frames invisible, the first at
        `t = 2.333`** - within one frame of `T_SUCK_END = 2.33`.
        """
        eng, clock = engine()
        eng.begin_work(1.0)
        invisible, first = 0, None
        total = int((eng.grow_end + 0.5) * 60)
        for _ in range(total):
            clock.advance(1 / 60)
            pose = eng.frame()
            if not pose.sprite_visible:
                invisible += 1
                if first is None:
                    first = pose.t
        assert invisible > 50, f"only {invisible}/{total} frames were hidden"
        assert first == pytest.approx(M.T_SUCK_END, abs=1 / 30), (
            f"first hidden at t={first}, expected {M.T_SUCK_END}")

    def test_stop_ends_the_timeline_and_returns_to_rest(self):
        eng, clock = engine()
        eng.begin_work(5.0)
        clock.advance(1.0)
        eng.frame()
        eng.stop()
        assert not eng.working
        rest = eng.frame()
        assert rest.sprite_visible
        assert rest.progress == pytest.approx(0.0)


# ── The rest pose ────────────────────────────────────────────────────────────

class TestTheRestPose:
    def test_rest_is_the_centre_of_the_stage_at_full_size(self):
        eng, _ = engine()
        rest = eng.frame()
        assert rest.x == pytest.approx(STAGE / 2)
        assert rest.y == pytest.approx(STAGE / 2)
        assert rest.d == pytest.approx(SIZE)

    def test_rest_matches_the_module_own_rest_pose(self):
        eng, _ = engine()
        assert eng.frame() == M._rest_pose(STAGE, STAGE, SIZE)

    def test_the_rest_mouth_rect_is_the_characters_own_size(self):
        """**Against a literal, because the test above compares the module with
        itself.**

        `eng.frame() == M._rest_pose(...)` is the right shape for a pose of twenty
        fields - it is why the module builds one object rather than twenty - but
        as a *witness* it re-derives the answer from the code under test, so a
        wrong radius in `_rest_pose` is wrong on both sides and the comparison
        still holds. That is the `42/87/70` mistake in a test: two
        representations of one thing compared as strings.

        `mouth_rect` is where a wrong radius lands, and it is a channel an idle
        mascot never draws - `mouth=0.0` at rest, so `_open_mouth` returns the
        empty string and the rect is never read. Measured: w **132.0**, h 0.0,
        x 40.0 - the sprite's own width, centred.
        """
        rect = engine()[0].frame().mouth_rect
        assert rect.w == pytest.approx(SIZE), "the rect is the character's width"
        assert rect.h == pytest.approx(0.0), "a closed mouth has no height"
        assert rect.x == pytest.approx(STAGE / 2 - SIZE / 2)
        assert rect.y == pytest.approx(STAGE / 2 - (SIZE / 2) * 0.97, abs=1e-9)


# ── The frame carries every channel a companion applies ─────────────────────

class TestTheFrameChannels:
    def test_a_fresh_frame_carries_every_channel_the_window_needs(self):
        eng, _ = engine()
        pose = eng.frame()
        for channel in ("x", "y", "d", "tilt", "mouth", "hop",
                        "look_x", "look_y", "sprite_visible",
                        "progress", "bar_alpha", "locked"):
            assert hasattr(pose, channel), f"the frame has no {channel}"

    def test_the_gaze_channels_are_in_range(self):
        eng, clock = engine()
        eng.grab(STAGE / 2, STAGE / 2)
        for _ in range(30):
            clock.advance(1 / 60)
            eng.move(STAGE / 2, STAGE / 2)
            pose = eng.frame()
            assert -1.0 <= pose.look_x <= 1.0
            assert -1.0 <= pose.look_y <= 1.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
