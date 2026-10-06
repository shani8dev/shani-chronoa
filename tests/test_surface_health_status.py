"""Every panel says something about itself, and the sidebar shows it.

A sidebar of twenty rows is a list. A sidebar of twenty rows with a health dot
on each is a dashboard, and the difference is the whole point: the question a
person opens this window with is usually "is anything wrong right now", and the
answer should not require opening twenty panels in turn to find out.

**The dot is drawn from `page.status()`, and `page.status` is a recorder the
panel's own status row was written through.** So the dot cannot disagree with the
sentence at the top of the panel - which is the failure this file is built to
prevent. An earlier version had twenty surfaces each re-derive their answer from
whatever they had read, and `senses.py` carried a comment about having to be
careful; a re-derivation is a second count, and a second count falls out of date
the moment the panel refreshes without rebuilding.

Every surface here is built through the product's own registry, with the real
`SURFACE_IDS` order, because a test that enumerates its own list would pass
while a surface was dropped from the sidebar.
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

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui import surfaces  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.gui.window import _panel_status  # noqa: E402


class _App:
    """Enough of an application to build any panel, and no more than that.

    `tool_tracker` is `None` on purpose: `activity.build` makes its own, so the
    panel is exercised the way it is when the window builds it, rather than
    against a fixture that happens to suit it.
    """

    def __init__(self) -> None:
        self.config = ChronoaConfig()
        self.window = None
        self.tool_tracker = None


def _build(name):
    import importlib

    module = importlib.import_module(f"shani_chronoa.gui.surfaces.{name}")
    return module, module.build(_App())


@pytest.fixture(scope="module")
def built():
    """Every panel the sidebar offers, built once.

    Module-scoped because building twenty panels is slow (several shell out to
    `systemctl`, `busctl` and `gsettings`) and none of these tests mutates a
    panel.
    """
    out = {}
    for name in surfaces.SURFACE_IDS:
        module, page = _build(name)
        out[name] = (module, page)
    return out


def test_the_sidebar_still_has_its_panels(built):
    """The guard against this file passing on an empty registry."""
    assert len(built) >= 15, f"only {len(built)} panels were built"
    assert len(built) == len(surfaces.SURFACE_IDS), (
        "a panel in SURFACE_IDS did not build - the assertions below would "
        "silently cover fewer panels than the sidebar offers")


@pytest.mark.parametrize("name", sorted(surfaces.SURFACE_IDS))
def test_every_panel_says_something_about_itself(name, built):
    """`status` is present, is callable, and answers in the closed vocabulary.

    Not just present: `_panel_status` calls it and *validates the return*, so a
    fourth word would be rejected and the row would silently keep no dot - which
    reads as "nothing is wrong" on a panel that cannot say whether it is.
    """
    _module, page = built[name]
    assert callable(getattr(page, "status", None)), (
        f"{name} exposes no status(), so its sidebar row gets no health dot. "
        f"_panel_status is duck-typed on purpose (window.py): a panel that "
        f"cannot say is not a broken panel, but twenty of them is a flat list.")
    answer = page.status()
    assert answer in common.STATUS_CLASSES, (
        f"{name}.status() returned {answer!r}; the vocabulary is "
        f"{sorted(common.STATUS_CLASSES)} and a fourth word renders an "
        "uncoloured dot")


@pytest.mark.parametrize("name", sorted(surfaces.SURFACE_IDS))
def test_what_the_window_would_draw_is_accepted(name, built):
    """The value survives the one function that consumes it.

    `_panel_status` is the only reader, and it validates the vocabulary and
    swallows exceptions. Asserting on it directly is what stops a panel's
    `status()` being correct in isolation and unusable in the pipeline.
    """
    _module, page = built[name]
    assert _panel_status(page) == page.status()


def test_a_panel_whose_status_raises_is_left_without_a_dot(built):
    """A sidebar dot is not worth taking the window down for.

    Not hypothetical: several panels read `systemctl`, `busctl` and gsettings
    behind `status()`, and any of them can raise on a machine where the tool is
    missing. `_panel_status` catches it and reports no answer, and the row keeps
    no dot - which is the correct reading, because a panel that cannot say is not
    a panel claiming all is well.
    """
    _module, page = built["senses"]

    def explodes():
        raise RuntimeError("systemctl did not answer")

    original = page.status
    page.status = explodes
    try:
        assert _panel_status(page) is None
    finally:
        page.status = original
    assert _panel_status(page) == page.status(), "the panel was left broken"


def test_a_fourth_word_is_not_passed_through(built):
    """The closed vocabulary is enforced at both ends, not one.

    `status_row` raises on a fourth word and `_panel_status` rejects it. A panel
    that returned one anyway would get no dot, so it reads as "no news" rather
    than as a bug - which is why this asserts both halves refuse.
    """
    with pytest.raises(ValueError):
        common.status_row("degraded", "something", "")
    _module, page = built["senses"]
    original = page.status
    page.status = lambda: "degraded"
    try:
        assert _panel_status(page) is None
    finally:
        page.status = original


def test_the_dot_agrees_with_the_row_the_panel_shows(built):
    """The recorder is one value, read twice - and this is that claim, tested.

    The one thing Phase 1 is for. Every panel builds a status row through
    `common.StatusRecorder.row`, which remembers the word it just rendered, and
    exposes `status()` for the dot. If any panel had grown a *second* place that
    decided its own health - a hand-written closure over its own variables, or a
    status row written straight to `common.status_row` while `status()` answered
    from somewhere else - this is the test that would catch it.

    It checks the property rather than the source: each panel's rendered row
    carries the CSS class for its state, and the class and the word have to
    agree, because `status_row` is what applies the class.
    """
    for name, (_module, page) in built.items():
        recorder = _recorder_of(page)
        if recorder is None:
            continue
        word = page.status()
        assert word in common.STATUS_CLASSES, f"{name}: {word!r}"
        classes = _classes_in(page)
        assert common.STATUS_CLASSES[word] in classes, (
            f"{name}: status() says {word!r} so the sidebar dot would be "
            f"{common.STATUS_CLASSES[word]!r}, but the panel renders no such "
            f"row. Either the row was not written through the recorder, or the "
            f"recorder recorded a word nothing was drawn for. Classes found: "
            f"{sorted(classes)}")


def _recorder_of(page):
    """The recorder a page's `status` came from, or None if it is a closure.

    A closure is legitimate - `senses.py` computes one from its entries - so this
    returns None for those and the check above skips them rather than failing on
    a different mechanism.
    """
    return getattr(page.status, "__self__", None)


def _classes_in(widget):
    """Every CSS class anywhere under `widget`."""
    out = set()
    if isinstance(widget, Gtk.Widget):
        out.update(widget.get_css_classes())
    child = widget.get_first_child()
    while child is not None:
        out.update(_classes_in(child))
        child = child.get_next_sibling()
    return out


@pytest.mark.parametrize("name", sorted(surfaces.SURFACE_IDS))
def test_a_panel_says_something_even_when_it_shows_an_empty_state(name, built):
    """A panel that renders nothing still answers, and says so honestly.

    Several panels return an empty state at a gate - no calendar backend, no
    paired devices, permission not granted. Those used to return a page with no
    status at all, which is the worst case: the sidebar row had no dot *because
    the panel could not build*, and that is indistinguishable from "nothing is
    wrong".

    So the answer must exist, and where the panel genuinely cannot tell, it must
    be `STATUS_UNKNOWN` rather than a clean `STATUS_OK`. Asserted on the status
    vocabulary rather than on which gate was hit, because which gate is hit
    depends on the machine the tests run on.
    """
    _module, page = built[name]
    answer = page.status()
    assert answer in common.STATUS_CLASSES
    # A panel that rendered an empty state and still claims "Ready" is the one
    # thing this file would most want to be wrong about.
    if _classes_in(page) & set(common.STATUS_CLASSES.values()):
        pass                                        # it rendered a row; fine
    else:
        assert answer == common.STATUS_UNKNOWN, (
            f"{name} rendered no status row at all and answered {answer!r}. A "
            "panel that could not build has to say it could not determine, or "
            "the sidebar shows a healthy dot for a panel that is empty.")


def test_a_recorder_refuses_a_fourth_word_and_starts_unknown():
    """The recorder's own two guards, both cheap and both load-bearing.

    `STATUS_UNKNOWN` as the initial value is the interesting half: a panel whose
    status row has not been written yet has not said it is fine, and defaulting
    the other way would put a green dot on a panel that never reported.
    """
    recorder = common.StatusRecorder()
    assert recorder.status() == common.STATUS_UNKNOWN
    with pytest.raises(ValueError):
        recorder.set("probably fine")
    with pytest.raises(ValueError):
        common.StatusRecorder(initial="probably fine")
    recorder.set(common.STATUS_OK)
    assert recorder.status() == common.STATUS_OK


def test_recording_a_row_records_the_word_it_drew():
    """`row()` is the whole mechanism, so it is the thing that must hold.

    Without it a panel could keep calling `common.status_row` directly and
    `status()` would keep answering `STATUS_UNKNOWN` forever - a sidebar of
    twenty honest amber dots that never noticed anything.
    """
    recorder = common.StatusRecorder()
    assert recorder.status() == common.STATUS_UNKNOWN
    widget = recorder.row(common.STATUS_ATTENTION, "2 of 16 degraded", "the rest answered")
    assert recorder.status() == common.STATUS_ATTENTION
    assert common.STATUS_CLASSES[common.STATUS_ATTENTION] in widget.get_css_classes()


# ---------------------------------------------------------------------------
# the sidebar's icons
# ---------------------------------------------------------------------------


class TestSidebarIcons:
    """Twenty rows, twenty icons.

    Two rows with the same icon read as the same panel: the icon is the fastest
    thing to match when scanning a sidebar, so a collision is not a small
    cosmetic slip - it makes two different questions look like one. Three
    collided before this (`activity`/`conversations` both `view-list`,
    `inventory`/`skills` both `system-run`, `machine`/`model` both `computer`).

    **Every name is checked against the installed theme, not against a list in
    this file.** A missing icon renders as an empty box that looks like a
    rendering bug, and the plan this came from proposed three replacements -
    `cpu-symbolic`, `view-modules-symbolic` and `view-history-symbolic` - for
    which `Gtk.IconTheme.has_icon` returns **False** on this machine. All three
    would have shipped as blank squares.

    The replacements actually used, all confirmed present by the test below:
    `activity` -> `view-continuous` (a log is a continuous strip, and `view-list`
    is three dots over three lines - a bulleted list), `inventory` ->
    `view-app-grid` (a map of functions is a grid of parts, and `system-run`
    belongs to Skills, which lists the things those functions do), and `model` ->
    `media-playback-start` (this panel answers "what is running", while Machine
    keeps `computer` because Machine *is* the physical machine).
    """

    def test_no_two_panels_share_an_icon(self, built):
        by_icon = {}
        for name, (module, _page) in built.items():
            by_icon.setdefault(module.ICON, []).append(name)
        clashes = {icon: names for icon, names in by_icon.items() if len(names) > 1}
        assert not clashes, (
            f"panels sharing an icon: {clashes}. A sidebar row's icon is the "
            "fastest thing to match when scanning, so two of the same make two "
            "different questions look like one.")

    def test_every_icon_exists_in_the_theme(self, built):
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is None:
            pytest.skip("no display, so the icon theme cannot be consulted")
        theme = Gtk.IconTheme.get_for_display(display)

        missing = {
            name: module.ICON
            for name, (module, _page) in built.items()
            if not theme.has_icon(module.ICON)
        }
        assert not missing, (
            f"icons the theme does not have: {missing}. A missing icon name "
            "renders as an empty box, which reads as a rendering bug rather "
            "than as a missing glyph. Check with "
            "`Gtk.IconTheme.has_icon` before using a name.")

    def test_the_conversation_row_does_not_collide_with_a_panel(self, built):
        """The untitled "Conversation" row is a panel row in all but name.

        It sits at the top of the sidebar, above every section, and it is the one
        row a person clicks most. Its icon comes from `sidebar.CHAT_ICON`, and
        nothing checks it against the panels' - so a future panel that reaches
        for `chat-symbolic` would silently produce two identical rows at the
        top of the list.
        """
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk

        from shani_chronoa.gui.sidebar import CHAT_ICON

        display = Gdk.Display.get_default()
        if display is None:
            pytest.skip("no display, so the icon theme cannot be consulted")
        theme = Gtk.IconTheme.get_for_display(display)
        assert theme.has_icon(CHAT_ICON), (
            f"the Conversation row's icon {CHAT_ICON!r} is not in the theme - "
            "it renders as an empty box. Its own docstring records a previous "
            "absence of exactly this kind (`chat-bubble-symbolic`).")
        panel_icons = {module.ICON for module, _page in built.values()}
        assert CHAT_ICON not in panel_icons, (
            f"{CHAT_ICON!r} is both the Conversation row's icon and a panel's, "
            "so the top two rows of the sidebar look identical")