"""Three deferred mascot seams, driven through the product paths that reach them.

The mascot had **three** capabilities fully written and reached by nothing. All
three are seams rather than missing features, and all three had the same shape:
a method or constant that existed, a docstring that said so, and no caller in
`usr/`. Measured before this file, not inferred:

| seam | written | called from `usr/` | measured cost of leaving it |
|---|---|---|---|
| `mascot_art.quantise_level` / `LEVEL_STEP` | yes | **no** | 156 distinct pictures per turn, at **12.4 ms** each against a 16.6 ms frame |
| `Mascot.set_level` | yes (its own docstring said *"nothing calls this yet"*) | **no** | the halo never swelled, because the two audio feeds only drove the orb |
| `MascotMotion.begin_work` | yes, and **fully tested** in `test_mascot_drag.py` | **no** | a turn asked by voice left the mascot idle while the orb said "thinking" |

`tests/test_mascot_drag.py` is the sharp reminder of why this file exists: it
drives the engine directly and is 100% green against a `Mascot` that never calls
it. **A test that reaches past the call site to the function behind it is
testing the function.** Everything below therefore goes through `set_state` /
`set_level` / the voice handlers, and each test has a **negative control** in the
same class, because every witness in this repository's history that could not
fail has been a test whose fixture did not distinguish the two behaviours.

**What needs a display, needs one, and says so.** `mascot.available()` is `False`
on any compositor that does not speak `zwlr_layer_shell_v1` - Mutter never
implemented it - so `_build()` is refused here too. The tests that need a built
window force `_layer_shell` to a stand-in, which is what the two existing mascot
suites already do, and they skip rather than pretend when there is no display.
"""
import inspect
import sys
from pathlib import Path

import pytest

from gi.repository import GLib  # noqa: E402 - after pytest, before ROOT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import mascot, mascot_art, mascot_drag  # noqa: E402


def _display():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
        return Gtk.init_check() and Gdk.Display.get_default() is not None
    except Exception:  # noqa: BLE001
        return False


needs_display = pytest.mark.skipif(not _display(), reason="no display to build a window on")


# --- the harness, lifted from tests/test_mascot_wiring.py ------------------
#
# Duplicated rather than imported, for the reason that file records: importing
# across test modules makes a refactor of either into a red run in the other.
# **The two copies are held to each other**, so a change to one that the other
# has not absorbed is visible - see `TestTheHarnessIsNotItsOwnFixture`.

class _App:
    def __init__(self, on=True):
        self.config = type("C", (), {"get_bool": lambda s, k, d=False: on})()

    def lookup_action(self, name):
        return None

    def activate_action(self, name, parameter=None):
        pass


class _Shell:
    """Records what it was asked, and answers the two gate questions.

    `is_supported()` and `is_layer_window()` are **two different questions** and
    are separate attributes for that reason: a compositor can answer "yes, I
    speak layer-shell" and still fail to make the window a layer surface, which
    is what collapsing them into one flag would hide.

    The setters take the window as their first argument - measured from
    `mascot.py:_build`, which calls `shell.set_layer(win, shell.Layer.TOP)`. A
    hand-written stand-in with one argument raised `TypeError` on the first build
    and read as "the mascot cannot be built", which is the "a fake shaped like
    the call rather than like the thing being called" trap this file's own
    `_App` docstring records.
    """

    def __init__(self, supported=True, layered=True):
        self.supported = supported
        self.layered = layered
        self.namespaces = []
        self.margins = []
        self.Layer = type("Layer", (), {"TOP": 1})
        self.Edge = type("Edge", (), {"BOTTOM": 2, "RIGHT": 3})
        self.KeyboardMode = type("KeyboardMode", (), {"NONE": 0})

    def is_supported(self):
        return self.supported

    def is_layer_window(self, win):
        return self.layered

    def get_zwlr_layer_surface_v1(self, win):
        return 1 if self.layered else 0

    def init_for_window(self, win):
        pass

    def set_namespace(self, win, name):
        self.namespaces.append(name)

    def set_keyboard_mode(self, win, mode):
        pass

    def set_margin(self, win, edge, value):
        self.margins.append((edge, value))

    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda *a, **k: None
        raise AttributeError(name)


