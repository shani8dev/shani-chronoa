"""No panel may name the page that grants something and offer no way to get there.

Five surfaces shipped one defect, one after another, and each was found by hand:

| panel | what it said | offered |
|---|---|---|
| `desktop.py` | `global-shortcut-enabled` and `document-search-enabled` are granted in Settings | **nothing** |
| `listening.py` | Not allowed - 'Let Chronoa name a sound when you ask' is off | **nothing** |
| `goals.py` | (Settings, Privacy: 'Let Chronoa keep multi-step goals') | **nothing** |
| `phone.py` | Turn it on in Settings, under Privacy | **nothing** |
| `connections.py` | add 'telegram' to Gateways in Settings, Privacy | a Copy button |

`tests/test_dead_ends_have_buttons.py` already presses named buttons in four
panels. This one is the sweep: it builds **every registered surface**, reads the
text that reaches the screen, and fails if a panel points at a Settings row
without a route to it.

## What it does not do, and why that matters

**It only catches the phrasing.** A panel can be a dead end and pass this file,
because the rule is "if you tell a person where the switch is, give them a
button". That is the defect as it has actually appeared, five times, and a rule
that tried to catch every possible dead end would either fail on every panel or
need a judgement call per panel - which is what a human reviewer is for.

**It is not a substitute for reading the panel.** It walks the widget tree and
the visible strings, not the intent. A sentence naming a switch with no button
*on that row* but one elsewhere on the page passes, which is correct - the
`connections` fix is one button for the group, not one per row.

**It builds real panels.** Every surface is constructed through `build()` with
the same stub, so a panel that cannot be built at all fails here too. That is
deliberate: `diff.py` and `artifact_store.py` were once complete-looking and
100% unreachable because they raised before drawing a pixel.

Measured before this file existed, all 33 registered surfaces: **every panel that
named a Settings row had a route.** The guard is green, so it is a regression
guard rather than a report - and a red one means a sixth occurrence.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import surfaces  # noqa: E402

#: Text that tells a person *where a switch lives*. Narrow on purpose: the claim
#: is "you named the page, so you owe a button", and anything looser would fire
#: on a panel that merely mentions Settings in passing.
NAMES_A_SWITCH = re.compile(
    r"Settings,\s*(?:under\s*)?(?:Privacy|Senses)"
    r"|[Tt]urn (?:it|the key|them) on in Settings"
    r"|[Gg]rant it in Settings"
    r"|[Ii]n Settings, under (?:Privacy|Senses)",
)

#: What a route button is called. These are the labels
#: `common.open_settings_button()` is given, so the check reads the screen rather
#: than trusting a module constant - a panel that wires nothing still *builds* a
#: button, which is exactly what "dead ends have buttons" is guarding against.
ROUTE_LABELS = {"Open Privacy settings", "Open Senses settings"}


class _Config:
    """Every setting off, which is the state a fresh install is in.

    Off is the state that *shows* route buttons: each of the five fixes shows its
    button only while the gate is shut, so a permissive stub would hide the very
    rows this file is checking for.
    """

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=""):
        return str(self._values.get(key, default))

    def get_bool(self, key, default=False):
        return bool(self._values.get(key, default))

    def get_double(self, key, default=0.0):
        return float(self._values.get(key, default))

    def sense_allowed(self, name):
        """`privacy.py` reads senses through this, not through `get_bool`."""
        return bool(self._values.get(f"{name}-sense-enabled", False))

    privacy_mode = True
    wake_word_enabled = False
    whisper_model = ""
    stt_backend = "whisper"
    language = "en"


class _App:
    def __init__(self, **values):
        self.config = _Config(**values)
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


def _said(page):
    """Every string that reaches the screen on this page."""
    out = []
    for widget in _walk(page):
        if isinstance(widget, Gtk.Label):
            text = widget.get_text()
            if text:
                out.append(text)
        elif isinstance(widget, (Adw.ActionRow, Adw.ExpanderRow,
                                 Adw.PreferencesRow)):
            out.append(widget.get_title() or "")
            try:
                out.append(widget.get_subtitle() or "")
            except Exception:  # noqa: BLE001 - a row without a subtitle is fine
                pass
    return out


def _build(name):
    import importlib

    module = importlib.import_module(f"shani_chronoa.gui.surfaces.{name}")
    return module.build(_App())


@pytest.mark.parametrize("name", surfaces.SURFACE_IDS)
def test_a_panel_that_names_a_switch_offers_a_way_to_it(name):
    """Build it, read it, and check the sentence has a button behind it."""
    page = _build(name)

    said = _said(page)
    points_at_settings = sorted({s for s in said if NAMES_A_SWITCH.search(s)})
    routes = {w.get_label() for w in _walk(page)
              if isinstance(w, Gtk.Button) and w.get_label() in ROUTE_LABELS}

    assert routes or not points_at_settings, (
        f"{name!r} tells the person where the switch is and offers no way to "
        f"get there. The text: {points_at_settings[0][:160]!r}\n"
        "Add `common.open_settings_button(...)` + `common.wire_page_button(...)` "
        "- shown only while the gate is off, so a granted gate does not get a "
        "button that changes nothing.")


@pytest.mark.parametrize("name", surfaces.SURFACE_IDS)
def test_every_registered_surface_builds(name):
    """A panel that raises before drawing a pixel is invisible and looks fine.

    `diff.py` and `artifact_store.py` were both complete-looking unit tests and
    100% unreachable, because they raised on construction. Six GTK3-era defects
    in one file - `get_children()`, `set_relief()`, `button-release-event` - are
    all valid Python that `py_compile` cannot catch.
    """
    assert _build(name) is not None


def test_the_sweep_actually_covers_every_surface():
    """**The guard against the guard.** A parametrised sweep over an empty list
    passes, and a surface added to `SURFACE_IDS` without a matching test would
    never be checked. This pins the count and the coverage.
    """
    ids = surfaces.SURFACE_IDS
    assert len(ids) == len(set(ids)), "a surface is listed twice in SURFACE_IDS"
    assert len(ids) >= 33, (
        f"only {len(ids)} surfaces registered, was 33 when this was written - "
        "a sweep over fewer panels is a sweep over a smaller app")

# ---------------------------------------------------------------------------
# the pattern, checked against the sentences that produced this file
# ---------------------------------------------------------------------------

#: The exact strings the five panels printed, verbatim from the diffs. A regex
#: that quietly stops matching turns this file into 67 passing tests that check
#: nothing, and it would do so silently - which is the third time this session a
#: test has been green while proving less than it appeared to. So the pattern is
#: asserted against the sentences it was written for, before anything is trusted
#: to it.
#: **Two of the five do not match, and that is the finding, not an oversight.**
#:
#: `listening.py` said *"Not allowed - 'Let Chronoa name a sound when you ask'
#: is off"* - it named the **switch**, not the page, and never said "Settings" at
#: all. `desktop.py` said *"granted in Settings, and the Settings and Privacy
#: panels are where that happens"* - the page name is split across a clause, so
#: the contiguous "Settings, Privacy" does not appear.
#:
#: A pattern cannot reliably catch "names the switch", because a switch name is
#: any quoted phrase and panels quote plenty of things that are not switches.
#: Those two are covered by their own panel tests
#: (`test_listening_gate_offers_a_way.py`, `test_desktop_gates_offer_a_way.py`),
#: which assert the route directly rather than by pattern. What this sweep adds is
#: the **page-naming** phrasings, which are a closed, checkable set.
MATCHES = [

    # goals.py
    "Ask Chronoa to plan something in steps and save it as a goal "
    "(Settings, Privacy: 'Let Chronoa keep multi-step goals'). It is listed "
    "here, and nothing runs until you start it.",
    # phone.py
    "Nothing was asked of your phone, because 'phone-control-enabled' is off. "
    "Turn it on in Settings, under Privacy.",
    # connections.py
    "not accepted - add 'telegram' to Gateways in Settings, Privacy",
]

#: The two this pattern provably cannot catch, recorded so the limit is a
#: checked fact rather than something the next person assumes away.
CANNOT_MATCH = [
    # listening.py - names the switch, never the page
    "Not allowed - 'Let Chronoa name a sound when you ask' is off",
    # desktop.py - the page name is split across a clause
    "No gate is written from this panel: global-shortcut-enabled and "
    "document-search-enabled are granted in Settings, and the Settings and "
    "Privacy panels are where that happens.",
]


@pytest.mark.parametrize("said", MATCHES, ids=range(len(MATCHES)))
def test_the_pattern_still_matches_the_sentences_that_caused_this_file(said):
    """**The negative control for the guard itself.**

    Without this, widening or breaking `NAMES_A_SWITCH` is undetectable: the
    sweep stops finding rows, finds nothing wrong, and reports green. That is a
    guard that fails open, which is worse than no guard because it is trusted.
    """
    assert NAMES_A_SWITCH.search(said), (
        f"the pattern no longer matches a sentence that caused this file: "
        f"{said[:110]!r}")


@pytest.mark.parametrize("said", [
    "Chronoa is answering on this computer",
    "3 conversations recorded",
    "Nothing here polls, schedules, or writes.",
])
def test_the_pattern_does_not_match_ordinary_panel_text(said):
    """**The other direction.** Too broad a pattern fires on every panel and the
    rule stops meaning anything - it would demand a Settings route of a panel
    that never mentioned one."""
    assert not NAMES_A_SWITCH.search(said), (
        f"the pattern fires on unrelated panel text: {said!r}")


@pytest.mark.parametrize("said", CANNOT_MATCH, ids=range(len(CANNOT_MATCH)))
def test_the_limit_is_a_checked_fact_not_an_assumption(said):
    """**What this guard cannot do, asserted rather than assumed.**

    If one of these ever starts matching - because someone widened the pattern,
    or because a panel's wording changed - this fails and the note above is
    revisited. The per-panel tests still cover both cases directly; this is here
    so nobody reads the sweep as a complete net.
    """
    assert not NAMES_A_SWITCH.search(said), (
        f"this sentence now matches the pattern, so the note saying it cannot is "
        f"stale and the guard may be doing more than claimed: {said[:110]!r}")
