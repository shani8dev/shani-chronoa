"""The Routines surface: the screen that says what a routine actually asks for.

`skills/routines.py` could save one and `routines.expand` could run it, and until
2026-10-11 nothing let a person **see** them - a routine was a phrase you had to
already remember. These tests build the real page and check what a person would
read, which is the half a "the module imports" test cannot say.

Two things they are careful about, both for a reason this file records elsewhere:

- **Titles come from a file.** Routine names are text a person or the `routines`
  skill wrote, so they are escaped - a name carrying Pango markup must render as
  words. `test_a_name_carrying_markup_is_words_not_a_tag`.
- **Run must go through the window's own send path**, because a routine is a
  saved *request* the model plans, not a shortcut that bypasses the consent
  gates. The two directions are both pinned: it submits when it can, and it says
  so when it cannot rather than being a button that does nothing.
"""

from __future__ import annotations

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr", "lib", "shani-chronoa"))

from gi.repository import Adw, GLib, Gtk  # noqa: E402
Adw.init()

from shani_chronoa import routines  # noqa: E402  the store
from shani_chronoa.gui.surfaces import routines as surface_mod  # noqa: E402


def _pump():
    """Run what a `GLib.idle_add` queued, without a main loop.

    `_run` hands the turn to the window on an idle, exactly as
    `application.py`'s `--ask=` path does. Without this the callback is never
    reached and the test asserts that nothing happened, which is not what it
    means to say.
    """
    context = GLib.MainContext.default()
    for _ in range(50):
        if not context.pending():
            break
        context.iteration(False)


