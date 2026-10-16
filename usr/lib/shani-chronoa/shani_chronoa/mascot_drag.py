"""The mascot's motion engine: follow, lock, tilt, hop, mouth, and a work timeline.

**Ported from** `harness-study/coucou/windows/src/upload/sequence.ts`, whose own
header states the contract this module keeps: *"the engine is pure arithmetic: it
owns no DOM and draws nothing. It takes the cursor and a drop time, and hands
`frame()` back everything the canvas needs for one frame."* There is no GTK import
here and no widget is constructed, so every mechanic below is exercised by
`tests/test_mascot_drag.py` on a machine with no display - which matters, because
`mascot.available()` is **False** on any compositor that does not speak
`zwlr_layer_shell_v1` (Mutter has never implemented it), so the composited result
cannot be seen here at all.

## What is a mechanic and what is a feature

The reference mixes seven small pieces of arithmetic with a choreography that
answers *"a file landed on the island"*. The arithmetic is the transferable half
and it is ported whole:

| ported | why it is a mechanic rather than a feature |
|---|---|
| spring follow | a character chasing a pointer needs a chase, not a jump |
| **lock hysteresis** (`LOCK_IN`/`LOCK_OUT`) | a single threshold chatters; 60 in / 90 out is what stops it |
| `speed < 180` guard | a fast sweep *through* the mascot is not the mascot being caught |
| stiffer spring when locked | the character stops trailing once it is caught |
| velocity tilt | the character leans into its own motion |
| the 150 ms catch-hop | the one discrete event the whole drag is built around |
| the mouth spring | the only channel that says anything without a face change |

`morph` (the pop), the zone wash, the text dim and `uploadProgressCurve` are
**named in the reference and ported whole** rather than skipped.

## What the post-drop timeline became, and why it is not a lie

The reference's timeline is a file upload: squeeze, chew, shrink to a bar, fill the
bar, flash, check, grow to a "choose where it goes" affordance. Chronoa has no file
drop, so **naming the result an upload would be the confident-wrong-answer shape
this repository records in a dozen other costumes.** The timeline is kept whole and
re-pointed at a thing that genuinely has a start, a duration and an end here:
**an assistant turn**. `begin_work(seconds)` is the mascot being told a turn started
and how long it is expected to take; the mascot becomes a progress bar and grows back
to rest when it is done.

Every keyframe survives that re-pointing unchanged, which is the reason it is a port
rather than an invention. Two names are ours and are marked where they are defined:

- `choose_alpha` in the reference is the character growing into a *"where should this
  go"* affordance. A mascot has nothing to choose, so ours is `home_alpha` - growing
  **home**, which is the same motion with an honest destination.
- `file_visible` is `sprite_visible`: the mascot is on screen until the progress bar
  replaces it.

## The coordinate space, and why it is the window

The reference works in *island points* against a 640x176 surface because its island
is a fixed top-edge overlay. A mascot is a **window**, so the stage here is the
window's own logical box and the character travels **inside it**.

That is a deliberate choice with a cost, recorded because the cost is real:

- **Stage-relative, not island-relative.** Every constant the reference writes as an
  island point is written here as a fraction of the sprite diameter `d`, or derived
  from the stage. Redrawing at another window size changes the motion's *scale* and
  not its *shape*, which is the property the reference's absolute constants lack.
- **The window has to be bigger than the sprite for any of this to read.** The
  reference's character travels 520 points inside a 640-point island; a mascot in a
  window exactly its own size cannot travel at all. So the window is
  `SIZE + 2 * TRAVEL` and the click-through area grows with it - GTK4 exposes **no
  input-region API** (`Widget.set_input_region`, `Gdk.Region` and
  `Gdk.Window.input_shape_*` are all absent, measured three ways), so a transparent
  margin is not click-through, it is a region that eats clicks. Measured cost is in
  `mascot.py`'s `_STAGE_TRAVEL` and the harvest's PART 7.

**One reference constant is deliberately not ported as arithmetic.**
`USC.FOLLOW_MIN`/`FOLLOW_MAX` are `CARD_X + 50` and `CARD_X + CARD_W - 50` - a card
*inset*, not a follow behaviour, and at this stage size `50/62 * d` exceeds the whole
travel so porting the ratio would pin the sprite off-stage. The behaviour that
survives is the one the names imply: **the sprite cannot leave the stage**, which is
`[d/2, width - d/2]` and is a real clamp with a real test.

**Also deliberately not ported:** `LOCK_Y_OFFSET`'s magic `14`, kept but scaled by
`d/62` so it means "the pointer sits a little below the character's centre" at any
size; and the `1e9`/`-9` sentinels, which become `None` here because a float
sentinel that has to be compared against is a sentinel that eventually gets printed.
"""

from __future__ import annotations

import math
import time
from typing import Callable, List, NamedTuple, Optional

# ── Easing ───────────────────────────────────────────────────────────────────
#
# Verbatim from `sequence.ts`, and *one* copy of it: the reference ships the same
# five curves twice (`core/anim.ts` and a self-contained `upload/sequence.ts`),
# which is this repository's own "three copies of `wpctl`" shape found in someone
# else's tree. One vocabulary here.