class _Clock:
    """A clock the test steps, installed **before** anything reads it.

    `begin_work()` captures `_drop_wall = self._now()` and `grab()` captures
    `_entry_wall`, so a fake clock installed after the call it is meant to
    intercept answers a `now` far below the captured wall; `max(0.0, ...)`
    clamps it and the timeline never advances - which reads as a turn that will
    not end. This is the trap `tests/test_mascot_wiring.py`'s own `_Clock`
    docstring records having been walked into once.
    """

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def step(self, seconds=1 / 60, frames=1):
        self.now += seconds * frames
        return self.now


def _built(monkeypatch, *, reduced=False):
    """A **real built** mascot on a private clock, with a real engine.

    `_build()` cannot use `_App` for the window: `Gtk.Window(application=...)`
    wants a real `Gtk.Application` and a stand-in is a `TypeError`. That is the
    "a fake shaped like the call rather than like the thing being called" trap,
    and it is why this returns a built object or raises.
    """
    from gi.repository import Gtk

    shell = _Shell()
    monkeypatch.setattr(mascot, "_layer_shell", lambda: (shell, ""))
    monkeypatch.setattr(mascot, "_motion_is_reduced", lambda: reduced)

    app = Gtk.Application()          # no id: claims no bus name
    app.config = type("C", (), {"get_bool": lambda s, k, d=False: True})()
    m = mascot.Mascot(app)
    assert m._build() is True, "_build() refused, so there is no wiring to test"
    clock = _Clock()
    m._engine._clock = clock
    # The frame timer is real GLib; a test that calls `_frame_tick` by hand must
    # not also have one firing underneath it.
    m._frame_timer = 1
    return m, clock


def _unbuilt():
    """A mascot with no window and no engine - what a person without the
    desktop companion gets. Every seam must be a no-op here, not a crash."""
    return mascot.Mascot(_App())


def _tick(m, clock, seconds=1 / 60, limit=600):
    """Run frames until the turn ends, the way the timer would.

    **`pose.t` is read *before* the tick.** `_frame_tick` ends the turn on the
    frame where `t` passes `grow_end + hold`, and reads the pose itself - so a
    test that reads `t` after the tick sees the **rest** pose's `t` and cannot
    tell a finished timeline from an unfinished one. That is the whole of the
    `_frame_tick returns False on the ending tick` contract.
    """
    seen = []
    for _ in range(limit):
        clock.step(seconds)
        keep = m._frame_tick()
        seen.append(keep)
        if not keep:
            return seen
    raise AssertionError(f"the turn never ended in {limit} frames")


# ── Seam 1: the level quantisation ──────────────────────────────────────────

