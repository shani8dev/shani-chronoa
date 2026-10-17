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
    # A free display is a missing socket *and* a free port: tests/_broadway.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _broadway import free_display
    number = free_display(runtime, 40, 2600)
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


# --- what the page's own engine can do (measured: it cannot hear) ---------------
#
# WebKitGTK answers `undefined` for every Web Speech interface, measured in a real
# WebView over `http://127.0.0.1` with `navigator.mediaDevices` present in the
# same page as the control. So a page's own voice search or "read aloud" button
# is inert here - and `browse` clicking one and reporting success is the
# "a click that lands on something inert reports success" failure this driver
# has already met once.


@pytest.mark.parametrize("label", [
    "Voice search", "Search by voice", "Search with voice", "Dictate",
    "Voice input", "Tap to speak", "Read aloud", "Read this aloud",
    "Listen to this article", "text to speech",
])
def test_a_voice_control_says_when_the_engine_cannot_do_it(label, monkeypatch):
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "page_capabilities", lambda timeout=15.0: {
        "speech_recognition": False, "speech_synthesis": False, "microphone": True})

    note = browser_driver._voice_note(label)

    assert note, "%r is about voice and nothing was said about it" % label
    assert "may not have started" in note, (
        "the wording must not claim the control is broken: a page can dictate "
        "through its own server, so 'this engine has no Web Speech' is not "
        "proof the button is dead - %r" % note)


@pytest.mark.parametrize("label", [
    "Listen live", "Play episode", "Record a purchase", "Play all episodes",
    "Search flights", "Order history", "Jisha",
])
def test_an_ordinary_control_is_never_announced_as_voice(label, monkeypatch):
    """The control: these must not fire, or the note is noise on every page.

    "Listen", "play" and "record" are ordinary English - "Listen live" is a
    stream, not a speech synthesiser.
    """
    from shani_chronoa.gui import browser_driver

    def refused(timeout=15.0):
        raise AssertionError("the page was probed for a control that is not about voice")

    monkeypatch.setattr(browser_driver, "page_capabilities", refused)

    assert browser_driver._voice_note(label) == "", label


def test_an_ordinary_click_pays_nothing_for_the_probe(monkeypatch):
    """The capabilities are read only when the label matches, so a normal click
    costs nothing: one less round trip into the page on every click."""
    from shani_chronoa.gui import browser_driver

    def refused(timeout=15.0):
        raise AssertionError("the page was probed on an ordinary click")

    monkeypatch.setattr(browser_driver, "page_capabilities", refused)
    assert browser_driver._voice_note("Book a flight") == ""


def test_a_voice_control_that_can_work_is_not_announced(monkeypatch):
    """The other direction: a browser that *had* speech must stay quiet."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "page_capabilities", lambda timeout=15.0: {
        "speech_recognition": True, "speech_synthesis": True, "microphone": True})

    assert browser_driver._voice_note("Voice search") == ""


def test_a_probe_that_could_not_answer_is_not_read_as_an_absence(monkeypatch):
    """`None` is unknown, not missing. A page that blocks the check must not
    produce "this browser has no speech recognition" - that is the confident
    wrong answer this note exists to avoid."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "page_capabilities", lambda timeout=15.0: None)
    assert browser_driver._voice_note("Voice search") == ""

    monkeypatch.setattr(browser_driver, "page_capabilities", lambda timeout=15.0: {})
    assert browser_driver._voice_note("Voice search") == "", (
        "an empty dict is not an answer either")


def test_the_probe_names_the_interfaces_it_is_measuring():
    """A probe that quietly stops measuring anything is not a probe.

    Asserted on the script's text because the only thing that can tell is the
    names themselves - there is no page here to run it against.
    """
    from shani_chronoa.gui import browser_driver

    for symbol in ("SpeechRecognition", "webkitSpeechRecognition",
                   "speechSynthesis", "getUserMedia"):
        assert symbol in browser_driver.CAPABILITIES_JS, (
            "%s is missing from the probe, so the answer would be silently "
            "about something else" % symbol)