def e_out(t: float) -> float:
    """Ease out cubic. `1 - (1-t)^3`."""
    return 1.0 - (1.0 - t) ** 3


def e_in(t: float) -> float:
    """Ease in cubic. `t^3`."""
    return t * t * t


def e_in_out(t: float) -> float:
    """Ease in-out cubic."""
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def e_back(t: float) -> float:
    """Ease out back, `c1 = 1.70158`.

    **The reference's value, not the common `1.70158 -> 1.7` of the original.**
    `core/anim.ts` carries `c1 = 1.7` where `upload/sequence.ts` carries `1.70158`,
    and the difference is visible on the overshoot. Measured at `t = 0`: `e_back(0)`
    returns **`0.0`** here, not a negative number - see `test_the_eases_agree_with_
    the_reference` for the reading.
    """
    c1 = 1.70158
    c3 = c1 + 1.0
    u = t - 1.0
    return 1.0 + c3 * u * u * u + c1 * u * u


def seg(t: float, a: float, b: float) -> float:
    """`t` clamped to `0..1` across `[a, b]`. `b == a` reads as instant rather than
    dividing by zero, which is the only way this can be reached (`T_CHEW1` and
    `T_SUCK_END` are distinct in the reference, but a zero-length upload duration
    collapses two of the later ones and a `ZeroDivisionError` in a draw tick would
    take the window down)."""
    if b == a:
        return 0.0 if t < a else 1.0
    return max(0.0, min(1.0, (t - a) / (b - a)))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ── Springs ──────────────────────────────────────────────────────────────────

#: The reference's integration step, `1/240`. Fixed rather than frame-derived
#: because `simulate_to` sub-steps to it, which is what makes the motion identical
#: at 30fps and at 144fps.
DT = 1.0 / 240.0

#: Response / damping while chasing, and while caught. The caught spring is both
#: stiffer (0.18 vs 0.35 s) and better damped (0.75 vs 0.70), which is the whole of
#: "it stops trailing once you have hold of it".
RESPONSE_CHASE = 0.35
DAMPING_CHASE = 0.70
RESPONSE_LOCKED = 0.18
DAMPING_LOCKED = 0.75

#: The mouth is its own spring: a stiffer response and a heavier damping than
#: either position spring, because a mouth that overshoots reads as a twitch.
MOUTH_RESPONSE = 0.25
MOUTH_DAMPING = 0.6


class Spring:
    """The reference's `USSpring`, integrated by hand.

    Semi-implicit Euler on `a = k(target - v) - c*vel`, which is what the
    TypeScript does and what every one of its numbers is tuned against. A
    critically-damped analytic spring would be "better" and would change every
    constant, so the integrator is kept and only the surface is adapted.
    """

    __slots__ = ("v", "vel")

    def __init__(self, v: float = 0.0) -> None:
        self.v = v
        self.vel = 0.0

    def step(self, target: float, response: float, damping: float, dt: float) -> None:
        k = (2.0 * math.pi / response) ** 2
        c = 2.0 * damping * math.sqrt(k)
        a = k * (target - self.v) - c * self.vel
        self.vel += a * dt
        self.v += self.vel * dt

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Spring(v={self.v:.3f}, vel={self.vel:.3f})"


# ── Constants ────────────────────────────────────────────────────────────────
#
# **Two scales, and which one a number belongs to is a decision, not a default.**
#
# The reference writes every constant against *island points* in a 640x176 surface.
# There are two different things hiding in that, and porting them the same way is a
# defect this module shipped once and had measured out:
#
# * **What the character does to itself** - tilt, hop, mouth, squeeze, bar
#   thickness - is proportional to the *character*. Those are ratios of `d`.
# * **Where the pointer has to be to be caught** - the lock band, the speed that
#   counts as a sweep, the bearing the eyes lead - is proportional to the *surface*.
#   Those are ratios of the stage.
#
# Porting the lock band as a ratio of `d` gives `0.968 * 132 = 128px` inside a
# `172px` stage: the character latches from almost anywhere, so the **hysteresis
# becomes invisible** and mechanic 2 - the one mechanic with a distinct reason to
# exist - stops being observable. Measured, not reasoned: at that ratio the latch
# fired on frame 1 of every drag.
#
# The stage-relative figures below are the reference's own numbers over its own
# island: `60/640`, `90/640`, `180/640`, `14/176`, `200/640`, `150/176`.

#: Lock thresholds, **as fractions of each axis of the stage**.
#:
#: The reference's latch is `hypot(dx, dy) < 60` inside a **640x176** island, and
#: a circle of one radius in a non-square stage is the same thing as *two
#: different tolerances*: 60 of 640 across is narrow, 60 of 176 down is wide.
#: Scaling each axis by its own reference dimension is what preserves the actual
#: behaviour; a single ratio on a square stage does not, and **measured**: with one
#: radius scaled by the width the latch became **unreachable**, because `by` is
#: pinned to the stage centre and the character can never close a vertical gap
#: wider than the horizontal tolerance.
LOCK_IN_RATIO_X = 60.0 / 640.0
LOCK_OUT_RATIO_X = 90.0 / 640.0
LOCK_IN_RATIO_Y = 60.0 / 176.0
LOCK_OUT_RATIO_Y = 90.0 / 176.0

