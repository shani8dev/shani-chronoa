"""Skill: drive a real Chromium over the DevTools protocol.

Uses the same CDP infrastructure as shani-testbed's web_client.py:
launches headless Chromium with --remote-debugging-pipe, attaches to
a page target, and drives it - navigate, click, type, press keys,
scroll, hover, drag, select options, upload files, move through
history and tabs, evaluate JavaScript and capture screenshots.

Consent: every path checks `ChronoaConfig().sense_allowed("web")`,
which requires `web-sense-enabled` (default false) *and* privacy
mode off - the same gate `web_search` uses, read fresh per call so a
mid-session toggle takes effect immediately.

Egress: every page loaded by URL - `navigate` and `new_tab` - is
recorded in the egress ledger with `purpose="page-navigation"`,
the same way the built-in browser window records its own
navigations, so a page the assistant loaded appears in the
privacy panel's recent activity. Only http(s) is recorded - a
`file://` page is not egress, and logging it as one would be an
alarm that fires for local files. History moves (`back`,
`forward`) revisit pages the ledger already shows.

The browser is a module-level singleton: the assistant's process
keeps one Chromium (and one CDP session) alive across tool calls,
which is what "persistent session" means here. It is never closed -
skills have no lifecycle hook to close it from - so it lives for as
long as the process that imported this module.

Desktop automation (typing, clicking and moving the pointer in
*other* windows) is a different skill set - `type_text`,
`click_pointer`, `move_pointer`, `press_key` and `ui_elements` -
gated behind `input-control-enabled`, a separate consent key. This
skill drives the browser only, through its DOM.
"""

import base64
import fcntl
import json
import os
import select
import shutil
import subprocess
import tempfile
import time

from shani_chronoa import egress
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

