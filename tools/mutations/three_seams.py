"""Mutation set for the mascot's three deferred seams: level quantisation, the
live audio level feeds, and the `thinking` work timeline.

All three were deferred with a name attached to each - `quantise_level` existed
and nothing called it, `Mascot.set_level` said *"nothing calls this yet"* and
named two lines in `app/voice.py`, and `MascotMotion.begin_work()` was fully
tested with **no caller in `usr/`**. This set is what says they are wired now,
and each seam is mutated at the site that was changed rather than at the helper
behind it.

**The decisions here are mutated in both directions**, because most of them look
like defects from one side:

- **Quantisation snaps with `round`, not `floor`.** The reason is boundary
  stability, not preference: a floor puts `0.019` and `0.021` in different
  buckets either side of a value that draws identically, so a level hovering at a
  step edge re-encodes forever. Mutation 2 is the `floor` somebody would reach
  for, and mutation 1 is the quantisation removed altogether.
- **`LEVEL_STEP = 0.02` is at or below the drawing's own granularity.** Measured:
  over the 51 quantised levels the halo radius `int(44 * level)` takes **45
  distinct values**, which is every value the *unquantised* range spans - the
  radius moves by 1 on 44 of the 50 transitions and by 0 on 6. So 0.05 would cost
  21 pictures instead of 51 (better on the frame budget) and silently throw away
  the rest of the drawing's range, which is a trade nobody chose and a test that
  only counted pictures would call a win. That is what mutation 3 measures, and it
  is why the picture-count witness asserts a count that depends on the grid rather
  than one flat number.
- **`set_level` stores the *quantised* value and early-returns on an unchanged
  picture.** These are one property. A mic feeds at audio rate and `_render`
  rasterises the whole SVG through `GdkPixbuf` - measured at **12.4 ms per
  changed frame** against a **16.6 ms** budget (`HARVEST-COUCOU.md` PART 9), so a
  level finer than the drawing can show is a frame budget spent for nothing.
  Mutation 6 removes the early return and mutation 7 stores the unquantised
  value: the same shimmer by two different routes, and the second is the one that
  a fix aimed at the first would reintroduce.
- **`_begin_turn_work` has three refusals** (no engine, held, already working) and
  honours reduced motion. Each is mutated individually, because they are not
  defence in depth: removing the `working` guard re-plays the entry pop, removing
  the `held` guard feels like the mascot letting go of itself under the pointer,
  and `engine is None` is the only thing standing between a turn and an
  `AttributeError`.

**One witness is deliberately NOT on mutation 8, and the reason is measured
rather than argued.** Removing the `if state != self._state:` guard looks like
the biggest mutation here, and the obvious witness -
`test_an_unchanged_state_does_not_restart_the_turn` - **does not fail when it is
removed**: `_begin_turn_work` refuses while `engine.working` is already true, so
the timeline is protected by a second guard underneath and `_t_ref()` reads
**2.9500** either way. A double guard makes the outer one invisible to anything
that reads the timeline. The witness is therefore
`test_an_unchanged_state_does_not_render_or_announce_again`, which counts the work
the guard exists to avoid; and the timeline test is kept in the file as a floor,
not as a witness for this mutation. The mutation that *does* watch the inner guard
is 10, which is where that test belongs.

**Witnesses here are bare method names, not `Class::method`.** `mutate.py` builds
its verdict set from `line.split("::")[-1]` and its `--check-witnesses` collection
from `rsplit("::", 1)[-1]`, so a qualified name compares a full id against a bare
one and marks **every** witness in the set stale - the same "two representations
of one thing compared as strings" that `gaze_iris.py` records.

**Every anchor was read from the live file and asserted `count == 1` by
`tools/mutate.py --check-anchors` before the run.** The first version of this set
was written against anchors transcribed from a design rather than from the file -
two of them were indented to a depth the code does not use - and a rotation set
already records that an anchor that matches nothing produces a mutation reported
as a survivor, which is a false clean wearing a passing shape.
"""

TEST_FILE = "tests/test_mascot_three_seams.py"

_ART = "usr/lib/shani-chronoa/shani_chronoa/mascot_art.py"
_MSC = "usr/lib/shani-chronoa/shani_chronoa/mascot.py"
_VOI = "usr/lib/shani-chronoa/shani_chronoa/app/voice.py"
_DRG = "usr/lib/shani-chronoa/shani_chronoa/mascot_drag.py"

