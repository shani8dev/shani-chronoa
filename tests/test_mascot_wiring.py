"""The drag engine, wired to the window.

`mascot_drag` is a **pure** object: it measures a stage and answers poses, and it
owns no DOM and draws nothing. Every test in `tests/test_mascot_drag.py` drives
it that way, and every one of them would stay green if this module never called
it at all - the same gap the gaze port found, where three wiring mutations
survived a fully green engine suite.

So the population here is different, and the two rules that make it different are:

- **handlers are invoked through the ones `_build()` recorded**, not by name. A
  `lambda *_: None` in a connect line is a controller with a signal and no
  behaviour, and nothing else in the suite can see that.
- **`_build()` really runs.** `_build()` cannot use a `SimpleNamespace` for an
  application - `Gtk.Window(application=...)` wants a real `Gtk.Application` and
  the stand-in is a `TypeError`. That is the trap of a fake shaped like the call
  rather than like the thing being called.

What is measured rather than asserted:

- the stage arithmetic (`tests/test_mascot_drag.py` pins the *engine's* numbers
  against a 212x212 stage; this file pins that the window really is that stage);
- that a turn ends by itself, and that the hold window is geometrically inert;
- that the margin clamp never crops the widest thing the art draws.
"""

import inspect
import textwrap

import pytest

from shani_chronoa import mascot, mascot_drag

# --- the shared harness, lifted from tests/test_mascot.py ------------------
#
# Duplicated rather than imported: `tests/test_mascot.py` is a 1000-line file
# with its own module-level fixtures, and importing across test modules makes a
# refactor of either into a red run in the other.

def _display():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, Gtk
        return Gtk.init_check() and Gdk.Display.get_default() is not None
    except Exception:  # noqa: BLE001
        return False


needs_display = pytest.mark.skipif(not _display(),
                                   reason="no display to build a window on")


class _App:
    """For the tests that never build a window."""

    def __init__(self, on=True):
        self.config = type("C", (), {"get_bool": lambda s, k, d=False: on})()

    def lookup_action(self, name):
        return None

    def activate_action(self, name, parameter=None):
        pass


class _Shell:
    """Records what it was asked, and answers the two gate questions."""

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


def _wired(monkeypatch, *, layer_shell=None):
    """A real built mascot, with its controllers recorded by name."""
    from gi.repository import Gtk

    recorded = {}
    for kind in (Gtk.GestureDrag, Gtk.EventControllerMotion):
        original = kind.connect

        def spy(self, signal, handler, *a, _o=original, **k):
            recorded[(type(self).__name__, signal)] = handler
            return _o(self, signal, handler, *a, **k)

        monkeypatch.setattr(kind, "connect", spy)

    shell = _Shell(supported=True, layered=True)
    monkeypatch.setattr(mascot, "_layer_shell",
                        lambda: (shell if layer_shell is None else layer_shell, ""))
    monkeypatch.setattr(mascot, "_motion_is_reduced", lambda: False)

    app = Gtk.Application()          # no id: claims no bus name
    app.config = type("C", (), {"get_bool": lambda s, k, d=False: True})()
    m = mascot.Mascot(app)
    assert m._build() is True, "_build() refused, so there is no wiring to test"
    return m, recorded, shell


class _Clock:
    """A clock the test drives, installed **before** anything reads it.

    `MascotMotion` captures `_drop_wall = self._now()` at `release()` and
    `_entry_wall` at `grab()`, and `_t_ref` is `T_DROP + (now - drop_wall)`. A
    fake clock installed *after* a grab therefore answers a `now` far below the
    captured wall, `max(0.0, ...)` clamps it, and the timeline never advances -
    which reads as a turn that will not end. **That is what the first version of
    the lifecycle test did**, and it is the same shape as installing a monkeypatch
    after the call it is meant to intercept.
    """

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def step(self, seconds=0.016, frames=1):
        self.now += seconds * frames
        return self.now


def _on_a_controlled_clock(m):
    """Swap the engine's clock for one the test steps by hand."""
    clock = _Clock()
    m._engine._clock = clock
    return clock


# --- the stage --------------------------------------------------------------

