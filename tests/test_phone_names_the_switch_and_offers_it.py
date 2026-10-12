"""The Phone panel printed a settings key and offered no way to act on it.

Measured on the rendered panel, for two different gates:

    Calling is turned off ('phone-messages-send-enabled' in Settings, Privacy).
    Moving call audio is off ('bluetooth-call-enabled' in Settings, Privacy).

Two defects in one sentence.

**The storage name, not the one a person sees.** Settings calls the first row
"Let Chronoa send texts and place calls from your phone". The panel printed
`phone-messages-send-enabled` instead - a name that appears nowhere on screen
except here, and is not what a person would search Settings for. `GATE_NAMES`
now carries the labels the rows actually use, read from
`settings_window/privacy.py`.

**And no way to act.** Both keys are written in `settings_window/privacy.py` and
nowhere else, so `settings:privacy` is the only page on which either answer can
change. This is the fourth occurrence of the dead end
`tests/test_dead_ends_have_buttons.py` exists for, after `desktop.py`,
`listening.py` and `goals.py`.

The button appears **only while the gate is off**, following `devices.py`: granted
has nothing to fix, so a press would open Privacy settings and change nothing.

## A near-miss recorded here

The panel's other button is `Gtk.Button(label=k)` inside a loop over
`"123456789*0#"` - a **dial pad digit**. A grep for buttons labelled like a
settings key points straight at it, and "fixing" it would have put a digit on
the accessibility label of a keypad. Read before changing.
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

from shani_chronoa import pages  # noqa: E402
from shani_chronoa.gui.surfaces import phone  # noqa: E402

ROUTE = "Open Privacy settings"


class _Config:
    def __init__(self, on=False, explode=False):
        self._on, self._explode = on, explode

    def get(self, key, default=""):
        return str(default)

    def get_bool(self, key, default=False):
        if self._explode:
            raise RuntimeError("the settings backend is not answering")
        return self._on

    def get_double(self, key, default=0.0):
        return float(default)


class _App:
    def __init__(self, **kw):
        self.config = _Config(**kw)
        self.window = None
        self.tts = None
        self.stt = None

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


def test_no_gate_is_named_by_its_storage_key():
    """**The defect, verbatim.** A person cannot search Settings for
    `phone-messages-send-enabled`; they search for the sentence in the row.

    The assertion is on the **value**, not the key. `GATE_NAMES` is keyed by the
    setting key, so `key in GATE_NAMES` is true by construction - the first
    version of this test asserted that and could never fail.
    """
    for key in (phone.SEND_KEY, phone.CALL_KEY, phone.CONTROL_KEY):
        assert phone.GATE_NAMES[key] != key, (
            f"{key!r} is mapped to itself, so the storage name is still what "
            "reaches the screen")


def test_every_gate_has_the_name_settings_uses():
    for key in (phone.SEND_KEY, phone.CALL_KEY, phone.CONTROL_KEY):
        name = phone.GATE_NAMES[key]
        assert name[:1].isupper() and " " in name, (
            f"{key!r} maps to {name!r}, which does not read like a row label")
        assert "-" not in name, (
            f"{key!r} maps to {name!r}, which still contains a storage key")


def test_the_names_match_the_settings_page():
    """Why `GATE_NAMES` is right, asserted against the source rather than
    against a constant. If the row is renamed there, this fails here too."""
    import pathlib

    privacy = (pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"
               / "shani_chronoa" / "settings_window" / "privacy.py")
    source = privacy.read_text(encoding="utf-8", errors="replace")
    for key, name in phone.GATE_NAMES.items():
        assert f'"{name}"' in source, (
            f"{name!r} is not a row label in settings_window/privacy.py, so the "
            "panel and the page it points at would name the same switch "
            "differently")
        writers = [p.name for p in privacy.parent.glob("*.py")
                   if key in p.read_text(encoding="utf-8", errors="replace")]
        assert writers == ["privacy.py"], (
            f"{key!r} is written by {writers!r}, so 'settings:privacy' is "
            "pointing at a page that cannot change the answer")


def test_the_route_reaches_privacy_when_the_gate_is_off(monkeypatch):
    reached = []
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None:
                        reached.append(target) or True)

    panel = phone.PhonePanel(_App(on=False))
    row, button = panel._note_row()

    # The button is created once and shown only while a gate is off.
    assert button.get_visible() is False, (
        "the route button starts visible on a row with no gate reported yet")

    panel._say_gate(_label := Gtk.Label(label=""), button, phone.SEND_KEY,
                    "Calling is")

    assert "Calling is turned off." in _label.get_text(), _label.get_text()
    # **The sentence names the switch the way Settings does.**
    assert phone.GATE_NAMES[phone.SEND_KEY] in _label.get_text(), _label.get_text()
    assert phone.SEND_KEY not in _label.get_text(), (
        f"the panel is still printing the storage key: {_label.get_text()!r}")

    button.emit("clicked")
    assert reached == ["settings:privacy"], (
        f"the press reached {reached!r}, not the page that writes "
        f"{phone.SEND_KEY!r}")


def test_a_granted_gate_hides_the_route(monkeypatch):
    """**The negative.** Nothing to fix, so the button goes away rather than
    sitting there opening a page and changing nothing."""
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None: True)

    panel = phone.PhonePanel(_App(on=True))
    _row, button = panel._note_row()
    panel._say_gate(Gtk.Label(label=""), button, phone.SEND_KEY, "Calling is")

    assert button.get_visible() is False, (
        "a route stayed visible on a gate that was already granted")


def test_the_panel_really_carries_the_buttons():
    """**The call site.**

    The Goals guard found that testing helpers proves nothing about a button
    reaching the screen: changing the panel to drop the route entirely left
    every helper test green. This one builds the panel and walks it, in the
    state where a gate is off.
    """
    page = phone.build(_App(on=False))
    labels = [w.get_label() for w in _walk(page)
              if isinstance(w, Gtk.Button) and w.get_label()]

    assert ROUTE in labels, (
        "the gate is off and no route was built onto the panel - the note names "
        "a switch and nothing can be done about it")


def test_a_granted_control_gate_has_no_route():
    """**The negative, and the branch that matters.**

    With `phone-control-enabled` on, the panel builds its tabs and the control
    gate's route must not be there. (When it is *off* the panel returns early,
    so this is the only state in which the tabs exist to be walked.)
    """
    page = phone.build(_App(on=True))
    labels = [w.get_label() for w in _walk(page)
              if isinstance(w, Gtk.Button) and w.get_label()]

    # The notes' own buttons start hidden; only the control gate's was ever
    # appended unconditionally, and only on the off branch.
    assert ROUTE not in labels or all(
        not b.get_visible() for b in _walk(page)
        if isinstance(b, Gtk.Button) and b.get_label() == ROUTE), (
        "a route was left visible on an already-granted control gate")

def test_the_control_gate_names_the_switch_as_settings_does():
    """**The call site for the control gate, which the rest of this file missed.**

    Every other assertion here reads `GATE_NAMES`, so putting the storage key
    back into the one sentence that is actually rendered on screen - the status
    row on the branch where the panel returns earliest - left all seven tests
    green. The table can be perfect and unread.

    So the panel is built with the gate shut and its rendered text checked.
    """
    page = phone.build(_App(on=False))

    said = []
    for widget in _walk(page):
        if isinstance(widget, Gtk.Label) and widget.get_text():
            said.append(widget.get_text())
        if isinstance(widget, Adw.ActionRow):
            said.append(widget.get_title() or "")
            said.append(widget.get_subtitle() or "")
    text = " ".join(said)

    assert phone.GATE_NAMES[phone.CONTROL_KEY] in text, (
        f"the shut-gate sentence does not name the switch as Settings does. "
        f"Rendered: {text[:200]!r}")
    assert phone.CONTROL_KEY not in text, (
        f"the storage key {phone.CONTROL_KEY!r} reached the screen again: "
        f"{text[:200]!r}")
