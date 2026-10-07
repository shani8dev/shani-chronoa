"""Every surface must at least *build*, and the ones nobody was opening must render.

Both files here were complete-looking, unit-testable in isolation, and **100%
unreachable at runtime**. Each raised before drawing a single pixel:

- `diff.py` - six GTK3-era defects. `get_child()` on a `ScrolledWindow` returns
  the auto-inserted `Gtk.Viewport` (which has no `remove` in GTK4),
  `Gtk.ReliefStyle` and `set_relief()` were removed, `button-release-event` is a
  GTK3 signal, `Gtk.Label(markup=...)` is a GTK3 constructor property,
  `get_children()`/`get_child(0)` were removed, and `<span class=...>` is not
  valid Pango markup.
- `artifact_store.py` - `@dataclass` with no `from dataclasses import ...`, so
  the module did not import, and an `ArtifactCategory` enum that was used and
  never defined.

The reason none of this was caught is the reason this file exists: the layout
contract *did* have these surfaces registered, and it errored on all of them
(67 errors) without anybody reading the output - a safety net that was down and
looked like a passing suite. Constructing the widget is what finds this class of
bug, and `py_compile` cannot, because every one of these lines is valid Python.
"""

import pathlib

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.gui import surfaces  # noqa: E402


class _App:
    """The stand-in the layout contract builds surfaces with."""

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


def test_the_two_unreachable_surfaces_import():
    """`artifact_store` did not import at all, which no unit test caught."""
    from shani_chronoa.gui.surfaces import artifact_store, diff

    assert artifact_store.Artifact is not None
    assert diff._DiffView is not None


def test_the_category_enum_the_filter_iterates_exists():
    """It was used at `_CategoryFilter` and defined nowhere."""
    from shani_chronoa.gui.surfaces.artifact_store import ArtifactCategory

    values = [c.value for c in ArtifactCategory]
    assert values == ["all", "model", "voice", "document", "export"], values
    # A `str` subclass, so a member *is* the string `_artifact_category` returns.
    assert ArtifactCategory.MODEL == "model"
    assert ArtifactCategory("all") is ArtifactCategory.ALL


def test_the_diff_view_renders_files_and_hunks():
    """Six defects, all of them on the path from construction to pixels."""
    from types import SimpleNamespace

    from shani_chronoa.gui.surfaces import diff as diffmod

    view = diffmod._DiffView(SimpleNamespace(config=ChronoaConfig(), window=None))
    view.set_diffs({
        pathlib.Path("/tmp/a.py"): ("one\ntwo\n", "one\ntwo changed\n"),
        pathlib.Path("/tmp/b.py"): ("x\n", "x\ny\n"),
    })

    nodes = _walk(view)
    files = [n for n in nodes if isinstance(n, diffmod._DiffFileItem)]
    hunks = [n for n in nodes if isinstance(n, diffmod._DiffHunkView)]
    assert len(files) == 2, "the file list did not populate"
    assert hunks, "no diff hunks rendered"


def test_the_diff_file_list_is_mutated_through_the_box_not_the_scroller():
    """The specific bug: reaching back through the scroller returns a Viewport."""
    from types import SimpleNamespace

    from shani_chronoa.gui.surfaces import diff as diffmod

    view = diffmod._DiffView(SimpleNamespace(config=ChronoaConfig(), window=None))
    assert view._files_box is not None, "the box it mutates is not kept"
    # This is what `_render_files` used to do, and what raised.
    assert view._files_scrolled.get_child() is not view._files_box, (
        "if this is ever the same object again the Viewport bug is back")


def test_the_diff_row_click_still_selects():
    from types import SimpleNamespace

    from shani_chronoa.gui.surfaces import diff as diffmod

    view = diffmod._DiffView(SimpleNamespace(config=ChronoaConfig(), window=None))
    view.set_diffs({pathlib.Path("/tmp/a.py"): ("one\n", "one\ntwo\n")})
    item = next(n for n in _walk(view) if isinstance(n, diffmod._DiffFileItem))

    picked = []
    item.on_select = lambda f: picked.append(f.path)
    item._on_click(None, 1, 0.0, 0.0)

    assert picked == [pathlib.Path("/tmp/a.py")], "the click gesture is inert"