def test_the_stage_is_the_sprite_plus_travel_on_both_sides():
    """`_STAGE` is arithmetic, and the **expression** is asserted, not its value.

    The first version asserted `_STAGE == SIZE + 2*_STAGE_TRAVEL`, which a
    hand-typed `_STAGE = 212` satisfies today and stops satisfying the day `SIZE`
    or `_STAGE_TRAVEL` moves - so it was a test that could not fail, and the
    mutation run said so. This reads the assignment out of the module's AST, which
    is the claim actually being made: *this is computed, not typed*.
    """
    import ast
    import pathlib
    import shani_chronoa.mascot as mod
    tree = ast.parse(pathlib.Path(mod.__file__).read_text())
    assigned = [n.value for n in tree.body
                if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "_STAGE" for t in n.targets)]
    assert len(assigned) == 1, f"_STAGE is assigned {len(assigned)} times"
    assert ast.unparse(assigned[0]) == "SIZE + 2 * _STAGE_TRAVEL", \
        ast.unparse(assigned[0])
    # And the consequence, because the arithmetic is only worth having if the
    # result is the number every measured engine figure was taken against.
    assert mascot._STAGE == mascot.SIZE + 2 * mascot._STAGE_TRAVEL
    assert mascot._STAGE == 212
    assert mascot.SIZE == 132 and mascot._STAGE_TRAVEL == 40


def test_the_window_really_is_the_stage():
    """The window is the stage, not a frame around a 132px sprite.

    The test that matters is that the window's own default size carries the
    stage, because a window sized `SIZE` would let the engine believe it has
    travel it does not have - every mechanic in `mascot_drag` is a fraction of the
    stage it is handed.
    """
    import ast
    source = textwrap.dedent(inspect.getsource(mascot.Mascot._build))
    call = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", "") == "set_default_size")
    assert [ast.unparse(a) for a in call.args] == ["_STAGE", "_STAGE + _BUBBLE_PX"]


@needs_display
def test_the_window_margins_stay_at_the_value_the_bob_returns_to(monkeypatch):
    """**Every** margin, not "one of them is `_BASE_MARGIN`".

    The first version asserted `_BASE_MARGIN in {values}`, which the right-hand
    margin satisfies on its own - so moving only the *bottom* margin, which is
    the one the bob reads, left the test green. The mutation run found it.
    """
    m, _, shell = _wired(monkeypatch)
    try:
        assert shell.margins, "no margin was ever set"
        wrong = [(edge, value) for edge, value in shell.margins
                 if value != mascot._BASE_MARGIN]
        assert wrong == [], (
            f"these window margins are not _BASE_MARGIN ({mascot._BASE_MARGIN}): "
            f"{wrong}. `_bob_tick` returns there every "
            f"{mascot._BOB_EVERY_MS}ms, so the bob's resting point and its return "
            "value would disagree")
    finally:
        m.stop()


def test_the_resting_picture_sits_where_the_stage_centre_puts_it(monkeypatch):
    """The sprite rests at the stage centre, so it rests TRAVEL px in.

    Before this change the window was `SIZE` wide and the mascot sat `_BASE_MARGIN`
    from the right edge. The window is now `SIZE + 2*TRAVEL` wide and the mascot
    sits at the stage centre inside it, which is `_STAGE_TRAVEL` further in - a
    deliberate, deterministic shift. **This test pins the arithmetic rather than
    the old pixel**, so it keeps meaning if TRAVEL moves.
    """
    rest = mascot_drag.MascotMotion(mascot._STAGE, mascot._STAGE,
                                    mascot.SIZE).frame()
    left = rest.x - rest.d * rest.sx / 2.0
    assert left == pytest.approx(mascot._STAGE_TRAVEL, abs=0.5)
    assert rest.d * rest.sx <= mascot._STAGE


# --- the gesture ------------------------------------------------------------