def test_the_capabilities_action_is_reachable_and_not_mistaken_for_a_bad_name(
        web_allowed, no_provider):
    """It can be asked, not only inferred from a click.

    **This asserts routing, not wording.** There is no window in this process,
    so the action honestly reports that - and that answer is indistinguishable
    from any other failure, which is why the wording is checked separately
    below with the probe stubbed. What is checked here is the part that *can*
    tell two cases apart: the action got past the registry instead of being
    refused as unknown.
    """
    from shani_chronoa.gui import browser_driver

    assert "capabilities" in browser_driver.ACTIONS

    answer = browser_driver.act({"action": "capabilities"})

    assert "unknown action" not in answer, answer
    # the control: an action that is NOT registered is refused by name
    assert "unknown action" in browser_driver.act({"action": "capabilitie"})


def test_the_capabilities_action_names_what_is_missing(web_allowed, no_provider,
                                                      monkeypatch):
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "page_capabilities", lambda timeout=15.0: {
        "speech_recognition": False, "speech_synthesis": False, "microphone": True})

    answer = browser_driver.act({"action": "capabilities"})

    assert "microphone" in answer, answer
    assert "speech recognition" in answer and "speech synthesis" in answer, answer
    assert "will not work here" in answer, answer


def test_the_click_answer_actually_carries_the_note(web_allowed, no_provider,
                                                   monkeypatch):
    """**A helper can be correct and still be unreachable.**

    Every other test here calls `_voice_note` directly, so removing it from the
    click path entirely - the mutation that removed the whole feature - left the
    file green. This drives `_act` through the click branch instead, because
    the feature is not the function, it is the sentence the person reads.
    """
    from shani_chronoa.gui import browser_driver

    for name, value in (
            ("_order_button", lambda *a, **k: ""),
            ("_money_ok", lambda *a, **k: ""),
            ("_say", lambda *a, **k: None),
            ("_point_at", lambda *a, **k: None),
            ("_click_script", lambda *a, **k: "return 1;"),
            ("_start_script", lambda *a, **k: "return 1;"),
            ("_describe_load", lambda *a, **k: "Nothing new loaded."),
            ("_challenge_note", lambda: ""),
            ("_drive", lambda *a, **k: ({"label": "Voice search"}, None)),
            ("_voice_note", lambda label, timeout=15.0: " NOTE-MARKER"),
              ("page_controls", lambda timeout=15.0: []),
    ):
        monkeypatch.setattr(browser_driver, name, value)

    answer = browser_driver._act("click", {"selector": "#mic"}, 15.0)

    assert "NOTE-MARKER" in answer, (
        "the click answer never asked whether the control could work: %r" % answer)
    assert answer.index("Clicked") < answer.index("NOTE-MARKER"), (
        "the note belongs after the outcome, so a refusal is never dressed up "
        "with it: %r" % answer)


def test_a_refusal_is_not_dressed_up_with_the_note(web_allowed, no_provider,
                                                   monkeypatch):
    """The note is added *after* the outcome is known good."""
    from shani_chronoa.gui import browser_driver

    for name, value in (
            ("_order_button", lambda *a, **k: ""),
            ("_money_ok", lambda *a, **k: ""),
            ("_say", lambda *a, **k: None),
            ("_point_at", lambda *a, **k: None),
            ("_click_script", lambda *a, **k: "return 1;"),
            ("_start_script", lambda *a, **k: "return 1;"),
            ("_describe_load", lambda *a, **k: "Error: the network policy refuses it."),
            ("_voice_note", lambda label, timeout=15.0: " NOTE-MARKER"),
              ("page_controls", lambda timeout=15.0: []),
            ("_drive", lambda *a, **k: ({"label": "Voice search"}, None)),
    ):
        monkeypatch.setattr(browser_driver, name, value)

    answer = browser_driver._act("click", {"selector": "#mic"}, 15.0)

    assert "NOTE-MARKER" not in answer, answer


# --- a control that is covered is not a control you clicked (2026-10-11) --------
#
# Ported from `harness-study/browser-use`'s `dom/serializer/paint_order.py`, which
# marks an element hidden when a later-painted element fully covers it. The
# existing check catches `display:none`, `visibility:hidden`, `type=hidden` and a
# zero-size box - it cannot catch a fully laid-out button painted *under*
# something, which is the failure this session's AGENTS.md records as
# `WINDOW=1024`: correctly-placed clicks doing nothing because another surface
# was on top.
#
# Asked of `elementFromPoint` at the element's own centre rather than a rectangle
# union, because the browser already knows the paint order.


