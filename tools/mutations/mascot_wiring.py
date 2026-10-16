"""Mutation set for the drag wiring: the stage, the tick, and the clamp.

`mascot_drag` is pure and is mutation-proven on its own (20/20). **That is exactly
why this set exists**: a fully green engine suite says nothing about whether
`mascot.py` calls it, and the gaze port's hard part was never the geometry - it was
that three wiring mutations survived a complete, correct, fully tested engine.

**Every witness is a bare method name.** `tools/mutate.py` builds its verdict set
from `line.split("::")[-1]`, so a `Class::method` compares a full id against a
bare one and marks the witness stale.

**Every anchor was re-read from the live file and asserted `count == 1` before the
first run.** Two of these were written from the plan rather than from the file and
matched nothing, which the runner reports as a survivor with a misleading reason -
so every anchor here was `grep -c`'d rather than trusted.
"""

TEST_FILE = "tests/test_mascot_wiring.py"

_M = "usr/lib/shani-chronoa/shani_chronoa/mascot.py"

# The stage is arithmetic, and `212` is the number every measured engine figure in
# `tests/test_mascot_drag.py` was taken against.
_STAGE_DEF = "_STAGE = SIZE + 2 * _STAGE_TRAVEL"

# The window's own default size. This is the whole claim that the window *is* the
# stage: a window sized `SIZE` would let the engine believe in travel it has not
# got, and every mechanic in `mascot_drag` is a fraction of the stage.
_WINDOW_SIZE = "win.set_default_size(_STAGE, _STAGE + _BUBBLE_PX)"

# The window margins must stay `_BASE_MARGIN`, because `_bob_tick` returns there
# every 90ms. Two different values here are the defect that is hardest to see.
_BOTTOM_MARGIN = "shell.set_margin(win, shell.Edge.BOTTOM, _BASE_MARGIN)"

# `drag-update` carries a delta. The accumulation is two lines and only the first
# is load-bearing for the *drag*; the second is what makes `drag-end` land where
# the pointer was released.
# `drag-update` carries a delta. The accumulation is two lines and only the first
# is load-bearing for the *drag*; the second is what makes `drag-end` land where
# the pointer was released.
#
# **The anchor runs on into `def _on_drag_end`, because the two handlers carry
# byte-identical bodies.** `tools/mutate.py` requires every anchor to occur exactly
# once and refused this as "occurs 2 times" - which is the check doing its job, and
# a `str.replace` that matched two sites would have mutated both halves for one
# edit and been reported as a pair.
_DRAG_UPDATE_ACCUM = ("        self._drag_point = (self._drag_point[0] + dx, "
                      "self._drag_point[1] + dy)\n        self._engine.move("
                      "*self._drag_point)\n\n    def _on_drag_end(self, "
                      "_gesture, dx: float, dy: float) -> None:")

_FRAME_TICK_STOP = (
    "if engine.working and pose.t >= engine.grow_end + _DONE_HOLD_MS / 1000.0:"
)

_CLAMP_CALL = ("        left = _clamp(pose.x - w / 2.0, 0.0, _STAGE - w)")
_CLAMP_TOP = ("        top = _clamp(pose.y - h / 2.0 - pose.hop, 0.0, _STAGE - h)")

_CLAMP_INVERTED = (
    "    if high < low:\n        return low\n")

_CLAMP_BODY = "    return max(low, min(high, value))"

_SIZE_REQUEST = ("        self._picture.set_size_request(int(round(w)), "
                 "int(round(h)))")

_MOUTH_ARG = ("                                 tilt=pose.tilt if pose is not None else 0.0,\n"
              "                                 mouth=pose.mouth if pose is not None else 0.0)")

_VISIBLE = "        self._picture.set_visible(pose.sprite_visible)"

_GAZE_FROM_POSE = "        self._gaze = (pose.look_x, pose.look_y)"

_WORK_SECONDS_CALL = "        self._engine.release(work_seconds=_WORK_SECONDS)"

_FRAME_TIMER_CANCEL = ('"_bubble_timer", "_unblink_timer", "_frame_timer"')

_FRAME_TIMER_ARM = "        self._frame_timer = GLib.timeout_add(_FRAME_MS, self._frame_tick)"

