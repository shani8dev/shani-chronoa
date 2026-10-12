"""The "Answering now" panel named the wizard and offered nothing, on one branch of two.

Two adjacent branches in `gui/surfaces/model.py`:

    if no engine can run it:      return self._with_setup(widget)      <- button
    if none is available at all:  return self.status_recorder.row(...) <- nothing

`_with_setup` appends the "Set Chronoa up" button - the only thing in the app that
downloads a model. The branch three lines below it returned a bare row.

Measured on the rendered panel with the gate shut:

    Needs attention
    qwen3:4b - no engine available - chosen for this computer's hardware tier;
    none of the 3 engines can run it
    [BUTTON] Reload

One button, and it re-reads the same answer. The row names a problem a person
cannot act on, and the fix - the wizard - is the one route this panel already
knows how to open, six lines above.

This is the sixth instance of the dead end `tests/test_dead_ends_have_buttons.py`
was written about, after `desktop.py`, `listening.py`, `goals.py`, `phone.py` and
`connections.py` - and the reason it was missed by the sweep in
`tests/test_no_panel_points_at_settings_without_a_way.py` is worth recording: the
panel did not name a *Settings page*, it named the *setup wizard*. The sweep's
pattern checks for one of those and this was the other.

**The claim under test is that both branches offer the route, and a lone button
somewhere on the page is not the same as the route on the row that needs it.**
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui.surfaces import model  # noqa: E402

ROUTE = "Set Chronoa up"


class _Config:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=""):
        return str(self._values.get(key, default))

    def get_bool(self, key, default=False):
        return bool(self._values.get(key, default))

    def get_double(self, key, default=0.0):
        return float(self._values.get(key, default))

    privacy_mode = True
    wake_word_enabled = False
    whisper_model = ""
    stt_backend = "whisper"
    language = "en"


class _App:
    def __init__(self):
        self.config = _Config()
        self.window = None
        self.tts = None
        self.stt = None
        self.hardware = None
        self.tool_tracker = None
        self.percept_store = None

    def activate_action(self, name, arg):
        pass


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _routes(widget):
    return [w for w in _walk(widget)
            if isinstance(w, Gtk.Button) and w.get_label() == ROUTE]


def test_the_attention_row_carries_the_route_it_needs():
    """Built through the same refresh the panel uses, so the row is the one a
    person sees rather than one constructed to pass."""
    page = model.build(_App())

    assert _routes(page), (
        "the panel says no engine is available and offers no way to fix it. The "
        "route is the setup wizard, which is the only thing that downloads a "
        "model, and this panel opens it six lines above in a sibling branch.")


def test_reloading_is_not_the_same_as_a_way_out():
    """**The control.** A "Reload" button existed the whole time, so a naive
    check for *any* button on the page passed before the fix. Reloading re-reads
    the answer it already gave."""
    page = model.build(_App())
    labels = {w.get_label() for w in _walk(page) if isinstance(w, Gtk.Button)}

    assert ROUTE in labels, f"no route on the page; buttons present: {sorted(labels)}"
    assert ROUTE not in labels - {ROUTE}, "impossible"  # pragma: no cover
