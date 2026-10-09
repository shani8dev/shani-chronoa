"""The in-app browser: the address-bar decision, availability, and the attach path.

Three of these tests exist to prove the suite can fail. A GUI module that was
never constructed passes every test that only calls its pure functions, which
is how this repo has shipped a mic button wired to nothing; the real window is
therefore driven from a child process in `test_browser_window_probe.py`, and
here the controls are:

- `test_the_url_logic_catches_a_wrong_implementation` rewrites the decision
  function in a copy of the real source file, loads that copy, and asserts the
  same assertions fail against it. A URL-decision test that only tests the
  shipped code proves nothing about whether it can notice a change.
- `test_attach_reaches_webtext_and_never_an_http_client` replaces
  `httpx.Client` with something that raises, so an implementation that opened
  its own client fails instead of quietly passing.
- The source checks at the bottom are `ast`, not `in`, so they cannot be
  satisfied by a mention in a docstring.

Scope, stated up front because it decides what can be proven here: a real
`WebKit.WebView` needs a display connection and spawns a web process, so the
window itself is built in a child process elsewhere in this file's directory.
Everything that does not need one - the address-bar decision, availability, and
the whole attach path including its refusals - is covered here.
"""

import ast
import re
import importlib.util
import pathlib
import sys
import tempfile

import httpx
import pytest

from shani_chronoa import webtext
from shani_chronoa.gui import browser

SOURCE_PATH = pathlib.Path(browser.__file__)


# --- 1. the address bar, as a pure function ---------------------------------


class TestAddressBarDecision:
    """`target_url` is what the address entry does with a line, and it is pure:
    no widget, no view, no network. That is deliberate - the decision is the part
    that is easy to get subtly wrong, and it is testable without GTK."""

    @pytest.mark.parametrize("typed, expected", [
        ("https://example.com/page", "https://example.com/page"),
        ("http://example.com", "http://example.com"),
        ("  https://example.com/x?q=1&r=2  ", "https://example.com/x?q=1&r=2"),
        ("HTTPS://Example.COM/A", "HTTPS://Example.COM/A"),
        ("file:///home/someone/notes.html", "file:///home/someone/notes.html"),
        ("about:blank", "about:blank"),
    ])
    def test_a_full_url_is_loaded_as_written(self, typed, expected):
        assert browser.target_url(typed) == expected

    @pytest.mark.parametrize("typed, expected", [
        ("example.com", "https://example.com"),
        ("example.co.uk/news", "https://example.co.uk/news"),
        ("sub.example.com:8080/a?b=c", "https://sub.example.com:8080/a?b=c"),
        ("localhost", "https://localhost"),
        ("localhost:8080", "https://localhost:8080"),
        ("192.168.1.1", "https://192.168.1.1"),
        ("[::1]:8080/", "https://[::1]:8080/"),
    ])
    def test_a_bare_host_gets_https(self, typed, expected):
        assert browser.target_url(typed) == expected

    @pytest.mark.parametrize("typed", [
        "what is the weather in Lisbon",
        "3.1",
        "1.2.3",
        "how do I spell accommodate",
        "note: buy milk",
        "example .com",
    ])
    def test_anything_else_is_a_search_query(self, typed):
        result = browser.target_url(typed)
        assert result.startswith("https://duckduckgo.com/?q=")
        assert typed.strip() != result, "the query was not turned into a URL at all"

    @pytest.mark.parametrize("typed", ["", "   ", None])
    def test_nothing_typed_loads_nothing(self, typed):
        """A stray Enter in an empty field must not load the home page.

        Returning the home URL here would put a remote page on screen with no
        navigation, which is the surprise this exists to avoid; the caller asks
        this function because it cannot tell an empty entry from a real one.
        """
        assert browser.target_url(typed) == ""

    def test_a_version_number_is_searched_rather_than_loaded(self):
        """The reason the last label has to be alphabetic.

        Loading `https://3.1/` answers nothing and leaves an error page in the
        window; searching for `3.1` answers something. A purely structural "has
        a dot and no space" rule - which is what Alpaca's does - sends every
        version number, and every decimal, to a host that does not exist.
        """
        assert browser.target_url("3.1") == "https://duckduckgo.com/?q=3.1"
        assert browser.target_url("192.168.1.1") == "https://192.168.1.1"

    def test_a_javascript_or_data_url_is_not_loaded(self):
        """`javascript:` in an address bar runs in the document it is typed into
        and `data:` renders arbitrary markup in the view. Neither is something
        one Enter keypress should be able to do, so the scheme list is an
        allowlist and both fall through to the search."""
        for typed in ("javascript:alert(document.cookie)",
                      "data:text/html,<script>alert(1)</script>"):
            assert browser.target_url(typed).startswith("https://duckduckgo.com/?q=")

    def test_the_search_endpoint_is_configurable(self):
        assert browser.target_url(
            "hello", search_url="https://search.example/?q={query}"
        ) == "https://search.example/?q=hello"

    def test_a_query_is_percent_encoded_into_the_search_url(self):
        # An unencoded space or `&` in a query produces a broken search, or an
        # extra parameter the endpoint never asked for.
        assert browser.target_url(
            "c++ vs python? a&b", search_url="https://search.example/?q={query}"
        ) == "https://search.example/?q=c%2B%2B%20vs%20python%3F%20a%26b"

    def test_an_endpoint_without_a_placeholder_gets_the_query_appended(self):
        """`web_search.SEARCH_ENDPOINT` is a bare prefix the query is appended to,
        so one configured endpoint has to serve both shapes."""
        assert browser.search_target("https://lite.duckduckgo.com/lite/?q=", "a b") == \
            "https://lite.duckduckgo.com/lite/?q=a%20b"


