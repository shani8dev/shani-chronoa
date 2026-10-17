"""The desktop companion's wiring - gating, lifecycle, and the event-to-face map.

The overlay needs a compositor that speaks layer-shell, so the window itself is
not built here unless a display exists; what is checked is the logic that decides
whether to build one, and that it never presents a window the compositor refused
to turn into a layer surface.

**The gates are the interesting part.** `available()` used to answer only "is the
library importable", which on this product's own desktop is True while the window
it then opens is an ordinary always-on-top rectangle pinned to the corner. So two
answers are pinned here, and they are different questions:

- `available()` asks the **compositor**, before any window exists;
- `_build()` asks about the **window it actually made**, and destroys it if the
  answer is no.

A check that could not answer used to answer yes.
"""
import logging
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import mascot, mascot_art  # noqa: E402


def _display():
    """Whether a real GTK window can be built here.

    `Gtk.init_check()` is **not** the question on its own - it answers True with
    no display, and every widget built afterwards is unallocated. But it is what
    loads the backend, and `Gdk.Display.get_default()` answers None until it has,
    so the two are asked in that order: the first opens the door, the second says
    whether anything came through it.
    """
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk
    Gtk.init_check()
    return Gdk.Display.get_default() is not None


def _code_only(func) -> str:
    """The function's source with comments and docstrings removed.

    A source assertion whose needle is also in the prose explaining it is a check
    that cannot fail - and this file hit that twice on its first run: the guard
    against re-implementing a setting read matched the sentence saying it does
    not. Parsing and dropping the docstring node is the same fix the dead-code
    scan in this repository uses; tokenising by hand got the indentation wrong
    and missed it anyway.
    """
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    function = tree.body[0]
    if (function.body and isinstance(function.body[0], ast.Expr)
            and isinstance(function.body[0].value, ast.Constant)):
        function.body = function.body[1:]        # the docstring
    return ast.unparse(function)


needs_display = pytest.mark.skipif(not _display(), reason="no display to build a window on")


class _App:
    """A stand-in for the application, for the tests that never build a window.

    **`_build()` cannot use this one.** `Gtk.Window(application=...)` wants a
    real `Gtk.Application` and a `SimpleNamespace` is a `TypeError` - the same
    trap as a fake shaped like the call rather than like the thing being called.
    `_real_app()` builds the real object for those tests.
    """

    def __init__(self, on=True):
        self.config = type("C", (), {"get_bool": lambda s, k, d=False: on})()

    def lookup_action(self, name):
        return None

    def activate_action(self, name, parameter=None):
        pass


def _real_app():
    """A real, **unregistered** `Gtk.Application`.

    No application id, so it claims no bus name - which matters because a name
    already owned elsewhere forwards activation to the stale owner and this file
    would then be measuring that, which is the fifth time in this repository that
    concurrency has been the answer.
    """
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    app = Gtk.Application()
    app.config = type("C", (), {"get_bool": lambda s, k, d=False: True})()
    return app


class _Shell:
    """A stand-in for the layer-shell library, recording what it was asked.

    `supported` and `layered` are **two different questions** and are separate
    attributes for that reason: a compositor can answer "yes, I speak
    layer-shell" to `is_supported()` and still fail to make this window a layer
    surface, which is what collapsing them into one flag would hide.
    """

    def __init__(self, supported=True, layered=True):
        self.supported = supported
        self.layered = layered
        self.namespaces = []
        self.presented = []

        class _Enum:
            pass

        self.Layer = type("Layer", (), {"TOP": 1})
        # The real `GtkLayerShell.Edge` has all four, and a fake that carries two
        # makes a top-anchored branch raise `AttributeError` rather than
        # fail an assertion - so the under-specification reads as a product
        # defect. `TOP` is what `mascot-corner=top-center` needs.
        self.Edge = type("Edge", (), {"LEFT": 0, "RIGHT": 1,
                                        "TOP": 2, "BOTTOM": 3})
        self.KeyboardMode = type("KeyboardMode", (), {"NONE": 0})

    # the questions
    def is_supported(self):
        return self.supported

    def is_layer_window(self, win):
        return self.layered

    def get_zwlr_layer_surface_v1(self, win):
        return 1 if self.layered else 0

    # the protocol
    def init_for_window(self, win):
        pass

    def set_namespace(self, win, name):
        self.namespaces.append(name)

    def set_keyboard_mode(self, win, mode):
        pass

    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda *a, **k: None
        raise AttributeError(name)


# --- the gates -------------------------------------------------------------

def test_it_is_off_by_default_and_gated():
    assert mascot.Mascot(_App(on=False)).start() is False
    ok, why = mascot.available()
    assert isinstance(ok, bool) and isinstance(why, str)
    if not ok:
        # A refusal that says nothing is a refusal the next person re-investigates.
        assert why, "available() declined without saying why"
        # And the consent switch is a *separate* gate from the session's.
        assert mascot.Mascot(_App(on=True)).start() is False


