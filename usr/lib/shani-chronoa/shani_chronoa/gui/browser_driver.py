"""The `browse` skill's actions, performed in the in-app browser window.

When Chronoa's window is running, `browse` drives `gui/browser.py`'s WebKit
window instead of a headless Chromium, so the person sees every page the model
opens and every field it fills, and can stop it by closing the window. This
module is that path; `skills/browse.py` picks it when `browser_bridge` has a
provider and falls back to Chromium when it does not.

It runs in Chronoa's own process (`browse` is on `tools._LOCAL_TOOLS` while the
window is up), outside the skill sandbox, and the window carries the person's
own cookies and sign-ins. Three rules follow from that, and they are why this
is narrower than the Chromium path:

- **Every model-driven navigation is checked.** The address the model asks for
  is checked before anything loads, and while an action runs the window's
  `navigation_guard` holds every navigation it causes - a clicked link, a
  submitted form, a redirect - to the same check: http(s) only (no `file://`,
  which would read this machine's files into a page) and
  `egress.check_destination`. The person's own typing and clicking are not
  gated, as `gui/browser.py` explains.
- **No arbitrary script.** `evaluate` is refused here: a script in a page that
  holds the person's sign-ins can read anything on it and send it anywhere with
  `fetch`, which no navigation check would see. The fixed actions below run
  fixed scripts whose only input is a JSON-encoded selector or text.
- **Screenshots go to Chronoa's own folder**, not to a path the model names,
  because this code can write wherever the person can.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from gi.repository import GLib

from shani_chronoa import browser_bridge, egress, webtext
from shani_chronoa.gui import browser as _browser

logger = logging.getLogger(__name__)

#: What this path does. Anything else gets `UNSUPPORTED`'s sentence.
ACTIONS = ("navigate", "get_text", "click", "type", "select", "scroll", "back",
           "forward", "reload", "stop", "get_title", "get_url", "screenshot",
           "wait", "wait_text")

UNSUPPORTED = {
    "evaluate": ("the in-app browser carries your own sign-ins, and a script run in "
                 "it could read the page and send it anywhere, so it runs no "
                 "arbitrary JavaScript. get_text, get_title and get_url read the page"),
    "key": ("the in-app browser does not send raw key presses; type text into a "
            "field, or click the button the key would have pressed"),
}
_TABS = "the in-app browser is one window with no tabs"
for _name in ("new_tab", "close_tab", "switch_tab", "list_tabs"):
    UNSUPPORTED[_name] = _TABS
for _name in ("hover", "drag", "upload_file"):
    UNSUPPORTED[_name] = f"'{_name}' needs real pointer input, which the in-app browser does not fake"

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_SETTLE = 1.5


# --- what the model may open --------------------------------------------------


def model_may_open(url: str) -> str:
    """Why the model may not open `url` in the in-app browser, or "" if it may."""
    if url == "about:blank":
        return ""
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError as exc:
        return f"the address could not be parsed ({exc})"
    if scheme not in ("http", "https"):
        return (f"the in-app browser opens only http and https pages for the model, "
                f"not {scheme or 'an address with no scheme'}: it holds your own "
                f"files and sign-ins")
    try:
        egress.check_destination(url)
    except egress.DestinationRefused as exc:
        return f"the egress policy refuses it: {exc}"
    return ""


# --- talking to the main thread -----------------------------------------------


def _on_main_thread() -> bool:
    return (threading.current_thread() is threading.main_thread()
            or GLib.MainContext.default().is_owner())


def _wait_until(predicate: Callable[[], bool], timeout: float) -> bool:
    """Wait for `predicate`, running the main loop ourselves when we are on it."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        if _on_main_thread():
            context.iteration(False)
        time.sleep(0.02)
    return True


def _window():
    return browser_bridge.window()


def _main(fn: Callable[[Any], Any], timeout: float = 15.0) -> Any:
    """`fn(window)` on the main thread, returning its value."""
    return browser_bridge.on_main(lambda finish: finish(fn(_window())), timeout)


def _evaluate(script: str, timeout: float = 15.0) -> str:
    """Run a fixed script in the page; its result as a string."""
    def work(finish):
        view = _window().webview()

        def done(webview, result, _data):
            try:
                finish(webview.evaluate_javascript_finish(result).to_string())
            except GLib.Error as exc:
                finish(error=RuntimeError(exc.message))

        view.evaluate_javascript(script, -1, None, None, None, done, None)

    return browser_bridge.on_main(work, timeout)