#: A pointer crossing this fraction of the stage per second is *passing through*
#: rather than *being caught*. `180/640` px/s in the reference island, which is
#: one character-length per second against a 620px card.
LOCK_SPEED_RATIO = 180.0 / 640.0

#: The pointer sits this fraction of the stage height below the character's centre
#: when the lock distance is measured (`cursorY + 14 - by.v`). `14/176`.
LOCK_Y_OFFSET_RATIO = 14.0 / 176.0

#: Velocity to tilt. `bx.velocity * 0.0015`, clamped, then smoothed toward the
#: target by `1 - 0.0005^dt` - a frame-rate-independent exponential.
TILT_GAIN = 0.0015
TILT_CLAMP = 0.18
TILT_SMOOTH_BASE = 0.0005

#: The catch-hop: `-5 * sin(pi * seg(t, lockAt, lockAt + 0.15))`, amplitude as a
#: ratio of the *character* (5px on a 62px character, so 8% of its size).
HOP_PX_RATIO = 5.0 / 62.0
HOP_SECONDS = 0.15

#: Mouth openings, as a fraction of the mouth rect's half-height. `MOUTH_AJAR` is
#: "being chased", `MOUTH_OPEN` is "caught", `MOUTH_MAX` is "the moment of receipt".
MOUTH_AJAR = 0.2
MOUTH_OPEN = 0.42
MOUTH_MAX = 0.5

#: The morph pop: `eBack` over this many seconds on entry and on the way home,
#: and the overshoot is clamped here rather than at every use.
MORPH_SECONDS = 0.38
MORPH_MAX = 1.08

#: Zone feedback. Hovering the zone washes the sprite green at this strength; a
#: text label dims to this fraction while the character is in front of it.
ZONE_WASH = 0.22
ZONE_TEXT_DIM = 0.25

#: Where the character looks, as a fraction of the stage. The reference divides
#: `lkx / 200` and `lky / 150` in a 640x176 island, so the bearing reaches its
#: clamp after about a third of the width and a whole height.
LOOK_DIV_X = 200.0 / 640.0
LOOK_DIV_Y = 150.0 / 176.0

#: The progress bar's geometry as fractions of the stage. `46/640`, `520/640` and
#: `118/176` in the reference island.
BAR_X0 = 0.071875
BAR_X1 = 0.8125
BAR_Y = 0.670455
#: Bar thickness, as a fraction of the sprite: `14 / 62`.
BAR_THICKNESS_RATIO = 14.0 / 62.0
#: The dip the character makes when the work completes: `8 * sin(...)`.
BAR_DIP_RATIO = 8.0 / 62.0

#: The text label dims while the character is drawn over it. The reference tests
#: `x > TEXT_X - 40` against a label at `196` in a `640` island, so the threshold
#: is at `0.244` of the stage width.
TEXT_BLOCKED_AT = 156.0 / 640.0

#: Timeline. **Absolute seconds, anchored on the drop**, exactly as the reference
#: anchors them on `T_DROP`. `ENTRY_T_REF` gives 0.40 s of following before the
#: drop, which is the reference's own comment and the reason a long hover does not
#: arrive already finished.
T_DROP = 1.95
T_SUCK_START = 2.03
T_SUCK_END = 2.33
T_CLOSE_END = 2.42
T_CHEW1 = 2.60
T_CHEW_END = 2.88
T_SHRINK_END = 3.23
T_BAR_IN = 3.00
T_PROG_START = 3.25
ENTRY_T_REF = T_DROP - 0.4

#: The reference's default `uploadDuration`.
DEFAULT_WORK_SECONDS = 2.4

#: A long stall - a backgrounded tab, a suspended session - must not spin the
#: integrator. Matches the reference's own guard.
MAX_CATCHUP = 2.0

#: The reference's `fileVisible`/`zoneAlpha` windows.
ZONE_ALPHA_FADE = 0.2
BAR_IN_FADE = 0.25
SQUEEZE_HOLD = 0.07
SQUEEZE_RELEASE = 0.2
CHEW_PERIOD = 0.14
GROW_LEAD = 0.25
GROW_TRAIL = 0.7
HOME_LEAD = 0.15
HOME_FADE = 0.6
FLASH_SECONDS = 0.3
CHECK_SECONDS = 0.25
DIP_SECONDS = 0.2


