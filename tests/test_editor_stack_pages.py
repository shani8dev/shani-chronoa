"""The editor's `GtkStack` had one page named twice and another named wrong.

Both were found by driving the real `editor.build()` on a broadway display and
reading GTK's own warnings, and **neither is visible in the source**: the first
is a widget added twice under one name, which GTK answers with a *warning* and an
undefined-behaviour re-parent, and the second is a name nothing looks up.

## What is observable, and what is not — measured on this GTK before writing

Three facts decide the shape of every test below, and all three are counters to
what one would write by default:

| attempt | measured |
|---|---|
| `child.get_name()` for a stack child named `status` | **`"GtkBox"`** — the widget class, not the stack name |
| `stack.child_get_property(child, "name")` | **does not exist** in this PyGObject |
| `stack.add_named(same_widget, same_name)` | **child count 2 → 2**, `get_child_by_name` still resolves |

So **there is no way to enumerate a `GtkStack`'s child names from Python.** The
only reader is `get_child_by_name`, and it returns the one match and is blind to
a second. And a duplicate is invisible to a count, which is why
`test_a_duplicate_does_not_change_the_count` exists: it is the control that
*disqualifies* a count as the witness for the duplicate, which leaves GTK's
stderr as the only one left.

That is why the fd-2 test is not supplementary here. It is the witness.

## The three states a stack can be in, and all three are asserted

| what | how it is asked |
|---|---|
| `status` and `list` are both registered under those names | `get_child_by_name` |
| `open()` reveals the page named `file` | `get_visible_child_name` |
| two opens leave exactly **three** children | counting siblings — the only count that means something here |

The third is the one the other two do not imply. `f"file:{editor.path}"` is a
different name for every file, so a second `open()` adds a fourth child that
nothing can ever reveal again — invisible in a screenshot of one open file, and
unbounded over a session. It *is* visible to the child count, which is the one
place a count is the right instrument.

## The control that could have failed and did

`test_no_gtk_says_anything_about_the_stack` reads names, counts and the visible
name. All three can pass on a build where GTK printed warnings, because a
duplicate changes none of them — measured above. So GTK's own **stderr** is
redirected and searched, which is the only place *"duplicate child name in
GtkStack"* is written. GLib does not log through Python, so this is an **fd 2
redirect** and not `capsys` — the trap this repository has already met once, with
Pango's refusals in `common.row`. The positive control is a genuine duplicate
produced in the same capture, so *"the file was empty"* cannot pass.

The probe deliberately names its page `dup`, which this stack never uses, so
"one warning that is not ours" is asked by **name** rather than by counting
occurrences: GTK emits the duplicate warning under two different sentences and
capitalises them differently (`Duplicate child name in GtkStack: dup` *and*
`While adding page: duplicate child name in GtkStack: dup`, measured), so an
exact-string or exact-count assertion would pin one message of two.
"""

import gc
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))


@pytest.fixture
def on_broadway():
    """Skip honestly when there is no window to build.

    The same rule the rest of the GUI suite uses (`conftest`'s
    `_has_display_backend`), spelled out here because this file builds widgets
    itself rather than through a window.
    """
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "BROADWAY_DISPLAY"):
        if os.environ.get(key, "").strip():
            return
    pytest.skip("no display backend; the editor panel needs a real GtkStack")


def _build_panel():
    """`build(app)`, plus the `_Page` it had to construct along the way.

    **`build()`, not `_Page()`** — the duplicate child name was added in
    `build()` and nowhere else, so constructing the page alone would leave the
    defect this file exists for out of reach. `build()` returns
    `common.surface(...)`'s navigation view rather than the page, so the page is
    recovered by identity from the registry that `build()` had to consult to
    create it.

    A named function rather than fixture body because **the fd-2 test has to
    call it from inside its own capture** — see the note there.
    """
    pytest.importorskip("gi")
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")

    from shani_chronoa.gui.surfaces import editor as ed

    before = {id(p) for p in _pages_in_flight()}
    widget = ed.build(_App())
    fresh = [p for p in _pages_in_flight() if id(p) not in before]
    assert len(fresh) == 1, (
        f"expected build() to construct exactly one page, got {len(fresh)}")
    return ed, fresh[0], widget


@pytest.fixture
def panel(on_broadway):
    """The real `_Page` behind `editor.build(app)`, and the widget that carries it."""
    return _build_panel()


def _pages_in_flight():
    """Every `_Page` any surface has constructed, newest last.

    A private class and a private constructor, so the route is construction-time
    interception rather than reading a private attribute off a widget the module
    does not publish.
    """
    from shani_chronoa.gui.surfaces.editor import _Page

    seen = getattr(_pages_in_flight, "seen", None)
    if seen is None:
        seen = []
        original = _Page.__init__

        def recording(self):
            original(self)
            seen.append(self)

        _Page.__init__ = recording
        _pages_in_flight.seen = seen
    return seen