def _json(body: str, timeout: float = 15.0) -> Any:
    """Run `body` (a function body that `return`s) and decode its JSON result."""
    raw = _evaluate(f"JSON.stringify((() => {{ {body} }})())", timeout)
    if raw in (None, "", "undefined"):
        return None
    return json.loads(raw)


class _Watch:
    """The loads one action causes, with the guard on. Built on the main thread."""

    def __init__(self, window) -> None:
        self.window = window
        self.view = window.webview()
        self.started = False
        self.result: Optional[dict] = None
        self._ids = [self.view.connect("load-changed", self._changed),
                     self.view.connect("load-failed", self._failed)]
        window.navigation_guard = self._guard

    def arm(self) -> None:
        """Forget anything seen so far: only loads after this belong to the action."""
        self.started = False
        self.result = None

    def _guard(self, url: str) -> str:
        reason = model_may_open(url)
        if reason and self.result is None:
            self.result = {"refused": url, "reason": reason}
        return reason

    def _changed(self, webview, event) -> None:
        if event == _browser._LOAD_STARTED:
            self.started = True
        elif event == _browser._LOAD_FINISHED and self.result is None:
            self.result = {"url": webview.get_uri() or "", "title": webview.get_title() or ""}

    def _failed(self, _webview, _event, uri, error) -> bool:
        if self.result is None:
            self.result = {"failed": uri or "", "error": error.message if error else "no reason given"}
        return False

    def close(self) -> None:
        for handler in self._ids:
            self.view.disconnect(handler)
        self._ids = []
        self.window.navigation_guard = None


def _drive(start: Callable[[Any], Any], *, settle: Optional[float], timeout: float):
    """Run `start(window)` with the guard on, and wait for whatever load it caused.

    `settle` is how long to wait for a load to begin at all (None: one must -
    `navigate`, `back`). Returns `(start's value, load result or None)`.
    """
    # A load already in flight - the window's own blank page when it was just
    # built, a page still settling from the last action - would otherwise finish
    # inside this action's watch and be reported as its result (measured: the
    # first navigate said "Opened about:blank").
    _wait_until(lambda: not _main(lambda w: w.webview().is_loading()), min(timeout, 10.0))
    watch = _main(_Watch)
    try:
        # Armed in the same main-thread step that starts the action, so no
        # event can land between forgetting the old ones and causing the new.
        value = _main(lambda w: (watch.arm(), start(w))[1])
        if isinstance(value, dict) and value.get("error"):
            return value, None
        began = _wait_until(lambda: watch.started or watch.result is not None,
                            settle if settle is not None else min(timeout, 10.0))
        if not began:
            if settle is None:
                return value, {"failed": "", "error": "the page did not start loading"}
            return value, None
        if not _wait_until(lambda: watch.result is not None, timeout):
            return value, {"failed": "", "error": f"the page did not finish loading within {timeout:g}s"}
        return value, watch.result
    finally:
        _main(lambda _w: watch.close())


_CHALLENGE_JS = (
    # Known human-check widgets, by the frames and elements their vendors ship.
    "const f = Array.from(document.querySelectorAll('iframe')).map(i => i.src || '');"
    "const vendor = f.find(u => /recaptcha|hcaptcha\\.com|challenges\\.cloudflare\\.com|turnstile|arkoselabs|funcaptcha/i.test(u));"
    "const box = document.querySelector('.g-recaptcha, .h-captcha, .cf-turnstile, #challenge-form, [data-sitekey]');"
    "const words = /verify you are human|i'm not a robot|are you a robot|complete the security check/i"
    ".test((document.body && document.body.innerText || '').slice(0, 4000));"
    "return {found: !!(vendor || box || words), what: vendor ? new URL(vendor).hostname : (box ? 'a challenge form' : (words ? 'a human-verification page' : ''))};"
)


