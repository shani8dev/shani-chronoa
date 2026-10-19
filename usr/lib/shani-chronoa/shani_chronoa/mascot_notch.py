"""The companion as a GNOME Shell **notch**: a strip along the top edge carrying
the mascot, what it is doing, and the tool step it is on.

**Why this module exists at all.** `mascot.py` opens a layer-shell window, and
on the compositor this product ships on that window is never a layer surface:
`mascot.available()` answers `(False, ...)` on Mutter, correctly, because Mutter
has never implemented `zwlr_layer_shell_v1`. So on GNOME the companion is off -
`start()` returns False, the consent switch does nothing, and a badge in
Settings is a promise the product cannot keep. `chronoa-mascot@shani.dev` is the
route that does work there, and this module is what makes the product *use* it.
Without this the extension is a thing that renders when something else tells it
to, and the two have no relationship the app can see.

**The same public surface as `mascot.Mascot`, deliberately.** `start`, `stop`,
`set_state`, `set_level`, `express`, `say`, `notice` and `running` are the whole
contract, and `mascot.create(app)` returns whichever of the two the compositor
allows. The caller cannot tell which it got, which is the point: there is one
companion with two presentations, not a companion and a fallback with two sets
of bugs.

**What this one does not do, stated rather than left to be discovered.** There is
no drag (`mascot_drag`'s engine drives a *window* stage with margins; a strip
pinned to an edge has no stage to throw the sprite around in), and there is no
click-to-listen: the strip is `reactive: false` in the extension because a
translucent panel over the top edge that swallows clicks is worse than no
button. Blink, bob, sleep and the gaze are all here, because those are the parts
that make it a companion rather than a status bar.

**The art is not reimplemented here.** `mascot_art.svg()` stays the single
generator; this rasterises it through the same `GdkPixbuf` pipeline
`Mascot._render` uses and writes a PNG, because the extension loads it from a
file - GJS cannot push bytes into a `St.TextureCache` (PART 9 of
HARVEST-COUCOU.md: `St.ImageContent.set_bytes` wants a `CoglContext` this
Shell's GJS cannot produce).
"""
from __future__ import annotations

import logging
import os
import tempfile
import time

from shani_chronoa import mascot
from shani_chronoa import mascot_art
from shani_chronoa import mascot_events


def _play(kind: str) -> None:
    """Sound is optional, and must never be able to take the strip down.

    **The import is inside the function on purpose.** The window's
    `Mascot.notice` imports `mascot_sounds` at module scope, and this module is
    the surface that exists *because* the window could not be built - so a
    module-level import here would make the fallback the fragile one: a missing
    or half-written `mascot_sounds` would stop the notch from being created at
    all, which is a strictly worse outcome than silence.

    It is also what lets this module be imported at all on a tree where
    `mascot_sounds` is not present - measured, not assumed: a module-level
    import made `tests/test_the_notch_is_reachable.py` fail at *collection*
    with `ImportError: cannot import name 'mascot_sounds'`, so one optional
    module took the whole notch witness file down with it.

    A broken or absent sound costs one event's noise. Never the companion.
    """
    try:
        from shani_chronoa import mascot_sounds
    except Exception:  # noqa: BLE001 - a sound module must not gate a surface
        return
    try:
        mascot_sounds.play(kind)
    except Exception:  # noqa: BLE001 - a note must never fail a notice
        logger.debug("mascot notch: the note for %r did not play", kind,
                     exc_info=True)

logger = logging.getLogger(__name__)

BUS_NAME = "org.shani.Chronoa.Mascot"
OBJECT_PATH = "/org/shani/Chronoa/Mascot"
IFACE = "org.shani.Chronoa.Mascot"

#: The extension id, only used to say in the log what has to be enabled.
EXTENSION = "chronoa-mascot@shani.dev"