# CDP implementation from shani-testbed/lib/web_client.py
class CDP:
    """CDP over --remote-debugging-pipe: fd 3 is the browser's input, fd 4 its
    output, every message a JSON object followed by a NUL byte."""

    def __init__(self, argv, timeout):
        to_browser_r, self._w = os.pipe()
        self._r, from_browser_w = os.pipe()

        def child_fds():
            # The browser expects exactly fds 3 and 4. Move both ends above 10
            # first, so dup2 onto 3 cannot clobber the other end if it was 3/4.
            a = fcntl.fcntl(to_browser_r, fcntl.F_DUPFD, 10)
            b = fcntl.fcntl(from_browser_w, fcntl.F_DUPFD, 10)
            os.dup2(a, 3)
            os.dup2(b, 4)
            os.close(a)
            os.close(b)

        # close_fds=False: Popen closes every fd not in pass_fds AFTER
        # preexec_fn, which would close the 3/4 made above. Nothing else
        # leaks: os.pipe() fds are close-on-exec, and dup2 clears that only
        # on 3 and 4.
        self.proc = subprocess.Popen(
            argv, preexec_fn=child_fds, close_fds=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.close(to_browser_r)
        os.close(from_browser_w)
        fl = fcntl.fcntl(self._r, fcntl.F_GETFL)
        fcntl.fcntl(self._r, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        self._buf = b""
        self._id = 0
        self.timeout = timeout
        self.events = []      # every event received, in order
        self.session = None

    def _read_messages(self, wait):
        ready, _, _ = select.select([self._r], [], [], wait)
        if not ready:
            return []
        try:
            chunk = os.read(self._r, 1 << 20)
        except BlockingIOError:
            return []
        if not chunk:
            raise RuntimeError("browser closed the DevTools pipe")
        self._buf += chunk
        out = []
        while b"\0" in self._buf:
            msg, self._buf = self._buf.split(b"\0", 1)
            out.append(json.loads(msg))
        return out

    def send(self, method, params=None, session=True, timeout=None):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session and self.session:
            msg["sessionId"] = self.session
        data = json.dumps(msg).encode() + b"\0"
        while data:
            n = os.write(self._w, data)
            data = data[n:]
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            for m in self._read_messages(0.2):
                if m.get("id") == mid:
                    if "error" in m:
                        raise RuntimeError(f"{method}: {m['error'].get('message')}")
                    return m.get("result", {})
                if "method" in m:
                    self.events.append(m)
        raise TimeoutError(f"{method}: no reply in {timeout or self.timeout}s")

    def pump(self, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            for m in self._read_messages(min(0.2, max(0.0, deadline - time.time()))):
                if "method" in m:
                    self.events.append(m)

    def wait_event(self, method, seconds, since=0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            for e in self.events[since:]:
                if e["method"] == method:
                    return e
            self.pump(0.2)
        return None

    def close(self):
        try:
            self.send("Browser.close", session=False, timeout=5)
        except Exception:
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Page:
    def __init__(self, cdp, timeout=30):
        self.cdp = cdp
        self.timeout = timeout

    def evaluate(self, expr, await_promise=False, timeout=None):
        r = self.cdp.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": await_promise},
            timeout=timeout)
        if "exceptionDetails" in r:
            # .text is only "Uncaught"; the actual message is in the
            # exception's description ("Error: Element not found").
            details = r["exceptionDetails"]
            message = details.get("exception", {}).get("description") \
                or details.get("text") or "evaluate failed"
            raise RuntimeError(message)
        return r.get("result", {}).get("value")

    def navigate(self, url, settle=None, reload=False):
        """Load url and wait for load + a quiet network. Returns the events this
        load produced and the main document's HTTP status."""
        since = len(self.cdp.events)
        if reload:
            self.cdp.send("Page.reload", {"ignoreCache": False})
        else:
            r = self.cdp.send("Page.navigate", {"url": url})
            if r.get("errorText"):
                return self.cdp.events[since:], None, r["errorText"]
        self.wait_for_load()
        inflight, quiet_since = set(), time.time()
        cap = time.time() + (settle or 10)
        seen = since
        while time.time() < cap:
            self.cdp.pump(0.1)
            for e in self.cdp.events[seen:]:
                m, p = e["method"], e.get("params", {})
                if m == "Network.requestWillBeSent":
                    inflight.add(p["requestId"]); quiet_since = time.time()
                elif m in ("Network.loadingFinished", "Network.loadingFailed"):
                    inflight.discard(p["requestId"]); quiet_since = time.time()
            seen = len(self.cdp.events)
            if not inflight and time.time() - quiet_since > 0.5:
                break
        evs = self.cdp.events[since:]
        docs = [e["params"]["response"]["status"] for e in evs
                if e["method"] == "Network.responseReceived" and e["params"].get("type") == "Document"]
        status = docs[-1] if docs else None
        return evs, status, None

    def wait_for_load(self, timeout=None):
        """Wait for the next Page.loadEventFired - a navigation completed."""
        since = len(self.cdp.events)
        return self.cdp.wait_event("Page.loadEventFired", timeout or self.timeout, since)


class Browser:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.cdp = None
        self.page = None
        self.profile = None
        self._target_id = None
        self._launch_browser()

    def _launch_browser(self):
        profile = tempfile.mkdtemp(prefix="shani-chronoa-browser-")
        self.profile = profile
        browser = next((shutil.which(b) for b in
                        ("chromium", "google-chrome-stable", "google-chrome", "chromium-browser")
                        if shutil.which(b)), None)
        if not browser:
            raise RuntimeError("Chromium not found (install chromium or google-chrome)")

        argv = [browser, "--headless", "--remote-debugging-pipe",
                f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", "--disable-component-update",
                "--disable-sync", "--metrics-recording-only",
                "--disable-features=Translate,OptimizationHints,MediaRouter",
                "about:blank"]
        if os.geteuid() == 0:
            # Chromium refuses to run as root with its sandbox; the
            # builder container and the sandbox executor run as root.
            argv.insert(1, "--no-sandbox")

        self.cdp = CDP(argv, self.timeout)
        # Page/Runtime/Network commands are page-domain commands: they
        # need an attached target, not the browser endpoint.
        target = self.cdp.send("Target.createTarget", {"url": "about:blank"},
                               session=False)["targetId"]
        self.switch_tab(target)

    # -- tabs ------------------------------------------------------------

    def tabs(self):
        """Every open page target: [{id, url, title}]."""
        result = self.cdp.send("Target.getTargets", session=False)
        return [{"id": t["targetId"], "url": t.get("url", ""), "title": t.get("title", "")}
                for t in result.get("targetInfos", []) if t.get("type") == "page"]

    def switch_tab(self, target_id):
        """Activate a tab and attach to it, so later commands act on it."""
        if target_id == self._target_id:
            return
        self.cdp.send("Target.activateTarget", {"targetId": target_id}, session=False)
        attached = self.cdp.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True},
            session=False)
        self.cdp.session = attached["sessionId"]
        self._target_id = target_id
        for dom in ("Page", "Runtime", "Network"):
            self.cdp.send(f"{dom}.enable")
        self.page = Page(self.cdp, self.timeout)

    def new_tab(self, url=None):
        """Open a tab (and switch to it), optionally navigating it.
        Returns (targetId, HTTP status or None)."""
        target = self.cdp.send("Target.createTarget", {"url": "about:blank"},
                               session=False)["targetId"]
        self.switch_tab(target)
        status = None
        if url:
            _, status, err = self.page.navigate(url)
            if err:
                raise RuntimeError(err)
        return target, status

    def close_tab(self, target_id=None):
        """Close a tab. Closing the current one switches to a remaining
        tab (or opens a fresh one - a browser always has a tab to act on)."""
        target_id = target_id or self._target_id
        if not target_id:
            raise RuntimeError("no tab is open")
        result = self.cdp.send("Target.closeTarget", {"targetId": target_id},
                               session=False)
        if not result.get("result"):
            raise RuntimeError(f"no such tab: {target_id}")
        self.cdp.pump(0.3)
        if target_id == self._target_id:
            remaining = [t for t in self.tabs() if t["id"] != target_id]
            if remaining:
                self.switch_tab(remaining[0]["id"])
            else:
                self._target_id = None
                self.new_tab()

    # -- input -----------------------------------------------------------

    def click(self, x, y):
        # A click is three events, not one: only mousePressed leaves
        # the button held down, and without the matching release no
        # click event ever reaches the page. The same sequence
        # shani-testbed's web_features dispatches.
        for type_, buttons in (("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 0)):
            self.cdp.send("Input.dispatchMouseEvent", {
                "type": type_, "x": x, "y": y, "button": "left",
                "buttons": buttons, "clickCount": 1,
            })

    def move(self, x, y, buttons=0):
        self.cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
            "button": "left" if buttons else "none", "buttons": buttons,
        })

    def press(self, x, y):
        self.cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left",
            "buttons": 1, "clickCount": 1,
        })

    def release(self, x, y):
        self.cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left",
            "buttons": 0, "clickCount": 1,
        })

    def hover(self, x, y):
        self.move(x, y)

    def travel(self, from_x, from_y, to_x, to_y, steps=10):
        # Intermediate moves with the button held: a single jump
        # does not produce the mousemove events a drag handler
        # tracks.
        for i in range(1, steps + 1):
            self.move(from_x + (to_x - from_x) * i / steps,
                      from_y + (to_y - from_y) * i / steps, buttons=1)

    def drag(self, from_x, from_y, to_x, to_y, steps=10):
        self.move(from_x, from_y)
        self.press(from_x, from_y)
        self.travel(from_x, from_y, to_x, to_y, steps)
        self.release(to_x, to_y)

    def scroll(self, x, y, delta_x, delta_y):
        self.cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": x, "y": y,
            "deltaX": delta_x, "deltaY": delta_y,
        })

    def _key_event(self, type_, key, code, vk, modifiers=0, text=None):
        params = {"type": type_, "key": key, "code": code,
                  "windowsVirtualKeyCode": vk, "modifiers": modifiers}
        if type_ == "keyDown" and text:
            params["text"] = text
        self.cdp.send("Input.dispatchKeyEvent", params)

    def key(self, modifiers, key, code, vk, text=None):
        """Dispatch one key, holding any modifiers around it the way a
        person does: Control down, c down (with the Control bit set),
        c up, Control up. The bitmask on the key's own events is what
        sites read as e.ctrlKey / e.shiftKey."""
        held = [name for name in _MODIFIER_ORDER
                if modifiers & _MODIFIER_DISPATCH[name][3]]
        acc = 0
        for name in held:
            mkey, mcode, mvk, mbit = _MODIFIER_DISPATCH[name]
            self._key_event("keyDown", mkey, mcode, mvk, acc)
            acc |= mbit
        self._key_event("keyDown", key, code, vk, modifiers, text)
        self._key_event("keyUp", key, code, vk, modifiers)
        for name in reversed(held):
            mkey, mcode, mvk, mbit = _MODIFIER_DISPATCH[name]
            self._key_event("keyUp", mkey, mcode, mvk, modifiers)

    def type(self, text):
        self.cdp.send("Input.insertText", {"text": text})

    # -- navigation ------------------------------------------------------

    def navigate(self, url, settle=None):
        return self.page.navigate(url, settle)

    def go(self, method):
        """goBack/goForward/reload: a navigation, so wait for its load."""
        self.cdp.send(method)
        self.page.wait_for_load()

    def evaluate(self, expr, await_promise=False, timeout=None):
        return self.page.evaluate(expr, await_promise, timeout)

    def screenshot(self, path):
        # The whole page, not just the first screen, capped the way
        # shani-testbed's web_client caps it: an uncapped long page
        # would produce a gigantic image and time the capture out.
        width = self.page.evaluate("window.innerWidth") or 1280
        height = self.page.evaluate("document.documentElement.scrollHeight") or 800
        shot = self.cdp.send("Page.captureScreenshot", {
            "format": "png", "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": int(width),
                     "height": min(int(height), 12000), "scale": 1}
        }, timeout=120)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(shot["data"]))
        return path

    def upload_file(self, selector, files):
        """Set the files of a file input, the way a file chooser does."""
        check = (
            "(() => {\n"
            f"  const element = document.querySelector({json.dumps(selector)});\n"
            "  if (!element) throw new Error('Element not found');\n"
            "  if (element.tagName !== 'INPUT' || element.type !== 'file') {\n"
            "    throw new Error('Not a file input');\n"
            "  }\n"
            "  return true;\n"
            "})()"
        )
        self.page.evaluate(check)
        self.cdp.send("DOM.enable")
        document = self.cdp.send("DOM.getDocument", {"depth": 0})
        node = self.cdp.send("DOM.querySelector", {
            "nodeId": document["root"]["nodeId"], "selector": selector})
        if not node.get("nodeId"):
            raise RuntimeError("Element not found")
        remote = self.cdp.send("DOM.resolveNode", {"nodeId": node["nodeId"]})
        self.cdp.send("DOM.setFileInputFiles", {
            "objectId": remote["object"]["objectId"], "files": list(files)})

    def close(self):
        self.cdp.close()
        shutil.rmtree(self.profile, ignore_errors=True)