def _challenge_note() -> str:
    """When the page is a human check, the hand-over sentence; else ''.

    CAPTCHAs exist to confirm a person is present, so Chronoa does not try to
    solve them - it stops, says so in the window, and hands over: the person
    completes it in the visible browser, and the model carries on after.
    """
    try:
        found = _json("return (() => {" + _CHALLENGE_JS + "})();", 10.0) or {}
    except Exception:  # noqa: BLE001 - a check that could not run is not a challenge
        return ""
    if not found.get("found"):
        return ""
    _say("this page wants a human check - over to you in this window", refused=True)
    return (f" This page is showing a human-verification check ({found.get('what') or 'a CAPTCHA'}). "
            "Chronoa does not solve these - ask the user to complete it in the in-app "
            "browser window (ask_user), then continue.")


def _describe_load(load: Optional[dict], verb: str) -> str:
    if load is None:
        return ""
    if "refused" in load:
        return f"Error: the in-app browser did not open {load['refused']}: {load['reason']}."
    if "failed" in load:
        where = f"{load['failed']} " if load["failed"] else "the page "
        return f"Error: {where}did not load ({load['error']})."
    title = f" - {load['title']!r}" if load.get("title") else ""
    return f"{verb} {load['url']}{title} in the in-app browser."


# --- what the person watching sees ---------------------------------------------
#
# The model drives this browser in front of the person, so it has to be visible
# where it is pointing: a cursor labelled "Chronoa" glides to each element, a
# ring outlines it, a click ripples, typing appears a few characters at a time
# and scrolling is smooth. It is drawn in a shadow root on a click-through
# layer - nothing on the page can receive it, and `get_text` never reads it.
# Reduced-motion settings are honoured. These scripts are fixed text; the only
# data in them is JSON-encoded.

#: How long the cursor takes to arrive, matched by the pause before acting.
_GLIDE_S = 0.6
#: Typing is shown in at most this many steps, however long the text.
_TYPE_STEPS = 40

_CUE_CSS = (
    ".ptr{position:fixed;left:0;top:0;width:22px;height:22px;"
    "transition:transform .55s cubic-bezier(.2,.8,.2,1);filter:drop-shadow(0 1px 2px rgba(0,0,0,.45))}"
    ".tag{position:absolute;left:18px;top:18px;background:#3584e4;color:#fff;"
    "font:600 11px/1 system-ui,sans-serif;padding:3px 6px;border-radius:9px;white-space:nowrap}"
    ".ring{position:fixed;box-sizing:border-box;border:3px solid #3584e4;border-radius:8px;"
    "box-shadow:0 0 0 4px rgba(53,132,228,.25);transition:all .3s ease;opacity:0}"
    ".ring.on{opacity:1}"
    ".ripple{position:fixed;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;"
    "background:rgba(53,132,228,.55);animation:rp .5s ease-out forwards}"
    "@keyframes rp{to{transform:scale(3.2);opacity:0}}"
    "@media (prefers-reduced-motion: reduce){.ptr,.ring{transition:none}.ripple{animation:none;opacity:0}}"
)
_CUE_HTML = (
    '<div class="ring"></div><div class="ptr"><svg viewBox="0 0 24 24" width="22" height="22">'
    '<path d="M3 2l7.5 19 2.6-7.6L21 11z" fill="#fff" stroke="#1c1c1c" stroke-width="1.6" '
    'stroke-linejoin="round"/></svg><span class="tag">Chronoa</span></div>'
)


def _cue_layer_js() -> str:
    """JS that finds or builds the cue layer and leaves it in `root`."""
    return (
        "let host = document.getElementById('__chronoa_cue');"
        "if (!host) {"
        "  host = document.createElement('div'); host.id = '__chronoa_cue';"
        "  host.setAttribute('aria-hidden', 'true');"
        "  host.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;';"
        "  const r = host.attachShadow({mode: 'open'});"
        f"  const css = {json.dumps(_CUE_CSS)};"
        # A constructed sheet is not blocked by a page's style-src CSP the way
        # an inserted <style> is; the <style> is the fallback for old engines.
        "  try { const sh = new CSSStyleSheet(); sh.replaceSync(css); r.adoptedStyleSheets = [sh]; }"
        "  catch (e) { const st = document.createElement('style'); st.textContent = css; r.appendChild(st); }"
        f"  r.innerHTML += {json.dumps(_CUE_HTML)};"
        "  document.documentElement.appendChild(host);"
        "}"
        "const root = host.shadowRoot;"
    )


