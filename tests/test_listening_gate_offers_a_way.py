"""The Listening panel named the switch that grants its permission, and offered no way there.

The same defect `gui/surfaces/desktop.py` had, in the panel one section away.
`_consent()` has always reported the permission by naming the switch exactly as
a person sees it -

    Not allowed - 'Let Chronoa name a sound when you ask' is off

- which is the most useful sentence on the page, and the only way to act on it
was to go and find that switch. `grep -c "Button(" listening.py` was 2 before
this change, both action buttons: *Transcribe it now* and *Look again*.

`heard-sound` is written in `settings_window/privacy.py` and nowhere else, so
`settings:privacy` is not a guess - it is the only page on which the answer can
change. The same route `desktop.py` uses for the two gates it names.

**The route is offered only on refusal**, and both negatives are tested here
because a guard that cannot fail is not a guard:

| `allowed` | route | why |
|---|---|---|
| `False` | yes | the gate is shut and one switch opens it |
| `True` | **no** | nothing to fix; the press would change nothing |
| `None` | **no** | "could not ask" is not "off" - a button would claim the one certainty the row has just said it lacks |
| any, `app=None` | **no** | built headless; the button would close over nothing |

`allowed=None` is the case most easily got wrong: `_StateRow.set_state` treats
`ok=None` as "a fact with no verdict", and offering a route on it would invent
the verdict the panel just disclaimed.
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
from shani_chronoa.gui.surfaces import listening  # noqa: E402


class _Config:
    def __init__(self):
        self.written = {}

    def set(self, key, value):
        self.written[key] = value

    def get_bool(self, key, default=False):
        return self.written.get(key) == "true"


class _App:
    """The smallest thing `wire_page_button` can close over."""

    def __init__(self):
        self.window = None
        self.config = _Config()

    def activate_action(self, name, arg):
        pass


def _buttons(widget, acc=None):
    acc = [] if acc is None else acc
    if isinstance(widget, Gtk.Button):
        acc.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _buttons(child, acc)
        child = child.get_next_sibling()
    return acc


def test_a_refused_permission_is_turned_on_here():
    """The positive case, asserted on **what the press writes**.

    It used to open Privacy settings and leave the person to find the row; now
    the switch is turned on from the page, by its Settings name. Asserted on
    the literal key, not `listening.CONSENT_KEY` - a mutated constant would
    move both sides of a tautology at once.
    """
    app = _App()
    banner = listening._consent_route(app, False)
    assert banner is not None, "a refused permission offered no way to turn it on"
    (button,) = [b for b in _buttons(banner) if b.get_label() == "Turn on"]
    button.emit("clicked")
    assert app.config.written == {"heard-sound-sense-enabled": "true"}, app.config.written


@pytest.mark.parametrize("allowed", [True, None])
def test_nothing_to_act_on_gets_no_button(allowed, monkeypatch):
    """**Both negatives.**

    Granted has nothing to fix, and `None` is a fact with no verdict - the row
    has just said it could not ask, so a button on it would claim a certainty
    the panel does not have. Either way the press would open Privacy settings
    and change nothing.
    """
    reached = []
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None:
                        reached.append(target) or True)

    assert listening._consent_route(_App(), allowed) is None, (
        "a route was offered on a gate that had nothing to act on "
        f"(allowed={allowed!r}) - a worse dead end than the sentence it "
        "replaced")
    assert reached == []


def test_a_headless_build_gets_no_dead_button(monkeypatch):
    """`build(app=None)` is how tests render this panel.

    `common.wire_page_button` closes over the app it is handed, so a button
    built with no app would look complete and press into nothing. The negative
    control matters: without the `app is None` guard this returns a real
    `Gtk.Button` and passes a naive "is there a button" check.
    """
    assert listening._consent_route(None, False) is None, (
        "a button was built with no app to press, which reads as complete and "
        "does nothing")


def test_the_route_is_the_page_that_actually_writes_the_key():
    """Why `settings:privacy` is the target, asserted against the source.

    Without this the target is a constant someone chose once. `heard-sound` is
    granted by one page; if that ever moves, this fails and the route follows,
    rather than the route quietly opening a Settings page that cannot change
    the answer.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa" \
        / "shani_chronoa" / "settings_window"
    # **The settings key, not the sense name.** `settings_window/senses.py`
    # mentions `heard-sound` too, but only in a table that describes what the
    # sense is for; the row that actually flips it is
    # `("Let Chronoa name a sound when you ask", "heard-sound-sense-enabled", ...)`
    # in privacy.py. Asserting on the sense name caught that ambiguity and
    # failed on it, which is why this searches for the key.
    key = "heard-sound-sense-enabled"
    writers = [q.name for q in root.glob("*.py")
               if key in q.read_text(encoding="utf-8", errors="replace")]
    assert writers == ["privacy.py"], (
        f"{key!r} is written by {writers!r}, so the route "
        "'settings:privacy' is pointing at a page that cannot change the "
        "answer - or the key has moved and this route now opens the wrong "
        "Settings page")