@needs_display
def test_the_drag_gesture_is_connected_to_the_window(monkeypatch):
    """Three signals, and the handlers `_build()` recorded rather than named."""
    m, recorded, _ = _wired(monkeypatch)
    try:
        drag = {sig for (cls, sig) in recorded if cls == "GestureDrag"}
        assert drag == {"drag-begin", "drag-update", "drag-end"}, sorted(drag)
        handlers = [recorded[("GestureDrag", s)]
                    for s in ("drag-begin", "drag-update", "drag-end")]
        for handler, name in zip(handlers, ("_on_drag_begin", "_on_drag_update",
                                            "_on_drag_end")):
            assert handler.__func__ is getattr(mascot.Mascot, name)
    finally:
        m.stop()


@needs_display
def test_the_gesture_lives_on_the_window_not_the_picture(monkeypatch):
    """Why it is on the window, measured rather than asserted.

    `set_size_request` is `pose.d * sx * sy`, and **the sprite really does resize**
    - 51 distinct diameters across one turn, from 29.8 px at the progress bar to
    142.2 px at the top of the grow. So the picture's origin moves under the
    pointer for most of a turn, and a gesture on it would be handed a different
    stage point every frame. **The demonstration is the measurement**: if `d` were
    constant the argument would not hold and this test would say so.
    """
    m, recorded, _ = _wired(monkeypatch)
    try:
        clock = _on_a_controlled_clock(m)
        engine = m._engine
        begin = recorded[("GestureDrag", "drag-begin")]
        begin(None, mascot._STAGE / 2.0, mascot._STAGE / 2.0)
        end = recorded[("GestureDrag", "drag-end")]
        end(None, 0.0, 0.0)

        diameters = set()
        for _ in range(600):
            clock.step(0.016)
            diameters.add(round(engine.frame().d, 3))
        assert len(diameters) > 5, (
            f"the sprite barely resizes ({len(diameters)} diameters), so a "
            "picture-origin gesture would not move - and this test's premise "
            "would be false")
        assert min(diameters) < mascot.SIZE < max(diameters), (
            f"expected the sprite to shrink below {mascot.SIZE} and grow above "
            f"it; saw {min(diameters)}..{max(diameters)}")
    finally:
        m.stop()


@needs_display
def test_the_drag_gesture_is_not_on_the_picture(monkeypatch):
    """Asked of the **widgets**, not of the source.

    The first version scanned `_build`'s AST for `add_controller` calls and read
    the *variable names* - so it asserted `"win" in added`, which passes the
    moment someone renames the local and fails for no reason at all. A widget's own
    controller list is the thing the argument is about.
    """
    m, _, _ = _wired(monkeypatch)
    try:
        def attached(widget):
            return [type(c).__name__ for c in widget.observe_controllers()]

        on_window = attached(m._win)
        on_picture = attached(m._picture)
        assert "GestureDrag" in on_window, on_window
        assert "GestureDrag" not in on_picture, on_picture
        # The picture keeps the click - it is what starts a listening turn - so
        # this is not "controllers were never attached to either". **The reverse
        # direction is deliberately not asserted**: a `Gtk.Window` carries a
        # `GestureClick` of its own, so "the window has no click" is false and a
        # test that claimed it would be testing GTK rather than this module.
        assert "GestureClick" in on_picture, on_picture
    finally:
        m.stop()


# --- a turn, end to end -----------------------------------------------------