def _point_script(selector: str, x: Any, y: Any, start: "tuple[float, float]") -> str:
    target = (f"document.querySelector({json.dumps(selector)})" if selector
              else f"document.elementFromPoint({float(x)}, {float(y)})")
    return (
        f"const el = {target}; if (!el) return {{missing: true}};"
        "el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});"
        + _cue_layer_js() +
        "const ptr = root.querySelector('.ptr'), ring = root.querySelector('.ring');"
        f"if (!ptr.dataset.placed) {{ ptr.style.transition = 'none';"
        f"  ptr.style.transform = 'translate({float(start[0])}px,{float(start[1])}px)';"
        "  ptr.getBoundingClientRect(); ptr.style.transition = ''; ptr.dataset.placed = '1'; }"
        "const b = el.getBoundingClientRect();"
        "const cx = Math.round(b.left + b.width / 2), cy = Math.round(b.top + Math.min(b.height / 2, 24));"
        "ptr.style.transform = `translate(${cx}px,${cy}px)`;"
        "Object.assign(ring.style, {left: (b.left - 4) + 'px', top: (b.top - 4) + 'px',"
        " width: (b.width + 8) + 'px', height: (b.height + 8) + 'px'});"
        "ring.classList.add('on'); ring.dataset.shown = String((+ring.dataset.shown || 0) + 1);"
        "clearTimeout(window.__chronoaRing);"
        "window.__chronoaRing = setTimeout(() => ring.classList.remove('on'), 1800);"
        "return {ok: true, x: cx, y: cy};"
    )


def _press_script(x: float, y: float) -> str:
    return (_cue_layer_js()
            + "const d = document.createElement('div'); d.className = 'ripple';"
            f"d.style.left = '{float(x)}px'; d.style.top = '{float(y)}px';"
            "root.appendChild(d); setTimeout(() => d.remove(), 600); return {ok: true};")


def _pause(seconds: float) -> None:
    """Let the person see a step land; keeps the page painting on the main thread."""
    if _on_main_thread():
        end = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while time.monotonic() < end:
            if not context.iteration(False):
                time.sleep(0.01)
    else:
        time.sleep(seconds)


def _say(text: str, refused: bool = False) -> None:
    """Name the step in the window's activity strip. Never fails the action."""
    try:
        _main(lambda w: w.show_activity(text, refused))
    except Exception:  # noqa: BLE001 - a strip that could not update is not an error in the page
        logger.debug("activity strip not updated", exc_info=True)


def _point_at(selector: str, x: Any = None, y: Any = None, timeout: float = 15.0) -> Optional[dict]:
    """Glide the cursor to the element and ring it; None when there is nothing there."""
    window = _window()
    start = getattr(window, "_chronoa_pointer", (40.0, 40.0)) if window is not None else (40.0, 40.0)
    value = _json(_point_script(selector, x, y, start), timeout)
    if not value or not value.get("ok"):
        return None
    if window is not None:
        window._chronoa_pointer = (value["x"], value["y"])
    _pause(_GLIDE_S)
    return value


# --- money: asked, never assumed ------------------------------------------------
#
# Typing into a card field, or pressing the button that places an order, is
# where a browsing mistake costs money. Measured 2026-10-08 in the real app: a
# cloud model filled in a card number on its own initiative. So these two steps
# are put to the person through Chronoa's own question dialog, once per site
# for a few minutes; with nobody to ask, or no answer, they do not happen.

_PAYMENT_FIELD_JS = (
    "const el = document.querySelector(SEL); if (!el) return {payment: false};"
    "const ac = (el.getAttribute('autocomplete') || '').toLowerCase();"
    "const words = [el.name, el.id, el.placeholder, el.getAttribute('aria-label')].join(' ');"
    "return {payment: ac.startsWith('cc-') || /card ?number|credit ?card|creditcard|cc-?(num|number|csc|exp)|\\bcvv\\b|\\bcvc\\b|security ?code|card ?(expir|month|year)/i.test(words)};"
)
_ORDER_BUTTON_JS = (
    "const el = document.querySelector(SEL); if (!el) return {order: false};"
    "const label = String(el.innerText || el.value || el.getAttribute('aria-label') || '').trim();"
    "return {order: /\\b(pay|purchase|place (your )?order|buy now|complete (purchase|order|payment)|confirm (and )?pay)\\b/i.test(label), label: label.slice(0, 60)};"
)
_MONEY_WINDOW_S = 300.0
_money_allowed: dict = {}