def test_a_covered_control_is_refused_and_names_what_is_on_top(no_provider):
    from shani_chronoa.gui import browser_driver

    script = browser_driver._click_script("#btn", 0, 0)

    assert "elementFromPoint" in script, (
        "nothing asks what is actually on top of the target")
    assert "covered" in script, "the refusal it produces is not named anywhere"


def test_a_control_with_nothing_over_it_is_not_refused():
    """The control: `elementFromPoint` returning the element **or one of its own
    children** must not fire. A button's label is a child, and a driver refusing
    every button with text in it is worse than no check."""
    from shani_chronoa.gui import browser_driver

    script = browser_driver._click_script("#btn", 0, 0)

    assert "el.contains(top)" in script, (
        "a child painted over the parent is not coverage, and the script has "
        "no allowance for it")


def test_a_covered_click_says_what_is_in_the_way(web_allowed, no_provider,
                                                monkeypatch):
    """The sentence, not the mechanism. "could not click: covered" names the
    reason and not the thing a person needs."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "_order_button", lambda *a, **k: "")
    monkeypatch.setattr(browser_driver, "_money_ok", lambda *a, **k: "")
    monkeypatch.setattr(browser_driver, "_say", lambda *a, **k: None)
    monkeypatch.setattr(browser_driver, "_point_at", lambda *a, **k: None)
    monkeypatch.setattr(browser_driver, "_describe_load", lambda *a, **k: "")
    monkeypatch.setattr(browser_driver, "_challenge_note", lambda: "")
    monkeypatch.setattr(browser_driver, "_drive", lambda *a, **k: (
        {"error": "covered", "onTop": "Please wait"}, None))
    monkeypatch.setattr(browser_driver, "page_controls", lambda *a, **k: [])

    said = browser_driver._act("click", {"selector": "#btn", "settle": 0}, 5.0)

    assert "Please wait" in said, said
    assert "on top of it" in said, said
    assert "not a hidden element" in said, (
        "the distinction is the point: the usual hidden check passes it, which "
        "is why it needed a second one: %r" % said)


def test_a_click_says_what_appeared_after_it():
    """browser-use marks any node absent from the previous step's id set with `*`,
    so the model can see what its action produced. Without the equivalent,
    `Clicked 'Book'` is the same sentence whether or not the page changed."""
    from shani_chronoa.gui import browser_driver

    before = ["button|Book the flight", "input|From"]
    after = ["button|Book the flight", "input|From",
             "button|Confirm", "button|Receipt"]

    said = browser_driver._what_appeared(before, after)

    assert "Confirm" in said and "Receipt" in said, said
    assert "Since the click" in said, said


def test_a_click_that_changed_nothing_says_rather_than_inventing_it():
    """The control. Empty is the honest answer: a click can also navigate or
    change only text, so "nothing appeared" must not read as a failure."""
    from shani_chronoa.gui import browser_driver

    same = ["button|Book the flight"]

    assert browser_driver._what_appeared(same, same) == ""
    # and a page that could not be read says nothing at all, not "no controls"
    assert browser_driver._what_appeared(None, []) == ""
    assert browser_driver._what_appeared([], None) == ""


def test_the_controls_themselves_are_named_not_indexed():
    """**Names differ from CDP node ids on a page that re-renders.** browser-use
    keys `is_new` on `backend_node_id`; a framework that rebuilds its DOM hands
    out fresh ids for the same controls, so ids would report everything as new
    and the signal would be noise. A tag+name pair survives that."""
    from shani_chronoa.gui import browser_driver

    assert "innerText" in browser_driver.CONTROLS_JS, (
        "the fingerprint has to carry the control's name, not only its tag")
    assert "aria-label" in browser_driver.CONTROLS_JS
    assert "tagName.toLowerCase()" in browser_driver.CONTROLS_JS


def test_a_click_answer_carries_what_appeared(web_allowed, no_provider,
                                              monkeypatch):
    from shani_chronoa.gui import browser_driver

    seen = iter([["button|Book the flight"],
                 ["button|Book the flight", "button|Confirm"]])
    monkeypatch.setattr(browser_driver, "page_controls", lambda timeout=15.0: next(seen))
    for name, value in (
            ("_order_button", lambda *a, **k: ""),
            ("_money_ok", lambda *a, **k: ""),
            ("_say", lambda *a, **k: None),
            ("_point_at", lambda *a, **k: None),
            ("_click_script", lambda *a, **k: "return 1;"),
            ("_start_script", lambda *a, **k: "return 1;"),
            ("_describe_load", lambda *a, **k: ""),
            ("_challenge_note", lambda: ""),
            ("_drive", lambda *a, **k: ({"label": "Book"}, None)),
    ):
        monkeypatch.setattr(browser_driver, name, value)

    said = browser_driver._act("click", {"selector": "#book", "settle": 0}, 5.0)

    assert "Clicked 'Book'" in said, said
    assert "Confirm" in said and "Since the click" in said, (
        "the reply never asked what the click produced: %r" % said)


# --- a JavaScript dialog used to hang the whole browser (2026-10-11) ------------
#
# `gui/browser.py` connected no `script-dialog` handler anywhere. Measured on a
# real `WebKit.WebView`: `alert()` halts the page's script, and every later
# `evaluate_javascript` - which is how *everything* here reads text, finds
# controls and clicks - never calls back at all. So any page that raised a
# dialog took the driver down with it, and the failure looked like a slow page.
#
# Ported in shape from `harness-study/browser-use`'s `popups_watchdog.py`, which
# keeps each auto-dismissed dialog's message so the model sees the page's own
# words. Chronoa cannot dismiss these (measured below), so the same rule applies
# with the sign flipped: the message is the only thing the person has.


def test_the_browser_watches_for_javascript_dialogs(web_allowed, no_provider):
    """A handler is connected, and it is the *modern* signal.

    WebKit 6.0 has no `alert`/`confirm`/`prompt` signals at all - connecting one
    raises `unknown signal name` (measured) - so this is the only seam there is.
    """
    import inspect

    from shani_chronoa.gui import browser

    src = inspect.getsource(browser)

    assert '"script-dialog"' in src, (
        "nothing is connected, so a page that raises alert() wedges the browser")
    assert 'connect("confirm"' not in src, (
        "that signal does not exist in WebKit 6.0 and raises at connect time")


def test_a_dialog_is_recorded_and_never_dismissed(web_allowed, no_provider):
    """**The load-bearing one.** `dialog.close()` segfaults this build.

    Measured four ways: on an `alert` and on a `confirm`, with and without an
    explicit `ref()`/`unref()` bracket. The control is the same handler returning
    `True` without calling it, which runs to completion - so the crash belongs to
    `close()` and not to having a handler.

    A watchdog that fixed the hang by crashing the process would be worse than
    the hang, so this asserts the *absence* of the only call that can do that.
    """
    import ast
    import inspect
    import textwrap

    from shani_chronoa.gui import browser

    cls = next(v for v in vars(browser).values()
               if inspect.isclass(v) and hasattr(v, "_on_script_dialog"))
    src = textwrap.dedent(inspect.getsource(cls._on_script_dialog))

    # **Parsed, not grepped.** The docstring above quotes the whole measurement
    # table, so a substring check finds "dialog.close()" in the prose describing
    # the crash and reports the fix as present. A source check that can match its
    # own explanation is not a source check - this is the same trap this repo has
    # recorded twice (a grep matching its own comment, a check matching the sheet
    # prose it was written about).
    called = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "dialog":
            called.add(node.attr)

    assert "close" not in called, (
        "close() segfaults WebKitScriptDialog on this WebKit/PyGObject build; "
        "the dialog must be recorded, not dismissed, not closed")
    assert "unref" not in called
    assert "confirm_set_confirmed" not in called, (
        "it dismisses nothing on its own - measured: the page stayed wedged "
        "after it")


def test_a_read_that_fails_names_the_dialog(web_allowed, no_provider,
                                           monkeypatch):
    """A timeout that does not quote the dialog is indistinguishable from a slow
    page, which is the whole failure."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(
        browser_driver, "pending_dialogs",
        lambda *a, **k: [{"type": "alert",
                          "message": "Your session expires in 5 minutes"}])

    note = browser_driver.dialog_note()

    assert "Your session expires in 5 minutes" in note, note
    assert "paused" in note, note
    assert "cannot dismiss" in note, (
        "the note must not imply Chronoa cleared it: %r" % note)