# Named keys: name -> (key, code, windowsVirtualKeyCode). The vk is
# what sites read off KeyboardEvent.which; the code is the physical
# key, which is what layout-independent shortcuts need.
_NAMED_KEYS = {
    "enter": ("Enter", "Enter", 13),
    "return": ("Enter", "Enter", 13),
    "tab": ("Tab", "Tab", 9),
    "escape": ("Escape", "Escape", 27),
    "esc": ("Escape", "Escape", 27),
    "backspace": ("Backspace", "Backspace", 8),
    "delete": ("Delete", "Delete", 46),
    "insert": ("Insert", "Insert", 45),
    "home": ("Home", "Home", 36),
    "end": ("End", "End", 35),
    "pageup": ("PageUp", "PageUp", 33),
    "pagedown": ("PageDown", "PageDown", 34),
    "arrowup": ("ArrowUp", "ArrowUp", 38),
    "arrowdown": ("ArrowDown", "ArrowDown", 40),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37),
    "arrowright": ("ArrowRight", "ArrowRight", 39),
    "up": ("ArrowUp", "ArrowUp", 38),
    "down": ("ArrowDown", "ArrowDown", 40),
    "left": ("ArrowLeft", "ArrowLeft", 37),
    "right": ("ArrowRight", "ArrowRight", 39),
    "space": (" ", "Space", 32),
    **{f"f{n}": (f"F{n}", f"F{n}", 111 + n) for n in range(1, 13)},
}