class TestTheLevelIsQuantised:
    """`LEVEL_STEP` is load-bearing because the picture is a file.

    The measurement that decided it is in `mascot_art`'s own comment and in
    `harness-study/HARVEST-COUCOU.md` PART 9: a Shell actor shows this art
    through `St.TextureCache.load_file_sync`, which costs **12.4 ms** per
    changed frame against a **16.6 ms** budget. So the quantity that matters is
    *how many distinct pictures a turn produces*, and it was measured by
    hashing rasters - not by counting strings, which is why an earlier probe
    reporting "159 distinct strings / smallest visible change 0.0000" was
    measuring the wrong thing entirely.
    """

    def test_the_step_produces_the_number_of_pictures_that_fit_a_frame(self, monkeypatch):
        """The table the constant was chosen from, re-derived here.

        Asserted as a **count of distinct pictures**, because that is the
        quantity the constant is for.

        **The unquantised count is 156 or 157 depending on the grid, and both
        are asserted rather than one of them.** A turn's level runs 0.0 to 1.0
        inclusive, which is 1001 points and **157** pictures unquantised; the
        committed table's 156 is the 1000-point grid `0.0..0.999`. The first
        version of this test asserted 156 over a 1001-point grid and failed by
        one - and "fixing" it to 157 would have made the docstring's number a
        lie. **The quantised counts are 51 and 21 on either grid**, which is
        why they are the load-bearing pair: they are what the constant buys,
        and they are what does not depend on the grid.

        **The unquantised column is measured by neutralising `quantise_level`,
        not by calling something else.** The first version of this test passed
        `step=0` to a helper and got **51**, which is this file's own subject:
        the quantisation lives inside `svg()`, so there is no route through it
        that is not quantised, and a column labelled "none" quietly measured
        the constant it was there to justify.
        """
        def distinct(monkeypatch, quantise, points):
            monkeypatch.setattr(mascot_art, "quantise_level", quantise)
            return {mascot_art.svg("listening", level=i / 1000.0)
                    for i in range(points)}

        # **The real function is bound here, before anything is patched.**
        # `monkeypatch` undoes only at teardown, so inside one test
        # `mascot_art.quantise_level` is *whatever the last call installed*.
        # Passing `mascot_art.quantise_level` as a later argument therefore
        # passes the coarse lambda from the previous assertion and the loop
        # measured 21 where it meant 51 - a late-bound read of a patched name,
        # which is this repository's "patch a name where it is read" rule
        # pointed the other way.
        real_quantise = mascot_art.quantise_level
        ident = lambda v: v
        coarse = lambda v: round(v / 0.05) * 0.05

        assert mascot_art.LEVEL_STEP == pytest.approx(0.02)

        # Grid-independent: the two numbers the constant is chosen between.
        for points in (1000, 1001):
            assert len(distinct(monkeypatch, real_quantise, points)) == 51
            assert len(distinct(monkeypatch, coarse, points)) == 21

        # Grid-dependent, and pinned on the grid it was measured on.
        assert len(distinct(monkeypatch, ident, 1000)) == 156
        assert len(distinct(monkeypatch, ident, 1001)) == 157

        # And the constant is the reason: a step ten times coarser is not a
        # rounding error on the count, it is a visibly coarser halo.
        snapped = distinct(monkeypatch, real_quantise, 1001)
        assert len(snapped) < len(distinct(monkeypatch, ident, 1001))

    def test_a_level_inside_one_step_is_the_same_picture_as_its_neighbours(self):
        """Two levels a thousandth apart inside one step must not differ.

        This is the claim `LEVEL_STEP` makes and the reason the sprite does not
        shimmer: the halo radius is an `int`, so a step below what the drawing
        can show is a re-encode with no visible change.
        """
        low = mascot_art.svg("listening", level=0.400)
        high = mascot_art.svg("listening", level=0.401)
        assert low == high, "0.401 is inside 0.40's step but drew something else"

    def test_quantising_rounds_so_a_level_at_a_boundary_does_not_re_encode(self):
        """`round`, not `floor` - and the boundary case is the whole reason.

        A level hovering at a step edge re-encodes forever under `floor`,
        because every fresh sample rounds to a *different* bucket either side of
        a value that looks the same. 0.019 and 0.021 must therefore both land in
        the same step.
        """
        assert mascot_art.quantise_level(0.019) == mascot_art.quantise_level(0.021)
        assert mascot_art.quantise_level(0.019) == pytest.approx(0.02)

    def test_the_ends_are_the_ends(self):
        assert mascot_art.quantise_level(0.0) == 0.0
        assert mascot_art.quantise_level(-3.0) == 0.0
        assert mascot_art.quantise_level(1.0) == 1.0
        assert mascot_art.quantise_level(4.0) == 1.0

    def test_the_stored_level_is_the_quantised_one(self):
        """**What is stored is what is drawn**, through the product path.

        A finer value stored and a coarser value drawn would make the early
        return fire on a level that *should* have re-rendered, and the halo
        would freeze mid-turn at whatever the first sample happened to be.
        """
        m = _unbuilt()
        m._state = "listening"
        m.set_level(0.401)
        assert m._level == pytest.approx(0.40)

    def test_a_level_finer_than_a_step_does_not_re_render(self):
        """The early return, measured on the number of renders and not on the value.

        A microphone feeds at audio rate, so without this every sample would
        rebuild the SVG and re-rasterise the picture - on a route measured at
        12.4 ms per changed frame.
        """
        m = _unbuilt()
        m._state = "listening"
        rendered = []
        m._render = lambda pose=None: rendered.append(m._level)

        m.set_level(0.40)
        m.set_level(0.405)          # same step
        m.set_level(0.409)          # still the same step
        assert rendered == [pytest.approx(0.40)], f"re-rendered {len(rendered)} times"

        m.set_level(0.42)           # the next step
        assert len(rendered) == 2, "a step change did not re-render"

    def test_a_level_in_a_state_whose_halo_does_not_move_is_ignored(self):
        """The state rule stays in the module that draws the halo.

        `app/voice.py` calls `set_level` unconditionally - `one question, one
        owner` - so the guard has to live here. A level arriving while the mascot
        is `thinking` must not become a stored level nothing draws.
        """
        m = _unbuilt()
        m._state = "thinking"
        rendered = []
        m._render = lambda pose=None: rendered.append(m._level)
        m.set_level(0.8)
        assert rendered == []
        assert m._level == 0.0

    def test_quantisation_happens_inside_the_renderer_too(self):
        """`svg()` snaps the level itself, so a caller cannot bypass it.

        The quantisation lives in `mascot_art` rather than only in `set_level`
        because **that is where the level-to-pixels mapping is**, so the two
        cannot disagree about which levels are distinguishable. This is the half
        that holds if somebody calls `svg()` directly.
        """
        assert mascot_art.svg("listening", level=0.5001) == \
            mascot_art.svg("listening", level=0.5)
        assert mascot_art.svg("listening", level=0.5001) != \
            mascot_art.svg("listening", level=0.53)