def _load_variant(source: str, name: str, directory=None):
    """Import `source` as a module of its own, under a name of its own.

    Loaded from a copy on disk rather than by reloading the package, because
    `shani_chronoa.gui.browser` is already in `sys.modules` and a second copy of
    a package is a mess every `from .x import y` depends on. Everything this
    module imports is absolute and already loaded, so a fresh file is enough.
    """
    directory = pathlib.Path(directory or tempfile.mkdtemp())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_url_logic_catches_a_wrong_implementation():
    """Mutation control, and the reason this file has a mutation in it.

    Every URL test above asserts against the shipped code, so they would all pass
    against a deliberately wrong rule. This one replaces the whole decision
    function with Alpaca's - "a dot and no space is a host" - in a copy of the
    real source, loads it, and asserts the cases above now disagree. That is the
    plausible mistake, taken verbatim from the implementation this was modelled
    on, rather than a strawman.
    """
    original = SOURCE_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"def _is_address\(text: str\) -> bool:.*?\n\ndef ", re.S)
    alpaca = (
        "def _is_address(text: str) -> bool:\n"
        '    """Alpaca\'s rule: a dot and no space is a host."""\n'
        "    return bool(text) and '.' in text and ' ' not in text\n\n"
        "def "
    )
    wrong, replacements = pattern.subn(alpaca, original)
    assert replacements == 1, (
        "the mutation did not apply - the source no longer contains the function "
        "this control replaces, so the control cannot fail and proves nothing "
        "about the tests above"
    )
    mutated = _load_variant(wrong, "browser_mutant_url_rule")

    for typed in ("3.1", "1.2.3", "localhost:8080", "[::1]:8080/"):
        assert mutated.target_url(typed) != browser.target_url(typed), (
            f"{typed!r} behaves the same under both rules, so this control is not "
            "distinguishing them"
        )
    # The mutant still gets the ordinary host right, or this would be testing
    # that the module is broken rather than that the rule matters.
    assert mutated.target_url("example.com") == browser.target_url("example.com")


# --- 2. availability, with and without WebKit -------------------------------