# Modifiers: name -> (key, code, vk, bitmask bit). The bit is what
# CDP's modifiers field and KeyboardEvent.ctrlKey et al are built
# from; the key/code/vk dispatch the modifier's own press and
# release so a shortcut looks like a person's, not a bare bitmask.
_MODIFIER_DISPATCH = {
    "ctrl": ("Control", "ControlLeft", 17, 2),
    "control": ("Control", "ControlLeft", 17, 2),
    "alt": ("Alt", "AltLeft", 18, 1),
    "option": ("Alt", "AltLeft", 18, 1),
    "meta": ("Meta", "MetaLeft", 91, 4),
    "command": ("Meta", "MetaLeft", 91, 4),
    "cmd": ("Meta", "MetaLeft", 91, 4),
    "super": ("Meta", "MetaLeft", 91, 4),
    "shift": ("Shift", "ShiftLeft", 16, 8),
}
_MODIFIER_ORDER = ("ctrl", "alt", "meta", "shift")


def _parse_key(spec):
    """Resolve a key spec ('Enter', 'Ctrl+c', 'Alt+F4') to
    (modifiers, key, code, vk, text) for `Browser.key`."""
    tokens = spec.split("+")
    modifiers = 0
    key_spec = spec.strip()
    if len(tokens) > 1:
        for token in tokens[:-1]:
            token = token.strip().lower()
            if token not in _MODIFIER_DISPATCH:
                raise ValueError(f"unknown modifier in key: {token!r}")
            modifiers |= _MODIFIER_DISPATCH[token][3]
        # "Ctrl++" splits to an empty last token: a literal plus.
        key_spec = tokens[-1].strip() or "+"
    lowered = key_spec.lower()
    if lowered in _NAMED_KEYS:
        key, code, vk = _NAMED_KEYS[lowered]
        return modifiers, key, code, vk, None
    if len(key_spec) == 1:
        if key_spec.isalpha():
            code = "Key" + key_spec.upper()
        elif key_spec.isdigit():
            code = "Digit" + key_spec
        elif key_spec == " ":
            code = "Space"
        else:
            code = key_spec
        vk = ord(key_spec) if ord(key_spec) < 256 else 0
        return modifiers, key_spec, code, vk, key_spec
    raise ValueError(
        f"unknown key: {key_spec!r} - use a named key (Enter, Tab, "
        "Escape, ArrowUp, F5, ...) or a single character")