class _Config:
    def get(self, key, default=None):
        return default

    def get_bool(self, key, default=False):
        return default


class _App:
    """Whatever `editor.build` reads off the app.

    The defect is in **construction**, so the fixture has to build the real
    panel; the stub is only here so the build does not stop on a settings read.
    """

    config = _Config()


def _children(stack):
    """The stack's children, in order.

    The **only** structural reader GTK4 offers from Python — see the module
    docstring's table for the two things that were tried first and measured
    against the wrong question.
    """
    out = []
    child = stack.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


class TestTheStackHasOnePageOfEachKind:

    def test_both_pages_are_findable_by_the_name_that_reveals_them(self, panel):
        """Asked by `get_child_by_name`, the only reader of a name there is.

        Asserting `get_name()` would be asserting the widget class, and
        `child_get_property(child, "name")` raises `AttributeError` on this
        PyGObject — so this is the strongest form of the claim available.
        """
        ed, page, _widget = panel
        assert page.stack.get_child_by_name("status") is page.status, (
            "build() puts the status page up under 'status', and _show_status() "
            "is what opens with it")
        assert page.stack.get_child_by_name("list") is not None, (
            f"show_list() did not run: {len(_children(page.stack))} children")

    def test_two_pages_and_no_third(self, panel):
        """**The half of the duplicate claim a count can carry**, and only that.

        `status` + `list`. A third child would be a page nothing names, and the
        paired test below is the reason this is not the witness for the duplicate
        itself.
        """
        ed, page, _widget = panel
        assert len(_children(page.stack)) == 2, (
            "status + list, and nothing else. The duplicate add_named() that "
            f"used to be here re-parented rather than appended, so this count "
            f"is the same with it: {len(_children(page.stack))} children")

    def test_a_duplicate_does_not_change_the_count(self, on_broadway):
        """**The control that disqualifies the count as a witness.**

        A real duplicate, on a real stack, in the same file — and the count does
        not move. Without this, `test_two_pages_and_no_third` reads as coverage of
        the duplicate `build()` used to contain, and it is not: a green count
        here proves nothing about it, which is why the warning on fd 2 is the
        witness and this file treats that test as primary.
        """
        from gi.repository import Gtk

        stack = Gtk.Stack()
        widget = Gtk.Box()
        stack.add_named(widget, "status")
        assert len(_children(stack)) == 1

        stack.add_named(widget, "status")  # the shipped defect, verbatim

        assert len(_children(stack)) == 1, (
            "a second add_named under a name that is taken warns and "
            f"re-parents; it does not append, so the count is still "
            f"{len(_children(stack))}")
        assert stack.get_child_by_name("status") is widget, (
            "and get_child_by_name still resolves, so it is blind to the "
            "duplicate too - which is why neither reader can see it and only "
            "GTK's stderr can")

    def test_opening_a_file_reveals_the_file_page(self, panel, tmp_path):
        ed, page, _widget = panel
        assert page.stack.get_visible_child_name() == "list"

        path = tmp_path / "a.txt"
        path.write_text("hello\n")
        page.open(path)

        assert page.stack.get_visible_child_name() == "file", (
            "open() reveals the name 'file'; a page named f'file:{path}' is "
            "never revealed, so the status page stays on screen and GTK says "
            "\"Child name 'file' not found in GtkStack\". Visible here: "
            f"{page.stack.get_visible_child_name()!r}")

    def test_two_opens_leave_three_children(self, panel, tmp_path):
        """The one the other two do not imply.

        A per-file name is a second key to one page: nothing removes the
        previous one, so the stack grows by a child per open and every one of
        them is unreachable afterwards. **A page nobody can scroll to is
        invisible**, so a screenshot of the first open looks correct.

        This is the one claim a **count** is the right instrument for — unlike the
        duplicate above, which it cannot see at all.
        """
        ed, page, _widget = panel

        (tmp_path / "one.txt").write_text("one\n")
        page.open(tmp_path / "one.txt")
        assert len(_children(page.stack)) == 3, (
            f"after one open: {len(_children(page.stack))} children")
        assert page.stack.get_child_by_name("file") is not None

        (tmp_path / "two.txt").write_text("two\n")
        page.open(tmp_path / "two.txt")
        assert len(_children(page.stack)) == 3, (
            "a second open added a page nobody can reveal: "
            f"{len(_children(page.stack))} children")
        assert page.stack.get_visible_child_name() == "file"

    def test_an_unreadable_file_still_reaches_the_status_page(self, panel,
                                                             tmp_path):
        """**The over-fix this cannot be allowed to become.**

        "Drop the old page first" is the obvious repair for the accumulating
        names, and a version of it that hides before it knows the new page can be
        shown would strand a person who picked a file that cannot be read on an
        empty stack. Both branches of `open()`'s refusal are driven, because
        the first version of a guard like this usually only gets one.
        """
        ed, page, _widget = panel

        gone = tmp_path / "gone.txt"
        gone.write_text("x\n")
        gone.unlink()
        page.open(gone)
        assert page.stack.get_visible_child_name() == "status"
        assert page.stack.get_child_by_name("file") is None, (
            "a refused open must not leave a file page behind")

        junk = tmp_path / "junk.gguf"
        junk.write_bytes(b"\x00\x01\x02not readable as text\xff\xfe")
        page.open(junk)
        assert page.stack.get_visible_child_name() == "status"
        assert page.stack.get_child_by_name("file") is None