_MUTATIONS = [

    # --- the stage ---------------------------------------------------------
    {
        "label": "the stage is a hand-typed 212, not SIZE plus travel",
        "file": _M,
        "old": _STAGE_DEF,
        "new": "_STAGE = 212",
        "why": "The first version of the plan typed the measured number, and it "
               "is right today and silently wrong the day SIZE or TRAVEL moves: "
               "the engine would keep scaling every mechanic against a stage that "
               "no longer exists while the picture changed size with it. "
               "**The witness had to be rewritten for this to be caught at all** - "
               "it asserted `_STAGE == SIZE + 2*_STAGE_TRAVEL`, which `212` "
               "satisfies today, so the first run reported it a survivor. It now "
               "reads the assigned expression out of the module's AST, because the "
               "claim *is* that this is computed rather than typed.",
        "expect": ["test_the_stage_is_the_sprite_plus_travel_on_both_sides"],
    },
    {
        "label": "the window is sized to the sprite, so the engine has no travel",
        "file": _M,
        "old": _WINDOW_SIZE,
        "new": "win.set_default_size(SIZE, SIZE + 40)",
        "why": "**The shipped defect this set exists for.** The window was "
               "`SIZE x (SIZE + 40)`; growing it is what gives the sprite somewhere "
               "to travel. Caught by `test_the_window_really_is_the_stage`, which "
               "reads the call out of the AST rather than restating it - so a "
               "retyped 212 cannot satisfy it either.",
        "expect": ["test_the_window_really_is_the_stage"],
    },
    {
        "label": "the window is positioned by the sprite's travel, not the bob's margin",
        "file": _M,
        "old": _BOTTOM_MARGIN,
        "new": "shell.set_margin(win, shell.Edge.BOTTOM, _BUBBLE_PX + _STAGE_TRAVEL)",
        "why": "The layer-shell margins place the *window*; the sprite's own "
               "margins move inside it. Putting the travel here is the reading this "
               "change was written to avoid, and it silently breaks the bob - "
               "whose every-90ms return value is `_BASE_MARGIN`. **The witness "
               "first said `_BASE_MARGIN in {values}`**, which the untouched "
               "*right* margin satisfies on its own, so the first run reported this "
               "a survivor; it now checks every margin.",
        "expect": ["test_the_window_margins_stay_at_the_value_the_bob_returns_to"],
    },

    # --- the gesture -------------------------------------------------------
    {
        "label": "drag-update's delta is sent as a position",
        "file": _M,
        "old": _DRAG_UPDATE_ACCUM,
        "new": ("        self._engine.move(dx, dy)\n\n    def _on_drag_end(self, "
                "_gesture, dx: float, dy: float) -> None:"),
        "why": "**A real defect this test file found**, not a hypothetical: the "
               "handler passed the delta through without accumulating, so every "
               "`drag-update` asked the engine to move to the same absolute point "
               "and the mascot never travelled at all - while `held` stayed true, so "
               "the drag looked live. Caught by the assertion on "
               "`m._drag_point == (186, 66)` after twenty (4, -2) moves.",
        "expect": ["test_a_drag_begins_the_frames_and_a_release_ends_the_turn"],
    },
    {
        "label": "the release starts no work timeline",
        "file": _M,
        "old": _WORK_SECONDS_CALL,
        "new": "        self._engine.release()",
        "why": "The drop's whole second half - the progress bar, the chew, the grow "
               "home - is the work timeline. Without it the mascot is released and "
               "simply stops, which reads as a drag that dropped on the floor. "
               "Caught by `test_a_drag_begins_the_frames_and_a_release_ends_the_turn`.",
        "expect": ["test_a_drag_begins_the_frames_and_a_release_ends_the_turn"],
    },

    # --- the tick ----------------------------------------------------------
    {
        "label": "the turn is never ended",
        "file": _M,
        "old": _FRAME_TICK_STOP,
        "new": "if False:",
        "why": "`MascotMotion.stop()` is the only thing that clears "
               "`active`/`working`, and nothing else in the module calls it - so "
               "this is the defect the whole tick exists to prevent: a mascot stuck "
               "at its last pose for the life of the window, ticking 62 times a "
               "second. Caught by the lifecycle test's `assert not engine.active`.",
        "expect": ["test_a_drag_begins_the_frames_and_a_release_ends_the_turn"],
    },
    {
        "label": "the hold window is dropped, so the turn ends on the frame it moves",
        "file": _M,
        "old": _FRAME_TICK_STOP,
        "new": ("if engine.working and pose.t >= engine.grow_end:"),
        "why": "The hold is not cosmetic. Measured: from `t = 7.00` the geometry "
               "is bit-identical every frame, so the hold changes nothing a person "
               "can see - it exists so the tick that ends the turn lands on a pose "
               "already shown rather than in the same millisecond as the last "
               "movement. **A frame count cannot catch this**, because dropping "
               "the hold entirely still leaves a long identical tail - that was the "
               "first witness and the first run reported it a survivor. The "
               "observable that differs is *when* `stop()` ran.",
        "expect": ["test_the_turn_stops_on_the_frame_after_the_hold"],
    },
    {
        "label": "the frame timer is never armed, so no pose is ever drawn",
        "file": _M,
        "old": _FRAME_TIMER_ARM,
        "new": "        self._frame_timer = 0",
        "why": "`_start_frames` is what connects the engine to the display at all. "
               "Without it the mascot is grabbed, follows, and releases - and the "
               "window never shows a frame of it, because the *only* thing that "
               "calls `_frame_tick` is this timeout. Caught by the lifecycle "
               "test's `assert m._frame_timer` immediately after the grab.",
        "expect": ["test_a_drag_begins_the_frames_and_a_release_ends_the_turn"],
    },
    {
        "label": "the frame timer is not cancelled by stop()",
        "file": _M,
        "old": _FRAME_TIMER_CANCEL,
        "new": '"_bubble_timer", "_unblink_timer"',
        "why": "A `GLib` timeout that outlives its window calls "
               "`_apply_pose` and `_render` on a destroyed tree. Every guard in "
               "those two methods is an `is None` check, so it degrades rather "
               "than raising - which is why it would show up as a widget error "
               "somewhere else entirely. Caught by "
               "`test_stop_cancels_the_frame_timer`, which reads the tuple out of "
               "the AST.",
        "expect": ["test_stop_cancels_the_frame_timer"],
    },
    {
        "label": "the eyes aim at the grab point instead of the engine's look",
        "file": _M,
        "old": _GAZE_FROM_POSE,
        "new": "        self._gaze = self._gaze",
        "why": "The pointer is *inside* the window being moved, so `_on_motion` "
               "aims the eyes wherever the grab happened to start - which is where "
               "the mascot **was**. The mascot would stare at its old position "
               "while travelling, and the engine's own `look_x`/`look_y` would be "
               "computed every frame and read by nothing.",
        "expect": ["test_the_engines_look_wins_while_a_turn_runs"],
    },

    # --- the clamp ---------------------------------------------------------
    {
        "label": "the horizontal margin is not clamped, so a pose can leave the window",
        "file": _M,
        "old": _CLAMP_CALL,
        "new": "        left = pose.x - w / 2.0",
        "why": "TRAVEL cannot make this unnecessary: measured on this stage, one "
               "pixel of travel removes 0.88 px of the worst overflow, and the home "
               "swing *deliberately* goes past the follow bound because "
               "overshooting is what makes the settle look like a settle. Caught by "
               "the 15-case parametrised `test_the_sprite_is_always_inside_the_"
               "stage`.",
        "expect": ["test_the_sprite_is_always_inside_the_stage"],
    },
    {
        "label": "the vertical margin is not clamped",
        "file": _M,
        "old": _CLAMP_TOP,
        "new": "        top = pose.y - h / 2.0 - pose.hop",
        "why": "The same, on the axis that also carries the hop - so this one is "
               "the over-fix's twin: a clamp dropped here is invisible at rest and "
               "shows up during the bar phase, when `d` is 29.8px and the sprite is "
               "at its smallest.",
        "expect": ["test_the_sprite_is_always_inside_the_stage_vertically"],
    },
    {
        "label": "the hop is added rather than subtracted",
        "file": _M,
        "old": _CLAMP_TOP,
        "new": "        top = _clamp(pose.y - h / 2.0 + pose.hop, 0.0, _STAGE - h)",
        "why": "`hop` is the height the catch **lifts** the sprite by, so it "
               "subtracts. A sign error is invisible at rest (`hop` is 0.0 there) "
               "and would put the mascot's feet below the window for the whole of "
               "every catch. Caught by `test_the_hop_lifts_the_sprite`, which "
               "compares two otherwise identical poses.",
        "expect": ["test_the_hop_lifts_the_sprite"],
    },
    {
        "label": "an inverted clamp range centres the overflow on the window's edge",
        "file": _M,
        "old": _CLAMP_INVERTED,
        "new": "",
        "equivalent": True,
        "why": "**Measured, not reasoned.** `max(low, min(high, value))` already "
               "answers `low` whenever `high < low`: `min(high, value) <= high < "
               "low`, so the outer `max` is `low` either way. Exhaustive over all "
               "three arguments from -20 to 40 with `value` every third value - "
               "**72,000 cases, 0 differences** - so the guard is an equivalent by "
               "construction and the test written for it could never have bitten. "
               "The property it names is still worth having, so it is pinned "
               "against the *behaviour* by "
               "`test_clamp_answers_low_for_an_inverted_range`, which sweeps the "
               "whole inverted family rather than one hand-picked triple. The "
               "guard stays in the code: it is where a reader looks, and a "
               "provable equivalent is not a defect.",
        "expect": ["test_clamp_answers_low_for_an_inverted_range"],
    },
    {
        "label": "the clamp body is removed entirely",
        "file": _M,
        "old": _CLAMP_BODY,
        "new": "    return value",
        "why": "The blunt version of the same defect, so the pair cannot both be "
               "one witness: a mutation that removed only the inverted-range guard "
               "leaves the ordinary clamp working, and one that removes only the "
               "clamp leaves the guard unreachable.",
        "expect": ["test_clamp_holds_a_value_inside_its_range",
                   "test_the_sprite_is_always_inside_the_stage"],
    },

    # --- the picture -------------------------------------------------------
    {
        "label": "the size request ignores sx and sy",
        "file": _M,
        "old": _SIZE_REQUEST,
        "new": "        self._picture.set_size_request(int(round(pose.d)), int(round(pose.d)))",
        "why": "The picture's drawn size is `d * sx` by `d * sy`, and **this is "
               "also why the gesture is on the window and not the picture**: a size "
               "request that moves with `d` moves the widget's origin under the "
               "pointer. **The first witness derived its bound from the drawn "
               "height**, which is the right way to assert the bound and the wrong "
               "way to assert the *squash* - a widget sized `d x d` when the pose "
               "says `0.97 x 1.06` still satisfies it, so the first run reported "
               "this a survivor. `test_the_picture_is_drawn_at_the_posed_size` asks "
               "the widget for both dimensions across four of the engine's own "
               "squeeze phases, and `test_the_engine_really_does_squash_it` holds "
               "the formula to the engine that supplies the numbers.",
        "expect": ["test_the_picture_is_drawn_at_the_posed_size"],
    },
    {
        "label": "`sprite_visible` is never applied, so a hidden sprite is drawn",
        "file": _M,
        "old": _VISIBLE,
        "new": "        pass",
        "why": "`sprite_visible` is what the engine's `fileVisible` window drives - "
               "the mascot is *absent* from the frame for a slice of the drop and "
               "then present again. Ignoring it draws a mascot that should not be "
               "there. Caught by `test_sprite_visibility_reaches_the_picture`.",
        "expect": ["test_sprite_visibility_reaches_the_picture"],
    },
    {
        "label": "the over-fix: the sprite is hidden whenever a pose is applied",
        "file": _M,
        "old": _VISIBLE,
        "new": "        self._picture.set_visible(False)",
        "why": "Every over-applicable guard gets its own mutation, because the "
               "obvious wrong repair to a visibility line is to hide rather than to "
               "show - and a suite with only the first direction passes it.",
        "expect": ["test_sprite_visibility_reaches_the_picture"],
    },
    {
        "label": "the mouth channel is not passed to the art",
        "file": _M,
        "old": _MOUTH_ARG,
        "new": ("                                 tilt=pose.tilt if pose is not None else 0.0,\n"
                "                                 mouth=0.0)"),
        "why": "The mouth is one of the two live channels `mascot_drag` owns, and "
               "it is the whole of the `MOUTH_MAX`/`MOUTH_OPEN` distinction the art "
               "module was built for - `MOUTH_OPEN` is a spring *target* the spring "
               "passes, and `MOUTH_MAX` is what the post-drop branch assigns. A "
               "hardcoded 0.0 renders a closed mouth for the entire turn and every "
               "test in `test_mascot_art.py` still passes, because none of them "
               "drives this call.",
        "expect": ["test_the_two_live_channels_reach_the_art"],
    },
    {
        "label": "the last drag's channels are kept when no pose is given",
        "file": _M,
        "old": "tilt=pose.tilt if pose is not None else 0.0,\n"
               "                                 mouth=pose.mouth if pose is not None else 0.0)",
        "new": "tilt=0.0,\n                                 mouth=0.0)",
        "why": "The over-fix: caching the channels on the instance would carry the "
               "last drag's open mouth and its last lean into every state change "
               "for the life of the window. The second half of "
               "`test_the_two_live_channels_reach_the_art`, and the reason the "
               "zero case is asserted at all.",
        "expect": ["test_the_two_live_channels_reach_the_art"],
    },
]
# The runner reads the public name; `_MUTATIONS` is the conventional local.
MUTATIONS = _MUTATIONS