def _box_script(selector):
    # scrollIntoView with behavior 'instant' completes the
    # scroll synchronously, so the box measured right after it is where the
    # element sits when the click is dispatched. (A smooth scroll
    # still in flight is what made shani-testbed's clicks land on the
    # wrong element - measured once, 2026-10-05.)
    return (
        "(() => {\n"
        f"  const element = document.querySelector({json.dumps(selector)});\n"
        "  if (!element) throw new Error('Element not found');\n"
        "  element.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});\n"
        "  const r = element.getBoundingClientRect();\n"
        "  return {x: r.left + r.width / 2, y: r.top + r.height / 2};\n"
        "})()"
    )


def _focus_script(selector):
    # Focus before Input.insertText: insertText goes to whatever
    # element has focus, and a direct .value assignment would fire no
    # input event at all, so framework-controlled inputs (React, Vue)
    # would never see the edit. This is the same sequence
    # shani-testbed's web_features uses for typing.
    return (
        "(() => {\n"
        f"  const element = document.querySelector({json.dumps(selector)});\n"
        "  if (!element) throw new Error('Element not found');\n"
        "  element.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});\n"
        "  element.focus();\n"
        "  return true;\n"
        "})()"
    )


def _element_point(browser, selector, verb):
    """Measure an element's centre for click/hover/drag: ((x, y), None)
    on success, (None, message) on failure."""
    try:
        result = browser.evaluate(_box_script(selector))
    except RuntimeError as e:
        return None, f"Could not {verb} '{selector}': {e}"
    if not isinstance(result, dict) or "x" not in result or "y" not in result:
        return None, f"Could not {verb} '{selector}': no coordinates returned"
    return (result["x"], result["y"]), None


# Global browser instance for the skill
_current_browser = None