def test_the_note_says_nothing_when_no_dialog_is_holding_the_page(
        web_allowed, no_provider, monkeypatch):
    """The control. A note attached to an ordinary failure would train a reader
    to ignore it."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "pending_dialogs", lambda *a, **k: [])

    assert browser_driver.dialog_note() == ""


def test_a_window_that_cannot_be_reached_is_not_a_clean_page(web_allowed,
                                                             no_provider,
                                                             monkeypatch):
    """`[]` means *nothing recorded*, never *the page is fine*.

    A read that cannot reach the window answers `[]` too, so the note is only
    ever added to a failure and never used to contradict a success - and that is
    the property, not the return value.
    """
    from shani_chronoa.gui import browser_driver

    def unreachable(fn, timeout=15.0):
        raise RuntimeError("no window")

    monkeypatch.setattr(browser_driver, "_main", unreachable)
    monkeypatch.setattr(browser_driver.browser_bridge, "on_main", unreachable)

    assert browser_driver.pending_dialogs() == []


# --- a page could ask for the microphone and nobody was watching (2026-10-11) ---
#
# browser-use's `permissions_watchdog.py` is 43 lines and **grant-only**: it calls
# `Browser.grantPermissions` for whatever the profile lists, never denies, never
# asks, never reports. That is the opposite of what a consent architecture wants,
# so nothing was taken from it directly.
#
# What was taken is the shape: permissions are an **explicit, listable set**, not
# an implicit default. Chronoa connected no `permission-request` handler at all,
# so every decision belonged to whatever the platform did on its own - and none
# of it reached the person or the driver, so a refusal and a grant looked
# identical from inside the app.


class _FakeAsk:
    """Stands in for a `WebKit.PermissionRequest` without needing a WebView."""

    def __init__(self, class_name, domain=None):
        type(self).__name__  # the real signal names its own type; mirror that
        self.__class__ = type(class_name, (_FakeAsk,), {})
        self.denied = False
        self._domain = domain

    def deny(self):
        self.denied = True


def _handler_class():
    import inspect

    from shani_chronoa.gui import browser

    return next(v for v in vars(browser).values()
                if inspect.isclass(v) and hasattr(v, "_on_permission_request"))


class _StubWindow:
    permission_asks = []


def test_a_permission_request_is_refused_and_recorded():
    """The ask is recorded *and* refused - a record alone changes nothing."""
    cls = _handler_class()
    stub = _StubWindow()
    stub.permission_asks = []
    ask = _FakeAsk("NotificationPermissionRequest")

    handled = cls._on_permission_request(stub, None, ask)

    assert ask.denied, "the page's request was left to whatever the platform does"
    assert handled is True, (
        "returning False runs WebKit's default handler, which on a machine "
        "with a working permission store can answer yes")
    assert stub.permission_asks == ["desktop notifications"], stub.permission_asks


def test_a_refusal_is_the_handler_answer_and_not_the_default():
    """**Returning False is the dangerous value**, and it is not obvious from
    reading - `False` reads as "not handled, carry on", which is exactly what
    lets the platform decide."""
    import inspect

    cls = _handler_class()
    body = inspect.getsource(cls._on_permission_request)

    assert "return True" in body, "the handler must answer True - it handled it"


def test_what_was_asked_for_is_named_for_a_person(web_allowed, no_provider):
    stub = _StubWindow()
    stub.permission_asks = []

    cases = {
        "NotificationPermissionRequest": "desktop notifications",
        "GeolocationPermissionRequest": "your location",
        "ClipboardPermissionRequest": "the clipboard",
    }
    for class_name, expected in cases.items():
        cls = _handler_class()
        got = cls._on_permission_request(stub, None, _FakeAsk(class_name))
        assert got is True
        assert stub.permission_asks[-1] == expected, (
            "%s was named %r" % (class_name, stub.permission_asks[-1]))


def test_a_permission_nobody_has_heard_of_keeps_its_own_name():
    """**The control, and the reason the helper is written this way.** Folding an
    unknown request into a friendly label is how a capability you never audited
    arrives wearing a name you did audit."""
    cls = _handler_class()
    stub = _StubWindow()
    stub.permission_asks = []

    cls._on_permission_request(stub, None, _FakeAsk("TelepathyPermissionRequest"))

    assert stub.permission_asks == ["Telepathy"], (
        "an unknown request was renamed into something familiar: %r"
        % stub.permission_asks)


def test_the_driver_says_what_was_refused(web_allowed, no_provider, monkeypatch):
    """A page whose microphone was refused has a button that does nothing, and
    "the driver clicked it and nothing happened" is the ambiguity this repo has
    recorded several times."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "pending_permission_asks",
                        lambda *a, **k: ["the microphone"])

    note = browser_driver.permission_note()

    assert "the microphone" in note, note
    assert "refused" in note, note
    assert "will not work" in note, (
        "the note must connect the refusal to the dead control: %r" % note)


