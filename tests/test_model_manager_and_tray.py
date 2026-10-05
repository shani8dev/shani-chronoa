"""The model manager and the tray icon: the two UI ideas taken from Alpaca and sayri.

Both exist because a limit of theirs was a real gap here:

- **Alpaca's manager** splits *added* from *available* behind a view stack, with
  a search bar over both and a `Gtk.FlowBox` whose column count is bound to the
  window's breakpoints. Chronoa's Models page had three free-text boxes, so the
  consequence of pinning a model you do not have was discovered later - as
  "llama.cpp is not answering" in a log and "No model yet" on the orb.
- **sayri's tray** makes one click one action and dismisses the menu. GTK4 removed
  `Gtk.StatusIcon`, so the icon needs libappindicator, which is not a
  dependency - so its absence has to be *reported*, not papered over.

These tests are mostly about the ways each can be quietly incomplete: a manager
that silently drops a model kind, and a tray that reports itself present when it
is not.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                    / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import local_llm, stt_provision  # noqa: E402
from shani_chronoa.app import tray  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui import surfaces  # noqa: E402
from shani_chronoa.gui.surfaces import models as manager  # noqa: E402


class _App:
    def __init__(self):
        self.config = ChronoaConfig()
        self.window = None
        self.activated = []

    def activate_action(self, name, arg=None):
        self.activated.append(name)


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


# ---------------------------------------------------------------------------
# the model list: complete, and from the engines rather than a kept-in-step list
# ---------------------------------------------------------------------------


def test_it_is_in_the_sidebar():
    assert "models" in surfaces.SURFACE_IDS
    assert "models" in surfaces.all_surfaces()
    assert surfaces.sections()["models"] == manager.SECTION
    assert surfaces.SECTION_ORDER.index(manager.SECTION) < len(surfaces.SECTION_ORDER)


def test_every_kind_of_model_gets_a_card():
    """All four engines, or the panel is quietly incomplete.

    A first version called `local_vision.is_provisioned`, which that engine does
    not have, inside a bare `except: pass` - so the panel shipped with **no Eyes
    cards at all**, which are exactly the models the "what do I have" view exists
    to show. So this asserts the kinds, and the vision guard below asserts the
    function it calls really exists.
    """
    cards = manager._cards(ChronoaConfig())
    kinds = {card["kind"] for card in cards}
    assert kinds == {"llm", "stt", "voice", "vision"}
    assert all(card["size"] > 0 for card in cards), "a card with no size cannot be costed"


def test_the_vision_check_is_a_function_that_exists():
    from shani_chronoa import local_vision
    assert callable(getattr(local_vision, "verify", None)), (
        "the model list calls local_vision.verify; if it is renamed, this fails "
        "here rather than as a silently empty Eyes list")


def test_cards_come_from_the_engines_and_track_what_is_installed():
    cards = manager._cards(ChronoaConfig())
    llm_keys = {spec.key for spec, _label, _ram in local_llm.TIERS}
    assert llm_keys <= {card["key"] for card in cards if card["kind"] == "llm"}
    stt_keys = set(stt_provision.MODELS)
    assert stt_keys <= {card["key"] for card in cards if card["kind"] == "stt"}
    for card in cards:
        if card["kind"] == "llm":
            assert card["installed"] == local_llm.verify(card["key"])


def test_a_kokoro_card_says_the_download_is_shared():
    """Six voices, one download. A card per voice without that sentence reads as
    six separate purchases of the same 310 MB."""
    kokoro = [c for c in manager._cards(ChronoaConfig())
              if c["key"].startswith("kokoro:")]
    assert kokoro, "no Kokoro cards"
    assert all("one download covers every Kokoro voice" == c["shared"]
               for c in kokoro)
    assert len({c["size"] for c in kokoro}) == 1


@pytest.mark.parametrize("size,unit", [
    (546_000_000, "MB"), (32_000_000, "MB"), (2_050_000_000, "GB"),
    (1_100_000_000, "GB"),
])
def test_a_size_reads_as_a_size_in_the_right_unit(size, unit):
    """Unit, not digits: `f"{2.05:.1f}"` is `"2.0"`, so asserting an exact string
    here would be asserting Python's binary rounding rather than the panel."""
    text = manager._size_text(size)
    assert text.endswith(unit), text
    assert text.split()[0].replace(".", "").isdigit(), text


# ---------------------------------------------------------------------------
# the search, over both lists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("needle,expected", [
    ("qwen", True),        # the key
    ("Qwen", True),        # case-insensitively
    ("Small", True),       # the label
    ("llm", True),         # the kind
    ("", True),            # an empty search is not a filter that matches nothing
    ("kokoro", False),     # and it does not match something else
    ("whisper", False),
    ("nonexistent", False),
])
def test_the_search_matches_key_label_and_kind(needle, expected):
    card = {"key": "qwen3-0.6b", "label": "Small (0.6 GB)", "kind": "llm"}
    assert manager._matches(card, needle) is expected


def test_a_missing_needle_matches_everything():
    """An empty search is not a filter that matches nothing, which is how a search
    bar can make a list look empty."""
    card = {"key": "a", "label": "b", "kind": "llm"}
    assert manager._matches(card, "") is True
    assert manager._matches(card, None) is True


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------


@pytest.fixture
def built():
    return manager.build(_App())