#: How long a D-Bus call to the Shell may take before it is treated as "the
#: extension is not there". The Shell answers these from its own main loop and
#: every one is a few bytes of arithmetic, so a second is not "slow" - it is the
#: extension having gone away mid-session (`Alt+F2 r` restarts the Shell, and a
#: logout ends it), and blocking the app's main loop on a process that will
#: never answer is how an overlay freezes the whole desktop.
_CALL_TIMEOUT_MS = 1000

#: At most one art push per this many ms, however much changed. The gaze is
#: sampled far faster than it can be *pushed*: `St.TextureCache.load_file_sync`
#: runs on the Shell's main thread, so a push per pointer event is a push into
#: the compositor's frame loop, and the companion would be the reason the desktop
#: stutters. Coalescing means the last state in the interval is the one drawn,
#: which is the only one anybody would have seen anyway.
_PUSH_EVERY_MS = 90

#: How often the pointer is sampled for the gaze. `mascot.py`'s version is
#: event-driven off a window's `motion` signal; a strip pinned to an edge has no
#: widget to hang that on, so this samples Gdk's pointer instead - in this
#: process, with no D-Bus round trip, which is what makes it affordable at all.
_GAZE_EVERY_MS = 60

#: How often the strip's own geometry is re-read. It moves when the corner
#: changes or the monitor is resized, and both are rare, so a second is ample -
#: and it is the one call that has to cross to the Shell.
_RECT_EVERY_MS = 1000

#: What each state says on the strip's first line. **Sentences, not the states'
#: own names**: the top line of a notch is read at a glance from across a desk,
#: and "thinking" next to a mascot already looking thoughtful says the word
#: without saying anything. An unrecognised state falls back to `mascot`'s own
#: accessible vocabulary, so there is one word per state in this product and not
#: two tables disagreeing.
_TITLES = {
    "idle": "Ready when you are",
    "listening": "Listening",
    "thinking": "Working on it",
    "speaking": "Speaking",
    "interrupting": "One moment",
}

#: How long a line stays after a turn ends, so the strip does not blank the
#: instant the answer is given - which is the moment somebody reads it.
_FADE_MS = 8000


def available() -> tuple[bool, str]:
    """Whether the notch can run *here*: the extension owns the bus name.

    **The bus name is the gate, and it is the honest one.** There is no
    `is_supported()` to ask here: the question is not whether this compositor can
    do layer-shell, it is whether the thing that draws the notch is running. A
    name that nothing owns answers immediately, so the check costs one round trip
    on the same bus the calls will use.

    `mascot.available()` is consulted by `mascot.create()`, not here: the window
    is the richer surface - it can be dragged, clicked and moved - so it wins
    wherever the compositor allows it, and the notch is what is left.
    """
    try:
        from gi.repository import Gio, GLib
    except Exception as error:  # noqa: BLE001 - no typelib, so no bus
        return False, f"no GObject introspection ({error})"
    # There is deliberately no `GLib.main_depth()` gate here. This version
    # answered that question wrongly: `g_application_run` uses
    # `g_main_context_loop`, not the recursion-counted `g_main_loop_run`, so
    # depth reads **0 inside both `do_startup` and `do_activate`** on a real
    # `Gio.Application` (measured) while being 1 inside a hand-built
    # `GLib.MainLoop`. The gate therefore refused at exactly the two places the
    # app asks, and protected nothing: `call_sync` is bounded by
    # `_CALL_TIMEOUT_MS`, so "no main loop" was never a hang.
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None)
    except GLib.Error as error:
        return False, f"the session bus did not answer ({error.message})"
    if not reply.unpack()[0]:
        return False, (f"the {EXTENSION} GNOME Shell extension is not enabled - "
                       f"nothing owns {BUS_NAME}")
    return True, ""