@needs_display
def test_a_drag_begins_the_frames_and_a_release_ends_the_turn(monkeypatch):
    """The whole lifecycle, through the recorded handlers, on a driven clock.

    The clock is installed **before** the grab - see `_Clock` - and the test calls
    `_frame_tick` directly rather than pumping a real `GLib` timeout, because a
    pump that waits on a wall-clock deadline is the harness fault `AGENTS.md`
    records for the 180-second asks.

    **The last tick returns `False`, and that is the assertion that matters**: the
    tick that ends the turn is the tick that stops the timer, and a test that
    demanded `True` from every tick in the loop would have failed on the turn
    ending *correctly*.
    """
    m, recorded, _ = _wired(monkeypatch)
    try:
        clock = _on_a_controlled_clock(m)
        begin = recorded[("GestureDrag", "drag-begin")]
        update = recorded[("GestureDrag", "drag-update")]
        end = recorded[("GestureDrag", "drag-end")]

        begin(None, mascot._STAGE / 2.0, mascot._STAGE / 2.0)
        assert m._engine.active, "the mascot was not picked up"
        assert m._frame_timer, "no frame timer was armed by the grab"

        # `drag-update` carries a **delta**, so the handler has to accumulate.
        # Twenty moves of (4, -2) from (106, 106) land on (186, 66) - and the
        # assertion is on the *accumulated point*, because a handler that passed
        # the delta through as a position would put the engine somewhere nobody
        # asked for while still reporting a held drag.
        for _ in range(20):
            update(None, 4.0, -2.0)
        assert m._engine.held, "the drag did not register as held"
        assert m._drag_point == pytest.approx((186.0, 66.0)), m._drag_point

        end(None, 0.0, 0.0)
        assert m._engine.working, "the drop started no work timeline"
        assert m._engine.work_seconds == mascot._WORK_SECONDS

        # And the turn ends by itself, with nobody polling.
        engine = m._engine
        frames = 0
        while engine.active:
            clock.step()
            again = m._frame_tick()
            frames += 1
            if again is False:
                break
            assert frames < 5000, f"the turn never ended ({frames} frames)"
        assert not engine.active, f"the turn never ended ({frames} frames)"
        assert frames < 5000
        assert m._frame_timer == 0, "the timer id outlived the turn"
    finally:
        m.stop()


@needs_display
def test_the_hold_window_is_geometrically_inert(monkeypatch):
    """`_DONE_HOLD_MS` costs nothing a person can see.

    Measured on this stage: from `t = 7.00` the geometry is bit-identical every
    frame, so holding the finished pose longer changes nothing visible - which is
    what makes the hold free rather than a 200ms freeze somebody can feel.

    **The assertion is over the hold window itself**, from `grow_end` onwards, and
    every frame in it must be identical. Note what this test deliberately does
    *not* do: count the tail, because the geometry is identical either way and a
    count cannot tell a held turn from an unheld one.
    """
    m, _, _ = _wired(monkeypatch)
    try:
        clock = _on_a_controlled_clock(m)
        engine = m._engine
        engine.grab(mascot._STAGE / 2.0, mascot._STAGE / 2.0)
        engine.release(work_seconds=mascot._WORK_SECONDS)
        deadline = engine.grow_end + mascot._DONE_HOLD_MS / 1000.0

        window = []
        for _ in range(3000):
            clock.step()
            pose = engine.frame()
            if pose.t >= engine.grow_end:
                window.append((pose.x, pose.y, pose.d, pose.sx, pose.sy,
                               pose.tilt, pose.hop, pose.mouth))
            if pose.t >= deadline:
                break
        else:
            raise AssertionError("the turn never reached its hold window")

        assert len(window) >= 3, (
            f"only {len(window)} frames lie inside the hold window; the hold is "
            "too short to be doing what its comment says")
        assert len(set(window)) == 1, "the hold window moved the sprite"
    finally:
        m.stop()


@needs_display
def test_the_turn_stops_on_the_frame_after_the_hold(monkeypatch):
    """The other half of the hold, asked of the tick rather than of the frames.

    A count of identical frames cannot tell a held turn from an unheld one, because
    the geometry is identical either way from `t = 7.00`. **When `stop()` happened**
    can, and it is the only observable that differs.
    """
    m, _, _ = _wired(monkeypatch)
    try:
        clock = _on_a_controlled_clock(m)
        engine = m._engine
        engine.grab(mascot._STAGE / 2.0, mascot._STAGE / 2.0)
        engine.release(work_seconds=mascot._WORK_SECONDS)
        stop_t = None
        for _ in range(3000):
            clock.step()
            # **Read the timeline before the tick.** `MascotMotion.stop()` makes
            # `frame()` answer the rest pose with `t = 0.0`, so asking afterwards
            # for "when did it stop" reports the instant the engine was created -
            # which is what the first version did, and it read as a turn that ended
            # at zero.
            seen = engine.frame().t
            m._frame_tick()
            if not engine.active:
                stop_t = seen
                break
        assert stop_t is not None, "the turn never ended"
        deadline = engine.grow_end + mascot._DONE_HOLD_MS / 1000.0
        assert stop_t >= deadline - mascot_drag.DT, (
            f"the turn stopped at t={stop_t:.4f}, before its hold ran out at "
            f"{deadline:.4f}")
    finally:
        m.stop()