def _host() -> str:
    try:
        return urlsplit(_main(lambda w: w.current_url())).hostname or "this site"
    except Exception:  # noqa: BLE001
        return "this site"


def _money_ok(what: str) -> str:
    """'' when the person allowed `what` on this site; otherwise why not."""
    host = _host()
    if _money_allowed.get(host, 0) > time.monotonic():
        return ""
    from shani_chronoa import ask_bridge
    if _on_main_thread() or not ask_bridge.has_presenter():
        return (f"{what} on {host} needs your say-so, and there is nobody here to ask - "
                "so it was not done")
    _say(f"asking you before {what}", refused=True)
    answer = ask_bridge.ask(f"Chronoa wants to {what} on {host}. Allow it?",
                            ["Allow", "Don't allow"])
    if answer != "Allow":
        return f"you did not allow {what} on {host}, so it was not done"
    _money_allowed[host] = time.monotonic() + _MONEY_WINDOW_S
    return ""


def _payment_field(selector: str, timeout: float) -> bool:
    found = _json(_PAYMENT_FIELD_JS.replace("SEL", json.dumps(selector)), timeout) or {}
    return bool(found.get("payment"))


def _order_button(selector: str, timeout: float) -> str:
    found = _json(_ORDER_BUTTON_JS.replace("SEL", json.dumps(selector)), timeout) or {}
    return found.get("label", "") if found.get("order") else ""


# --- the fixed page scripts ----------------------------------------------------


def _click_script(selector: str, x: Any, y: Any) -> str:
    target = (f"document.querySelector({json.dumps(selector)})" if selector
              else f"document.elementFromPoint({float(x)}, {float(y)})")
    where = json.dumps(selector) if selector else json.dumps(f"({x}, {y})")
    return (f"const el = {target};"
            f"if (!el) return {{error: 'nothing matches ' + {where}}};"
            # Clicking a hidden element does nothing, and this used to answer
            # "Clicked '9696'" for a hidden <input> holding a flight number - so a
            # model believed it and repeated it (measured in the real app,
            # 2026-10-08). It is an error now, naming the form's real button.
            "const st = getComputedStyle(el), bx = el.getBoundingClientRect();"
            "if ((el.tagName === 'INPUT' && el.type === 'hidden') || st.display === 'none'"
            " || st.visibility === 'hidden' || (bx.width === 0 && bx.height === 0)) {"
            "  const form = el.closest('form');"
            "  const btns = form ? Array.from(form.querySelectorAll('button, input[type=submit], input[type=button]'))"
            "    .filter(b => b.offsetParent !== null).map(b => (b.innerText || b.value || '').trim()).filter(Boolean).slice(0, 3) : [];"
            "  return {error: 'that element is hidden (it holds a value; it is not something to click), so nothing happened'"
            "    + (btns.length ? '. Its form\\'s button: ' + btns.map(t => JSON.stringify(t)).join(' or ') : '')};"
            "}"
            "el.scrollIntoView({block: 'center', inline: 'center'});"
            "if (el.disabled) return {error: 'the element is disabled'};"
            "el.click();"
            "return {ok: true, tag: el.tagName.toLowerCase(),"
            " label: String(el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80)};")


def _type_script(selector: str, text: str, carry_on: bool = False) -> str:
    # execCommand('insertText') is what a keystroke does to a field: it fires the
    # input events framework-controlled inputs listen for, where assigning
    # `.value` fires none. The value is never echoed back - a password field's
    # contents do not belong in the transcript.
    # `carry_on`: a later chunk of the same text. Search boxes built with a
    # framework re-create their input while you type (measured: Wikipedia's
    # #searchInput was replaced after 11 of 13 characters), so the selector can
    # stop matching mid-word; the field that still has focus is the one the
    # first chunk went into, so typing continues there - if it is editable.
    focused = ("const a = document.activeElement;"
               "const editable = a && (a.isContentEditable || /^(INPUT|TEXTAREA)$/.test(a.tagName));"
               if carry_on else "const a = null, editable = false;")
    return (focused
            + f"const el = document.querySelector({json.dumps(selector)}) || (editable ? a : null);"
            f"if (!el) return {{error: 'nothing matches ' + {json.dumps(selector)}}};"
            "if (el !== a) { el.scrollIntoView({block: 'nearest'}); el.focus({preventScroll: true}); }"
            "if (document.activeElement !== el) return {error: 'the element cannot take focus, so nothing can be typed into it'};"
            f"const text = {json.dumps(text)}; let ok = false;"
            "try { ok = document.execCommand('insertText', false, text); } catch (e) {}"
            "if (!ok) {"
            "  if (!('value' in el)) return {error: 'the element is not a text field'};"
            "  const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');"
            "  if (d && d.set) d.set.call(el, el.value + text); else el.value = el.value + text;"
            "  el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: text}));"
            "}"
            "el.dispatchEvent(new Event('change', {bubbles: true}));"
            "return {ok: true, length: ('value' in el ? String(el.value) : el.textContent || '').length};")