#: The quantisation as `svg()` spells it, two lines below the state check. The
#: blank line is part of the anchor so the pair cannot be read as two edits: the
#: level is zeroed for a state whose halo does not move, and quantised after that,
#: so swapping them would quantise a level that is about to become zero.
_Q_IN_SVG = (
    "    level = _live_level(level) if state in (\"listening\", \"speaking\") else 0.0\n"
    "    level = quantise_level(level)")

#: `round` inside `quantise_level`, alone on the return line.
_ROUND = "    return min(1.0, round(level / LEVEL_STEP) * LEVEL_STEP)"

_LEVEL_STEP = "LEVEL_STEP = 0.02"

#: `set_level`'s body: quantise, early-return, render. One anchor for all three
#: mutations aimed here, because each of them is a different edit *within* it and
#: `mutate.py` reports a pair of anchors as a pair rather than as one edit.
_SET_LEVEL = (
    "            level = mascot_art.quantise_level(level)\n"
    "            if level == self._level:\n"
    "                return\n"
    "            self._level = level\n"
    "            self._render()")

#: The two handlers' last two lines, which are the whole of the seam: one call to
#: the orb, one call to the mascot. A pair rather than two anchors, because the two
#: feeds are the same shape and only one of them is mutated - mutating both
#: separately would be two mutations for one claim.
_INPUT_HANDLER_TAIL = (
    "            self.window.set_input_level(level)\n"
    "        self._level_to_mascot(level)")

#: `_level_to_mascot`'s mascot lookup. `getattr` rather than the attribute, so a
#: voice turn that begins before the desktop companion starts is not an
#: `AttributeError`.
_GETATTR_MASCOT = '        mascot = getattr(self, "mascot", None)'

#: The transition guard in `set_state`, at its own indentation - it is inside a
#: method, so eight spaces, not four.
_STATE_GUARD = "        if state != self._state:"

#: `_begin_turn_work`'s three refusals as one line, plus the call pair below it.
#: Mutated one clause at a time; the whole guard is the anchor so a fourth refusal
#: cannot be added without a rotation of this set.
_WORK_REFUSALS = (
    "        if engine is None or engine.held or engine.working:\n"
    "            return\n"
    "        if _motion_is_reduced():\n"
    "            return\n"
    "        engine.begin_work(_WORK_SECONDS)\n"
    "        self._start_frames()")

#: The engine's own clock. `_t_ref` -> `_now` -> `_clock` is the whole chain, and
#: the last link is the one that makes a timeline testable at all.
_ENGINE_NOW = (
    "    def _now(self) -> float:\n"
    "        return self._clock()")

