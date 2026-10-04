"""The real `BrowserWindow`, constructed and driven in a child process.

`test_browser.py` covers everything about this module that does not need a
display. This file covers the part that does, because that is the part that only
fails when it is actually built: a GTK4-removed API, a signal name that does not
exist, a widget that refuses its child. All three shipped in this repo's history
and none of them was visible to a passing suite.

A child process, not this one: GTK holds a display connection and WebKit spawns a
web process, neither of which can be set up and torn down per test inside a
single interpreter (`test_adw_initialisation.py` says the same about libadwaita).

**The load test carries a control that can fail.** Constructing a
`WebKit.WebView` and loading a page needs WebKit's web process, which on a
machine without unprivileged user namespaces cannot start at all - measured on
this machine, 2026-10-02: with the sandbox on, the child dies with
`bwrap: setting up uid map: Permission denied`. So that test first runs a *bare*
`WebKit.WebView` through the same load in the same child. If the bare view fails,
the environment is at fault and the test skips, saying exactly that. If the bare
view works and the window does not, that is a real failure in this module - which
is the only reason the skip is not a way of hiding one.
"""

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: `home_url=""` builds the whole tree without asking WebKit for a web process,
#: which is what makes the first harness runnable on a machine that cannot start
#: one at all. That is the most of this module that can be proven without a
#: network, a display *and* a working sandbox.
_BUILD_TREE = textwrap.dedent(
    """
    import json
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gtk, GLib

    from shani_chronoa import egress
    from shani_chronoa.gui import browser

    out = {"available": browser.is_available(), "webkit": browser.webkit_version()}

    def on_activate(app):
        window = browser.BrowserWindow(app, on_attach=lambda *a: None, home_url="")
        out["constructed"] = True
        out["title"] = window.get_title()
        out["is_webview"] = type(window.webview()).__module__.endswith("WebKit")
        out["nav_sensitive"] = [window._back.get_sensitive(),
                                window._forward.get_sensitive()]
        out["current_url"] = window.current_url()
        out["spinner_visible"] = window._spinner.get_visible()
        out["spinner_running"] = window._spinner.get_spinning()

        labels = []
        child = window._menu_button.get_popover().get_child().get_first_child()
        while child is not None:
            labels.append(child.get_child().get_last_child().get_text())
            child = child.get_next_sibling()
        out["menu_labels"] = labels

        # The address decision, through the window's own entry and endpoint.
        out["decisions"] = {}
        for typed in ("https://example.com/a?b=1", "example.com", "two words",
                      "javascript:alert(1)", "3.1"):
            window._entry.set_text(typed)
            out["decisions"][typed] = browser.target_url(typed, window._search_url)
            window._entry.set_text("")

        # Markup escaping on the one line that is fed remote content.
        window._set_status("Attached https://example.com/?a=1&b=2 <not a tag>")
        out["status_text"] = window._status.get_text()
        out["status_label"] = window._status.get_label()

        # The refusal path with no listener attached.
        no_listener = browser.BrowserWindow(app, on_attach=None, home_url="")
        no_listener.attach()
        out["no_listener_status"] = no_listener._status.get_text()
        no_listener.close()

        # The recording hook, driven directly: a navigation is egress, and this
        # needs neither a page load nor a network to prove the log gets a line.
        window._record_navigation("https://example.com/x")
        out["egress"] = egress.read_events()
        # A file:// page fetched nothing off this machine, and recording it
        # produced a real false alarm (measured, before the scheme check above):
        # `egress.record` reads the host as empty, decides the page is remote and
        # logs a violation with no host in it.
        window._record_navigation("file:///tmp/some-local-page.html")
        out["egress_after_file_url"] = egress.read_events()
        out["egress_alarms"] = [
            event for event in out["egress"] if event["violation"] and not event["host"]
        ]

        # close-request terminates the web process rather than leaking it.
        window.close()
        app.quit()

    app = Gtk.Application(application_id="org.shani.chronoa.browserprobe")
    app.connect("activate", on_activate)
    GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)

#: A bare WebView through the same load, then the real window doing the same
#: thing. The bare view is the control: if it cannot load either, the machine
#: cannot start a web process and nothing in this module is on trial.
_LOAD_AND_ATTACH = textwrap.dedent(
    """
    import json
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gtk, GLib, WebKit

    from shani_chronoa import egress
    from shani_chronoa.gui import browser

    PAGE = (b"<html><head><title>Probe</title></head><body>"
            b"<h1>heading</h1><p>first line of the page</p><p>second line</p>"
            b"</body></html>")
    # A page that takes its time, so the loading indicator can be looked at while
    # the load is genuinely in flight. On a local file the load finishes inside
    # the first frame, which is how a spinner that is wired to nothing would pass
    # a check made after the load.
    DELAY = 2.5

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(DELAY)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/probe.html" % server.server_address[1]

    out = {}

    def on_activate(app):
        # Control first and alone, so its failure is separable from the window's.
        # It has to be in a presented window: a WebView that is never realized
        # does not start a load at all, so an unrealized control would report
        # "this machine cannot run WebKit" on a machine that runs it perfectly.
        state = {"loaded": False}
        control_view = WebKit.WebView()
        control_view.connect("load-changed",
                             lambda view, event: state.__setitem__("loaded", True))
        control_window = Gtk.Window(application=app)
        control_window.set_child(control_view)
        control_window.present()
        control_view.load_uri("about:blank")
        out["control_ok"] = False

        def after_control():
            out["control_ok"] = bool(state["loaded"])
            control_window.close()
            control_view.terminate_web_process()

            attached = []
            window = browser.BrowserWindow(
                app, on_attach=lambda t, u, x: attached.append((t, u, x)), home_url="")
            window.present()
            started = time.time()
            window.webview().load_uri(url)

            def halfway():
                # Read mid-load: the spinner and the title are only meaningful
                # while the load is in flight, and a check made after it would
                # pass on an implementation that shows nothing at all.
                out["spinner_visible_mid_load"] = window._spinner.get_visible()
                out["spinner_running_mid_load"] = window._spinner.get_spinning()
                out["elapsed_mid_load"] = time.time() - started
                out["mid_load_url"] = window.current_url()
                return False

            def report():
                out["attached"] = attached
                out["status"] = window._status.get_text()
                out["url"] = window.current_url()
                out["title"] = window.get_title()
                out["spinner_visible_after"] = window._spinner.get_visible()
                out["nav_sensitive"] = [window._back.get_sensitive(),
                                        window._forward.get_sensitive()]
                out["egress"] = egress.read_events()
                window.close()
                server.shutdown()
                app.quit()
                return False

            def attach_and_report():
                out["spinner_visible_pre_attach"] = window._spinner.get_visible()
                window.attach()
                GLib.timeout_add(1500, report)

            GLib.timeout_add(900, halfway)
            GLib.timeout_add(int(DELAY * 1000) + 900, attach_and_report)

        GLib.timeout_add(3000, after_control)

    app = Gtk.Application(application_id="org.shani.chronoa.browserload")
    app.connect("activate", on_activate)
    GLib.timeout_add(60000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


def _run_child(harness, tmp_path, extra_env=None, timeout=150):
    """Run a harness script in a child and return its RESULT payload, if any."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    for name in ("home", "data", "state", "config", "cache"):
        (tmp_path / name).mkdir(exist_ok=True)
    # The child inherits this interpreter's import path. Overriding HOME drops
    # `~/.local/lib/python3.*/site-packages` from sys.path, so `httpx` - which
    # `webtext` imports - disappears; a documented trap, measured here as a
    # ModuleNotFoundError out of webtext before the window was ever built.
    inherited = [entry for entry in sys.path
                 if entry and os.path.isdir(entry) and entry != str(REPO_ROOT)]
    env = dict(os.environ)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(
            [str(REPO_ROOT / "usr" / "lib" / "shani-chronoa")] + inherited),
        # Isolated so an egress record or a GSettings write cannot reach the real
        # home: a suite appending to a real audit log is a documented past
        # accident (`egress._data_home`'s own docstring).
        "HOME": str(tmp_path / "home"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        # Measured here: with accessibility left on, the child dies in
        # at-spi-bus-launcher's dbus-proxy ("Failed to fully launch dbus-proxy"),
        # which needs a session bus this container has no usable copy of. This
        # probe is about widget construction, not the accessibility tree.
        "GTK_A11Y": "none",
        "GSETTINGS_BACKEND": "keyfile",
    })
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-c", harness],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=str(tmp_path),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    return payload, proc


@pytest.fixture(scope="module")
def built_tree(tmp_path_factory):
    payload, proc = _run_child(_BUILD_TREE, tmp_path_factory.mktemp("tree"))
    assert payload is not None, (
        "the child produced no payload, so it never reached the end of "
        f"activate():\n{proc.stdout}\n{proc.stderr[-3000:]}"
    )
    return payload


def test_the_window_constructs_around_a_real_webview(built_tree):
    """A real `Gtk.Window` with a real `WebKit.WebView` inside it, built by this
    module's own constructor.

    No page is loaded: the web process cannot start on every machine, and the
    tree is where removed GTK4 APIs and wrong signal names show up.
    """
    assert built_tree["available"] is True, (
        "WebKitGTK is not importable in this child, so no window was ever really "
        "built and this whole file proves nothing"
    )
    assert built_tree["constructed"] is True
    assert built_tree["is_webview"] is True, (
        "what is inside the window is not a WebKit.WebView, so the navigation "
        "buttons would be reading a history that does not exist"
    )
    assert built_tree["title"] == "Chronoa Browser"
    assert built_tree["nav_sensitive"] == [False, False], (
        "back/forward are sensitive on a view with no history, so the 'sensitivity "
        "comes from the real WebView' claim is not what is in the window"
    )
    assert built_tree["current_url"] == ""
    assert built_tree["spinner_visible"] is False
    assert built_tree["spinner_running"] is False, (
        "a spinner is running on a window that has loaded nothing; the loading "
        "indicator is stuck on rather than driven by the load"
    )


def test_the_menu_has_the_four_documented_actions(built_tree):
    assert built_tree["menu_labels"] == [
        "Reload", "Open in browser", "Go home",
        "Attach this page to the conversation",
    ]


def test_the_real_entry_makes_the_documented_decisions(built_tree):
    """Through the window's own entry and its own configured endpoint, rather
    than by calling the pure function directly."""
    decisions = built_tree["decisions"]
    assert decisions["https://example.com/a?b=1"] == "https://example.com/a?b=1"
    assert decisions["example.com"] == "https://example.com"
    assert decisions["two words"] == "https://duckduckgo.com/?q=two%20words"
    assert decisions["3.1"] == "https://duckduckgo.com/?q=3.1"
    assert decisions["javascript:alert(1)"].startswith("https://duckduckgo.com/?q=")


def test_the_status_line_escapes_what_it_is_given(built_tree):
    """A remote URL reaching `set_markup` unescaped shows `&amp;` to the user, or
    refuses to render at all."""
    assert built_tree["status_text"] == "Attached https://example.com/?a=1&b=2 <not a tag>"
    assert "&amp;" in built_tree["status_label"]
    assert "<not a tag>" not in built_tree["status_label"], (
        "the angle brackets reached the label as markup, so the text was parsed "
        "instead of shown")


def test_attaching_with_nothing_listening_says_so(built_tree):
    """An absent callback is a real state, not a silent no-op: a control that
    does nothing when pressed is how this repo shipped a mic button."""
    assert "nowhere" in built_tree["no_listener_status"]


def test_a_navigation_is_recorded_in_the_egress_log(built_tree):
    """The audit that answers "what left this machine" cannot omit the browser.

    Driven directly rather than through a real load, so it needs neither a
    network nor a web process. What matters is the component name, not merely a
    line: a navigation recorded under somebody else's component would put a URL
    in the log described as a different subsystem's request.
    """
    events = built_tree["egress"]
    assert events, "a navigation was made and nothing reached the egress log"
    last = events[-1]
    assert last["component"] == "browser:navigate"
    assert last["url"] == "https://example.com/x"
    assert last["host"] == "example.com"
    assert last["local"] is False


def test_a_local_file_page_is_not_reported_as_egress(built_tree):
    """A regression test for a real false alarm, measured by driving the window.

    `file://` passes a `'://' in url` check, and `egress.record` then reads the
    host as empty, calls the page remote and logs
    `EGRESS: browser:navigate sent a request to  while privacy mode was ON` -
    a violation for a page read off this machine's own disk. An alarm that fires
    for local files is an alarm that gets ignored.
    """
    assert len(built_tree["egress_after_file_url"]) == len(built_tree["egress"]), (
        "loading a file:// page wrote to the egress log; nothing left the machine"
    )
    assert built_tree["egress_alarms"] == []


def test_a_loaded_page_is_attached_through_webtext(tmp_path):
    """The whole attach path, end to end, on a real page over a real load.

    Served by a loopback HTTP server in the child that answers after 2.5
    seconds, so the loading indicator can be read while the load is genuinely in
    flight - on a page that answers immediately, a spinner wired to nothing
    passes a check made afterwards.

    Skipped - and only after the control has run - on a machine where WebKit's
    web process cannot start at all.
    """
    payload, proc = _run_child(_LOAD_AND_ATTACH, tmp_path)
    if payload is None or not payload.get("control_ok"):
        pytest.skip(
            "this machine cannot start WebKit's web process, so no page can be "
            "loaded here and the attach path could not be exercised. The control "
            "- a bare WebKit.WebView loading about:blank in the same child - "
            f"failed too:\n{proc.stderr[-1500:]}"
        )

    assert payload["elapsed_mid_load"] < 2.5, (
        "the mid-load check ran after the page had already answered, so it saw a "
        "finished load and proves nothing about the loading indicator")
    assert payload["spinner_visible_mid_load"] is True, (
        "the loading indicator was never visible during a real two-and-a-half "
        "second load, so it is not driven by the load")
    assert payload["spinner_running_mid_load"] is True, (
        "the spinner was visible but not running: a static icon is not an "
        "indication of progress")
    assert payload["spinner_visible_after"] is False, (
        "the spinner stayed on after the load finished")
    assert payload["url"].startswith("http://127.0.0.1:")

    attached = payload["attached"]
    assert len(attached) == 1, (
        f"the attach callback ran {len(attached)} times, not once; a second run "
        "would put the same page into the conversation twice"
    )
    title, url, text = attached[0]
    assert title == "Probe"
    assert url == payload["url"]
    assert "first line of the page" in text
    assert "second line" in text
    # webtext's framing, which is the reason this goes through webtext at all.
    assert "Untrusted third-party content" in text
    assert payload["status"] == f"Attached {url}", payload["status"]
    assert payload["title"] == "Probe - Chronoa Browser"


def test_a_real_navigation_is_recorded_as_this_modules_own_request(tmp_path):
    """The browser's egress is in the audit, under its own component.

    Loopback, so this checks the recording and not the alarm: `127.0.0.1` is
    local, and a violation on it would be the false-alarm bug the
    `file://` regression test above was written for.
    """
    payload, proc = _run_child(_LOAD_AND_ATTACH, tmp_path)
    if payload is None or not payload.get("control_ok"):
        pytest.skip("WebKit's web process cannot start here; nothing to record")
    events = payload["egress"]
    assert [event["component"] for event in events] == ["browser:navigate"], events
    assert events[0]["host"] == "127.0.0.1"
    assert events[0]["local"] is True
    assert events[0]["violation"] is False, (
        "a loopback page load was reported as a privacy-mode violation")