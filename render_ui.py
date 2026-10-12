#!/usr/bin/env python3
"""Render one of Chronoa's own windows to a PNG, offscreen.

Run as a script, not imported into a test, and that is deliberate. Every version
of this that lived inside a pytest process returned an empty PNG while the same
code worked here - the failure is a GTK main-loop ordering problem that only
disappears when the script owns the process. So the test runs this and measures
what comes out, rather than reimplementing the sequence and losing the battle
again.

Requirements, all four of which fail by producing an empty PNG rather than an
error. See `AGENTS.md` for the full account:

- `Adw.init()` at module scope, before the `Gtk.Application` is constructed.
  Adw widgets built before it render nothing.
- `present()` in the activate handler, and the snapshot in a later turn of the
  loop. A tree built inside `activate` is unallocated, and calling `present()`
  again from inside the capture re-maps the window.
- `save_to_png(path)`, not `save_to_png_bytes()`, on a Cairo-renderer texture.
- A lowercased application id; a D-Bus name may not contain `Adwaita`.
- **This window cannot be scrolled offscreen.** Measured on libadwaita 1.5 with
  no window manager: the preferences page is ~9300px tall, the window is clamped
  to the size asked for, `Gtk.ScrolledWindow` then reports an adjustment range
  equal to its own height, and `set_value()` on it silently succeeds and moves
  nothing. Asking for a 9450px window instead is not a way round it - the capture
  comes back with the glyphs horizontally squeezed, i.e. scaled rather than
  rendered. Photographing the panel widget on its own is not a way round it
  either: a detached snapshot leaves the page background unpainted (measured: page
  alpha 0, group description alpha 140) and does not inherit the window's
  color-scheme, so the header comes out invisible and the rows come out light.
  What does work is the window's own search - typing "Tool activity" hides every
  other group, the page becomes short, and the panel is in frame in a fully
  painted window. That is a real user action rather than a trick, and it is what
  `tool-activity` does.

Usage: render_ui.py <settings|main|help|tool-activity|empty|panel:NAME|page:WINDOW:ID>
                    <out.png> <width> <height> [theme]
"""

import json
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gio, GLib, Gdk, Graphene, Gsk, Gtk  # noqa: E402

# Before the application exists, not inside `activate`.
Adw.init()

# `--list` takes no width, height or output path, so the ordinary positional
# parse is skipped for it rather than raising on `sys.argv[3]`. The first
# version parsed unconditionally and every `--list` died with an IndexError,
# which is how an enumeration mode can look like an empty one.
_LISTING = len(sys.argv) > 1 and sys.argv[1] == "--list"
if _LISTING:
    WHICH = OUT = THEME = ""
    WIDTH = HEIGHT = 0
else:
    WHICH, OUT = sys.argv[1], sys.argv[2]
    WIDTH, HEIGHT = int(sys.argv[3]), int(sys.argv[4])
    THEME = sys.argv[5] if len(sys.argv) > 5 else ""

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "usr", "lib", "shani-chronoa")
)

from shani_chronoa.config import ChronoaConfig  # noqa: E402


def _walk(node, out):
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _press_quick_ask(window):
    """Press Quick Ask's own Ask button, so the answer panel is in the picture.

    Driven through the button rather than by calling the handler: a window
    photographed with an empty answer box is the state before anybody has used
    it, and the half that answers is the half worth looking at. `_walk` is the
    file's own collector - a second one would be a second answer to "what is
    under this widget", which is how `common.rows_of` had to be retired.
    """
    for child in _walk(window, []):
        if type(child).__name__ == "Button" and child.get_label() == "Ask":
            child.emit("clicked")
            return


def _seed_tool_activity():
    """One call per verdict, plus one the log holds and the ring cannot know about.

    The point of the render is the four renderings side by side, so the fixture
    has to contain all four - a panel photographed with only `verified` rows
    says nothing about the three states that are easy to get wrong.
    """
    from shani_chronoa import tool_tracking, tools
    tools._TRACKER.record_call("set_brightness", {"level": 40}, "Set to 40%", 12.0,
                               verdict="verified", evidence="the backlight reads 40%")
    tools._TRACKER.record_call("delete_file", {"path": "/tmp/opencode/junk.txt"},
                               "All done successfully.", 8.0, verdict="failed",
                               evidence="the path is still there and is not in the trash")
    tools._TRACKER.record_call("get_datetime", {}, "2026-10-01T16:40:00", 3.0,
                               verdict="unverified")
    tools._TRACKER.record_call("notify", {"summary": "lunch is ready"}, "", 1.0)
    tools._TRACKER.record_call("move_pointer", {"x": 10, "y": 20}, "moved", 4.0,
                               origin="unattended", verdict="failed",
                               evidence="the pointer did not move")
    with tool_tracking.LOG_FILE.open("a") as fh:
        fh.write(json.dumps({
            "timestamp": "2026-09-30T08:00:00+00:00",
            "tool_name": "get_battery_status", "args": {}, "result": "82%",
            "duration_ms": 210.5, "origin": "user", "verdict": "unverified",
            "evidence": "",
        }) + "\n")