# --- margins ----------------------------------------------------------------

def test_clamp_holds_a_value_inside_its_range():
    assert mascot._clamp(5.0, 0.0, 10.0) == 5.0
    assert mascot._clamp(-5.0, 0.0, 10.0) == 0.0
    assert mascot._clamp(15.0, 0.0, 10.0) == 10.0


def test_clamp_answers_low_for_an_inverted_range():
    """A sprite drawn larger than the stage inverts the range, and the answer
    must be `low` - the overflow goes inward, not over the window's edge.

    **This test was deleted and written again, and the reason is the interesting
    part.** It asserted `_clamp(5.0, 10.0, 2.0) == 10.0`, and the mutation run
    reported it as a **survivor** when the `if high < low` guard was removed -
    because `max(low, min(high, v))` already answers `low` whenever
    `high < low`: `min(high, v) <= high < low`, so the `max` is `low` either way.

    Exhaustive check, all three arguments over -20..40 with `v` every third
    value: **72,000 cases, 0 differences.** So the guard in `_clamp` is an
    equivalent by construction and the witness could never have bitten; the
    property it names is now pinned here against the *behaviour*, and the guard
    stays in the code because it is where a reader looks.
    """
    assert mascot._clamp(5.0, 10.0, 2.0) == 10.0
    # And the whole family, so the claim is not one hand-picked triple.
    for low in (0.0, 10.0, -5.0):
        for high in (0.0, 2.0, -8.0):
            if high >= low:
                continue
            for value in (-100.0, 0.0, 5.0, 1000.0):
                assert mascot._clamp(value, low, high) == low, (value, low, high)


class _Picture:
    """Records what `_apply_pose` asks of the sprite.

    A stand-in for `Gtk.Picture` that **answers**, rather than one that absorbs
    anything: `set_margin_start` and `set_size_request` are real GTK methods with
    real constraints, and a recorder that ignored an out-of-range value would
    hide exactly the clamping defect under test.
    """

    def __init__(self):
        self.width = 0
        self.height = 0
        self.start = None
        self.bottom = None
        self.visible = None
        self.rejected = []

    def set_size_request(self, w, h):
        if w < 1 or h < 1:
            self.rejected.append((w, h))
        self.width, self.height = w, h

    def set_margin_start(self, v):
        self.start = v

    def set_margin_bottom(self, v):
        self.bottom = v

    def set_visible(self, v):
        self.visible = v


class _Pose:
    """A pose built from literals, so a witness is not a self-reference."""

    def __init__(self, x, y, d=132.0, sx=1.0, sy=1.0, hop=0.0, visible=True):
        self.x, self.y, self.d = x, y, d
        self.sx, self.sy, self.hop = sx, sy, hop
        self.sprite_visible = visible
        self.mouth_rect = None


def _mascot_with_picture(monkeypatch):
    m = mascot.Mascot(_App())
    m._win = object()
    m._picture = _Picture()
    return m


@pytest.mark.parametrize("x", [-500.0, -1.0, 0.0, 106.0, 1000.0])
@pytest.mark.parametrize("d", [10.0, 132.0, 210.0])
def test_the_sprite_is_always_inside_the_stage(monkeypatch, x, d):
    """Clamped, not merely inside the window by luck.

    Nine poses across the stage and well outside it: the ones a drag can actually
    produce, plus two it cannot, because a clamp that only worked for legal input
    would be the absence of a clamp.
    """
    m = _mascot_with_picture(monkeypatch)
    m._apply_pose(_Pose(x, mascot._STAGE / 2.0, d=d))
    pic = m._picture
    assert pic.rejected == [], f"a zero or negative size request: {pic.rejected}"
    assert 0 <= pic.start <= mascot._STAGE - pic.width, (pic.start, pic.width)


