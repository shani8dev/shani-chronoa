"""A header button that opens a surface must land on it (2026-10-11)

The Listening button in the mode strip goes through `pages.show("main:listening")`,
the same route `navigate_page` uses. Measured on a real `ChronoaWindow` before
this change:

    show_page("listening")               -> True
    "listening" in window._surface_pages -> **False**   (never built)
    split.get_show_content()             -> False         (chat still up)
    split.get_collapsed()                -> False         (sidebar open)

So the button **returned success and left the person on the conversation** with
the sidebar open and one row highlighted - which is what made it read as *almost*
working rather than broken.

The comment that used to sit on that branch said calling `_show_surface` as well
would "run select twice and push a bare Gtk.Box". **The first half is invented** -
`SidebarPage.select` only adds and removes the `selected` CSS class and pushes
nothing. **The second half turned out to be real**, for a different reason:
`Adw.NavigationView.push` takes an `Adw.NavigationPage`, and `listening.build()`
returns a plain `Gtk.Box`, so it raised

    TypeError: Expected Adw.NavigationPage, but got gi.repository.Gtk.Box

A comment that named the right symptom and the wrong mechanism is why the fix was
deferred: the branch was written to avoid the very call that would have shown the
real problem.
"""


def _steps_harness():
    from test_slash_menu_opens import _in_a_live_window

    return _in_a_live_window


def _split(window):
    return window._split


def test_a_surface_page_really_builds_and_comes_to_the_front(monkeypatch):
    """The end-to-end that was broken: the button claimed success and nothing
    opened."""
    results = _steps_harness()([
        lambda w: w.show_page("listening"),
        lambda w: ("listening" in w._surface_pages),
        lambda w: _split(w).get_show_content(),
        lambda w: _split(w).get_collapsed(),
    ], monkeypatch)

    claimed, built, content, collapsed = results
    assert claimed is True, "show_page should say it handled a registered surface"
    assert built is True, (
        "show_page reported success while the page was never built, so nothing "
        "was ever pushed into the content view")
    assert content is True, (
        "the page was pushed but the chat is still on top of it, so the person "
        "cannot see where they went")
    assert collapsed is True, (
        "the sidebar is still a column, so the panel is beside the chat rather "
        "than filling it")


def test_an_unknown_page_is_still_refused(monkeypatch):
    """The control. A `show_page` that always returns True is what made the bug
    invisible in the first place."""
    results = _steps_harness()([lambda w: w.show_page("no-such-page")], monkeypatch)

    assert results[0] is False, (
        "an id that names nothing was reported as opened, which is the same "
        "false-success shape this whole change is about")


def test_a_surface_that_builds_a_plain_widget_is_wrapped_in_a_page(monkeypatch):
    """`Adw.NavigationView.push` refuses anything that is not an
    `Adw.NavigationPage`, and a surface building a `Gtk.Box` is not wrong on its
    own - only the split view has an opinion, so only the split view's code
    wraps."""
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from shani_chronoa.gui import surfaces as surfaces_mod

    real = surfaces_mod.available_surfaces

    def fake():
        entry = dict(real())
        entry["probe-plain"] = (
            "Probe", "Probe surface", lambda app: Gtk.Box(),
            "Everything else")
        return entry

    # Patched **inside** the step, not before it: the window reads
    # `available_surfaces()` while it builds itself, and a registry that lies
    # about what exists during construction leaves `app.window` as None - which
    # is a broken harness reporting as a broken product.
    def install_then_show(w):
        monkeypatch.setattr(surfaces_mod, "available_surfaces", fake)
        w._show_surface("probe-plain")
        return "called"

    results = _steps_harness()([
        install_then_show,
        lambda w: w._surface_pages.get("probe-plain"),
    ], monkeypatch)

    page = results[-1]
    assert isinstance(page, Adw.NavigationPage), (
        "a surface that builds a plain Gtk.Box reached push() unwrapped, which "
        "raises TypeError: Expected Adw.NavigationPage")


def test_a_surface_that_is_already_a_page_is_pushed_as_it_is(monkeypatch):
    """The over-fix: wrapping everything would bury a page's own title and tag
    in a second, empty shell."""
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from shani_chronoa.gui import surfaces as surfaces_mod

    real = surfaces_mod.available_surfaces
    made = []

    def builder(app):
        page = Adw.NavigationPage(title="Probe page")
        page.set_child(Gtk.Label(label="inside"))
        made.append(page)
        return page

    def fake():
        entry = dict(real())
        entry["probe-page"] = ("Probe", "Probe surface", builder, "Everything else")
        return entry

    def install_then_show(w):
        monkeypatch.setattr(surfaces_mod, "available_surfaces", fake)
        w._show_surface("probe-page")
        return "called"

    results = _steps_harness()([
        install_then_show,
        lambda w: w._surface_pages.get("probe-page"),
    ], monkeypatch)

    assert made, "the builder never ran"
    assert results[-1] is made[0], (
        "a surface that already returns an Adw.NavigationPage was wrapped in a "
        "second one, so its own title and tag are not the page's")


def test_the_wrapper_never_touches_a_broken_page(monkeypatch):
    """A surface whose build raised becomes `_broken_page`, which is already a
    page - and an error state that cannot be pushed is a window that will not
    open."""
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from shani_chronoa.gui import surfaces as surfaces_mod

    real = surfaces_mod.available_surfaces

    def boom(app):
        raise RuntimeError("this surface is broken on purpose")

    def fake():
        entry = dict(real())
        entry["probe-broken"] = ("Probe", "Probe surface", boom, "Everything else")
        return entry

    def install_then_show(w):
        monkeypatch.setattr(surfaces_mod, "available_surfaces", fake)
        w._show_surface("probe-broken")
        return "called"

    results = _steps_harness()([
        install_then_show,
        lambda w: isinstance(w._surface_pages.get("probe-broken"), Adw.NavigationPage),
    ], monkeypatch)

    assert results[-1] is True, (
        "the error state for a surface that failed to build is not a page, so a "
        "build failure takes the window down instead of being shown")

def test_a_page_title_falls_back_to_something_readable(monkeypatch):
    """The wrapper's title comes from the surface's own registry entry, and that
    read can fail. An unreadable registry must not take the surface down - a page
    with a poor title is cosmetic, a page that never opens is not."""
    from shani_chronoa.gui import window as window_mod

    def boom():
        raise RuntimeError("the registry is unreadable on purpose")

    monkeypatch.setattr(window_mod, "available_surfaces", boom, raising=False)

    assert window_mod._surface_title("some_new_surface") == "Some new surface"


def test_a_page_title_comes_from_the_registry_not_a_hand_kept_list(monkeypatch):
    from shani_chronoa.gui import window as window_mod

    # The real shape is `(TITLE, ICON, build)` - read out of
    # `available_surfaces`, not invented here.
    entry = {"listening": ("Listening", "audio-input-microphone-symbolic",
                        lambda app: None)}
    monkeypatch.setattr(window_mod, "available_surfaces", lambda: entry,
                        raising=False)

    assert window_mod._surface_title("listening") == "Listening"
    assert window_mod._surface_title("not_there") == "Not there"
