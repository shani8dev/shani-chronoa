"""The desktop companion: Chronoa's mascot, floated on screen as a layer-shell overlay.

It draws `mascot_art` (the orbit-sprite organism) in a small always-available
window pinned to a corner, and makes it *live*:

- it **mirrors the real assistant state** by watching the app's `status` action -
  the same source the window orb and the top-bar indicator use - so it can never
  show "thinking" while the orb says "idle";
- it **idles** with a gentle bob and an occasional blink, and the bob stops when
  the desktop asks for less animation;
- it **reacts to an event** through `notice(kind, text)` - a landline call, a
  permission prompt and its outcome, a tool's question, a finished turn, a
  failed one - each showing the matching expression and a short speech bubble.
  The producers are the modules that *make* those decisions and they reach here
  through `mascot_events`, because none of them holds this object;
- it **follows a tool run** with a step bubble (`mascot_events.step`), the same
  words the desktop cue pill shows, so a long turn says *what* it is doing
  rather than only that it is busy;
- it **falls asleep** after `_SLEEP_AFTER_MS` of an idle sprite and wakes on the
  next state, event or pointer motion;
- **clicking it starts listening** (`toggle-listening`), so it is a face *and* a
  button.

Off by default, behind `mascot-enabled`: it is an always-on overlay, and ShaniOS
defaults to quiet.

**Two gates stand between the switch and a window, and both are measured rather
than assumed** - `available()` and then, on the real window, `_build()`'s own
layer check. This is the whole reason the companion never opens a stray window,
and the second gate exists because the first one alone was a false positive on
this product's own desktop; see `available()` for the measurement.
"""
from __future__ import annotations

import logging

#: Module level, not deferred into `_render`. `mascot_art` is pure - it imports
#: `sysfs`, which is pure - so this costs nothing, and `set_state` and
#: `_describe` both need it. Deferring it into the draw call would have made
#: those two reach for it a second way.
from shani_chronoa import mascot_art
from shani_chronoa import mascot_drag
from shani_chronoa import mascot_events

logger = logging.getLogger(__name__)

CONSENT_KEY = "mascot-enabled"
SIZE = 132                      # on-screen px of the sprite
_BLINK_MS = 130
_BUBBLE_MS = 4200
_BLINK_EVERY_MS = 4300
_BOB_EVERY_MS = 90
#: The bob is +/-5px about this margin, so this is the *middle* of the travel
#: rather than its lowest point - and it is the value `_bob_tick` returns to, so
#: the two cannot disagree.
_BASE_MARGIN = 28
_BOB_PX = 5

#: Below this change in bearing, the mascot does not redraw. `_render` rasterises
#: the SVG through a `GdkPixbuf` loader, and pointer motion arrives far faster
#: than a mascot needs to be seen to move - so without a floor every flick of the
#: mouse is a full re-rasterise for a sub-pixel change nobody can perceive.
_GAZE_DEADZONE = 0.08

#: How far the sprite may travel inside the window, in px, on each side.
#:
#: **The window is the stage.** `mascot_drag` measures everything as a fraction
#: of the stage it is handed (`LOCK_IN_RATIO_X`, `HOP_PX_RATIO`, the lock speed
#: threshold), so the stage size is the single number that decides how far the
#: mascot can be thrown and how hard a flick has to be to lock on. 40px is what
#: every measured number in `mascot_drag`'s own test suite was taken against;
#: changing it re-scales the whole mechanic.
#:
#: This is a margin of *transparent window*, not a shift of the mascot: the
#: mascot's rest position inside the stage is the centre, and the resting layer-
#: shell margins are computed from that centre so the mascot does not move on
#: screen when this is first added.
_STAGE_TRAVEL = 40

#: The stage is the sprite plus travel on both sides. `SIZE + 2*_STAGE_TRAVEL`,
#: written out because the arithmetic is the contract: the rest pose is at
#: `stage/2` and has diameter `SIZE`, so any other expression would move the
#: resting mascot on screen.
_STAGE = SIZE + 2 * _STAGE_TRAVEL

#: The label sits above the picture in the same box, so the window is taller
#: than the stage by exactly this much. It is the bubble's reserve whether or
#: not a bubble is showing, which keeps the stage - and therefore every
#: fraction in `mascot_drag` - the same size in both states.
_BUBBLE_PX = 40

#: The frame tick. 16ms is one frame at 60Hz; `mascot_drag` sub-steps internally
#: (`DT = 1/240`) so the integrator does not care, but the *pose* the window
#: shows does, and a coarser tick is a visibly stepping hop.
_FRAME_MS = 16

#: How long the completed turn holds on its final pose before `stop()`. From
#: `grow_end` the geometry is bit-identical every frame, so this changes nothing
#: visible - it exists so the tick that ends the turn happens on a pose the user
#: has actually seen, rather than in the same millisecond as the last movement.
_DONE_HOLD_MS = 200

#: The work timeline handed to `release()`. The reference's `uploadDuration`
#: default is 2.4; 3.0 is what the drop was measured at (progress bar out at
#: 6.25s, grow from 6.50s to 6.95s after the release).
_WORK_SECONDS = 3.0

#: Idle before the sprite nods off. Coucou's `desktop-logic.ts` uses 120 s, and
#: the exact number matters less than that it is longer than any pause a person
#: makes mid-thought: a companion that falls asleep between two questions is a
#: distraction, not a companion. The pointer-far condition their version also
#: carries is implicit here - `_on_motion` fires only while the pointer is over
#: the sprite, and any motion wakes it - so a reach for the mascot is always
#: enough to find it awake.
_SLEEP_AFTER_MS = 120_000


