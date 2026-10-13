"""The main window did not build. One line, 25 test files, the whole product.

`85c11ab` added the empty-state starter row and centred it with

    row.set_halign(Gtk.Alignment.CENTER)

`Gtk.Alignment` is GTK3. Measured on this box:

    hasattr(Gtk, "Alignment") -> False
    ChronoaWindow(app)       -> AttributeError:
                                'gi.repository.Gtk' object has no attribute
                                'Alignment'

The call is the third statement of `_build_starter_row` and sits **above** that
function's `try:`. `_build_starter_row` is called from `_setup_ui`
(`window.py:1906`), `_setup_ui` is called unguarded from `__init__`
(`window.py:1467`), and `__init__` calls `super().__init__` first - so the
exception escapes before a single widget exists and the window is never a
window.

    package installed at /usr/lib/shani-chronoa?  No - the repo tree is the
    product, so this is not a stale build on a machine.  It is HEAD.

**The scale, measured against `git archive HEAD` on a separate tree** rather
than against a remembered figure:

    tests/test_enter_reaches_the_orb.py + tests/test_mode_strip.py
      -> 1 failed, 15 passed, 10 errors
      -> 11 of the failures carry the identical
         "has no attribute 'Alignment'"

25 test files construct `ChronoaWindow`. The commit that added the feature and
the commit that could not construct the window are the same commit.

**Why the guard below is a scan and not a build.** A test that builds the
window catches *this* line. It does not catch the next one written into a
helper the window does not reach on this display, this theme or this machine -
and a start-up crash has no partial credit for being in a cold path. The scan
is the check that generalises; the build is the check that says what a person
would feel. Both, because they fail for different reasons and neither one
alone is the rule.

`Gtk.Alignment` is only one member of the GTK3 alignment family. The scan
covers the whole set, so the next `Gtk.Alignment`, `Gtk.Alignment.CENTER` or
`set_alignment(` is caught by the same assertion rather than by a user.
"""
import re
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import ChronoaWindow  # noqa: E402

GUI = Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa/shani_chronoa/gui"

#: GTK3 spellings that were removed in GTK4. Each one raises `AttributeError`
#: at the moment it is *evaluated*, not when the widget is drawn, so on this
#: product it is a start-up crash rather than a cosmetic defect.
GONE = (
    r"\bGtk\.Alignment\b",
    r"\bset_alignment\s*\(",
    r"\bGtk\.Misc\b",
    r"\bGtk\.Table\b",
    r"\bgtk_container_border_width\b",
)


def test_gtk4_has_no_alignment_enum():
    """The premise, asserted, so the scan cannot pass for the wrong reason."""
    assert not hasattr(Gtk, "Alignment"), (
        "this test is guarding a removed GTK3 enum; if Gtk.Alignment exists "
        "again then either PyGObject changed or the scan below is wrong, and "
        "neither should be discovered by the next person to hit it"
    )


@pytest.mark.parametrize("pattern", GONE)
def test_no_removed_gtk3_alignment_api_in_the_gui(pattern):
    rx = re.compile(pattern)
    hits = []
    for path in sorted(GUI.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            # A comment naming the removed API is the documentation of the
            # removal; a call to it is the crash. `window.py` has one on
            # purpose, at the line that explains why the enum is gone.
            if stripped.startswith("#"):
                continue
            if rx.search(line):
                hits.append(f"{path.relative_to(GUI.parents[2])}:{number}: {stripped}")
    assert not hits, (
        "GTK3 alignment API reachable at run time - each of these raises "
        "AttributeError while the argument is evaluated, so this is a start-up "
        "crash and not a rendering fault:\n  " + "\n  ".join(hits)
    )


class _App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.shani.chronoa.windowbuilds")


def test_the_main_window_actually_builds():
    """The thing a person sees, built the way the product builds it.

    A start-up crash hides behind every suite that constructs a stand-in, and
    25 files construct the real window - so this is the assertion that would
    have caught `85c11ab` at the moment it was written.
    """
    window = ChronoaWindow(_App())
    assert window is not None
    # A window that constructed but built no UI is the same failure wearing a
    # different hat, so the tree itself is asserted rather than the object.
    assert window.get_content() is not None, (
        "the window exists but packed no content - _setup_ui did not finish"
    )