# Skill schema
SCHEMA = {
    "type": "function",
    "function": {
        "name": "browse",
        "description": (
            "Drive a real headless Chromium over the DevTools protocol: "
            "navigate, click, type, press keys, scroll, hover, drag, "
            "select options, upload files, move through history and tabs, "
            "evaluate JavaScript, take screenshots and wait for conditions. "
            "Uses a persistent browser session. Requires the web sense to "
            "be enabled and privacy mode to be off."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "navigate", "click", "type", "key", "scroll", "hover",
                        "drag", "select", "upload_file", "back", "forward",
                        "reload", "stop", "new_tab", "close_tab", "switch_tab",
                        "list_tabs", "evaluate", "screenshot", "get_title",
                        "get_url", "wait", "wait_text",
                    ],
                    "description": "The action to perform.",
                },
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to (navigate, new_tab).",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector (click, type, hover, drag, scroll, select, upload_file).",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (type), a JavaScript expression (evaluate), or text to wait for (wait_text).",
                },
                "key": {
                    "type": "string",
                    "description": "Key to press: a named key (Enter, Tab, Escape, ArrowUp, F5, ...) or a '+'-separated combination (Ctrl+c, Alt+F4, Shift+Tab).",
                },
                "value": {
                    "type": "string",
                    "description": "Option value to select (select action).",
                },
                "label": {
                    "type": "string",
                    "description": "Option visible text to select (select action, alternative to value).",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Absolute file paths to set on a file input (upload_file action).",
                },
                "x": {
                    "type": "number",
                    "description": "X coordinate in CSS pixels from the viewport's top-left (click, hover, scroll).",
                },
                "y": {
                    "type": "number",
                    "description": "Y coordinate in CSS pixels from the viewport's top-left (click, hover, scroll).",
                },
                "to_x": {
                    "type": "number",
                    "description": "Destination X coordinate for drag (alternative to to_selector).",
                },
                "to_y": {
                    "type": "number",
                    "description": "Destination Y coordinate for drag (alternative to to_selector).",
                },
                "to_selector": {
                    "type": "string",
                    "description": "CSS selector of the drag destination (drag action, alternative to to_x/to_y).",
                },
                "delta_x": {
                    "type": "number",
                    "description": "Horizontal scroll amount (scroll action; positive scrolls right).",
                },
                "delta_y": {
                    "type": "number",
                    "description": "Vertical scroll amount (scroll action; positive scrolls down, default 600). Give a selector or x/y to say where to scroll.",
                },
                "steps": {
                    "type": "integer",
                    "description": "Intermediate moves during a drag (default 10).",
                },
                "repeat": {
                    "type": "integer",
                    "description": "How many times to press the key (key action, default 1).",
                },
                "tab_id": {
                    "type": "string",
                    "description": "Tab id from list_tabs (switch_tab, close_tab).",
                },
                "path": {
                    "type": "string",
                    "description": "Path to save screenshot (screenshot action).",
                },
                "wait_for": {
                    "type": "string",
                    "description": "CSS selector to wait for (wait action).",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (default 30).",
                },
                "settle": {
                    "type": "number",
                    "description": "Max seconds to wait for a quiet network after navigation (default 5).",
                },
            },
            "required": ["action"],
        },
    },
}