@pytest.mark.parametrize("y", [-500.0, -1.0, 0.0, 106.0, 1000.0])
def test_the_sprite_is_always_inside_the_stage_vertically(monkeypatch, y):
    """The same, vertically - and both clamped edges.

    The assertion reads `bottom` back, so the bound is derived from the *drawn*
    height rather than a typed `SIZE`: `bottom = _STAGE - (top + h)`, so a sprite
    of height `h` inside the stage means `0 <= bottom <= _STAGE - h`.
    """
    m = _mascot_with_picture(monkeypatch)
    m._apply_pose(_Pose(mascot._STAGE / 2.0, y))
    pic = m._picture
    assert 0 <= pic.bottom <= mascot._STAGE - pic.height, (pic.bottom, pic.height)


@pytest.mark.parametrize("sx,sy", [(1.0, 1.0), (0.97, 1.06), (1.06, 0.92), (1.4, 0.5)])
def test_the_picture_is_drawn_at_the_posed_size(monkeypatch, sx, sy):
    """`set_size_request` is `d * sx` by `d * sy`, and it is **asked of the widget**.

    The vertical clamp test derives its bound from `pic.height`, which is the
    right way to assert the bound and the wrong way to assert the *squash* - a
    widget sized `d x d` when the pose says `0.97 x 1.06` still satisfies it.
    `sx`/`sy` are the engine's own squeeze phases, read off `mascot_drag`'s
    constants so a typo in a test fixture is not mistaken for a defect.
    """
    d = mascot.SIZE
    m = _mascot_with_picture(monkeypatch)
    m._apply_pose(_Pose(mascot._STAGE / 2.0, mascot._STAGE / 2.0, d=d, sx=sx, sy=sy))
    pic = m._picture
    assert pic.width == pytest.approx(d * sx, abs=0.5), (pic.width, d * sx)
    assert pic.height == pytest.approx(d * sy, abs=0.5), (pic.height, d * sy)


def test_the_engine_really_does_squash_it():
    """The premise of the squash assertion, measured off the engine.

    `mascot_drag` has seven squeeze phases and if none of them moved `sx`/`sy` the
    test above would be pinning a formula for a constant. **Asserted against a
    real turn**, so the formula and the engine are held together.
    """
    clock = _Clock()
    engine = mascot_drag.MascotMotion(mascot._STAGE, mascot._STAGE,
                                      mascot.SIZE, now=clock)
    engine.grab(mascot._STAGE / 2.0, mascot._STAGE / 2.0)
    engine.release(work_seconds=mascot._WORK_SECONDS)
    ratios = set()
    for _ in range(600):
        clock.step()
        pose = engine.frame()
        ratios.add((round(pose.sx, 4), round(pose.sy, 4)))
        if not pose.active:
            break
    assert len(ratios) > 10, f"only {len(ratios)} distinct sx/sy pairs in a turn"
    assert any(sx != sy for sx, sy in ratios), "the squash is never asymmetric"
    assert (1.0, 1.0) in ratios, "the rest pose is missing from the turn"


def test_the_hop_lifts_the_sprite(monkeypatch):
    """`hop` is the height the catch **lifts** the sprite by, so it subtracts.

    A sign error here is invisible at rest - `hop` is 0.0 there - and would put the
    mascot's feet below the window for the whole of every catch. **Both poses are
    otherwise identical**, which is what makes the comparison mean anything: the
    first version compared a clamped pose against an unclamped one and read the
    clamp as the hop.
    """
    m = _mascot_with_picture(monkeypatch)
    mid = mascot._STAGE / 2.0
    m._apply_pose(_Pose(mid, mid))
    resting = m._picture.bottom
    m._apply_pose(_Pose(mid, mid, hop=10.0))
    lifted = m._picture.bottom
    assert lifted > resting, (
        f"a 10px hop moved the bottom margin from {resting} to {lifted}; it must "
        "lift the sprite, i.e. increase the bottom margin")
    assert lifted - resting == pytest.approx(10.0, abs=1.0)