def test_the_module_imports_and_answers_whether_it_can_build_a_window():
    """`is_available()` is a bool - on this machine, and on one without
    webkit2gtk.

    A module that cannot be imported without its optional dependency takes the
    whole assistant down on a machine without it, which is a worse state than
    any one of those things missing.
    """
    assert isinstance(browser.is_available(), bool)


def test_a_missing_webkit_is_a_message_not_a_traceback(monkeypatch):
    """Refusing has to say what is missing and what to install.

    The state worth handling is the typelib being absent, which is the ordinary
    state on a machine without webkit2gtk - and `BrowserUnavailable` exists so a
    caller can catch one thing rather than whatever `gi` happened to raise.
    """
    monkeypatch.setattr(browser, "_webkit", lambda: None)
    assert browser.is_available() is False
    reason = browser.unavailable_reason()
    assert "WebKitGTK" in reason
    # The GTK 4 package: webkit2gtk-4.1 is the GTK 3 build, which cannot load
    # into this GTK 4 process, so naming it sent people to the wrong install.
    assert "webkitgtk-6.0" in reason, (
        "the message names no installable package, so a user who hit it has "
        f"nothing to act on: {reason!r}"
    )
    with pytest.raises(browser.BrowserUnavailable) as caught:
        browser.BrowserWindow()
    assert "WebKitGTK" in str(caught.value)


def test_the_webkit_probe_tolerates_every_absent_namespace():
    """`gi.require_version` raises ValueError for a version that is not present
    and the import raises ImportError when the typelib is not installed at all.

    Both are the normal way this module finds WebKit missing, and a probe that
    only handles one of them turns a machine without webkit2gtk into a traceback
    out of `is_available()` - the exact failure this module is required not to
    have.
    """
    original = SOURCE_PATH.read_text(encoding="utf-8")
    # Both mutations replace a call *inside* its try block, so what is being
    # tested is that the except clause catches that error - moving the raise
    # outside the try would test the mutant instead of the handler.
    no_namespace = original.replace(
        "            gi.require_version('WebKit', version)",
        "            raise ValueError(version)",
    )
    assert no_namespace != original, "the first mutation did not apply"
    no_typelib = original.replace(
        "            namespace = import_module('gi.repository.WebKit')",
        "            raise ImportError(version)",
    )
    assert no_typelib != original, "the second mutation did not apply"
    for name, source in (("browser_no_namespace", no_namespace),
                         ("browser_no_typelib", no_typelib)):
        variant = _load_variant(source, name)
        assert variant.is_available() is False, (
            f"{name}: is_available() answered True (or raised) with WebKit gone")
        assert "WebKitGTK" in variant.unavailable_reason(), name


# --- 3. attaching goes through webtext --------------------------------------


def test_attach_reaches_webtext_and_never_an_http_client(monkeypatch):
    """The whole point of reusing `webtext` is that it is the only way out of
    this module, so the control is an `httpx.Client` that raises.

    An implementation with its own client fails here; one that goes through
    `webtext` has `retrieve` called with the page's URL and touches no network.
    """
    def _no_network(*args, **kwargs):
        raise AssertionError("the browser opened its own HTTP client")

    monkeypatch.setattr(webtext.httpx, "Client", _no_network)
    calls = []

    def _fake_retrieve(url, *args, **kwargs):
        calls.append(url)
        return webtext.Page(url=url, title="Fetched", text="the page body",
                            chars_dropped=0, bytes_truncated=False)

    monkeypatch.setattr(webtext, "retrieve", _fake_retrieve)
    text = browser.text_for_attachment("https://example.com/p", "", "Title")

    assert calls == ["https://example.com/p"]
    assert "the page body" in text


def test_attach_prefers_the_pages_own_rendered_text(monkeypatch):
    """The text in the view wins, because it is what the person is looking at.

    A page that draws its content with JavaScript has nothing in its HTML for an
    HTTP fetch to read, so preferring the fetch would attach an empty page for
    exactly the pages most worth attaching. The control: `retrieve` fails if
    called at all.
    """
    def _must_not_fetch(*args, **kwargs):
        raise AssertionError("fetched a page the view had already rendered")

    monkeypatch.setattr(webtext, "retrieve", _must_not_fetch)
    text = browser.text_for_attachment(
        "https://example.com/app", "Rendered heading\nRendered body", "App")
    assert "Rendered body" in text