class Pose(NamedTuple):
    """One frame, in **stage pixels** and stage fractions.

    `x`, `y` and `d` are the character's centre and diameter. Everything else is
    either a px offset, a scale factor in `1.0 == resting`, or an alpha in
    `0.0..1.0`; the named constants above say which.
    """

    t: float
    cursor_x: float
    cursor_y: float
    morph: float
    x: float
    y: float
    d: float
    sx: float
    sy: float
    tilt: float
    hop: float
    mouth: float
    mouth_rect: "MouthRect"
    eye: str
    look_x: float
    look_y: float
    sprite_visible: bool
    suck: float
    zone_over: bool
    zone_alpha: float
    text_alpha: float
    bar_reveal: float
    bar_alpha: float
    progress: float
    flash: float
    check: float
    green_wash: float
    #: Ours. The reference calls this `chooseAlpha` and grows the character into a
    #: "where should this file go" affordance; a mascot has nothing to choose, so
    #: it grows **home**. Same motion, honest destination.
    home_alpha: float
    active: bool
    held: bool
    locked: bool
    working: bool
    prog_end: float
    grow_start: float
    grow_end: float


class MouthRect(NamedTuple):
    """The rect to draw, in stage pixels, already sheared by `sx`/`sy`."""

    x: float
    y: float
    w: float
    h: float


def _rest_pose(stage_w: float, stage_h: float, d: float) -> Pose:
    """The frame for a mascot that is doing nothing.

    Named rather than inlined so `inactive_engine.frame()` and every reset return
    *the same object*, which is what lets a test assert equality instead of
    comparing twenty fields - the lesson this repository records about counting
    where identity would do.
    """
    cx, cy = stage_w / 2.0, stage_h / 2.0
    r = d / 2.0
    return Pose(
        t=0.0, cursor_x=cx, cursor_y=cy, morph=0.0, x=cx, y=cy, d=d,
        sx=1.0, sy=1.0, tilt=0.0, hop=0.0, mouth=0.0,
        mouth_rect=MouthRect(cx - r, cy - r * 0.97, 2.0 * r, 0.0),
        eye="pill", look_x=0.0, look_y=0.0, sprite_visible=True, suck=0.0,
        zone_over=False, zone_alpha=1.0, text_alpha=1.0, bar_reveal=0.0,
        bar_alpha=0.0, progress=0.0, flash=0.0, check=0.0, green_wash=0.0,
        home_alpha=0.0, active=False, held=False, locked=False, working=False,
        prog_end=T_PROG_START + DEFAULT_WORK_SECONDS,
        grow_start=T_PROG_START + DEFAULT_WORK_SECONDS + GROW_LEAD,
        grow_end=T_PROG_START + DEFAULT_WORK_SECONDS + GROW_TRAIL,
    )


def upload_progress_curve(u: float) -> float:
    """Quick to 60%, an unhurried middle, then a last push.

    The reference's own comment: *"A plain ease-out reads as a different animation
    entirely."* Kept as three branches rather than composed, because the shape of
    the middle is the whole point and a single expression over `u` cannot express
    it.
    """
    if u < 0.4:
        return 0.6 * e_out(u / 0.4)
    if u < 0.85:
        return 0.6 + 0.32 * e_in_out((u - 0.4) / 0.45)
    return 0.92 + 0.08 * e_in((u - 0.85) / 0.15)


def progress_at(t: float, prog_start: float, prog_end: float) -> float:
    return 0.0 if t < prog_start else upload_progress_curve(seg(t, prog_start, prog_end))


def _squeeze_y(t: float) -> float:
    """Vertical keyframes for the `T_SUCK_END` -> `T_CHEW1` squeeze."""
    t0 = T_SUCK_END
    t1, t2, t3 = t0 + SQUEEZE_HOLD, t0 + SQUEEZE_RELEASE, T_CHEW1
    if t <= t0:
        return 1.06
    if t <= t1:
        return lerp(1.06, 0.82, e_out(seg(t, t0, t1)))
    if t <= t2:
        return lerp(0.82, 1.1, e_out(seg(t, t1, t2)))
    if t <= t3:
        return lerp(1.1, 1.0, e_in_out(seg(t, t2, t3)))
    return 1.0


def _squeeze_x(t: float) -> float:
    """Horizontal keyframes for the same window."""
    t0 = T_SUCK_END
    t1, t2, t3 = t0 + SQUEEZE_HOLD, t0 + SQUEEZE_RELEASE, T_CHEW1
    if t <= t0:
        return 0.97
    if t <= t1:
        return lerp(0.97, 1.14, e_out(seg(t, t0, t1)))
    if t <= t2:
        return lerp(1.14, 0.95, e_out(seg(t, t1, t2)))
    if t <= t3:
        return lerp(0.95, 1.0, e_in_out(seg(t, t2, t3)))
    return 1.0


