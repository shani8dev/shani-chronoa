"""Driving any app's window the way `browse` drives a web page.

`browse` has one vocabulary - open, click, type, read, screen - because a page
publishes its own structure. A native app publishes one too, through AT-SPI, and
this module reads it and acts on it, so driving a container's GUI app is the same
shape as driving a page rather than a different craft.

It began in the container work - a container app needed a display bound
in and a bus shared before it could be driven at all - and it is
deliberately not container-specific: an app on this desktop is the same
problem, published on the same accessibility bus, and reached through the
same two input routes. The only container-specific half is where the
window comes from.

Four things make it work that are not true of an
arbitrary window:

- **The container's app can draw here at all.** `container run display=x11`
  binds the display's socket in and pins the toolkits to their X11 backends (the
  mechanism `shani-testbed`'s `lib/nspawn.sh` and
  `shani-desktop-plasma`'s session test both use), so the window is on this
  desktop where the person can see it.
- **The app's position is knowable.** `ui_elements` refuses to click a GTK4
  control when `WAYLAND_DISPLAY` is set, because no client may learn where a
  *native* Wayland window is. A container started with `display=x11` draws on
  XWayland, where it is an ordinary X window - so the position is knowable and
  that refusal would be wrong. `knowable()` decides by asking the display
  server, not by reading the session's variables.
- **The person watches.** Every click glides the cue layer's labelled cursor to
  the target and ripples before the real pointer moves, exactly as
  `gui/browser_driver.py` does inside a page.

Driving is *input*, so it needs the input key as well as the container key - the
same two-key shape `git_resolve` uses, with the escalation in the skill's code
rather than a second entry in `capabilities.GATED` (which maps one tool to one
key).
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import socket
import tempfile
import subprocess
import time

from shani_chronoa import automation_overlay as ao

logger = logging.getLogger(__name__)

#: How deep the accessibility tree is walked, and how many nodes it holds.
#: The depth is `ui_elements.MAX_DEPTH`'s own value - see `_walk_all` for the
#: measurement that showed 8 was silently dropping every control of a real
#: libadwaita app on this desktop.
_MAX_DEPTH = 30
_MAX_NODES = 800
#: How long a window has to appear after its container starts.
_WINDOW_TIMEOUT = 30
#: How long to wait for the cue layer's cursor to finish gliding.
_GLIDE_BUDGET = 1.5


class GuiUnavailable(Exception):
    """Why this cannot be driven; the caller turns it into one sentence."""


def _portal_input():
    """Input through the RemoteDesktop portal - the TeamViewer/AnyDesk route.

    That is not an analogy. A remote-desktop app on Wayland gets its pointer and
    keyboard exactly here: `org.freedesktop.portal.RemoteDesktop`, with PipeWire
    carrying the video. XTEST (`xdotool`) is the *X11* route, and it is what
    `container run display=x11` uses because the container's app is an XWayland
    window. A **native** Wayland window is reachable no other way - no client may
    inject input into another's surface - so this is the only sanctioned route,
    and the desktop shows the person a dialog before it grants.

    Already implemented in `portal.py` (`RemoteInput`); this is the same client
    through the driver's own error type, so the failure reads as one sentence.
    """
    try:
        from shani_chronoa import portal
    except Exception as exc:  # noqa: BLE001 - a module that cannot load is a refusal
        raise GuiUnavailable(f"the desktop's input portal is unavailable: {exc}") from exc
    try:
        # A short timeout on purpose. The portal client's own default is 60s,
        # and on a machine whose RemoteDesktop backend does not deliver, every
        # call after `Start` blocks for exactly that long - measured: a
        # two-character `type` never came back inside the skill's 30s budget.
        # A driver that hangs is a driver that stops, so this fails fast and the
        # refusal names what actually happened.
        return portal.RemoteInput(client=portal.PortalClient(timeout=5.0))
    except Exception as exc:  # noqa: BLE001 - PortalError and friends
        raise GuiUnavailable(
            f"this app is a native Wayland window, so driving it needs the "
            f"desktop's RemoteDesktop portal - the same mechanism TeamViewer and "
            f"AnyDesk use - and it did not deliver: {exc}. On a desktop whose "
            f"portal works it drives with no dialog once the person has allowed "
            f"it; on this one it must not be reported as working.") from exc


def _portal_sentence() -> str:
    """The one sentence explaining why XTEST is not enough here."""
    return (
        "this app is a native Wayland window, which no client may inject input "
        "into; the route that works is the desktop's RemoteDesktop portal (the "
        "mechanism TeamViewer and AnyDesk use), which asks the person to allow "
        "it. Start the container with display=x11 so its app is an XWayland "
        "window instead, and XTEST can drive it without a dialog.")


def portal_key(app: str, keys: str) -> str:
    """Send keys through the portal, for a window XTEST cannot reach.

    This is the real attempt, not the sentence: `portal.RemoteInput` runs the
    whole handshake and raises if the desktop does not grant. On this box it
    does not (measured: the RemoteDesktop `version` property reads empty and the
    session handle is unusable), so the honest outcome is a refusal - but the
    path is wired, and on a desktop whose portal works it drives without a
    dialog once the person has allowed it.

    Modifiers are a *chord*, not separate taps: ctrl-alt-f2 must press all three
    before releasing any of them, which is the difference between a chord and
    three keys.
    """
    from shani_chronoa import portal

    tokens = [t for t in keys.replace("-", "+").split("+") if t]
    if not tokens:
        raise GuiUnavailable("no keys were named")
    keysyms = []
    for token in tokens:
        keysym = portal.keysym_for(token)
        if keysym is None:
            raise GuiUnavailable(f"{token!r} is not a key name the portal can send")
        keysyms.append(keysym)
    try:
        with _portal_input() as remote:
            remote.chord(keysyms) if len(keysyms) > 1 else remote.tap(keysyms[0])
    except GuiUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - PortalError and friends
        raise GuiUnavailable(str(exc)) from exc
    # Not "sent". The portal accepted the call; nothing here can see a native
    # Wayland window well enough to confirm the key arrived anywhere, and a
    # driver that reports an unverified effect as delivered is the failure this
    # whole module exists to prevent.
    return (f"Asked the desktop's input portal to send {keys} to {app}. It "
            f"could not be confirmed that anything arrived - the app's window "
            f"is a native Wayland surface, which nothing but its compositor can "
            f"observe.")


def portal_click(app: str, x_percent: float, y_percent: float) -> str:
    """A click through the portal, for a window XTEST cannot reach.

    A pointer position is not knowable for a native Wayland window, so this
    refuses rather than guessing where the centre of the screen is: the portal
    moves the pointer in *its* coordinate space, and a click aimed at a guess
    lands somewhere in another app's window.
    """
    raise GuiUnavailable(
        "clicking inside a native Wayland window needs a pointer position, and "
        "no client may learn where one is on screen. Start the container with "
        "display=x11 so its app is an XWayland window, where the position is "
        "knowable, or drive it by keyboard through the portal instead.")


def portal_type(app: str, text: str) -> str:
    """Type through the portal, for a window XTEST cannot reach."""
    try:
        with _portal_input() as remote:
            remote.type_text(text)
    except GuiUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - PortalError and friends
        raise GuiUnavailable(str(exc)) from exc
    return (f"Asked the desktop's input portal to type {len(text)} character(s) "
            f"into {app}. It could not be confirmed that anything arrived - see "
            f"the app's own window, or start it with display=x11 so it can be "
            f"driven and read through XTEST instead.")


def _which(binary: str, verb: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise GuiUnavailable(f"{binary} is not installed, so a container's GUI "
                             f"app cannot be {verb}.")
    return path


def knowable() -> bool:
    """Whether an element's screen position can be known here.

    An X11 or XWayland window: yes. A native Wayland window: no. Decided by
    asking the display server for its geometry, because a session variable says
    nothing about where a particular app's window is.
    """
    if shutil.which("xdotool") is None:
        return False
    try:
        proc = subprocess.run(["xdotool", "getdisplaygeometry"],
                              capture_output=True, text=True, timeout=10,
                              check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def cue_socket(name: str) -> str:
    return ao.socket_path(f"container-{name}")


#: One cue layer per app, kept for the driving session. A layer per *call*
#: would make the highlight blink out between two clicks, which is the opposite
#: of what it is for.
_CUES: "dict[str, object]" = {}


#: What `start_cue` reports about the layer's visibility - **measured, not
#: assumed, and corrected twice.** The layer *does* draw: with `present()` it is
#: raised, and a before/after full-screen capture measured **5,845 pixels
#: changing from `(10,10,10)` to exactly `CUE_BLUE`, `(53,132,228)`**, when a
#: highlight was sent. That is the cue's own colour out of the module.
#:
#: It first said "gtk-layer-shell is not installed", which was **measured true
#: and then stopped being true** the moment the packages were installed - and
#: became false in a worse way, because the reason changed underneath it. The
#: library loads now; the *compositor* is what cannot provide the protocol. A
#: message that reports a cause rather than a state is the only one that
#: survives the next thing someone installs.
#:
#: What it cannot do is stay on top. It is an ordinary `Gtk.Window`, and an
#: ordinary window can be **raised over** by anything else that asks - measured
#: as the same probe returning 0 cue pixels on a second run with nothing changed
#: except that a terminal, running the probe, had raised itself since. Only a
#: **layer-shell surface** cannot be covered, and `GtkLayerShell` is not
#: installed here. So the honest sentence is "it draws and it is raised, but
#: anything that raises itself afterwards will cover it" - not "you will not see
#: it", which was the over-claim, and was wrong.
_CUE_LAYER_CAVEAT = (
    "the cue layer draws and has been raised, but it is an ordinary window: "
    "anything that raises itself afterwards - a terminal, a dialog - will cover "
    "it. Only a layer-shell surface cannot be covered, and this compositor "
    "cannot provide one: wlr-layer-shell is a wlroots/KDE protocol and Mutter "
    "does not implement it, so no package makes that possible here.")


def _cue_answers(path: str, timeout: float = 1.5) -> bool:
    """Whether something is already listening on this cue socket.

    **This is what stops the leak.** `_CUES` is a per-Python-process dict, so a
    layer started by one driving process is invisible to the next one - which
    means every process spawns its own, all racing to `bind()` the *same* path.
    Measured on this desktop after one session of driving: **116 processes**,
    with the socket owned by the oldest of them, hours stale. Only one can have
    bound it, so 115 were launched to fail and then sit there.

    Asking the socket instead of consulting a dict is the check that matches
    what is actually true.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            probe.connect(path)
            probe.sendall(b'{"ping": true}\n')
            return bool(probe.makefile("rb").readline())
    except OSError:
        return False