def _select_script(selector: str, value: Any, label: Any) -> str:
    return (f"const el = document.querySelector({json.dumps(selector)});"
            f"if (!el) return {{error: 'nothing matches ' + {json.dumps(selector)}}};"
            "if (el.tagName !== 'SELECT') return {error: 'the element is not a <select>'};"
            f"const value = {json.dumps(value)}, label = {json.dumps(label)};"
            "const opt = Array.from(el.options).find(o => value !== null ? o.value === value : o.text.trim() === label);"
            "if (!opt) return {error: 'no option ' + (value !== null ? 'with value ' + value : 'labelled ' + label),"
            " options: Array.from(el.options).map(o => o.text.trim()).slice(0, 20)};"
            "el.value = opt.value;"
            "el.dispatchEvent(new Event('input', {bubbles: true}));"
            "el.dispatchEvent(new Event('change', {bubbles: true}));"
            "return {ok: true, chosen: opt.text.trim()};")


def _scroll_script(selector: str, dx: float, dy: float) -> str:
    return ((f"const el = document.querySelector({json.dumps(selector)});"
             f"if (!el) return {{error: 'nothing matches ' + {json.dumps(selector)}}};"
             if selector else "const el = null;")
            + "const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;"
            + f"(el || window).scrollBy({{left: {dx}, top: {dy}, behavior: calm ? 'instant' : 'smooth'}});"
            "return {ok: true};")


def _scroll_position_script(selector: str) -> str:
    return ((f"const el = document.querySelector({json.dumps(selector)});" if selector else "const el = null;")
            + "return {x: Math.round(el ? el.scrollLeft : window.scrollX),"
            " y: Math.round(el ? el.scrollTop : window.scrollY)};")


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _screenshot_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "shani-chronoa" / "browser-screenshots"


def _snapshot(path: Path, timeout: float) -> None:
    def work(finish):
        window = _window()
        webkit = window._webkit_ns
        view = window.webview()

        def done(webview, result, _data):
            try:
                texture = webview.get_snapshot_finish(result)
            except GLib.Error as exc:
                finish(error=RuntimeError(exc.message))
                return
            if not texture.save_to_png(str(path)):
                finish(error=RuntimeError(f"could not write {path}"))
                return
            finish(True)

        view.get_snapshot(webkit.SnapshotRegion.VISIBLE, webkit.SnapshotOptions.NONE,
                          None, done, None)

    browser_bridge.on_main(work, timeout)


# --- the actions ---------------------------------------------------------------


def act(arguments: dict) -> str:
    """Perform one `browse` action in the in-app browser.

    Failures start with "Error " - `skills/browse.py` turns those into a
    `ToolFailure`, the same contract its Chromium path keeps.
    """
    action = str(arguments.get("action") or "navigate").lower()
    if action in UNSUPPORTED:
        _say(f"refused '{action}' - not allowed in this browser", refused=True)
        return f"Error: '{action}' is not available here: {UNSUPPORTED[action]}."
    if action not in ACTIONS:
        return f"Error: unknown action {action!r}."
    timeout = _num(arguments.get("timeout"), _DEFAULT_TIMEOUT)
    try:
        return _act(action, arguments, timeout)
    except browser_bridge.MainThreadTimeout as exc:
        return f"Error performing '{action}' in the in-app browser: {exc}."
    except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
        return f"Error performing '{action}' in the in-app browser: {exc}"


