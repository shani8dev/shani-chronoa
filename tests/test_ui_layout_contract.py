"""Every surface must be scrollable, must wrap, must not overflow, and must stop moving.

This file is an audit turned into a contract. It exists because measuring all
twenty surfaces found one that was not scrollable at all - `inventory`, whose
content went straight into the toolbar's content slot, so a window shorter than
its twenty-odd rows could not reach the bottom of them. Nineteen had a
`Gtk.ScrolledWindow` and that one had none, and nobody had noticed, because a
panel that is *mostly* visible looks fine in a screenshot taken at full height.

Four properties, each with a failure mode behind it:

- **scrollable** - a surface taller than the window needs a scroller, or its
  bottom is unreachable.
- **no horizontal overflow** - the minimum width has to fit a narrow window.
  Measured at 480px: the widest surface came to 256px, so this passes today, and
  it passes *by measurement* rather than by hope - which is the difference
  between this test and the clipping that was really the harness having no
  window manager.
- **long text wraps or ellipsises** - a clipped explanation is not an
  explanation, and a label that overflows its row silently is worse than one
  that visibly shortens.
- **anything that moves can stop** - every selector with an `animation` needs a
  `.reduce-motion` rule, because the desktop's accessibility setting is only
  honoured if something reads it.
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

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui import surfaces  # noqa: E402
from shani_chronoa.gui import AssistantState  # noqa: E402

#: A window narrow enough to catch anything: the phone-width case, and the width
#: at which a 560px wizard looked clipped.
NARROW = 480

#: A label longer than this has to wrap or ellipsise. Group titles and button
#: labels are short by design; a 60-character row that neither wraps nor
#: ellipsises is the defect.
LONG_TEXT = 30


class _App:
    def __init__(self):
        self.config = ChronoaConfig()
        self.window = None

    def activate_action(self, name, arg=None):
        pass


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _build(name):
    import importlib
    module = importlib.import_module(f"shani_chronoa.gui.surfaces.{name}")
    return module, module.build(_App())


@pytest.fixture(scope="module")
def built():
    """Every surface, built once: building twenty GTK panels per test is slow."""
    out = {}
    for name in surfaces.SURFACE_IDS:
        out[name] = _build(name)
    return out


def test_there_are_surfaces_to_check(built):
    """A guard against this file passing vacuously if the registry empties."""
    assert len(built) >= 15, f"only {len(built)} surfaces were measured"


def test_every_surface_icon_exists_on_this_machine(built):
    """A name the installed theme does not have renders as a blank gap the size
    of an icon - and no assertion on the string can see that.

    Existence is the weaker half of the problem and the only half that can be
    checked without looking at pixels, so this is deliberately not the whole
    answer. `phone-symbolic` **existed** on this machine and still rendered as
    an empty rounded rectangle beside its title, which reads as a missing icon
    rather than as a phone; that half is judged from a rendered sidebar, and
    this file does not pretend a name check can stand in for it.

    Two sources, because a surface's own `ICON` is not where the misses were.
    The eight names this caught were four `ICON` attributes and four *inline*
    names - a toolbar button's icon, a status icon looked up in a dict, a
    category map - so a check that read only `ICON` would have passed with four
    blanks still on screen. `diff.py` is the sharpest case: its own comment
    recorded that `diff-symbolic` was not shipped and fixed `ICON` accordingly,
    while a status lookup three hundred lines down still asked for it.
    """
    from gi.repository import Gdk  # noqa: E402
    import shani_chronoa.gui as gui  # noqa: E402

    display = Gdk.Display.get_default()
    if display is None:
        # Skipping on a machine with a live session is the failure mode
        # documented in AGENTS.md: this returns None until GTK is initialised,
        # so a guard that skipped on None skipped everywhere and passed.
        pytest.skip("no display, so the installed icon theme cannot be asked")
    theme = Gtk.IconTheme.get_for_display(display)

    missing = sorted(
        f"{name} -> ICON={module.ICON!r}"
        for name, (module, _widget) in built.items()
        if not theme.has_icon(module.ICON)
    )

    # Every `-symbolic` literal in the GUI package. 56 names today, all of which
    # resolve, so this has no false positives to tolerate; a future string that
    # ends in `-symbolic` without being an icon would be reported here, which is
    # the cheaper mistake to make.
    inline = sorted({
        m
        for source in Path(gui.__file__).parent.rglob("*.py")
        for m in re.findall(r'["\']([a-z0-9][a-z0-9-]*-symbolic)["\']',
                            source.read_text(encoding="utf-8"))
        if not theme.has_icon(m)
    })
    missing.extend(f"{n} (inline literal)" for n in inline)

    assert not missing, (
        "icons this theme does not have, which render as a blank gap: "
        + ", ".join(missing))


@pytest.mark.parametrize("name", sorted(surfaces.SURFACE_IDS))
def test_every_surface_can_scroll(name, built):
    """A panel taller than the window with no scroller has an unreachable bottom.

    This is the one that failed: `inventory` passed twenty-odd rows straight to
    the toolbar's content slot, so the last functions listed could not be read at
    any window height.
    """
    _module, widget = built[name]
    scrollers = [n for n in _walk(widget) if isinstance(n, Gtk.ScrolledWindow)]
    assert scrollers, f"{name} has no Gtk.ScrolledWindow, so tall content cannot scroll"


@pytest.mark.parametrize("name", sorted(surfaces.SURFACE_IDS))
def test_no_surface_overflows_a_narrow_window(name, built):
    """Minimum width has to fit NARROW, or GTK clips or adds a sideways scrollbar."""
    _module, widget = built[name]
    widest = 0
    for node in _walk(widget):
        try:
            widest = max(widest, node.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        except Exception:                       # noqa: BLE001 - an unmeasurable node is not an overflow
            continue
    assert widest <= NARROW, f"{name} needs {widest}px, more than {NARROW}px"


@pytest.mark.parametrize("name", sorted(surfaces.SURFACE_IDS))
def test_long_labels_wrap_or_ellipsise(name, built):
    """A label that is neither is a clipped explanation, silently."""
    _module, widget = built[name]
    offenders = []
    for label in (n for n in _walk(widget) if isinstance(n, Gtk.Label)):
        text = (label.get_label() or "").strip()
        if len(text) <= LONG_TEXT or label.get_wrap():
            continue
        if label.get_ellipsize() != 0:         # Pango.EllipsizeMode.NONE == 0
            continue
        # A group title inside a preferences group is a heading, not a sentence,
        # and is allowed to be long only if the group clips it.
        #
        # Two bugs on these lines, both invisible while the first one raised.
        # `PreferencesGroup` is `Adw`, not `Gtk`, so this raised
        # `AttributeError` on the first long label and every surface in this
        # parametrization errored instead of reporting. And `pass` fell through
        # to `offenders.append(...)`, so the exemption did nothing even once the
        # name resolved - a check that cannot fail, in the file whose whole
        # subject is checks that cannot fail.
        if any(isinstance(a, Adw.PreferencesGroup) for a in _walk(widget)):
            continue
        offenders.append(text[:44])
    assert not offenders, f"{name}: {offenders}"


def test_every_animation_can_be_stopped():
    """The desktop's reduce-motion setting is only honoured if something reads it.

    Read out of the stylesheet rather than remembered: three animations existed
    before this file, and the orb's `queued` pulse was added later - which is
    exactly the kind of addition that arrives without its opt-out.
    """
    from shani_chronoa.gui import style as style_module
    source = Path(style_module.__file__).read_text(encoding="utf-8")
    start = source.index('css_data = b"""') + len('css_data = b"""')
    css = source[start:source.index('"""', start)]

    # **Comments are not selectors.** The sheet explains itself in prose between
    # the rules - including prose that mentions `.reduce-motion` and
    # `animation:` - and a naive block regex captured that prose as a selector
    # list, so the first version of this test reported eleven "uncovered
    # animations" and every one was a sentence.
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    animated = set()
    for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", bare):
        if "animation:" in block.group(2):
            for selector in block.group(1).split(","):
                selector = selector.strip()
                if selector and not selector.startswith(".reduce-motion"):
                    animated.add(selector)
    assert animated, "no animation found - this file has stopped reading the sheet"

    reduced = bare[bare.index(".reduce-motion"):] if ".reduce-motion" in bare else ""
    missing = [s for s in sorted(animated)
               if f".reduce-motion {s}" not in reduced]
    assert not missing, f"these animate with no way to stop them: {missing}"