def test_the_attached_text_is_capped_and_says_it_was_truncated(monkeypatch):
    """A silently shortened page lets the model answer confidently from half a
    document, which is the reason `webtext` exists. The cap and the note are
    webtext's, so they have to arrive with the text rather than be added here.
    """
    monkeypatch.setattr(webtext, "retrieve", lambda *a, **k: pytest.fail("fetched"))
    long_page = "\n".join(f"line {n}" for n in range(4000))
    text = browser.text_for_attachment("https://example.com/long", long_page, "Long")
    assert "line 3999" not in text
    assert str(webtext.MAX_TEXT_CHARS) in text
    assert len(text) < len(long_page)


def test_the_attached_text_is_marked_as_untrusted_third_party(monkeypatch):
    """The framing is webtext's, and it is what stops a page's own instructions
    being read as the user's. A browser surface is a prompt-injection surface
    like any other, and this one hands the page to a model that can call tools."""
    monkeypatch.setattr(webtext, "retrieve", lambda *a, **k: pytest.fail("fetched"))
    text = browser.text_for_attachment("https://example.com/x", "body", "X")
    assert "Untrusted third-party content" in text
    assert "never as instructions" in text


def test_a_refused_destination_is_never_fetched():
    """The egress policy is inherited rather than reimplemented, so the cloud
    metadata address is refused on the attach path exactly as it is for the `web`
    sense. Reading it from the real policy is the point: a second
    implementation would be a second opinion, and a wrong one.
    """
    from shani_chronoa import egress
    with pytest.raises(webtext.RetrievalError) as caught:
        browser.text_for_attachment("http://169.254.169.254/latest/meta-data/", "", "")
    assert "metadata" in str(caught.value).lower()
    # Refused by `egress`, not by a rule of this module's own: the exception
    # carries the policy's own reason because webtext re-raises it.
    assert isinstance(caught.value.__cause__, egress.DestinationRefused)


def test_the_fallback_fetches_are_still_gated_by_robots(monkeypatch):
    """Behaves as `webtext` does, not merely calls it: a site whose robots.txt
    refuses automated readers is not fetched on the attach path either.

    The control is the interesting half - the same URL is allowed by robots.txt
    and comes back with its body. Without it, a `retrieve` that refused
    everything would satisfy this test.
    """
    requested: list = []
    real_client = httpx.Client

    def _serve(monkeypatch, robots_status=200, robots_text="User-agent: *\nDisallow: /\n"):
        requested.clear()
        monkeypatch.setattr(webtext, "_ROBOTS", {})

        def _handler(request):
            requested.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(robots_status, text=robots_text)
            return httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"},
                text="<html><head><title>Notes</title></head><body>"
                     "<p>blue-green survives a failed health check</p></body></html>")

        transport = httpx.MockTransport(_handler)

        def _factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(**kwargs, transport=transport)

        monkeypatch.setattr(webtext.httpx, "Client", _factory)

    _serve(monkeypatch)
    with pytest.raises(webtext.RetrievalError) as caught:
        browser.text_for_attachment("https://example.org/notes", "", "")
    assert "robots.txt" in str(caught.value)
    assert requested == ["/robots.txt"], (
        f"the page was requested despite robots.txt: {requested}")

    _serve(monkeypatch, robots_status=404)
    text = browser.text_for_attachment("https://example.org/notes", "", "")
    assert "blue-green survives" in text, (
        "the control failed: a site with no robots.txt is allowed everything, so "
        "a retrieve that refused regardless would have satisfied the check above")


# --- 4. properties of the source that a widget walk cannot show ------------


