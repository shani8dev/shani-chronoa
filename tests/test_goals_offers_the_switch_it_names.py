"""The Goals empty state named the switch that grants goals, and offered no way there.

Third occurrence of one dead end, after `gui/surfaces/desktop.py` and
`gui/surfaces/listening.py`. The empty state read, measured on the rendered
panel:

    No goals yet
    Ask Chronoa to plan something in steps and save it as a goal
    (Settings, Privacy: 'Let Chronoa keep multi-step goals'). It is listed here,
    and nothing runs until you start it.

**The panel never read the setting.** No `get_bool` anywhere in `goals.py` - it
named the switch without knowing whether the person had already flipped it. So
the sentence was true at most once, and the case it was written for (the gate is
shut) was indistinguishable from the case it was not (the gate is already on).

The button replaces the parenthetical rather than joining it: naming where a
switch lives *and* offering a way there says the same thing twice, and the
sentence is the half that goes stale.

Three states, and the two negatives are the point:

| `goals-enabled` | route | why |
|---|---|---|
| off | yes | the gate is shut and one switch opens it |
| on | **no** | nothing to fix; the press would change nothing |
| unreadable | **no** | "could not ask" is not "off". An unreadable setting falls back to the sentence, which is the honest thing when nothing is known |

The unreadable case falls back to `_WHERE_IT_IS` rather than to nothing, because
an empty state with no explanation reads as a bug - which this repo has shipped.
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
from shani_chronoa.gui.surfaces import goals  # noqa: E402


class _Config:
    def __init__(self, allowed=None, explode=False):
        self._allowed = allowed
        self._explode = explode
        self.written = []

    def set(self, key, value):
        self.written.append((key, value))

    def get(self, key, default=""):
        return str(default)

    def get_bool(self, key, default=False):
        if self._explode:
            raise RuntimeError("the settings backend is not answering")
        return bool(default if self._allowed is None else self._allowed)

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


def test_a_shut_gate_offers_the_switch_that_opens_it(monkeypatch):
    """The positive case, asserted on the **route**, not the label. A button
    with the right words that opens nothing is the orb this repo treats as the
    standing example of reading as complete and doing nothing."""
    # Since the 2026-10-15 redesign the route is **Turn on, here**: the banner
    # writes the switch itself instead of sending the person to Privacy
    # settings to find it. Asserted on the write, not the label.
    app = _App(allowed=False)
    banner = goals._grant_route(app)
    assert banner is not None, (
        "the empty state named the switch that grants goals and offered no way "
        "to reach it")
    buttons = _route_buttons(banner)
    assert len(buttons) == 1, "the banner has no Turn on button"

    buttons[0].emit("clicked")
    # A literal, not a constant read from the module: asserting against the
    # module's own value moves both sides at once and passes after a change.
    assert app.config.written == [("goals-enabled", "true")], (
        f"the press wrote {app.config.written!r}, not the goals switch")


def test_an_already_granted_gate_gets_no_button(monkeypatch):
    """**The negative.** Nothing to fix, so the press would change nothing - the
    dead end `devices.py` calls out by name."""
    reached = []
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None:
                        reached.append(target) or True)

    assert goals._grant_route(_App(allowed=True)) is None, (
        "a route was offered on a gate that was already granted")
    assert reached == []


def test_an_unreadable_gate_gets_no_button_and_keeps_the_sentence():
    """**The negative that is easy to get wrong.**

    "Could not ask" is not "off". Offering the button would claim the gate is
    shut, which is the one claim the panel does not have. The sentence stays in
    that case, because an empty state with no explanation reads as a bug.
    """
    assert goals._grant_route(_App(explode=True)) is None, (
        "a button was offered on a gate that could not be read")
    assert "Settings, Privacy" in goals._WHERE_IT_IS, (
        "with no button to offer, the sentence must still say where the switch "
        "is - an empty state with no explanation reads as a bug")


def test_the_button_replaces_the_parenthetical():
    """Naming where a switch lives *and* offering a way there says it twice, and
    the sentence is the half that goes stale."""
    assert "(Settings, Privacy" in goals._WHERE_IT_IS
    assert "(Settings, Privacy" not in goals._HOW_TO, (
        "the routed copy still carries the parenthetical, so a person with the "
        "gate shut is told where to go and handed a button for it")

    # **Which copy is shown, not what each copy contains.** Asserting only on
    # the constants passes whether or not the panel ever uses them - so pointing
    # the call site permanently at `_WHERE_IT_IS` left the first version of this
    # test green. The button decides.
    assert goals._empty_copy(object()) == goals._HOW_TO
    assert goals._empty_copy(None) == goals._WHERE_IT_IS


def test_the_key_is_written_by_the_page_the_route_opens():
    """Why `settings:privacy` is the target, asserted against the source rather
    than against a constant. If the row moves, this fails and the route follows."""
    import pathlib

    root = (pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"
            / "shani_chronoa" / "settings_window")
    writers = [p.name for p in root.glob("*.py")
               if goals.GOALS_KEY in p.read_text(encoding="utf-8",
                                                 errors="replace")]
    assert writers == ["privacy.py"], (
        f"{goals.GOALS_KEY!r} is written by {writers!r}, so the route "
        "'settings:privacy' points at a page that cannot change the answer")


@pytest.mark.parametrize("config", [None, object()])
def test_a_window_without_readable_settings_gets_no_button(config):
    """No settings, or settings that cannot answer a boolean. Both are facts
    this panel can still render, and neither is a shut gate."""
    app = _App()
    app.config = config
    assert goals._grant_route(app) is None

# ---------------------------------------------------------------------------
# the call site
# ---------------------------------------------------------------------------


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _route_buttons(page):
    return [w for w in _walk(page)
            if isinstance(w, Gtk.Button) and w.get_label() == "Turn on"]


@pytest.fixture
def empty_store(tmp_path, monkeypatch):
    """A goals store with nothing in it, so the empty state is the one built.

    `GoalStore` resolves under the state directory, so pointing `XDG_STATE_HOME`
    at a tmp path is enough - and it is the *panel* that must be walked, not the
    helpers.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def test_the_empty_state_really_carries_the_button(empty_store):
    """**The call site, and the reason this file is not done without it.**

    `_grant_route` and `_empty_copy` are helpers. Testing only helpers left a
    hole that mutation found immediately: changing the panel to

        _empty_copy(route))

    - dropping `route`, so the button never reaches the screen - left all seven
    tests green. Every one of them was about a decision, and none of them was
    about the decision being used.

    So the panel is built with an empty store and walked. A helper that is right
    and unused is still a dead end.
    """
    page = goals.build(_App(allowed=False))

    assert _route_buttons(page), (
        "the gate is shut and the empty state put no button on screen - the "
        "helper was right and unused")


def test_a_granted_gate_renders_no_button(empty_store):
    """**The negative, at the call site.** Same hole from the other side."""
    page = goals.build(_App(allowed=True))

    assert not _route_buttons(page), (
        "a route was rendered on an already-granted gate")