def _clamp(value: float, low: float, high: float) -> float:
    """Hold `value` inside `[low, high]`, and tolerate `high < low`.

    `high < low` happens whenever the sprite is drawn larger than the stage, and
    the answer there is `low` - the sprite stays inside the window and overflows
    inward rather than being pushed outside it. `min(max(...))` alone would
    return `high` there and centre the overflow on the window's edge.
    """
    if high < low:
        return low
    return max(low, min(high, value))

#: event kind -> the expression `mascot_art` draws for it.
#:
#: **Five of these nine now have producers, through `mascot_events`.** `call` is
#: fired by `landline`; `meeting` by an approval prompt, `build-ok`/`build-fail`
#: by its answer and by a turn ending (well or badly), and `greet` by a tool's
#: question. The names are the reference's own - `mascot_events.BY_NAME` is the
#: one table, so the next reader greps there rather than guessing at call sites.
#: The remaining four (`download`, `away`, `love`, `sleep`) are still drawn and
#: tested with no producer, which is a product decision rather than a mechanical
#: wire-up - they are kept so the artwork a state reaches is not deleted for
#: want of a caller.
_EVENT_FACE = {
    "meeting": "surprised", "call": "surprised",
    "build-ok": "excited", "build-fail": "concerned",
    "download": "happy", "away": "happy", "greet": "wink",
    "love": "love", "sleep": "sleepy",
}

_NO_SURFACE = (
    "the compositor did not create a layer surface. wlr-layer-shell is a "
    "wlroots/KDE protocol and Mutter does not implement it, and gtk4-layer-shell "
    "must also be linked *before* libwayland-client or it cannot initialise at "
    "all - usr/bin/shani-chronoa does not preload it the way "
    "usr/bin/shani-chronoa-overlay does. Nothing was shown."
)

#: What each state is called in the accessible name. The words are the ones the
#: window and the transcript already use, so a reader hears what the screen says
#: rather than a second vocabulary for the same five states.
_STATE_WORDS = {
    "idle":         "waiting",
    "listening":    "listening",
    "thinking":     "thinking",
    "speaking":     "speaking",
    "interrupting": "interrupted",
}


def _describe(state: str) -> str:
    """The mascot's accessible name: what it is, and what it is doing.

    **On the widget, not in the SVG.** `_render` rasterises the markup through a
    GdkPixbuf loader into a paintable, so anything the markup carries dies with
    the pixels. A name that is not here is not a name.

    An unrecognised state says "waiting" rather than naming itself, for the same
    reason `mascot_art.svg` falls back to idle - one answer to "what state is
    this", so the picture and the name cannot disagree.
    """
    return f"Chronoa, ShaniOS AI assistant, {_STATE_WORDS.get(state, 'waiting')}"


def _layer_shell():
    """The layer-shell library, or `(None, why)`.

    **Both spellings are tried, GTK 4 first, because the namespace is chosen by
    the GTK major version and the two are not a version typo.** Measured on
    Ubuntu 26.04 by reading both packages' file lists:

    | typelib file | namespace | package | GTK |
    |---|---|---|---|
    | `GtkLayerShell-0.1.typelib` | `GtkLayerShell` **0.1** | `gir1.2-gtklayershell-0.1` | **3** |
    | `Gtk4LayerShell-1.0.typelib` | `Gtk4LayerShell` **1.0** | `gir1.2-gtk4layershell-1.0` | **4** |

    So asking only for the older name would import a library that cannot load in
    a GTK 4 process at all (`ImportError: Requiring namespace 'Gtk' version
    '3.0', but '4.0' is already loaded`), and asking only for the newer one
    misses the distributions that ship a GTK 4 build under the old name.

    **The module is reached with `importlib`, not `getattr(gi.repository, ...)`**,
    and that is measured rather than stylistic. `gi.repository` on this PyGObject
    does **not** import a submodule on attribute access, so
    `getattr(gi.repository, "Gtk4LayerShell")` raised `AttributeError: module
    'gi.repository' has no attribute 'Gtk4LayerShell'` *with `require_version`
    satisfied and the typelib installed* - while
    `importlib.import_module("gi.repository.Gtk4LayerShell")` in the same process
    returned the real module. Both spellings name the same typelib, so the
    `getattr` form refused on **every** machine, including one whose compositor
    speaks layer-shell. And the refusal named the wrong library, because the loop
    reported its *last* failure rather than its first.
    """
    errors = []
    for namespace, version in (("Gtk4LayerShell", "1.0"),
                               ("GtkLayerShell", "0.1")):
        try:
            import importlib

            import gi as _gi
            _gi.require_version(namespace, version)
            return importlib.import_module(f"gi.repository.{namespace}"), ""
        except Exception as error:  # noqa: BLE001 - try the other spelling
            errors.append(f"{namespace} {version} is unusable ({error})")
    return None, (
        f"no layer-shell library: {'; '.join(errors)}. A GTK 4 app needs "
        "gir1.2-gtk4layershell-1.0 and libgtk4-layer-shell0 - the "
        "GtkLayerShell-0.1 typelib many distributions ship is the GTK 3 build "
        "and cannot load in a GTK 4 process."
    )