class MascotMotion:
    """The whole portable set, in one pure object.

    Lifecycle, in the order a person meets it:

    1. `grab(x, y)` - the mascot is picked up. Starts the follow phase.
    2. `move(x, y)` - the pointer moved. Locks, tilts and hops happen here.
    3. `release(seconds)` - let go, and (optionally) begin a work timeline of that
       length. The reference's `performDrop(uploadDuration)`.
    4. `frame()` - one frame. Safe to call at any rate; the integrator sub-steps.

    `begin_work(seconds)` enters the same timeline without a drag, for a turn that
    started without anybody touching the mascot.
    """

    def __init__(self, stage_w: float, stage_h: float, size: float,
                 now: Optional[Callable[[], float]] = None) -> None:
        if stage_w <= size or stage_h <= size:
            raise ValueError(
                "the stage must hold the sprite with room to travel: a mascot in a "
                "window its own size cannot move at all, and every mechanic here is "
                "travel")
        self.stage_w = float(stage_w)
        self.stage_h = float(stage_h)
        self.size = float(size)
        self._clock = now if now is not None else time.monotonic

        self._active = False
        self._held = False
        self._working = False
        self._t = 0.0
        self._entered: Optional[float] = None
        self._entry_wall = 0.0
        self._drop_wall: Optional[float] = None
        self._cursor_x = stage_w / 2.0
        self._cursor_y = stage_h / 2.0
        self._prev_x = self._cursor_x
        self._prev_y = self._cursor_y
        self._prev_t = 0.0
        self._speed = 0.0
        self._locked = False
        self._lock_at = -9.0
        self._tilt = 0.0
        self._bx = Spring(self._rest_x)
        self._by = Spring(self._rest_y)
        self._mouth = Spring(0.0)
        self._work_seconds = DEFAULT_WORK_SECONDS

    # ── Geometry, all derived ────────────────────────────────────────────────

    @property
    def _rest_x(self) -> float:
        return self.stage_w / 2.0

    @property
    def _rest_y(self) -> float:
        return self.stage_h / 2.0

    @property
    def _follow_min(self) -> float:
        return self.size / 2.0

    @property
    def _follow_max(self) -> float:
        return self.stage_w - self.size / 2.0

    @property
    def bar_x0(self) -> float:
        return BAR_X0 * self.stage_w

    @property
    def bar_x1(self) -> float:
        return BAR_X1 * self.stage_w

    @property
    def bar_y(self) -> float:
        return BAR_Y * self.stage_h

    @property
    def prog_end(self) -> float:
        return T_PROG_START + self._work_seconds

    @property
    def grow_start(self) -> float:
        return self.prog_end + GROW_LEAD

    @property
    def grow_end(self) -> float:
        return self.prog_end + GROW_TRAIL

    def _now(self) -> float:
        return self._clock()

    # ── Session lifecycle ────────────────────────────────────────────────────

    def grab(self, x: float, y: float) -> None:
        """The mascot has been picked up at stage point `(x, y)`."""
        now = self._now()
        self._cursor_x, self._cursor_y = float(x), float(y)
        self._prev_x, self._prev_y = float(x), float(y)
        self._prev_t = now
        self._speed = 0.0

        self._t = ENTRY_T_REF
        self._entered = ENTRY_T_REF
        self._entry_wall = now
        self._bx = Spring(self._rest_x)
        self._by = Spring(self._rest_y)
        self._tilt = 0.0
        self._mouth = Spring(0.0)
        self._locked = False
        self._lock_at = -9.0
        self._drop_wall = None
        self._active = True
        self._held = True
        self._working = False

    def move(self, x: float, y: float) -> None:
        """The pointer moved. Only meaningful while held."""
        now = self._now()
        dt = now - self._prev_t
        # The reference's own `dt > 0.001` guard. Without it two events in the
        # same millisecond divide by ~0 and the speed guard that stops a fast sweep
        # latching the character reads as "infinitely fast" - which latches it.
        if dt > 0.001:
            self._speed = math.hypot(x - self._prev_x, y - self._prev_y) / dt
        self._prev_x, self._prev_y, self._prev_t = float(x), float(y), now
        self._cursor_x, self._cursor_y = float(x), float(y)

    def release(self, work_seconds: float = 0.0) -> None:
        """Let go. `work_seconds > 0` also begins a work timeline of that length."""
        self._held = False
        self._drop_wall = self._now()
        if work_seconds > 0:
            self._work_seconds = float(work_seconds)
            self._working = True
            # Restart the canonical post-drop timeline however long the drag ran,
            # **preserving the spring state** - position and velocity are what make
            # the character arrive with the momentum the drag gave it.
            self._t = T_DROP
        else:
            self._working = False

    def begin_work(self, seconds: float) -> None:
        """Start the work timeline without a drag: an assistant turn, narrated.

        **The entry pop is synthesised rather than skipped.** The reference always
        reaches its timeline through `enterZone`, so `entered` is always set and
        `morph` is always `eBack(seg(pt, entered, entered + 0.38))` from the start.
        A turn that starts without anybody touching the mascot has no zone entry -
        and leaving `_entered` as `None` makes `morph` evaluate `eBack(seg(t, 1e9,
        1e9 + 0.38))`, which is **`eBack(0) = 0.0` for the whole drop-to-chew
        window**. Measured: a work timeline started this way never pops in. So the
        entry is placed one pop-length before the drop, which makes the pop land
        exactly on it.
        """
        now = self._now()
        self._cursor_x, self._cursor_y = self._rest_x, self._rest_y
        self._prev_x, self._prev_y, self._prev_t = self._cursor_x, self._cursor_y, now
        self._speed = 0.0
        self._work_seconds = max(0.0, float(seconds))
        self._t = T_DROP
        self._entered = T_DROP - MORPH_SECONDS
        self._entry_wall = now
        self._locked = False
        self._lock_at = -9.0
        self._bx = Spring(self._rest_x)
        self._by = Spring(self._rest_y)
        self._tilt = 0.0
        self._mouth = Spring(0.0)
        self._drop_wall = now
        self._active = True
        self._held = False
        self._working = True

    def stop(self) -> None:
        """End every session. `frame()` is the rest frame from here on."""
        self._active = False
        self._held = False
        self._working = False
        self._drop_wall = None
        self._entered = None
        self._locked = False
        self._lock_at = -9.0

    # ── Readable state, for the window and for tests ─────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def held(self) -> bool:
        return self._held

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def working(self) -> bool:
        return self._working

    @property
    def work_seconds(self) -> float:
        return self._work_seconds

    # ── Public entry point ───────────────────────────────────────────────────

    def frame(self) -> Pose:
        """Everything one frame needs. A pure function of the clock and the input."""
        if not self._active:
            return _rest_pose(self.stage_w, self.stage_h, self.size)
        t = self._t_ref()
        self._simulate_to(t)
        return self._compute_frame(t)

    def _t_ref(self) -> float:
        """The wall-clock instant the integrator is chasing.

        **Two anchors, not one.** The reference keeps `entryWall` and `dropWall`
        separately and reads whichever is live: before the release the timeline is
        measured from when the character was picked up, and from the release it is
        measured from the release. A single anchor cannot express both, and the
        common version of this bug - one anchor plus "restart `t` on release" -
        makes the phase clock jump by however long the drag lasted the first frame
        after it ends, which is the one frame that decides whether the work
        timeline is seen at all.
        """
        now = self._now()
        if self._drop_wall is not None:
            return T_DROP + max(0.0, now - self._drop_wall)
        return ENTRY_T_REF + max(0.0, now - self._entry_wall)

    # ── Simulation ───────────────────────────────────────────────────────────

    def _simulate_to(self, target: float) -> None:
        if target - self._t > MAX_CATCHUP:
            self._t = target - MAX_CATCHUP
        while self._t < target - 1e-10:
            dt = min(DT, target - self._t)
            self._step_once(dt)
            self._t += dt

    def _step_once(self, dt: float) -> None:
        dragging = self._drop_wall is None

        # Horizontal follow and the lock latch, only while held and engaged.
        if dragging and self._held and self._entered is not None:
            # Two axes, two tolerances - see the constant table. The character's
            # vertical position is pinned to the stage centre, so a single radius
            # scaled by one axis makes the latch unreachable rather than merely
            # wrong, which is what the first version of this did.
            dx = self._cursor_x - self._bx.v
            dy = (self._cursor_y + LOCK_Y_OFFSET_RATIO * self.stage_h - self._by.v)
            in_x = LOCK_IN_RATIO_X * self.stage_w
            out_x = LOCK_OUT_RATIO_X * self.stage_w
            in_y = LOCK_IN_RATIO_Y * self.stage_h
            out_y = LOCK_OUT_RATIO_Y * self.stage_h
            fast_enough = self._speed < LOCK_SPEED_RATIO * self.stage_w
            if not self._locked and abs(dx) < in_x and abs(dy) < in_y and fast_enough:
                self._locked = True
                self._lock_at = self._t
            # A fast sweep must not *un*-lock either: the reference only breaks the
            # latch on distance, and a pointer leaving at speed would otherwise
            # unlatch on the way past and hop again. Kept identical.
            if self._locked and (abs(dx) > out_x or abs(dy) > out_y):
                self._locked = False
            target = max(self._follow_min, min(self._follow_max, self._cursor_x))
            response = RESPONSE_LOCKED if self._locked else RESPONSE_CHASE
            damping = DAMPING_LOCKED if self._locked else DAMPING_CHASE
            self._bx.step(target, response, damping, dt)
            self._by.step(self._rest_y, response, damping, dt)

        # Tilt follows how fast the character is sliding.
        if dragging and self._held:
            tilt_target = max(-TILT_CLAMP, min(TILT_CLAMP, self._bx.vel * TILT_GAIN))
        else:
            tilt_target = 0.0
        self._tilt = lerp(self._tilt, tilt_target, 1.0 - TILT_SMOOTH_BASE ** dt)

        # Mouth. The post-drop close only runs once the drop has actually happened.
        t = self._t
        if not dragging and t >= T_SUCK_END:
            self._mouth.v = max(0.0, lerp(MOUTH_MAX, 0.0,
                                          e_in(seg(t, T_SUCK_END, T_CLOSE_END))))
        else:
            mt = 0.0
            if self._held and self._entered is not None:
                mt = MOUTH_OPEN if self._locked else MOUTH_AJAR
            if not dragging and t < T_SUCK_END:
                mt = MOUTH_MAX
            self._mouth.step(mt, MOUTH_RESPONSE, MOUTH_DAMPING, dt)
            if self._mouth.v < 0:
                self._mouth.v = 0.0

    # ── Frame computation ────────────────────────────────────────────────────

    def _compute_frame(self, t: float) -> Pose:
        d = self.size
        prog_end, grow_start, grow_end = self.prog_end, self.grow_start, self.grow_end
        entered = self._entered if self._entered is not None else 1e9
        dragging = self._drop_wall is None
        # Clamp the phase clock to just before the drop while still held, so a
        # long hover never trips the post-drop visuals.
        pt = min(t, T_DROP - DT) if dragging else t

        # Morph: 0 -> 1 on entry, 1 -> 0 shrinking away, 0 -> 1 growing home.
        if pt < T_CHEW_END:
            morph = e_back(seg(pt, entered, entered + MORPH_SECONDS))
        elif pt < grow_start:
            morph = 1.0 - e_out(seg(pt, T_CHEW_END, T_SHRINK_END))
        else:
            morph = e_back(seg(pt, grow_start, grow_end))
        morph = max(0.0, min(morph, MORPH_MAX))

        # Position and diameter.
        x, y, dd = self._bx.v, self._by.v, d
        if T_CHEW_END <= pt < T_PROG_START:
            k = e_in_out(seg(pt, T_CHEW_END, T_SHRINK_END))
            x = lerp(self._bx.v, self.bar_x0, k)
            y = lerp(self._by.v, self.bar_y, k)
            dd = lerp(d, BAR_THICKNESS_RATIO * d, k)
        if pt >= T_PROG_START:
            p = progress_at(pt, T_PROG_START, prog_end)
            x = lerp(self.bar_x0, self.bar_x1, p)
            y = self.bar_y
            dd = BAR_THICKNESS_RATIO * d
        if pt >= prog_end:
            x = self.bar_x1
            y = self.bar_y - BAR_DIP_RATIO * d * math.sin(
                math.pi * seg(pt, prog_end, prog_end + DIP_SECONDS))
        if pt >= grow_start:
            k = e_in_out(seg(pt, grow_start, grow_end))
            x = lerp(self.bar_x1, self._rest_x, k)
            y = lerp(self.bar_y, self._rest_y, k)
            dd = lerp(BAR_THICKNESS_RATIO * d, d, e_back(seg(pt, grow_start, grow_end)))

        # Squeeze, chew, stretch - the reference's seven phases, in order.
        sx = sy = 1.0
        if T_DROP <= pt < T_SUCK_START:
            k = e_out(seg(pt, T_DROP, T_SUCK_START))
            sy = lerp(1.0, 0.92, k)
            sx = lerp(1.0, 1.06, k)
        if T_SUCK_START <= pt < T_SUCK_END:
            k = e_in_out(seg(pt, T_SUCK_START, T_SUCK_END))
            sy = lerp(0.92, 1.06, k)
            sx = lerp(1.06, 0.97, k)
        if T_SUCK_END <= pt < T_CHEW1:
            sy, sx = _squeeze_y(pt), _squeeze_x(pt)
        if T_CHEW1 <= pt < T_CHEW_END:
            k = ((pt - T_CHEW1) % CHEW_PERIOD) / CHEW_PERIOD
            sy = 1.0 - 0.05 * math.sin(math.pi * k)
            sx = 1.0 + 0.03 * math.sin(math.pi * k)
        if T_CHEW_END <= pt < T_SHRINK_END:
            k = seg(pt, T_CHEW_END, T_SHRINK_END)
            sy = 1.0 + 0.12 * math.sin(math.pi * k)
            sx = 1.0 - 0.06 * math.sin(math.pi * k)
        if T_PROG_START <= pt < prog_end:
            v = (progress_at(pt + 0.01, T_PROG_START, prog_end)
                 - progress_at(pt, T_PROG_START, prog_end)) / 0.01
            st = max(0.0, min(1.0, v * 0.18))
            sx = 1.0 + 0.25 * st
            sy = 1.0 - 0.15 * st
        if grow_start <= pt < grow_end:
            sy = 1.0 + 0.06 * math.sin(math.pi * seg(pt, grow_start, grow_end))

        # The catch-hop, the one discrete event the drag is built around.
        hop = 0.0
        if self._lock_at > 0 and dragging and self._held:
            hop = -HOP_PX_RATIO * d * math.sin(
                math.pi * seg(pt, self._lock_at, self._lock_at + HOP_SECONDS))

        # Eyes: `cup` once caught, `content` while working and on completion.
        eye = "pill"
        if self._locked and pt < T_SUCK_END:
            eye = "cup"
        if T_SUCK_END <= pt < T_CHEW_END + 0.1:
            eye = "content"
        if prog_end <= pt < grow_end + 0.3:
            eye = "content"

        # The bearing. While held the eyes follow the pointer; once the character
        # is working they look ahead down the bar, then home.
        if pt < T_SUCK_END:
            lkx = self._cursor_x - x
            lky = self._cursor_y + LOCK_Y_OFFSET_RATIO * self.stage_h - y
        elif pt < T_PROG_START:
            lkx = 0.0
            lky = 0.0
        else:
            lkx = LOOK_DIV_X * self.stage_w
            lky = 0.0
        look_x = max(-1.0, min(1.0, lkx / (LOOK_DIV_X * self.stage_w)))
        look_y = max(-1.0, min(1.0, lky / (LOOK_DIV_Y * self.stage_h)))

        # Visible until the bar takes over, and **again once it grows home**.
        #
        # The reference's single test is `pt < T_SUCK_END`, which is right for a
        # file upload: the character does not come back, because what grows at the
        # end is a *different* thing - its "where should this go" affordance. Our
        # last phase grows the character **home**, so with the reference's rule the
        # mascot stayed invisible for the whole of it and `stop()` was the only
        # thing that ever brought it back - which is a mascot that vanishes for
        # six seconds on every narrated turn. Measured: `sprite_visible` was False
        # at every sample after the drop, and still False at `grow_end`, thirteen
        # seconds in.
        sprite_visible = pt < T_SUCK_END or pt >= grow_start
        suck = seg(pt, T_SUCK_START, T_SUCK_END)

        zone_over = self._entered is not None and pt < T_CHEW_END
        zone_alpha = 1.0 - seg(pt, T_CHEW_END, T_CHEW_END + ZONE_ALPHA_FADE)
        over_text = x > TEXT_BLOCKED_AT * self.stage_w and dragging
        text_alpha = zone_alpha * (ZONE_TEXT_DIM if over_text else 1.0)

        bar_reveal = e_out(seg(pt, T_BAR_IN, T_BAR_IN + BAR_IN_FADE)) * (
            1.0 - seg(pt, grow_start, grow_start + 0.2))
        bar_alpha = seg(pt, T_BAR_IN + 0.05, T_BAR_IN + BAR_IN_FADE) * (
            1.0 - seg(pt, grow_start, grow_start + 0.2))
        progress = progress_at(pt, T_PROG_START, prog_end)
        flash = math.sin(math.pi * seg(pt, prog_end, prog_end + FLASH_SECONDS)) \
            if pt >= prog_end else 0.0
        check = e_back(seg(pt, prog_end, prog_end + CHECK_SECONDS)) if pt >= prog_end else 0.0

        hover_green = ZONE_WASH if zone_over else 0.0
        upload_green = 0.0
        if pt >= T_PROG_START:
            base_green = progress * 0.5
            flash_extra = (0.2 * math.sin(math.pi * seg(pt, prog_end, prog_end + 0.4))
                           if pt >= prog_end else 0.0)
            fade_out = 1.0 - seg(pt, grow_end, grow_end + HOME_FADE)
            upload_green = (base_green + flash_extra) * fade_out
        green_wash = max(hover_green, upload_green)
        home_alpha = seg(pt, grow_start + HOME_LEAD, grow_end)

        # Mouth rect, already sheared by the squeeze and lifted by the hop.
        r = dd / 2.0 / 1.04
        mc = max(0.0, min(morph, 1.0))
        rx = r * (1.04 - 0.04 * mc)
        ry = r * (0.97 - 0.03 * mc)
        mh = self._mouth.v * r * mc
        mw = 2.0 * rx - 0.24 * r
        mouth_rect = MouthRect(x + (-mw / 2.0) * sx,
                               y + hop + (-ry + 0.1 * r) * sy,
                               mw * sx, mh * sy)

        return Pose(
            t=t, cursor_x=self._cursor_x, cursor_y=self._cursor_y, morph=morph,
            x=x, y=y, d=dd, sx=sx, sy=sy, tilt=self._tilt, hop=hop,
            mouth=self._mouth.v, mouth_rect=mouth_rect, eye=eye,
            look_x=look_x, look_y=look_y, sprite_visible=sprite_visible, suck=suck,
            zone_over=zone_over, zone_alpha=zone_alpha, text_alpha=text_alpha,
            bar_reveal=bar_reveal, bar_alpha=bar_alpha, progress=progress,
            flash=flash, check=check, green_wash=green_wash,
            home_alpha=home_alpha, active=True, held=self._held,
            locked=self._locked, working=self._working,
            prog_end=prog_end, grow_start=grow_start, grow_end=grow_end,
        )


__all__: List[str] = [
    "DT", "LOCK_IN_RATIO_X", "LOCK_OUT_RATIO_X", "LOCK_IN_RATIO_Y",
    "LOCK_OUT_RATIO_Y", "LOCK_SPEED_RATIO", "LOCK_Y_OFFSET_RATIO", "TILT_CLAMP",
    "MOUTH_AJAR", "MOUTH_OPEN", "MOUTH_MAX", "MORPH_MAX", "ZONE_WASH",
    "ZONE_TEXT_DIM", "T_DROP", "T_PROG_START", "ENTRY_T_REF",
    "DEFAULT_WORK_SECONDS", "MascotMotion", "MouthRect", "Pose", "Spring",
    "e_back", "e_in", "e_in_out", "e_out", "lerp", "progress_at", "seg",
    "upload_progress_curve",
]