_MUTATIONS = [

    # --- the quantisation seam ------------------------------------------------
    {
        "label": "the level is no longer quantised, so every sample is a new picture",
        "file": _ART,
        "old": _Q_IN_SVG,
        "new": (
            "    level = _live_level(level) if state in (\"listening\", \"speaking\") else 0.0"),
        "why": "The shipped gap, restored verbatim: `quantise_level` existed, was "
               "tested, and was not called by the renderer, so the mapping from "
               "level to pixels and the number of pictures a level could produce "
               "were two answers to one question.",
        "expect": [
            "test_the_step_produces_the_number_of_pictures_that_fit_a_frame",
        ],
    },
    {
        "label": "quantisation floors instead of rounding, so a level on a step edge re-encodes",
        "file": _ART,
        "old": _ROUND,
        "new": "    return min(1.0, int(level / LEVEL_STEP) * LEVEL_STEP)",
        "why": "The wrong snap, and the one that looks like a simplification. A "
               "floor is not symmetric about a step: 0.019 and 0.021 draw the same "
               "character and land either side of a bucket boundary, so a level "
               "hovering at the edge re-encodes forever. Mutation 1 removed "
               "quantisation altogether and this removes the part of it that makes "
               "it stable.",
        "expect": [
            "test_quantising_rounds_so_a_level_at_a_boundary_does_not_re_encode",
        ],
    },
    {
        "label": "the step is doubled, which costs pictures and loses drawing range",
        "file": _ART,
        "old": _LEVEL_STEP,
        "new": "LEVEL_STEP = 0.05",
        "why": "A cheaper picture count bought with the drawing's own range. 21 "
               "pictures instead of 51 is a real win on the 12.4 ms frame budget, "
               "and the halo radius `int(44 * level)` then spans 23 values where "
               "0.02 spans all 45 the unquantised range can show. **This is the "
               "mutation that stops the picture-count test being a metric lock**: "
               "the count is a property of the grid, so the witness reads the "
               "grid out of the module rather than asserting a flat number.",
        "expect": [
            "test_the_step_produces_the_number_of_pictures_that_fit_a_frame",
        ],
    },

    # --- the live audio level feeds -------------------------------------------
    {
        "label": "the input level feed no longer reaches the mascot",
        "file": _VOI,
        "old": _INPUT_HANDLER_TAIL,
        "new": "            self.window.set_input_level(level)",
        "why": "The seam removed from one side. The orb is still told, so nothing "
               "raises and the recording still works - the mascot's halo simply "
               "stops swelling on one of the two feeds, which is the half nobody "
               "would notice by watching.",
        "expect": [
            "test_the_input_level_handler_reaches_the_mascot",
        ],
    },
    {
        "label": "the mascot is read by attribute, so a turn before the companion starts crashes",
        "file": _VOI,
        "old": _GETATTR_MASCOT,
        "new": "        mascot = self.mascot",
        "why": "The `getattr` was not defensive style. The mascot is built when the "
               "desktop companion starts and a voice turn can begin before it, so "
               "the attribute read is an `AttributeError` on a real ordering - and "
               "it happens on the audio callback, where the traceback is printed "
               "and swallowed rather than shown.",
        "expect": [
            "test_a_turn_before_the_companion_started_is_not_a_crash",
        ],
    },
    {
        "label": "set_level re-renders even when the picture is unchanged",
        "file": _MSC,
        "old": _SET_LEVEL,
        "new": (
            "            level = mascot_art.quantise_level(level)\n"
            "            self._level = level\n"
            "            self._render()"),
        "why": "The shimmer the whole seam exists to prevent, as its own "
               "mutation: a mic feeds at audio rate and `_render` rasterises the "
               "whole SVG (12.4 ms measured, 16.6 ms budget), so every sample that "
               "lands inside a step spends a frame to draw the character already "
               "on screen. Mutation 7 causes the same shimmer by a different "
               "route, which is why both are here - a fix aimed at only this one "
               "would leave the other open.",
        "expect": [
            "test_a_level_finer_than_a_step_does_not_re_render",
        ],
    },
    {
        "label": "set_level stores the unquantised level, so the unchanged-picture guard cannot fire",
        "file": _MSC,
        "old": _SET_LEVEL,
        "new": (
            "            if level == self._level:\n"
            "                return\n"
            "            self._level = level\n"
            "            self._render()"),
        "why": "The store/compare pair is one property: the early return only "
               "fires if what was stored is what is compared, so dropping the "
               "quantisation *into the store* silently disables the guard rather "
               "than removing it. The quantisation still runs, the level is still "
               "drawn correctly, and every mic sample re-encodes. A witness that "
               "only counted renders would be satisfied by a stored level that is "
               "fine - so the stored value is asserted directly.",
        "expect": [
            "test_the_stored_level_is_the_quantised_one",
        ],
    },

    # --- the thinking work timeline -------------------------------------------
    {
        "label": "set_state restarts the turn on every call, so a level feed re-rasterises",
        "file": _MSC,
        "old": _STATE_GUARD,
        "new": "        if True:",
        "why": "The transition guard removed. **The obvious witness does not see "
               "it**: `_begin_turn_work` refuses while `engine.working` is already "
               "true, so the timeline is guarded a second time underneath and "
               "`_t_ref()` reads 2.9500 either way. The witness is the render/"
               "announce count instead - which is what the guard is actually for, "
               "given that `set_state` is how both a turn and a level feed arrive. "
               "`test_an_unchanged_state_does_not_restart_the_turn` is NOT on this "
               "mutation: it passes with the guard removed, and a witness that "
               "passes with the defect present reads as coverage.",
        "expect": [
            "test_an_unchanged_state_does_not_render_or_announce_again",
        ],
    },
    {
        "label": "a turn starts even when the mascot is already working",
        "file": _MSC,
        "old": _WORK_REFUSALS,
        "new": (
            "        if engine is None or engine.held:\n"
            "            return\n"
            "        if _motion_is_reduced():\n"
            "            return\n"
            "        engine.begin_work(_WORK_SECONDS)\n"
            "        self._start_frames()"),
        "why": "The inner guard, which is what the timeline test above actually "
               "watches. `begin_work` rewinds `t` to `T_DROP`, so a mascot dropped "
               "200 ms ago is mid-chew and a second `set_state(\"thinking\")` re-"
               "plays the entry pop the person just watched. This is the mutation "
               "that gives that test a witness at all.",
        "expect": [
            "test_a_mascot_dropped_a_moment_ago_chews_once_and_not_twice",
        ],
    },
    {
        "label": "a turn lets go of a mascot that is being dragged",
        "file": _MSC,
        "old": _WORK_REFUSALS,
        "new": (
            "        if engine is None or engine.working:\n"
            "            return\n"
            "        if _motion_is_reduced():\n"
            "            return\n"
            "        engine.begin_work(_WORK_SECONDS)\n"
            "        self._start_frames()"),
        "why": "`begin_work()` sets `_held = False`, so without this clause a turn "
               "starting mid-drag is felt as the mascot dropping itself from under "
               "the pointer - the one of the three refusals that is a *sensation* "
               "rather than a wrong number.",
        "expect": [
            "test_a_turn_does_not_let_go_of_a_mascot_being_dragged",
        ],
    },
    {
        "label": "the work timeline is ignored when reduced motion is on",
        "file": _MSC,
        "old": _WORK_REFUSALS,
        "new": (
            "        if engine is None or engine.held or engine.working:\n"
            "            return\n"
            "        engine.begin_work(_WORK_SECONDS)\n"
            "        self._start_frames()"),
        "why": "The timeline is an animation, and the desktop's accessibility "
               "setting is only honoured if something reads it. The mascot was "
               "built after the strip's `.reduce-motion` rules existed, so removing "
               "this check is a regression against a decision this repository has "
               "already made twice elsewhere.",
        "expect": [
            "test_the_timeline_is_the_modes_own",
        ],
    },
    {
        "label": "a turn with no engine raises instead of doing nothing",
        "file": _MSC,
        "old": _WORK_REFUSALS,
        "new": (
            "        if engine.held or engine.working:\n"
            "            return\n"
            "        if _motion_is_reduced():\n"
            "            return\n"
            "        engine.begin_work(_WORK_SECONDS)\n"
            "        self._start_frames()"),
        "why": "A machine with no layer surface and no window is not an error state "
               "here - `available()` returns `False` on this desktop and the mascot "
               "is built anyway - so the `engine is None` clause is the only thing "
               "between an ordinary `set_state(\"thinking\")` and an `AttributeError` "
               "on the turn path.",
        "expect": [
            "test_a_mascot_with_no_engine_raises_nothing",
        ],
    },
    {
        "label": "the work timeline is never given a frame tick, so the turn cannot end",
        "file": _MSC,
        "old": _WORK_REFUSALS,
        "new": (
            "        if engine is None or engine.held or engine.working:\n"
            "            return\n"
            "        if _motion_is_reduced():\n"
            "            return\n"
            "        engine.begin_work(_WORK_SECONDS)"),
        "why": "**The failure mode this is here for is a hang, not a wrong "
               "number.** Nothing else calls `engine.frame()` except "
               "`_frame_tick`, and `working` is what `_frame_tick` tests to end the "
               "turn - so a timeline with no timer enters `working`, never moves, "
               "and never stops. The obvious witness (`test_the_timeline_really_"
               "advances_and_ends_by_itself`) calls `_frame_tick` by hand and so "
               "cannot see the timer; the witness is the arming one.",
        "expect": [
            "test_the_frames_are_armed",
        ],
    },
    {
        "label": "the engine reads time.monotonic instead of its own clock",
        "file": _DRG,
        "old": _ENGINE_NOW,
        "new": (
            "    def _now(self) -> float:\n"
            "        import time\n"
            "        return time.monotonic()"),
        "why": "The last link in `_t_ref` -> `_now` -> `_clock`. Both links have to "
               "be ours: with `time.monotonic` the timeline is unreadable by a "
               "test, so this is the mutation that says so - and it is why the "
               "clock is asserted on the *engine*, not only on `_t_ref`.",
        "expect": [
            "test_the_clock_is_the_engine_own",
        ],
    },
]

MUTATIONS = _MUTATIONS