def test_a_compositor_without_layer_shell_is_refused_before_any_window(monkeypatch):
    """The defect this file exists for.

    Measured on this product's own desktop - GNOME on Wayland, with
    `gir1.2-gtk4layershell-1.0` installed - the library imports and the old check
    answered `(True, "")`; the window that followed was **not** a layer surface
    (`get_zwlr_layer_surface_v1()` returned 0 and GTK warned "GtkWindow is not a
    layer surface"). `is_supported()` is the compositor's own answer and it is the
    one that can be asked before a window exists.
    """
    shell = _Shell(supported=False)
    monkeypatch.setattr(mascot, "_layer_shell", lambda: (shell, ""))
    ok, why = mascot.available()
    assert ok is False
    assert "layer surface" in why, why
    # Nothing was built, so nothing could have been shown.
    assert mascot.Mascot(_App())._win is None


def test_a_reason_names_the_protocol_not_the_package():
    """"not installed" sends someone to install something they already have."""
    shell = _Shell(supported=False)
    import shani_chronoa.mascot as module
    original = module._layer_shell
    module._layer_shell = lambda: (shell, "")
    try:
        _, why = module.available()
    finally:
        module._layer_shell = original
    assert "compositor" in why or "layer surface" in why, why
    assert "no layer-shell library" not in why, why


def test_both_library_spellings_are_tried_gtk4_first():
    """`GtkLayerShell-0.1` is the GTK 3 build on many systems and cannot load.

    Measured on Ubuntu 26.04 by reading both packages' file lists: the two
    typelibs differ by *namespace*, not by version, so asking for only one of
    them either imports a library that cannot load in a GTK 4 process or misses a
    distribution that ships the GTK 4 build under the old name.
    """
    import gi
    from gi.repository import Gtk4LayerShell as gtk4
    gi.require_version("Gtk4LayerShell", "1.0")
    assert hasattr(gtk4, "is_supported"), "the compositor check is not on the GTK 4 namespace"
    shell, why = mascot._layer_shell()
    if shell is None:
        # The message has to name both, because a person who has only one of
        # them needs to know which.
        assert "Gtk4LayerShell" in why and "GtkLayerShell" in why, why
    else:
        assert hasattr(shell, "set_keyboard_mode"), shell


