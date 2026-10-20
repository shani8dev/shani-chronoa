#!/usr/bin/env python3
"""Run the real Chronoa app, then photograph and measure its own main window.

Started by `tools/live_window.sh`, which supplies the headless compositor, the
private D-Bus session and the throwaway keyring this needs. Run that, not this.

**Why the app photographs itself.** Every other route was measured to lie:

- `render_ui.py` builds the window in a bare `Gtk.Application`. Above the
  window's own 460px it captured a zoomed-in sidebar search field rather than
  the window, and it never runs the real application's startup.
- Broadway with a headless browser left the render node empty (`content_w 0`)
  and does not fire timers added in `activate` (AGENTS.md records the same).
- The person's own desktop gives a true picture, but the window takes keyboard
  focus: a keystroke meant for another window landed in the composer and was
  saved as a draft that showed up in every later capture.

So the real `ChronoaApplication` runs on a headless gnome-shell, and once the
window has mapped it snapshots itself through its own renderer
(`Gtk.WidgetPaintable` -> `get_renderer().render_texture`).

Environment, all optional:

    SIZE=WxH      window size, applied on `realize` - `window-added` fires inside
                  the window's constructor, which then sets its own 460x640
    SEED=1        add a short conversation (a tool call, two replies)
    SEED=code     a coding turn: a real file edited through the undo ring, so
                  the edit card's inline diff and the Diff panel have a change
    SEED=ask      a pending approval for an edit, with its preview - the
                  question row as the person sees it before saying yes
    SHOT=path     PNG to write
    SHOT_AFTER=ms when to take it (default 4000, after the narrow-layout idle)
    TREE=a,b      Python expressions over `win` whose widget trees are printed
                  with x / content width, e.g. `win._mode_strip`
    PAGE=id       open this panel (`gui.surfaces.SURFACE_IDS`) through the app's
                  own `show_page`, after seeding
    QUIT=0        leave the app running after the shot

Lines on stdout: `PROBE ...` (split view state), `TREE ...`, `SHOT path w h`.
"""
import os
import sys

repo = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(repo, "usr", "lib", "shani-chronoa"))

from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from shani_chronoa.app import application as appmod  # noqa: E402


def _tree(win, widget, depth=0, out=None):
    """One line per widget: type, CSS classes, x in window space, width.

    **`get_width()` is the content box** - padding, border and margin are not
    in it. Two chips 52px apart reporting `w=36` are touching, not 16px apart;
    that misreading cost a round of CSS changes that moved nothing.
    """
    out = [] if out is None else out
    ok, rect = widget.compute_bounds(win)
    x = int(rect.get_x()) if ok else "?"
    out.append("  " * depth + f"{type(widget).__name__} {widget.get_css_classes()} "
               f"x={x} w={widget.get_width()} h={widget.get_height()} "
               f"vis={widget.get_visible()}")
    child = widget.get_first_child()
    while child is not None and depth < 4:
        _tree(win, child, depth + 1, out)
        child = child.get_next_sibling()
    return out


def _probe(win, tag):
    split = win._split
    print("PROBE", tag, "win", win.get_width(), "x", win.get_height(),
          "split.collapsed", split.get_collapsed(),
          "show_content", split.get_show_content(),
          "content_w", win._chat_page.get_width(), flush=True)
    for expr in filter(None, os.environ.get("TREE", "").split(",")):
        print("TREE", expr, flush=True)
        for line in _tree(win, eval(expr, {"win": win})):  # noqa: S307 - our own probe
            print("   ", line, flush=True)
    return False


def _seed(win):
    win.add_user_turn("how much disk space is left?")
    win.add_tool_call("disk_usage", {"path": "/"}, "Root: 38G free of 120G (32% used)")
    win.set_response("Root has **38G** free of 120G.\n- `/` is 32% full\n"
                     "- `/home` has 210G free")
    win.add_user_turn("and what's the weather like in Pune tomorrow? keep it short")
    win.set_response("Tomorrow in Pune: **29°C**, partly cloudy, 20% chance of "
                     "rain in the evening.")