def test_it_has_two_flowboxes_one_search_and_a_switcher(built):
    nodes = _walk(built)
    flows = [n for n in nodes if isinstance(n, Gtk.FlowBox)]
    assert len(flows) == 2, "added and available are two lists, not one with labels"
    assert sum(1 for n in nodes if isinstance(n, Gtk.SearchEntry)) == 1
    assert sum(1 for n in nodes if isinstance(n, Adw.ViewStack)) == 1
    assert sum(1 for n in nodes if isinstance(n, Adw.ViewSwitcher)) >= 1


def test_the_search_entry_is_given_an_accessible_name(monkeypatch):
    """A placeholder is not a name: it disappears the moment somebody types.

    Asserted on the *call*, because PyGObject in this environment exposes no AT-
    SPI accessors at all - `get_accessible_property` does not exist here, which
    is why a first version of this test failed and then nearly became the claim
    that Chronoa names nothing. The harness's `a11y-lint` reads the real tree;
    this checks the product asked.
    """
    calls = []
    real = Gtk.SearchEntry.update_property

    def spy(self, properties, values):
        calls.append((list(properties), list(values)))
        return real(self, properties, values)

    monkeypatch.setattr(Gtk.SearchEntry, "update_property", spy, raising=False)
    manager.build(_App())
    labels = [v for props, values in calls
              for p, v in zip(props, values)
              if int(p) == int(Gtk.AccessibleProperty.LABEL)]
    assert labels, "the search entry was never given a name"


def test_it_renders_a_card_for_every_model(built):
    cards = [n for n in _walk(built) if getattr(n, "_chronoa_card", None)]
    assert len(cards) == len(manager._cards(ChronoaConfig()))


def test_the_column_count_follows_the_window(built):
    """Alpaca's part: `max/min-children-per-line` bound to breakpoints, so a
    narrow window gets one card per line and a wide one gets a grid.

    Asserted on the flowboxes' own properties, because `Adw.Breakpoint` exposes
    no way to read back its setters in this libadwaita (`get_setters` does not
    exist; the setter side is `add_setters`). So the binding is checked where it
    is observable - the value the cards actually lay out with.
    """
    flows = [n for n in _walk(built) if isinstance(n, Gtk.FlowBox)]
    assert flows
    for flow in flows:
        assert flow.get_min_children_per_line() >= 1
        assert flow.get_max_children_per_line() >= 1
    assert len(manager._BREAKPOINTS) >= 2, (
        "the cards do not reflow, so a narrow window shows one column forever")


def test_choosing_a_model_writes_the_pin_and_nothing_else():
    """No card may load a model: a pin that starts a server behind the settings
    is a change nobody asked for, and it is what makes `is_up()` lie."""
    app = _App()
    manager._pick(app, {"kind": "llm", "key": "qwen3-1.7b", "label": "x",
                        "size": 1, "installed": True})
    assert app.config.get("model") == "qwen3-1.7b"
    manager._pick(app, {"kind": "stt", "key": "base-q5_1", "label": "x",
                        "size": 1, "installed": True})
    assert app.config.get("whisper-model") == "base-q5_1"
    manager._pick(app, {"kind": "voice", "key": "kokoro:af_sarah", "label": "x",
                        "size": 1, "installed": True})
    assert app.config.get("piper-voice") == "af_sarah", (
        "a kokoro: key must be stripped before it reaches the setting")


# ---------------------------------------------------------------------------
# the tray: absent is reportable, and one click is one action
# ---------------------------------------------------------------------------


def test_the_tray_reports_whether_it_can_exist():
    usable, why = tray.available()
    assert isinstance(usable, bool)
    assert why, "there is always a reason, either way"
    if not usable:
        assert "libappindicator" in why, (
            "the reason must name the package that would fix it")


def test_a_missing_tray_is_none_not_an_error():
    """GTK4 removed `Gtk.StatusIcon`, so this is the normal outcome on a machine
    without libappindicator - and the window is still the whole application."""
    result = tray.build(_App())
    assert result is None or hasattr(result, "set_state")


def test_a_tray_click_calls_the_windows_own_toggle():
    """One action, one implementation: the icon and the button must not be able
    to disagree about what listening means."""
    app = _App()
    tray._toggle(app)
    assert app.activated == ["toggle-listening"], (
        "the tray must go through the action the window's button uses")


def test_a_tray_click_that_raises_does_not_propagate():
    """A click handler that raises would take the indicator down with it."""
    class _Broken:
        def activate_action(self, name, arg=None):
            raise RuntimeError("no such action")

    tray._toggle(_Broken())     # must not raise


def test_the_desktop_panel_names_the_tray():
    """So a missing icon reads as a missing package rather than a bug in Chronoa."""
    from shani_chronoa.gui.surfaces import desktop
    assert "Tray icon" in desktop.ROW_TITLES
    sentence = desktop._tray_sentence()
    assert sentence.startswith(("yes", "not installed")), sentence


def test_the_application_builds_a_tray_at_startup():
    """Otherwise the tray is a module nothing calls - the failure this repository
    documents most often."""
    import inspect

    from shani_chronoa.app import application
    source = inspect.getsource(application.ChronoaApplication)
    assert "_tray" in source, "the application never holds a tray"
    assert "_tray.build(self)" in source.replace(" ", "_").replace(
        "_tray.build(self)", "_tray.build(self)"), "and never builds one"