def test_the_clamp_never_crops_the_widest_thing_drawn(monkeypatch):
    """The clamp's premise, measured rather than asserted.

    The widest thing the art draws is 150.5 px across and 162.5 px tall, against a
    212 px stage. **If either grew past the stage the clamp would start hiding
    pixels**, so the premise is asserted rather than trusted, and it will fail the
    day the art gets bigger.
    """
    engine = mascot_drag.MascotMotion(mascot._STAGE, mascot._STAGE, mascot.SIZE)
    engine.grab(mascot._STAGE / 2.0, mascot._STAGE / 2.0)
    widest_w = widest_h = 0.0
    for i in range(600):
        engine.move(mascot._STAGE / 2.0 + (60.0 if i % 2 else -60.0),
                    mascot._STAGE / 2.0 + (30.0 if i % 3 else -30.0))
        pose = engine.frame()
        widest_w = max(widest_w, pose.d * pose.sx)
        widest_h = max(widest_h, pose.d * pose.sy)
    assert widest_w < mascot._STAGE, widest_w
    assert widest_h < mascot._STAGE, widest_h


def test_sprite_visibility_reaches_the_picture(monkeypatch):
    """`pose.sprite_visible` is what the engine's `fileVisible` window drives."""
    m = _mascot_with_picture(monkeypatch)
    m._apply_pose(_Pose(106.0, 106.0, visible=False))
    assert m._picture.visible is False
    m._apply_pose(_Pose(106.0, 106.0, visible=True))
    assert m._picture.visible is True


# --- the channels the art owns ---------------------------------------------

def test_the_two_live_channels_reach_the_art(monkeypatch):
    """`tilt` and `mouth` are passed, and the art normalises them.

    The assertion is on **what the art was handed**, because the art's own
    clamping is `tests/test_mascot_art.py`'s subject and re-deriving it here
    would be a second implementation of it.
    """
    seen = {}

    def spy(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return "<svg/>"

    monkeypatch.setattr(mascot.mascot_art, "svg", spy)
    m = _mascot_with_picture(monkeypatch)

    class _P(_Pose):
        tilt = 0.11
        mouth = 0.47

    m._render(_P(106.0, 106.0))
    assert seen["kwargs"]["tilt"] == 0.11
    assert seen["kwargs"]["mouth"] == 0.47

    # And with no pose - the state/blink/level paths that are not a drag - the
    # channels are **zero**, not the last drag's values.
    seen.clear()
    m._render()
    assert seen["kwargs"]["tilt"] == 0.0
    assert seen["kwargs"]["mouth"] == 0.0


@needs_display
def test_the_engines_look_wins_while_a_turn_runs(monkeypatch):
    """During a turn the eyes follow the engine, not the grab point.

    The pointer is inside the window being moved, so `_on_motion` would otherwise
    aim the eyes wherever the grab happened to start - which is exactly where the
    mascot *was*, so the mascot would stare at its old position while travelling.
    """
    m, _, _ = _wired(monkeypatch)
    try:
        engine = m._engine
        engine.grab(106.0, 106.0)
        engine.move(190.0, 60.0)
        m._on_motion(None, 106.0, 106.0)      # pointer where the grab was
        pointer_gaze = m._gaze
        m._frame_tick()
        assert m._gaze != pointer_gaze or engine.frame().look_x == 0.0
    finally:
        m.stop()


# --- the timer --------------------------------------------------------------

def _stop_source():
    import ast
    tree = ast.parse(textwrap.dedent(inspect.getsource(mascot.Mascot.stop)))
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            return {elt.value for elt in node.iter.elts
                    if isinstance(elt, ast.Constant)}
    raise AssertionError("stop() has no tuple of timer names to cancel")


def test_stop_cancels_the_frame_timer():
    """Read out of the AST rather than by running `stop()`.

    `stop()` needs a real `GLib.source_remove`, and a timer that is **armed** is
    the only case where cancelling it is observable - so a running test would
    have to arm it first, which is a longer path to the same claim than reading
    the tuple the method iterates.
    """
    assert "_frame_timer" in _stop_source()


def test_stop_stops_the_engine_and_drops_its_pose():
    """The engine belongs to the window, and so does the pose it produced."""
    m = mascot.Mascot(_App())
    engine = mascot_drag.MascotMotion(mascot._STAGE, mascot._STAGE, mascot.SIZE)
    engine.grab(106.0, 106.0)
    m._engine = engine
    m._pose = object()
    m.stop()
    assert m._engine is None
    assert m._pose is None