def _motion_is_reduced() -> bool:
    """Whether the desktop has asked for less animation.

    Reused rather than re-implemented: `automation_overlay.reduced_motion()`
    already answers this for the cue layer and reads it per call so a person who
    flips the setting does not have to restart anything. A third copy of "ask
    `org.gnome.desktop.interface` whether `enable-animations` is off" is a third
    answer to one question, and two of them would drift.

    An import that fails leaves the bob on, which is the direction that loses the
    feature rather than the preference - so the refusal is logged.
    """
    try:
        from shani_chronoa.automation_overlay import reduced_motion
        return bool(reduced_motion())
    except Exception as error:  # noqa: BLE001
        logger.info("mascot: could not read the reduce-motion setting (%s); "
                    "the bob stays on", error)
        return False


def _gaze_for(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    """Where the pointer is relative to the mascot, as a -1..1 bearing.

    Pure, so the geometry is testable without a display - the same reason
    `_render`'s decisions live in named helpers rather than inside the GLib
    callback that cannot be reached from a test.

    **An axis under 2px answers `0.0`**, because GTK reports a **1x1**
    allocation for a window that has not been laid out, and the first `motion`
    event can arrive before layout finishes. Dividing by a half-pixel puts the
    bearing at its maximum, so an unlaid-out window would aim the eyes at the
    corner in the direction the pointer happens to be - measured, not reasoned:
    `width=1` with a pointer at `(5, 5)` answers `(1.0, 1.0)` without this. There
    is no centre to be away from, so looking forward is the honest answer.
    """
    half_w, half_h = width / 2.0, height / 2.0
    if half_w < 1 or half_h < 1:
        return (0.0, 0.0)
    return (max(-1.0, min(1.0, (x - half_w) / half_w)),
            max(-1.0, min(1.0, (y - half_h) / half_h)))


def _gaze_moved(old: tuple[float, float], new: tuple[float, float]) -> bool:
    """Whether a bearing change is big enough to be worth a re-rasterise."""
    return (abs(new[0] - old[0]) >= _GAZE_DEADZONE
            or abs(new[1] - old[1]) >= _GAZE_DEADZONE)


def available() -> tuple[bool, str]:
    """Whether the overlay can run *here*: the library, **and** a compositor that
    speaks layer-shell.

    **The second half of this check is the one that was missing, and it is the
    whole difference between a companion and a stray window.** Measured on this
    product's own desktop - GNOME on Wayland, `gir1.2-gtk4layershell-1.0`
    installed - the old check answered `(True, "")`, and the window it then
    opened was **not a layer surface**: `get_zwlr_layer_surface_v1()` returned 0
    and GTK warned *"GtkWindow is not a layer surface"*. An always-on-top window
    is then pinned to the bottom-right corner of the screen for the life of the
    app, which is precisely what this module's own docstring promised never
    happens.

    Two different causes produce that, and **neither of them is "the library is
    missing"**:

    - **link order.** gtk4-layer-shell has to be loaded *before*
      libwayland-client or it cannot initialise. `usr/bin/shani-chronoa` does
      not preload it; `usr/bin/shani-chronoa-overlay` does, and re-execs for
      exactly this reason.
    - **the compositor.** `zwlr_layer_shell_v1` is a wlroots/KDE protocol and
      **Mutter has never implemented it**.

    `is_supported()` is the library's own answer to "does this compositor do
    layer-shell at all", and it answers correctly *before* any window exists,
    which is what makes it the cheap gate and `init_for_window() returning` - a
    statement about the call, not about the compositor - useless as one.
    """
    shell, why = _layer_shell()
    if shell is None:
        return False, why
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if not Gtk.init_check():
            # No display means no compositor to ask, which is a different answer
            # from "this compositor cannot do layer-shell".
            return False, "no display, so there is no compositor to ask"
        supported = bool(shell.is_supported())
    except Exception as error:  # noqa: BLE001 - an unanswerable question is not yes
        return False, f"layer-shell could not be asked ({error})"
    if not supported:
        return False, _NO_SURFACE
    return True, ""


class Mascot:
    """The companion window. Create with the app; `start()` shows it when allowed."""

    def __init__(self, app) -> None:
        self.app = app
        self._win = None
        self._picture = None
        self._bubble = None
        self._state = "idle"
        self._expr = ""
        self._blink = False
        self._level = 0.0
        # The layer-shell module, resolved once at build time. `_bob_tick` runs
        # eleven times a second, so re-resolving it per tick would put a
        # `require_version` and a repository lookup on the hot path for no
        # reason - and it was resolved here first anyway.
        self._shell = None
        self._expr_timer = 0
        self._bubble_timer = 0
        self._blink_timer = 0
        self._unblink_timer = 0
        self._bob_timer = 0
        self._bob = 0
        self._sleep_timer = 0
        # Whether the sprite is showing its `sleepy` face on the idle timer, and
        # when it last did anything. `_last_activity` is a monotonic clock rather
        # than a count of ticks: the tick that sleeps compares it to now, so a
        # pause is measured in seconds and not in how often GLib happened to run.
        self._asleep = False
        self._last_activity = 0.0
        # The thread the GTK loop runs on, recorded when `start()` builds the
        # window. `mascot_events` producers call from the tool loop's thread, so
        # `_on_event` compares against this to decide whether to hop.
        self._main_thread = None
        # Where the mascot is looking, and the controller that moves it. Both are
        # cleared in `stop()` with the rest: the controller belongs to the window
        # being destroyed, and a bearing left over from a destroyed window would
        # be applied to the next one's first frame.
        self._gaze: tuple[float, float] = (0.0, 0.0)
        self._motion = None
        # The engine that owns the drag, and the pose from the last frame it
        # produced. `_engine` is a separate name from `_motion` because that one
        # is the *gaze* controller - the two are cleared together in `stop()` and
        # mixing them up would mean re-running the gaze on a destroyed window.
        self._engine = None
        self._pose = None
        self._frame_timer = 0
        self._drag = None
        # Where the pointer was when the drag began, so `drag-update`'s *deltas*
        # can be turned back into the absolute stage point the engine wants.
        self._drag_point = (0.0, 0.0)

    # --- lifecycle ------------------------------------------------------------
    def _allowed(self) -> bool:
        config = getattr(self.app, "config", None)
        try:
            return bool(config.get_bool(CONSENT_KEY, False)) if config is not None else False
        except Exception:  # noqa: BLE001
            return False

    def start(self) -> bool:
        if self._win is not None:
            return True
        ok, why = available()
        if not ok or not self._allowed():
            if not ok:
                logger.info("mascot: %s", why)
            return False
        try:
            if not self._build():
                return False
        except Exception:  # noqa: BLE001
            logger.exception("mascot: could not create the companion")
            return False
        self._watch_state()
        self._schedule()
        # The bus is installed **after** the window exists, so a producer that
        # fires during `_build` cannot reach a half-built tree, and cleared in
        # `stop()` before the window is destroyed, so a late event cannot reach
        # a dead one. `_main_thread` is read by `_on_event` to tell a producer on
        # the tool loop's thread from one already on this one.
        import threading
        import time
        self._main_thread = threading.get_ident()
        self._last_activity = time.monotonic()
        mascot_events.install(self._on_event)
        return True

    def stop(self) -> None:
        from gi.repository import GLib
        # Cleared first, before the window is torn down: a producer already in
        # flight on another thread must find no listener rather than reach a
        # tree that is about to be destroyed. The `listener is _listener` guard
        # in `uninstall` keeps a late stop from unlistening a newer mascot.
        mascot_events.uninstall(self._on_event)
        # `_unblink_timer` belongs in this list and used not to be tracked at all,
        # which is why `stop()` could not cancel it: the blink schedules an
        # un-blink and threw the source id away. `_sleep_timer` is the same debt
        # for the idle nap: without it, a stop would leave a timeout that paints
        # a sleepy face onto the next window.
        for tid in ("_blink_timer", "_bob_timer", "_expr_timer",
                    "_bubble_timer", "_unblink_timer", "_frame_timer",
                    "_sleep_timer"):
            t = getattr(self, tid, 0)
            if t:
                GLib.source_remove(t)
                setattr(self, tid, 0)
        if self._win is not None:
            self._win.destroy()
            self._win = None
        # The controller belonged to that window, and the bearing belongs to it
        # too - a mascot that opened while the pointer was elsewhere would
        # otherwise spend its first seconds looking at where the pointer used to
        # be, which is a fact about the previous window.
        self._motion = None
        self._gaze = (0.0, 0.0)
        # Same for the drag engine: it holds the last pose, and a pose belongs to
        # the window it was drawn in. `MascotMotion.stop()` is what clears
        # `active`/`working`, and leaving one of those true on a destroyed window
        # is how a `_frame_tick` outlives its tree.
        if self._engine is not None:
            self._engine.stop()
        self._engine = None
        self._pose = None
        # The widgets belonged to that window. Leaving them bound would let a
        # timer that outlived it call into a destroyed tree, and the guards in
        # `_render`/`say` are `is None` checks - so clearing the refs is what makes
        # those guards mean anything after a stop.
        self._picture = None
        self._bubble = None
        self._blink = False
        self._shell = None
        self._asleep = False
        self._main_thread = None

    def _build(self) -> bool:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        shell, why = _layer_shell()
        if shell is None:
            logger.info("mascot: %s", why)
            return False

        win = Gtk.Window(application=self.app)
        # The window **is** the stage, not a frame around the sprite: the mascot
        # travels inside it, and the extra `SIZE + 2*_STAGE_TRAVEL - SIZE` of width
        # is transparent margin rather than anywhere the mascot can be seen. The
        # resting margins below are computed from the rest pose so this does not
        # move the mascot on screen.
        win.set_default_size(_STAGE, _STAGE + _BUBBLE_PX)
        win.add_css_class("chronoa-mascot")
        provider = Gtk.CssProvider()
        provider.load_from_data(b".chronoa-mascot{background:transparent;} "
                                b".chronoa-bubble{background:rgba(10,60,70,0.92);color:#eafffb;"
                                b"border-radius:12px;padding:6px 10px;margin-bottom:4px;}")
        Gtk.StyleContext.add_provider_for_display(
            win.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_valign(Gtk.Align.END)
        self._bubble = Gtk.Label()
        self._bubble.add_css_class("chronoa-bubble")
        self._bubble.set_visible(False)
        self._bubble.set_wrap(True)
        self._bubble.set_max_width_chars(22)
        self._picture = Gtk.Picture()
        self._picture.set_size_request(SIZE, SIZE)
        # The accessible name is set by `_announce` and **only** by
        # `_announce`. `_build` used to write the initial label itself, as a
        # second copy of the same call - and a copy of the only call that
        # matters is a copy that can be the wrong one: breaking `_announce` left
        # the build-time label behind, so the widget still carried a name and
        # nothing said the states had stopped being announced.
        box.append(self._bubble)
        box.append(self._picture)
        self._announce()
        win.set_child(box)

        # A click anywhere on the sprite starts listening.
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self._clicked())
        self._picture.add_controller(click)

        # The mascot watches the pointer. A motion controller on the *window*,
        # not on the picture: the picture is the size request, and motion over
        # the transparent margin around it is still motion over the mascot.
        # Boundary events carry the widget that saw them, and are what lets the
        # eyes return to centre when the pointer leaves - without them the gaze
        # stays locked on wherever the pointer went, which reads as the mascot
        # staring at the edge of the screen for the rest of the session.
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        # **No `set_enter_boundary` here**: that is GTK 3's
        # `gtk_event_controller_set_enter_boundary`, removed in GTK 4, and
        # calling it raises AttributeError on *every* build - the GTK3-API-in-
        # GTK4 class this repository has shipped a crash from before. GTK 4 fixes
        # the boundary at `CROSS | OUTSIDE`, which is the crossing we want.
        motion.connect("leave", lambda *_: self._recentre())
        win.add_controller(motion)
        self._motion = motion

        # The drag engine, and the gesture that drives it.
        #
        # **The gesture is on the window, not on the picture**, and that is the
        # measured reason rather than a preference. `self._picture.set_size_request`
        # is `pose.d * sx * sy`, so the picture's origin *moves* as the sprite
        # resizes - a gesture on it would see its coordinate origin slide under the
        # pointer every time the mascot grew, and `grab(x, y)` would be handed a
        # different point each frame. The window's origin does not move.
        self._engine = mascot_drag.MascotMotion(_STAGE, _STAGE, SIZE)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        win.add_controller(drag)
        self._drag = drag

        shell.init_for_window(win)
        shell.set_layer(win, shell.Layer.TOP)
        # **The protocol requires a namespace**, and this call was missing.
        shell.set_namespace(win, "chronoa-mascot")
        shell.set_anchor(win, shell.Edge.BOTTOM, True)
        shell.set_anchor(win, shell.Edge.RIGHT, True)
        # **These stay `_BASE_MARGIN`, and that is load-bearing twice over.** They
        # place the *window*, not the sprite: the TRAVEL of extra width is
        # transparent margin, so holding the window's own margins is what holds
        # the mascot where it was - it now rests TRAVEL px further in, which is a
        # deliberate, deterministic shift with no jitter. And `_bob_tick` returns
        # to `_BASE_MARGIN` every `_BOB_EVERY_MS`, so a different value here would
        # make the bob's resting point and its return value disagree.
        shell.set_margin(win, shell.Edge.BOTTOM, _BASE_MARGIN)
        shell.set_margin(win, shell.Edge.RIGHT, _BASE_MARGIN)
        # `set_keyboard_interactivity` was the GTK 3 spelling and became a *mode*
        # in 1.x; calling the old name is an AttributeError that took the whole
        # layer down rather than leaving it unlayered.
        if hasattr(shell, "set_keyboard_interactivity"):
            shell.set_keyboard_interactivity(win, 0)
        shell.set_keyboard_mode(win, shell.KeyboardMode.NONE)

        # **The gate `available()` cannot make, because it needs a real window.**
        # `init_for_window()` returning says the *call* worked; whether the
        # compositor agreed is a separate question with a separate answer, and
        # answering it here is the difference between a companion and a stray
        # always-on-top window. Measured here on GNOME with the library
        # installed: `is_layer_window()` False and the surface `0`.
        layered = bool(shell.is_layer_window(win)) and bool(
            shell.get_zwlr_layer_surface_v1(win))
        if not layered:
            win.destroy()
            self._win = None
            self._picture = None
            self._bubble = None
            logger.info("mascot: %s", _NO_SURFACE)
            return False

        self._win = win
        self._shell = shell
        self._render()
        win.present()
        return True

    # --- drag -----------------------------------------------------------------
    def _on_drag_begin(self, _gesture, x: float, y: float) -> None:
        """Pick the mascot up. `x`/`y` are **window** coordinates."""
        if self._engine is None:
            return
        self._drag_point = (x, y)
        self._engine.grab(x, y)
        self._start_frames()

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        """One pointer move.

        **`drag-update` carries a delta, not a point**, which is the reason
        `move(x, y)` is called with the accumulated position rather than the delta
        the signal handed over: the engine clamps the cursor into the stage, and a
        clamp applied to a delta would be meaningless.
        """
        if self._engine is None or not self._engine.active:
            return
        self._drag_point = (self._drag_point[0] + dx, self._drag_point[1] + dy)
        self._engine.move(*self._drag_point)

    def _on_drag_end(self, _gesture, dx: float, dy: float) -> None:
        """Let go, and start the work timeline."""
        if self._engine is None or not self._engine.active:
            return
        self._drag_point = (self._drag_point[0] + dx, self._drag_point[1] + dy)
        self._engine.move(*self._drag_point)
        self._engine.release(work_seconds=_WORK_SECONDS)

    def _start_frames(self) -> None:
        if self._frame_timer or self._engine is None:
            return
        from gi.repository import GLib
        self._frame_timer = GLib.timeout_add(_FRAME_MS, self._frame_tick)

    # --- one frame -----------------------------------------------------------
    def _frame_tick(self) -> bool:
        """Advance the engine one frame and draw it.

        **The turn is ended here, by the caller of `frame()`, not by the engine** -
        `MascotMotion.stop()` is the only thing that clears `active`/`working`, and
        nothing else in this module does, so a drag that is never released would
        leave the sprite at its last pose for the life of the window.

        `_DONE_HOLD_MS` is spent holding the finished pose *after* `grow_end`.
        Measured: from `t = 7.00` the geometry is bit-identical every frame, so
        the hold changes nothing a person can see - it exists so the tick that
        ends the turn lands on a pose the user has already seen rather than in the
        same millisecond as the last movement.
        """
        engine = self._engine
        if engine is None:
            self._frame_timer = 0
            return False
        pose = engine.frame()
        if engine.working and pose.t >= engine.grow_end + _DONE_HOLD_MS / 1000.0:
            engine.stop()
        # The engine's own look wins while a turn is running: the pointer is
        # *inside* the window being moved, so `_on_motion` would otherwise be
        # aiming the eyes at wherever the grab happened to start.
        self._gaze = (pose.look_x, pose.look_y)
        self._apply_pose(pose)
        self._render(pose)
        if engine.active:
            return True
        self._frame_timer = 0
        return False

    def _apply_pose(self, pose) -> None:
        """Put one pose on screen: size, position, visibility.

        **The margins are clamped, and TRAVEL cannot make that unnecessary.**
        Measured on this stage: growing the window by one pixel of travel removes
        0.88 px of the worst overflow, so no value of TRAVEL eliminates it - and the
        home swing *deliberately* goes past the follow bound, because overshooting
        the resting place is what makes the settle look like a settle. So the
        travel is the room the engine gets, and the clamp is what keeps the sprite
        whole inside the window: the widest thing drawn is 150.5 px across and
        162.5 px tall, against a 212 px stage, so a clamp never crops - it stops a
        pose putting half the mascot outside the window.
        """
        if self._picture is None:
            return
        w = pose.d * pose.sx
        h = pose.d * pose.sy
        self._picture.set_size_request(int(round(w)), int(round(h)))
        left = _clamp(pose.x - w / 2.0, 0.0, _STAGE - w)
        # `margin_bottom` rather than `margin_top`, because the box is a vertical
        # stack with the bubble *above* the picture and is bottom-aligned: a
        # bottom margin is the only one that lifts the sprite without pushing the
        # bubble's reserve around with it. The hop subtracts, since `hop` is the
        # height the catch lifts the sprite by.
        top = _clamp(pose.y - h / 2.0 - pose.hop, 0.0, _STAGE - h)
        self._picture.set_margin_start(int(round(left)))
        self._picture.set_margin_bottom(int(round(_STAGE - (top + h))))
        self._picture.set_visible(pose.sprite_visible)

    # --- drawing --------------------------------------------------------------
    def _render(self, pose=None) -> None:
        if self._win is None or self._picture is None:
            return
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf, Gdk
            # `tilt` and `mouth` are the two live channels `mascot_drag` owns; both
            # are normalised by `mascot_art` rather than here, so a caller cannot
            # hand it a value it has to clamp itself.
            svg = mascot_art.svg(self._state, self._expr, self._blink,
                                 self._level, self._gaze,
                                 tilt=pose.tilt if pose is not None else 0.0,
                                 mouth=pose.mouth if pose is not None else 0.0)
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.set_size(SIZE * 2, SIZE * 2)
            loader.write(svg.encode())
            loader.close()
            texture = Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
            self._picture.set_paintable(texture)
        except Exception:  # noqa: BLE001
            logger.debug("mascot: render failed", exc_info=True)

    def _begin_turn_work(self) -> None:
        """Run the work timeline for a turn nobody dragged the mascot for.

        `MascotMotion.begin_work()` existed and was fully tested and had **no
        caller in `usr/`** - the engine could chew after a drop (`release()`) but
        nothing could start a turn's worth of it, which is the common case: a
        question asked by voice reaches `set_state("thinking")` with no pointer
        anywhere near the sprite.

        **Two refusals, and neither is decoration.**

        - *Held.* `begin_work()` sets `_held = False`. Running it while a drag is
          in progress would let go of the mascot under the pointer, so a turn
          starting mid-drag would be felt as the mascot dropping itself.
        - *Already working.* A mascot dropped 200 ms ago is mid-chew; starting
          again rewinds `t` to `T_DROP` and **re-plays the entry pop** the person
          just watched. One turn, one timeline - and `_frame_tick` ends the
          timeline on its own, so skipping here cannot leave it running for ever.

        **`_start_frames()` is part of this, not a detail.** The timeline only
        advances when something calls `engine.frame()`, and the only caller is the
        frame timer - which `_on_drag_begin` starts and nothing else does. Without
        it the mascot would enter `working`, never move, and never stop: `working`
        is what `_frame_tick` checks to end the turn, so a turn that never ticks
        never ends.
        """
        engine = self._engine
        if engine is None or engine.held or engine.working:
            return
        if _motion_is_reduced():
            return
        engine.begin_work(_WORK_SECONDS)
        self._start_frames()

    def set_state(self, state: str) -> None:
        # Validated against the art module's own list. This carried a hand-typed
        # tuple, so a state added to `mascot_art._PALETTE` would render happily
        # and be refused here - the picture and the state could disagree.
        state = state if state in mascot_art.states() else "idle"
        if state != self._state:
            self._state = state
            self._note_activity()
            self._announce()
            self._render()
            # On the *transition*, not on every call: `set_state` is how a level
            # feed and a turn both reach here, and a turn re-announcing itself
            # would restart the timeline on every frame of it. `thinking` is the
            # only state that means work is under way.
            if state == "thinking":
                self._begin_turn_work()

    def _announce(self) -> None:
        """The one place the mascot's accessible name is set.

        It lives on the **widget**, not in the SVG: `_render` rasterises the
        markup through a GdkPixbuf loader into a paintable, so a
        `role`/`aria-label` in the artwork would be discarded with the pixels
        and reachable by nothing at all. AT-SPI reads the `Gtk.Picture`.

        Called on every state change, so the name does not stay "waiting" for
        the whole life of a window that then goes on to speak, listen and
        think - which is a name that is confidently wrong rather than absent.
        """
        if self._picture is None:
            return
        try:
            # `Gtk` is in no scope here - this module imports gi inside each
            # method that needs it, so the reference has to be brought in the
            # same way. Written without the import first, and the `except`
            # below swallowed the resulting NameError, which is the exact
            # failure the label was added to fix.
            from gi.repository import Gtk
            self._picture.update_property(
                [Gtk.AccessibleProperty.LABEL], [_describe(self._state)])
        except Exception:  # noqa: BLE001
            logger.debug("mascot: accessible label not set", exc_info=True)

    def set_level(self, level: float) -> None:
        """Feed a live mic/playback level, so the halo swells with the voice.

        Driven from `app/voice.py`'s two level handlers - the same two feeds the
        orb reads - so this is one call beside each of them rather than a second
        subscription to the audio.

        **The value stored is the quantised one**, from `mascot_art`, because
        the stored level is the picture's only record of it: a level finer than
        `mascot_art.LEVEL_STEP` renders an identical character, and returning
        early on an unchanged picture is what keeps a mic feeding samples at
        audio rate from re-rendering the orb on every one of them. The
        quantisation lives in the art module because that is where the mapping
        from level to pixels is, so the two cannot disagree about which levels
        are distinguishable.
        """
        # Only the live states show the halo swelling; ignore stray levels.
        if self._state in ("listening", "speaking"):
            level = mascot_art.quantise_level(level)
            if level == self._level:
                return
            self._level = level
            self._render()

    def express(self, name: str, seconds: float = 2.6) -> None:
        """Show an expression briefly, then return to the plain face."""
        from gi.repository import GLib
        self._expr = name
        self._render()
        if self._expr_timer:
            GLib.source_remove(self._expr_timer)

        def done():
            self._expr = ""
            self._expr_timer = 0
            self._render()
            return False
        self._expr_timer = GLib.timeout_add(int(seconds * 1000), done)

    def say(self, text: str) -> None:
        """A short speech bubble above the sprite, cleared after a few seconds."""
        from gi.repository import GLib
        if self._bubble is None:
            return
        self._bubble.set_text(text)
        self._bubble.set_visible(bool(text))
        if self._bubble_timer:
            GLib.source_remove(self._bubble_timer)

        def done():
            if self._bubble is not None:
                self._bubble.set_visible(False)
            self._bubble_timer = 0
            return False
        self._bubble_timer = GLib.timeout_add(_BUBBLE_MS, done)

    def notice(self, kind: str, text: str = "") -> None:
        """React to something that happened: an expression, and maybe a bubble."""
        self._note_activity()
        expr = _EVENT_FACE.get(kind)
        if expr is None:
            # A misspelled or unwired kind used to show a surprise face for three
            # seconds and say nothing, which reads as a reaction the mascot chose
            # rather than one nobody asked for.
            logger.warning("mascot: no face for %r; showing surprised", kind)
            expr = "surprised"
        self.express(expr, 3.0)
        if text:
            self.say(text)

    # --- idle life ------------------------------------------------------------
    def _schedule(self) -> None:
        from gi.repository import GLib
        self._blink_timer = GLib.timeout_add(_BLINK_EVERY_MS, self._blink_once)
        # A blink is one discrete event; a bob is continuous motion, so this is
        # the one the desktop's reduce-motion setting governs - and the setting is
        # read here rather than cached, so flipping it does not need a restart.
        if not _motion_is_reduced():
            self._bob_timer = GLib.timeout_add(_BOB_EVERY_MS, self._bob_tick)
        # The idle nap. Armed here with the other timers, and re-armed by
        # `_note_activity` whenever something happens - so the clock it measures
        # is time since the *last* thing, not time since the window opened.
        self._arm_sleep()

    def _blink_once(self) -> bool:
        from gi.repository import GLib
        if self._expr or self._state != "idle":
            return True                 # do not blink over an expression/active state
        self._blink = True
        self._render()
        self._unblink_timer = GLib.timeout_add(_BLINK_MS, self._unblink)
        return True

    def _unblink(self) -> bool:
        self._unblink_timer = 0
        self._blink = False
        self._render()
        return False

    # --- the idle nap ---------------------------------------------------------
    def _arm_sleep(self) -> None:
        """(Re)arm the one-shot timeout that puts the sprite to sleep."""
        from gi.repository import GLib
        if self._sleep_timer:
            GLib.source_remove(self._sleep_timer)
        self._sleep_timer = GLib.timeout_add(_SLEEP_AFTER_MS, self._sleep_tick)

    def _note_activity(self) -> None:
        """Something happened: reset the sleep clock, and wake if already asleep.

        Called from `set_state`, `notice` and `_on_motion` - the three things
        that mean "the person or the assistant is doing something". It does
        **not** re-arm the timeout on every call, because motion fires far faster
        than a nap needs to be rescheduled; instead it records *when*, and
        `_sleep_tick` re-arms itself for whatever is left.
        """
        import time
        self._last_activity = time.monotonic()
        self._wake()

    def _wake(self) -> None:
        """Clear the sleepy face if it is showing. Idempotent."""
        if not self._asleep:
            return
        self._asleep = False
        if self._expr == "sleepy":
            self._expr = ""
        self._render()

    def _sleep_tick(self) -> bool:
        """Fall asleep once `_SLEEP_AFTER_MS` have passed with nothing happening.

        **The condition is the live state, not a flag set somewhere else.** The
        nap is shown only while the assistant is `idle` and nothing is being
        expressed; a turn beginning (`thinking`) or a face (`notice`) reaches
        `_note_activity`, which wakes it - so it cannot be seen asleep while the
        orb says *thinking*, which is the one reading that would be a lie. When
        the state is busy it re-arms rather than sleeping late, so the 120 s is
        measured from the last real thing and not from when a tick happened to
        land.
        """
        import time
        self._sleep_timer = 0
        if self._win is None:
            return False
        last = self._last_activity or time.monotonic()
        idle_ms = (time.monotonic() - last) * 1000.0
        if self._state == "idle" and not self._expr and idle_ms >= _SLEEP_AFTER_MS:
            self._asleep = True
            self._expr = "sleepy"
            self._render()
            return False
        from gi.repository import GLib
        remaining = max(_SLEEP_AFTER_MS - idle_ms, 1000.0)
        self._sleep_timer = GLib.timeout_add(int(remaining), self._sleep_tick)
        return False

    # --- events from the rest of the app --------------------------------------
    def _on_event(self, kind: str, text: str = "") -> None:
        """One `mascot_events` event, from whichever thread produced it.

        **Producers are not on the GTK thread.** A permission is decided on the
        tool loop's executor thread, so touching a widget from here directly would
        be the undefined behaviour `ask_bridge`'s own docstring warns about. The
        thread recorded at `start()` is compared against the caller's, and a
        producer off it is hopped onto the loop the same way the landline call
        already is - `GLib.idle_add` is safe to call from any thread.
        """
        import threading
        if self._main_thread is not None and threading.get_ident() != self._main_thread:
            try:
                from gi.repository import GLib
                GLib.idle_add(self._on_event_here, kind, text)
            except Exception:  # noqa: BLE001 - a missed face is not a failed turn
                logger.debug("mascot: could not queue %r", kind, exc_info=True)
            return
        self._on_event_here(kind, text)

    def _on_event_here(self, kind: str, text: str = "") -> None:
        """Route one event on the GTK thread: a step is a bubble, the rest a face."""
        if kind == "step":
            self._note_activity()
            self.say(text)
            return
        self.notice(kind, text)

    # --- gaze -----------------------------------------------------------------
    def _on_motion(self, _controller, x: float, y: float) -> None:
        """Move the eyes toward the pointer, if that is worth drawing.

        **Reduce-motion governs this for the same reason it governs the bob**:
        both are continuous motion the desktop's animation setting exists to
        stop, and a setting honoured for one and not the other is a setting
        honoured for whichever one nobody noticed. Read per call rather than
        cached, so flipping it takes effect without a restart - the bob's
        comment says so and this is the second reader of it.

        The deadzone is the other half. `motion` fires far faster than a mascot
        needs to be seen moving, and every call here rasterises the whole SVG, so
        a per-event redraw is the most expensive thing this module could do.
        """
        if self._win is None:
            return
        # Presence wakes it, and does so even when reduce-motion stops the eyes
        # following - the setting is about animation, not about whether the
        # person is there.
        self._note_activity()
        if _motion_is_reduced():
            return
        bearing = _gaze_for(x, y, self._win.get_width(), self._win.get_height())
        if not _gaze_moved(self._gaze, bearing):
            return
        self._gaze = bearing
        self._render()

    def _recentre(self) -> None:
        """Look forward again after the pointer leaves the mascot."""
        if self._win is None or self._gaze == (0.0, 0.0):
            return
        self._gaze = (0.0, 0.0)
        self._render()

    def _bob_tick(self) -> bool:
        import math
        shell = self._shell
        if self._win is None or shell is None:
            return False
        self._bob = (self._bob + 1) % 360
        margin = _BASE_MARGIN + int(
            _BOB_PX * (1 + math.sin(self._bob * math.pi / 180)))
        try:
            shell.set_margin(self._win, shell.Edge.BOTTOM, margin)
        except Exception:  # noqa: BLE001
            return False
        return True

    # --- wiring to the app ----------------------------------------------------
    def _watch_state(self) -> None:
        action = self.app.lookup_action("status") if self.app is not None else None
        if action is None:
            return
        try:
            self.set_state(action.get_state().get_string())
        except Exception:  # noqa: BLE001
            pass
        action.connect("notify::state", lambda a, _p: self.set_state(
            a.get_state().get_string() if a.get_state() is not None else "idle"))

    def _clicked(self) -> None:
        if self.app is not None:
            self.app.activate_action("toggle-listening", None)