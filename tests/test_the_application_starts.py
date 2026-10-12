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
        a.quit()
        return False
    GLib.idle_add(later)

app.connect("activate", on_activate)
GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
app.run([])
print("WINDOW", seen.get("window") is not None)
print("SCHEDULER", seen.get("scheduler") is not None)
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
        # No `_apply_answer_mode` on this object, so the guard must take the
        # missing branch - and it must not raise.
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