# ── Seam 2: the live level feeds ────────────────────────────────────────────

class TestTheAudioFeedsReachTheMascot:
    """`set_level` had a docstring saying nothing called it, and named the two
    lines that should. Those two lines are the whole of this seam."""

    @staticmethod
    def _voice(level_calls):
        """A `VoiceMixin` with the mascot attribute the real application sets.

        `app/application.py:1844` does `self.mascot = mascot.Mascot(self)`, so
        the attribute name is read off **the real application**, not invented.
        The mixin is driven through its own bound handlers - `self.window` first,
        then the mascot - because a test that calls `set_level` directly is not a
        test that the feed reaches it.
        """
        from shani_chronoa.app import voice

        class _App:
            def __init__(self):
                self.window = None
                self.config = type("C", (), {"get_bool": lambda s, k, d=False: True})()
                self.mascot = type("M", (), {"set_level": level_calls.append})()

            def lookup_action(self, name):
                return None

            def activate_action(self, name, parameter=None):
                pass

        m = voice.VoiceMixin()
        m.__dict__.update(_App().__dict__)
        return m

    def test_the_input_level_handler_reaches_the_mascot(self):
        calls = []
        m = self._voice(calls)
        assert m._on_input_level_main(0.42) == GLib.SOURCE_REMOVE
        assert calls == [0.42]

    def test_the_playback_level_handler_reaches_the_mascot(self):
        calls = []
        m = self._voice(calls)
        assert m._on_playback_level_main(0.61) == GLib.SOURCE_REMOVE
        assert calls == [0.61]

    def test_the_orb_is_still_told(self):
        """**The control for the two tests above.**

        Each handler did one job - the orb - and the mascot call was added
        beside it. A seam that *replaced* the orb's line would pass both tests
        above while the halo the person has been watching for two releases
        stopped moving entirely.
        """
        told = []
        calls = []
        m = self._voice(calls)

        # The methods take `self`: a function on a class binds on attribute
        # access, so a bare `lambda v: ...` raises `TypeError` for two
        # arguments. That is the same "a fake shaped like the call rather than
        # like the thing being called" trap as `_App` above - and it reads as a
        # product defect, because the traceback names `voice.py`.
        class _W:
            def set_input_level(self, v):
                told.append(("input", v))

            def set_playback_level(self, v):
                told.append(("playback", v))

        m.window = _W()
        m._on_input_level_main(0.42)
        m._on_playback_level_main(0.61)
        assert told == [("input", 0.42), ("playback", 0.61)]

    def test_a_turn_before_the_companion_started_is_not_a_crash(self):
        """The mascot is built when the desktop companion starts, and a voice
        turn can begin before it. `getattr`, not `self.mascot`."""
        from gi.repository import GLib
        from shani_chronoa.app import voice

        class _Bare:
            # **Instance attributes, set in `__init__`, not class ones.** The
            # fixture is applied with `m.__dict__.update(_Bare().__dict__)`,
            # and `__dict__` does not carry class attributes - so `window = None`
            # written here as a class attribute left `m` with **no `window`
            # at all**, and the handler's own unguarded `if self.window:` raised
            # `AttributeError`. That read as "the mascot cannot be built" and was
            # nothing of the kind: it was a fake shaped unlike the call.
            def __init__(self):
                self.window = None
                self.config = type(
                    "C", (), {"get_bool": lambda s, k, d=False: True})()

            def lookup_action(self, name):
                return None

            def activate_action(self, name, parameter=None):
                pass

        m = voice.VoiceMixin()
        m.__dict__.update(_Bare().__dict__)
        assert not hasattr(m, "mascot")
        assert m._on_input_level_main(0.42) == GLib.SOURCE_REMOVE

    def test_a_mascot_that_cannot_draw_does_not_fail_the_recording(self):
        """Decoration must not be able to fail a turn.

        The two lines above `_level_to_mascot` have already told the window, so
        the only thing left to protect is the recording itself.
        """
        from gi.repository import GLib
        from shani_chronoa.app import voice

        class _Broken:
            def set_level(self, _level):
                raise RuntimeError("no surface")

        m = voice.VoiceMixin()
        m.__dict__.update({
            "window": None,
            "config": type("C", (), {"get_bool": lambda s, k, d=False: True})(),
            "mascot": _Broken(),
            "lookup_action": lambda name: None,
            "activate_action": lambda name, parameter=None: None,
        })
        assert m._on_playback_level_main(0.61) == GLib.SOURCE_REMOVE

    def test_the_handler_removes_its_own_idle_source(self):
        """`GLib.idle_add` keeps a source alive while it returns True, so a
        handler returning True is fired again for ever. **`GLib.SOURCE_REMOVE`
        is `False`**, measured - the first version of these tests asserted
        `is True` and read the shipped, correct code as broken.

        So the return value is asserted **against the constant** rather than a
        literal, which is also the shape that survives a PyGObject whose
        `SOURCE_REMOVE` is `0` rather than `False`.
        """
        from gi.repository import GLib

        calls = []
        m = self._voice(calls)
        assert GLib.SOURCE_REMOVE is False
        assert m._on_input_level_main(0.42) == GLib.SOURCE_REMOVE
        assert m._on_playback_level_main(0.42) == GLib.SOURCE_REMOVE

    def test_it_is_one_call_beside_each_feed_and_not_a_second_subscription(self):
        """Two audio subscriptions would be a second source of truth.

        The two handlers are the feeds; a new one would have to be kept in step
        with the microphone and the player both changing. So the level reaches
        the mascot from **these two lines and no others**, and this is asserted
        against the AST rather than by reading - the same shape as
        `test_stale_cross_references`.
        """
        import ast

        source = (ROOT / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "app" / "voice.py")
        tree = ast.parse(source.read_text())
        callers = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "_level_to_mascot"]
        assert len(callers) == 2, (
            f"{len(callers)} call sites for _level_to_mascot; the two feeds and "
            "its own definition is the whole of it")