def start_cue(name: str):
    """The cue layer for this app, starting it only if one is not already there.

    Returns `(process_or_None, why_it_is_not_visible)`. `why` is empty when a
    layer is running **and it can draw over other windows**; when it cannot, it
    says so in words, because a driver that cannot show its work should say
    that rather than look as though it can.
    """
    running = _CUES.get(name)
    if running is not None and running.poll() is None:
        return running, _CUE_LAYER_CAVEAT
    path = cue_socket(name)
    if _cue_answers(path):
        # Someone else's layer is already on this socket and serving it. Do not
        # add a second one: the second cannot bind, so it is a process that runs
        # forever and does nothing.
        _CUES[name] = None
        return None, _CUE_LAYER_CAVEAT
    launcher = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "bin", "shani-chronoa-overlay"))
    if not os.path.exists(launcher):
        return None, "the cue layer is not installed, so the driving will not be visible"
    try:
        proc = subprocess.Popen([launcher, "--socket", path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True)
    except OSError as exc:
        return None, f"the cue layer would not start ({exc})"
    _CUES[name] = proc
    return proc, _CUE_LAYER_CAVEAT


def show_window(app: str, box) -> None:
    """Mark the window being driven, so the person can see which one it is."""
    if not box:
        return
    start_cue(app)
    ao.cue(cue_socket(app), {"highlight": list(box)})


#: Characters a regex reads as syntax. `xdotool search --name` compiles its
#: argument as one, so a window title is *not* a literal string to it.
_REGEX_METACHARACTERS = r"\.^$*+?()[]{}|"


def _name_pattern(title: str) -> str:
    """`title` as a pattern that matches itself and nothing else.

    Measured, and it is not a Slack quirk: `xdotool search --name` compiles its
    argument as a POSIX regex, so Slack's window title *"Nevin Perumana (DM) -
    Verteil Engineering - Slack"* reaches it as `Nevin Perumana DM - ...` -
    the parentheses are a capture group, not the two characters on screen - and
    the search returns **nothing** for a window that is plainly visible. The
    same title without its brackets matches. `gtk4-demo` and `xeyes` have none
    of these characters in their titles, which is why every earlier measurement
    passed and this only appeared against a real chat app.

    Escaping is the whole fix; the local exact/loose matching stays as it was.
    """
    return "".join("\\" + ch if ch in _REGEX_METACHARACTERS else ch for ch in title)


def _xdotool(args, timeout=30):
    if shutil.which("xdotool") is None:
        raise GuiUnavailable(_portal_sentence())
    return subprocess.run(["xdotool", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def _require_xtest() -> None:
    """Refuse with the portal sentence when this display cannot be driven by XTEST.

    A native Wayland window is not an X window, so `xdotool` cannot see it and
    XTEST cannot inject into it. Saying *that*, and naming the route that does
    work, is the difference between a refusal that teaches and one that only
    stops.
    """
    if not knowable():
        raise GuiUnavailable(_portal_sentence())


def find_window(title: str, timeout: float = _WINDOW_TIMEOUT, app: str = ""):
    """The first window matching, once it exists - by title, then by the app.

    Polled rather than checked once, because an app is still pulling
    itself up when `run` returns - measured: gtk4-demo's window appears seconds
    after the container is reported started.

    The `app` fallback is not defensive padding, it is a measured bug: an app
    may **rename its own window** while it runs (gtk4-demo retitles to the demo
    showing - measured, "GTK Demo" became "Application Class"), and a lookup
    holding the original title then finds nothing while the pixel read - which
    asks the app's accessibility frame - keeps working. So the title is asked
    again from the app itself before giving up.
    """
    _require_xtest()
    _which("xdotool", "found")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = _xdotool(["search", "--onlyvisible", "--name", _name_pattern(title)])
        names = {w: _xdotool(["getwindowname", w]).stdout.strip()
                 for w in proc.stdout.split()}
        for window_id, name in names.items():
            if name == title:
                return window_id
        for window_id, name in names.items():
            if _loose(title, name):
                return window_id
        if app:
            frame = _frame_title(app)
            if frame:
                for window_id, name in names.items():
                    if _loose(frame, name):
                        return window_id
        # **Last, and only for a chat-style app: match the unchanging tail.**
        # A window titled after whatever is open inside it stops matching the
        # moment the driver clicks something else in it, so an exact-and-loose
        # lookup cannot find it a second time - measured here as "no window
        # titled 'Nevin Perumana (DM) - ...' appeared within 30s" for a window
        # that was on screen the entire time, one conversation over.
        stem = _window_stem(title, app)
        if stem:
            for window_id, name in names.items():
                if _window_stem(name, app) == stem:
                    return window_id
            # **The title query itself filters by the title.** `xdotool search
            # --name` takes the title as a regex, so once the window has been
            # retitled the search returns *nothing* and there is no candidate for
            # the comparison above to succeed on - which is why the first version
            # of this fix changed nothing and still reported "no window appeared
            # within 30s" for a window that was on screen. So ask again by the
            # app's own class, which does not change.
            by_class = _xdotool(["search", "--onlyvisible", "--class", app])
            for window_id in by_class.stdout.split():
                name = _xdotool(["getwindowname", window_id]).stdout.strip()
                if _window_stem(name, app) == stem:
                    return window_id
        time.sleep(0.5)
    return None


def window_box(window_id: str):
    """(x, y, w, h) of a window on screen, or None."""
    proc = _xdotool(["getwindowgeometry", "--shell", window_id])
    if proc.returncode != 0:
        return None
    values = dict(line.split("=", 1) for line in proc.stdout.splitlines()
                  if "=" in line)
    try:
        return (int(values["X"]), int(values["Y"]),
                int(values["WIDTH"]), int(values["HEIGHT"]))
    except (KeyError, ValueError):
        return None


def windows(title: str = "", app_hint: str = ""):
    """Every visible window (optionally matching a title), with its geometry - the
    GUI equivalent of `browse`'s page list."""
    _which("xdotool", "listed")
    proc = _xdotool(["search", "--onlyvisible", "--name", _name_pattern(title or ".")])
    found = []
    for window_id in proc.stdout.split():
        name = _xdotool(["getwindowname", window_id]).stdout.strip()
        found.append((window_id, name, window_box(window_id)))
    if found:
        show_window(app_hint, found[0][2])
    return found


# --- reading: through the accessibility tree, using the reader this repo has ---


def _in_window(acc, x: float, y: float, w: float, h: float) -> bool:
    """True when a click at the element's centre would reach it.

    Three things checked, and the third is the one that cost the testbed a page:
    a window taller than the display has rows *inside its frame* that are off
    screen - Cassini's frame reported 760px while its sidebar ended at 750, so
    an item centred at 758 was "in the window" and the click landed on the
    window's border.
    """
    try:
        frame = _frame_of(acc)
        fx, fy, fw, fh = _screen_box(frame) or (0, 0, 0, 0)
        display = _display_size()
        cx, cy = x + w // 2, y + h // 2
        if not (max(fx, 0) <= cx <= min(fx + fw, display[0]) - 1
                and max(fy, 0) <= cy <= min(fy + fh, display[1]) - 1):
            return False
        # inside every scrolled pane around it, too
        node, hops = acc.get_parent(), 0
        while node is not None and hops < 30:
            if (node.get_role_name() or "") in ("scroll pane", "viewport"):
                box = _screen_box(node)
                if box and box[2] > 0 and box[3] > 0:
                    px, py, pw, ph = box
                    if not (px <= cx <= px + pw and py <= cy <= py + ph):
                        return False
            node = node.get_parent()
            hops += 1
    except (AttributeError, TypeError, KeyError):
        return True
    return True


def _display_size():
    """The X display's size, so a window taller than it can be recognised."""
    try:
        proc = subprocess.run(["xdotool", "getdisplaygeometry"],
                              capture_output=True, text=True, timeout=5, check=False)
        parts = proc.stdout.split()
        return int(parts[0]), int(parts[1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return (10 ** 6, 10 ** 6)


def _bring_into_view(acc):
    """Make an unreachable element reachable, and return where it ended up.

    The chain is `shani-testbed`'s `a11y_client._bring_into_view`, copied rather
    than re-invented, because the order is not arbitrary: AT-SPI `scroll_to`
    works on GTK3/Qt, **focusing** works on GTK4 (which has no `scroll_to` but
    scrolls a focused child into view), and the mouse wheel over the element's
    own column - not the window centre, which is another pane - is the last
    resort. Each attempt re-measures, so the answer is where the element *is*,
    not where it was asked to be.
    """
    _a11y_env()
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return None
    comp = acc.get_component_iface()
    if comp is not None:
        for attempt in ("scroll_to", "focus"):
            try:
                if attempt == "scroll_to":
                    comp.scroll_to(Atspi.ScrollType.ANYWHERE)
                else:
                    comp.grab_focus()
                time.sleep(0.4)
                box = _screen_box(acc)
                if box and _in_window(acc, *box):
                    return box
            except (AttributeError, TypeError, KeyError):
                continue
    # The wheel, over the element's own column.
    try:
        frame = _frame_of(acc)
        fbox = _screen_box(frame) or (0, 0, 0, 0)
        display = _display_size()
        fw = min(fbox[2], display[0] - max(fbox[0], 0))
        fh = min(fbox[3], display[1] - max(fbox[1], 0))
        for _attempt in range(60):
            box = _screen_box(acc)
            if box is None:
                break
            below = (box[1] + box[3] // 2) > fbox[1] + fh // 2
            wx = min(max(box[0] + box[2] // 2, max(fbox[0], 0) + 5),
                     max(fbox[0], 0) + fw - 5)
            wy = max(fbox[1], 0) + fh // 2
            _xdotool(["mousemove", str(wx), str(wy), "click", "5" if below else "4"])
            time.sleep(0.08)
            box = _screen_box(acc)
            if box and _in_window(acc, *box):
                return box
    except (OSError, subprocess.SubprocessError):
        pass
    box = _screen_box(acc)
    return box if box and _in_window(acc, *box) else None


def _window_stem(title: str, app: str = "") -> str:
    """The part of a window title that does not change while you use the app.

    Measured, and this was a whole class of failure rather than one odd title.
    A chat client titles its window after **whatever is open in it**, so the
    title was `"Nevin Perumana (DM) - Verteil Engineering - Slack"`, the driver
    clicked a different conversation, and the title became `"deepu (DM) - ..."` -
    after which the same `find_window` call found **nothing** and reported "no
    window appeared", naming a 30-second timeout, when the window had been there
    the whole time. A driver that can only find a window by the title it had
    before the driver itself changed the title cannot do anything twice.

    So the identifying part is the tail: the app name and whatever workspace it
    is in, which is constant for the life of the window. `Nevin Perumana (DM) -
    Verteil Engineering - Slack` and `deepu (DM) - Verteil Engineering - Slack`
    share `VerteilEngineeringSlack`, and both match any title naming that app.

    Returns "" when there is nothing to fall back on, so a plain title still
    matches exactly as before.
    """
    if not app:
        return ""
    squashed_app = "".join(ch for ch in app.lower() if ch.isalnum())
    if not squashed_app or squashed_app not in "".join(
            ch for ch in title.lower() if ch.isalnum()):
        return ""
    parts = [p for p in title.split("-") if p.strip()]
    # The **last two** segments: the workspace and the app name, both of which
    # stay put while the leading one changes. Taking only the app name was the
    # first attempt and it is wrong in the other direction - it would match a
    # Slack window belonging to a different workspace, which on a machine with
    # several workspaces is the wrong window rather than no window.
    tail = parts[-2:] if len(parts) >= 2 else parts
    return "".join("".join(ch for ch in p.lower() if ch.isalnum()) for p in tail)


def _loose(a: str, b: str) -> bool:
    """Do these two names mean the same window?

    Separator- and case-insensitive, because the name a model is given and the
    name a toolkit sets are not spelled the same way: the app here is
    `gtk4-demo` and its window is titled `GTK Demo`, and no exact or substring
    match joins them - which made the driver fall through to the portal route
    (a native-Wayland answer) for a window that was sitting right there on
    XWayland.
    """
    squash = lambda t: "".join(ch for ch in t.lower() if ch.isalnum())
    left, right = squash(a), squash(b)
    return bool(left) and (left in right or right in left)


def _frame_of(acc):
    frame = acc
    try:
        while frame.get_parent() is not None and \
                frame.get_parent().get_role_name() != "application":
            frame = frame.get_parent()
    except (AttributeError, TypeError):
        pass
    return frame


def element_accessor(app: str, name: str):
    """The accessibility element itself, so it can be focused or scrolled."""
    if not knowable():
        return None
    _a11y_env()
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
        for application in Atspi.get_desktop(0):
            if application is None or application.get_name() != app:
                continue
            return _walk(application, name)
    except (AttributeError, TypeError, KeyError) as exc:
        logger.debug("element %r could not be read from %s: %s", name, app, exc)
    return None


def element_box(app: str, name: str):
    """An element's screen position, or None when the app publishes none.

    The a11y bus is pointed at explicitly: a skill child inherits no
    `AT_SPI_BUS_ADDRESS`, and dbind then *guesses* a socket path from
    `$XDG_RUNTIME_DIR` that is not the real one - measured as
    `/run/user/1001/at-spi2-E114W3/socket` against the bus at
    `/run/user/1001/at-spi/bus`, which made every element read as absent while
    the same lookup worked from the desktop's own process.
    """
    accessor = element_accessor(app, name)
    return _screen_box(accessor) if accessor is not None else None


def _a11y_env() -> None:
    a11y = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "at-spi", "bus")
    if os.path.exists(a11y):
        os.environ.setdefault("AT_SPI_BUS_ADDRESS", f"unix:path={a11y}")


def _walk_all(node, depth: int = 0, out=None):
    """Every element under an app, depth-first, capped - the whole tree once.

    Capped so a deep list cannot make one observation cost a model its whole
    context. **Both caps are measured, and the depth one was wrong.** This
    desktop's own gnome-text-editor publishes its buttons at depth **17-18**
    (467 nodes, deepest 26) - so a cap of 8 discarded **all 55** of its
    actionable named controls and the observation reported "none this window
    can act on by name", which is a confident wrong answer about an app whose
    window is full of them. `ui_elements` has always used 30; the driver's own
    copy did not, and only this desktop's deeper libadwaita tree could show it
    - the container app that was tested first (gtk4-demo) nests its buttons at
    depth 5, so a cap of 8 looked right.

    The node cap is what keeps 30 affordable: ui_elements' own bound is
    `MAX_ELEMENTS * 4` against 300, and 800 here leaves the measured 467-node
    tree whole while stopping a pathological one.
    """
    if out is None:
        out = []
    if node is None or depth > _MAX_DEPTH or len(out) >= _MAX_NODES:
        return out
    out.append(node)
    try:
        for index in range(node.get_child_count()):
            _walk_all(node.get_child_at_index(index), depth + 1, out)
    except (AttributeError, TypeError, KeyError):
        pass
    return out


def _selected(application) -> str:
    """The row of a list that is selected - what the app is currently showing."""
    for node in _walk_all(application):
        try:
            if node.get_role_name() == "list item":
                from gi.repository import Atspi
                if Atspi.StateType.SELECTED in node.get_state_set().get_states():
                    return (node.get_name() or "").strip()
        except (AttributeError, TypeError, KeyError):
            continue
    return ""


def _walk(node, name: str):
    if node is None:
        return None
    try:
        if node.get_role_name() and name.lower() in (node.get_name() or "").lower():
            return node
        for index in range(node.get_child_count()):
            found = _walk(node.get_child_at_index(index), name)
            if found is not None:
                return found
    except (AttributeError, TypeError, KeyError):
        return None
    return None


def _screen_box(acc):
    """Screen extents, with GTK4's window-relative ones corrected by its frame -
    the same correction `ui_elements` and shani-testbed's a11y_client use."""
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        app = acc.get_application()
        gtk4 = app.get_toolkit_name() == "GTK" and str(
            app.get_toolkit_version()).startswith("4")
        if not gtk4:
            rect = Atspi.Component.get_extents(acc, Atspi.CoordType.SCREEN)
            return (rect.x, rect.y, rect.width, rect.height)
        window = Atspi.Component.get_extents(acc, Atspi.CoordType.WINDOW)
        frame = acc
        while frame.get_parent() is not None and \
                frame.get_parent().get_role_name() != "application":
            frame = frame.get_parent()
        frame_box = Atspi.Component.get_extents(frame, Atspi.CoordType.WINDOW)
        screen = Atspi.Component.get_extents(frame, Atspi.CoordType.SCREEN)
        return (screen.x + window.x - frame_box.x,
                screen.y + window.y - frame_box.y,
                window.width, window.height)
    except Exception as exc:  # noqa: BLE001 - see below
        # The a11y bus raises `GLib.GError` for an object that publishes no
        # Component interface at all - measured on this desktop's own
        # gnome-text-editor, whose frame walk ends at the root object
        # (`/org/a11y/atspi/accessible/root`), and the tuple this used to catch
        # (`AttributeError`, `TypeError`, `KeyError`) does not hold it, so the
        # whole observation came back as a traceback instead of a reading. The
        # sibling reader in `ui_elements` has caught broadly here all along,
        # which is why only the driver's copy ever crashed; a position that
        # cannot be read is `None`, never an exception.
        logger.debug("no screen box for this element: %s", exc)
        return None


def observation(app: str, max_controls: int = 12, max_screen: int = 160) -> str:
    """A compact, numbered view of what the app is showing and what can be used.

    This exists because of a measurement, not a preference: a full accessibility
    tree for one window is hundreds of lines - gtk4-demo's is 22 controls with
    ids like `0/0/0/1/0/4/0` - and a 1.7B model given that plus fifteen tools
    answers nothing useful. `tool_select`'s own result in this repo is 0/62 with
    every schema offered against 56/62 with a few. So this is the *result* side
    of the same bargain: one short screen, numbered controls the model can name
    by a single digit, and the text that is actually on screen.

    The numbering is a function of the walk, never of process state, so the
    same tree numbers the same way on the next call - that is what lets "click
    3" mean the same control in a later call. A control whose window changed
    since the last read is renumbered, which the reply says.
    """
    _a11y_env()
    lines = [f"app {app}"]
    box = None
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
        application = None
        for candidate in Atspi.get_desktop(0):
            if candidate is not None and candidate.get_name() == app:
                application = candidate
                break
        if application is None:
            raise GuiUnavailable(f"{app} is not on the accessibility bus")
        frame = _frame_of(application)
        box = _screen_box(frame) if frame is not None else None
        if box:
            lines.append(f"window {box[2]}x{box[3]}")
        selected = _selected(application)
        focus = _focused_name(application)
        if selected:
            lines.append(f"selected {selected!r}")
        if focus:
            lines.append(f"focused {focus!r}")
        lines.append("controls:")
        shown = 0
        for node in _walk_all(application):
            if shown >= max_controls:
                break
            if not _actionable(node):
                continue
            shown += 1
            role = (node.get_role_name() or "").replace("push button", "button")
            name = (node.get_name() or "").strip()
            if not name:
                continue
            lines.append(f"  {shown} {role} {name[:44]}")
        if shown == 0:
            lines.append("  (none this window can act on by name)")
    except GuiUnavailable:
        raise
    except (AttributeError, TypeError, KeyError) as exc:
        logger.debug("observation of %s failed: %s", app, exc)
    try:
        seen = screen_text(app)
    except GuiUnavailable:
        seen = ""
    if seen:
        lines.append(f"screen: {seen[:max_screen]}")
    return "\n".join(lines)


def _actionable(node) -> bool:
    """Can this control be pressed or typed into, by its name alone?"""
    try:
        role = node.get_role_name() or ""
        if "button" in role or role in ("menu item", "check menu item",
                                        "radio menu item", "link", "switch"):
            return bool((node.get_name() or "").strip())
        return role in ("entry", "password", "text", "combo box") and \
            bool((node.get_name() or "").strip())
    except (AttributeError, TypeError):
        return False


def _focused_name(application) -> str:
    for node in _walk_all(application):
        try:
            from gi.repository import Atspi
            if Atspi.StateType.FOCUSED in node.get_state_set().get_states():
                name = (node.get_name() or "").strip()
                if name:
                    return name
        except (AttributeError, TypeError, KeyError):
            continue
    return ""


def control_by_number(app: str, number: int):
    """The Nth actionable control, numbered exactly as `observation` numbers it.

    Reproducible by construction: both walk the tree in the same order with the
    same filter, so the number the model was shown is the number that comes back
    - without any state kept between two calls, which cannot exist across a
    subprocess anyway.
    """
    _a11y_env()
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
        for application in Atspi.get_desktop(0):
            if application is None or application.get_name() != app:
                continue
            count = 0
            for node in _walk_all(application):
                if not _actionable(node):
                    continue
                count += 1
                if count == number:
                    return node
            return None
    except (AttributeError, TypeError, KeyError) as exc:
        logger.debug("control %d of %s could not be read: %s", number, app, exc)
    return None


def drive(action: str, app: str = "", image: str = "", title: str = "",
          number: "int | None" = None, text: str = "", keys: str = "",
          program: str = "") -> str:
    """One turn of driving: an action in, a compact observation out.

    The whole point is the shape. A model that must call `container` four times
    per step - read, then click, then read again, then decide - spends its
    context on tool round trips instead of on the app. This is **one** call that
    does the reading for it and returns one screen small enough to reason over,
    which is the same bargain `tool_select` makes on the schema side and this
    repo measured twice.

    The reply always ends with the observation, so "what do I do next" is
    answered by the same message that reports what the last action did.
    """
    if action == "start":
        if not image:
            raise GuiUnavailable("start needs an image, e.g. localhost/gtk4-probe2")
        # `title` was being used as the container's command, which meant the
        # window title and the program were one string and neither could be
        # given independently. `program` names what to run; `title` names the
        # window to look for afterwards, and defaults to what the program is
        # called.
        program = str(program).strip() or str(title).strip() or "gtk4-demo"
        name = app or program
        from shani_chronoa import tools

        out = tools.execute_tool_outcome("container", {
            "action": "run", "image": image, "name": name,
            "detach": True, "display": "x11", "share_bus": True,
            "command": [program]})
        started = getattr(out, "text", str(out))
        if "Refusing" in started or "Not started" in started:
            return started
        # Wait for the *tree*, not for a fixed sleep: a window appears before the
        # app registers its accessibility tree, and the sleep was a guess that
        # was either too short (an empty control list, which then made a model
        # ask for a control that was not there) or wasted time.
        deadline = time.monotonic() + 45
        state = ""
        while time.monotonic() < deadline:
            try:
                state = observation(name)
            except GuiUnavailable:
                state = ""
            if "controls:" in state and "(none this window" not in state:
                break
            time.sleep(1.0)
        return f"{started}\n\n{state or observation(name)}"

    if not app:
        raise GuiUnavailable("say which app, as start named it")

    def after(result: str) -> str:
        """Every turn ends with the state, so the next decision is in hand.

        The version this replaces returned the action's result alone, so a
        `key` or `click` turn left the model with nothing to read - and a loop
        that must ask a second time "what happened?" is the four-round-trip
        design this whole action exists to avoid.
        """
        try:
            return f"{result}\n\n{observation(app)}"
        except GuiUnavailable as exc:
            return f"{result}\n\n(the app's state could not be read: {exc})"

    if action == "read":
        return observation(app)

    if action == "click":
        if number is None:
            raise GuiUnavailable(
                "click needs the number from the controls list - the reply "
                "always ends with one, so use the number shown there")
        accessor = control_by_number(app, int(number))
        if accessor is None:
            raise GuiUnavailable(
                f"control {number} is not in the list any more; the window has "
                f"changed since it was drawn. Read again and use the new "
                f"numbering")
        return after(click(app=app, title=title,
                           target=(accessor.get_name() or "")))

    if action == "type":
        if not text:
            raise GuiUnavailable("type needs the text to send")
        return after(type_text(app=app, title=title or app, text=text))

    if action == "key":
        if not keys:
            raise GuiUnavailable("key needs a combination, e.g. Down or ctrl+l")
        return after(key(app=app, title=title or app, keys=keys))

    raise GuiUnavailable(
        f"action must be start, read, click, type or key, not {action!r}")


def read(app: str) -> str:
    """What the app is showing, in the shape a decision can be made from.

    Two channels, because a native app publishes one and *draws* the other:

    - **the tree**, through `ui_elements` - the reader this repo already has,
      which reports each element's role, name, actions and selected state. It is
      what a control will do.
    - **the pixels**, through `screen_text` - what is actually on screen, OCR'd.
      A demo's own text, a canvas, a video, a page rendered by a toolkit that
      publishes none of it to the accessibility tree: all of those are invisible
      to the first channel and obvious to the second.

    `browse` reads a page's DOM and never needs to look at the pixels; a native
    app is the opposite, and a driver with only one of the two is guessing.
    """
    from shani_chronoa import tools

    out = tools.execute_tool_outcome("ui_elements", {"action": "inspect", "app": app})
    tree = getattr(out, "text", str(out))
    head = tree.strip().splitlines()[0] if tree.strip() else ""
    if "not on the accessibility bus" in head or "Unable to" in head[:60]:
        raise GuiUnavailable(head)

    lines = [tree.strip()]
    try:
        seen = screen_text(app)
    except GuiUnavailable:
        # A capture or OCR that cannot happen is *said*, not dropped: a reader
        # that silently returns one channel looks like an app with no text.
        seen = "(the pixels could not be read - see the tree)"
    lines.append("")
    lines.append("what is on screen (read from the pixels):")
    lines.append(seen.strip() or "(nothing legible; see the tree above)")
    return "\n".join(lines)


def screen_shot():
    """A full-screen capture, or None.

    **Full screen, not the app's window, and that is the whole point.** A window
    capture gives pixels in the *window's* space, so turning them into a screen
    coordinate means adding a window origin - and on GNOME that origin is the
    decoration frame's, which sits above the app's own client and *moves*.
    Measured on one Slack window: frame (67,32), client (67,106) in one run and
    (67,69) in the next. The same target computed from a window capture landed
    **37px** low, which is most of a sidebar row; the same target read from a
    full-screen capture opened the right conversation.

    The Screenshot portal is the only route that works here. The alternatives are
    each dead on this desktop, and all four were measured rather than assumed:
    `import -window root` fails with "missing an image filename", `grim` answers
    "compositor doesn't support wlr-screencopy-unstable-1", `xwd` is not
    installed, `gnome-screenshot` exits having written nothing, and the private
    `org.gnome.Shell.Screenshot` says **"Screenshot is not allowed"** - it is
    locked like `org.gnome.Shell.Eval`.

    The portal's signature is `(sa{sv})`: the **parent window argument is
    required**, and leaving it out is an `InvalidArgs` rather than a default.
    Its file is found by **mtime**, not by "a new file appeared" - it reuses
    `Screenshot-N.png`, so a set difference against the previous listing finds
    nothing.
    """
    pictures = os.path.join(os.path.expanduser("~"), "Pictures")
    pattern = os.path.join(pictures, "Screenshot*.png")

    def newest():
        found = [path for path in glob.glob(pattern) if os.path.isfile(path)]
        return max(found, key=os.path.getmtime) if found else None

    before = os.path.getmtime(newest()) if newest() else 0.0
    token = f"chron{int(time.time() * 1000)}"
    try:
        subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.portal.Desktop",
             "--object-path", "/org/freedesktop/portal/desktop", "--method",
             "org.freedesktop.portal.Screenshot.Screenshot", "",
             "{'interactive': <false>, 'handle_token': <'%s'>}" % token],
            capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        found = newest()
        if found and os.path.getmtime(found) > before:
            return _settled(found)
        time.sleep(0.25)
    return None


def _settled(path: str, patience: float = 8.0) -> "str | None":
    """Wait for a capture to stop growing, or give up and say so.

    **A file that is still being written is a truncated file**, and tesseract
    names that exactly: `Error in findFileFormatStream: truncated file`. The
    portal writes the PNG asynchronously, so a caller that waits only for the
    mtime to change gets the path back while the bytes are still arriving -
    which is how three captures of an idle screen gave `.tsv` files of 18,370 /
    18,198 / 18,070 bytes and then a fourth of the *same* screen gave **0**,
    with the image itself 1080 rows of real pixels every time.

    Returning `None` rather than the path is the honest answer: a capture that
    never settled is not a capture, and handing it on is how an OCR failure
    gets reported as "that text is not on screen".
    """
    deadline = time.monotonic() + patience
    last, stable = -1, 0
    while time.monotonic() < deadline:
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size > 0 and size == last:
            stable += 1
            if stable >= 2:          # the same size on three polls running
                return path
        else:
            stable = 0
        last = size
        time.sleep(0.25)
    return None


def frame_state(app: str):
    """`(active, focused, title)` for `app`'s frame, or None if it has none.

    **The accessibility tree's own answer to "is this window the one that has
    focus"** - and it is not the same question X asks. Measured on this desktop:
    Slack's frame reported `ACTIVE=False FOCUSED=False SHOWING=True` while
    `xdotool getactivewindow` named its client as active. Both were true of
    different things: X focus is the X server's, and what a **session-wide**
    click follows is the compositor's.

    That distinction is the whole reason a click can be delivered correctly and
    go nowhere.
    """
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        if not Atspi.is_initialized():
            Atspi.init()
        wanted = (app or "").lower()
        for desktop_app in Atspi.get_desktop(0):
            if not desktop_app:
                continue
            if wanted and wanted not in (desktop_app.get_name() or "").lower():
                continue
            for index in range(desktop_app.get_child_count()):
                frame = desktop_app.get_child_at_index(index)
                if not frame:
                    continue
                try:
                    states = frame.get_state_set()
                except Exception:  # noqa: BLE001 - an unreadable state is unknown
                    return (None, None, frame.get_name() or "")
                return (states.contains(Atspi.StateType.ACTIVE),
                        states.contains(Atspi.StateType.FOCUSED),
                        frame.get_name() or "")
    except Exception:  # noqa: BLE001 - no a11y bus is not an answer either way
        return None
    return None


def app_launchers(app: str):
    """Launcher ids for `app` that are **actually installed**, best match first.

    **The id was being guessed, and the guesses were all wrong.** `raise_app`
    tried `slack`, `com.slack` and `slack.desktop` on a machine where the app is
    **`com.slack.Slack`** - capital S, `.Slack`, no other form matches. So every
    raise launched a program that does not exist, and because `subprocess.Popen`
    raises only when the *binary* is missing, `flatpak run slack` "succeeded",
    reported a raise that raised nothing, and the click went wherever focus
    already was.

    Read from `flatpak list --app` rather than from a table here, because a
    hand-kept id table is wrong on the first machine whose app is not named the
    way the author's was. Ranked exact-then-tail-then-substring so `slack`
    prefers `com.slack.Slack` over an unrelated app that merely contains the
    word.
    """
    wanted = (app or "").strip().lower()
    if not wanted:
        return []
    try:
        listing = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application,name"],
            capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    exact, tail, loose = [], [], []
    for line in listing.splitlines():
        parts = line.split("\t", 1)
        app_id = parts[0].strip()
        name = (parts[1] if len(parts) > 1 else "").strip()
        low_id, low_name = app_id.lower(), name.lower()
        if low_id == wanted or low_name == wanted:
            exact.append(app_id)
        elif low_id.rsplit(".", 1)[-1] == wanted or low_name.replace(" ", "") == wanted:
            tail.append(app_id)
        elif wanted in low_id or wanted in low_name:
            loose.append(app_id)
    return (exact + tail + loose)[:4]


def raise_app(app: str):
    """Ask `app` to raise itself, with no extension and no privilege.

    **A second launch request makes a running instance raise its own window** -
    that is the whole mechanism, and on this desktop it is the *only* one that
    works: `org.gnome.Shell.FocusApp` answers `AccessDenied: FocusApp is not
    allowed`, `org.gnome.Shell.Eval` returns `(false, '')` even for `1+1`, and
    `xdotool windowactivate` fails with `_NET_WM_DESKTOP` errors.

    **Measured here, and the reason this function had to change:** the raise
    works, but **the focus it produces lasts about three seconds** and then
    returns to whatever had it - measured by polling, with the accessibility
    tree and X agreeing on every sample. Anything that raises and then spends
    twenty seconds looking something up has already lost the window by the time
    it clicks.

    Returns **the id it launched**, or `None` when nothing is installed under
    any name. It used to return a bool that was `True` for a launch that had
    failed; the return value is now the thing that was started, because a caller
    cannot verify a `True`.
    """
    for app_id in app_launchers(app):
        try:
            subprocess.Popen(["flatpak", "run", app_id],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return app_id
        except OSError:
            continue
    return None


def ensure_focused(app: str, wait: float = 3.0) -> "tuple[bool, str]":
    """Raise `app` and confirm the **compositor** gave it focus.

    Returns `(focused, why)`. `why` is empty on success and names the reason on
    failure, because "I could not make this window the one that has focus" is
    a sentence somebody has to act on and "the click did nothing" is not.

    **The two states it must not confuse.** A window that exists and is *not*
    focused is the case worth catching: a session-wide portal or uinput click
    goes to whatever the compositor has focused, so the click lands somewhere
    else and reports success. An app that publishes **no frame at all** is not
    that case - it is a container's X11 app, where "focused" is not a question
    anyone can answer - so it is let through rather than blocked on a premise
    the platform never offered. Returning `None` from `frame_state()` is the
    signal, and treating it as "not focused" would refuse every legitimate
    container click.

    **Confirmation is the point, and it is fast.** Measured on this desktop, a
    raise gives focus for roughly **three seconds** before it reverts, so this
    polls every 0.15 s rather than sleeping long enough to miss it. A raise
    that silently fails is exactly the case this exists to catch: the click
    then goes to whatever *was* focused, which is how a correctly-placed click
    arrives at the wrong window and reports success.
    """
    state = frame_state(app)
    if state is None:
        return True, ""                      # no frame: nothing to raise
    active, focused, _title = state
    if active or focused:
        return True, ""
    if raise_app(app) is None:
        return False, (
            f"{app!r} is not installed under any name this launcher can see, "
            f"so its window cannot be raised. Start it yourself and try again.")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.15)
        state = frame_state(app)
        if state is None:
            return True, ""
        active, focused, _title = state
        if active or focused:
            return True, ""
    return False, (
        f"{app}'s window could not be given focus, so a click would go to "
        f"whatever the compositor has focused instead. Raise it yourself and "
        f"try again.")


def _decode_png(path: str):
    """`(rows, channels, width, height)` from a PNG, with no image library.

    Every screen capture here is a PNG written by the portal, and the driver
    reads pixels - so this belongs in the module rather than in each probe.
    **Eight copies of this decoder were pasted into throwaway scripts during one
    session**, which is exactly the duplication this file warns about elsewhere.

    Returns `([], 0, 0, 0)` rather than raising on anything unreadable: a
    capture that cannot be decoded is a capture that was not read, and the
    callers all have an honest "could not tell" answer for that.
    """
    try:
        import struct
        import zlib
        data = open(path, "rb").read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return [], 0, 0, 0
        width, height = struct.unpack(">II", data[16:24])
        colour = data[25]
        chunks, position = b"", 8
        while position < len(data):
            length, kind = struct.unpack(">I4s", data[position:position + 8])
            if kind == b"IDAT":
                chunks += data[position + 8:position + 8 + length]
            position += 12 + length
        raw = zlib.decompress(chunks)
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
        stride = width * channels
        previous = bytearray(stride)
        rows, position = [], 0
        for _ in range(height):
            kind = raw[position]
            position += 1
            line = bytearray(raw[position:position + stride])
            position += stride
            if kind == 1:
                for x in range(channels, stride):
                    line[x] = (line[x] + line[x - channels]) & 255
            elif kind == 2:
                for x in range(stride):
                    line[x] = (line[x] + previous[x]) & 255
            elif kind == 3:
                for x in range(stride):
                    a = line[x - channels] if x >= channels else 0
                    line[x] = (line[x] + ((a + previous[x]) >> 1)) & 255
            elif kind == 4:
                for x in range(stride):
                    a = line[x - channels] if x >= channels else 0
                    b = previous[x]
                    c = previous[x - channels] if x >= channels else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[x] = (line[x] + pred) & 255
            rows.append(bytes(line))
            previous = line
        return rows, channels, width, height
    except (OSError, ValueError, KeyError, struct.error, zlib.error):
        return [], 0, 0, 0


#: Pixels kept clear of the screen edges, and why it is a constant rather than
#: a measurement. Measured on this desktop: the Ubuntu Dock is `x 0..~64` and the
#: top bar `y 0..~30`, and both are drawn **by GNOME Shell**, so the cue layer's
#: ordinary window composites **underneath** them - a label painted at `x < 64`
#: or `y < 30` is invisible.
#:
#: **A pixel heuristic for the panel edges was written and deleted.** It is not
#: a measurement that happens to be imprecise; it is *confidently wrong* in a
#: different state each run. Measured live on this desktop: `x = 64` and `x = 14`
#: from the same function on the same 1920x1080 screen, and once `top = 0` -
#: claiming no top bar at all. `dash-to-dock intellihide = true` means the dock's
#: presence is **state-dependent**, so its colour-based extent is not a
#: property of the machine. And there is no sanctioned way to ask GNOME for it:
#: `org.gnome.Shell.Eval` returns `(false, '')` even for `1+1` and
#: `FocusApp` answers `AccessDenied`.
#:
#: A constant is the honest instrument: **too large and the cue stays clear of
#: the panels; too small and the number is a measurement.** 96px and 48px are
#: the widest dock and tallest bar on GNOME, so they are the bounds that cannot
#: be exceeded.
EDGE_INSET_LEFT = 96
EDGE_INSET_TOP = 48


#: The screen size, measured once. **Cached because `safe_area()` was taking a
#: portal screenshot on every call** - ~20 seconds each - to learn a number it
#: already had: `tests/test_ui_driver.py` went from 0.7 s to **91 s**, which is
#: what surfaced it.
_SCREEN: "list[int] | None" = None


def _screen_size():
    """The screen's size, measured once, or `(0, 0)` when it cannot be read."""
    global _SCREEN
    if _SCREEN and len(_SCREEN) == 2:
        return tuple(_SCREEN)
    shot = screen_shot()
    if shot is None:
        return 0, 0
    _rows, _channels, width, height = _decode_png(shot)
    if width and height:
        _SCREEN = [width, height]
    return width, height


def safe_area(shot: "str | None" = None):
    """`(left, top, width, height)` of where a cue may safely be drawn.

    **The edges are kept clear of GNOME Shell's own surfaces**, because the cue
    layer is an ordinary window and draws underneath them - see
    `EDGE_INSET_LEFT` for the measurement and for why this is a constant.

    **Never smaller than the screen**, which is the answer that cannot move a
    click: a wrong work area would move real clicks.
    """
    if shot:
        _rows, _channels, width, height = _decode_png(shot)
    else:
        width, height = _screen_size()
    if not width or not height:
        return (0, 0, 0, 0)
    return (EDGE_INSET_LEFT, EDGE_INSET_TOP,
            max(1, width - EDGE_INSET_LEFT), max(1, height - EDGE_INSET_TOP))


def screen_words(region: "tuple[int, int, int, int]" = None,
                 attempts: int = 2):
    """Every word on screen, once, as `(x, y, w, h, text)`.

    **This exists because the obvious way round costs the price of a whole
    screen capture per question.** Measured on this desktop: one
    `org.freedesktop.portal.Screenshot` call plus `tesseract` over 1920x1080 is
    roughly **20 seconds**, and `screen_box` does exactly that - so looking up
    four sidebar rows to find one that is present cost **80 seconds** and four
    chances to catch the screen mid-repaint.

    A caller that wants to look at several things should take one capture and
    ask several questions of it. `screen_box` is this, filtered to one word, so
    the two cannot disagree about where anything is.

    `region` is `(left, top, width, height)` in **absolute screen
    coordinates**, and a word is returned only if its box lies **inside** it -
    a limit on two axes is not a rectangle, and comparing against the width and
    height as if they were bounds matched a word in a completely different
    window.
    """
    for attempt in range(max(1, attempts)):
        words = _screen_words_once(region)
        if words:
            return words
    return []


def _screen_words_once(region):
    shot = screen_shot()
    if shot is None:
        return []
    handle, png = tempfile.mkstemp(prefix="chronoa-screen-", suffix=".png")
    os.close(handle)
    try:
        shutil.copyfile(shot, png)
        base = png[:-4]
        # **`capture_output` swallows the only thing that explains a failure.**
        # tesseract writes "Estimating resolution as N" on success and an error
        # on failure, and discarding both left a 0-byte `.tsv` with nothing to
        # say why - which is how three different causes looked identical.
        done = subprocess.run(["tesseract", png, base, "tsv"],
                              capture_output=True, text=True, timeout=300,
                              check=False)
        found = []
        read = 0
        with open(base + ".tsv", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                parts = line.rstrip("\n").split("\t")
                # **The header can appear more than once.** Tesseract repeats
                # `left top width height` mid-file, and `int("left")` raises a
                # ValueError that is not an OCR failure and is not caught as
                # one - so a perfectly good capture turned into a traceback
                # that looked like the capture was the problem. The first column
                # is `level` on every data row and a word on a header.
                if not (len(parts) > 11 and parts[11].strip()
                        and parts[0].strip().isdigit()):
                    continue
                left, top = int(parts[6]), int(parts[7])
                width, height = int(parts[8]), int(parts[9])
                read += 1
                if region:
                    rx, ry, rw, rh = region
                    if (left < rx or top < ry or left + width > rx + rw
                            or top + height > ry + rh):
                        continue
                found.append((left, top, width, height, parts[11].strip()))
        # **"No words" and "no words *there*" are different answers, and only one
        # of them is a failure.** With a `region` supplied, an empty result is
        # the ordinary, correct answer - nothing in that rectangle - and saying
        # "the screen was not read at all" about it is a claim stronger than
        # anything measured. `read` counts the rows before the filter, which is
        # what separates the two.
        if not found and not read:
            logging.warning(
                "screen OCR produced no rows at all - tesseract rc=%s wrote "
                "nothing for %s, so the screen was not read. Its own words: %s",
                done.returncode, png,
                (done.stderr or done.stdout or "").strip()[:200] or "(silent)")
        return found
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        for path in (png, png[:-4] + ".tsv"):
            try:
                os.unlink(path)
            except OSError:
                pass


def _word_near(words, needle: str):
    """The first word in `words` containing `needle`, matched forgivingly.

    OCR is never exact at this size - `zzdirect` reads as `zadirect`, `ab cd` as
    `ab cq` - so a caller that needs certainty reads the pixels itself. This
    answers "roughly where", which is what a click on a list row needs.
    """
    needle = "".join(ch for ch in needle.lower() if ch.isalnum())
    if not needle:
        return None
    for left, top, width, height, text in words:
        word = "".join(ch for ch in text.lower() if ch.isalnum())
        if word and needle in word:
            return (left, top, width, height)
    return None


def screen_box(text: str, x: "int | None" = None, y: "int | None" = None,
               w: "int | None" = None, h: "int | None" = None,
               region: "tuple[int, int, int, int]" = None,
               attempts: int = 2, words=None):
    """Where `text` is on screen, read from a **full-screen** capture.

    Returns `(x, y, w, h)` in **absolute screen coordinates**, or None. This is
    the coordinate space a click needs: nothing has to be added to it, so none of
    the frame/client arithmetic can be wrong.

    **One capture answers one question unless you pass `words`.** See
    `screen_words`: a capture plus a full-screen OCR is ~20 seconds here, so
    looking for four rows one at a time costs four captures. Take
    `screen_words(region)` once and pass it in for every further lookup - the
    `region` here is then only a filter over words you already have.
    """
    if words is None:
        limit = tuple(region) if region else (
            (x, y, w, h) if w and h else None)
        words = screen_words(limit, attempts=attempts)
    return _word_near(words, text)


def screen_text(app: str) -> str:
    """What the app's window is showing, read by OCR from the actual pixels.

    Capture is ImageMagick's `import -window <id>`, for three measured reasons.
    **`grim` cannot capture on GNOME at all** - it speaks wlroots' screencopy
    protocol and Mutter does not implement it ("compositor doesn't support
    wlr-screencopy-unstable-v1", zero bytes, exit 1). **`gnome-screenshot` exits
    0 having written nothing** on this desktop - the documented
    confident-wrong-answer shape, and why the file is validated rather than
    assumed. And `import` captures the *window*, not the desktop, so no
    coordinate arithmetic and no compositor protocol at all.

    The window is found through the app's own frame title, not by matching the
    app's *name*: `xdotool search --name .` returns the compositor's own guard
    window first (measured: 1920x1080 at 0,0), and OCR'ing that would be reading
    the desktop and calling it the app.
    """
    if shutil.which("import") is None:
        raise GuiUnavailable("ImageMagick's import is not installed, so a "
                             "container's app cannot be read from its pixels")
    if shutil.which("tesseract") is None:
        raise GuiUnavailable("tesseract is not installed, so an app "
                             "cannot be read from its pixels")

    title = _frame_title(app)
    window = _window_by_title(title) if title else None
    if window is None:
        raise GuiUnavailable(f"no window for {app} to read")

    handle, capture = tempfile.mkstemp(prefix="chronoa-read-", suffix=".png")
    os.close(handle)
    try:
        proc = subprocess.run(["import", "-window", window, capture],
                              capture_output=True, text=True, timeout=60, check=False)
        # A capture that wrote nothing is *said*, not OCR'd into a guess - and
        # `gnome-screenshot` proves that exit 0 does not mean a file exists.
        if proc.returncode != 0 or not os.path.getsize(capture):
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise GuiUnavailable("the window could not be captured"
                                 + (f": {detail[-1]}" if detail else ""))
        proc = subprocess.run(["tesseract", capture, "stdout"],
                              capture_output=True, text=True, timeout=120,
                              check=False)
        if proc.returncode != 0:
            detail = proc.stderr.strip().splitlines()
            raise GuiUnavailable("OCR failed"
                                 + (f": {detail[-1]}" if detail else ""))
        text = " ".join(proc.stdout.split())
        if not text:
            raise GuiUnavailable(
                f"{app}'s window was captured but nothing in it was legible")
        return text
    finally:
        try:
            os.unlink(capture)
        except OSError:
            pass


def _frame_title(app: str) -> str:
    """The app's own window title, from the accessibility tree."""
    _a11y_env()
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
        for application in Atspi.get_desktop(0):
            if application is None or application.get_name() != app:
                continue
            for index in range(application.get_child_count()):
                frame = application.get_child_at_index(index)
                if frame is not None and frame.get_role_name() in (
                        "frame", "window", "internal frame"):
                    return frame.get_name() or ""
    except (AttributeError, TypeError, KeyError) as exc:
        logger.debug("could not read %s's frame title: %s", app, exc)
    return ""


def _window_by_title(title: str):
    proc = _xdotool(["search", "--onlyvisible", "--name", _name_pattern(title)])
    ids = proc.stdout.split()
    # The compositor's own guard window matches almost anything; an app's
    # window is the one whose title is *the* title, not a superset of it.
    for window_id in ids:
        name = _xdotool(["getwindowname", window_id]).stdout.strip()
        if name == title:
            return window_id
    return ids[0] if ids else None


# --- acting: show it, then do it ------------------------------------------------


def _cue_announce(app: str, box=None):
    """Show the person that Chronoa is about to act, and return the socket.

    Every route through this module calls it before it acts - the browse
    contract: a person watching sees the cursor labelled "Chronoa" glide to
    where the action lands and ripple as it happens. An action nobody can see
    is indistinguishable from one that happened to somebody else's machine.
    """
    show_window(app, box)
    cue, socket = start_cue(app), cue_socket(app)
    if cue is not None and box:
        ao.cue(socket, {"glide": [box[0] + box[2] / 2, box[1] + box[3] / 2]})
        ao.wait_until_pointing_at(socket, box[0] + box[2] / 2,
                                  box[1] + box[3] / 2)
    return cue, socket


#: Mouse buttons a caller may name, and the X11 button number each is. Present
#: so an unknown name is refused by name rather than becoming `xdotool click 7`.
_BUTTONS = {"left": 1, "middle": 2, "right": 3}


def _clear_of_shell_surfaces(x: float, y: float):
    """Nudge a cue away from GNOME Shell's own edges, and say so if it moved.

    **The dock and top bar are composited above the cue layer** - measured, the
    layer draws underneath them - so a label at `x < 64` or `y < 30` is not seen.
    `intellihide` also means the dock slides out over content, so a cue gliding
    through the left edge can cover what it was sent to point at.

    **The click itself is never moved**, only the *cue* is: the target pixel was
    read from a capture and moving it would click somewhere else. So the drawn
    pointer is held inside `safe_area()` and the press still goes to the
    coordinate asked for.
    """
    # **The constants, not `safe_area()`.** Only the two edges are needed here,
    # and asking for the area meant a portal screenshot on every glide - a
    # ~20 second cost per click for a pair of module-level integers.
    moved_x = x if x >= EDGE_INSET_LEFT else EDGE_INSET_LEFT
    moved_y = y if y >= EDGE_INSET_TOP else EDGE_INSET_TOP
    return (moved_x, moved_y)


def _glide_and_click(socket_path: str, x: float, y: float, box=None,
                     button: str = "left") -> bool:
    """Show the click, then make it. Returns whether the cursor visibly arrived."""
    x, y = _clear_of_shell_surfaces(x, y)
    if box:
        ao.cue(socket_path, {"ring": list(box)})
    ao.cue(socket_path, {"glide": [x, y]})
    arrived = ao.wait_until_pointing_at(socket_path, x, y, budget=_GLIDE_BUDGET)
    # Eased and settling, not a jump: the cue layer drew the glide, and the real
    # pointer cutting to the target the instant the drawing arrives is the one
    # thing a person watching would catch.
    landed = _move_like_a_hand(x, y)
    _hand_over_to_the_real_cursor(socket_path, landed)
    ao.cue(socket_path, {"ripple": [x, y]})
    # **Through the portal or uinput, never XTEST.** See `_pointer_routes`: on
    # Wayland an XTEST click exits 0, moves the X pointer, and is never seen by
    # the application - which is what made a click at the right pixel do nothing.
    _press_pointer("", x, y, button)
    return arrived


def _hand_over_to_the_real_cursor(socket_path: str, landed: bool) -> None:
    """Stop drawing the Chronoa cursor, now that the real one is there.

    **This is the second cursor.** The cue layer's arrow is the *glide* - it is
    how a person sees where Chronoa is going - and the system pointer is the
    actual input. For the length of the glide both are visible and moving
    together, which reads as one cursor. At rest, with both drawn, it reads as
    **two**, and two cursors on a desktop means either two agents or a computer
    driving its own mouse, neither of which is true.

    So the drawn one is retired the moment the real one has arrived, and **not**
    if it has not: an honest arrival failure is worth seeing, and hiding the
    only cursor that got there would leave a click that looks like it happened
    somewhere it did not.
    """
    if landed and socket_path:
        ao.cue(socket_path, {"hide": True})


def _is_an_ending(target: str) -> bool:
    """Would clicking this end the app? `monkey` skips exactly these.

    Ending the app is a person's decision, never a click a driver makes on the
    way past - which is the testbed's `monkey` rule, and a crash in a harness
    that otherwise looks like an app bug.

    Whole words only, and `exit fullscreen` excluded: that is a toggle, and a
    substring rule refuses a harmless control while claiming to protect the app.
    """
    words = set(re.findall(r"[a-z]+", target.lower()))
    if "fullscreen" in words and {"exit", "leave"} & words:
        return False
    return bool(words & {"close", "quit", "exit", "shutdown"}
                or ({"sign", "log"} & words) and "out" in words)


def click(app: str, title: str, target: "str | None" = None,
          x_percent: "float | None" = None, y_percent: "float | None" = None,
          window_id: "str | None" = None, button: str = "left",
          screen_x: "int | None" = None, screen_y: "int | None" = None) -> str:
    """Click in a container's GUI app: by an element's name, or by position.

    The element's name wins when the tree publishes a position for it - the same
    "drive it by what it is" that `browse` gets from the DOM. Position in
    percent is the fallback, and the reply says which was used.

    **`x_percent`/`y_percent` are percentages, 0-100.** Passing `0.079` where the
    code expects `7.9` puts the click **0.3 pixels** from the window's corner and
    still reports a click, which is how a whole afternoon was spent clicking the
    top-left of Slack. A value in 0..1 with a companion above 1 is not guessed
    at: it is refused, because the two readings differ by a factor of 100 and
    picking one silently is how this happened.

    `button` is `"left"`, `"right"` or `"middle"`. Right-click is not a separate
    verb because it is the same action on a different button, and a driver with
    `click` but no `right_click` makes every context menu two calls and one
    re-focus.
    """
    if _BUTTONS.get(button) is None:
        raise GuiUnavailable(
            f"{button!r} is not a mouse button this driver has; use "
            f"{', '.join(sorted(_BUTTONS))}")
    if x_percent is not None and not 0.0 <= x_percent <= 100.0:
        raise GuiUnavailable(
            f"x_percent={x_percent} is not a percentage - this is 0 to 100, so "
            f"7.9% is 7.9 and 0.079 is 0.079% (a fraction of a pixel)")
    if y_percent is not None and not 0.0 <= y_percent <= 100.0:
        raise GuiUnavailable(
            f"y_percent={y_percent} is not a percentage - this is 0 to 100")
    if (screen_x is None) != (screen_y is None):
        raise GuiUnavailable(
            "screen_x and screen_y go together; one without the other is a "
            "coordinate the click would guess the rest of")
    cue, _why = start_cue(app)
    try:
        if window_id is None:
            window_id = find_window(title, app=app)
        if window_id is None:
            raise GuiUnavailable(
                f"no window titled {title!r} appeared within {_WINDOW_TIMEOUT}s; "
                f"is the app running, and was it started with display=x11?")
        # **The client, never the decoration frame.** `find_window` answers with
        # the frame (`WM_CLASS "mutter-x11-frames"`), whose origin is above the
        # app's own - measured 74px on this desktop, and it *moves*, so it was
        # 37px in one run and 74px in the next. Every coordinate anyone measures
        # comes from a capture of the client, so adding the frame's origin to a
        # client's offset lands that far from the target: measured as a click
        # that opened the wrong conversation. Percentages are fractions of a box,
        # so the box has to be the one the pixels are in.
        box = window_box(_client_window(window_id))
        if box is None:
            raise GuiUnavailable(f"cannot read window {window_id}'s geometry")
        wx, wy, ww, wh = box

        if target:
            if _is_an_ending(target):
                raise GuiUnavailable(
                    f"{target!r} would end {app}; ending an app is a person's "
                    f"decision, not a click a driver makes on the way past")
            accessor = element_accessor(app, target)
            if accessor is not None:
                ex, ey, ew, eh = _screen_box(accessor) or (0, 0, 0, 0)
                if not (ew > 0 and eh > 0 and _in_window(accessor, ex, ey, ew, eh)):
                    # Off screen or not laid out yet: scroll it, focus it, or
                    # wheel it, exactly as the testbed's `a11y_client` does -
                    # and if all three fail, say so rather than clicking blind.
                    box = _bring_into_view(accessor)
                    if box is None:
                        raise GuiUnavailable(
                            f"{target!r} is not reachable in {app} - it is off "
                            f"screen and focusing, scrolling and the mouse wheel "
                            f"did not bring it into view")
                    ex, ey, ew, eh = box
                cx, cy = ex + ew / 2, ey + eh / 2
                arrived = _glide_and_click(cue_socket(app), cx, cy, box, button)
                return (f"{button.capitalize()}-clicked {target!r} in {app} at "
                        f"({int(cx)},{int(cy)})"
                        + ("" if arrived else " (the cursor had not visibly arrived)"))
            element = element_box(app, target)
            if element is not None and element[2] > 0 and element[3] > 0:
                ex, ey, ew, eh = element
                cx, cy = ex + ew / 2, ey + eh / 2
                arrived = _glide_and_click(cue_socket(app), cx, cy, box, button)
                return (f"{button.capitalize()}-clicked {target!r} in {app} at "
                        f"({int(cx)},{int(cy)})"
                        + ("" if arrived else " (the cursor had not visibly arrived)"))

        if window_id is None and find_window(title, app=app, timeout=10) is None:
            # A native Wayland client: no X window, so the portal is the route.
            return portal_click(app, x_percent or 0, y_percent or 0)
        # **Absolute screen coordinates, read from a full-screen capture.**
        # This is the branch to reach for on a real desktop: a window capture is
        # in the window's space, so its origin has to be added, and on GNOME
        # that origin is the moving decoration frame's - measured 37px and 74px
        # of disagreement on one window, which is most of a sidebar row. A
        # full-screen capture is already in screen coordinates, so nothing can be
        # added wrongly. `screen_box()` produces these.
        if screen_x is not None and screen_y is not None:
            # **Refuse a click at a point the app does not own.** The point came
            # from a full-screen capture, and a full-screen capture shows
            # whatever is on top - which on this desktop is very often this
            # agent's own terminal, because it raises itself the instant
            # anything runs from it. Measured: a click at a correct coordinate
            # did nothing for a whole session because the sidebar being OCR'd
            # was terminal output. A click that lands on the wrong window is
            # worse than a refused one, because it reports success.
            #
            # Only the words **under the click** are consulted, and only when
            # the capture produced some - "nothing to read" is not evidence
            # that the app is hidden, so the click proceeds as before rather
            # than being blocked by an unrelated failure.
            here = screen_words((screen_x - 90, screen_y - 14, 180, 28))
            said = " ".join(t for _, _, _, _, t in here).strip() if here else ""
            # **Focus is taken here, after the read and before the press.**
            # Both routes that deliver a click - the RemoteDesktop portal and
            # uinput - are session-wide, so they go to whatever the compositor
            # has focused. Measured on this desktop: a raise makes the app
            # focused for about **three seconds**, and the read above is a
            # full-screen capture plus OCR, which is **~20 seconds**. Raising
            # at the start of a call therefore loses the window before the
            # press, which is how correctly-placed clicks did nothing all
            # session. Order is the whole fix: read, raise, confirm, press.
            ok, why = ensure_focused(app)
            if not ok:
                raise GuiUnavailable(why)
            arrived = _glide_and_click(cue_socket(app), screen_x, screen_y, box,
                                        button)
            # **Correction to the note that used to be here.** It said the
            # frame's AT-SPI `ACTIVE` "disagrees with X focus", and declined to
            # guard on it for that reason. Measured while fixing this: the two
            # **agree on every sample** - both named Slack's client for the ~3s
            # a raise held focus, and both named the same 1x1 override-redirect
            # window at (-100,-100) when it did not. So `ACTIVE` *can* tell the
            # two cases apart, and `ensure_focused` above does guard on it. The
            # "a guard that cannot tell them apart" argument was true of the
            # other candidate (OCR-comparing the app name against a window whose
            # title is a person's name) and has been dropped here, not carried
            # forward as a general rule.
            #
            # The evidence still goes in the reply, where the caller can see
            # it: a click that lands somewhere else is then obvious rather than
            # hidden behind a confident sentence.
            return (f"{button.capitalize()}-clicked the screen at "
                    f"({int(screen_x)},{int(screen_y)}), which is in "
                    f"{app}'s window at ({int(screen_x - wx)},"
                    f"{int(screen_y - wy)}) of its {ww}x{wh}"
                    + (f"; that spot reads {said!r}" if said else "")
                    + ("" if arrived else " (the cursor had not visibly arrived)"))

        if x_percent is None or y_percent is None:
            raise GuiUnavailable(
                f"{app} publishes no position for {target!r}, and no x/y was "
                f"given. Either name an element the accessibility tree can place "
                f"on this XWayland display, or give x and y as percentages of "
                f"the window.")
        x, y = wx + ww * x_percent / 100, wy + wh * y_percent / 100
        arrived = _glide_and_click(cue_socket(app), x, y, box, button)
        return (f"{button.capitalize()}-clicked {x_percent:g}%,{y_percent:g}% of "
                f"{app}'s window ({int(x)},{int(y)})"
                + ("" if arrived else " (the cursor had not visibly arrived)"))
    finally:
        _stop_cue(cue)


def scroll(app: str, title: str, direction: str = "down", amount: int = 3,
           x_percent: "float | None" = None, y_percent: "float | None" = None,
           window_id: "str | None" = None) -> str:
    """Scroll a window, showing where the wheel went first.

    The cursor is glided to the point being scrolled and the wheel turned there,
    so a person watching sees *which* pane moved - a wheel over the window's
    centre is a different action from one over a sidebar, and the centre is
    usually the wrong pane.

    `direction` is `"up"`, `"down"`, `"left"` or `"right"`; `amount` is notches.
    X11 wheel buttons are 4/5 (up/down) and 6/7 (left/right), which is why this is
    a named mapping rather than a number a caller passes.
    """
    wheel = {"up": 4, "left": 6, "right": 7, "down": 5}
    if wheel.get(direction) is None:
        raise GuiUnavailable(
            f"{direction!r} is not a direction; use {', '.join(sorted(wheel))}")
    if not 1 <= int(amount) <= 50:
        raise GuiUnavailable(
            f"amount={amount} is outside 1-50 notches; more than that is a "
            f"gesture, not a scroll")
    cue, _why = start_cue(app)
    try:
        if window_id is None:
            window_id = find_window(title, app=app)
        if window_id is None:
            raise GuiUnavailable(
                f"no window titled {title!r} appeared within {_WINDOW_TIMEOUT}s; "
                f"is the app running, and was it started with display=x11?")
        box = window_box(_client_window(window_id))
        if box is None:
            raise GuiUnavailable(f"cannot read window {window_id}'s geometry")
        wx, wy, ww, wh = box
        x = wx + ww * ((x_percent if x_percent is not None else 50.0) / 100.0)
        y = wy + wh * ((y_percent if y_percent is not None else 50.0) / 100.0)
        _glide_and_point(cue_socket(app), x, y, box)
        # **A wheel notch is a click on button 4/5/6/7**, and it is delivered the
        # same way every other press is - through the portal or uinput. XTEST
        # would report success and scroll nothing, for the reason
        # `_pointer_routes` sets out at length.
        notches = wheel[direction]
        for _notch in range(int(amount)):
            _scroll_notch(app, x, y, notches)
            time.sleep(0.05)
        return (f"Scrolled {direction} {amount} notch(es) over {app} at "
                f"({int(x)},{int(y)})")
    finally:
        _stop_cue(cue)


def _glide_and_point(socket_path: str, x: float, y: float, box=None) -> bool:
    """Glide the cursor to a place and ring it, **without clicking**.

    Separate from `_glide_and_click` because a wheel event is a click on button
    4/5/6/7, not a click: running the same helper would press the left button
    first and select whatever the scroll was meant to be over.
    """
    if box:
        ao.cue(socket_path, {"ring": list(box)})
    ao.cue(socket_path, {"glide": [x, y]})
    arrived = ao.wait_until_pointing_at(socket_path, x, y, budget=_GLIDE_BUDGET)
    landed = _move_like_a_hand(x, y)
    _hand_over_to_the_real_cursor(socket_path, landed)
    return arrived


def _typing_routes(app: str):
    """The ways to type into `app`, in the order they are worth trying, named.

    Ordered by what the platform sanctions, not by what happens to work:

    1. **`ydotool` (uinput)** - **measured to deliver on this desktop**, where
       both others do not. It writes to `/dev/uinput`, so the events enter
       through the compositor's own path as real hardware input. The cost, stated
       rather than discovered later: it needs the `input` group, it bypasses the
       compositor's access control entirely, and `ydotool.service` here is
       `enabled` but **inactive** (started by hand), so it is not present on a
       fresh login.
    2. **The RemoteDesktop portal** - the *sanctioned* route, and the one that
       needs no privilege at all, but **measured not to deliver here**. Backend
       version 2, session granted in 0.0 s (no dialog: `persist_mode=2` plus a
       restore token), keysyms sent, focus held, composer clicked - and no
       character arrived. That is the same shape as xdg-desktop-portal-kde's
       empty `NotifyKeyboardKeysym` stub (KDE bug 456025), and it is also what
       the specification warns of: the keysym is resolved against the active
       keymap, and the documented successor is `ConnectToEIS`/libei rather than
       the `Notify*` methods this portal still exposes. Kept as the route for a
       machine where uinput is unavailable, because there it is the only one
       left that needs no privilege.
    3. **XTEST (`xdotool`)** - the only route inside a container's X display,
       where there is no uinput device and no compositor to arbitrate, and the
       right one on an X11 session. Measured to exit 0 having delivered nothing
       into a Chromium/Electron client, which is why it is last and why its
       exit code is never treated as evidence.

    Returned as named callables so a caller can say *which* one delivered, or
    name the ones it tried when none did.
    """
    routes = []

    def via_portal(value):
        return portal_type(app, value)

    def via_uinput(value):
        return subprocess.run(["ydotool", "type", "-d", "80", "-H", "60", "--", value],
                              capture_output=True, text=True, timeout=120,
                              check=False)

    def via_xtest(value):
        return _xdotool(["type", "--clearmodifiers", "--delay", "45", "--", value],
                        timeout=120)

    if portal_available():
        routes.append(("the RemoteDesktop portal", via_portal))
    if shutil.which("ydotool"):
        routes.append(("ydotool (uinput)", via_uinput))
    routes.append(("xdotool (XTEST)", via_xtest))
    return routes


# The `btn` bitmask ydotool wants: `0x40` is "press", `0x80` is "release", and
# the low byte is the button, so `0xC0` is a complete left click. Read out of
# `ydotool(1)` rather than guessed - and `0x00`, which looks like "no button",
# is the *left* button doing nothing at all.
_UINPUT_BUTTONS = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}


def _pointer_routes(app: str):
    """The ways to move the pointer and press a button, best first, named.

    **There is no XTEST route here, and that is the finding rather than an
    omission.** OpenJDK's own bug report on the subject (JDK-8280990) says it
    plainly: *"the emulated input event reaches the X11 clients, but not the
    Wayland compositor which is the actual display server ... these click events
    do not leave the XWayland server and are not reported to the Wayland
    compositor."* So an XTEST click can be dispatched, can exit 0, and can move
    the X pointer - and the application never sees it.

    **This desktop is the documented worst case for it.** `Xwayland :0` here
    runs with **`-enable-ei-portal`** (measured: `/usr/bin/Xwayland :0 -rootless
    -noreset -accessx -core ... -enable-ei-portal`, Xwayland 24.1.10), so XTEST
    events are *additionally* forwarded to the compositor through the Remote
    Desktop portal - which means they inherit the portal's consent gate, and an
    ungranted gate stops them. The field report of that exact configuration
    (mutter 45.2, Fedora) is `xdotool key` opening a **"Remote Display"**
    consent window instead of pressing the key, and - once access is allowed
    once - the popup stopping while the keys still never arrive.

    So XTEST is a route that can report success and deliver nothing, which is
    the worst shape a route can have. Dropping it from the ladder is the fix;
    keeping it "as a last resort" would preserve exactly the bug.

    Both routes left deliver **real input to the compositor** rather than
    pretending to:
    1. **the RemoteDesktop portal** - sanctioned, no privilege, and it is the
       route that already made typing work here.
    2. **ydotool (uinput)** - the kernel's own input path, so the compositor
       treats the click as hardware. It needs membership of the `input` group,
       which a stock desktop user does not have, so it is second and not first.
    """
    routes = []

    def via_portal(point):
        from shani_chronoa import portal as _portal
        with _portal.RemoteInput() as remote:
            remote.move(float(point[0]), float(point[1]))
            remote.click(point[2] if len(point) > 2 else "left")
        return None

    def via_uinput(point):
        button = _UINPUT_BUTTONS.get(
            point[2] if len(point) > 2 else "left", "0xC0")
        subprocess.run(["ydotool", "mousemove", "-a",
                        str(int(round(point[0]))), str(int(round(point[1])))],
                       capture_output=True, text=True, timeout=30, check=False)
        time.sleep(0.08)
        return subprocess.run(["ydotool", "click", button],
                              capture_output=True, text=True, timeout=30,
                              check=False)

    if portal_available():
        routes.append(("the RemoteDesktop portal", via_portal))
    if shutil.which("ydotool"):
        routes.append(("ydotool (uinput)", via_uinput))
    if _compositor_is_wayland():
        # Deliberately absent, and the reason is the whole of this function's
        # second half: on a Wayland session an XTEST press is dispatched to the
        # X server and never reaches the application.
        pass
    else:
        # **No compositor to arbitrate.** Inside a container's private X display
        # (`container ... display=x11`) there is no Wayland session, no uinput
        # device and nothing between the X server and the application - so XTEST
        # is not merely tolerated there, it is the *only* thing that can work.
        # Refusing it everywhere would break the case that works, which is the
        # over-fix this repository records more than once.
        def via_xtest(point):
            _xdotool(["mousemove", "--sync", str(int(round(point[0]))),
                      str(int(round(point[1])))])
            return _xdotool(["click", str(_BUTTONS.get(point[2], 1))])
        routes.append(("xdotool (XTEST, no compositor)", via_xtest))
    return routes


def _compositor_is_wayland() -> bool:
    """Whether a Wayland compositor is arbitrating this session.

    `WAYLAND_DISPLAY` alone, with no cleverness, because **it is also the answer
    inside a container**: `container ... display=x11` hands the inner process a
    `DISPLAY` and no `WAYLAND_DISPLAY`, which is precisely the situation in
    which the X server *is* the display server and XTEST genuinely delivers.
    Reading `XDG_SESSION_TYPE` instead would refuse XTEST there too - the
    over-fix this repository keeps recording, where making the rule absolute
    breaks the one case that worked.
    """
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _scroll_notch(app: str, x: float, y: float, notches: int) -> str:
    """One wheel gesture, through the same routes as a click.

    `notches` is X's own button number (4 up, 5 down, 6 left, 7 right), because
    `scroll()` has always expressed a direction that way and the mapping to
    `NotifyPointerScroll`'s signed `dy` is the only translation needed.
    """
    vertical = {4: -1.0, 5: 1.0}
    horizontal = {6: -1.0, 7: 1.0}
    routes = _pointer_routes(app)
    if not routes:
        raise GuiUnavailable("no pointer route is available to scroll with")
    last = None
    for name, _route in routes:
        try:
            if name.startswith("the RemoteDesktop"):
                from shani_chronoa import portal as _portal
                with _portal.RemoteInput() as remote:
                    remote.move(float(x), float(y))
                    remote.scroll(horizontal.get(notches, 0.0),
                                  vertical.get(notches, 0.0))
            else:
                # ydotool names a wheel notch as a button in its own range:
                # 8 = up, 9 = down, 10 = left, 11 = right (evdev REL_WHEEL
                # extended bitmask), which is NOT X's 4/5/6/7.
                wheel_bit = {4: 0x08, 5: 0x09, 6: 0x0A, 7: 0x0B}.get(notches)
                if wheel_bit is None:
                    raise GuiUnavailable(
                        f"{notches} is not a wheel notch; use 4/5/6/7")
                subprocess.run(["ydotool", "click", hex(0xC0 | wheel_bit)],
                               capture_output=True, text=True, timeout=30,
                               check=False)
            return name
        except Exception as error:  # noqa: BLE001 - fall through to the next route
            last = error
    raise GuiUnavailable(f"no route could scroll at ({int(x)},{int(y)}): {last}")


def is_on_top(app: str, window_id: "str | None" = None, title: "str | None" = None,
              probe: str = None) -> "bool | None":
    """Whether the window about to be clicked is **actually the thing on top**.

    This is the question every other answer turned out to be standing in for,
    and it is asked here with **no X tools at all**.

    **Why it has to be asked.** On this desktop `flatpak run com.slack.Slack` is
    the only working way to raise a window, and it is fragile for a reason that
    is easy to miss: the moment anything runs from a terminal, *the terminal
    raises itself*, and it covers Slack's left column. Measured by OCR of the
    region a click was about to use - it returned `deepu`, `Jisha`, `Nevin`,
    `Abdul` and the click did nothing, because those words were on screen in
    **this agent's own terminal output**, not in Slack. Every click at the right
    pixel for a whole afternoon was a click on the terminal.

    **How it is answered.** Read the screen and look for something only the
    window would show. `None` means *could not tell* - no capture, no OCR, no
    `probe` - and it is deliberately not `True`: a check that answers "yes"
    when it could not look has no power to refuse anything.

    `probe` is text that appears in the window; without one the window's title
    is used, which is weaker - a terminal shows titles too, which is the whole
    point of asking.
    """
    if probe is None:
        probe = title or ""
    if not probe.strip():
        return None
    words = screen_words()
    if not words:
        return None
    needle = "".join(ch for ch in probe.lower() if ch.isalnum())
    if not needle:
        return None
    return any(needle in "".join(ch for ch in t.lower() if ch.isalnum())
               for _, _, _, _, t in words)


def _press_pointer(app: str, x: float, y: float, button: str = "left") -> str:
    """Move the pointer to (x, y) and press `button`, naming the route used.

    Raises when no route exists, rather than silently doing nothing: a click
    that could not be sent and a click that was delivered are different, and
    only one of them may be reported as a click.
    """
    routes = _pointer_routes(app)
    if not routes:
        raise GuiUnavailable(
            "no way to move the pointer on this desktop: the RemoteDesktop "
            "portal is not offered and ydotool is not installed. XTEST is "
            "deliberately not used - on Wayland it dispatches to the X server "
            "without the application ever seeing it.")
    last = None
    for name, route in routes:
        try:
            route((x, y, button))
            return name
        except Exception as error:  # noqa: BLE001 - fall through to the next route
            last = error
    raise GuiUnavailable(
        f"neither route could press {button} at ({int(x)},{int(y)}): {last}")


def portal_available() -> bool:
    """Whether the desktop offers the RemoteDesktop portal this route needs."""
    try:
        from shani_chronoa import portal as _portal
        return _portal.PortalClient().version(_portal.RD_IFACE) > 0
    except Exception:  # noqa: BLE001 - no portal is a route that does not exist
        return False


def _type_keys(text: str):
    """Type `text` by the best route available, without checking it arrived.

    Kept for callers that only need "the keys were sent"; `type_text` does not
    use it, because it must *verify* and therefore needs the routes individually.
    """
    routes = _typing_routes("")
    if routes:
        try:
            return routes[0][1](text)
        except Exception:  # noqa: BLE001 - fall through to the next route
            pass
    return _xdotool(["type", "--clearmodifiers", "--delay", "45", "--", text],
                    timeout=120)


def type_text(app: str, title: str, text: str,
              window_id: "str | None" = None) -> str:
    """Type into a container's GUI app, with the cursor shown where it lands."""
    cue, _reason = start_cue(app)
    try:
        # Checked first, and without needing an X window: on a Wayland desktop
        # the app is a native client, so the route below is the portal and a
        # guard placed after the window lookup would never run on the very apps
        # it exists for. A keystroke here leaves the machine, so it is refused
        # before anything is sent rather than half-typed.
        surface = _comms_app(app)
        # **The refusal is settled before the window is looked for.**
        #
        # `_comms_app()` reads the app's own frame title from the accessibility
        # tree, so it can answer "is this a chat surface?" **without a window** -
        # and that answer does not change while `find_window` spends its
        # `_WINDOW_TIMEOUT` of 30 seconds waiting for one that may never appear.
        #
        # Measured, and this is not a test-only cost: two tests took **exactly
        # 30 seconds each** because the window search ran first and the refusal
        # second. A consent gate that takes 30 seconds to say no is a gate that
        # looks like a hang, and the gate exists precisely to stop a keystroke
        # leaving the machine - so it should be the fastest thing in the function,
        # not the slowest.
        if surface and not _comms_override():
            raise GuiUnavailable(
                f"refusing to type into {app}: {surface}. A keystroke here "
                f"leaves the machine, so a driver aiming at a text field must "
                f"never be able to send a message in a chat or mail window.")
        if window_id is None:
            window_id = find_window(title, app=app)
        if window_id is None:
            # A native Wayland client: no X window, so the portal is the route.
            typed = portal_type(app, text)
            if surface:
                typed += (f" ({surface}, which '{_COMMS_CONSENT_KEY}' allows - "
                          f"a keystroke here leaves the machine).")
            return typed
        # A second refusal, now that a window is known. Typing is the one action
        # here whose effect leaves the app it was aimed at: `xdotool type` goes
        # to whatever holds focus, so a focus that was never confirmed is a
        # keystroke in an unknown window, and a communication surface would send
        # a person's words somewhere they did not ask them sent. Both are
        # checked *before* the text, because a partial type cannot be taken back.
        if not surface:
            surface = _comms_surface(window_id)
        if surface and not _comms_override():
            raise GuiUnavailable(
                f"refusing to type into {app}: {surface}. A keystroke here "
                f"leaves the machine, so a driver aiming at a text field must "
                f"never be able to send a message in a chat or mail window.")
        problem = _focus_the_window(window_id, title)
        if problem:
            raise GuiUnavailable(
                f"not typing into {app}: {problem}. The text would land in "
                f"whatever is focused, which is not necessarily this app.")
        box = window_box(window_id)
        if box:
            # **The lower part of the window, not the middle - measured twice.**
            # The old 40%/40% click landed in Slack's *message list*, so the
            # composer never took focus and every route failed on a correctly
            # focused window with no caret. 94% then overshot the other way:
            # Slack's composer sits at **91.7%** of the window's height (measured
            # from the window box and the placeholder's position), so the click
            # fell just below the field. This is a heuristic and is named as one -
            # a window that publishes an editable node should have it focused
            # through AT-SPI instead, and one that does not (Electron publishes
            # none) leaves this as the only available answer.
            _glide_and_click(cue_socket(app), box[0] + box[2] * 0.5,
                             box[1] + box[3] * 0.92, box)
        # **Each route is tried and then *read back*, and the first one that is
        # confirmed wins.** rc=0 is not evidence - measured: `xdotool type` into
        # this desktop's Slack exits 0 with X-level focus confirmed on the right
        # window and **nothing arrives**, while `Ctrl+K` through the same XTEST
        # path opens Slack's jump-to dialog. An earlier version of this reply
        # said "Typed 12 character(s)" on exactly that evidence and was wrong.
        #
        # Trying them in order and verifying each is what turns three guesses
        # into one measurement: whichever route actually delivers is the one
        # named, and a machine where none does says so instead of claiming the
        # last one tried.
        seen = ""
        tried = []
        delivered_by = None
        unreadable = False
        for name, send in _typing_routes(app):
            try:
                send(text)
            except Exception as exc:  # noqa: BLE001 - a route that cannot run
                tried.append(f"{name} ({type(exc).__name__})")
                continue
            tried.append(name)
            # A reader that cannot read is not a route that failed, and conflating
            # the two is what made a working portal look broken: the text arrived,
            # the read-back raised, and every route was blamed in turn. So a failed
            # read is recorded separately and the routes still get their turn.
            try:
                seen = _read_back(app, text)
            except Exception:  # noqa: BLE001 - unreadable is not undelivered
                unreadable = True
                continue
            if _matches(text, seen):
                delivered_by = name
                break
        if delivered_by:
            return (f"Typed {len(text)} character(s) into {app} and read them "
                    f"back, through {delivered_by}.")
        if unreadable and len(tried) == 1:
            # every route ran and the only thing that failed was the reading
            return (f"Asked {app} to take {len(text)} character(s) through "
                    f"{tried[0]}. The keys were sent, but this window could not "
                    f"be read back to confirm it - look at the app's window.")
        # **rc=0 is not evidence, and this file is where that has to be said.**
        # Nothing was confirmed, so nothing is claimed - what is claimed instead
        # is the shape of the failure, because "it did not work" is not
        # actionable and "these three routes were tried and none was confirmed"
        # says whether to suspect focus, the compositor, or the app.
        return (f"Asked {app} to take {len(text)} character(s); it could not be "
                f"confirmed that any arrived. Tried {', '.join(tried)}. On a "
                f"Wayland window the compositor owns input, so keys reach only "
                f"the window it has focused - look at its window.") + (
            "" if not surface else
            f" ({surface}, which '{_COMMS_CONSENT_KEY}' allows.)")
    finally:
        _stop_cue(cue)


def _read_back(app: str, text: str) -> str:
    """What the app's focused field holds, by whichever reader can see it.

    Two readers, because one is not enough on a real desktop. The accessibility
    tree is exact and cheap but **Slack publishes zero editable nodes**, with or
    without a dialog open - Chromium only builds its a11y tree for an announced
    assistive technology - so a tree-only check can never confirm delivery into
    an Electron app. OCR of the window is the second reader, and it is what
    actually read `test message` back out of the composer on this box.

    It is only consulted when the tree came back without the text, because a tree
    read is exact where OCR is approximate.
    """
    seen = ""
    try:
        seen = _read_focused_text(app)
    except Exception:  # noqa: BLE001 - a reader that cannot read is not a yes
        seen = ""
    if not _matches(text, seen):
        try:
            seen = screen_text(app)
        except Exception:  # noqa: BLE001 - a reader that cannot read is not a yes
            seen = ""
    return seen


def _matches(text: str, seen: str) -> bool:
    """Is `text` in `seen`, allowing OCR to have got a character wrong.

    **Measured, and this is the bug that made a working portal look broken.**
    Sent `zzdirect` into Slack's composer; the window then showed `zadirect`.
    OCR at that font size confuses `z`/`a`, so an exact substring test failed on
    text that had demonstrably arrived - and because the read-back was the only
    evidence, the driver reported "could not be confirmed" about a delivery that
    had happened, and every route was blamed in turn.

    So one differing character is tolerated, and two are not. That bound is not
    a guess: a reader that accepted arbitrary differences would confirm text
    that never arrived, which is the failure this whole mechanism exists to
    avoid - it is the mirror of the rc=0 lie, and worse, because it looks like
    proof of delivery. An empty observation never matches, whatever the text.
    """
    wanted = "".join(text.split())
    if not wanted:
        return False
    haystack = "".join(seen.split())
    if wanted in haystack:
        return True
    # a sliding window of the same length, allowing one character to differ
    for start in range(0, max(1, len(haystack) - len(wanted) + 1)):
        window = haystack[start:start + len(wanted)]
        if len(window) != len(wanted):
            continue
        if sum(1 for a, b in zip(window, wanted) if a != b) <= 1:
            return True
    return False


def key(app: str, title: str, keys: str) -> str:
    """Send a key combination to a container's GUI app, with the window marked."""
    _require_xtest()
    _which("xdotool", "given a key")
    window_id = find_window(title, app=app, timeout=10)
    if window_id is None:
        # No X window for the app: it is a native Wayland client, which XTEST
        # cannot see or drive however healthy the X display is. The portal is
        # then the route that works, so it is tried for real - but a chord can
        # send a message just as a keystroke can, so the surface is checked
        # first and without needing a window id.
        surface = _comms_app(app)
        if surface and not _comms_override():
            raise GuiUnavailable(
                f"refusing to send {keys} to {app}: {surface}. A key combination "
                f"here leaves the machine.")
        return portal_key(app, keys)
    box = window_box(window_id)
    # The window is marked *before* the keys go, so the person sees which window
    # is being driven and then sees the keys land in it.
    cue, socket = _cue_announce(app, box)
    if cue is not None and box:
        ao.cue(socket, {"ripple": [box[0] + box[2] / 2, box[1] + box[3] / 2]})
    _xdotool(["windowactivate", "--sync", window_id])
    proc = _xdotool(["key", keys])
    if proc.returncode != 0:
        raise GuiUnavailable(f"the app would not take {keys!r}")
    return f"Sent {keys} to {app}."


def _read_focused_text(app: str) -> str:
    """What the app's focused field holds, read back through the a11y tree.

    This is what turns "the command exited 0" into "the text is there". An
    Electron or GTK app that publishes no accessible text simply returns "", and
    "" is never treated as a successful delivery.
    """
    _a11y_env()
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
        for application in Atspi.get_desktop(0):
            if application is None or application.get_name() != app:
                continue
            for node in _walk_all(application):
                try:
                    states = node.get_state_set().get_states()
                    if Atspi.StateType.FOCUSED not in states:
                        continue
                    if Atspi.StateType.EDITABLE not in states:
                        continue
                    return (Atspi.Text.get_text(node, 0,
                            Atspi.Text.get_character_count(node)) or "").strip()
                except Exception:  # noqa: BLE001 - a node without Text
                    continue
    except Exception:  # noqa: BLE001 - no bus, no gi, no tree
        return ""
    return ""


def _stop_cue(cue) -> None:
    """Leave the cue layer running: it exits on its own after a quiet spell.

    Killing it here would drop the highlight the instant the click finished, so
    the person would see a flash rather than the window being driven.
    """
    return None


# --- the motion and the typing, shaped like a person's -------------------------


def _ease_in_out_cubic(t: float) -> float:
    """Slow at both ends, fast in the middle - what a hand does."""
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


def _pointer_at() -> "tuple[float, float]":
    """Where the real pointer is now, or (0, 0) when that cannot be read."""
    try:
        proc = _xdotool(["getmouselocation", "--shell"], timeout=10)
        if proc.returncode != 0:
            return (0.0, 0.0)
        values = dict(line.split("=", 1) for line in proc.stdout.splitlines()
                      if "=" in line)
        return (float(values["X"]), float(values["Y"]))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return (0.0, 0.0)


def _move_like_a_hand(x: float, y: float, duration_ms: int = 320) -> bool:
    """Move the real pointer along an eased path with a little overshoot.

    The cue layer already *draws* the glide; this is the real pointer following
    it, and a jump cut between the two is visible - a glide that finishes and
    then teleports is the one thing a person watching would notice. So the real
    motion starts where the pointer *is*, eases in and out, overshoots slightly
    and settles on the target, exactly as `gui/browser_driver.py` does inside a
    page.

    `xdotool mousemove --sync` per step rather than one move, because one move
    is what being looked at is meant to hide.

    **Returns whether the pointer actually ended up on the target.** That is not
    a nicety: the drawn cursor and the real one are two arrows, and they read as
    one cursor only while they are in the same place. Measured before this was
    checked - asked for (213,754), the pointer finished at (202,810), **56px
    away** - and a driver that reports "clicked" while its own two cursors sit
    apart is reporting the drawing rather than the machine.

    So the path ends with an exact move and a **read-back**, and a step that
    cannot be sent stops the eased path (jumping the rest of the way is the jump
    cut this function exists to avoid) without also giving up on landing: the
    final exact move is retried, because arriving is not optional and looking
    unhurried on the way there is.
    """
    here_x, here_y = _pointer_at()
    steps = max(6, round(duration_ms / 24))
    overshoot_x, overshoot_y = 12.0, 8.0
    for step in range(1, steps + 1):
        t = step / steps
        ease = _ease_in_out_cubic(t)
        # the target itself eases in from an overshoot, so the pointer passes
        # the target and comes back to it rather than arriving flat
        settle_x, settle_y = overshoot_x * (1 - t), overshoot_y * (1 - t)
        px = here_x + (x + settle_x - here_x) * ease
        py = here_y + (y + settle_y - here_y) * ease
        if step == steps:
            px, py = x, y
        if not _move_pointer(px, py):
            break
        time.sleep(min(0.05, duration_ms / 1000 / steps))

    # **Land exactly, then prove it.** Two attempts, because a single one can be
    # the step that failed, and the read-back is what turns "probably" into a
    # claim either the caller can trust or knows to distrust.
    want_x, want_y = int(round(x)), int(round(y))
    for _ in range(2):
        if not _move_pointer(want_x, want_y):
            continue
        at_x, at_y = _pointer_at()
        if abs(at_x - want_x) <= 1 and abs(at_y - want_y) <= 1:
            return True
    at_x, at_y = _pointer_at()
    return abs(at_x - want_x) <= 1 and abs(at_y - want_y) <= 1


def _move_pointer(x: float, y: float) -> bool:
    """One `mousemove`, and whether it could be sent at all."""
    try:
        _xdotool(["mousemove", "--sync", str(int(round(x))), str(int(round(y)))],
                 timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _pause_like_a_person(base_ms: int) -> float:
    """A delay between two characters, in a shape a person actually types in.

    A uniform delay is what automation looks like from the outside. Two uniforms
    averaged give a peaked distribution - most gaps near the middle, few at the
    extremes - with an occasional long tail for the pause before a word. The
    shape is the point, not the numbers: this is the same reason `browse` types
    in visible chunks instead of one paste.
    """
    import random

    a = random.randint(base_ms, base_ms + 90)
    b = random.randint(base_ms, base_ms + 90)
    peak = (a + b) / 2
    if random.random() < 0.1:
        peak += random.randint(0, 220)
    return min(peak + 220, 900) / 1000.0


def _focus_via_shell(title: str, window_id: str) -> str:
    """Ask GNOME's Shell to raise the window, by title. "" if it did not apply.

    **This is the only route that works when a native Wayland window is in
    front**, and the reason is structural rather than a Matter of trying harder.

    Measured on this box (GNOME Shell 50.1):
      - `xdotool windowraise` / `windowfocus` cannot lift an XWayland window over
        a native Wayland surface. `_NET_CLIENT_LIST_STACKING` showed Slack's
        client (`0x600004`) frontmost *of the X clients* while `getmouselocation`
        at the very point being clicked reported window `1024` - a Mutter
        internal window, i.e. a native surface was covering it. Two stacking
        domains, and only gnome-shell can move a window between them.
      - `org.gnome.Shell.Application.Activate`, which older GNOME exposed for
        exactly this, **is not present on 50.1** - `Object does not exist at path
        "/org/gnome/Shell/Application"`. `org.gnome.Shell` offers only
        `Introspect`, `Screenshot`, `AudioDeviceSelection`, `Wacom`,
        `ScreenTime`, `Brightness`.
      - `org.gnome.Shell.Eval` answers `(false, '')` - locked outside unsafe mode.

    So the route is this repo's own extension (`chronoa-windows@shani.dev`),
    whose own header says it exists for this reason: *"GNOME locks
    org.gnome.Shell.Eval and has no protocol through which another Wayland client
    may focus, close or move a window, so on GNOME this extension is the
    permission."* It is a fixed list of window verbs and never a way to run code
    in the shell.

    Returns "" when there is nothing to do (no extension, or no matching window),
    so the caller falls through to the X route rather than reporting a failure
    that a second mechanism may well fix.
    """
    try:
        from shani_chronoa.windows import gnome as _gnome
        backend, _why = _gnome.available()
        if backend is None:
            return ""
        target = None
        for window in backend.list():
            if window.title and (window.title == title or _loose(title, window.title)):
                target = window
                break
        if target is None:
            return ""
        backend.focus(target.id)
    except Exception:  # noqa: BLE001 - another route may still work
        return ""
    return ""


def _focus_the_window(window_id: str, title: str = "") -> str:
    """Focus `window_id` and confirm it took. Returns "" when it did.

    A driver that types without checking where the keys land sends them
    wherever focus happens to be - which on a desktop is very often another
    app. `xdotool type` targets the focused window, so the focus is part of the
    action, not a nicety, and an unconfirmed focus is a refusal.

    `windowactivate --sync` is the part that matters: without `--sync` the call
    returns before the window manager has done anything.
    """
    if not window_id:
        return "the window is not known, so focus cannot be checked"
    try:
        # **Already the compositor's focused window? Touch nothing.**
        # Measured, and it is the opposite of the obvious: calling `windowfocus`
        # on a window that is *already* active is not a no-op - it returned
        # success here, `getactivewindow` still named our window, and the click
        # and keystrokes that followed then went nowhere. The app's own internal
        # focus (which of its fields holds the caret) is disturbed by the
        # re-focus and is not restored by the click that comes after. So the
        # check that was already here - "is this window focused?" - is now asked
        # *first*, and the answer "yes" short-circuits.
        already = _xdotool(["getactivewindow"], timeout=10).stdout.strip()
        if already and (already == window_id
                        or _window_or_child(already, window_id)):
            return ""
        # `windowfocus` sets the X input focus *directly*. `windowactivate` is an
        # EWMH request to the compositor, and on GNOME a Wayland client ignores
        # it - measured on this box: `windowactivate --sync` on Slack's XWayland
        # window exits 0 printing "XGetWindowProperty[_NET_WM_DESKTOP] failed"
        # and focus does not move at all. This is the half the anti-surveillance
        # script had and this driver lacked: it calls activate *and* focus.
        #
        # **And the frame comes first, then the client** - measured, and the
        # order is load-bearing rather than defensive. `windowfocus` on the
        # `mutter-x11-frames` window alone left `getactivewindow` on the guard
        # window (4194307) every single time; the same call on the client inside
        # it, issued straight after, moved focus off the guard and the text
        # landed. Mutter will not focus a client whose frame does not hold focus,
        # so the second call without the first silently does nothing.
        _xdotool(["windowfocus", window_id], timeout=15)
        client = _client_window(window_id)
        proc = _xdotool(["windowfocus", client], timeout=15)
        if proc.returncode != 0:
            return "the app's window would not take focus"
        window_id = client
        # The Shell extension first when there is a title to match, because the
        # X route below cannot lift a window over a native Wayland surface and
        # this can. It is the person's decision whether it is enabled, so a
        # refusal here is "" and the X route still gets its turn.
        if title:
            _focus_via_shell(title, window_id)
        # The check is X-level focus, not the compositor's idea of the active
        # window. `getactivewindow` returns Mutter's guard window (measured
        # 4194307) for a Wayland client's XWayland window, so it would refuse
        # every native app on this desktop while `getwindowfocus` reports what X
        # actually holds - and that is what `xdotool type` delivers to.
        focused = _xdotool(["getwindowfocus"], timeout=10).stdout.strip()
        if focused and not _window_or_child(focused, window_id):
            return (f"focus went to window {focused} instead of {window_id}, so "
                    f"the keys would land somewhere else")
        # X focus is necessary but on its own it is not enough, and saying so is
        # the difference between this check and the one that was there before:
        # with X focus confirmed on the right window and **the compositor still
        # showing its guard window**, every keystroke was measured going to
        # whatever the compositor had actually focused - another terminal, in the
        # run that found it. `getactivewindow` naming our own window is the
        # compositor agreeing, and it is the only signal here that does.
        active = _xdotool(["getactivewindow"], timeout=10).stdout.strip()
        if active and active != window_id and not _window_or_child(active, window_id):
            # Named precisely, because "focus failed" sends someone hunting for a
            # bug that is not there. On this desktop a native Wayland window in
            # front cannot be displaced by any X-side call (measured: the X
            # stacking list and the window under the click disagree), so the two
            # real ways forward are a click, or the Shell extension.
            return (f"another window is still in front, so keys sent now would go "
                    f"to it rather than to {title or window_id}. Bring the app to "
                    f"the front yourself, or enable Chronoa's GNOME Shell "
                    f"extension (`gnome-extensions enable "
                    f"chronoa-windows@shani.dev`), which is the only thing on "
                    f"GNOME that can raise a window over another one")
    except (OSError, subprocess.SubprocessError):
        return "focus could not be checked on this display"
    return ""


def _client_window(window_id: str) -> str:
    """The app's own window inside Mutter's decoration frame, or `window_id`.

    Measured on this box, and it is the whole reason typing into Slack failed for
    an afternoon: `xdotool search --name` returns **10485898**, whose
    `WM_CLASS` is `"mutter-x11-frames"` - GNOME's frame *around* the window, not
    Slack. Focusing it raises nothing, `windowactivate` on it fails with
    "XGetWindowProperty[_NET_WM_DESKTOP] failed", and `getactivewindow` keeps
    answering Mutter's guard window (4194307) no matter what is sent to it.

    The real client is its child, **6291460**, carrying `WM_CLASS "slack","Slack"`
    and `_NET_WM_PID` of the app. Focusing *that* moved `getactivewindow` off the
    guard for the first time - and the typing landed immediately after.

    So the frame is a decoration, not a window: it is where a pointer lands, and
    the client underneath is what the compositor will focus and what keys go to.
    A window with no `mutter-x11-frames` class is already its own client and is
    returned unchanged, so a plain X11 or container window is unaffected.
    """
    if not window_id:
        return window_id
    try:
        # **`xprop`, not `xdotool getwindowclassname`** - measured: this xdotool
        # has no such verb ("xdotool: Unknown command: getwindowclassname", rc=1).
        # An unknown verb returns empty output rather than raising, so the first
        # version of this read a permanent empty string, decided every window was
        # its own client, and the whole resolution was dead code that the unit
        # test passed because the fixture supplied the value a real run never
        # could. `xprop -id ... WM_CLASS` is what actually answered.
        cls = subprocess.run(["xprop", "-id", window_id, "WM_CLASS"],
                             capture_output=True, text=True, timeout=10,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return window_id
    if "mutter-x11-frames" not in cls:
        return window_id
    try:
        tree = subprocess.run(["xwininfo", "-id", window_id, "-tree"],
                              capture_output=True, text=True, timeout=15,
                              check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return window_id
    # The direct child is the client; anything deeper is a widget inside it. The
    # indent of `xwininfo -tree` is what separates the two, so it is read rather
    # than guessed at: on this box the client is one level in and the client's own
    # child (0x1200027) is two, and focusing the widget instead would fail the same
    # way the frame did.
    for line in tree.splitlines()[1:]:
        stripped = line.strip()
        if not stripped.startswith("0x"):
            continue
        indent = len(line) - len(line.lstrip())
        child = stripped.split()[0]
        if indent:
            # `xwininfo -tree` prints **hex**, and every `xdotool` call in this
            # driver speaks decimal - so handing `0x600004` straight back would
            # break `getwindowgeometry`, `getwindowname` and the rest with a
            # window-not-found, which reads as "the window has gone" rather than
            # as a number in the wrong base. Caught by the test that pins it.
            try:
                return str(int(child, 16))
            except ValueError:
                return window_id
    return window_id


def _window_or_child(focused: str, window_id: str) -> bool:
    """Is `focused` the window itself, or a widget inside it?

    A toolkit sometimes puts X focus on an input child rather than the top-level
    frame, so strict equality would refuse a window that is genuinely focused.
    The names decide it: a child and its frame publish the same title here.
    """
    if focused == window_id:
        return True
    try:
        a = _xdotool(["getwindowname", focused], timeout=10).stdout.strip()
        b = _xdotool(["getwindowname", window_id], timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(a) and a == b


#: The one key that lifts the communication-surface refusal below. Off by
#: default, and separate from `input-control-enabled`: driving an app's controls
#: and typing into a chat window are different agreements.
_COMMS_CONSENT_KEY = "comms-typing-enabled"

#: Surfaces a driver must never type into, whatever it was asked to type. The
#: words a person sends here leave the machine, so a keystroke aimed by a model
#: at "the app's text field" must never be able to become a message in a chat
#: window or a mail composer. Matched against the window's class and title,
#: whole words only, because "signal" is also an ordinary word in a title.
_COMMS_SURFACES = ("slack", "teams", "discord", "zoom", "webex", "meet", "gmail",
                   "mail", "thunderbird", "evolution", "outlook", "signal",
                   "telegram", "whatsapp", "mattermost", "skype", "messenger")


def _comms_override() -> bool:
    """Whether the person has agreed to typing into a communication surface.

    Its own key, deliberately *not* `input-control-enabled`: agreeing that Chronoa
    may drive this machine's apps is not the same agreement as letting it type
    into a chat window, and one switch cannot express the difference. Fails
    closed - a config that cannot be read is not a permission.
    """
    try:
        from shani_chronoa.config import ChronoaConfig
        return bool(ChronoaConfig().get_bool(_COMMS_CONSENT_KEY, False))
    except Exception:  # noqa: BLE001 - a config that cannot be read is a refusal
        return False


def _comms_app(app: str) -> str:
    """The communication surface `app` is, without needing an X window.

    `_comms_surface` needs a window id, which `xdotool` gives only for an
    XWayland window - and on a Wayland desktop the app being typed into is
    usually *not* one, so it goes through the portal and the check below never
    ran. This reads the app's own frame title from the accessibility tree, which
    every toolkit publishes on either display server, and falls back to the name
    it was asked by.

    Measured the hard way: the guard was written against an X window id, so on
    this desktop it could not fire on the very apps it was for.
    """
    name = (app or "").strip().lower()
    if not name:
        return ""
    for surface in _COMMS_SURFACES:
        if surface in name:
            return f"{app!r} is a communication surface"
    title = _frame_title(app)
    if title:
        low = title.lower()
        for surface in _COMMS_SURFACES:
            if surface in low:
                return f"its window is titled {title!r}"
    return ""


def _comms_surface(window_id: str) -> str:
    """The communications surface this window is, or "" - and never a guess."""
    try:
        cls = _xdotool(["getwindowclassname", window_id], timeout=10)
        if cls.returncode == 0:
            name = cls.stdout.strip().lower()
            if name:
                for surface in _COMMS_SURFACES:
                    if surface in name:
                        return f"{name} is a communication surface"
    except (OSError, subprocess.SubprocessError):
        pass
    # This xdotool has no getwindowclassname (measured on this box), so the
    # title is the second source rather than the only one.
    try:
        proc = _xdotool(["getwindowname", window_id], timeout=10)
        if proc.returncode == 0:
            title = proc.stdout.strip().lower()
            for surface in _COMMS_SURFACES:
                if surface in title:
                    return f"the window is titled {proc.stdout.strip()!r}"
    except (OSError, subprocess.SubprocessError):
        pass
    return ""