def test_no_refusals_means_no_note(web_allowed, no_provider, monkeypatch):
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "pending_permission_asks", lambda *a, **k: [])

    assert browser_driver.permission_note() == ""


def test_the_permission_handler_is_actually_connected():
    """**Calling the handler proves nothing about it being reached.**

    Every test above invokes `_on_permission_request` directly, so a build in
    which the signal was never connected would pass all of them - which is this
    repository's own recorded rule ("a test that calls the function is not a test
    that the skill is reachable"), and the way `scan_archive.py` and
    `guardrail.py` both shipped.

    Parsed rather than grepped, so it cannot match its own prose; and the control
    is the neighbouring `script-dialog` connection, which really is there.
    """
    import ast
    import inspect
    import textwrap

    cls = next(v for v in vars(__import__(
        "shani_chronoa.gui.browser", fromlist=["x"])).values()
        if inspect.isclass(v) and hasattr(v, "_build_webview"))
    src = textwrap.dedent(inspect.getsource(cls._build_webview))
    connected = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "connect" and node.args \
                and isinstance(node.args[0], ast.Constant):
            connected.add(node.args[0].value)

    assert "permission-request" in connected, (
        "the handler is never attached, so no page can reach it: %r" % connected)
    assert "script-dialog" in connected, (
        "the control: the sibling connection really is there")