# ── Seam 3: the thinking timeline ───────────────────────────────────────────

class TestThinkingRunsTheWorkTimeline:
    """`begin_work()` existed, was **fully tested** in `test_mascot_drag.py`, and
    had no caller in `usr/`. A turn asked by voice reaches `set_state` with no
    pointer near the sprite, so the engine could chew after a drop and never for
    a turn."""

    @needs_display
    def test_going_to_thinking_starts_the_timeline(self, monkeypatch):
        m, clock = _built(monkeypatch)
        assert not m._engine.working
        m.set_state("thinking")
        assert m._engine.working, "the mascot did not start working"
        assert m._engine.active
        assert not m._engine.held, "a turn must not read as a drag in progress"

    @needs_display
    def test_the_timeline_really_advances_and_ends_by_itself(self, monkeypatch):
        m, clock = _built(monkeypatch)
        m.set_state("thinking")
        _tick(m, clock)
        assert not m._engine.working, "the turn never ended"
        assert m._engine.active is False
        # And it ended on the *rest* pose, which is what a person sees.
        pose = m._engine.frame()
        assert pose.sprite_visible
        assert pose.progress == pytest.approx(0.0)

    @needs_display
    def test_the_frames_are_armed(self, monkeypatch):
        """**Without the frame timer the turn cannot end.**

        Nothing else calls `engine.frame()` except `_frame_tick`, and `working`
        is what `_frame_tick` tests to end the turn - so a timeline with no timer
        enters `working`, never moves, and never stops. `_on_drag_begin` starts
        the timer; a turn is the *other* way in, so this has to start it too.
        """
        started = []
        m, clock = _built(monkeypatch)
        m._frame_timer = 0
        m._start_frames = lambda: started.append(True)
        m.set_state("thinking")
        assert started == [True], "the timeline was started with no frame timer"

    @needs_display
    def test_an_unchanged_state_does_not_restart_the_turn(self, monkeypatch):
        """`set_state` is how a level feed and a turn both reach here.

        Restarting on every call would re-play the entry pop on every frame of a
        turn, so the seam fires on the **transition** and the transition is
        asserted by re-setting the same state.

        **The witness is `engine._t_ref()`, not `engine._t`.** `_t` is the
        integrator's position and only `_simulate_to`, inside `frame()`, ever
        advances it - so after `begin_work()` with no frame it still reads
        `T_DROP` however long the clock ran, and the first version of this test
        asserted "`_t` moved" about a number that cannot move until a frame is
        drawn. `_t_ref()` reads the clock and answers *"where on the timeline is
        this turn now"*, which is the question: `begin_work` resets
        `_drop_wall` to now, so re-running it **rewinds `_t_ref()` back to
        `T_DROP`** - which is exactly the re-played entry pop.
        """
        m, clock = _built(monkeypatch)
        m.set_state("thinking")
        began = m._engine._t_ref()
        clock.step(1.0)
        # Before the second call: the clock alone has moved it on by a second.
        assert m._engine._t_ref() == pytest.approx(began + 1.0), \
            "the fake clock is not driving the timeline, so this proves nothing"
        m.set_state("thinking")
        assert m._engine._t_ref() == pytest.approx(began + 1.0), \
            f"an unchanged state rewound the timeline to {m._engine._t_ref()}"

    @needs_display
    def test_an_unchanged_state_does_not_render_or_announce_again(self, monkeypatch):
        """The same guard, asked about what it *costs* rather than where it lands.

        The test above cannot see this guard's absence. `_begin_turn_work` refuses
        while `engine.working`, so with the guard removed the timeline is still
        refused and `_t_ref()` reads the same number either way - measured at
        2.9500 both ways. **A double guard makes the inner one unobservable**,
        and this is the witness for the outer one.

        What the guard is actually for is the work: `_render` rasterises the
        whole SVG through `GdkPixbuf` and hands back a fresh texture (measured at
        12.4 ms for a changed frame, against a 16.6 ms budget), and `_announce`
        rewrites the widget's accessible name. A level feed calls `set_state` at
        audio rate, so a guard that only skips the timeline still re-rasterises
        the whole mascot on every sample.

        **Counting is the right instrument here and is not the trap this file
        warns about elsewhere.** Elsewhere the count was equal for an appending
        and a rebuilding map; here two different builds must produce different
        *counts of work*, which is the claim. The control is the first call -
        without it a `_render` that never runs at all passes.

        `_announce` cannot be read back - `get_accessible_property` does not exist
        on this PyGObject, which this file records elsewhere - so it is counted
        too rather than asserted on a value nobody can read.
        """
        m, clock = _built(monkeypatch)
        rendered, announced = [], []
        real_render, real_announce = m._render, m._announce
        m._render = lambda pose=None: (rendered.append(pose), real_render(pose))[1]
        m._announce = lambda: (announced.append(True), real_announce())[1]

        m.set_state("thinking")
        assert (len(rendered), len(announced)) == (1, 1), \
            f"the transition rendered/announced {len(rendered)}/{len(announced)}, " \
            "so the control is not there and this test proves nothing"

        m.set_state("thinking")
        assert (len(rendered), len(announced)) == (1, 1), \
            f"an unchanged state rendered {len(rendered)} times and announced " \
            f"{len(announced)} times"

        # And a genuinely different state still works, so the guard cannot be
        # satisfied by a `set_state` that does nothing at all.
        m.set_state("speaking")
        assert (len(rendered), len(announced)) == (2, 2), \
            "a real transition stopped reaching the mascot"

    @needs_display
    def test_a_turn_does_not_let_go_of_a_mascot_being_dragged(self, monkeypatch):
        """`begin_work()` sets `_held = False`.

        A turn starting mid-drag would therefore be *felt* as the mascot letting
        go of itself under the pointer, which is the opposite of what a person
        watching it wants.
        """
        m, clock = _built(monkeypatch)
        m._on_drag_begin(None, 100.0, 100.0)
        assert m._engine.held
        m.set_state("thinking")
        assert m._engine.held, "a turn started released the mascot"
        assert not m._engine.working

    @needs_display
    def test_a_mascot_dropped_a_moment_ago_chews_once_and_not_twice(self, monkeypatch):
        """`begin_work` rewinds `t` to `T_DROP` **and re-plays the entry pop**.

        A mascot dropped 200 ms ago is mid-chew; starting again would rewind the
        timeline the person is watching. One turn, one timeline - and the
        timeline still ends on its own, because `_frame_tick` stops it.
        """
        m, clock = _built(monkeypatch)
        m._on_drag_begin(None, 100.0, 100.0)
        m._on_drag_end(None, 0.0, 0.0)
        clock.step(0.2)
        m._frame_tick()
        reached = m._engine._t
        assert m._engine.working
        m.set_state("thinking")
        assert m._engine._t >= reached, "the drop's timeline was rewound"

    @needs_display
    def test_the_timeline_is_the_modes_own(self, monkeypatch):
        """Reduced motion is the same refusal the bob makes.

        `_motion_is_reduced()` is read per call, so a person who flips the
        setting mid-turn is obeyed without a restart, and this file's own `_built`
        makes it a parameter so the refusal can be exercised rather than assumed.
        """
        m, clock = _built(monkeypatch, reduced=True)
        m.set_state("thinking")
        assert not m._engine.working, "the turn animated anyway"

    def test_a_mascot_with_no_engine_raises_nothing(self):
        """What a person without the desktop companion gets.

        No window means no engine and no window, and `set_state` is still called
        for every turn - so a seam that assumed an engine would make the *state*
        fail, which is the whole of what the mascot is decoration for.
        """
        m = _unbuilt()
        m.set_state("thinking")
        assert m._state == "thinking"
        assert m._engine is None

    @needs_display
    def test_it_is_the_thinking_transition_and_not_every_state(self, monkeypatch):
        """The control on the state rule.

        `thinking` is the only state that means work is under way. If the seam
        fired on `listening`, the mascot would chew for the whole of a recording
        - and the test below on `set_state` as a whole would not see it.
        """
        m, clock = _built(monkeypatch)
        for state in mascot_art.states():
            if state == "thinking":
                continue
            m.set_state(state)
            assert not m._engine.working, f"{state!r} started the work timeline"

    @needs_display
    def test_a_second_turn_after_the_first_one_ended_starts_again(self, monkeypatch):
        """**The control for the "chews once" test above.**

        Refusing to start whenever the mascot is busy would pass that test for
        the wrong reason: the mascot would never chew at all. So two turns, one
        after the other, both run - and the first has to *end* for the second to
        be reachable, which is why this also witnesses that a turn terminates.
        """
        m, clock = _built(monkeypatch)
        m.set_state("thinking")
        _tick(m, clock)
        assert not m._engine.working

        m.set_state("speaking")
        m.set_state("thinking")
        assert m._engine.working, "the second turn never started"
        _tick(m, clock)
        assert not m._engine.working