class App(Gtk.Application):
    """A real Gtk.Application: `application=` is a typed property, so a duck
    type is rejected outright."""

    def __init__(self):
        # NON_UNIQUE: two renders running at once otherwise register the same
        # name, and the second becomes a remote that forwards `activate` to the
        # first and exits having written nothing - every render in a parallel
        # batch but one came back empty for exactly that reason.
        super().__init__(application_id="test.chronoa.render",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = ChronoaConfig()
        self.window = None
        self._wake_word_active = False

    def activate_action(self, name, arg):
        pass


def _in_a_window(widget):
    """`widget` itself if it is a window, otherwise it in one.

    **A widget snapshotted detached has no surface to draw into, and returns
    `None` for its node.** That is AGENTS.md's own recorded trap for an editable
    widget, and it is why the first version of the strip and organ states
    reported NO NODE for all nineteen of them: a `Gtk.FlowBox` that was built,
    styled and never mapped paints nothing at all, and `NO NODE` is the correct
    answer rather than a fault.

    So the widget is put in a real window, presented, and the *window* is what
    is captured. The strip's own measurement, alignment and CSS all apply,
    because it is the same widget - which is the difference from photographing a
    lookalike.
    """
    if isinstance(widget, Gtk.Window):
        return widget
    window = Gtk.Window()
    # No titlebar: it is harness chrome, it eats a third of a short window's
    # height, and "python3" in a picture of Chronoa's mode strip is a lie about
    # what was rendered.
    window.set_decorated(False)
    # A `Gtk.Box` in a window grows to fill it, and each chip became a
    # full-height column with its label stranded mid-way - the shape of the
    # wrapper, not of the product. The strip is a row; it should be a row.
    if hasattr(widget, "set_vexpand"):
        widget.set_vexpand(False)
    window.set_child(widget)
    # **Size the window to what the widget actually wants.** A `Gtk.Box` handed a
    # whole window expands to fill it, and a mode strip photographed that way is
    # five full-height columns with each chip's label stranded in the middle -
    # a picture of the harness, not of the product. Found by *looking* at a
    # render whose byte count was perfectly plausible; no size check would ever
    # have said it was wrong. So the window takes the widget's own measurement,
    # which is also the shape the strip has inside the real window.
    natural = Gtk.Widget.measure(widget, Gtk.Orientation.HORIZONTAL, -1)
    window.set_default_size(min(WIDTH, max(320, natural[1])),
                            max(40, natural[2]))
    window.set_size_request(-1, -1)
    return window


def _snapshot(widget, width, height):
    """The render node for `widget`, or `None`.

    This is where AGENTS.md's "NO NODE" trap lives: a `Gtk.WidgetPaintable` over
    a window whose child is an `Adw.NavigationView` returns None even though the
    window is mapped and fully allocated, and the *same* paintable over that same
    NavigationView returns a real node. So it is a wrong argument, not a broken
    page - and the cure is to walk down rather than to conclude.
    """
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    return snapshot.to_node()


def _deepest_node(widget, width, height, depth=0):
    """The deepest descendant that yields a node, falling back to the parent.

    A renderer that only ever shoots the top of a tree reports NO NODE for every
    window whose content is a NavigationView, which on this box is the browser
    window and anything else built on one.

    **The whole tree is tried first and the walk is only a fallback.** The first
    version preferred the deepest node-bearing child, which sounded more thorough
    and was much worse: `panel:machine` went from 203,035 bytes to 21,532 and
    `help` from 171,840 to 35,885, because the node it found was an inner box
    rather than the window. A capture that succeeds and photographs less is a
    worse failure than NO NODE, because it looks like a render. So: the top
    answers whenever it answers at all, and the walk exists only for the windows
    where it does not.
    """
    node = _snapshot(widget, width, height)
    if node is not None:
        return node
    children = []
    child = widget.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    best = None
    for child in children:
        found = _deepest_node(child, width, height, depth + 1)
        if found is not None:
            # First child that answers wins; a later one must not displace it.
            if best is None:
                best = found
    return best if best is not None else node


def _render(widget, width, height, path, own_size=False):
    """Capture `widget`. `own_size` uses the widget's own measurement.

    A viewport of WIDTH x HEIGHT over a window smaller than that **stretches**,
    and a 40px-tall strip photographed into 900px came back as smeared glyphs
    with a black band - a broken picture from a successful render, which is the
    worst of both. So anything sized to its content is captured at its content.
    """
    if own_size:
        # **The window's allocation, not its measurement.** Measuring a widget
        # that has not been through a layout pass reports the size before CSS
        # and children are resolved - measured 1019 bytes, a picture of almost
        # nothing - and the window sized to *that* is sized to the wrong thing
        # too. By capture time the window is mapped and its allocation is what a
        # reader would see.
        allocated_w = widget.get_width()
        allocated_h = widget.get_height()
        if allocated_w > 1 and allocated_h > 1:
            width, height = min(width, allocated_w), min(height, allocated_h)
    node = _deepest_node(widget, width, height)
    if node is None:
        print("NO NODE", flush=True)
        return False
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(None)
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
    if texture is None:
        print("NO TEXTURE", flush=True)
        return False
    texture.save_to_png(path)
    print("WROTE", flush=True)
    return False


def _frame_panel(window, css_class, needle):
    """Hide every group but the one named `css_class`, by using the real search.

    The alternative - scrolling the window, or rendering the panel detached - is
    documented in this module's docstring and both produce a picture that is not
    what a user sees. This is the filter the window ships, driven with a typed
    needle, so every remaining pixel is the real window rendering itself.
    """
    group = next((n for n in _walk(window.get_child(), [])
                  if css_class in n.get_css_classes()), None)
    if group is None:
        print(f"NO PANEL {css_class}", flush=True)
        return False
    window._search.insert_text(needle, 0)
    print(f"FRAMED {css_class} with needle {needle!r}", flush=True)
    return False


def _report_framing(window):
    """How many groups the needle actually hid - a turn later.

    `set_visible()` does not take effect within the turn that asks for it, so
    reading it in the same turn reported "0 of 23 hidden" over a panel that was
    plainly the only thing on screen. A diagnostic that cannot be right is worse
    than none, which is this repo's own rule about display probes.
    """
    hidden = sum(1 for g, _t in window._searchable if not g.get_visible())
    print(f"HIDDEN {hidden} of {len(window._searchable)} groups", flush=True)
    return False


# --- interaction states ------------------------------------------------------
#
# **Absorbed from the former `tools/render_ui_states.py`.** It was a second
# renderer with its own `Adw.init()`, its own `Gtk.Application`, its own
# `capture()` and its own `settle()` - a second copy of the four requirements
# AGENTS.md writes down, which is the shape that lets one copy drift unnoticed.
#
# Every state is produced by **driving the real code** - setting what the control
# reads, then calling its own refresh - rather than by building a lookalike. A
# hand-built mock photographs the mock, which is the failure this repository
# records repeatedly: *"a fixture shaped like the call rather than like the thing
# being called tests the bug and passes."*
#
# **These are the states a first paint cannot show.** Which row of a mode chip is
# selected is decided by `quiet=`; what the composer looks like with text in it;
# whether the stop and undo buttons are ever visible. `render_ui`'s page targets
# photograph a window as it first appears, which is a state nobody spends time in.
#
# `_APP` is this module's single application, handed to the builders. They cannot
# make their own: a second `Gtk.Application` in one process is the segfault
# `test_composer_and_escape.py` records, and it is why `app.register(None)` twice
# takes the process down rather than raising.

_APP = [None]

#: Whether `local_llm.is_up` answers true for the state being rendered. A list,
#: so the lambda a probe closes over is rebound per state rather than captured
#: once - the closure-capture bug, which would render every presence state with
#: the first one's answer.
_MACHINE_UP = [False]

#: Windows a target opens only to keep the application alive. A local variable
#: would be collected before the capture runs, and the application would quit
#: with the file unwritten - the same silent exit, from a different cause.
_KEEP = []


def _strip_state(state: str):
    """One row of one mode chip, selected by driving what the chip reads.

    The five chips read five different things - a settings key, the permission
    layer, `planmode`, `presence`, and a value *derived* from two settings - so
    each state sets what its own chip actually reads. Setting a variable the
    chip ignores would photograph the default again, which is the check that
    cannot fail.
    """
    from shani_chronoa.gui.window import _ModeStrip

    config = _Config()
    strip = _ModeStrip(_APP[0], config)
    strip.set_size_request(760, -1)

    # `state` is "strip-<chip>-<row>", and a row key may itself contain a dash
    # ("this-machine", "local-answers"). `partition("-")` splits at the FIRST
    # dash, so it yielded chip="strip" - the chip was never driven and every
    # strip state photographed the default. The chip is the *second* field and
    # the row is the rest.
    _prefix, _chip, row_key = state.split("-", 2)
    chip_name = _chip
    _apply_row(strip, config, chip_name, row_key)
    if hasattr(strip, "refresh"):
        strip.refresh()
    return strip, f"mode strip: {chip_name} = {row_key}"


def _apply_row(strip, config, chip_name, row_key) -> None:
    """Make the chip believe this row is current, **through the thing that owns it**.

    Every chip reads a different owner, and the strip's own docstring says so:
    *"every read goes to the thing that owns the state"*. The first version of
    this function set one settings key per chip - `build-mode`,
    `permission-posture`, `answer-mode` - and **three of those five keys are read
    by nothing**. Measured: eleven strip states rendered and every pair was
    **byte-identical**, so the sweep reported eleven passes over the same
    photograph of the default. That is this repository's recurring failure in its
    purest form - a control that cannot fail - and it is invisible in the output
    because a successful render of the wrong state looks exactly like a
    successful render.

    Read back from `_ModeStrip`'s own `refresh()`, one reader per chip:

    | chip | what it actually reads |
    |---|---|
    | capability | `buildmode.is_enabled()` |
    | auto | `permissions.get_mode()` |
    | plan | `planmode.is_enabled()` |
    | ambient | `config.get_bool("background-mode-enabled")` |
    | presence | `app._presence`, reconciled by `presence.reconcile` |
    | hands | `config.wake_word_enabled`, `config.barge_in_vad_enabled` |
    | egress | `config.privacy_mode` **and** the stored answer mode |
    """
    if chip_name == "capability":
        from shani_chronoa import buildmode
        buildmode.set_enabled(row_key == "build")
    elif chip_name == "auto":
        from shani_chronoa import permissions
        permissions.set_mode(permissions.Mode.DONT_ASK if row_key == "auto"
                             else permissions.Mode.DEFAULT)
    elif chip_name == "plan":
        from shani_chronoa import planmode
        planmode.set_enabled(row_key == "plan")
    elif chip_name == "ambient":
        config.set_bool("background-mode-enabled", row_key == "background")
    elif chip_name == "presence":
        from shani_chronoa import presence
        # Set on the **application**, because that is what `_read_presence`
        # reads first: `chosen = getattr(self._app, "_presence", None)`.
        #
        # Calling `reconcile()` here was both less faithful and broken. Its
        # second argument is the `is_up` **callable** and its body does
        # `Presence.ACTIVE if is_up() else Presence.DROWSY`, so passing the
        # value `False` - which read as "the model is not resident" - raised
        # `TypeError: 'bool' object is not callable` on two of the three rows.
        # And even had it worked it would have been the wrong call: with a bare
        # `Gtk.Application` there is no `_presence` to reconcile *from*, so the
        # reader fell through to `detect(local_llm.is_up)` and photographed the
        # machine rather than the choice - the one thing this chip is about.
        #
        # The chip table spells its rows lowercase ("sleeping") and the enum
        # spells them uppercase (`Presence.SLEEPING`), so the two are mapped
        # rather than looked up by the table's own spelling.
        strip._app._presence = presence.Presence[row_key.upper()]

        # **Both halves, or the chip photographs the reconciliation.** Set the
        # machine too. `reconcile` returns ACTIVE only when `is_up()` says so
        # and DROWSY otherwise, so on this box - where no model is loaded - a
        # stored ACTIVE reconciles straight back to DROWSY and `active` and
        # `drowsy` render byte-identical. That is the product being *correct*
        # ("a presence that disagrees with the machine is worse than no
        # presence"), so the fix is to drive the machine rather than to
        # photograph a disagreement as if it were a distinct state.
        #
        # A state that deliberately keeps them apart is `stale`: a stored ACTIVE
        # with the machine down, which is the real sequence after a cold start.
        from shani_chronoa import local_llm
        _MACHINE_UP.append(row_key == "active")
        local_llm.is_up = lambda: _MACHINE_UP[-1]
    elif chip_name == "hands":
        config.wake_word_enabled = row_key in ("wake", "wake-over")
        config.barge_in_vad_enabled = row_key in ("over", "wake-over")
    elif chip_name == "egress":
        # Two settings and a mapping, because `_read_egress` reads both and then
        # maps a *value* to a word:
        #     {"cloud": "cloud", "local": "local-answers"}.get(mode,
        #                                                 "local-then-cloud")
        # with `privacy-mode` short-circuiting to "this-machine" first. So the
        # four rows need four different *pairs*, and reading them as one setting
        # apiece left every row showing the same chip - measured as
        # `strip-egress-cloud` rendering byte-identical to `strip-auto-ask`.
        # `answer-mode` is a **property** here, not a `get` key, so storing it in
        # the dict left the reader at its default for every row.
        config.privacy_mode = row_key == "this-machine"
        config.answer_mode = {"this-machine": "local",
                              "local-answers": "local",
                              "local-then-cloud": "local-cloud",
                              "cloud": "cloud"}[row_key]
    elif chip_name == "style":
        config.reply_style = row_key


class _Config:
    """The smallest thing the chips read.

    A real `ChronoaConfig` needs a compiled schema and writes through GSettings,
    which a render has no business doing. This carries values in a dict and
    answers the same accessors, so the chips read a source rather than a default.
    """

    def __init__(self):
        self._values: dict = {}
        self._bools: dict = {}

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_bool(self, key, default=False):
        return self._bools.get(key, default)

    def get_string(self, key, default=""):
        return self._values.get(key, default)

    def get_double(self, key, default=0.0):
        return float(self._values.get(key, default))

    def set(self, key, value):
        self._values[key] = value

    def set_bool(self, key, value):
        self._bools[key] = value

    # **The strip reads four of these as properties, not through `get_bool`.**
    # Measured by grepping what `_ModeStrip` actually touches: `privacy_mode`,
    # `reply_style`, `barge_in_vad_enabled` and `wake_word_enabled`. A stub
    # carrying only the dict accessors is a stub shaped like the *call* rather
    # than like the thing being called - the `meeting.notes` fault - and it
    # raised `'_Config' object has no attribute 'privacy_mode'` on the first
    # chip, which is how this was found rather than guessed.
    privacy_mode = False
    answer_mode = ""
    reply_style = ""
    barge_in_vad_enabled = False
    wake_word_enabled = False


#: Every row of every chip, read from the strip's own tables rather than typed -
#: a hand-kept list of what a class already declares is a list that rots, and
#: the chip tables have changed shape several times in this file's history.
def _strip_states():
    """Every row of every chip, read from the strip's own tables.

    **The first list asked for `_RULES` and `_MODES`, which do not exist.** The
    enumeration silently returned nothing for those two and the sweep reported a
    clean run - the "an absent result reads as a pass" failure. The table names
    are read off `_ModeStrip` itself, and `_strip_states` is what a target group
    is built from, so a table renamed upstream now shows up as a missing chip
    rather than as a quietly shorter sweep.
    """
    from shani_chronoa.gui.window import _ModeStrip
    out = []
    for chip, table in (("capability", "_CAPABILITY"), ("auto", "_AUTO"),
                        ("plan", "_PLAN"), ("ambient", "_AMBIENT"),
                        ("presence", "_PRESENCE"), ("hands", "_HANDS"),
                        ("egress", "_EGRESS"), ("style", "_STYLES")):
        rows = getattr(_ModeStrip, table, None)
        if not rows:
            raise RuntimeError(
                f"_ModeStrip.{table} is gone - the strip has changed shape and "
                f"this enumerator would quietly skip the {chip} chip")
        for row in rows:
            out.append(f"strip-{chip}-{row[0]}")
    return out


def _composer_state(state: str):
    """The composer with something in it, or empty, or asking a question."""
    config = _Config()
    from shani_chronoa.gui.window import ChronoaWindow
    window = ChronoaWindow(_APP[0])
    window._config = config
    _drive_composer(window, state)
    return window, f"composer: {state}"


def _drive_composer(window, state: str) -> None:
    from shani_chronoa.gui import AssistantState
    if state == "text":
        window._set_input_text("what is on my calendar today?")
    elif state == "multiline":
        window._set_input_text("compare these two files\nand tell me which is newer")
    elif state == "attachment":
        window._set_input_text("what is in this screenshot?")
        for name in ("screenshot.png", "notes.txt"):
            try:
                window._attachments.add(name)
            except Exception:
                pass
    elif state == "question":
        try:
            window._question_row.set_visible(True)
            window._question_entry.get_buffer().set_text("Delete this file?")
        except Exception:
            pass
    elif state == "speaking":
        window._sync_from_state(AssistantState.SPEAKING)
    elif state == "thinking":
        window._sync_from_state(AssistantState.THINKING)


def _organ_state(state: str):
    """One organ light lit, driven through the register the strip reads.

    `OrganStrip.refresh()` asks `body.body.snapshot()` and nothing else, so a
    render that wanted a lit light has to light it *there*. The first version
    called a `set_organ` the class does not have, inside a bare `except` -
    which means every organ state photographed the same idle strip and reported
    no error, the shape this repository records as "a fixture that cannot fail".

    The light is held open past the capture on purpose: `body.lit` is a context
    manager that puts the light out on exit, so exiting it before the snapshot
    is taken photographs exactly the state it was supposed to show.
    """
    from shani_chronoa import body
    from shani_chronoa.gui.organs import OrganStrip

    strip = OrganStrip(_Config())
    strip.set_size_request(760, -1)
    # **The organ name is the part after `organ-`.** `body.use` validates
    # against `ORGANS` and refuses anything else - correctly, with "a light with
    # no name is worse than no light" - so passing the whole state string
    # `organ-brain` logs "not an organ" for all eight and lights nothing. The
    # first version of this line looked like a fix and was a no-op replace, which
    # is why eight states kept failing identically.
    organ = state.split("-", 1)[1]
    body.body.use(organ, f"the {organ} organ is doing its thing",
                  deadline=float("inf"))
    strip.refresh()
    return strip, f"organ: {state} lit"


def _all_states():
    """Every state this file can render, from the code rather than a list."""
    out = []
    out += _strip_states()
    out += [f"composer-{s}" for s in
            ("empty", "text", "multiline", "attachment", "question",
             "speaking", "thinking")]
    # **No bare `except` here, and the table is the one that exists.**
    # `ORGAN_ICONS` lives in `gui/organs.py`, not in `body` - so the first
    # version's `body.ORGAN_ICONS` raised `AttributeError`, the `except: pass`
    # swallowed it, and the enumerator reported **18 states** while silently
    # dropping every organ state. A sweep that quietly loses a third of its
    # population and prints a confident number is worse than one that fails.
    from shani_chronoa.gui.organs import ORGAN_ICONS
    out += [f"organ-{name}" for name in sorted(ORGAN_ICONS)]
    return out



def on_activate(app):
    if THEME:
        Gtk.Settings.get_default().set_property("gtk-theme-name", THEME)
    if WHICH.startswith("panel:"):
        # Any panel in the registry, by its id: `panel:machine`, `panel:senses`.
        #
        # Added because "look at it" was otherwise impossible for most of this
        # app. The four fixed views below cover the conversation, the settings
        # window and the help window, and every one of the twenty panels -
        # including the ones that were fixed in this pass - could not be
        # photographed at all. A defect found by reading a screenshot is worth
        # nothing if there is no way to take a screenshot of the screen it is on.
        #
        # The panel is pushed onto a real window through the real `_show_surface`,
        # so this captures what a person gets after clicking the sidebar row,
        # not a panel widget photographed on its own (which AGENTS.md records as
        # rendering with an unpainted page background).
        name = WHICH.split(":", 1)[1]
        from shani_chronoa.gui import ChronoaWindow
        window = ChronoaWindow(app)
        window.set_default_size(WIDTH, HEIGHT)
        window.show_page("panels")
        window._show_surface(name)
        window.present()
        # **3500ms, the same settle `main` uses, and not the 1800 this branch
        # used.** The main window seeds every panel's health dot on idle after it
        # maps - twenty-odd `systemctl`/`busctl`/`gsettings` probes, measured at
        # about two seconds - and a panel is pushed into that same window. At
        # 1800ms the sidebar is still filling, and the capture photographs a
        # window mid-settle: **three runs gave 202,899 / 202,918 / 32,707
        # bytes**, so one render in three was a picture of a window that had not
        # finished looking like itself. It is not a rare failure to shrug at
        # either - a small file is a *valid* PNG, so every size check passes and
        # the sweep reports a clean success over a half-built page.
        GLib.timeout_add(3500, lambda: _render(window, WIDTH, HEIGHT, OUT))
        GLib.timeout_add(5600, lambda: (app.quit(), False)[1])
        return None
    if WHICH.startswith("page:"):
        # Any registered page of any window: `page:settings:privacy`,
        # `page:setup:review`. Goes through `pages.show()` - the same entry point
        # as `--show-page=` - so what is photographed is what that flag opens,
        # and a page id that has drifted from its window fails here as "REFUSED"
        # rather than producing a picture of whatever page was already up.
        from shani_chronoa import pages
        target = WHICH.split(":", 1)[1]
        if not pages.show(target, app, app.config):
            print(f"REFUSED {target}", flush=True)
            app.quit()
            return None
        window = pages._WINDOWS[target.split(":", 1)[0]]
        # `show()` has already presented it, at its own default size. Without
        # an explicit size the snapshot below stretches a smaller window up to
        # WIDTHxHEIGHT and every glyph comes out scaled - legible, and wrong.
        window.set_default_size(WIDTH, HEIGHT)
        window.set_size_request(WIDTH, HEIGHT)
        GLib.timeout_add(1800, lambda: _render(window, WIDTH, HEIGHT, OUT))
        GLib.timeout_add(3200, lambda: (app.quit(), False)[1])
        return None
    if WHICH.startswith("state:"):
        name = WHICH.split(":", 1)[1]
        builder = {"strip": _strip_state, "composer": _composer_state,
                   "organ": _organ_state}.get(name.split("-")[0])
        if builder is None:
            print(f"NO STATE BUILDER for {name!r}", flush=True)
            app.quit()
            return None
        window, caption = builder(name)
        print("STATE:", caption, flush=True)
        # A strip is a `Gtk.FlowBox` and the composer is a `Gtk.ApplicationWindow`;
        # only the second has a size to set. Asking unconditionally raised on
        # every strip and organ state, which is the third window-shaped widget
        # this renderer has now had to special-case - so the guard is here rather
        # than added again by the next target.
        if hasattr(window, "set_default_size"):
            window.set_default_size(WIDTH, HEIGHT)
        # Kept alive: a bare widget holds no reference that stops the application
        # quitting when `activate` returns.
        _KEEP.append(window)
        # **`GApplication.run()` returns as soon as nothing holds it open, and
        # only a *window* does.** A bare `Gtk.FlowBox` in a module-level list is
        # a reference, not a hold, so `activate` returned, `run()` returned, and
        # every capture was skipped with no output at all - measured as exactly
        # the 7 composer states (which are `ChronoaWindow`s and hold it open)
        # passing and all 19 strip and organ states (which are bare widgets)
        # failing. `hold()` is the instrument for "no window, but stay alive".
        _KEEP.append(app.hold())
        window = _in_a_window(window)
        window.present()
        hold = _KEEP.pop()

        def capture():
            _render(window, WIDTH, HEIGHT, OUT, own_size=True)
            # **Destroy the window and drop the hold before quitting.** A sweep
            # runs many renders against one broadway display, and a window that
            # is never destroyed leaves its surface on the server for the rest
            # of the run: measured, a 6-state sweep passed and a 12-state sweep
            # stopped after two, with every later target reporting "no file
            # written" and no error - the server had run out of room for new
            # surfaces. The count is not fixed, which is why it read as flakiness
            # rather than as a leak.
            window.destroy()
            _KEEP.remove(window)
            app.release(hold)
            app.quit()
            return False

        GLib.timeout_add(2000, capture)
        GLib.timeout_add(9000, lambda: (app.quit(), False)[1])
        return None
    if WHICH.startswith("group:"):
        # One *group* of the settings window, isolated through the window's own
        # search filter - the way a person reaches it, not by poking rows.
        #
        # **The seven registered sections are not the whole window.** The window
        # builds ~25 `Adw.PreferencesGroup`s and the section chips only *reveal*
        # families of them, so `page:settings:senses` is one view of a window
        # with far more in it than any registry names. This target reaches a
        # group by its own visible title, which is what a reader is looking for
        # and what no id carries.
        #
        # Absorbed from the former `render_group.py`, which was a second
        # renderer with its own copy of the capture path, its own `App` and its
        # own `Adw.init()` - three copies of the recipe AGENTS.md writes down,
        # which is exactly the shape that lets one of them drift.
        from shani_chronoa.settings_window import SettingsWindow
        needle = WHICH.split(":", 1)[1]
        window = SettingsWindow(app)
        window.set_default_size(WIDTH, HEIGHT)
        window.present()

        def filter_then_render():
            window._search.insert_text(needle, 0)

            def capture():
                for g in _walk(window, []):
                    if isinstance(g, Adw.PreferencesGroup) and g.get_visible():
                        print("VISIBLE GROUP:", g.get_title(), flush=True)
                _render(window, WIDTH, HEIGHT, OUT)
                app.quit()
                return False

            GLib.timeout_add(600, capture)
            return False

        GLib.timeout_add(1200, filter_then_render)
        GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
        return None
    if WHICH == "tool-activity":
        from shani_chronoa.settings_window import SettingsWindow
        _seed_tool_activity()
        window = SettingsWindow(app)
    elif WHICH == "settings":
        from shani_chronoa.settings_window import SettingsWindow
        window = SettingsWindow(app)
    elif WHICH == "help":
        from shani_chronoa.gui import ChronoaWindow
        parent = ChronoaWindow(app)
        _KEEP.append(parent)
        window = parent.open_help()
    elif WHICH == "shortcuts":
        # `gui/about.py`'s own window, reached the way a person reaches it.
        # **Exported from `gui/__init__.py` with zero product callers** - this
        # target is what makes that claim checkable rather than asserted, and it
        # is also the only way to look at the window at all.
        from shani_chronoa.gui import ChronoaWindow
        from shani_chronoa.gui.about import ShortcutsWindow
        # **A window nobody holds open lets `GApplication` quit underneath the
        # render.** `Gtk.Application.run()` returns as soon as the last window
        # closes, and an `Adw.ShortcutsDialog` presented with no parent does not
        # hold the application - so `on_activate` returned, `run()` returned, and
        # the process exited 0 having printed nothing and written no file. That is
        # indistinguishable from a target that does not exist, which is why it
        # took a probe rather than a read to find.
        #
        # `help` works for the same reason its own code does: it keeps a
        # `ChronoaWindow` parent alive. This does the same, and `_KEEP` holds the
        # reference so the parent is not collected before the capture.
        _KEEP.append(ChronoaWindow(app))
        window = ShortcutsWindow(application=app)
        # **An `Adw.ShortcutsDialog` is not a `Gtk.Window`.** `about.py`'s own
        # docstring says the two results are "widgets, so present() and close()
        # work on either" - which is true of those two calls and not of the
        # sizing ones, and the first version asked unconditionally and so never
        # reached the capture on the branch this machine takes.
        if hasattr(window, "set_default_size"):
            window.set_default_size(WIDTH, HEIGHT)
    elif WHICH == "about":
        from shani_chronoa.gui.about import AboutWindow
        window = AboutWindow(application=app)
    elif WHICH == "quick-ask":
        # A separate `Gtk.Window`, opened by `app/conversation.py:125` from
        # Ctrl+Shift+A. It is not a page of any window, so `pages.show()` cannot
        # reach it and no registry names it - which is exactly the class of
        # surface a registry-driven sweep cannot see.
        from shani_chronoa.gui.quick_ask import QuickAskWindow
        window = QuickAskWindow(
            application=app,
            on_ask=lambda text, done: done("Lisbon is 14:32"),
            on_keep=lambda q, a: None,
        )
        window._entry.set_text("what time is it in Lisbon")
        GLib.idle_add(lambda: (_press_quick_ask(window), False)[1])
    elif WHICH == "browser":
        from shani_chronoa.gui.browser import BrowserWindow
        window = BrowserWindow(app)
    elif WHICH == "body":
        # `open_body_page()`'s `OrganPanel` - the organ-body page, keyed "body" in
        # the window's own page dict and **not** in `SURFACE_IDS`, so the panel
        # sweep cannot reach it either.
        from shani_chronoa.gui import ChronoaWindow
        parent = ChronoaWindow(app)
        parent.open_body_page()
        parent.set_default_size(WIDTH, HEIGHT)
        parent.set_size_request(WIDTH, HEIGHT)
        parent.present()
        GLib.timeout_add(2500, lambda: _render(parent, WIDTH, HEIGHT, OUT))
        GLib.timeout_add(5600, lambda: (app.quit(), False)[1])
        return None
    else:
        from shani_chronoa.gui import ChronoaWindow
        window = ChronoaWindow(app)
        if WHICH == "empty":
            pass
        else:
            window.add_user_turn("how much disk space is left?")
            window.set_response("Root has **38G** free of 120G.\n- `/` is 32% full")
    if hasattr(window, "set_default_size"):
        window.set_default_size(WIDTH, HEIGHT)
    # **The capture and the quit are armed BEFORE present().** `present()` on a
    # modal dialog runs a *nested* main loop, so every statement after it in this
    # function never executes - and the process then exits 0 having printed
    # nothing at all, which reads exactly like a target that does not exist.
    # `help` is modal and worked only because its quit happened to be armed on a
    # path that did run; `shortcuts` is modal too and produced no file for two
    # minutes before this was measured rather than guessed.
    #
    # A non-modal window is unaffected: the timeouts simply come due afterwards.
    if WHICH == "tool-activity":
        # After present(): a needle typed before the map lands in a tree that is
        # not laid out yet.
        GLib.timeout_add(900, lambda: _frame_panel(
            window, "tool-activity-group", "Tool activity"))
        GLib.timeout_add(1500, lambda: _report_framing(window))
        GLib.timeout_add(1800, lambda: _render(window, WIDTH, HEIGHT, OUT))
    else:
        # **The main window seeds every panel's health dot on idle after it
        # maps**, twenty panels' worth of `systemctl`/`busctl`/`gsettings`, and
        # that measures at about two seconds. Capturing at 1500ms photographed a
        # sidebar with no dots on it at all - a screenshot of the window before
        # it finished looking like itself, which is the failure this file exists
        # to prevent. 3500ms is past the end of it.
        settle = 3500 if WHICH in ("main", "empty") else 1500
        GLib.timeout_add(settle, lambda: _render(window, WIDTH, HEIGHT, OUT))
    # The help window is modal, so app.quit() alone leaves it up and run() hangs.
    GLib.timeout_add(2500 if WHICH == "help" else 5600,
                     lambda: (app.quit(), False)[1])
    # Presented last, and only for the non-modal case: a modal dialog's present()
    # does not return until the dialog closes.
    window.present()
    return None


# `--list <kind>` prints the targets of one kind and exits, so a sweep can read
# the inventory from here rather than keeping its own. **Importing this file does
# not work for that**: it runs `app.run()` at module scope, so an import is a
# render. A sweep that imported it would enumerate nothing and report "0 targets",
# which is a result that looks like an empty registry.
if len(sys.argv) > 1 and sys.argv[1] == "--list":
    _KIND = sys.argv[2] if len(sys.argv) > 2 else "states"
    if _KIND == "states":
        for _name in _all_states():
            print("state:" + _name)
    elif _KIND == "panels":
        from shani_chronoa.gui import surfaces
        for _name in surfaces.SURFACE_IDS:
            print("panel:" + _name)
    else:
        print(f"unknown kind {_KIND!r}; try states or panels", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


app = App()
_APP[0] = app
app.connect("activate", on_activate)
GLib.timeout_add(60000, lambda: (app.quit(), False)[1])
app.run([])
