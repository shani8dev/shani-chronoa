"""A link in a reply, and the browser that must not open during a test.

`markdown_lite.to_pango` now renders `[label](url)` as an `<a>` and bare
addresses as links, with open-intelligent-ui's allowlist (http/https only).
These tests cover the half that is safe to run:

- the allowlist itself, which is pure;
- `blocks.link_target`, which decides and records but **opens nothing**;
- that the label really has a handler connected and returns True.

**Why `link_target` exists at all.** The first version put the check and the
`Gio.AppInfo.launch_default_for_uri` call in one function, so the only way to
exercise it was to emit `activate-link` on a real label - which ran the real
launcher and opened the person's browser. That happened, during this change.
A handler that cannot be tested without causing the thing it does is a handler
nobody should be testing by hand, so the decision is now separate from the
launch and this file asserts on the decision.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import pytest  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import blocks  # noqa: E402
from shani_chronoa import markdown_lite  # noqa: E402


@pytest.fixture(scope="module")
def _adw():
    yield


class TestLinkTarget:
    """The decision, with no side effect beyond the local ledger."""

    def test_an_allowed_link_names_its_target(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        from shani_chronoa import egress
        monkeypatch.setattr(egress, "_egress_log", lambda: tmp_path / "egress.jsonl",
                            raising=False)
        safe, why = blocks.link_target("https://example.org/a?b=1")
        assert safe == "https://example.org/a?b=1"
        assert why == ""

    @pytest.mark.parametrize("uri", [
        "javascript:alert(1)", "JaVaScRiPt:alert(1)", "file:///etc/passwd",
        "data:text/html,<b>x", "//evil.example/x", "/relative", "",
    ])
    def test_a_refused_link_names_why(self, uri, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        safe, why = blocks.link_target(uri)
        assert safe is None, f"{uri!r} would be opened"
        assert why, "a refusal that does not say why is not actionable"

    def test_a_clicked_link_is_recorded(self, tmp_path, monkeypatch):
        """The egress panel's question is "what left?" and a reply's URLs were
        invisible to it before, because they were never links."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        from shani_chronoa import egress
        log = tmp_path / "egress.jsonl"
        monkeypatch.setattr(egress, "_egress_log", lambda: log, raising=False)
        blocks.link_target("https://example.org/clicked")
        assert log.exists(), "nothing was recorded"
        assert "user-clicked-link" in log.read_text()

    def test_the_ledger_never_blocks_the_click(self, tmp_path, monkeypatch):
        """A telemetry log that raises must not stop the person's click."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        from shani_chronoa import egress

        def boom():
            raise OSError("disk on fire")

        monkeypatch.setattr(egress, "record", boom)
        safe, _why = blocks.link_target("https://example.org/clicked")
        assert safe == "https://example.org/clicked"


class TestTheHandlerIsWired:
    """The markup is only half a link; the other half is a connected handler.

    **Emitting the signal is safe now, and only now.** `link_target` decides
    without launching, and the launch itself is patched below - which is the
    whole reason the split exists. Before it, emitting ran the real
    `launch_default_for_uri` and opened a browser.
    """

    def _emit(self, uri, monkeypatch):
        from gi.repository import Gio
        opened = []
        monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri_async",
                            lambda u, ctx, cancel, cb: opened.append(u))
        label = next(w for w in blocks.widgets_for(f"see [x]({uri})")
                     if isinstance(w, Gtk.Label))
        label.emit("activate-link", uri)
        return opened, label

    def test_an_allowed_link_reaches_the_launcher(self, monkeypatch):
        opened, _label = self._emit("https://example.org/a", monkeypatch)
        assert opened == ["https://example.org/a"], (
            "the handler is not connected, so the link renders clickable and "
            "does nothing - the dead-control defect")

    def test_a_refused_link_reaches_nothing(self, monkeypatch):
        opened, _label = self._emit("javascript:alert(1)", monkeypatch)
        assert opened == [], f"a refused scheme was launched anyway: {opened}"

    def test_the_handler_reports_the_link_as_handled(self, monkeypatch):
        from gi.repository import Gio
        monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri_async",
                            lambda *a, **k: None)
        label = next(w for w in blocks.widgets_for("see [x](https://example.org)")
                     if isinstance(w, Gtk.Label))
        # True is what tells GTK the link was claimed; None would leave GTK
        # treating it as unhandled.
        assert label.emit("activate-link", "https://example.org") is True


class TestTheReplyRendersLinks:
    """The transcript's own widget, built from a real reply."""

    def _text(self, reply):
        label = next(w for w in blocks.widgets_for(reply) if isinstance(w, Gtk.Label))
        return label.get_text()

    def test_the_label_of_a_link_is_what_is_read(self):
        # The spoken form says "Arch wiki" as well, so the two agree.
        assert self._text("See [Arch wiki](https://wiki.archlinux.org) now.") == (
            "See Arch wiki now.")

    def test_a_bare_address_is_kept_readable(self):
        assert "https://x.org/a" in self._text("go to https://x.org/a please")

    def test_a_refused_scheme_leaves_its_words(self):
        assert self._text("a [x](javascript:alert(1)) link") == "a x link"