def test_the_orb_pulses_and_can_stop():
    """The one animation a person watches for hours, so it is checked by name."""
    from shani_chronoa.gui import style as style_module
    source = Path(style_module.__file__).read_text(encoding="utf-8")
    for state in (AssistantState.LISTENING, AssistantState.QUEUED):
        rule = f".chronoa-orb.state-{state.value}"
        assert rule in source, f"{state} has no orb rule"
        assert f".reduce-motion {rule}" in source, f"{state} pulses and cannot stop"


def test_the_orb_has_a_style_and_a_label_for_every_state():
    """A state with no rule renders unstyled, whatever colour the table says."""
    from shani_chronoa.gui import widgets
    for state in AssistantState:
        assert state in widgets._STATE_STYLE
        assert widgets._STATE_LABELS.get(state)
        assert widgets._STATE_STYLE[state][1].endswith("-symbolic")


def test_the_new_model_manager_reflows():
    """Alpaca's part, asserted where it is observable: the card flowboxes.

    `Adw.Breakpoint` cannot be read back in this libadwaita - there is no
    `get_setters` - so the binding is checked through the property it drives.
    """
    from shani_chronoa.gui.surfaces import models
    widget = models.build(_App())
    flows = [n for n in _walk(widget) if isinstance(n, Gtk.FlowBox)]
    assert len(flows) == 2, "added and available are two lists"
    for flow in flows:
        assert flow.get_min_children_per_line() >= 1
        assert flow.get_max_children_per_line() >= flow.get_min_children_per_line()
    assert len(models._BREAKPOINTS) >= 2, "and nothing reflows them"