def _run(arguments: dict) -> str:
    global _current_browser

    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return f"Web browsing is not permitted: {config.sense_allowed_reason('web')}."

    action = arguments.get("action", "navigate").lower()

    try:
        if _current_browser is None:
            timeout = arguments.get("timeout", 30)
            _current_browser = Browser(timeout=timeout)

        if action == "navigate":
            url = arguments.get("url", "")
            if not url:
                return "Please provide a URL to navigate to."
            settle = arguments.get("settle", 5)
            _, status, err = _current_browser.navigate(url, settle)
            if err:
                return f"Could not navigate to {url}: {err}"
            scheme = url.split(":", 1)[0].lower() if ":" in url else ""
            if scheme in ("http", "https"):
                # Same purpose string the built-in browser window uses,
                # so both read alike in the privacy panel.
                egress.record("skill:browse", url, status=status,
                              purpose="page-navigation")
            if status is not None:
                return f"Navigated to {url} (HTTP {status})"
            return f"Navigated to {url}"

        elif action == "click":
            selector = arguments.get("selector", "")
            x = arguments.get("x")
            y = arguments.get("y")

            if selector:
                point, err = _element_point(_current_browser, selector, "click")
                if err:
                    return err
                x, y = point
            elif x is None or y is None:
                return "Please provide either a selector or x,y coordinates for click."

            _current_browser.click(x, y)
            return f"Clicked at ({x}, {y})"

        elif action == "type":
            selector = arguments.get("selector", "")
            text = arguments.get("text", "")
            if not selector:
                return "Please provide a selector for typing."
            if not text:
                return "Please provide text to type."

            try:
                _current_browser.evaluate(_focus_script(selector))
            except RuntimeError as e:
                return f"Could not type into '{selector}': {e}"
            # Real keystrokes rather than assigning .value: a direct
            # assignment fires no input event, so framework-controlled
            # inputs (React, Vue) never see the edit.
            # Input.insertText is what shani-testbed's web_features
            # uses for the same reason.
            _current_browser.type(text)
            return f"Typed {len(text)} character(s) into '{selector}'"

        elif action == "key":
            spec = arguments.get("key", "")
            if not spec:
                return "Please provide a key to press."
            try:
                modifiers, key, code, vk, text = _parse_key(spec)
            except ValueError as e:
                return str(e)
            repeat = arguments.get("repeat", 1)
            repeat = max(1, int(repeat))
            for _ in range(repeat):
                _current_browser.key(modifiers, key, code, vk, text)
            return f"Pressed key: {spec}" + (f" x{repeat}" if repeat > 1 else "")

        elif action == "scroll":
            selector = arguments.get("selector", "")
            x = arguments.get("x")
            y = arguments.get("y")
            if selector:
                point, err = _element_point(_current_browser, selector, "scroll")
                if err:
                    return err
                x, y = point
            elif x is None or y is None:
                x = _current_browser.evaluate("Math.round(window.innerWidth / 2)")
                y = _current_browser.evaluate("Math.round(window.innerHeight / 2)")
            delta_x = arguments.get("delta_x", 0)
            delta_y = arguments.get("delta_y", 600)
            _current_browser.scroll(x, y, delta_x, delta_y)
            return f"Scrolled by ({delta_x}, {delta_y}) at ({x}, {y})"

        elif action == "hover":
            selector = arguments.get("selector", "")
            x = arguments.get("x")
            y = arguments.get("y")
            if selector:
                point, err = _element_point(_current_browser, selector, "hover over")
                if err:
                    return err
                x, y = point
            elif x is None or y is None:
                return "Please provide either a selector or x,y coordinates to hover over."
            _current_browser.hover(x, y)
            return f"Hovered at ({x}, {y})"

        elif action == "drag":
            selector = arguments.get("selector", "")
            if not selector:
                return "Please provide a selector for the element to drag."
            start, err = _element_point(_current_browser, selector, "drag")
            if err:
                return err
            steps = arguments.get("steps", 10)
            steps = max(1, int(steps))
            to_selector = arguments.get("to_selector", "")
            to_x = arguments.get("to_x")
            to_y = arguments.get("to_y")
            if to_selector:
                # Press on the element first, then scroll the
                # destination into view and measure it: the press must
                # land on the element and the release on the
                # destination, even when scrolling to the destination
                # moves the start off-screen.
                _current_browser.move(start[0], start[1])
                _current_browser.press(start[0], start[1])
                end, err = _element_point(_current_browser, to_selector, "drop on")
                if err:
                    _current_browser.release(start[0], start[1])
                    return err
                _current_browser.travel(start[0], start[1], end[0], end[1], steps)
                _current_browser.release(end[0], end[1])
                return f"Dragged '{selector}' to '{to_selector}' at ({end[0]}, {end[1]})"
            elif to_x is not None and to_y is not None:
                _current_browser.drag(start[0], start[1], to_x, to_y, steps)
                return f"Dragged '{selector}' to ({to_x}, {to_y})"
            return "Please provide a drag destination: to_selector or to_x/to_y."

        elif action == "select":
            selector = arguments.get("selector", "")
            value = arguments.get("value")
            label = arguments.get("label")
            if not selector:
                return "Please provide a selector for select."
            if value is None and label is None:
                return "Please provide a value or a label to select."
            by_label = value is None
            wanted = label if by_label else value
            script = (
                "(() => {\n"
                f"  const element = document.querySelector({json.dumps(selector)});\n"
                "  if (!element) throw new Error('Element not found');\n"
                "  if (element.tagName !== 'SELECT') throw new Error('Not a <select> element');\n"
                f"  const wanted = {json.dumps(wanted)};\n"
                f"  const byLabel = {json.dumps(by_label)};\n"
                "  let index = -1;\n"
                "  for (let i = 0; i < element.options.length; i++) {\n"
                "    const option = element.options[i];\n"
                "    if (byLabel ? option.label === wanted : option.value === wanted) { index = i; break; }\n"
                "  }\n"
                "  if (index < 0) {\n"
                "    return {matched: false, options: Array.from(element.options).map(o => o.value)};\n"
                "  }\n"
                "  element.selectedIndex = index;\n"
                "  element.dispatchEvent(new Event('change', {bubbles: true}));\n"
                "  return {matched: true, index: index, value: element.value};\n"
                "})()"
            )
            try:
                result = _current_browser.evaluate(script)
            except RuntimeError as e:
                return f"Could not select in '{selector}': {e}"
            if not isinstance(result, dict):
                return f"Could not select in '{selector}': no result"
            if result.get("matched"):
                return f"Selected '{result.get('value')}' in '{selector}' (option {result.get('index')})"
            options = result.get("options") or []
            return (f"No option matching '{wanted}' in '{selector}'; "
                    f"options are: {', '.join(options)}")

        elif action == "upload_file":
            selector = arguments.get("selector", "")
            files = arguments.get("files")
            if not selector:
                return "Please provide a selector for the file input."
            if not isinstance(files, list) or not files \
                    or not all(isinstance(f, str) and f for f in files):
                return "Please provide a non-empty list of file paths."
            try:
                _current_browser.upload_file(selector, files)
            except RuntimeError as e:
                return f"Could not set files on '{selector}': {e}"
            return f"Set {len(files)} file(s) on '{selector}'"

        elif action in ("back", "forward", "reload"):
            method = {"back": "Page.goBack", "forward": "Page.goForward",
                      "reload": "Page.reload"}[action]
            try:
                _current_browser.go(method)
            except Exception as e:
                return f"Error performing '{action}': {e}"
            return f"{action.capitalize()} done"

        elif action == "stop":
            _current_browser.cdp.send("Page.stopLoading")
            return "Stopped loading"

        elif action == "new_tab":
            url = arguments.get("url", "")
            try:
                tab_id, status = _current_browser.new_tab(url or None)
            except RuntimeError as e:
                return f"Could not open a new tab: {e}"
            if url:
                scheme = url.split(":", 1)[0].lower() if ":" in url else ""
                if scheme in ("http", "https"):
                    # Loaded by URL, so it is egress like navigate.
                    egress.record("skill:browse", url, status=status,
                                  purpose="page-navigation")
            msg = f"Opened tab {tab_id}"
            if url:
                msg += f" at {url}"
                if status is not None:
                    msg += f" (HTTP {status})"
            return msg

        elif action == "close_tab":
            tab_id = arguments.get("tab_id", "")
            try:
                _current_browser.close_tab(tab_id or None)
            except RuntimeError as e:
                return f"Could not close the tab: {e}"
            return f"Closed tab {tab_id or 'current'}"

        elif action == "switch_tab":
            tab_id = arguments.get("tab_id", "")
            if not tab_id:
                return "Please provide the tab_id to switch to (from list_tabs)."
            try:
                _current_browser.switch_tab(tab_id)
            except RuntimeError as e:
                return f"Could not switch to tab {tab_id}: {e}"
            return f"Switched to tab {tab_id}"

        elif action == "list_tabs":
            tabs = _current_browser.tabs()
            if not tabs:
                return "No tabs are open."
            listing = "\n".join(
                f"  {i + 1}. id={t['id']}  {t['title'] or '(untitled)'}  {t['url']}"
                for i, t in enumerate(tabs))
            current = _current_browser._target_id
            return f"{len(tabs)} tab(s) open (current: {current}):\n{listing}"

        elif action == "evaluate":
            text = arguments.get("text", "")
            if not text:
                return "Please provide JavaScript expression to evaluate."

            try:
                result = _current_browser.evaluate(text, await_promise=True, timeout=30)
                return f"Evaluation result: {result}"
            except Exception as e:
                return f"Error evaluating JavaScript: {str(e)}"

        elif action == "screenshot":
            path = arguments.get("path", "/tmp/screenshot.png")
            try:
                _current_browser.screenshot(path)
                return f"Screenshot saved to: {path}"
            except Exception as e:
                return f"Error taking screenshot: {str(e)}"

        elif action == "get_title":
            title = _current_browser.evaluate("document.title")
            return f"Page title: {title or 'Untitled'}"

        elif action == "get_url":
            url = _current_browser.evaluate("window.location.href")
            return f"Current URL: {url}"

        elif action == "wait":
            wait_for = arguments.get("wait_for", "")
            if not wait_for:
                return "Please provide an element selector to wait for."

            script = (
                "(() => {\n"
                f"  const element = document.querySelector({json.dumps(wait_for)});\n"
                "  return {found: !!element};\n"
                "})()"
            )
            timeout = arguments.get("timeout", 30)
            deadline = time.time() + timeout
            while True:
                result = _current_browser.evaluate(script)
                if result and result.get("found"):
                    return f"Element appeared: {wait_for}"
                if time.time() >= deadline:
                    return f"Timed out after {timeout}s waiting for: {wait_for}"
                time.sleep(0.5)

        elif action == "wait_text":
            needle = arguments.get("text", "")
            if not needle:
                return "Please provide text to wait for."

            script = (
                "(() => {\n"
                f"  const needle = {json.dumps(needle)};\n"
                "  return {found: (document.body.innerText || '').includes(needle)};\n"
                "})()"
            )
            timeout = arguments.get("timeout", 30)
            deadline = time.time() + timeout
            while True:
                result = _current_browser.evaluate(script)
                if result and result.get("found"):
                    return f"Text appeared: {needle}"
                if time.time() >= deadline:
                    return f"Timed out after {timeout}s waiting for text: {needle}"
                time.sleep(0.5)

        else:
            return f"Unknown action: {action}"

    except Exception as e:
        return f"Error performing action '{action}': {str(e)}"


SKILLS = [Skill(name="browse", schema=SCHEMA, run=_run)]