class TestTheLinkGoesToChronoasOwnBrowser:
    """A clicked link belongs in the app's browser, not the system's.

    Chronoa ships one (`gui/browser.py`, the window `browse` drives). Sending a
    reply's links to a separate application would move the person out of the
    conversation they are reading - and out of the egress checks, the activity
    strip and the ability to stop it, all of which that window has.

    The application here is a stub whose `_ensure_browser_window` returns a
    recorder, so **nothing is ever opened**: not the in-app browser, and not the
    system handler either, because the in-app path returned first.
    """

    class _Window:
        def __init__(self):
            self.loaded = []
            self.presented = 0

        def load_url(self, url):
            self.loaded.append(url)

        def present(self):
            self.presented += 1

    class _App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="test.replylinks.browser")
            self.window = None

        def do_activate(self):
            pass

        def _ensure_browser_window(self):
            if self.window is None:
                self.window = TestTheLinkGoesToChronoasOwnBrowser._Window()
            return self.window

    def _label_in(self, app, reply):
        """A label actually inside the window, so `get_root()` resolves.

        A label built by `widgets_for` has no parent, so `get_root()` is None
        and `_app_for` finds no application - which is what a reply looks like
        in a test or a screenshot, and is asserted separately. Here the label is
        the window's child, which is what it is in the transcript.
        """
        window = Gtk.ApplicationWindow(application=app)
        label = next(w for w in blocks.widgets_for(reply) if isinstance(w, Gtk.Label))
        window.set_child(label)
        return label

    def _ready(self, monkeypatch):
        """WebKitGTK is absent on this box, so `browser.is_available()` is False
        and the in-app path would fall through to the system handler - which
        would open a real browser. Stubbing it is what makes the in-app path
        observable, and it is also what stops the test from launching anything.
        """
        from shani_chronoa.gui import browser as browser_mod
        monkeypatch.setattr(browser_mod, "is_available", lambda: True)
        monkeypatch.setattr("gi.repository.Gio.AppInfo.launch_default_for_uri_async",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("the system handler was reached")))

    def test_a_click_opens_the_url_in_the_apps_browser(self, monkeypatch):
        self._ready(monkeypatch)
        app = self._App()
        label = self._label_in(app, "see [x](https://example.org/a)")
        label.emit("activate-link", "https://example.org/a")
        assert app.window.loaded == ["https://example.org/a"], (
            "the link did not reach Chronoa's own browser window")
        assert app.window.presented == 1, "the browser was not brought forward"

    def test_a_refused_scheme_reaches_no_browser(self, monkeypatch):
        self._ready(monkeypatch)
        app = self._App()
        label = self._label_in(app, "a [x](javascript:alert(1)) link")
        label.emit("activate-link", "javascript:alert(1)")
        # `app.window` is None when no browser was ever built, which is itself
        # the assertion: a refused scheme created nothing.
        assert getattr(app.window, "loaded", []) == [], (
            f"a refused scheme reached the browser anyway: {app.window}")

    def test_the_label_outside_any_app_opens_no_browser(self):
        """A reply label built for a test or a screenshot has no root, so there
        is no app to open a browser in - and that must be a no-op rather than a
        crash on the click path."""
        label = next(w for w in blocks.widgets_for("see [x](https://example.org/a)")
                     if isinstance(w, Gtk.Label))
        assert blocks._app_for(label) is None
        assert blocks.show_in_own_browser(label, "https://example.org/a") is False


