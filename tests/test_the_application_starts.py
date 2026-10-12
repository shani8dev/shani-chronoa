"""Chronoa opens, and says so when part of it cannot.

**This file exists because the application did not start at all** and 1,600
tests had not noticed. `do_startup` called `self._apply_answer_mode()`, a method
defined nowhere in `app/` - it lived only in an uncommitted file - so startup
raised `AttributeError`, `do_activate` never ran, `sense_scheduler` stayed
`None`, and no window ever appeared.

**Everything except startup passed while it was broken.** The unit tests build
widgets directly; the harness tests that do start an application had their own
`AttributeError` swallowed into a "produced no result" message that four tests
reported as a pre-existing failure.

So the gate is the one nobody had: *start the thing*.
"""

import subprocess
import sys
import os
import pathlib
import tempfile

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PKG = _REPO / "usr/lib/shani-chronoa"

_PROBE = """
import sys, gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
from shani_chronoa.app import ChronoaApplication

app = ChronoaApplication()
app.hold()
seen = {}

from gi.repository import GLib

def on_activate(a):
    # **Deferred to an idle, not read inline.** `do_activate` is the default
    # handler and the window is built partway through it; reading `a.window`
    # straight out of the `activate` signal handler saw it before that, and
    # reported "the window was never built" on an application that builds it.
    # The question presenter harness already works this way.
    def later():
        seen["window"] = a.window
        seen["scheduler"] = a.sense_scheduler
        w = a.window
        # **The minimap, checked here because this is the only place the real
        # window's `__init__` runs.** Every other test of the minimap binds its
        # methods to a stand-in, so the four lines that build the map and hook
        # it to the scroll and to `turn-added` are covered by nothing at all
        # unless something builds a real window and looks.
        seen["minimap"] = getattr(w, "_minimap", None)
        seen["scroller"] = getattr(w, "_minimap_adjustment", None)
        # **Add a turn and count the ticks.** The `turn-added` connection is the
        # one line of the minimap's wiring that nothing else covered: the
        # wiring tests bind the handler themselves, so deleting the connection
        # left all of them green. Proved by mutation - `self._transcript.connect(
        # "turn-added", ...)` deleted, every test still passed.
        seen["ticks"] = -1
        try:
            w._transcript.add_user_turn("a probe turn")
            # **`get_first_child`/`get_next_sibling`, not `get_children`.**
            # The latter is GTK3 and does not exist on a GTK4 Box - so the
            # first version of this probe caught its own exception, printed
            # -1, and the assertion below reported a wiring failure rather
            # than the missing method.
            # **`isinstance`, not a type name.** In this PyGObject a GI
            # widget's `type(...).__name__` is `"Button"`, not `"GtkButton"` -
            # only plain Python classes report their own name. Counting
            # `"GtkButton"` found nothing in a map full of ticks and reported
            # the wiring as broken.
            ticks, child = 0, w._minimap.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.Button):
                    ticks += 1
                child = child.get_next_sibling()
            seen["ticks"] = ticks
        except Exception as exc:
            seen["ticks"] = -1
            seen["tick_error"] = repr(exc)
        seen["in_tree"] = False
        if seen["minimap"] is not None:
            # **Walk down through children**, not up through parents. The first
            # version started at the window and followed `get_parent()`, which
            # is None on the window itself - so it examined exactly one node,
            # never found the minimap, and reported that a correctly attached
            # widget was dropped.
            stack = [w]
            while stack:
                node = stack.pop()
                if type(node).__name__ == "TurnMinimap":
                    seen["in_tree"] = True
                    break
                child = node.get_first_child() if hasattr(
                    node, "get_first_child") else None
                while child is not None:
                    stack.append(child)
                    child = child.get_next_sibling()
        a.quit()
        return False
    GLib.idle_add(later)

app.connect("activate", on_activate)
GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
app.run([])
print("WINDOW", seen.get("window") is not None)
print("SCHEDULER", seen.get("scheduler") is not None)
print("MINIMAP", seen.get("minimap") is not None)
print("MINIMAP_TREE", seen.get("in_tree"))
print("MINIMAP_SCROLL", seen.get("scroller") is not None)
print("MINIMAP_TICKS", seen.get("ticks"))
print("TICK_ERROR", seen.get("tick_error", ""))
"""


def test_the_application_starts_and_builds_its_window():
    """Startup must complete. This is the assertion that was missing."""
    runtime = pathlib.Path(tempfile.mkdtemp(prefix="chronoa-starts-"))
    os.chmod(runtime, 0o700)
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["PYTHONPATH"] = str(_PKG)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-c", _PROBE],
                          capture_output=True, text=True, timeout=180,
                          env=env, cwd=str(runtime))
    out = proc.stdout
    assert "WINDOW True" in out, (
        "the application never built a window.\n"
        f"stdout:\n{out[-2000:]}\nstderr:\n{proc.stderr[-3000:]}")
    assert "MINIMAP True" in out, (
        "the window built no minimap, so the turn ruler is not in the "
        f"application at all.\nstdout:\n{out[-2000:]}")
    assert "MINIMAP_TREE True" in out, (
        "the minimap exists but was never added to the widget tree - it was "
        f"constructed and dropped.\nstdout:\n{out[-2000:]}")
    assert "MINIMAP_TICKS 1" in out, (
        "a turn was added to the real window and the minimap did not grow a "
        "tick, so `turn-added` is not connected and the map will never track "
        f"the conversation.\nstdout:\n{out[-2000:]}")
    assert "MINIMAP_SCROLL True" in out, (
        "the minimap has no scroll adjustment, so it will never show where "
        "you are in the conversation.\nstdout:\n{out[-2000:]}")
    assert "SCHEDULER True" in out, (
        "the ambient scheduler is None, so no sense will ever poll - startup "
        "failed after the window was created, or something cleared it.\n"
        f"stderr:\n{proc.stderr[-2000:]}")


def test_a_missing_applier_warns_rather_than_failing_silently():
    """The guard must say something.

    A `hasattr` check that quietly does nothing is the same defect one layer
    down: the mode strip would show "In the cloud" on a machine that answered
    locally, and nothing on screen or in the log would say otherwise.
    """
    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication.__new__(ChronoaApplication)
    app._answer_mode_warned = False

    class _Config:
        def get(self, key, default=""):
            return "cloud"

    app.config = _Config()
    import logging

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("shani_chronoa.app.application")
    handler = _Capture()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        # **Shadow the method to reach the guard's missing branch.**
        # This test used to rely on `_apply_answer_mode` being defined nowhere,
        # which stopped being true when the method landed - so the guard found
        # it, called it, and warned about nothing, and the test failed for
        # having been right. An instance attribute of `None` makes `getattr`
        # return `None`, which is exactly the "missing" case the guard checks,
        # and is the only way to exercise it now that the method exists.
        app._apply_answer_mode = None
        ChronoaApplication._apply_answer_mode_if_present(app)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert records, "the guard did nothing and said nothing"
    assert any("answer-mode" in r for r in records), records
    assert any("cloud" in r for r in records), (
        f"the warning does not name the value that will be ignored: {records}")


def test_the_guard_does_nothing_quietly_when_the_applier_exists():
    """The control: with an applier present it runs it and stays silent."""
    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication.__new__(ChronoaApplication)
    app._answer_mode_warned = False
    calls = []
    app._apply_answer_mode = lambda: calls.append("applied")

    import logging

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("shani_chronoa.app.application")
    handler = _Capture()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        ChronoaApplication._apply_answer_mode_if_present(app)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert calls == ["applied"], f"the applier was not called: {calls}"
    assert not records, f"it warned although the applier exists: {records}"