# --- a dead web process was reported as "Unsupported result type" (2026-10-11) ---
#
# The third instance of the same shape: a way for the page to stop answering that
# the answer does not name. Measured by really killing the web process of a real
# WebView with `terminate_web_process()`:
#
#     web-process-terminated         -> `terminated-by-api`
#     get_is_web_process_responsive -> **True**
#     the next evaluate_javascript  -> **"Unsupported result type"**
#
# browser-use's `crash_watchdog.py` dispatches a `BrowserErrorEvent` carrying the
# URL and whether it was the agent's own tab. The transferable part is that a
# crash is a **first-class event naming the page**, not a silent death.


class _FakeReason:
    def __init__(self, nick):
        self.value_nick = nick


def _termination_handler_class():
    import inspect

    from shani_chronoa.gui import browser

    return next(v for v in vars(browser).values()
                if inspect.isclass(v) and hasattr(v, "_on_web_process_terminated"))


def test_a_dead_web_process_is_recorded_with_its_own_reason():
    cls = _termination_handler_class()
    stub = _StubWindow()
    stub.web_process_deaths = []

    for nick in ("crashed", "exceeded-memory-limit", "terminated-by-api"):
        cls._on_web_process_terminated(stub, None, _FakeReason(nick))

    assert stub.web_process_deaths == [
        "crashed", "exceeded-memory-limit", "terminated-by-api"]