def _texts(widget, out=None):
    """Every label in the tree, as strings - what a person would read."""
    if out is None:
        out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        _texts(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _buttons(child, out)
        child = child.get_next_sibling()
    return out


class _Window:
    def __init__(self):
        self.submitted = []

    def submit_text(self, text):
        self.submitted.append(text)


class _App:
    def __init__(self, window=None):
        self.window = window


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


def _build(app=None):
    page = surface_mod.build(app or _App())
    return page, getattr(page, "surface", None)


# --- what the page says -----------------------------------------------------


def test_an_empty_store_says_there_is_nothing_and_what_to_save_first(store):
    routines.save_all({})
    page, _surface = _build()
    shown = "\n".join(t for t in _texts(page) if t)
    assert "No routines yet" in shown, shown
    # The empty case names what to do first. A rectangle with nothing in it reads
    # as a bug, which is what this panel replaced.
    assert "Good morning" in shown, shown


def test_each_routine_says_what_it_asks_for_and_what_triggers_it(store):
    routines.save_all({"good morning": {"request": "weather and my calendar", "phrases": []}})
    page, _surface = _build()
    shown = "\n".join(t for t in _texts(page) if t)
    assert "good morning" in shown
    assert "weather and my calendar" in shown, (
        "a row that shows a phrase and not the request reads as one that works")
    assert "say:" in shown


def _pango_warns(build):
    """True when building a row makes Pango refuse the text as markup.

    `get_title()` returns the source string whether it was parsed or not, so
    comparing labels cannot tell an escaped row from an unescaped one - which is
    how this repo shipped a control that could not fail. The discriminator is
    the render path: `Adw.ActionRow.set_title("a & b")` fails to parse and logs,
    and a bare ampersand is an ordinary character in a name somebody typed.
    """
    import io
    import logging
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        build()
        return "from markup due to error" in buf.getvalue(), buf.getvalue()
    finally:
        root.removeHandler(handler)


def _rendered_markup_parsed(row):
    """Whether a built row's title still contains a live tag.

    A row that parses markup turns `<b>bold</b>` into the *word* bold; a row that
    escapes it shows the tags. Either way `get_title()` hands back the source, so
    the only way to tell them apart is whether a Pango attribute was applied.
    """
    child = row.get_first_child()
    while child is not None:
        label = child
        if isinstance(child, Gtk.Box):
            label = child.get_first_child()
        if isinstance(label, Gtk.Label):
            attrs = label.get_attributes()
            if attrs is not None and attrs.iterator_next():
                return True
        child = child.get_next_sibling()
    return False


def _pango_refused(build) -> "tuple[bool, str]":
    """Whether building a row made Pango reject the text as markup.

    **The discriminator, and the second time a control here could not fail.**
    `get_title()` returns the source string whether it was parsed or not, so
    comparing labels cannot tell an escaped row from an unescaped one. The render
    path is what differs: an unescaped bare `&` makes `Adw.ActionRow` refuse the
    text and log.

    Two earlier attempts at measuring that were vacuous, and both passed with the
    escaping removed, so both are worth recording:

    1. comparing `get_label()` - it hands back the same string either way;
    2. a Python `logging` handler - **GLib does not log through Python.** The
       warning reaches stderr, so fd 2 redirected to a file is what works.
    """
    import os
    import tempfile

    handle, path = tempfile.mkstemp(suffix=".log", prefix="pango-")
    os.close(handle)
    saved = os.dup(2)
    try:
        os.dup2(os.open(path, os.O_WRONLY | os.O_TRUNC), 2)
        build()
    finally:
        sys.stderr.flush()
        os.dup2(saved, 2)
        os.close(saved)
        with open(path, errors="replace") as log:
            data = log.read()
        os.unlink(path)
    return ("from markup due to error" in data), data


def test_common_row_escapes_the_title_it_is_given():
    """`common.row`'s own docstring said it did this. It did not, until measured.

    `Adw.ActionRow`'s title is Pango markup, and a routine called
    "weather & news" - a bare ampersand, an ordinary thing to type - reached it
    unescaped.
    """
    from shani_chronoa.gui.surfaces import common

    warned, seen = _pango_refused(lambda: common.row("weather & news", "x"))
    assert not warned, f"Pango refused the row's title as markup: {seen}"


def test_common_row_escapes_the_subtitle_too():
    """The subtitle is also markup - measured, not assumed.

    `set_subtitle("a & b")` logs the same parse error, so escaping only the title
    would have left half the row. Three surfaces had hand-rolled the escape and
    had it removed here, where it belongs once.
    """
    from shani_chronoa.gui.surfaces import common

    warned, seen = _pango_refused(lambda: common.row("a routine", "weather & news"))
    assert not warned, f"Pango refused the row's subtitle as markup: {seen}"


def test_a_name_with_markup_is_words_not_an_instruction(store):
    """`<b>bold</b>` is the text, not a tag - because `common.row` escapes."""
    from shani_chronoa.gui.surfaces import common

    warned, seen = _pango_refused(lambda: common.row("<b>unclosed", "and <b>too"))
    assert not warned, f"an unclosed tag reached Pango: {seen}"


def test_a_routine_is_on_the_page_with_its_name(store):
    """The panel is not just a store: the routine is listed."""
    routines.save_all({"weather & news": {"request": "forecast", "phrases": []}})
    page, _surface = _build()
    # The row's own text is the *escaped* source - `common.row` escapes and
    # libadwaita parses, so `get_label()` hands back `&amp;`. Matching on the
    # words is what says the routine is listed.
    assert any("weather" in (t or "") and "news" in (t or "") for t in _texts(page)), _texts(page)


def test_a_routine_with_no_request_says_it_has_nothing_saved(store):
    routines.save_all({"placeholder": {"request": "", "phrases": []}})
    page, _surface = _build()
    assert "nothing yet" in "\n".join(t for t in _texts(page) if t)


def test_an_unreadable_store_is_a_sentence_not_a_crash(store, monkeypatch):
    monkeypatch.setattr(surface_mod.routines, "load",
                        lambda: (_ for _ in ()).throw(OSError("disk gone")))
    page, _surface = _build()
    assert "could not be read" in "\n".join(t for t in _texts(page) if t)


# --- Run, which must go through the window's own send path -------------------


def test_run_submits_the_request_as_if_typed(store):
    """The gate-preserving route: the same path `--ask=` and the desktop entry use."""
    routines.save_all({"good morning": {"request": "weather and my calendar", "phrases": []}})
    window = _Window()
    page, surface = _build(_App(window))

    surface._run("weather and my calendar")
    _pump()
    # The request is handed to the window, not run by the panel itself.
    assert window.submitted == ["weather and my calendar"], window.submitted
    # And the row says what happened, so the press is not silent.
    assert "Asked it" in "\n".join(t for t in _texts(page) if t)


def test_run_with_no_window_says_so_rather_than_doing_nothing(store):
    """A dead control is worse than an absent one.

    This panel is also built where there is no window - a render probe, a script -
    and a Run button that silently does nothing is the defect this repo has
    recorded five times.
    """
    routines.save_all({"good morning": {"request": "weather", "phrases": []}})
    page, surface = _build(_App(window=None))
    surface._run("weather")
    assert "could not be run" in "\n".join(t for t in _texts(page) if t)


def test_run_with_nothing_saved_says_that(store):
    page, surface = _build(_App(_Window()))
    surface._run("")
    assert "nothing saved" in "\n".join(t for t in _texts(page) if t)


# --- saving and deleting ----------------------------------------------------


def test_saving_a_routine_puts_it_in_the_store_and_on_the_page(store):
    page, surface = _build()
    surface._save_new("good morning", "weather and my calendar")
    assert routines.load() == {"good morning": {"request": "weather and my calendar"}}
    assert "good morning" in "\n".join(t for t in _texts(page) if t)
    assert "Say it, or open this page" in "\n".join(t for t in _texts(page) if t)


def test_a_routine_needs_both_a_name_and_a_request(store):
    page, surface = _build()
    surface._save_new("", "")
    assert routines.load() == {}
    assert "needs a name and a request" in "\n".join(t for t in _texts(page) if t)


def test_deleting_removes_it_from_the_store_and_the_page(store):
    routines.save_all({"good morning": {"request": "weather", "phrases": []},
                       "wrap up": {"request": "summarise today", "phrases": []}})
    page, surface = _build()
    surface._delete("good morning")
    assert list(routines.load()) == ["wrap up"]
    assert "Deleted" in "\n".join(t for t in _texts(page) if t)


def test_a_broken_store_on_delete_is_a_sentence(store, monkeypatch):
    # Seed first, then break the writer: patching before the seed made the
    # *fixture* raise, which proved nothing about `_delete`.
    routines.save_all({"good morning": {"request": "weather", "phrases": []}})
    monkeypatch.setattr(surface_mod.routines, "save_all",
                        lambda *_a: (_ for _ in ()).throw(OSError("full")))
    page, surface = _build()
    surface._delete("good morning")
    assert "Could not delete" in "\n".join(t for t in _texts(page) if t)


# --- the panel is really wired in -------------------------------------------

def test_it_is_registered_and_its_shape_matches_the_contract():
    from shani_chronoa.gui.surfaces import SURFACE_IDS
    assert "routines" in SURFACE_IDS
    assert len(SURFACE_IDS) == len(set(SURFACE_IDS)), "a duplicate surface id"
    for attr in ("TITLE", "ICON", "SECTION", "build"):
        assert hasattr(surface_mod, attr), attr
    assert callable(surface_mod.build)
    assert surface_mod.SECTION in (
        "Acting", "Sensing", "This machine", "What Chronoa knows",
        "Health and trust", "Conversation"), surface_mod.SECTION


def test_it_re_reads_the_store_on_every_showing(store):
    """The skill saves a routine from a chat turn while the page sits cached."""
    routines.save_all({"good morning": {"request": "weather", "phrases": []}})
    page, surface = _build()
    routines.save_all({})
    # A real `showing` emission is what refreshes it.
    if hasattr(page, "connect"):
        page.emit("showing")
    assert "No routines yet" in "\n".join(t for t in _texts(page) if t)