def test_the_over_fix_guard_is_not_claimed_as_covered(on_broadway):
    """**One line that could not fail, and is therefore not written.**

    `_drop_file_page()` hides the `"file"` page before removing it, and its
    docstring used to justify that by saying a `GtkStack` refuses to remove
    its visible child. Measured on this GTK (4.22): the remove **succeeds**
    and leaves `get_visible_child_name()` answering `None` - so the stated
    reason is false, the hide is not what makes the removal happen, and
    deleting it leaves every test in this file green.

    What the hide actually buys is a frame with nothing on screen, which
    exists only between two statements inside that method. There is no
    instrument for a transient state, so asserting it would be a test that
    passes for no reason that matters - the shape this repository records
    most often. So the guard is kept as **documented defence**, not claimed as
    covered.

    **What this can and cannot check, stated rather than glossed.** It pins the
    *measured* claim being written down. It deliberately does **not** assert the
    stale sentence is absent: the replacement docstring **quotes** it in order to
    record what it replaced, so an absence check here matches the fix's own
    quotation - the version of this test that did exactly that is recorded in the
    line above as the mistake it was. Pinning prose is weak; pinning the
    *measurement* is the part that carries information.
    """
    from shani_chronoa.gui.surfaces import editor as ed

    doc = ed._Page._drop_file_page.__doc__ or ""
    assert "no visible child" in doc, (
        "the measured reason - the remove succeeds and leaves the stack with "
        "nothing visible - should be what the docstring says")


class TestGtkSaidNothing:

    def test_no_gtk_says_anything_about_the_stack(self, on_broadway, tmp_path,
                                                   capfd):
        """**The witness for the duplicate, and the reason the file exists.**

        None of the tests above can see a *warning*. A duplicate child name
        prints it and carries on; a wrong name prints `Child name 'file' not
        found in GtkStack` and carries on — and, measured, a duplicate does not
        even change the child count. So a suite of the behavioural checks alone
        is green on the build that shipped both defects.

        **The panel is built here, inside the capture, and not through the
        `panel` fixture.** `capfd` begins capturing when *its own* fixture is set
        up, and pytest sets up same-scope fixtures in signature order — so a
        `panel` listed before `capfd` has already built the widget, and the
        warning it produced is not in the capture. Measured: with the duplicate
        restored, `build()` emitted three messages and the capture contained
        only the control's — **the witness was blind by construction**, and the
        suite stayed green against the shipped defect.

        Captured on **fd 2**, because that is where GLib writes and Python's
        `capsys` does not see it. The control is a genuine duplicate produced in
        the same capture, so *"the file was empty"* cannot pass.
        """
        ed, page, _widget = _build_panel()
        (tmp_path / "one.txt").write_text("one\n")
        page.open(tmp_path / "one.txt")
        (tmp_path / "two.txt").write_text("two\n")
        page.open(tmp_path / "two.txt")

        # **The control**: a real duplicate, right here, in the same capture, on
        # a page named `dup` - a name this stack never uses.
        _make_duplicate()

        err = capfd.readouterr().err
        low = err.lower()
        assert "duplicate child name" in low, (
            "the control did not fire, so an empty capture below proves "
            f"nothing. Captured stderr was: {err[:400]!r}")

        # Case-insensitively, because GTK says it two ways: measured on this box
        # as both `Duplicate child name in GtkStack: dup` and `While adding
        # page: duplicate child name in GtkStack: dup`.
        assert "duplicate child name in gtkstack: status" not in low, (
            "build() added the status page a second time. Captured stderr "
            f"was: {err[:400]!r}")
        assert "not found in gtkstack" not in low, (
            "open() revealed a name no child carries, so the status page "
            f"stayed on screen. Captured stderr was: {err[:400]!r}")


def _make_duplicate():
    """One genuine duplicate, on a throwaway stack.

    The stack is dropped and collected so the warning is emitted before the
    capture is read — GTK writes it synchronously from `add_named`, and the
    `gc.collect()` is here so the second child is really released rather than
    merely unreferenced.
    """
    from gi.repository import Gtk

    probe = Gtk.Stack()
    probe.add_named(Gtk.Label(), "dup")
    probe.add_named(Gtk.Label(), "dup")
    del probe
    gc.collect()