# ---------------------------------------------------------------------------
# the wizard, which is not a surface and had the worst of it
# ---------------------------------------------------------------------------


def _wizard_page(tag):
    """One wizard page's *demand* - the number that decides whether the page
    needs a horizontal scrollbar.

    Measured on the child of the page's own `Gtk.ScrolledWindow`, not on the
    page: a widget that has been allocated 900px will happily report 900px, and
    reading that as a demand is how this was chased in circles for a while. The
    demand is what the content *asks* for.
    """
    from shani_chronoa import setup_wizard
    from shani_chronoa.config import ChronoaConfig

    class _App:
        config = ChronoaConfig()
        window = None

        def activate_action(self, name, arg=None):
            pass

    built = {}
    ready = []
    app = Gtk.Application(application_id="dev.shani.auditor",
                           flags=Gio.ApplicationFlags.NON_UNIQUE)

    def on_activate(a):
        built["win"] = setup_wizard.build_window(a, ChronoaConfig())
        built["win"].present()
        ready.append(True)
        # Two turns: one to build, one so the pages are realised.
        GLib.timeout_add(400, lambda: (app.quit(), False)[1])

    app.connect("activate", on_activate)
    app.hold()
    try:
        app.run([])
    finally:
        app.release()
    win = built.get("win")
    if win is None:
        pytest.skip("the wizard would not build headless")
    page = next((n for n in _walk(win)
                 if isinstance(n, Adw.NavigationPage) and n.get_tag() == tag), None)
    if page is None:
        pytest.skip(f"no page called {tag}")
    scroller = next((n for n in _walk(page) if isinstance(n, Gtk.ScrolledWindow)), None)
    assert scroller is not None, f"{tag} has no scroller"
    return scroller.get_child()


import gi  # noqa: E402  - GLib and Gio are used by the harnesses below

gi.require_version("Adw", "1")
from gi.repository import GLib, Gio  # noqa: E402

WIZARD_WIDTH = 560


# Every page the wizard has. `extras` is deliberately absent: the hub page was
# removed, because it existed only to list the pages it stood between you and.
# Naming it here would skip forever, which reads as a broken test rather than a
# deleted page.
@pytest.mark.parametrize("tag", [
    "welcome", "mode", "cloud-keys", "brain", "model-picker", "ears", "voice",
    "review", "eyes", "imagine", "memory", "photos", "sounds", "speakers",
    "languages", "done",
])
def test_no_wizard_page_needs_a_sideways_scrollbar(tag):
    """The bar that sat under every wizard page for days.

    `Adw.NavigationPage` wraps its child in a scroller whose horizontal policy is
    the default, so a page wider than the window gets a sideways scrollbar.
    Nothing here needs one: measured, the worst page asks for 538px of 560.

    Three things caused it, all measured rather than guessed:
      * wrapping labels with no `set_max_width_chars` (the welcome description
        alone asked for 947px),
      * long `Adw.ActionRow` subtitles - libadwaita 1.5 does not wrap them, so
        a 130-character subtitle is 1,227px,
      * long `Adw.PreferencesGroup` descriptions, which do not wrap either.
    """
    demand = _wizard_page(tag).measure(Gtk.Orientation.HORIZONTAL, -1)[1]
    assert demand <= WIZARD_WIDTH, (
        f"the {tag} page asks for {demand}px, more than {WIZARD_WIDTH}px, so it "
        "gets a horizontal scrollbar")


# ---------------------------------------------------------------------------
# the main window, which is the screen people use most and was measured last
# ---------------------------------------------------------------------------


