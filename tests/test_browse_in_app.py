"""`browse` drives the in-app browser while Chronoa's window runs.

`browse` was a headless Chromium only, which nobody watches and which dies in
the skill sandbox ("browser closed the DevTools pipe"). With a window running,
`browser_bridge` has a provider and the skill drives `gui/browser.py`'s WebKit
window instead (`gui/browser_driver.py`), in this process - so `browse` is on
`tools._LOCAL_TOOLS`, but runs locally only while that provider exists.

What is checked here:

- the consent gate refuses before either browser is touched;
- path selection: provider -> in-app driver, none -> Chromium (`_act`);
- the privilege is conditional: `_runs_locally("browse")` follows the provider;
- what the model may open: http(s) the egress policy allows, nothing else;
- the real thing, on a Broadway display in a child process, through
  `tools.execute_tool`: navigate, type, click, read "Hi Pune" back, and a link
  to a refused address stopped by the window's navigation guard - with a
  control showing it is the guard that stops it.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "usr/lib/shani-chronoa"))


@pytest.fixture
def web_allowed(gsettings_env):
    from shani_chronoa.config import ChronoaConfig
    config = ChronoaConfig()
    config.set("web-sense-enabled", "true")
    config.set("privacy-mode", "false")
    return config


@pytest.fixture
def no_provider(monkeypatch):
    from shani_chronoa import browser_bridge
    monkeypatch.setattr(browser_bridge, "_provider", None)
    monkeypatch.setattr(browser_bridge, "_available", None)


def test_the_model_may_open_only_allowed_web_pages():
    from shani_chronoa.gui.browser_driver import model_may_open
    assert model_may_open("http://127.0.0.1:8765/") == ""
    assert model_may_open("https://example.com/a") == ""
    assert model_may_open("about:blank") == ""
    assert "egress policy" in model_may_open("http://169.254.169.254/latest/meta-data/")
    assert "egress policy" in model_may_open("http://169.254.1.1/")
    for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x"):
        assert "only http and https" in model_may_open(url), url


def test_browse_runs_locally_only_while_a_window_can_serve_it(no_provider):
    from shani_chronoa import browser_bridge, tools
    assert "browse" in tools._LOCAL_TOOLS
    assert tools._runs_locally("ask_user")
    assert not tools._runs_locally("browse"), "no window, yet browse left the sandbox"
    browser_bridge.set_provider(lambda: None, available=lambda: False)
    assert not tools._runs_locally("browse"), "no WebKit, yet browse left the sandbox"
    browser_bridge.set_provider(lambda: None, available=lambda: True)
    assert tools._runs_locally("browse")
    assert not tools._runs_locally("get_datetime")


def test_the_path_follows_the_provider(web_allowed, no_provider, monkeypatch):
    from shani_chronoa import browser_bridge
    from shani_chronoa.gui import browser_driver
    from shani_chronoa.skills import browse
    monkeypatch.setattr(browse, "_act", lambda a: "chromium path")
    monkeypatch.setattr(browser_driver, "act", lambda a: "in-app path")
    assert browse._run({"action": "get_url"}) == "chromium path"
    browser_bridge.set_provider(lambda: None, available=lambda: True)
    assert browse._run({"action": "get_url"}) == "in-app path"


def test_consent_is_checked_before_either_browser(gsettings_env, no_provider, monkeypatch):
    from shani_chronoa import browser_bridge
    from shani_chronoa.gui import browser_driver
    from shani_chronoa.skills import browse
    touched = []
    monkeypatch.setattr(browse, "_act", lambda a: touched.append("chromium") or "x")
    monkeypatch.setattr(browser_driver, "act", lambda a: touched.append("in-app") or "x")
    for provider in (False, True):
        if provider:
            browser_bridge.set_provider(lambda: None, available=lambda: True)
        said = browse._run({"action": "navigate", "url": "https://example.com/"})
        assert "not permitted" in said and "web-sense-enabled" in said, said
    assert touched == [], f"a browser was driven without consent: {touched}"


@pytest.mark.parametrize("action", ["evaluate", "key", "new_tab", "hover", "upload_file"])
def test_unsupported_in_app_actions_fail_without_touching_the_window(action, no_provider):
    from shani_chronoa.gui import browser_driver
    said = browser_driver.act({"action": action, "text": "document.cookie"})
    assert said.startswith("Error:") and "not available here" in said, said


def test_an_in_app_error_is_a_tool_failure(web_allowed, no_provider, monkeypatch):
    from shani_chronoa import browser_bridge
    from shani_chronoa.gui import browser_driver
    from shani_chronoa.skills import browse
    from shani_chronoa.toolfailure import ToolFailure
    browser_bridge.set_provider(lambda: None, available=lambda: True)
    monkeypatch.setattr(browser_driver, "act", lambda a: "Error: the page did not load.")
    with pytest.raises(ToolFailure):
        browse._run({"action": "navigate", "url": "https://example.com/"})


# --- the real window ------------------------------------------------------------

_HARNESS = textwrap.dedent(
    """
    import json, sys, threading, functools, http.server
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib, Gio
    from shani_chronoa.config import ChronoaConfig
    config = ChronoaConfig()
    config.set("web-sense-enabled", "true"); config.set("privacy-mode", "false")
    from shani_chronoa import browser_bridge, tools
    from shani_chronoa.gui import browser_driver
    from shani_chronoa.gui.browser import BrowserWindow, is_available

    PAGE = (b"<!doctype html><title>Greeter</title>"
            b"<input id=name> <button id=go onclick=\\"document.getElementById('out')"
            b".textContent='Hi '+document.getElementById('name').value\\">Greet</button>"
            b"<p id=out></p><a id=away href='http://169.254.1.1/'>away</a>"
            # Like Wikipedia's search box: after three characters the field is
            # replaced by a new one without the id, keeping focus and text.
            b"<div id=box><input id=q oninput=\\"if (this.value.length >= 3 && !window.swapped) {"
            b" window.swapped = 1; const n = document.createElement('input'); n.className = 'q2';"
            b" n.value = this.value; this.replaceWith(n); n.focus(); }\\"></div>")

    SHOP = (b"<!doctype html><title>Shop</title><form><input type=hidden name=flight value=9696>"
            b"<input type=submit value='Choose This Flight'></form>"
            b"<input id=card name=creditCardNumber placeholder='Credit Card Number'>"
            b"<input id=who name=inputName><input type=submit id=buy value='Purchase Flight'>")
    CHECK = (b"<!doctype html><title>Check</title><div class=g-recaptcha data-sitekey=x>"
             b"<label><input type=checkbox> I'm not a robot</label></div>")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.end_headers(); self.wfile.write(CHECK if self.path.startswith("/check") else SHOP if self.path.startswith("/shop") else PAGE)
        def log_message(self, *a): pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"

    app = Gtk.Application(application_id="test.chronoa.browse.inapp",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    held = {}
    def provider():
        if "w" not in held:
            held["w"] = BrowserWindow(application=app, home_url="about:blank")
            held["w"].present()
        return held["w"]
    browser_bridge.set_provider(provider, available=is_available)

    out = {"local": tools._runs_locally("browse")}
    def call(**step):
        return tools.execute_tool("browse", dict(step, timeout=20))

    def worker():
        try:
            out["navigate"] = call(action="navigate", url=base)
            out["type"] = call(action="type", selector="#name", text="Pune")
            out["click"] = call(action="click", selector="#go")
            # What the person watching saw: the cursor and ring on the button,
            # and the step named in the window's strip.
            out["cue"] = browser_driver._json(
                "const h = document.getElementById('__chronoa_cue'); if (!h) return null;"
                "const r = h.shadowRoot, ring = r.querySelector('.ring'), b = document.getElementById('go').getBoundingClientRect();"
                "const ptr = r.querySelector('.ptr');"
                "return {ring_shown: +(ring.dataset.shown || 0), ring_left: parseFloat(ring.style.left),"
                " button_left: b.left, pointer: ptr.style.transform, clickthrough: getComputedStyle(h).pointerEvents};")
            out["strip"] = browser_driver._main(lambda w: w.activity_text())
            out["text"] = call(action="get_text")
            out["swap"] = call(action="type", selector="#q", text="Shaniwar")
            out["swap_value"] = browser_driver._json("return document.querySelector('.q2') ? document.querySelector('.q2').value : null;")
            out["check"] = call(action="navigate", url=base + "check")
            out["strip_check"] = browser_driver._main(lambda w: w.activity_text())
            out["back"] = call(action="navigate", url=base)
            out["shop"] = call(action="navigate", url=base + "shop")
            out["hidden"] = call(action="click", selector="input[value='9696']")
            out["name"] = call(action="type", selector="#who", text="Asha")
            # Nobody to ask in this harness, so money steps must not happen.
            out["card"] = call(action="type", selector="#card", text="4111111111111111")
            out["card_value"] = browser_driver._json("return document.getElementById('card').value;")
            out["buy"] = call(action="click", selector="#buy")
            call(action="navigate", url=base)
            out["away"] = call(action="click", selector="#away", settle=3)
            out["url_after"] = call(action="get_url")
            out["file"] = call(action="navigate", url="file:///etc/passwd")
            # Control: the same click with the guard's check disabled. If the
            # guard were not what stopped it, this would read the same.
            browser_driver.model_may_open = lambda url: ""
            out["away_unguarded"] = call(action="click", selector="#away", settle=3, timeout=8)
        except Exception as exc:
            out["exception"] = repr(exc)
        finally:
            GLib.idle_add(app.quit)

    app.connect("activate", lambda a: (a.hold(), threading.Thread(target=worker, daemon=True).start()))
    GLib.timeout_add(150000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture
def broadway():
    """A private Broadway display, so no window appears on anybody's desktop.

    In the real runtime directory: WebKit's web process reaches the session bus
    through a proxy socket it makes there, and a private runtime directory has no
    bus (measured: `bwrap: Can't find source path .../webkitgtk/bus-proxy-*`).
    """
    daemon = shutil.which("gtk4-broadwayd")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if daemon is None or not runtime:
        pytest.skip("no gtk4-broadwayd or no XDG_RUNTIME_DIR, so no headless display")
    runtime = pathlib.Path(runtime)
    number = next((n for n in range(40, 90)
                   if not (runtime / f"broadway{n + 1}.socket").exists()), None)
    if number is None:
        pytest.skip("no free Broadway display number")
    socket = runtime / f"broadway{number + 1}.socket"
    proc = subprocess.Popen([daemon, f":{number}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline and not socket.exists():
        time.sleep(0.1)
    try:
        yield number, runtime
    finally:
        proc.terminate()  # this PID only
        proc.wait(timeout=10)
        if socket.exists():
            socket.unlink()


def test_browse_drives_the_real_in_app_window(tmp_path, broadway, compiled_schema_dir):
    pytest.importorskip("gi")
    from shani_chronoa.gui.browser import is_available
    if not is_available():
        pytest.skip("WebKitGTK for GTK 4 is not installed (webkitgtk-6.0)")
    number, runtime = broadway
    inherited = [e for e in sys.path if e and os.path.isdir(e) and e != str(REPO_ROOT)]
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    for name in ("home", "data", "state", "config", "cache"):
        (tmp_path / name).mkdir(exist_ok=True)
    env.update({
        "GDK_BACKEND": "broadway", "BROADWAY_DISPLAY": f":{number}",
        "HOME": str(tmp_path / "home"),
        "XDG_DATA_HOME": str(tmp_path / "data"), "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "GSETTINGS_BACKEND": "keyfile", "GSETTINGS_SCHEMA_DIR": str(compiled_schema_dir),
        "SHANI_CHRONOA_KEYRING": "0", "GTK_A11Y": "none", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT / "usr/lib/shani-chronoa")] + inherited),
    })
    proc = subprocess.run([sys.executable, "-c", _HARNESS], capture_output=True, text=True,
                          timeout=180, env=env, cwd=str(tmp_path))
    lines = [l for l in proc.stdout.splitlines() if l.startswith("RESULT")]
    assert lines, f"the child produced no result:\n{proc.stderr[-2000:]}"
    out = json.loads(lines[-1][len("RESULT"):])
    if "did not start loading" in out.get("navigate", "") or "did not load" in out.get("navigate", ""):
        pytest.skip(f"WebKit's web process cannot load a page here: {out['navigate']}")
    assert "exception" not in out, out
    assert out["local"] is True
    assert out["navigate"].startswith("Opened http://127.0.0.1:"), out["navigate"]
    assert "Typed 4 character(s)" in out["type"], out["type"]
    assert "Clicked 'Greet'" in out["click"], out["click"]
    assert "Hi Pune" in out["text"] and "Untrusted third-party content" in out["text"], out["text"]
    cue = out["cue"]
    assert cue and cue["ring_shown"] >= 1 and cue["clickthrough"] == "none", cue
    assert abs(cue["ring_left"] - (cue["button_left"] - 4)) < 1, f"the ring is not on the button: {cue}"
    assert "translate(" in cue["pointer"], cue
    assert out["strip"] == "Chronoa: clicking #go", out["strip"]
    assert "Chronoa" not in out["text"].split("Untrusted third-party content", 1)[1], (
        "the cursor's label leaked into what the model reads: " + out["text"])
    assert out["swap"].startswith("Typed 8"), out["swap"]
    assert "hidden" in out["hidden"] and "Choose This Flight" in out["hidden"], out["hidden"]
    assert out["name"].startswith("Typed 4"), out["name"]
    assert "payment details" in out["card"] and "nobody here to ask" in out["card"], out["card"]
    assert out["card_value"] == "", "a card number went in without anyone allowing it"
    assert "Purchase Flight" in out["buy"] and "nobody here to ask" in out["buy"], out["buy"]
    assert out["swap_value"] == "Shaniwar", f"typing lost the field when it was re-created: {out['swap_value']!r}"
    assert "human-verification check" in out["check"] and "does not solve" in out["check"], out["check"]
    assert "over to you" in out["strip_check"], out["strip_check"]
    assert "egress policy refuses" in out["away"] and "169.254.1.1" in out["away"], out["away"]
    assert out["url_after"].startswith("Current URL: http://127.0.0.1:"), out["url_after"]
    assert "only http and https" in out["file"], out["file"]
    assert "egress policy refuses" not in out["away_unguarded"], (
        "the control was refused too, so the guard is not what the test measured: "
        + out["away_unguarded"])