def test_a_typelib_that_is_installed_is_actually_handed_back():
    """The gate refused on **every** machine, and named a library that loads.

    `_layer_shell()` read the module with `getattr(gi.repository, namespace)`.
    `gi.repository` on this PyGObject does not import a submodule on attribute
    access, so on a **cold** `gi.repository` that raised `AttributeError` with
    the typelib **installed and `require_version` satisfied**. The mascot
    therefore reported "no layer-shell library" on a machine that has one, and a
    machine whose compositor genuinely supports layer-shell could never run it.

    **Why this runs in a subprocess, which is the only instrument that works.**
    Measured on this box:

    | | `getattr(gi.repository, "Gtk4LayerShell")` |
    |---|---|
    | cold process | **`AttributeError`** |
    | after any `from gi.repository import Gtk4LayerShell` | **OK** |

    `from gi.repository import X` binds `X` as an attribute of `gi.repository`,
    so **one earlier test warms it for the whole process** - and
    `test_both_library_spellings_are_tried_gtk4_first` does that at line 210.
    A witness written in-process is therefore green with the defect fully
    restored, which is how it was measured before this line was written: the
    mutation ran, and the file reported **51 passed**. A subprocess is the only
    place `gi.repository` is guaranteed cold, whatever order pytest runs things
    in.

    **The precondition is asserted rather than assumed.** The child prints what
    `importlib` found, so a box where no typelib is installed skips the strong
    claim rather than asserting it - and a child that printed nothing at all
    fails, because a run that could not measure is not a measurement (the same
    rule `tools/mutate.py`'s `MutationDidNotParse` states for mutation runs).

    Restoring `getattr` fails this on a box where the typelib is present: that
    is the shipped defect, not a hypothetical one.
    """
    import os
    import subprocess
    import sys

    child = r"""
import sys
import gi, gi.repository as R

# Coldness FIRST, and presence probed WITHOUT importing. Both details are
# measured rather than arranged:
#
#   cold process + require_version() alone  ->  getattr still ABSENT
#   cold process + importlib.import_module ->  getattr OK
#
# so `require_version` is the only presence probe that leaves the precondition
# this test depends on intact. The first version of this child discovered the
# typelibs with importlib - which is precisely what warmed `gi.repository` and
# made the shipped `getattr` survive with the file reporting 51 passed.
cold = getattr(R, "Gtk4LayerShell", None) is None and \
       getattr(R, "GtkLayerShell", None) is None

found = []
for ns, ver in (("Gtk4LayerShell", "1.0"), ("GtkLayerShell", "0.1")):
    try:
        gi.require_version(ns, ver)
        found.append(ns)
    except Exception:
        pass
if not found:
    print("NOT_INSTALLED")
    raise SystemExit(0)

sys.path.insert(0, %(pkg)r)
from shani_chronoa import mascot
shell, why = mascot._layer_shell()
print("FOUND=%%s COLD_GETATTR=%%s SHELL=%%s" %% (
    ",".join(found), cold, "yes" if shell is not None else "no"))
"""
    pkg = str(Path(mascot.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", child % {"pkg": pkg}],
                         capture_output=True, text=True, timeout=60, env=env,
                         cwd=str(Path(pkg).parents[2]))
    line = (out.stdout or "").strip()
    assert line, (
        "the child measured nothing - which is not the same as the gate working:\n"
        f"stdout={out.stdout!r} stderr={out.stderr[-400:]!r}")

    if line == "NOT_INSTALLED":
        pytest.skip("no layer-shell typelib on this machine")

    found, cold, got = line.split()
    assert cold == "COLD_GETATTR=True", (
        "the child was not cold, so this measured nothing: "
        "something imported gi.repository first")
    assert got == "SHELL=yes", (
        f"{found} imports in a cold process and the gate still refused it")
    shell, why = mascot._layer_shell()
    assert shell is not None and hasattr(shell, "init_for_window"), shell or why


def test_the_gate_does_not_blame_the_spellings_it_did_not_reach_first():
    """The refusal reported the *last* failure and hid the first.

    The loop tried the GTK 4 namespace first and the GTK 3 one second, and kept
    only the final error. Both failures have the same shape here, so the sentence
    named `GtkLayerShell 0.1` as unusable while a reader concludes that installing
    `gir1.2-gtk4layershell-1.0` would be the fix. Both are now reported.

    **The refusal branch is forced rather than waited for**, because on a machine
    that *has* the typelib the gate succeeds and this branch is never reached -
    measured: restoring the last-error-only reporting left the file **51 passed**.
    A branch that only runs on a machine missing a package is a branch whose
    witness cannot exist on a machine that has one, so the condition is arranged
    instead of hoped for. What is being tested is the refusal *text*; whether a
    library loads is the other test's subject, and asserting both directions here
    is what keeps this from becoming "the gate succeeded and said nothing".
    """
    import importlib

    def _nothing_is_installed(name, *args, **kwargs):
        raise ImportError(f"no module named {name!r}")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(importlib, "import_module", _nothing_is_installed)
        shell, why = mascot._layer_shell()

    assert shell is None, "a gate whose imports all fail must still refuse"
    assert why, "a refusal that says nothing sends someone to reinstall the app"
    assert "Gtk4LayerShell" in why and "GtkLayerShell" in why, why
    assert why.count("is unusable") == 2, (
        f"both spellings must be reported, and each once: {why}")


@needs_display
def test_a_window_the_compositor_refused_is_destroyed_and_never_presented(monkeypatch):
    """The gate `available()` cannot make, because it needs a real window.

    `init_for_window()` returning says the *call* worked. Whether the compositor
    agreed is a separate question, and presenting a window that failed it pins an
    always-on-top rectangle to the screen edge for the life of the app - at the
    notch's top centre today, and at the bottom-right corner when that is what
    `mascot-corner` says.
    """
    shell = _Shell(supported=True, layered=False)
    monkeypatch.setattr(mascot, "_layer_shell", lambda: (shell, ""))
    m = mascot.Mascot(_real_app())
    assert m._build() is False
    assert m._win is None, "an unlayered window was kept"
    # Nothing was set on it that a later render could reach.
    assert m._picture is None and m._bubble is None


@needs_display
def test_the_protocol_namespace_is_set(monkeypatch):
    """Every wlr-layer-shell surface carries a namespace, and the call was absent."""
    shell = _Shell(supported=True, layered=True)
    monkeypatch.setattr(mascot, "_layer_shell", lambda: (shell, ""))
    m = mascot.Mascot(_real_app())
    assert m._build() is True
    assert shell.namespaces == ["chronoa-mascot"], shell.namespaces
    m.stop()


@needs_display
def test_a_layered_window_is_presented(monkeypatch):
    """The positive control: the guard above has to be able to pass."""
    shell = _Shell(supported=True, layered=True)
    monkeypatch.setattr(mascot, "_layer_shell", lambda: (shell, ""))
    m = mascot.Mascot(_real_app())
    assert m._build() is True, "the gate refused a window that *was* a layer surface"
    assert m._win is not None
    m.stop()


# --- lifecycle -------------------------------------------------------------

def test_stop_clears_the_widgets_so_a_late_timer_cannot_touch_a_destroyed_tree():
    """Both halves of one defect.

    The blink scheduled an un-blink and threw the source id away, so `stop()`
    could not cancel it; and `_render`'s guard was `is None` on a reference
    `stop()` never cleared, so a timer outliving the window called into a
    destroyed tree - where an AttributeError inside a GLib callback aborts the
    process rather than failing a test.
    """
    m = mascot.Mascot(_App())
    m._picture = object()
    m._bubble = object()
    m._blink = True
    # A **real** source id, so `stop()` really cancels it rather than warning
    # about an id that was never registered.
    from gi.repository import GLib
    m._unblink_timer = GLib.timeout_add(60_000, lambda: False)
    m.stop()
    assert (m._picture, m._bubble, m._shell) == (None, None, None)
    assert m._blink is False
    # The guards now mean something: this raises if the refs were left bound.
    m._render()


def test_every_timer_stop_knows_about_is_one_it_can_cancel():
    """A timer `stop()` cannot name is a timer that can outlive the window.

    The needle is the **cancel tuple's own elements**, read out of the AST, and
    not source text. `stop()` carries a comment naming `_unblink_timer` to
    explain why it is in the list at all, so a text search for that name is
    satisfied by the explanation alone - the first version of this test passed
    with `_unblink_timer` deleted from the tuple it was checking.
    """
    cancelled = _cancelled_timers(mascot.Mascot.stop)
    for name in ("_blink_timer", "_bob_timer", "_expr_timer",
                 "_bubble_timer", "_unblink_timer"):
        assert name in cancelled, (
            f"{name} is not cancelled by stop(); stop() cancels "
            f"{sorted(cancelled)}")


def _cancelled_timers(func) -> set[str]:
    """The attribute names `stop()` iterates when removing GLib sources.

    Parsed rather than grepped, so only a name that is genuinely in the tuple
    counts. Everything else this test could ask - the substring, the count, a
    docstring - is satisfiable by a comment or by prose about the fix.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        # `for tid in ("_blink_timer", ...)` - a literal tuple, or a bare name
        # built one per line.
        if isinstance(node, (ast.Tuple, ast.List)):
            for element in node.elts:
                if isinstance(element, ast.Constant) and isinstance(
                        element.value, str):
                    names.add(element.value)
    return names


def test_the_bob_stops_when_the_desktop_asks_for_less_motion(monkeypatch):
    """A bob is continuous motion; a blink is one discrete event.

    The bob is the mascot's only animation and it is on an 11 Hz timeout for the
    life of the app, so the desktop's reduce-motion setting has to reach it. It is
    read per start rather than cached, so flipping it needs no restart.
    """
    from gi.repository import GLib
    armed = []
    monkeypatch.setattr(GLib, "timeout_add", lambda ms, fn: armed.append(ms) or 1)
    monkeypatch.setattr(mascot, "_motion_is_reduced", lambda: True)
    mascot.Mascot(_App())._schedule()
    assert mascot._BLINK_EVERY_MS in armed, "a blink is not motion"
    assert mascot._BOB_EVERY_MS not in armed, "the bob ran under reduce-motion"


def test_the_bob_runs_when_motion_is_allowed(monkeypatch):
    """The other direction - a floor that only ever fails one way is not a guard."""
    from gi.repository import GLib
    armed = []
    monkeypatch.setattr(GLib, "timeout_add", lambda ms, fn: armed.append(ms) or 1)
    monkeypatch.setattr(mascot, "_motion_is_reduced", lambda: False)
    mascot.Mascot(_App())._schedule()
    assert mascot._BOB_EVERY_MS in armed


def test_the_reduce_motion_reader_is_reused_not_re_written():
    """A third copy of "ask enable-animations" is a third answer to one question.

    `automation_overlay.reduced_motion()` already answers it for the cue layer.

    The needle is checked against **code only** (`_code_only`), because the first
    version of this assertion matched the sentence in the docstring explaining
    that the read is *not* re-implemented - a check satisfied by its own
    explanation.
    """
    code = _code_only(mascot._motion_is_reduced)
    assert "automation_overlay" in code, code
    assert "enable-animations" not in code, "it re-implemented the read instead"
    assert "Gio.Settings" not in code, "it re-implemented the read instead"


def test_the_reduce_motion_reader_answers_what_the_desktop_said(monkeypatch):
    """The helper's **return value**, which no other test in this file reads.

    The test above proves the helper *reuses* `automation_overlay.reduced_motion`
    rather than re-reading the setting, which is a claim about its source. This is
    the claim about its answer - and every existing reduce-motion test
    (`test_the_bob_stops_...`, `test_reduce_motion_stops_the_eyes_too`) replaces
    the helper with `lambda: True` to reach the call sites, which is precisely
    what makes them unable to see anything wrong with the helper itself. A body
    that answered `False` unconditionally passed all of them, with the eyes and the
    bob both moving under a desktop that had asked for less motion.

    **Both directions.** A one-way floor is not a guard: `return True` would pass
    a test that only checked "the desktop asked and the bob stopped".

    The patch is on `automation_overlay.reduced_motion` - **what the helper
    reads**, not the helper - because patching the helper is the thing that made
    the other tests blind to it. `_motion_is_reduced` imports it inside the
    function body, so a module attribute is what it resolves.
    """
    from shani_chronoa import automation_overlay
    for desktop_said, want in ((True, True), (False, False)):
        monkeypatch.setattr(automation_overlay, "reduced_motion",
                            lambda said=desktop_said: said, raising=False)
        got = mascot._motion_is_reduced()
        assert got is want, (
            f"the desktop's reduced-motion answer was {desktop_said} and the "
            f"reader answered {got!r}")


# --- the face map ----------------------------------------------------------

def test_every_event_maps_to_a_real_expression():
    for kind, expr in mascot._EVENT_FACE.items():
        assert expr in mascot_art._EXPRESSIONS, \
            f"{kind} -> {expr} is not a drawable expression"


def test_an_unknown_event_is_logged_rather_than_silently_surprised():
    """A misspelled kind used to show a surprise face for three seconds and say
    nothing, which reads as a reaction the mascot chose rather than one nobody
    asked for."""
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("shani_chronoa.mascot")
    handler = _Handler()
    logger.addHandler(handler)
    try:
        mascot.Mascot(_App()).notice("no-such-event")
    finally:
        logger.removeHandler(handler)
    assert [r for r in records if r.levelno >= logging.WARNING], \
        "an unknown kind must be visible, not silently drawn"


def test_a_known_event_does_not_warn():
    """The other direction: a guard that fires on everything is noise."""
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("shani_chronoa.mascot")
    handler = _Handler()
    logger.addHandler(handler)
    try:
        for kind in mascot._EVENT_FACE:
            mascot.Mascot(_App()).notice(kind)
    finally:
        logger.removeHandler(handler)
    assert not [r for r in records if r.levelno >= logging.WARNING]


def test_state_is_sanitised():
    m = mascot.Mascot(_App())
    # set_state with no window must not raise and must reject nonsense.
    m.set_state("thinking"); assert m._state == "thinking"
    m.set_state("garbage"); assert m._state == "idle"


def test_the_level_only_moves_the_halo_while_live():
    """A level arriving after the turn ended must not make the halo pulse at
    nothing - the same rule the orb's own two feeds carry."""
    m = mascot.Mascot(_App())
    m.set_state("idle"); m.set_level(0.9)
    assert m._level == 0.0
    m.set_state("listening"); m.set_level(0.9)
    assert m._level == 0.9


# --- the accessible name ------------------------------------------------------------

@needs_display
def test_the_mascot_is_named_on_the_widget_and_not_in_the_svg(monkeypatch):
    """It is drawn as a **raster**, so a label in the markup reaches nobody.

    `_render` feeds the SVG through a GdkPixbuf loader into a paintable. A
    `role`/`aria-label` in the markup is discarded with the pixels - it is a
    claim about an accessibility tree this picture never enters. AT-SPI reads
    the `Gtk.Picture`, so the name has to be on the widget, and asserting that
    the *SVG* carries no label is what stops the reachable one being replaced
    by the decorative one.

    `get_accessible_property` does not exist in this PyGObject (the repo's own
    recorded probe defect), so `update_property` is recorded at the class level
    - the only place the call can be observed.
    """
    from gi.repository import Gtk
    labels = _record_labels(monkeypatch, Gtk)
    shell = _Shell(supported=True, layered=True)
    monkeypatch.setattr(mascot, "_layer_shell", lambda: (shell, ""))
    m = mascot.Mascot(_real_app())
    assert m._build() is True
    # Exactly **one** label, set by the one method that is supposed to set it.
    # Asserting a count as well as the words is what gives the second half its
    # teeth: `_build` used to carry its own copy of the call, so a broken
    # `_announce` still left a label behind and "the widget is named" passed.
    assert len(labels) == 1, f"the name was set {len(labels)} times: {labels}"
    assert "Chronoa" in labels[0], labels[0]
    m.stop()


@needs_display
def test_the_accessible_name_follows_the_state(monkeypatch):
    """The other direction: a name that says "waiting" on a speaking window is
    not an absent name, it is a confidently wrong one.

    Without this the label is whatever the widget was constructed with - idle -
    for the whole life of a window that then went on to speak, listen and think.
    """
    from gi.repository import Gtk
    labels = _record_labels(monkeypatch, Gtk)
    shell = _Shell(supported=True, layered=True)
    monkeypatch.setattr(mascot, "_layer_shell", lambda: (shell, ""))
    m = mascot.Mascot(_real_app())
    assert m._build() is True

    said = {}
    for state in ("speaking", "listening", "thinking", "interrupting"):
        m.set_state(state)
        said[state] = labels[-1] if labels else ""
    assert len(set(said.values())) == len(said), (
        f"states do not get distinct names: {said}")
    for state, text in said.items():
        assert mascot._STATE_WORDS[state] in text, (
            f"{state} is announced as {text!r}, expected "
            f"{mascot._STATE_WORDS[state]!r}")
    m.stop()


@needs_display
def test_an_unknown_state_is_announced_as_waiting_and_not_as_itself(monkeypatch):
    """The refusal is a real one: it falls back to idle, so it must say idle.

    Otherwise the name and the picture disagree - a state this does not know
    draws as `idle` and would be announced as whatever string it was handed.
    """
    from gi.repository import Gtk
    labels = _record_labels(monkeypatch, Gtk)
    m = mascot.Mascot(_real_app())
    m._picture = Gtk.Picture()
    # From a state that is *not* idle: `set_state` only announces on a change,
    # and idle -> idle is not one, so starting at idle would assert nothing.
    m._state = "speaking"
    m.set_state("no-such-state")
    assert labels, "the refusal announced nothing at all"
    # **The words are written out, not taken from `_describe`.** The first
    # version asserted `labels[-1] == mascot._describe("idle")`, which compares
    # the product against itself: breaking `_describe` moved both sides and the
    # mutation ran green. A witness that reads its expectation out of the code
    # under test cannot fail.
    assert "waiting" in labels[-1], labels[-1]
    assert "no-such-state" not in labels[-1], labels[-1]
    assert m._state == "idle", m._state


def test_set_state_validates_against_the_art_modules_own_list():
    """One answer to "is this a state".

    `set_state` carried its own tuple while `mascot_art` carried `_PALETTE`, so
    a state added to the art would render happily and be **refused** by the
    caller - falling back to idle with nothing said, which is the invisible kind.
    Asserted by planting a state in the art's palette and asking `set_state` to
    accept it, so the two lists cannot drift apart without this going red.
    """
    added = "a-state-only-the-art-knows"
    palette = mascot_art._PALETTE
    palette[added] = palette["idle"]
    original = mascot_art._STATES
    try:
        mascot_art._STATES = mascot_art._STATES + (added,)
        m = mascot.Mascot(_App())
        m._picture = None          # no display needed; `_announce` guards on it
        m.set_state(added)
        assert m._state == added, (
            "set_state refused a state mascot_art draws - two lists, two answers")
    finally:
        mascot_art._STATES = original
        palette.pop(added, None)


def _record_labels(monkeypatch, Gtk) -> list[str]:
    """Every accessible label set on any `Gtk.Picture`, in order.

    Recorded by wrapping `update_property` on the class, because that is the
    only place a PyGObject with no `get_accessible_property` can be observed.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    labels: list[str] = []
    original = Gtk.Picture.update_property

    def spy(self, properties, values):
        try:
            from gi.repository import Gtk as _G
            if _G.AccessibleProperty.LABEL in properties:
                labels.append(list(values)[0])
        except Exception:  # noqa: BLE001
            pass
        return original(self, properties, values)

    monkeypatch.setattr(Gtk.Picture, "update_property", spy, raising=False)
    return labels

# --- gaze: where the eyes are pointed, and when that costs a redraw -----------

class _Win:
    """A window with a size and no window manager's opinions about it.

    `_on_motion` asks its window for the allocation to turn a pointer position
    into a bearing, so a test that does not provide one measures nothing. This is
    the minimum that answers that question, and nothing more: `_render` is
    stubbed wherever it would be reached.
    """

    def __init__(self, width=100, height=100):
        self._w, self._h = width, height

    def get_width(self):
        return self._w

    def get_height(self):
        return self._h


class TestWhereTheEyesArePointed:
    """`_gaze_for`, which is pure - so the geometry needs no display at all.

    The 1x1 case is the one that was wrong and is measured rather than argued:
    GTK reports a **1x1** allocation for a window that has not been laid out, and
    the first `motion` event can arrive before layout finishes. Dividing by a
    half-pixel put the bearing at its maximum, so an unlaid-out window aimed the
    eyes at whichever corner the pointer happened to be in.
    """

    @pytest.mark.parametrize("width,height", [
        (0, 0), (1, 1), (1, 100), (100, 1), (2, 2),
    ])
    def test_an_unlaid_out_window_looks_forward(self, width, height):
        """There is no centre to be away from, so 0.0 is the honest answer.

        Includes `(2, 2)`: half-width is 1.0, which divides, and the guard is
        `< 1` rather than `<= 1`, so this pins which side of the line the
        boundary sits on.
        """
        for x, y in ((5, 5), (0, 0), (width * 3, height * 3)):
            got = mascot._gaze_for(x, y, width, height)
            if width < 2 or height < 2:
                assert got == (0.0, 0.0), f"{width}x{height} at ({x},{y}) -> {got}"
            else:
                assert all(-1.0 <= v <= 1.0 for v in got), got

    @pytest.mark.parametrize("x,y,width,height,want", [
        (50, 50, 100, 100, (0.0, 0.0)),      # dead centre
        (100, 50, 100, 100, (1.0, 0.0)),    # right edge
        (0, 50, 100, 100, (-1.0, 0.0)),     # left edge
        (50, 0, 100, 100, (0.0, -1.0)),     # top edge
        (50, 100, 100, 100, (0.0, 1.0)),   # bottom edge
        (0, 0, 100, 100, (-1.0, -1.0)),     # the top-left corner
        (10, 20, 100, 100, (-0.8, -0.6)),   # a real 100x100 window
    ])
    def test_the_bearing_is_the_pointer_relative_to_the_centre(self,
                                                             x, y, width, height,
                                                             want):
        assert mascot._gaze_for(x, y, width, height) == pytest.approx(want)

    @pytest.mark.parametrize("x,y", [
        (-500, -500), (5000, 5000), (10 ** 9, -(10 ** 9)),
    ])
    def test_a_pointer_outside_the_window_clamps_rather_than_winding_up(
            self, x, y):
        """Beyond the edge is "as far as it goes", not "proportionally further".

        Left unclamped this returns thousands, and the eye is translated by
        thousands of units - off the picture entirely rather than off the eye.
        """
        got = mascot._gaze_for(x, y, 100, 100)
        assert got == (-1.0, -1.0) if (x < 0 and y < 0) else got in (
            (-1.0, -1.0), (1.0, 1.0), (1.0, -1.0), (-1.0, 1.0))
        assert all(-1.0 <= v <= 1.0 for v in got), got


class TestTheDeadzone:
    """`_gaze_moved`. `motion` fires far faster than a mascot needs to be seen
    moving, and **every** call it triggers rasterises the whole SVG - so this is
    the difference between a face that tracks and a process that re-renders the
    pointer's own micro-movements."""

    def test_a_flick_smaller_than_the_deadzone_is_not_worth_a_redraw(self):
        below = mascot._GAZE_DEADZONE / 2.0
        assert not mascot._gaze_moved((0.0, 0.0), (below, below))
        assert not mascot._gaze_moved((0.0, 0.0), (-below, below))

    def test_a_change_at_the_deadzone_is(self):
        """The boundary is `>=`, so exactly the deadzone counts.

        Asserted at the constant rather than at a hand-typed number: a floor
        pinned by a literal is a floor that can be halved without the test
        noticing, which is the trap the timbre-suffix test in this repository
        records.
        """
        at = mascot._GAZE_DEADZONE
        assert mascot._gaze_moved((0.0, 0.0), (at, 0.0))
        assert mascot._gaze_moved((0.0, 0.0), (0.0, at))
        assert not mascot._gaze_moved((0.0, 0.0), (at * 0.999, 0.0))

    def test_either_axis_alone_is_enough(self):
        """Not "the vector changed" - a pure sideways flick must still count,
        because the eye moves sideways when only x moves.

        **The bases are 0.0 and 0.2, not 0.1.** `old + deadzone` does not
        round-trip in binary floating point, and `_gaze_moved` compares with
        `>=`, so the base decides whether the boundary case is reached at all:
        `0.1 + 0.08` is `0.18000000000000002`, whose distance back from `0.1` is
        `0.07999999999999999` - just *under* the deadzone - so `>=` correctly
        answers False and the test fails for a reason that has nothing to do
        with the deadzone. Measured, not assumed. The precondition is `>=` and
        not `==`, because all the handler needs is that the delta be at least the
        deadzone; `0.2` overshoots it by one ulp and that is fine.
        """
        at = mascot._GAZE_DEADZONE
        for base in (0.0, 0.2):
            assert abs((base + at) - base) >= at, (
                f"{base} + {at} lands under the deadzone, so this test would "
                "measure floating point rather than the deadzone")
        for bx, by in ((0.0, 0.2), (0.2, 0.0)):
            assert mascot._gaze_moved((bx, by), (bx + at, by))
            assert mascot._gaze_moved((bx, by), (bx, by + at))

    def test_no_change_at_all_is_never_a_redraw(self):
        assert not mascot._gaze_moved((0.3, -0.7), (0.3, -0.7))


class TestTheMotionHandler:
    """`_on_motion` and `_recentre`, driven through a real `Mascot`.

    The window is a stand-in rather than a GTK one because this is a headless
    box and `available()` refuses here anyway - but `_on_motion` and
    `_recentre` are **methods of the class**, so they are called unbound against a
    real instance rather than on a stand-in that only carries the two names.
    That is the trap two mixins and two objects is: building a separate object
    and calling the handler there tests the fake.
    """

    def _mascot(self, monkeypatch, *, width=100, height=100, gaze=(0.0, 0.0)):
        drawn = []
        m = mascot.Mascot(_App())
        monkeypatch.setattr(m, "_win", _Win(width, height), raising=False)
        monkeypatch.setattr(m, "_gaze", gaze, raising=False)
        monkeypatch.setattr(m, "_render", lambda: drawn.append(m._gaze))
        monkeypatch.setattr(mascot, "_motion_is_reduced", lambda: False)
        return m, drawn

    def test_the_pointer_moves_the_eyes_and_redraws(self, monkeypatch):
        m, drawn = self._mascot(monkeypatch)
        mascot.Mascot._on_motion(m, None, 100.0, 50.0)
        assert m._gaze == pytest.approx((1.0, 0.0))
        assert drawn == [m._gaze], drawn

    def test_a_flick_below_the_deadzone_draws_nothing(self, monkeypatch):
        """The other direction of the deadzone, reached through the handler.

        `_gaze_moved` is tested on its own above; what this adds is that the
        handler actually consults it, which a deleted check in the handler would
        not be caught by.
        """
        m, drawn = self._mascot(monkeypatch, gaze=(0.0, 0.0))
        nudge = mascot._GAZE_DEADZONE / 4.0
        mascot.Mascot._on_motion(m, None, 50.0 + nudge, 50.0)
        assert drawn == [], f"a sub-deadzone flick caused {drawn}"

    def test_reduce_motion_stops_the_eyes_too(self, monkeypatch):
        """The setting governs gaze for the same reason it governs the bob: both
        are continuous motion the desktop's setting exists to stop, and honouring
        it for one and not the other is honouring it for whichever nobody noticed.
        """
        m, drawn = self._mascot(monkeypatch)
        monkeypatch.setattr(mascot, "_motion_is_reduced", lambda: True)
        mascot.Mascot._on_motion(m, None, 100.0, 100.0)
        assert m._gaze == (0.0, 0.0), "the eyes moved under reduce-motion"
        assert drawn == [], drawn

    def test_no_window_is_not_a_crash(self, monkeypatch):
        """`_win` is None before the window exists and after it is destroyed."""
        m, drawn = self._mascot(monkeypatch)
        m._win = None
        mascot.Mascot._on_motion(m, None, 10.0, 10.0)
        mascot.Mascot._recentre(m)

    def test_leaving_recentres_and_redraws_once(self, monkeypatch):
        m, drawn = self._mascot(monkeypatch, gaze=(0.8, -0.4))
        mascot.Mascot._recentre(m)
        assert m._gaze == (0.0, 0.0)
        assert drawn == [(0.0, 0.0)], drawn

    def test_leaving_when_already_forward_draws_nothing(self, monkeypatch):
        """The other direction of the recentre guard.

        A floor that only ever fails one way is not a guard - and `leave` fires on
        every pointer exit, so redrawing on each one for no change is the cost
        the guard exists to avoid.
        """
        m, drawn = self._mascot(monkeypatch, gaze=(0.0, 0.0))
        mascot.Mascot._recentre(m)
        assert drawn == [], f"recentring an already-centred gaze caused {drawn}"


# --- the wiring: that the handlers above are reached at all --------------------

def _wired_motion(monkeypatch):
    """A **real** `_build()`, with the motion controller's own `connect` calls
    recorded - and the handlers it recorded, ready to be invoked.

    **Why this is separate from `TestTheMotionHandler`:** every test in that class
    calls `_on_motion` / `_recentre` *unbound* on a `Mascot` whose `_build()` never
    ran, so no `GestureMotion` controller is ever constructed or connected. That is
    the correct unit test - but it is structurally blind to the wiring, which is
    a different defect with the same symptom: `motion.connect("motion", lambda
    *_: None)` leaves a handler that is defined, correct, and never called, and
    every test above passes with it applied. This is the "built and wired to
    nothing" class this repository records most often, and the only instrument
    that can see it is one that presses the thing.

    Two details the product forces rather than the test choosing:

    - **`_layer_shell` is stubbed**, exactly as the window-gate tests above do.
      On a machine with no layer-shell typelib (this one) `_build()` returns
      `False` at its first line and never reaches the wiring at all. The gate is
      not the thing under test; the eleven lines after it are, and they are the
      product's own.
    - **`_win` is pinned to `_Win(100, 100)`.** The window `_build()` really makes
      is unallocated, and `get_width()` on an unallocated window answers **1** -
      which is the 1x1 case, so the bearing would be `(0.0, 0.0)` for every
      pointer position and the wiring could be perfectly correct while this test
      measured nothing. The stand-in gives the bearing a defined meaning.
    """
    from gi.repository import Gtk

    recorded = {}
    original = Gtk.EventControllerMotion.connect

    def spy(self, signal, handler, *a, **k):
        recorded[signal] = handler
        return original(self, signal, handler, *a, **k)

    monkeypatch.setattr(Gtk.EventControllerMotion, "connect", spy)
    monkeypatch.setattr(mascot, "_layer_shell",
                        lambda: (_Shell(supported=True, layered=True), ""))
    # Pinned, not read: the desktop's own `enable-animations` is a property of
    # *this* machine, and a witness that passes or fails on it is describing the
    # box rather than the wiring. `test_the_reduce_motion_reader_answers_what_the
    # _desktop_said` is where the helper's answer is the subject.
    monkeypatch.setattr(mascot, "_motion_is_reduced", lambda: False)

    m = mascot.Mascot(_real_app())
    assert m._build() is True, "_build() refused, so there is no wiring to test"
    m._win = _Win(100, 100)
    return m, recorded


@needs_display
def test_the_pointer_reaches_the_eyes_through_the_controller_that_is_wired_to_it(
        monkeypatch):
    """`motion.connect("motion", ...)` actually lands on `_on_motion`.

    Invoking the **recorded** handler rather than `m._on_motion` is the whole
    test: the record only exists because `_build()` ran, and it only names
    `_on_motion` because `_build()` connected that one. A `lambda *_: None` in
    the connect line is a controller with both a signal and no behaviour, and
    every other gaze test in this file is green with it.
    """
    m, recorded = _wired_motion(monkeypatch)
    assert {"motion", "leave"} <= set(recorded), sorted(recorded)

    drawn = []
    m._gaze = (0.0, 0.0)
    m._render = lambda: drawn.append(m._gaze)

    recorded["motion"](m._motion, 100.0, 50.0)
    assert m._gaze == pytest.approx((1.0, 0.0)), (
        "the recorded motion handler did not move the eyes")
    assert drawn == [m._gaze], drawn


@needs_display
def test_leaving_reaches_the_recentre_through_the_controller_that_is_wired_to_it(
        monkeypatch):
    """`motion.connect("leave", ...)` actually lands on `_recentre`.

    The boundary crossing is the only thing that lets the eyes come back. Wired
    to a no-op, the gaze stays locked on wherever the pointer went - the mascot
    stares at the edge of the screen for the rest of the session - while the
    handler, the controller, the connect call and every other test in this file
    are all exactly where they should be.
    """
    m, recorded = _wired_motion(monkeypatch)
    assert {"motion", "leave"} <= set(recorded), sorted(recorded)

    drawn = []
    m._gaze = (0.8, -0.4)
    m._render = lambda: drawn.append(m._gaze)

    recorded["leave"](m._motion)
    assert m._gaze == (0.0, 0.0), "the recorded leave handler did not recentre"
    assert drawn == [(0.0, 0.0)], drawn