def test_every_registered_surface_builds():
    """The check that was erroring 67 times and being read as a pass.

    Asserted as *building*, not as building correctly - the layout contract owns
    correctness. This only has to catch a surface that raises on construction,
    which is what both of these did.
    """
    built = []
    for name in sorted(surfaces.SURFACE_IDS):
        module = __import__(f"shani_chronoa.gui.surfaces.{name}", fromlist=["build"])
        widget = module.build(_App())
        assert widget is not None, name
        built.append(name)
    assert "diff" in built and "artifact_store" in built, (
        f"the two fixed surfaces are not registered: {built}")

# --- the wizard's action row must not scroll -------------------------------
#
# Measured before the fix, against the 620px window: five of the sixteen pages
# had more content than that (`languages` 1137px, `review` 1029px, `voice`
# 897px, `welcome` 849px, `cloud-keys` 672px), and the buttons were the last
# children of the *scrolling* box. So on the first screen a new person sees,
# "Start" was 849px down and out of sight - and on the eleven short pages it
# looked perfectly fine, which is why nobody noticed.

_WIZARD_NAV = ("Next", "Start", "Close", "Back to the list", "Save these keys",
               "Start using Chronoa", "Listen to this voice")


@pytest.fixture(scope="module")
def wizard():
    from shani_chronoa import setup_wizard

    return setup_wizard.build_window(
        Gtk.Application(application_id="test.wizard.footer"))


def _wizard_pages(wizard):
    return {p.get_tag(): p for p in _walk(wizard) if isinstance(p, Adw.NavigationPage)}


def test_the_wizard_builds_every_page(wizard):
    pages = _wizard_pages(wizard)
    for tag in ("welcome", "mode", "cloud-keys", "brain", "model-picker", "ears",
                "voice", "review", "eyes", "imagine", "memory", "photos",
                "sounds", "speakers", "languages", "done"):
        assert tag in pages, f"the wizard no longer builds the {tag!r} page"


def test_no_wizard_action_button_scrolls_out_of_reach(wizard):
    """The defect: a page's own forward action living inside its scroller."""
    offenders = {}
    for tag, page in sorted(_wizard_pages(wizard).items()):
        scrollers = [n for n in _walk(page) if isinstance(n, Gtk.ScrolledWindow)]
        if not scrollers:
            continue
        scrolling = [
            b.get_label() for b in _walk(scrollers[0])
            if isinstance(b, Gtk.Button) and b.get_label()
            and b.get_label().startswith(_WIZARD_NAV)
        ]
        if scrolling:
            offenders[tag] = scrolling
    assert not offenders, (
        f"page actions inside the scroller, so they scroll away: {offenders}")


def test_the_welcome_pages_start_button_is_reachable(wizard):
    """The first screen, and the one that was worst: 849px of content."""
    page = _wizard_pages(wizard)["welcome"]
    scrollers = [n for n in _walk(page) if isinstance(n, Gtk.ScrolledWindow)]
    assert scrollers, "the welcome page has no scroller to be pinned under"
    inside = {id(b) for b in _walk(scrollers[0]) if isinstance(b, Gtk.Button)}
    starts = [b for b in _walk(page)
              if isinstance(b, Gtk.Button) and b.get_label() == "Start"]
    assert starts, "the welcome page has no Start button at all"
    assert all(id(b) not in inside for b in starts), (
        "Start is inside the scroller, so it is below the fold on a 620px window")


def test_the_footer_sits_below_the_scroller_not_above_it(wizard):
    """Order is the point: pinned above the content would be a different design,
    and a descendant-only test would not notice the difference."""
    page = _wizard_pages(wizard)["voice"]
    outer = next(n for n in _walk(page)
                 if isinstance(n, Gtk.Box)
                 and any(isinstance(c, Gtk.ScrolledWindow) for c in _walk(n))
                 and any(isinstance(c, Gtk.Button) for c in _walk(n)))
    kinds = []
    child = outer.get_first_child()
    while child is not None:
        kinds.append(type(child).__name__)
        child = child.get_next_sibling()
    assert "ScrolledWindow" in kinds and "Box" in kinds, kinds
    assert kinds.index("ScrolledWindow") < len(kinds) - 1, (
        f"the action row is not the last child: {kinds}")