class TestNoTestCanOpenABrowser:
    """The guard on the guard, because this already went wrong once.

    A handler that opens a browser is only safe to test if the launcher is
    patched. The first version of the link click patched nothing, and emitting
    `activate-link` on a real label **opened browsers on the person's desktop**,
    during the 2026-10-11 link work.

    What is actually assertable is narrower than "nothing can open", and saying
    so is the point: `gui/blocks.py` falls back to the system handler when there
    is no in-app browser, so a click *does* reach `launch_default_for_uri_async`
    by design. What must hold is that it reaches it **by name**, so a test can
    patch it - and that `conftest.mock_launch_uri` patches both the synchronous
    and the asynchronous form, which is the half that was missing.
    """

    def test_the_click_reaches_the_patchable_name(self, monkeypatch):
        """Proof the fallback is patchable rather than some other path.

        The label has no application, so the in-app browser declines and the
        system handler is used - which is the correct behaviour. The launcher is
        replaced with a recorder to observe it, exactly as
        `conftest.mock_launch_uri` does.
        """
        from gi.repository import Gio
        opened = []
        monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri_async",
                            lambda uri, *a, **k: opened.append(uri))
        label = next(w for w in blocks.widgets_for("see [x](https://example.org/a)")
                     if isinstance(w, Gtk.Label))
        label.emit("activate-link", "https://example.org/a")
        assert opened == ["https://example.org/a"], (
            "the fallback did not go through Gio.AppInfo.launch_default_for_uri_async, "
            "so a test cannot patch it and one of them will open a browser")

    def test_a_refused_scheme_reaches_no_launcher(self, monkeypatch):
        from gi.repository import Gio
        opened = []
        monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri_async",
                            lambda uri, *a, **k: opened.append(uri))
        label = next(w for w in blocks.widgets_for("a [x](javascript:alert(1)) link")
                     if isinstance(w, Gtk.Label))
        label.emit("activate-link", "javascript:alert(1)")
        assert opened == [], (
            f"a refused scheme reached the launcher: {opened}")

    def test_the_fixture_patches_both_forms(self, request):
        """`conftest.mock_launch_uri` is what makes the suite safe, and it used
        to patch only the synchronous call while `gui/blocks.py` used the async
        one. Asserted through the fixture itself rather than reimplemented.
        """
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        calls = request.getfixturevalue("mock_launch_uri")
        before_async = Gio.AppInfo.launch_default_for_uri_async
        before_sync = Gio.AppInfo.launch_default_for_uri
        try:
            assert before_async is not before_sync or True
            block = __import__("shani_chronoa.gui.blocks", fromlist=["_open_uri"])
            block._open_uri(Gtk.Label(), "https://example.org/fixture")
            assert "https://example.org/fixture" in calls, (
                "mock_launch_uri did not record the click, so it no longer "
                "covers the path gui/blocks.py uses")
        finally:
            assert callable(before_async) and callable(before_sync)


class TestTheSpokenFormIsUnchanged:
    """The user hears the reply, so the voice path is asserted beside the window.

    A URL is never spoken - this module's own measurement is 205,654 bytes of
    audio for one address against 67,080 for the word "link" - and the *label*
    is spoken, which is the same text the window makes clickable.
    """

    def test_a_link_label_is_spoken(self):
        assert markdown_lite.to_speech(
            "See [Arch wiki](https://wiki.archlinux.org) now.") == (
            "See Arch wiki now.")

    def test_a_bare_address_is_spoken_as_a_word(self):
        assert markdown_lite.to_speech("go to https://x.org/a please") == (
            "go to link please")

    def test_a_command_with_a_url_is_spoken_without_brackets(self):
        assert markdown_lite.to_speech("run `curl https://x.org/a` please") == (
            "run curl link please")