class Notch:
    """The companion as a strip. Same surface as `mascot.Mascot`; see that class
    and this module's docstring for what the two are."""

    def __init__(self, app) -> None:
        self.app = app
        self._proxy = None
        self._dir = None
        self._art = None
        self._state = "idle"
        self._expr = ""
        self._blink = False
        self._level = 0.0
        self._gaze = (0.0, 0.0)
        self._rect = (0, 0, 0, 0)
        # The three lines the strip shows. `_detail` is the last step or speech,
        # and `_detail_until` is when it stops being true - a strip that kept
        # "Reading src/invoice.ts" on screen after the turn finished would be
        # reporting work that is not happening, which is the one thing a status
        # surface must never do.
        self._title = _TITLES["idle"]
        self._detail = ""
        self._detail_until = 0.0
        # The values the last push drew, so an unchanged frame costs nothing.
        self._drawn = None
        self._push_timer = 0
        self._dirty = False
        self._gaze_timer = 0
        self._rect_timer = 0
        self._expr_timer = 0
        self._sleep_timer = 0
        self._fade_timer = 0
        self._asleep = False
        self._last_activity = 0.0
        # When the extension's name appeared or disappeared. `Alt+F2 r` restarts
        # the Shell and the name goes with it; without this the strip would stay
        # gone until the app was restarted, which on a desktop is a thing nobody
        # does and a companion that never comes back.
        self._owner_id = 0
        self._main_thread = None
        # Where the pointer was last seen.
        self._pointer = (0.0, 0.0)
        # The display's scale, for rasterising the art at the size it is shown.
        self._scale = 1

    # --- lifecycle ------------------------------------------------------------
    @property
    def running(self) -> bool:
        """Whether the companion is up. `application.py` asks this instead of
        reaching for the window's `_win`, which this class has no reason to
        carry."""
        return self._proxy is not None

    def _allowed(self) -> bool:
        return mascot.Mascot._allowed(self)

    def start(self) -> bool:
        if self._proxy is not None:
            return True
        ok, why = available()
        if not ok or not self._allowed():
            if not ok:
                logger.info("mascot notch: %s", why)
            return False
        try:
            self._build()
        except Exception:  # noqa: BLE001
            logger.exception("mascot notch: could not raise the strip")
            self._teardown()
            return False
        import threading
        self._main_thread = threading.get_ident()
        self._last_activity = time.monotonic()
        self._owner_id = self._proxy.connect("notify::g-name-owner",
                                             self._on_owner_changed)
        self._watch_state()
        self._schedule()
        mascot_events.install(self._on_event)
        # First paint before anything is scheduled, so the strip is never a
        # frame of empty panel waiting on a timer that has not run yet.
        self._render()
        return True

    def stop(self) -> None:
        mascot_events.uninstall(self._on_event)
        if self._proxy is not None:
            # `Show(false)`, not a `Hide` - the extension's interface has no such
            # method, and a call to one it does not have comes back as an error
            # this method would swallow, leaving the strip on screen after the
            # app has quit.
            self._call("Show", False)
        self._teardown()

    def _teardown(self) -> None:
        from gi.repository import GLib
        # Every source this module can arm, and no others: a timer that outlives
        # its proxy calls `_render`, which asks for a push that cannot be made,
        # and a strip that never comes back after the app has quit is worse than
        # one that never went up.
        for name in ("_push_timer", "_gaze_timer", "_rect_timer",
                     "_blink_timer", "_unblink_timer", "_expr_timer",
                     "_sleep_timer", "_fade_timer"):
            tid = getattr(self, name, 0)
            if tid:
                GLib.source_remove(tid)
                setattr(self, name, 0)
        if self._proxy is not None and self._owner_id:
            try:
                self._proxy.disconnect(self._owner_id)
            except Exception:  # noqa: BLE001
                pass
        self._owner_id = 0
        self._proxy = None
        self._drawn = None
        self._gaze = (0.0, 0.0)
        self._rect = (0, 0, 0, 0)
        self._blink = False
        self._asleep = False
        self._main_thread = None
        self._teardown_dir()

    def _teardown_dir(self) -> None:
        """Remove this companion's art directory, and everything in it."""
        if self._dir is None:
            return
        for name in os.listdir(self._dir):
            try:
                os.unlink(os.path.join(self._dir, name))
            except OSError:
                pass
        try:
            os.rmdir(self._dir)
        except OSError:
            pass
        self._dir = None
        self._art = None

    def _build(self) -> None:
        from gi.repository import Gio
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
            BUS_NAME, OBJECT_PATH, IFACE, None)
        # A proxy for a name nobody owns constructs fine and fails on first use,
        # so the owner is checked here rather than discovered by a dropped call
        # later - at which point the strip would be up and invisible.
        if not self._proxy.get_name_owner():
            self._proxy = None
            raise RuntimeError(f"{BUS_NAME} has no owner")
        self._dir = tempfile.mkdtemp(prefix="chronoa-notch.")
        self._art = os.path.join(self._dir, "mascot.png")
        # **Rasterise at the resolution the strip will be shown at.** The
        # extension loads the file at `SIZE` logical px with the Shell's own
        # resource scale, so on a 2x display a 132px file is upscaled from half
        # the pixels it needs and the mascot is visibly soft - while a 264px
        # file on a 1x display costs nothing but the disk.
        self._scale = _display_scale()
        self._call("Show", True)

    # --- D-Bus ----------------------------------------------------------------
    #: The signature character for each Python type this module sends, so a call
    #: is written once and cannot answer "incorrect value type" for a `bool`
    #: spelled as a string. `Show` takes `b` in the extension's interface; the
    #: first version sent `"true"` and got an error it then swallowed, which is a
    #: strip that never appears and a log line nobody reads.
    _SIG = {str: "s", bool: "b", int: "i"}

    def _call(self, method: str, *args) -> "tuple | None":
        """One call to the extension, or None when it cannot be made.

        **Never raises.** The Shell is another process that can vanish mid-session
        (`Alt+F2 r`, a logout, an extension being disabled in the Extensions app),
        and this is called from the app's frame callbacks: an exception escaping
        into a GLib timeout would take the timeout with it and leave the
        companion silently frozen, which is the same failure as no companion and
        much harder to notice.
        """
        if self._proxy is None:
            return None
        from gi.repository import Gio, GLib
        params = None
        if args:
            try:
                signature = "(" + "".join(self._SIG[type(a)] for a in args) + ")"
            except KeyError as error:
                raise TypeError(f"cannot send {error.args[0]!r} over D-Bus") from None
            params = GLib.Variant(signature, args)
        try:
            reply = self._proxy.call_sync(method, params, Gio.DBusCallFlags.NONE,
                                          _CALL_TIMEOUT_MS, None)
        except GLib.Error as error:
            logger.debug("mascot notch: %s() failed: %s", method, error.message)
            return None
        if reply is None or reply.get_child_value(0) is None:
            return ()
        return reply.unpack()

    def _call_rect(self, method: str) -> tuple[int, int, int, int]:
        out = self._call(method)
        if not out or len(out) < 4:
            return (0, 0, 0, 0)
        try:
            return tuple(int(v) for v in out[:4])  # type: ignore[return-value]
        except (TypeError, ValueError):
            return (0, 0, 0, 0)

    def _on_owner_changed(self, _proxy, _param) -> None:
        """The extension's name appeared or went away."""
        owner = self._proxy.get_name_owner() if self._proxy is not None else None
        if owner:
            logger.info("mascot notch: %s came back; repainting", EXTENSION)
            self._drawn = None
            self._call("Show", "true")
            self._render()
        else:
            logger.info("mascot notch: %s went away", EXTENSION)
            self._drawn = None

    # --- drawing --------------------------------------------------------------
    def _render(self) -> None:
        """Rasterise the mascot and hand it to the strip.

        **Coalesced, and the coalescing is the interesting part.** A pointer
        sweep produces a new bearing many times between two frames the eye could
        distinguish; pushing each one would mean `St.TextureCache.load_file_sync`
        on the Shell's main thread that often, which is a re-upload of the
        compositor's own frame budget spent on a 20-pixel-wide pupil. So a change
        sets a flag and arms a timer, and the push happens once per interval with
        whatever the latest values are.
        """
        if self._proxy is None:
            return
        frame = (self._state, self._expr, self._blink, self._level,
                 round(self._gaze[0], 3), round(self._gaze[1], 3))
        if frame == self._drawn:
            return
        self._dirty = True
        if self._push_timer:
            return
        from gi.repository import GLib
        self._push_timer = GLib.timeout_add(_PUSH_EVERY_MS, self._push_tick)

    def _push_tick(self) -> bool:
        self._push_timer = 0
        if not self._dirty or self._proxy is None:
            return False
        self._dirty = False
        frame = (self._state, self._expr, self._blink, self._level,
                 round(self._gaze[0], 3), round(self._gaze[1], 3))
        self._drawn = frame
        self._push_art()
        self._push_status()
        return False

    def _push_art(self) -> None:
        """Write the PNG and tell the Shell to load it.

        **Written through a temporary name and `os.replace`d into place.** The
        Shell reads this file on its own thread, and a `save_to_png` straight
        onto the live path can be caught half-written - which decodes to a
        truncated image and an exception in the Shell's `load_file_sync`, so the
        companion disappears for a frame at exactly the moment somebody is
        watching it move. `os.replace` is atomic within a directory, so the
        Shell sees either the old file or the new one.
        """
        if self._art is None:
            return
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, GdkPixbuf
            svg = mascot_art.svg(self._state, self._expr, self._blink,
                                 self._level, self._gaze)
            side = mascot.SIZE * self._scale
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.set_size(side, side)
            loader.write(svg.encode())
            loader.close()
            scratch = self._art + ".new"
            Gdk.Texture.new_for_pixbuf(loader.get_pixbuf()).save_to_png(scratch)
            os.replace(scratch, self._art)
        except Exception:  # noqa: BLE001 - a bad frame is not a dead companion
            logger.debug("mascot notch: render failed", exc_info=True)
            return
        if self._call("Art", self._art) == (False,):
            # The path is unreadable from the Shell - a private tmpdir is the
            # usual cause, since `$XDG_RUNTIME_DIR` is per-session and the Shell
            # may be in a different one. Retrying with the same path would repeat
            # the failure, so the strip is told to hide rather than left showing
            # the last good frame as if it were live.
            logger.warning("mascot notch: the Shell could not load %s", self._art)
            self._call("Show", "false")

    def _push_status(self) -> None:
        self._call("Status", self._title, self._detail)

    # --- the two lines --------------------------------------------------------
    def _say_detail(self, text: str, seconds: float = mascot._BUBBLE_MS / 1000.0) -> None:
        """Put a line on the strip's second line, and take it away again."""
        from gi.repository import GLib
        self._detail = text or ""
        self._detail_until = time.monotonic() + max(seconds, 1.0)
        if self._fade_timer:
            GLib.source_remove(self._fade_timer)

        def fade():
            self._fade_timer = 0
            if time.monotonic() < self._detail_until:
                return True
            self._detail = ""
            self._push_status()
            return False
        self._fade_timer = GLib.timeout_add(400, fade)
        self._push_status()

    # --- lifecycle helpers ----------------------------------------------------
    def _schedule(self) -> None:
        from gi.repository import GLib
        self._gaze_timer = GLib.timeout_add(_GAZE_EVERY_MS, self._gaze_tick)
        self._rect_timer = GLib.timeout_add(_RECT_EVERY_MS, self._rect_tick)
        self._blink_timer = GLib.timeout_add(mascot._BLINK_EVERY_MS, self._blink_once)
        # **No bob timer, and that is a decision rather than an omission.**
        # `mascot.py`'s bob changes the window's bottom margin, which moves the
        # sprite inside a transparent stage - there is no margin here, the Shell
        # owns a fixed rectangle. The equivalent would be a scale change on the
        # strip, so a periodic `Scale()` call would re-upload the mascot's texture
        # eleven times a second to animate a few pixels of a panel that is
        # already alive because the eyes follow the pointer.
        self._arm_sleep()

    def _blink_once(self) -> bool:
        if self._expr or self._state != "idle" or not self.running:
            return True
        self._blink = True
        self._render()
        from gi.repository import GLib
        self._unblink_timer = GLib.timeout_add(mascot._BLINK_MS, self._unblink)
        return True

    def _unblink(self) -> bool:
        self._unblink_timer = 0
        self._blink = False
        self._render()
        return False

    def _arm_sleep(self) -> None:
        from gi.repository import GLib
        if self._sleep_timer:
            GLib.source_remove(self._sleep_timer)
        self._sleep_timer = GLib.timeout_add(mascot._SLEEP_AFTER_MS, self._sleep_tick)

    def _note_activity(self) -> None:
        self._last_activity = time.monotonic()
        if self._asleep:
            self._asleep = False
            if self._expr == "sleepy":
                self._expr = ""
            self._render()

    def _sleep_tick(self) -> bool:
        self._sleep_timer = 0
        if not self.running:
            return False
        idle_ms = (time.monotonic() - (self._last_activity or time.monotonic())) * 1000.0
        if self._state == "idle" and not self._expr and idle_ms >= mascot._SLEEP_AFTER_MS:
            self._asleep = True
            self._expr = "sleepy"
            self._title = "Resting"
            self._render()
            return False
        from gi.repository import GLib
        remaining = max(mascot._SLEEP_AFTER_MS - idle_ms, 1000.0)
        self._sleep_timer = GLib.timeout_add(int(remaining), self._sleep_tick)
        return False

    # --- gaze -----------------------------------------------------------------
    def _rect_tick(self) -> bool:
        """Re-read the mascot's own rect from the Shell."""
        self._rect = self._call_rect("Rect")
        return self.running

    def _gaze_tick(self) -> bool:
        """Sample the pointer and aim the eyes at it.

        **The pointer is read from Gdk, not from the Shell.** The Shell could
        answer `Pointer()` - the recorder uses it - but that is a round trip per
        sample, twenty times a second, forever, to learn a coordinate this
        process already has. The bearing needs the mascot's position *and* the
        pointer in the same space, and both are: the Shell's `Rect()` is in
        logical pixels and so is Gdk's pointer.
        """
        if not self.running:
            return False
        if mascot._motion_is_reduced():
            return True
        x, y, w, h = self._rect
        if w < 2 or h < 2:
            # The strip has not been placed yet (a Shell that has not finished
            # starting, or a corner change that has not been re-read). There is
            # no centre to be away from, so looking forward is the honest answer
            # rather than aiming at wherever the pointer happens to be.
            return True
        point = _pointer()
        if point is None:
            return True
        self._pointer = point
        bearing = mascot._gaze_for(point[0] - x, point[1] - y, w, h)
        if not mascot._gaze_moved(self._gaze, bearing):
            return True
        self._gaze = bearing
        self._note_activity()
        self._render()
        return True

    # --- the same surface `mascot.Mascot` offers ------------------------------
    def set_state(self, state: str) -> None:
        state = state if state in mascot_art.states() else "idle"
        if state == self._state:
            return
        self._state = state
        self._title = _TITLES.get(state, mascot._STATE_WORDS.get(state, "Ready"))
        self._note_activity()
        self._render()

    def set_level(self, level: float) -> None:
        """The halo swells with the voice, exactly as it does in the window.

        **Only the states that show it**, and only on a change the art can draw -
        the same two guards `Mascot.set_level` has, because they are the same
        decision: `mascot_art.quantise_level` owns which levels are
        distinguishable, and an idle mascot with a halo is not a thing this art
        has.
        """
        if self._state not in ("listening", "speaking"):
            return
        level = mascot_art.quantise_level(level)
        if level == self._level:
            return
        self._level = level
        self._render()

    def express(self, name: str, seconds: float = 2.6) -> None:
        from gi.repository import GLib
        self._expr = name
        self._render()
        if self._expr_timer:
            GLib.source_remove(self._expr_timer)

        def done():
            self._expr_timer = 0
            self._expr = ""
            self._render()
            return False
        self._expr_timer = GLib.timeout_add(int(seconds * 1000), done)

    def say(self, text: str) -> None:
        """A line on the strip, where the window version has a speech bubble."""
        if not self.running:
            return
        self._say_detail(text)

    def notice(self, kind: str, text: str = "") -> None:
        self._note_activity()
        _play(kind)
        expr = mascot._EVENT_FACE.get(kind)
        if expr is None:
            logger.warning("mascot notch: no face for %r; showing surprised", kind)
            expr = "surprised"
        self.express(expr, 3.0)
        if text:
            self.say(text)
        elif kind in ("call", "meeting"):
            # An event with nothing to say used to show a face and no words, which
            # reads as a reaction the companion chose rather than one somebody
            # asked for. The strip has room for the reason, so it says it.
            self._say_detail({"call": "A call is ringing",
                              "meeting": "A meeting needs you"}.get(kind, ""), 5.0)

    # --- events from the rest of the app --------------------------------------
    def _on_event(self, kind: str, text: str = "") -> None:
        """One `mascot_events` event, from whichever thread produced it.

        **Producers are not on the GTK thread** - a permission is decided on the
        tool loop's executor - so the hop is the same one `Mascot._on_event`
        makes. Every D-Bus call here is synchronous, and a synchronous call from
        a worker thread into a GTK process is how a deadlock starts.
        """
        import threading
        if self._main_thread is not None and threading.get_ident() != self._main_thread:
            try:
                from gi.repository import GLib
                GLib.idle_add(self._on_event_here, kind, text)
            except Exception:  # noqa: BLE001
                logger.debug("mascot notch: could not queue %r", kind, exc_info=True)
            return
        self._on_event_here(kind, text)

    def _on_event_here(self, kind: str, text: str = "") -> None:
        if kind == "step":
            self._note_activity()
            self.say(text)
            return
        self.notice(kind, text)

    def _watch_state(self) -> None:
        """Mirror the app's real assistant state, as the window version does."""
        action = self.app.lookup_action("status") if self.app is not None else None
        if action is None:
            return
        try:
            self.set_state(action.get_state().get_string())
        except Exception:  # noqa: BLE001
            pass
        action.connect("notify::state", lambda a, _p: self.set_state(
            a.get_state().get_string() if a.get_state() is not None else "idle"))


def _display_scale() -> int:
    """The scale factor of the display the strip will be shown on, at least 1.

    Read from Gdk rather than asked of the Shell: it is the same number, this
    process already knows it, and the extension asks the Shell's own
    `St.ThemeContext` for the same value at load time - so neither side is
    trusting the other with a number both can read.
    """
    try:
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is not None:
            monitor = display.get_monitor(0) if display.get_n_monitors() else None
            if monitor is not None:
                return max(1, int(monitor.get_scale_factor()))
    except Exception:  # noqa: BLE001 - 1x is the safe assumption
        logger.debug("mascot notch: no monitor scale", exc_info=True)
    return 1


def _pointer() -> "tuple[float, float] | None":
    """The pointer in Shell logical pixels, or None when there is no display."""
    try:
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is None:
            return None
        seat = display.get_default_seat()
        if seat is None:
            return None
        ok, x, y = seat.get_pointer().get_position()
        if not ok:
            return None
        return (float(x), float(y))
    except Exception:  # noqa: BLE001 - no pointer is not an error worth raising
        logger.debug("mascot notch: no pointer", exc_info=True)
        return None