def _act(action: str, arguments: dict, timeout: float) -> str:
    selector = str(arguments.get("selector") or "")
    settle = _num(arguments.get("settle"), _DEFAULT_SETTLE)

    if action == "navigate":
        url = str(arguments.get("url") or "").strip()
        if not url:
            return "Please provide a URL to navigate to."
        reason = model_may_open(url)
        if reason:
            _say(f"refused to open {url} - {reason.split(':')[0]}", refused=True)
            return f"Error: the in-app browser did not open {url}: {reason}."
        _say(f"opening {url}")
        _, load = _drive(lambda w: w.load_url(url), settle=None, timeout=timeout)
        said = _describe_load(load, "Opened")
        return said if said.startswith("Error") else said + _challenge_note()

    if action in ("back", "forward", "reload"):
        _say({"back": "going back", "forward": "going forward", "reload": "reloading the page"}[action])

        def start(window):
            view = window.webview()
            if action == "back" and not view.can_go_back():
                return {"error": "there is no page to go back to"}
            if action == "forward" and not view.can_go_forward():
                return {"error": "there is no page to go forward to"}
            {"back": window.go_back, "forward": window.go_forward, "reload": window.reload}[action]()
            return {"ok": True}
        value, load = _drive(start, settle=None, timeout=timeout)
        if value.get("error"):
            return f"Error: {value['error']}."
        return _describe_load(load, {"back": "Went back to", "forward": "Went forward to",
                                     "reload": "Reloaded"}[action])

    if action == "stop":
        _main(lambda w: w.webview().stop_loading())
        return "Stopped loading."

    if action == "click":
        x, y = arguments.get("x"), arguments.get("y")
        if not selector and (x is None or y is None):
            return "Please provide either a selector or x,y coordinates for click."
        if selector:
            label = _order_button(selector, timeout)
            refusal = _money_ok(f"press '{label}', which may place an order or take payment") if label else ""
            if refusal:
                return f"Error: did not click: {refusal}."
        _say(f"clicking {selector or f'({x}, {y})'}")
        spot = _point_at(selector, x, y, timeout)
        if spot is not None:
            _json(_press_script(spot["x"], spot["y"]), timeout)
            _pause(0.2)
        script = _click_script(selector, x, y)
        value, load = _drive(lambda w: _start_script(w, script), settle=settle, timeout=timeout)
        if value.get("error"):
            return f"Error: could not click: {value['error']}."
        what = value.get("label") or value.get("tag") or "the element"
        said = f"Clicked {what!r}" + (f" ({selector})" if selector else f" at ({x}, {y})") + "."
        followed = _describe_load(load, "It opened")
        if followed.startswith("Error"):
            _say(f"the click on {what!r} led somewhere the network policy refuses", refused=True)
            return f"{followed} (after clicking {what!r})"
        return f"{said} {followed}".strip() + (_challenge_note() if load else "")

    if action == "type":
        text = str(arguments.get("text") or "")
        if not selector:
            return "Please provide a selector for typing."
        if not text:
            return "Please provide text to type."
        if _payment_field(selector, timeout):
            refusal = _money_ok("enter payment details")
            if refusal:
                return f"Error: did not type into '{selector}': {refusal}."
        # The text itself is not named in the strip: it may be a password.
        _say(f"typing into {selector}")
        _point_at(selector, timeout=timeout)
        step = max(1, -(-len(text) // _TYPE_STEPS))
        for i in range(0, len(text), step):
            value = _json(_type_script(selector, text[i:i + step], carry_on=i > 0), timeout)
            if not value or value.get("error"):
                return (f"Error: could not type into '{selector}': "
                        f"{(value or {}).get('error', 'no answer from the page')}."
                        + (f" {i} of {len(text)} character(s) went in first." if i else ""))
            _pause(0.04)
        return f"Typed {len(text)} character(s) into '{selector}'."

    if action == "select":
        value_arg = arguments.get("value")
        label = arguments.get("label")
        if not selector or (value_arg is None and label is None):
            return "Please provide a selector and a value or label to select."
        if _payment_field(selector, timeout):
            refusal = _money_ok("enter payment details")
            if refusal:
                return f"Error: did not choose in '{selector}': {refusal}."
        _say(f"choosing {label if label is not None else value_arg!r} in {selector}")
        _point_at(selector, timeout=timeout)
        value = _json(_select_script(selector, value_arg, label), timeout)
        if not value or value.get("error"):
            extra = f" Options: {', '.join(value['options'])}" if value and value.get("options") else ""
            return f"Error: could not select in '{selector}': {(value or {}).get('error')}.{extra}"
        return f"Selected {value['chosen']!r} in '{selector}'."

    if action == "scroll":
        # `direction` is what a model tends to send for "scroll down"; the
        # deltas, when given, are the precise form and win.
        way = str(arguments.get("direction") or "").strip().lower()
        dx = _num(arguments.get("delta_x"), {"left": -600.0, "right": 600.0}.get(way, 0.0))
        dy = _num(arguments.get("delta_y"), {"up": -600.0, "left": 0.0, "right": 0.0}.get(way, 600.0))
        _say(f"scrolling {way or ('down' if dy > 0 else 'up')}")
        value = _json(_scroll_script(selector, dx, dy), timeout)
        if not value or value.get("error"):
            return f"Error: could not scroll: {(value or {}).get('error')}."
        _pause(_GLIDE_S)
        where = _json(_scroll_position_script(selector), timeout) or {"x": 0, "y": 0}
        return f"Scrolled to ({where['x']}, {where['y']})."

    if action == "get_title":
        return f"Page title: {_main(lambda w: w.current_title()) or 'Untitled'}"

    if action == "get_url":
        return f"Current URL: {_main(lambda w: w.current_url())}"

    if action == "get_text":
        _say("reading the page")
        url, title = _main(lambda w: (w.current_url(), w.current_title()))
        text = _evaluate(_browser.VISIBLE_TEXT_JS, timeout) or ""
        if not text.strip():
            return f"{url} shows no text."
        return webtext.render(_browser.page_from_text(url, text, title))

    if action == "screenshot":
        _say("taking a screenshot")
        folder = _screenshot_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"page-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.png"
        _snapshot(path, timeout)
        asked = arguments.get("path")
        note = (f" (not {asked}: the in-app browser saves only into Chronoa's own folder)"
                if asked and Path(str(asked)) != path else "")
        return f"Screenshot saved to: {path}{note}"

    if action in ("wait", "wait_text"):
        needle = str(arguments.get("wait_for") or "") if action == "wait" else str(arguments.get("text") or "")
        if not needle:
            return ("Please provide an element selector to wait for." if action == "wait"
                    else "Please provide text to wait for.")
        test = (f"return {{found: !!document.querySelector({json.dumps(needle)})}};" if action == "wait"
                else f"return {{found: (document.body ? document.body.innerText : '').includes({json.dumps(needle)})}};")
        deadline = time.monotonic() + timeout
        while True:
            found = (_json(test, 15.0) or {}).get("found")
            if found:
                return f"{'Element' if action == 'wait' else 'Text'} appeared: {needle}"
            if time.monotonic() >= deadline:
                return f"Timed out after {timeout:g}s waiting for: {needle}"
            _wait_until(lambda: False, 0.5)

    return f"Error: unknown action {action!r}."


def _start_script(window, script: str) -> dict:
    """Run a fixed click script from inside `_drive`'s main-thread step.

    `_drive` needs the click's own answer and the load it causes; the script is
    evaluated asynchronously, so its answer is collected by spinning the main
    context here until WebKit hands it over - bounded, like every wait here.
    """
    box: dict = {}

    def done(webview, result, _data):
        try:
            box["raw"] = webview.evaluate_javascript_finish(result).to_string()
        except GLib.Error as exc:
            box["error"] = exc.message

    window.webview().evaluate_javascript(
        f"JSON.stringify((() => {{ {script} }})())", -1, None, None, None, done, None)
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 15
    while not box and time.monotonic() < deadline:
        if not context.iteration(False):
            time.sleep(0.01)
    if "error" in box:
        return {"error": box["error"]}
    if "raw" not in box:
        return {"error": "the page did not answer within 15s"}
    try:
        return json.loads(box["raw"]) or {"error": "the page returned nothing"}
    except (TypeError, ValueError):
        return {"error": f"unexpected answer {box['raw']!r}"}


__all__ = ["ACTIONS", "UNSUPPORTED", "act", "model_may_open"]