# ── the harness, held to itself ────────────────────────────────────────────

class TestTheHarnessIsNotItsOwnFixture:
    """Two of the fixtures in this file could make a defect look like a pass.

    Both are the shape this repository records most: **a fixture that does not
    reproduce production answers a different question from the one the test
    asks.** So each is proved to be capable of failing, and the proof is that the
    thing the fixture stands for is the thing being measured.
    """

    def test_the_built_mascot_really_has_an_engine_and_a_window(self, monkeypatch):
        """If `_built` returned a mascot with no engine, every seam in the
        thinking class would raise rather than pass - so this is the floor. What
        it cannot be is the proof, and the proof is that the engine exists at all:
        **`hasattr(Mascot, "_begin_turn_work")` would be True on a `Mascot` that
        never calls it**, which is the whole defect the third seam is about.
        """
        m, _ = _built(monkeypatch)
        assert m._engine is not None
        assert m._win is not None
        assert m._engine.active is False and m._engine.working is False

    def test_the_clock_is_the_engine_own(self):
        """`_built` assigns `_clock`, and `MascotMotion` reads it through a
        **two-link chain** - so this is a check that both links hold, not a
        restatement of a number.

        **A clock assigned to the wrong attribute would leave the timeline on
        `time.monotonic`**, and the tests would still pass - they would simply
        be measuring the wall clock, which advances far enough in a few hundred
        iterations to make a timeline end. So the assignment is checked against
        the engine's own reader.

        **`_t_ref` does not call `_clock` itself** - it calls `self._now()`, and
        `_now()` is what calls `self._clock()`. The first version of this test
        asserted on `_t_ref`'s own AST and failed on **correct code**, because
        the reader is one call deeper. That is why both links are checked here:
        asserting one half drops the other, and the half dropped is the half
        that would let the timeline fall back to the wall clock.
        """
        import ast
        import textwrap

        def calls_in(func, *names):
            body = textwrap.dedent(inspect.getsource(func))
            tree = ast.parse(body)
            return {
                ast.unparse(n.func) for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in names
            }

        # Link 1: the reference asks the engine for the time.
        assert "self._now" in calls_in(mascot_drag.MascotMotion._t_ref, "_now"), \
            "_t_ref does not read the time through the engine"
        # Link 2: and the engine asks its own clock for it, not `time.monotonic`.
        assert "self._clock" in calls_in(mascot_drag.MascotMotion._now, "_clock"), \
            "_now does not read the engine's own clock"
        assert "_clock" in inspect.getsource(mascot_drag.MascotMotion)

    def test_the_tick_helper_steps_the_clock_before_the_tick(self):
        """The ordering the helper documents, asserted on the **code** and not
        on the text.

        The first version of this read `inspect.getsource(_tick)` including the
        docstring, and the docstring names `_frame_tick` **before** the first
        `clock.step` - so a helper whose body had the order backwards passed its
        own check. Which is this repository's own "a check that matches its own
        input reports nothing", reached through a docstring.

        So the docstring is stripped and the two calls are located in the body.
        """
        import ast
        import textwrap

        source = inspect.getsource(_tick)
        tree = ast.parse(textwrap.dedent(source))
        body = tree.body[0].body
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant):
            body = body[1:]                       # the docstring
        order = []
        for node in body:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    order.append(inner.func.attr)
        assert "step" in order and "_frame_tick" in order
        assert order.index("step") < order.index("_frame_tick"), \
            f"the clock is stepped {order} - the turn cannot advance"

    def test_every_seam_has_a_negative_control_in_this_file(self):
        """No witness that passes both ways.

        A mutation set proves this from the outside, but a set can only prove it
        for the mutations somebody thought of. This is the cheap in-file half:
        each of the three seams is asserted **and its opposite state is asserted
        in the same class**, so a test that cannot fail is visible by reading the
        class rather than only by running a mutation.

        Walks the classes rather than `getattr(module, name)` - the first version
        looked the names up at module scope, found nothing, and failed for that
        reason while reporting it as a missing control.
        """
        here = {cls.__name__: {n for n in vars(cls) if n.startswith("test_")}
                for cls in (TestTheLevelIsQuantised,
                            TestTheAudioFeedsReachTheMascot,
                            TestThinkingRunsTheWorkTimeline)}
        for seam, control in (
                ("test_a_level_finer_than_a_step_does_not_re_render",
                 "test_the_stored_level_is_the_quantised_one"),
                ("test_the_orb_is_still_told",
                 "test_the_input_level_handler_reaches_the_mascot"),
                ("test_an_unchanged_state_does_not_restart_the_turn",
                 "test_a_second_turn_after_the_first_one_ended_starts_again"),
        ):
            found = [cls for cls, names in here.items() if seam in names]
            assert found, f"{seam} is not in any class in this file"
            assert control in here[found[0]], \
                f"{control} is not in {found[0]} beside {seam}"

        # And the third class is the one with the pair, because "does not start
        # twice" and "starts again next turn" are opposite claims about the same
        # guard - and only the second can tell a guard that refuses always from
        # a guard that refuses when it should not.
        assert len(here) == 3