def _main_window():
    """The main window, built headless.

    It is not a surface and not a wizard page, so it was the one screen the
    audit never covered - and the first pass over it found two suggestion chips
    whose labels could neither wrap nor ellipsise, which is the one thing a
    wrapping `Gtk.FlowBox` cannot rescue: a single chip wider than the window.
    """
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.gui.window import ChronoaWindow

    class _App:
        def __init__(self):
            self.config = ChronoaConfig()
            self.assistant = None
            self.llm = None

        def activate_action(self, name, arg=None):
            pass

        def quick_ask(self, *a):
            pass

        def toggle_listening(self, *a):
            pass

        def answer_pending_question(self, text):
            return False

        def add_percept(self, *a):
            pass

    built = {}
    # **`NON_UNIQUE`, and this is not a detail.** A `Gtk.Application` claims a
    # D-Bus well-known name from its id. If another process already owns that
    # name - which is exactly what happens when two runs of this suite overlap,
    # and this repo's own AGENTS.md warns about concurrent runs - then `run()`
    # forwards the activation to the *owner* and returns at once. `built["win"]`
    # is never filled, the helper returns None, and the test **skips**. Measured
    # here with three concurrent suites live: the plain construction gave
    # `False` (no window), `NON_UNIQUE` on the same id gave `True`, and a
    # different id gave `True`. A test that skips because a stranger took its bus
    # name reads as coverage of a window nobody built.
    app = Gtk.Application(application_id="dev.shani.wincontract",
                           flags=Gio.ApplicationFlags.NON_UNIQUE)

    def on_activate(a):
        built["win"] = ChronoaWindow(a, ChronoaConfig())
        built["win"].present()

    app.connect("activate", on_activate)
    # **A main loop that is never told to stop.** `app.hold()` keeps the
    # application alive after `activate` returns, and nothing in this harness
    # ever called `app.quit()`, so `app.run([])` never returned and the test hung
    # until something killed it - measured: `timeout 60 pytest -k
    # main_window_can_scroll` exits **124** with no output at all, which reads as
    # a passing suite and is not one. The wizard harness below got this right
    # (`GLib.timeout_add(..., app.quit())`) and this one did not; the timeout is
    # the whole fix, and the same guard is why a failure here now shows up as a
    # failure rather than as a stall.
    # `app`, not `a`: the timeout callback is defined outside `on_activate`, so
    # the first attempt referenced a name that is not in its scope. Inside a
    # PyGObject callback that is a `NameError` swallowed by the main loop, and
    # `app.run([])` then never returned - the same stall, one indirection
    # further in. A control that fails silently is worse than a missing one.
    GLib.timeout_add(250, lambda: (app.quit(), False)[1])
    app.hold()
    try:
        app.run([])
    finally:
        app.release()
    return built.get("win")


def test_the_main_window_can_scroll():
    window = _main_window()
    if window is None:
        pytest.skip("the main window would not build headless")
    scrollers = [n for n in _walk(window) if isinstance(n, Gtk.ScrolledWindow)]
    assert scrollers, "the conversation area has no scroller, so long turns cannot be read"


def test_the_main_window_wraps_or_ellipsises_its_long_text():
    window = _main_window()
    if window is None:
        pytest.skip("the main window would not build headless")
    offenders = [
        (l.get_label() or "")[:50] for l in _walk(window)
        if isinstance(l, Gtk.Label)
        and not l.get_wrap()
        and len((l.get_label() or "").strip()) > LONG_TEXT
        and int(l.get_ellipsize()) == 0
    ]
    assert not offenders, (
        "these cannot shrink, and a single one of them can be wider than the "
        f"window: {offenders}")


def test_a_suggestion_chip_cannot_be_wider_than_its_window():
    """A `Gtk.FlowBox` wraps its children, which rescues a long row and cannot
    rescue a long child. So the chip's own label must ellipsise."""
    from shani_chronoa.gui.widgets import SuggestionBar
    window = _main_window()
    if window is None:
        pytest.skip("the main window would not build headless")
    # `n.get_css_classes.__self__` is a `try_getattr` in disguise and it is not
    # a bound method: on this PyGObject it is a `gi.FunctionInfo`, so the
    # attribute is `None` and the whole list came out empty - the assertion
    # below then had nothing to check and the test passed over chips it never
    # found. `get_css_classes()` is on `Gtk.Widget` alone, so testing for that
    # is both correct and sufficient.
    chips = [n for n in _walk(window)
             if isinstance(n, Gtk.Widget) and "suggestion-chip" in n.get_css_classes()]
    assert chips, (
        "no suggestion chips on the empty state - the check below would have had "
        "nothing to look at and said so by passing")
    for chip in chips:
        labels = [n for n in _walk(chip) if isinstance(n, Gtk.Label)]
        assert labels, "a chip with no label cannot ellipsise"
        for label in labels:
            assert int(label.get_ellipsize()) != 0, (
                f"chip label does not ellipsise: {(label.get_label() or '')[:40]!r}")