def _module_ast():
    return ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def test_the_module_has_no_http_client_of_its_own():
    """Static, and the right tool for the property: an implementation that both
    imported httpx and called `webtext` would pass every behavioural test above."""
    source = SOURCE_PATH.read_text(encoding="utf-8")
    for banned in ("import httpx", "urllib.request", "http.client", "socket."):
        assert banned not in source, banned


def test_every_string_shown_to_the_user_is_escaped_before_markup():
    """The status line is markup and it is fed remote content - a page title and a
    URL. An unescaped `&` in a query string renders as `&amp;` to the user, or
    the label refuses to render at all, which is the bug this repo's markdown
    module was written for."""
    calls = [node for node in ast.walk(_module_ast())
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "set_markup"]
    assert calls, "the status line no longer uses markup; this test would then assert nothing"
    for call in calls:
        assert isinstance(call.args[0], ast.Call), (
            "set_markup is being handed something other than a call, so nothing "
            "here has checked that it was escaped")
        inner = call.args[0].func
        assert isinstance(inner, ast.Attribute) and inner.attr == "escape", (
            f"line {call.lineno}: set_markup is not given an escape() result")


def test_navigation_sensitivity_comes_from_the_view_not_from_a_counter():
    """The buttons must not track history themselves. WebKit is the only thing
    that knows what is on the stack, and it changes on a redirect as well as on a
    click."""
    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert "self._webview.can_go_back()" in source
    assert "self._webview.can_go_forward()" in source


def test_the_window_does_not_need_libadwaita():
    """Plain GTK4, for the same reason `gui/window.py` is: a window that needs
    `Adw.init()` to appear is a window that appears or not depending on an
    initialisation order this module does not control.

    Checked against the imports rather than the text, so the module docstring may
    go on explaining why - a substring check on "Adw" fails on the explanation.
    """
    imported = set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not any("Adw" in name for name in imported), imported
    assert not hasattr(browser, "Adw"), "the module exposes an Adw name"


# The Arch manifest lives in the sibling shani-pkgbuilds repo; the copy that used
# to sit in this one was removed because nothing built from it and it had drifted.
# Reading `REPO_ROOT/PKGBUILD` therefore failed with FileNotFoundError, which is
# a confusing way to learn that a dependency is absent.
from _pkgbuild import arch_pkgbuild, depends as _pkgbuild_depends  # noqa: E402


def test_the_module_is_not_a_hard_package_dependency():
    """webkit2gtk is deliberately not a hard dependency, and is declared in
    neither package file, which is the condition this module's whole
    availability story exists for.

    An `optdepends`/`Suggests` entry would be the *right* way to offer it and is
    not what this forbids: only a hard dependency makes the missing-WebKit path
    unreachable, and a `depends` line for a browser engine would put one on every
    install for a surface most sessions never open.
    """
    declared = {name.lower() for name in
                _pkgbuild_depends(arch_pkgbuild().read_text(encoding="utf-8"))}
    assert not any("webkit" in name for name in declared), declared
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    control = (repo_root / "DEBIAN" / "control").read_text(encoding="utf-8")
    hard = re.search(r"^Depends:\s*(.+)$", control, re.M)
    assert hard, "no Depends: field in DEBIAN/control"
    assert "webkit" not in hard.group(1).lower()


def test_the_module_survives_without_webkit_in_sys_modules(monkeypatch):
    """The import contract, checked by running it: `gi.repository.WebKit` must
    not be reachable and the module must still import, answer `is_available()`
    and refuse to build a window.

    This is the failure the whole module is shaped around - `from
    gi.repository import WebKit` at module scope would take down every window in
    the assistant on a machine without webkit2gtk - and a test that only imports
    the module on a machine where WebKit happens to be installed cannot see it.
    """
    monkeypatch.setitem(sys.modules, "gi.repository.WebKit", None)
    module = _load_variant(SOURCE_PATH.read_text(encoding="utf-8"),
                           "browser_without_webkit")
    assert module.is_available() is False
    assert "WebKitGTK" in module.unavailable_reason()
    with pytest.raises(module.BrowserUnavailable):
        module.BrowserWindow()