def test_a_restarted_process_is_not_reported_as_a_crash(web_allowed,
                                                        no_provider,
                                                        monkeypatch):
    """**The distinction the three reasons exist for.** `terminated-by-api` is a
    deliberate process replacement and is not a fault; calling it a crash is the
    false alarm this repo has recorded repeatedly in other shapes."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "pending_web_process_deaths",
                        lambda *a, **k: ["terminated-by-api"])

    note = browser_driver.page_death_note()

    assert "crash" not in note.lower(), (
        "a deliberate restart reported as a crash: %r" % note)
    assert "restarted" in note, note


def test_the_three_reasons_are_told_apart(web_allowed, no_provider, monkeypatch):
    """Running out of memory and crashing are different facts, and a driver that
    cannot say which will retry a page that will only die again."""
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "pending_web_process_deaths",
                        lambda *a, **k: ["exceeded-memory-limit"])

    note = browser_driver.page_death_note()

    assert "memory" in note, note
    assert "crash" not in note.lower(), note


def test_no_deaths_means_no_note(web_allowed, no_provider, monkeypatch):
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "pending_web_process_deaths",
                        lambda *a, **k: [])

    assert browser_driver.page_death_note() == ""


def test_web_process_responsiveness_is_not_used_as_a_health_signal():
    """**The measured trap, pinned.** WebKit relaunches the web process, so this
    property answers `True` a moment after the page was destroyed. A liveness
    check built on it reports a healthy browser that cannot be read."""
    import ast
    import inspect
    import textwrap

    cls = next(v for v in vars(__import__(
        "shani_chronoa.gui.browser", fromlist=["x"])).values()
        if inspect.isclass(v) and hasattr(v, "pending_web_process_deaths"))

    # Parsed, not grepped: the docstring quotes the measurement that produced this
    # rule, so a substring check finds the very name it forbids. Third time this
    # session, and the reason this repo's own guidance is "assert on the shape of
    # an interface, not its text".
    for method in (cls._on_web_process_terminated, cls.pending_web_process_deaths,
                   cls.clear_web_process_deaths):
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        forbidden = {"is_web_process_responsive",
                     "get_is_web_process_responsive"}
        assert not ((names | attrs) & forbidden), (
            "%s consults a liveness probe that answers True on a dead page"
            % method.__name__)


#: Every signal `_build_webview` is expected to connect, and why. One list, so
#: the next watchdog inherits the reachability guard rather than needing it
#: written again - the gap that let the `web-process-terminated` connection be
#: deleted with the whole file still green.
EXPECTED_WEBVIEW_SIGNALS = {
    "load-changed": "navigation and failures",
    "load-failed": "a page that would not load",
    "notify::title": "the window title follows the page",
    "create": "a new window the page asked for",
    "decide-policy": "navigations are checked before they are made",
    "script-dialog": "a JavaScript dialog pauses the page instead of wedging it",
    "permission-request": "a page's asks for the microphone etc. are refused",
    "web-process-terminated": "a dead web process is reported as one",
}


def test_every_watchdog_this_module_claims_is_actually_connected():
    """**The general form of a gap this suite walked into twice.**

    Every behavioural test here calls its handler directly, so a build in which
    the signal was never connected passes all of them - the rule this repository
    records as "a test that calls the function is not a test that the skill is
    reachable", and the way `scan_archive.py` and `guardrail.py` both shipped.

    The connection is read from `_build_webview`'s AST rather than its text, so
    this cannot match the prose above it. The control is that the same scan finds
    the long-standing connections, so a parse that silently finds nothing cannot
    pass.
    """
    import ast
    import inspect
    import textwrap

    from shani_chronoa.gui import browser

    cls = next(v for v in vars(browser).values()
               if inspect.isclass(v) and hasattr(v, "_build_webview"))
    src = textwrap.dedent(inspect.getsource(cls._build_webview))
    connected = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect" and node.args
                and isinstance(node.args[0], ast.Constant)):
            connected.add(node.args[0].value)

    missing = sorted(set(EXPECTED_WEBVIEW_SIGNALS) - connected)
    assert not missing, (
        "connected but not really: %s - %s. A handler nothing connects is a "
        "module that passes every behavioural test while the feature is dead."
        % (missing, [EXPECTED_WEBVIEW_SIGNALS[m] for m in missing]))


# --- a page could put a file on disk with a name the server chose (2026-10-11) ---
#
# `grep -i download` across `gui/browser.py` and `gui/browser_driver.py` returned
# **no hits at all**. Measured on a real page whose link serves
# `Content-Disposition: attachment; filename="../../escaped.txt"`:
#
#     WebKit sanitised the traversal   -> the file landed as `escaped.txt`
#     `download-started` signal        -> does not exist on any class
#     `PolicyDecisionType`             -> NAVIGATION_ACTION, NEW_WINDOW_ACTION,
#                                         RESPONSE.  No download member.
#     a RESPONSE decision **does** fire, and its headers carry
#       `get_content_disposition()` -> `(is_attachment, disposition, params)`
#
# So the file arrives either way and WebKit offers no way to stop it. What is
# available is **seeing** it, and saying so.


class _FakeDisposition:
    """`get_content_disposition()` is a 3-tuple named tuple, measured:

        (False, disposition=None,     params=None)
        (True,  disposition='attachment', params={'filename': 'escaped.txt'})
    """

    def __init__(self, attachment, params=None):
        self.data = (attachment, "attachment" if attachment else None, params)


class _FakeHeaders:
    def __init__(self, disposition):
        self._d = disposition

    def get_content_disposition(self):
        return self._d.data


class _FakeResponse:
    def __init__(self, disposition):
        self._h = _FakeHeaders(disposition)

    def get_http_headers(self):
        return self._h


class _FakeDecision:
    def __init__(self, disposition):
        self._r = _FakeResponse(disposition)
        self.ignored = 0

    def get_response(self):
        return self._r

    def ignore(self):
        self.ignored += 1


def _note_download_class():
    import inspect

    from shani_chronoa.gui import browser

    return next(v for v in vars(browser).values()
                if inspect.isclass(v) and hasattr(v, "_note_download"))


def _dl_stub():
    cls = _note_download_class()
    stub = _StubWindow()
    stub.started_downloads = []
    stub.recorded = []
    stub._record_download = lambda name: stub.recorded.append(name)
    return cls, stub


def test_a_download_is_named_from_the_headers_parameters():
    """**The parameters dict, not index 1.** Measured: index 1 is the
    disposition *type*, so reading the name from there records every download as
    `"attachment"` - which my first version did, and the live run showed."""
    cls, stub = _dl_stub()
    decision = _FakeDecision(_FakeDisposition(True, {"filename": "escaped.txt"}))

    cls._note_download(stub, decision)

    assert stub.started_downloads == ["escaped.txt"], stub.started_downloads
    assert stub.recorded == ["escaped.txt"], stub.recorded


def test_the_download_is_not_claimed_to_be_prevented():
    """**The load-bearing one.** `decision.ignore()` was tried and measured to
    do nothing: with it and without it the file is on disk. Returning True would
    tell the caller the response was handled, which is the false-alarm shape this
    repo records repeatedly."""
    cls, stub = _dl_stub()
    decision = _FakeDecision(_FakeDisposition(True, {"filename": "escaped.txt"}))

    handled = cls._note_download(stub, decision)

    assert decision.ignored == 0, (
        "ignore() was called and does not prevent the download; the file lands "
        "either way, and claiming otherwise is the lie")
    assert handled is False, (
        "answering True claims the response was refused, which nothing does")


def test_a_normal_response_is_not_a_download_and_is_not_claimed_as_handled():
    """The control, strengthened after a survivor.

    Asserting only that nothing was *recorded* left a mutation that returned
    `True` for every response - claiming it was handled - completely green. What
    was missing is the second half: an ordinary response must also **not** be
    answered as handled, because "handled" is what the caller believes when it
    decides nothing further is needed.
    """
    cls, stub = _dl_stub()
    decision = _FakeDisposition(False)

    handled = cls._note_download(stub, _FakeDecision(decision))

    assert stub.started_downloads == [], (
        "an ordinary page response was recorded as a download")
    assert handled is False, (
        "an ordinary response claimed to have been handled, which is a refusal "
        "of a page nobody asked to be refused")


def test_a_server_chosen_name_is_reduced_to_a_bare_basename():
    """Ported from browser-use's `_sanitize_download_filename`. Defence in depth
    rather than a patch for a hole: WebKit reduced `../../escaped.txt` itself.
    What it did **not** do is stop the file being named by the server."""
    from shani_chronoa.gui.browser import _safe_download_name

    assert _safe_download_name("../../escaped.txt") == "escaped.txt"
    assert _safe_download_name("..\\..\\windows\\evil.exe") == "evil.exe"
    assert _safe_download_name("..") == "download"
    assert _safe_download_name(".") == "download"
    assert _safe_download_name("") == "download"
    assert _safe_download_name(None) == "download"


def test_the_note_says_the_file_is_there_and_does_not_claim_a_refusal(
        web_allowed, no_provider, monkeypatch):
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "started_downloads",
                        lambda *a, **k: ["escaped.txt"])

    note = browser_driver.download_note()

    assert "escaped.txt" in note, note
    assert "downloaded" in note, note
    assert "cannot prevent" in note, (
        "the note must state the limit rather than imply the file did not land: "
        "%r" % note)
    assert "refused" not in note.lower(), (
        "nothing refused it; saying so is the claim this change removed: %r" % note)


def test_no_downloads_means_no_note(web_allowed, no_provider, monkeypatch):
    from shani_chronoa.gui import browser_driver

    monkeypatch.setattr(browser_driver, "started_downloads", lambda *a, **k: [])

    assert browser_driver.download_note() == ""