def _seed_code(win):
    """One real edit, recorded the way `edit_file` records it."""
    import pathlib
    from shani_chronoa.skills.undo_last_change import record_preimage
    work = pathlib.Path(os.environ.get("XDG_DATA_HOME", "/tmp")) / "project"
    work.mkdir(parents=True, exist_ok=True)
    target = work / "greet.py"
    before = ('def greet(name):\n    print("hello " + name)\n\n\n'
              'if __name__ == "__main__":\n    greet("world")\n')
    after = ('def greet(name: str, excited: bool = False) -> str:\n'
             '    """Say hello."""\n    line = f"Hello, {name}"\n'
             '    return line + ("!" if excited else ".")\n\n\n'
             'if __name__ == "__main__":\n    print(greet("world", excited=True))\n')
    target.write_text(before)
    record_preimage(target, before.encode())
    target.write_text(after)
    win.add_user_turn("add type hints to greet() and make it return the string")
    win.add_tool_call("edit_file", {"path": str(target)}, f"Edited {target.name}")
    win.set_response("Done - `greet()` now takes `excited`, has type hints and "
                     "returns the line instead of printing it.")


def _seed_ask(win):
    """An edit waiting for approval, through the real preview and question."""
    import pathlib
    import threading
    from shani_chronoa import ask_bridge, permissions, tools
    work = pathlib.Path(os.environ.get("XDG_DATA_HOME", "/tmp")) / "project"
    work.mkdir(parents=True, exist_ok=True)
    target = work / "greet.py"
    target.write_text('def greet(name):\n    print("hello " + name)\n')
    args = {"path": str(target), "old_string": 'print("hello " + name)',
            "new_string": 'return f"Hello, {name}!"'}
    win.add_user_turn("make greet() return the string instead of printing it")
    preview = tools.approval_preview("edit_file", args)
    request = permissions.approval_request("edit_file", str(target),
                                           "file-edit-enabled", "edit a file")
    threading.Thread(target=lambda: ask_bridge.ask(
        request.question, request.options_with_cancel(), timeout=30,
        preview=preview), daemon=True).start()


def _shoot(win):
    width, height = win.get_width(), win.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    out = os.environ.get("SHOT", "")
    if node is None:
        print("NO NODE - the window drew nothing yet; raise SHOT_AFTER", flush=True)
    elif out:
        texture = win.get_renderer().render_texture(
            node, Graphene.Rect().init(0, 0, width, height))
        texture.save_to_png(out)
        print("SHOT", out, width, height, flush=True)
    _probe(win, "shot")
    if os.environ.get("QUIT", "1") == "1":
        win.get_application().quit()
    return False


def _on_map(win):
    if os.environ.get("SEED") == "1":
        _seed(win)
    elif os.environ.get("SEED") == "code":
        _seed_code(win)
    elif os.environ.get("SEED") == "ask":
        _seed_ask(win)
    elif os.environ.get("SEED") == "palette":
        # **The palette, open.** It is the one surface that is built but
        # never *seen* in a capture, because nothing opens it - a person
        # presses Ctrl+K, and a script cannot. Measured on the real app: the
        # palette had five groups and no skills, and no picture of it had
        # ever been taken, which is how a control that cannot reach 260
        # capabilities went unnoticed.
        win._palette.set_visible(True)
        win._palette.focus_search()
        # **And a search string**, so the group can be looked at rather
        # than counted.
        needle = os.environ.get("PALETTE_SEARCH", "")
        if needle:
            win._palette._search.set_text(needle)
    page = os.environ.get("PAGE", "")
    if page:
        print("PAGE", page, win.show_page(page), flush=True)
    GLib.timeout_add(int(os.environ.get("SHOT_AFTER", "4000")), _shoot, win)
    return False


def _on_window(_app, win):
    if not hasattr(win, "add_user_turn"):
        return
    size = os.environ.get("SIZE", "")
    if size:
        width, height = (int(v) for v in size.lower().split("x"))
        win.connect("realize", lambda w: w.set_default_size(width, height))
    # The state at the moment of mapping, before any idle has run: what the
    # first frame shows. A window that maps uncollapsed at a narrow width
    # flashes a clipped layout on every start.
    win.connect("map", lambda w: _probe(w, "map"))
    win.connect("map", lambda w: GLib.timeout_add(1000, _on_map, w))


if os.environ.get("MCP"):
    # Injected into this process only: there is no settings schema here (see
    # live_window.sh), and the panel and discovery both read `servers()`.
    from shani_chronoa import mcp_client
    _servers = mcp_client.servers
    mcp_client.servers = lambda raw=None: _servers(os.environ["MCP"] if raw is None else raw)

_init = appmod.ChronoaApplication.__init__


def _patched_init(self, *args, **kwargs):
    _init(self, *args, **kwargs)
    self.connect("window-added", _on_window)


appmod.ChronoaApplication.__init__ = _patched_init
sys.argv = [sys.argv[0]]
appmod.main()
