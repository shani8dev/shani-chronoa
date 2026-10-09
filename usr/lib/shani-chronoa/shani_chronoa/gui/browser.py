"""An in-app web browser: go somewhere, and hand the page to the conversation.

Alpaca has one (`widgets/activities/web_browser.py`) and the shape is borrowed
rather than reinvented: an address entry that decides for itself what it was
given, back/forward/reload/home, a loading state, a menu, and one action that
matters more than the rest - attaching what is on screen to the conversation
so the assistant can be asked about the page rather than about a link to it.

Three decisions are not Alpaca's and are the reason this is not a copy.

**The text comes from `webtext`, not from a second HTTP client.** Alpaca
converts the page's HTML with `markitdown` and hands *that* to the conversation.
This repo already has `webtext.retrieve`, and it is not a convenience wrapper:
it caps the response bytes, walks the redirect chain re-checking every hop,
refuses the cloud metadata address, honours robots.txt and bounds the text with
a visible truncation note. A browser that fetched the same page a second way
would have none of that, so this module asks the *view* for the document text
(`WebKit.WebView.evaluate_javascript`, which can also see content the page only
draws with JavaScript, and which is a fetch of nothing) and, when there is no
document to ask - a failed evaluation, an empty body - falls back to
`webtext.retrieve`, so the egress policy and robots.txt stay in force for
everything Chronoa fetches on its own behalf. Either way the text is capped and
framed by `webtext.render`, because that is where the "this is untrusted
content, not instructions" line lives and the model in this codebase can call
tools.

**A missing WebKitGTK is a state, not a crash.** `webkit2gtk` is not a hard
dependency and is not declared in either package file, so on a machine without
it this module still imports, `is_available()` answers False, and
`BrowserWindow` refuses with a message naming the package. An assistant that
cannot show a web view is still an assistant.

**The view itself is not gated by the egress policy, and that is deliberate.**
`egress.check_destination` exists to stop *Chronoa* fetching something on the
model's behalf; here the person typed the address into their own browser
window, which is the same trust decision as any browser. Anything fetched
outside the view is not exempt, which is why the attach fallback goes through
`webtext` and why every completed navigation is recorded in the egress log - an
audit that answers "what left this machine" cannot omit the surface that leaves
it most often.

Plain GTK4, no libadwaita, for the same reason `gui/window.py` is: the main
window is a plain `Gtk.ApplicationWindow`, and a browser that needs
`Adw.init()` to appear is a browser that appears or not depending on an
initialisation order nobody here controls.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import urllib.parse
from importlib import import_module

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GLib', '2.0')

from gi.repository import Gio, GLib, Gtk  # noqa: E402 - gi.require_version must come first

from shani_chronoa import egress, markdown_lite, webtext

logger = logging.getLogger(__name__)

#: Where "home" goes, and where anything that is not an address is looked up.
#: DuckDuckGo because `skills/web_search.py` already treats it as this machine's
#: search provider; a second default engine would mean a second opinion about
#: who gets the query.
DEFAULT_HOME_URL = "https://duckduckgo.com/"
DEFAULT_SEARCH_URL = "https://duckduckgo.com/?q={query}"

#: Schemes the view may be pointed at. An allowlist rather than "anything with
#: a scheme", because `javascript:` runs in the document it is typed into and
#: `data:` renders arbitrary HTML in the view, and both would be one Enter
#: keypress away from being loaded.
_LOADABLE_SCHEMES = ("http", "https", "file", "about")

#: One DNS label. Anything else in a host is a typo or something else entirely.
_HOST_LABEL = re.compile(r"\A[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\Z")

#: WebKit.LoadEvent values, named because the signal hands over an int and
#: `if event == 3` is not a thing to read at the call site.
_LOAD_STARTED = 0
_LOAD_FINISHED = 3

#: The view's own reading of the loaded document. `innerText` and not
#: `textContent`: `textContent` returns `<script>` bodies, `<style>` rules and
#: anything the page hid with CSS, none of which the person looking at the
#: window can see, and the first two are code rather than prose.
VISIBLE_TEXT_JS = "document.body ? document.body.innerText : ''"

#: GIR namespace versions accepted for WebKit. `WebKit-6.0` is the GTK 4 build
#: (Arch `webkitgtk-6.0`, Debian/Ubuntu `gir1.2-webkit-6.0`) and the one that
#: loads here. `4.1` is the GTK 3 build: it stays listed so a machine with only
#: that installed is reported as "no WebKit" by the version check below rather
#: than by a crash, since it cannot load beside GTK 4.
_WEBKIT_NAMESPACES = ("6.0", "5.0", "4.1")

_WEBKIT = None
_WEBKIT_VERSION = ""
_WEBKIT_TRIED = False


class BrowserUnavailable(RuntimeError):
    """WebKitGTK is not installed, so there is no browser surface to build.

    A distinct type so a caller can offer to install the package rather than
    report a traceback to somebody who only asked for a web view.
    """


def _webkit():
    """The `gi.repository.WebKit` namespace, or None when it is not installed.

    Never raises. A missing typelib raises ImportError and a namespace version
    that is not present raises ValueError; both are the ordinary state of a
    machine without webkit2gtk, and neither may be allowed to propagate out of
    an import - this module is reachable from the assistant's own GUI package,
    where an ImportError at module scope would take the whole window down for a
    missing browser.
    """
    global _WEBKIT, _WEBKIT_VERSION, _WEBKIT_TRIED
    if _WEBKIT_TRIED:
        return _WEBKIT
    _WEBKIT_TRIED = True
    for version in _WEBKIT_NAMESPACES:
        try:
            gi.require_version('WebKit', version)
        except (ImportError, ValueError):
            continue
        try:
            namespace = import_module('gi.repository.WebKit')
        except ImportError:
            continue
        _WEBKIT = namespace
        _WEBKIT_VERSION = version
        logger.info("WebKitGTK available as namespace %s", version)
        return _WEBKIT
    logger.info("WebKitGTK is not installed; the in-app browser is unavailable")
    return None


def is_available() -> bool:
    """Whether a browser window can be built at all on this machine."""
    return _webkit() is not None


def unavailable_reason() -> str:
    """The message to show instead of a browser, naming the package to install."""
    found = ", ".join(_WEBKIT_NAMESPACES)
    return (
        "The in-app browser needs WebKitGTK, which is not installed "
        f"(looked for the WebKit {found} namespaces). On Arch that is the "
        "'webkitgtk-6.0' package, on Debian and Ubuntu 'gir1.2-webkit-6.0' - the "
        "GTK 4 builds; 'webkit2gtk-4.1' is the GTK 3 one and cannot load into "
        "this window. Every other part of Chronoa works without it."
    )


def webkit_version() -> str:
    """The GIR namespace version in use, or "" when WebKit is absent."""
    _webkit()
    return _WEBKIT_VERSION


# --- what a line typed into the address bar means ---------------------------


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _is_address(text: str) -> bool:
    """Whether `text` names somewhere to go, rather than something to look up.

    The last label is what separates the two, and that is the whole argument:
    `example.co.uk` and `192.168.1.1` are places, `3.1` and `1.2.3` are version
    numbers, and loading `https://3.1/` answers nothing while searching for it
    does. `localhost` counts without a dot because it is the one bare hostname
    worth loading, and it is what a person developing against this machine types.
    """
    if not text or any(character.isspace() for character in text):
        return False
    authority = text.split('/', 1)[0]
    if authority.startswith('['):
        # An IPv6 literal: `partition(":")` would split inside the address.
        closing = authority.find(']')
        return closing > 1 and _is_ip_literal(authority[1:closing])
    host, _, port = authority.partition(':')
    if port and not port.isdigit():
        return False
    if not host:
        return False
    if host.lower() == 'localhost' or _is_ip_literal(host):
        return True
    labels = host.rstrip('.').split('.')
    return len(labels) >= 2 and all(_HOST_LABEL.match(label) for label in labels) \
        and labels[-1][:1].isalpha()


def search_target(search_url: str, query: str) -> str:
    """A query turned into a URL through the configured search endpoint.

    `{query}` is substituted when the template carries one; otherwise the
    encoded query is appended, which is the shape `web_search.SEARCH_ENDPOINT`
    already has, so one configured endpoint serves both callers without either
    of them parsing the other's format.
    """
    encoded = urllib.parse.quote(query.strip(), safe='')
    if '{query}' in search_url:
        return search_url.replace('{query}', encoded)
    return search_url + encoded


def target_url(text: str, search_url: str = DEFAULT_SEARCH_URL) -> str:
    """Where one line from the address bar goes.

    Three cases, in this order, and the order is the behaviour: a URL of a
    scheme the view may load is loaded as written; a bare host gets `https://`;
    everything else is a query and goes to `search_url`.

    Empty in, empty out. Returning the home page would mean a stray Enter in an
    empty field silently loads a remote page and takes the focus, and the
    caller asked this function because it could not tell an empty entry from a
    real one.
    """
    text = (text or '').strip()
    if not text:
        return ''
    scheme = text.split(':', 1)[0].lower() if ':' in text else ''
    if scheme in _LOADABLE_SCHEMES:
        return text
    if _is_address(text):
        return 'https://' + text
    return search_target(search_url, text)


# --- the page's text ---------------------------------------------------------


def page_from_text(url: str, text: str, title: str = '') -> webtext.Page:
    """Wrap text already read out of a document in the `Page` a fetch returns.

    The line-per-block collapsing is `webtext._TextExtractor.text()`'s, so text
    from the view is shaped like text from a fetch, and the cap and the
    `chars_dropped` count are `webtext`'s own: a page attached to a
    conversation is bounded by the same ceiling as one handed to the model as a
    tool result. Without that, `document.body.innerText` on a documentation
    page is an unbounded paste into the context budget.
    """
    lines = (' '.join(line.split()) for line in str(text or '').splitlines())
    body = '\n'.join(line for line in lines if line)
    clipped = body[:webtext.MAX_TEXT_CHARS]
    return webtext.Page(
        url=url,
        title=title or '',
        text=clipped,
        chars_dropped=len(body) - len(clipped),
        bytes_truncated=False,
        start=0,
    )


def text_for_attachment(url: str, dom_text: str = '', title: str = '') -> str:
    """The page's text, ready to hand to `on_attach(title, url, text)`.

    `dom_text` is what the view read out of the loaded document and it wins when
    it is there: the rendered page is what the person is looking at, and an HTTP
    fetch cannot see anything the page only draws with JavaScript. When it is
    absent - no document to ask, an empty body, an evaluation the page refused -
    the URL is fetched through `webtext.retrieve`, which is the one path that
    applies the egress policy and robots.txt. There is no second HTTP client in
    this module.

    The return value is `webtext.render(page)`: the page's own text, under the
    same character cap as a tool result, with the note when characters were
    dropped and the line telling the model the content is data rather than
    instructions. A caller that appends this to a conversation is handing it to
    a model that can call tools, and that framing is not decoration.

    Raises `webtext.RetrievalError` when the fallback fetch fails; the message is
    written for the model and reads fine in a status line too.
    """
    if str(dom_text or '').strip():
        return webtext.render(page_from_text(url, dom_text, title))
    return webtext.render(webtext.retrieve(url))


# --- the window --------------------------------------------------------------


class BrowserWindow(Gtk.Window):
    """A web view with an address bar, and one action that feeds the assistant.

    `on_attach(title, url, text)` is called with what is on screen; `text` is
    the `webtext.render` block described on `text_for_attachment`, not a bare
    string, so the caller does not have to re-add the framing that says the page
    is third-party content.

    `home_url` is loaded on construction, so this fetches as soon as it is
    built. `""` is a window that loads nothing, and `about:blank` one that
    loads a page without touching the network - the distinction matters in a
    test, and to anybody who wants a browser surface that stays off the wire.
    """

    def __init__(self, application=None, *, on_attach=None,
                 home_url: str = DEFAULT_HOME_URL,
                 search_url: str = DEFAULT_SEARCH_URL) -> None:
        webkit = _webkit()
        if webkit is None:
            raise BrowserUnavailable(unavailable_reason())
        super().__init__(application=application)
        self._webkit_ns = webkit
        self._on_attach = on_attach
        self._search_url = search_url
        self._home_url = home_url
        self._webview = None
        #: Set by `gui/browser_driver.py` only while the model is driving this
        #: window: a callable `url -> reason` ("" to allow). The person's own
        #: typing and clicking are never gated - see the module docstring.
        self.navigation_guard = None
        self._build_ui()
        self.connect("notify::visible", self._on_shown)
        self.load_url(home_url)

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        self.set_title("Chronoa Browser")
        self.set_default_size(960, 700)
        self.set_size_request(420, 320)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)

        self._back = self._tool_button("go-previous-symbolic", "Back", self.go_back)
        self._forward = self._tool_button("go-next-symbolic", "Forward", self.go_forward)
        self._reload = self._tool_button("view-refresh-symbolic", "Reload", self.reload)
        self._home = self._tool_button("go-home-symbolic", "Go home", self.go_home)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Search or enter an address")
        self._entry.set_hexpand(True)
        # A placeholder is not an accessible name; without this a screen reader
        # announces the field as "text" (shani-testbed a11y-lint).
        self._entry.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Search or enter an address"])
        self._entry.connect("activate", self._on_entry_activate)
        focus = Gtk.EventControllerFocus()
        # Selecting everything on focus is what makes typing a new address
        # possible at all; done from an idle callback because the entry is not
        # in its normal state until the focus event has been processed.
        focus.connect("enter", lambda *_args: GLib.idle_add(self._select_entry_text))
        self._entry.add_controller(focus)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        self._spinner.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Loading the page"])

        self._menu_button = Gtk.MenuButton()
        self._menu_button.set_icon_name("view-more-symbolic")
        self._menu_button.add_css_class("flat")
        self._menu_button.set_tooltip_text("Page actions")
        self._menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Page actions"])
        self._menu_button.set_popover(self._build_menu())

        for widget in (self._back, self._forward, self._reload, self._home,
                       self._entry, self._spinner, self._menu_button):
            toolbar.append(widget)
        root.append(toolbar)

        self._status = Gtk.Label()
        self._status.set_xalign(0.0)
        self._status.set_wrap(True)
        self._status.add_css_class("dim-label")
        self._status.set_margin_start(10)
        self._status.set_margin_end(10)
        self._status.set_margin_bottom(4)
        self._status.set_visible(False)
        root.append(self._status)

        # What Chronoa is doing in this window, while it is doing it. The model
        # drives this browser in front of the person, so every action it takes
        # is named here as it happens - and a refusal is named too, in red.
        self._activity = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        strip.add_css_class("chronoa-activity")
        for side in ("start", "end"):
            getattr(strip, f"set_margin_{side}")(10)
        strip.set_margin_bottom(4)
        self._activity_spinner = Gtk.Spinner(spinning=True)
        self._activity_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        strip.append(self._activity_spinner)
        strip.append(self._activity_label)
        self._activity.set_child(strip)
        self._activity_hide = 0
        root.append(self._activity)
        provider = Gtk.CssProvider()
        provider.load_from_string(
            ".chronoa-activity { background: alpha(@accent_bg_color, 0.14); border-radius: 8px;"
            " padding: 6px 10px; font-weight: 600; }"
            ".chronoa-activity.refused { background: alpha(@error_bg_color, 0.16); color: @error_color; }")
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._webview = self._build_webview()
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self._webview)
        root.append(scroller)

        self.connect("close-request", self._on_close_request)
        self._update_navigation_state()

    def _tool_button(self, icon: str, tooltip: str, handler) -> Gtk.Button:
        button = Gtk.Button()
        button.set_icon_name(icon)
        button.add_css_class("flat")
        button.set_tooltip_text(tooltip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        button.connect("clicked", lambda _b: handler())
        return button

    def _build_menu(self) -> Gtk.Popover:
        """The page menu, as four labelled buttons rather than a `Gio.Menu`.

        Four items with a callback each is what `Gio.Menu` models worst - it
        needs a GObject property class per item and a dispatch switch - and the
        one item that matters ("attach") wants a CSS class the model cannot
        carry.
        """
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        for icon, label, handler, css in (
            ("view-refresh-symbolic", "Reload", self.reload, None),
            ("go-next-symbolic", "Open in browser", self.open_in_browser, None),
            ("go-home-symbolic", "Go home", self.go_home, None),
            ("mail-attachment-symbolic", "Attach this page to the conversation",
             self.attach, "suggested-action"),
        ):
            button = Gtk.Button()
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_pixel_size(16)
            content.append(image)
            content.append(Gtk.Label(label=label, xalign=0.0))
            button.set_child(content)
            button.set_tooltip_text(label)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            if css:
                button.add_css_class(css)
            button.connect("clicked", lambda _b, action=handler: self._run_menu_action(action))
            box.append(button)
        popover.set_child(box)
        return popover

    def _build_webview(self):
        view = self._webkit_ns.WebView()
        settings = self._webkit_ns.Settings()
        # A fullscreen page hides every control above it, including the one that
        # attaches it, and there is no way back out of fullscreen from a title
        # bar that is no longer on screen.
        settings.set_enable_fullscreen(False)
        # The developer extras menu is a debugging surface, not a feature.
        settings.set_enable_developer_extras(False)
        view.set_settings(settings)
        view.connect("load-changed", self._on_load_changed)
        view.connect("load-failed", self._on_load_failed)
        view.connect("notify::title", self._on_view_title)
        view.connect("create", self._on_create)
        view.connect("decide-policy", self._on_decide_policy)
        return view

    def _run_menu_action(self, action) -> None:
        """Run a menu action and close the popover.

        Closing first matters for `attach`: the popover grabs the pointer, and a
        status line saying "Attached ..." underneath a still-open menu is an
        answer nobody reads.
        """
        if self._menu_button.get_popover() is not None:
            self._menu_button.get_popover().popdown()
        action()

    # -- navigation --------------------------------------------------------

    def _on_shown(self, _window, _param) -> None:
        """Put the caret in the address bar when the window appears.

        A browser window that opens without the focus in its address bar costs a
        click before the first thing anybody does with it, and the shortcut that
        opens it usually leaves the focus in another application.
        """
        if self.get_visible():
            self._entry.grab_focus()

    def webview(self):
        """The real `WebKit.WebView`. None only before construction finishes."""
        return self._webview

    def current_url(self) -> str:
        """The address the view is on, falling back to what was typed."""
        if self._webview is not None:
            loaded = self._webview.get_uri()
            if loaded:
                return loaded
        return self._entry.get_text().strip()

    def current_title(self) -> str:
        if self._webview is None:
            return ''
        return self._webview.get_title() or ''

    def load_url(self, url: str) -> None:
        if self._webview is None or not url:
            return
        self._webview.load_uri(url)

    def go_home(self) -> None:
        self.load_url(self._home_url)

    def reload(self) -> None:
        if self._webview is not None:
            self._webview.reload()

    def go_back(self) -> None:
        # Guarded even though the button is already insensitive: the shortcut
        # and the click are not the only callers, and `go_back` on a one-entry
        # history is a WebKit warning rather than a no-op.
        if self._webview is not None and self._webview.can_go_back():
            self._webview.go_back()

    def go_forward(self) -> None:
        if self._webview is not None and self._webview.can_go_forward():
            self._webview.go_forward()

    def open_in_browser(self) -> None:
        """Hand the current page to whatever the desktop has registered for it."""
        url = self.current_url()
        if not url:
            self._set_status("There is no page to open yet.")
            return
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as exc:
            # No handler for this scheme is the ordinary reason, and it has to
            # be said: a silent failure here looks exactly like a browser that
            # opened and closed itself.
            self._set_status(
                f"Nothing on this machine can open {url} ({exc.message}).", error=True)

    # -- view signals ------------------------------------------------------

    def _on_entry_activate(self, entry) -> None:
        self.load_url(target_url(entry.get_text(), self._search_url))

    def _select_entry_text(self) -> bool:
        self._entry.select_region(0, -1)
        return False

    def _on_load_changed(self, webview, event) -> None:
        url = webview.get_uri()
        if url and not self._entry.has_focus():
            # The entry follows the view rather than the other way round: a click
            # through to another page must not need retyping. Not while the entry
            # has focus, though, or a redirect arriving mid-typing eats what is
            # being typed.
            self._entry.set_text(url)
        self._update_navigation_state()
        if event == _LOAD_STARTED:
            self._set_loading(True)
        elif event == _LOAD_FINISHED:
            self._set_loading(False)
            self._record_navigation(url or self.current_url())

    def _on_load_failed(self, webview, event, uri, error) -> None:  # noqa: ARG002 - signal contract
        self._set_loading(False)
        # WebKit reports a cancelled load as a failure too, and "could not reach"
        # is the wrong thing to tell somebody who navigated away.
        if error is not None and error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED):
            return
        self._set_status(f"{uri} did not load ({error.message if error else 'no reason given'}).",
                         error=True)

    def _on_view_title(self, webview, _param) -> None:
        title = webview.get_title() or ''
        self.set_title(f"{title} - Chronoa Browser" if title else "Chronoa Browser")

    def _on_create(self, _webview, navigation_action):
        """Refuse a new window and keep the navigation in this one.

        Returning None is how a WebKit `create` handler declines: WebKit would
        otherwise open a `target=_blank` page in a window with no toolbar, no
        back button and nothing to attach from, which is the same surface
        without the parts that make it useful.
        """
        uri = navigation_action.get_request().get_uri()
        if uri:
            self.load_url(uri)
        return None

    def _on_decide_policy(self, _webview, decision, decision_type) -> bool:
        """Hold a model-driven navigation to the egress policy.

        Only while `navigation_guard` is set, which is only while a `browse`
        call is in flight. That covers what the model asked for directly and
        what it caused - a clicked link, a submitted form, a server redirect -
        because each of those reaches WebKit as a navigation decision here.
        """
        guard = self.navigation_guard
        if guard is None:
            return False
        kinds = self._webkit_ns.PolicyDecisionType
        if decision_type not in (kinds.NAVIGATION_ACTION, kinds.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri() or ""
        if guard(uri):
            decision.ignore()
            return True
        return False

    def _update_navigation_state(self) -> None:
        """Read back/forward sensitivity from the view, not from a guess.

        The entry count is the only thing that knows, and it changes on a
        redirect as well as on a click, so it is read here rather than tracked.
        """
        can_back = bool(self._webview is not None and self._webview.can_go_back())
        can_forward = bool(self._webview is not None and self._webview.can_go_forward())
        self._back.set_sensitive(can_back)
        self._forward.set_sensitive(can_forward)

    def _set_loading(self, loading: bool) -> None:
        self._spinner.set_visible(loading)
        if loading:
            self._spinner.start()
        else:
            self._spinner.stop()

    def _record_navigation(self, url: str) -> None:
        """One egress line per navigation the view finished.

        `privacy_mode` is read at the call site and not left at its default: a
        call site that omits the flag computes `violation=False` structurally,
        which is how `webtext.retrieve` was uninstrumented while looking
        instrumented. A remote page loaded with privacy mode on really is
        something leaving the machine, and saying so is this log's whole job.

        Only http(s) is recorded. Measured by driving the real window: a
        `file://` page passes `'://' in url`, and `egress.record` then reports it
        as a remote host with an empty name and logs
        `EGRESS: browser:navigate sent a request to  while privacy mode was ON`
        - a violation alarm for a page read off this machine's own disk. An
        alarm that fires for local files is an alarm nobody reads.
        """
        scheme = url.split(':', 1)[0].lower() if ':' in url else ''
        if scheme not in ('http', 'https'):
            return
        # **A `purpose`, so the privacy panel can say what this line was.**
        # It renders `{purpose}: {method} {url} - {bytes_out} bytes`, and without
        # one a navigation read as a bare `GET https://...` - accurate, and
        # indistinguishable in that panel from any other outbound request, which
        # is the whole question the panel is opened to answer.
        egress.record("browser:navigate", url,
                      privacy_mode=egress.privacy_mode_enabled(),
                      purpose="page-navigation")

    # -- attaching ---------------------------------------------------------

    def attach(self) -> None:
        """Read the page and hand it to `on_attach(title, url, text)`.

        Reading is the view's JavaScript, so it is asynchronous and the button
        goes insensitive until an answer or an error arrives - a second press
        would otherwise attach the same page twice, which in a conversation
        means the same turn twice.
        """
        if self._on_attach is None:
            self._set_status("Nothing is waiting for this page, so there is nowhere "
                             "to attach it to.", error=True)
            return
        if self._webview is None:
            return
        self._menu_button.set_sensitive(False)
        self._set_status("Reading the page…")
        self._webview.evaluate_javascript(
            VISIBLE_TEXT_JS, -1, None, None, None, self._on_dom_text, None)

    def _on_dom_text(self, webview, result, _user_data) -> None:
        self._menu_button.set_sensitive(True)
        try:
            dom_text = webview.evaluate_javascript_finish(result).to_string()
        except GLib.Error as exc:
            # A document that refuses evaluation (a cross-origin frame, a page
            # that cleared its own scripts) leaves the HTTP fallback as the only
            # route to the text - and that fallback still goes through webtext.
            logger.info("could not read the page through the view: %s", exc.message)
            dom_text = ''
        self._attach_current(dom_text)

    def _attach_current(self, dom_text: str) -> None:
        url = self.current_url()
        title = self.current_title()
        try:
            text = text_for_attachment(url, dom_text, title)
        except webtext.RetrievalError as exc:
            self._set_status(f"Could not read {url}: {exc}", error=True)
            return
        except Exception as exc:                              # noqa: BLE001 - shown, not raised
            logger.exception("reading %s failed", url)
            self._set_status(f"Could not read {url}: {exc}", error=True)
            return
        try:
            self._on_attach(title, url, text)
        except Exception:                                   # noqa: BLE001 - the read succeeded
            logger.exception("the attach callback failed for %s", url)
            self._set_status("The page was read but could not be attached.",
                             error=True)
            return
        self._set_status(f"Attached {url}")

    # -- chrome ------------------------------------------------------------

    def show_activity(self, text: str, refused: bool = False) -> None:
        """Name what Chronoa is doing in this window; hides after a quiet spell."""
        strip = self._activity.get_child()
        (strip.add_css_class if refused else strip.remove_css_class)("refused")
        self._activity_spinner.set_visible(not refused)
        self._activity_label.set_label(f"Chronoa: {text}")
        self._activity.set_reveal_child(True)
        if self._activity_hide:
            GLib.source_remove(self._activity_hide)
        self._activity_hide = GLib.timeout_add(4000 if refused else 2500, self._hide_activity)

    def _hide_activity(self) -> bool:
        self._activity_hide = 0
        self._activity.set_reveal_child(False)
        return False

    def activity_text(self) -> str:
        """What the strip says now, '' when hidden - for tests and the status."""
        return self._activity_label.get_label() if self._activity.get_reveal_child() else ""

    def _set_status(self, text: str, error: bool = False) -> None:
        """The one line under the toolbar: notices and failures both.

        Markup, so the text is escaped first. A page title and a URL are remote
        content, and one unescaped `&` in a query string is enough to make the
        label render `&amp;` to the user or refuse to render at all.
        """
        if error:
            self._status.add_css_class("error")
        else:
            self._status.remove_css_class("error")
        self._status.set_markup(markdown_lite.escape(text))
        self._status.set_visible(bool(text))

    def _on_close_request(self, _window) -> bool:
        """Close, and take the web process with it.

        WebKit runs pages in a separate process; a window closed without
        `terminate_web_process()` leaves it running with nothing driving it,
        which is why the process outlives the window rather than exiting with it.
        """
        if self._webview is not None:
            try:
                self._webview.terminate_web_process()
            except Exception as exc:                          # noqa: BLE001 - closing must not fail
                logger.debug("terminating the web process failed: %s", exc)
        return False


def open_browser(application=None, *, on_attach=None,
                 home_url: str = DEFAULT_HOME_URL,
                 search_url: str = DEFAULT_SEARCH_URL) -> BrowserWindow:
    """Build and present a `BrowserWindow`, or raise `BrowserUnavailable`.

    The one call a caller needs, so that "is there a browser here" is answered
    by `is_available()` and never by catching an exception out of a constructor.
    """
    window = BrowserWindow(application, on_attach=on_attach,
                           home_url=home_url, search_url=search_url)
    window.present()
    return window