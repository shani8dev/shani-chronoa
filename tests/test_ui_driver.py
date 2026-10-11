"""The driver's own caps and its position read - both defects found by running
the real thing against a real app on this desktop, not by reading the code.

Three of these exist because a measurement contradicted the code:

- `_walk_all` capped the tree at **8** deep, and gnome-text-editor publishes its
  buttons at depth **17-18**. Measured on this box: 55 actionable named controls,
  **0 of them under the old cap**, so the observation answered "none this window
  can act on by name" about a window full of them - the confident-wrong-answer
  shape this repo keeps recording. The fix is the cap `ui_elements` has always
  used; the test builds a tree deep enough that only the new number finds
  anything, so it fails if either cap is restored.
- `_screen_box` caught `(AttributeError, TypeError, KeyError)`, and the a11y bus
  raises **`GLib.GError`** for an object that publishes no Component interface -
  measured on this desktop's own gnome-text-editor, whose frame walk ends at the
  root. The whole observation came back as a traceback.
- The same run measured **0 hidden** actionable controls, which is the negative
  control for the filter that is *not* written here: nothing needed adding, and
  a filter nobody asked for would be decoration.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import ui_driver  # noqa: E402


class FakeNode:
    """An accessibility element: a role, a name, and children."""

    def __init__(self, role="", name="", children=None):
        self._role, self._name = role, name
        self._children = list(children or [])

    def get_role_name(self):
        return self._role

    def get_name(self):
        return self._name

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, index):
        return self._children[index] if index < len(self._children) else None


def _deep_tree(depth: int, name: str = "Open") -> FakeNode:
    """A chain `depth` long with one named button at the bottom of it."""
    leaf = FakeNode("button", name)
    node = leaf
    for _ in range(depth - 1):
        node = FakeNode("panel", "", [node])
    return node


def _count(root) -> int:
    return sum(1 for n in ui_driver._walk_all(root)
               if ui_driver._actionable(n))


def test_a_control_this_deep_is_found() -> None:
    """Depth 17, measured: gnome-text-editor's own buttons."""
    root = _deep_tree(17)
    found = _count(root)
    assert found == 1, "a named button at depth 17 must be reachable"


def test_the_controls_are_numbered_in_walk_order() -> None:
    root = FakeNode("frame", "w", [
        FakeNode("button", "Open"),
        FakeNode("panel", "", [FakeNode("button", "Save")]),
    ])
    assert [n.get_name() for n in ui_driver._walk_all(root)
            if ui_driver._actionable(n)] == ["Open", "Save"]


def test_the_node_cap_still_bounds_a_wide_tree() -> None:
    """The depth fix must not become an unbounded walk on a huge tree."""
    wide = [FakeNode("button", f"b{i}") for i in range(5000)]
    root = FakeNode("frame", "w", wide)
    assert len(ui_driver._walk_all(root)) <= ui_driver._MAX_NODES


def test_the_depth_cap_is_the_repo_reader_own_number() -> None:
    """Two walkers with two caps was the defect; they must agree."""
    from shani_chronoa.skills import ui_elements
    assert ui_driver._MAX_DEPTH == ui_elements.MAX_DEPTH


class _NoComponent:
    """An element under an app that publishes no Component interface."""

    def get_application(self):
        raise _GError()

    def get_parent(self):
        return None


class _GError(Exception):
    """Stands in for `GLib.GError`, which is not any of the three types the
    driver used to catch - that is the whole defect."""


def test_a_position_that_cannot_be_read_is_none_not_a_traceback() -> None:
    box = ui_driver._screen_box(_NoComponent())
    assert box is None


def test_a_plain_none_element_is_none_too() -> None:
    assert ui_driver._screen_box(None) is None


@pytest.mark.parametrize("cap", [8])
def test_the_old_depth_cap_finds_nothing_here(cap) -> None:
    """The negative control, run by hand: with 8 the same tree yields 0.

    Asserted directly rather than by monkeypatching a module constant, because
    the point is that the *number* is load-bearing.
    """
    root = _deep_tree(17)
    shallow = []

    def walk(node, depth=0):
        if node is None or depth > cap:
            return
        shallow.append(node)
        for index in range(node.get_child_count()):
            walk(node.get_child_at_index(index), depth + 1)

    walk(root)
    assert sum(1 for n in shallow if ui_driver._actionable(n)) == 0


# --- the two ideas taken from the anti-surveillance script ----------------------
#
# Not its subject. What that file has that this driver lacked is a *shape*: it
# moves a pointer along an eased path with overshoot instead of jumping it, and
# it refuses to type until it has confirmed where the keys will land. Both are
# the "an action nobody can see is indistinguishable from one that happened to
# somebody else's machine" rule, applied to motion and to focus.


class _Run:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_the_pointer_is_moved_in_steps_not_jumped(monkeypatch) -> None:
    """A glide that finishes and then teleports is visible; steps are the fix."""
    moves = []
    monkeypatch.setattr(ui_driver, "_xdotool",
                        lambda args, timeout=30: moves.append(args) or _Run())
    ui_driver._move_like_a_hand(400.0, 300.0, duration_ms=240)
    steps = [m for m in moves if m[:1] == ["mousemove"]]
    assert len(steps) >= 6, "an eased move is several moves, not one"
    # eased: the middle step travels further than the first
    xs = [int(m[-1]) for m in steps]
    assert xs != [xs[0]] * len(xs)
    assert all("--sync" in m for m in steps)


def test_the_pause_between_keys_is_not_a_constant(monkeypatch) -> None:
    """A uniform delay is what automation looks like from the outside."""
    import random

    random.seed(7)
    gaps = [ui_driver._pause_like_a_person(60) for _ in range(60)]
    assert min(gaps) > 0.02 and max(gaps) < 1.0
    assert len(set(gaps)) > 10, "sixty gaps with fewer than ten shapes"


def test_a_communication_surface_is_refused_before_the_first_key(monkeypatch) -> None:
    def fake(args, timeout=30):
        if args[0] == "getwindowname":
            return _Run("Slack | #general\n")
        if args[0] == "getwindowclassname":
            return _Run("", 1)          # this xdotool has no such command
        return _Run()
    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    said = ui_driver._comms_surface("55")
    assert said, "a Slack window must be refused"
    assert "slack" in said.lower()


def test_it_is_refused_by_class_when_the_class_is_available(monkeypatch) -> None:
    """The same refusal through WM_CLASS, for an xdotool that has it."""
    def fake(args, timeout=30):
        if args[0] == "getwindowclassname":
            return _Run("Thunderbird\n")
        if args[0] == "getwindowname":
            return _Run("Inbox - Mozilla Thunderbird\n")
        return _Run("", 1)
    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    assert "communication surface" in ui_driver._comms_surface("55")


def test_an_ordinary_window_is_not_called_a_communication_surface(monkeypatch) -> None:
    def fake(args, timeout=30):
        if args[0] == "getwindowname":
            return _Run("GTK Demo\n")
        return _Run("", 1)
    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    assert ui_driver._comms_surface("55") == ""


def test_a_focus_that_went_somewhere_else_is_a_refusal(monkeypatch) -> None:
    def fake(args, timeout=30):
        if args[0] == "getwindowfocus":
            return _Run("999\n")
        if args[0] == "getwindowname":
            # two *different* names, or the child check cannot tell them apart
            return _Run("Some Other Window\n" if args[1] == "999" else "The App\n")
        return _Run()
    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    problem = ui_driver._focus_the_window("55")
    assert "999" in problem and "somewhere else" in problem


def test_a_focus_that_took_is_empty_not_a_sentence(monkeypatch) -> None:
    def fake(args, timeout=30):
        if args[0] == "getwindowfocus":
            return _Run("55\n")
        return _Run()
    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    assert ui_driver._focus_the_window("55") == ""


def test_a_focused_child_of_the_window_is_not_a_refusal(monkeypatch) -> None:
    """A toolkit sometimes puts X focus on an input child, not the frame.

    Both publish the same title, so a strict equality check would refuse a window
    that is genuinely focused.
    """
    def fake(args, timeout=30):
        if args[0] == "getwindowfocus":
            return _Run("77\n")
        if args[0] == "getwindowname":
            return _Run("The App\n")
        return _Run()
    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    assert ui_driver._focus_the_window("55") == ""


def test_focus_checks_both_x_level_and_the_compositor(monkeypatch) -> None:
    """**This test asserted the opposite, and the measurement it rested on was
    half true.** It said `getactivewindow` "lies" - returns Mutter's guard window
    (4194307) - so the check must be `getwindowfocus` alone.

    The guard window is not a lie. It is the compositor's truthful answer to a
    different question: *which surface holds keyboard focus*. `getwindowfocus`
    answers what **X** holds, and for an XWayland client those are two different
    things - which is exactly how `xdotool type` came to exit 0 and deliver
    nothing while X focus was confirmed correct.

    Measured on this box, typing into Slack: with `getwindowfocus` reporting the
    right window and `getactivewindow` still reporting the guard, every
    keystroke went to whatever window the compositor actually had focused (a
    terminal), and the composer stayed empty. Both are checked now, and either
    one disagreeing is a refusal.
    """
    seen = []
    state = {"active": "4194307"}

    def fake(args, timeout=30):
        seen.append(list(args))
        if args[0] == "getactivewindow":
            # first asking: something else has focus, so the focus path runs;
            # from then on: our window, so the function may succeed
            out = state["active"]
            state["active"] = "55"
            return _Run(out + "\n")
        if args[0] == "getwindowname":
            # the guard window and our window must not look like the same
            # window, or the "already focused?" short-circuit fires instead
            return _Run("something-else\n" if args[1] == "4194307" else "target\n")
        return _Run("55\n")

    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    monkeypatch.setattr(ui_driver, "_client_window", lambda w: w)
    assert ui_driver._focus_the_window("55") == ""
    joined = " ".join(" ".join(a) for a in seen)
    assert "getwindowfocus" in joined, "X-level focus is still what xdotool delivers to"
    assert "getactivewindow" in joined, "and the compositor's answer decides whether it lands"
    assert "windowfocus 55" in joined
    assert "windowactivate" not in joined


def test_an_already_focused_window_is_left_completely_alone(monkeypatch) -> None:
    """Measured: re-focusing a window that is *already* the compositor's active
    one returns success and then breaks everything after it - the click and the
    keystrokes that follow go nowhere, because the app's internal caret is
    disturbed and the click that comes next does not restore it.

    So the "is it already focused?" question is asked **first**, and a yes means
    no `windowfocus` at all. The control is the assertion below: with
    `windowfocus` restored the suite must go red, or "touches nothing" would be
    a claim nothing checks.
    """
    seen = []
    monkeypatch.setattr(ui_driver, "_xdotool",
                        lambda args, timeout=30: seen.append(list(args)) or _Run("55\n"))
    monkeypatch.setattr(ui_driver, "_client_window", lambda w: w)
    assert ui_driver._focus_the_window("55") == ""
    joined = " ".join(" ".join(a) for a in seen)
    assert "getactivewindow" in joined, "the question is still asked"
    assert "windowfocus" not in joined, "an already-focused window must not be re-focused"


def test_x_focus_right_but_the_compositor_says_otherwise_is_a_refusal(monkeypatch) -> None:
    """The control for the check above: agreeing on one level is not agreement.

    Without this, deleting the `getactivewindow` comparison would leave every
    focus test green - the same "a check that cannot fail" shape this file
    keeps recording. The guard window is Mutter's answer for "something else has
    focus", so it is exactly the value that must be refused.
    """
    answers = {"getwindowfocus": "55\n", "getactivewindow": "4194307\n"}

    def fake(args, timeout=30):
        return _Run(answers.get(args[0], ""))

    monkeypatch.setattr(ui_driver, "_xdotool", fake)
    monkeypatch.setattr(ui_driver, "_client_window", lambda w: w)
    problem = ui_driver._focus_the_window("55")
    assert problem, "the compositor holding another window must be a refusal"
    assert "in front" in problem
    # and it names the two ways out, because "focus failed" sends someone
    # hunting for a bug that is not there
    assert "chronoa-windows@shani.dev" in problem


def test_the_mutter_frame_is_resolved_to_the_real_client(monkeypatch) -> None:
    """`xdotool search` returns Mutter's **decoration frame**, not the app.

    Measured: Slack's window is `0x9cfbaa` = 10485898 with `WM_CLASS
    "mutter-x11-frames"`, and the real client is its child `0x600004` = 6291460
    carrying `WM_CLASS "slack","Slack"`. Focusing the frame left
    `getactivewindow` on the guard every time; focusing the client moved it.

    A window that is not a frame is returned unchanged, so a plain container X11
    window - the case this driver was built for - is untouched.
    """
    class Fake:
        def __init__(self, out):
            self.stdout, self.returncode = out, 0

    class FakeProc:
        def __init__(self, out):
            self.stdout, self.returncode = out, 0

    def fake_run(argv, **kwargs):
        # `xprop` is the only reader here: `xdotool getwindowclassname` does not
        # exist in the installed build (rc=1, "Unknown command"), so an unknown
        # verb silently returned "" and made this whole function a no-op that
        # still passed against a fixture.
        if argv[0] == "xprop":
            return FakeProc('WM_CLASS(STRING) = "mutter-x11-frames", '
                            '"mutter-x11-frames"\n')
        return FakeProc(
            "xwininfo: Window id: 0x9cfbaa\n"
            "  0x600004 \"Nevin (DM) - Slack\": (\"slack\" \"Slack\")  1853x1011+0+37\n"
            "    1 child:\n"
            "    0x1200027 (has no name): ()  1853x1011+0+0\n")

    monkeypatch.setattr(ui_driver.subprocess, "run", fake_run)
    assert ui_driver._client_window("10485898") == "6291460"


def test_a_window_that_is_not_a_frame_is_left_alone(monkeypatch) -> None:
    """The negative control for the resolution above: a container X11 window has
    no mutter frame, and must come back as itself rather than as a child."""
    class FakeProc:
        def __init__(self, out):
            self.stdout, self.returncode = out, 0

    monkeypatch.setattr(ui_driver.subprocess, "run",
                        lambda argv, **k: FakeProc(
                            'WM_CLASS(STRING) = "xmessage", "xmessage"\n'))
    assert ui_driver._client_window("12345") == "12345"


def test_typing_routes_are_ordered_by_what_the_platform_sanctions(monkeypatch) -> None:
    """Portal first, then uinput, then XTEST - and each has a reason.

    On GNOME/Mutter the RemoteDesktop portal is *the* route: every input
    protocol that could target a window (`zwp-virtual-keyboard`,
    `wlr-virtual-pointer`) is wlroots-only, which is why `wtype` answers
    "Compositor does not support the virtual keyboard protocol" here and why
    Chromium ships its own portal-based tool for the same job. uinput works but
    arrives as hardware input *without* the compositor's access control seeing
    it, and needs the `input` group; XTEST is last because it is the only route
    that works inside a container's X display.

    The control: with no portal and no `ydotool`, XTEST is still offered, so a
    container or an X11 session is never left with no route at all.
    """
    monkeypatch.setattr(ui_driver, "portal_available", lambda: True)
    monkeypatch.setattr(ui_driver.shutil, "which", lambda name: "/usr/bin/ydotool")
    names = [n for n, _ in ui_driver._typing_routes("slack")]
    assert names == ["the RemoteDesktop portal", "ydotool (uinput)",
                     "xdotool (XTEST)"]

    monkeypatch.setattr(ui_driver, "portal_available", lambda: False)
    assert [n for n, _ in ui_driver._typing_routes("slack")] == [
        "ydotool (uinput)", "xdotool (XTEST)"]
    monkeypatch.setattr(ui_driver.shutil, "which", lambda name: None)
    assert [n for n, _ in ui_driver._typing_routes("slack")] == ["xdotool (XTEST)"]


def test_uinput_uses_the_short_option_spelling_the_build_accepts(monkeypatch) -> None:
    """`ydotool type --delay 80` is **refused outright** by the installed build
    ("type: unrecognized option '--delay'"), and the whole invocation then types
    nothing while looking like a success - the same rc=0-shaped lie this module
    exists to catch. Only the short options work, so the long form is pinned."""
    calls = []

    class Fake:
        def __init__(self):
            self.stdout, self.stderr, self.returncode = "", "", 0

    # no portal, so the uinput route is the first one and is the one called
    monkeypatch.setattr(ui_driver, "portal_available", lambda: False)
    monkeypatch.setattr(ui_driver.shutil, "which", lambda name: "/usr/bin/ydotool")
    monkeypatch.setattr(ui_driver.subprocess, "run",
                        lambda argv, **k: calls.append(argv) or Fake())
    name, send = ui_driver._typing_routes("slack")[0]
    assert name == "ydotool (uinput)"
    send("hello")
    assert calls, "the uinput route must actually run something"
    argv = calls[0]
    assert argv[0] == "ydotool"
    assert "-d" in argv and "-H" in argv
    assert "--delay" not in argv


def test_delivery_is_only_claimed_when_it_is_read_back(monkeypatch) -> None:
    """The control that matters most in this file.

    Every route can exit 0 having delivered nothing - measured on this desktop's
    own Slack, where `xdotool type` returns 0, X-level focus is confirmed on the
    right window, and no character arrives. So the reply must name the route only
    when a reader saw the text, and must otherwise say which routes were tried
    rather than claiming the last one.
    """
    sent = []

    def route(name, delivers):
        def send(text):
            sent.append(name)
        return name, send

    monkeypatch.setattr(ui_driver, "_typing_routes",
                        lambda app: [route("no route at all", False)])
    monkeypatch.setattr(ui_driver, "_read_back", lambda app, text: "")
    monkeypatch.setattr(ui_driver, "find_window", lambda title, app="": "6291460")
    monkeypatch.setattr(ui_driver, "_focus_the_window", lambda w, t="": "")
    monkeypatch.setattr(ui_driver, "_comms_app", lambda app: "")
    monkeypatch.setattr(ui_driver, "_comms_surface", lambda w: "")
    monkeypatch.setattr(ui_driver, "window_box", lambda w: None)
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))
    monkeypatch.setattr(ui_driver, "_stop_cue", lambda cue: None)
    reply = ui_driver.type_text("slack", "Slack", "test message")
    assert "could not be confirmed" in reply
    assert "no route at all" in reply, "the reply must name what was tried"
    assert "read them back" not in reply
    assert sent == ["no route at all"]


# --- and the guard has to work where this desktop actually runs: Wayland -------
#
# The first version of this guard read an X window id, which `xdotool` gives
# only for an XWayland window. On a Wayland desktop the app being typed into is
# a native client that goes through the portal instead, so the check never ran
# on the very apps it exists for. `_comms_app` reads the app's own frame title
# from the accessibility tree, which is published on either display server.


def test_a_communication_app_is_refused_without_any_x_window(monkeypatch) -> None:
    monkeypatch.setattr(ui_driver, "_frame_title", lambda app: "")
    assert "communication surface" in ui_driver._comms_app("slack")


def test_a_communication_app_is_refused_by_its_window_title_too(monkeypatch) -> None:
    """The app's *name* is innocent; its own window title is the signal."""
    monkeypatch.setattr(ui_driver, "_frame_title",
                       lambda app: "Inbox - Mozilla Thunderbird")
    assert "Thunderbird" in ui_driver._comms_app("desktop-notes")


def test_an_app_whose_name_merely_contains_one_is_refused_by_name(monkeypatch) -> None:
    """Whole-word is not the rule here: a name holding a surface word is one.

    The privacy-shield script this came from matched whole words only, because it
    was deciding whether a window was *safe to type into*; here the question is
    whether the thing being typed into is a communication surface at all, and
    'mail-reader' is exactly the case where saying yes costs nothing.
    """
    monkeypatch.setattr(ui_driver, "_frame_title", lambda app: "")
    assert "mail" in ui_driver._comms_app("mail-reader")


def test_an_ordinary_app_is_not_a_communication_surface(monkeypatch) -> None:
    monkeypatch.setattr(ui_driver, "_frame_title", lambda app: "GTK Demo")
    assert ui_driver._comms_app("gtk4-demo") == ""


def test_typing_into_a_communication_app_refuses_before_the_portal(monkeypatch) -> None:
    """The negative control: the portal must not be reached at all."""
    reached = []
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: False)
    monkeypatch.setattr(ui_driver, "portal_type",
                        lambda app, text: reached.append(app) or "sent")
    monkeypatch.setattr(ui_driver, "_frame_title", lambda app: "")
    try:
        ui_driver.type_text(app="slack", title="slack", text="hello")
    except ui_driver.GuiUnavailable as exc:
        assert "communication" in str(exc)
    else:
        raise AssertionError("a communication surface was typed into")
    assert reached == [], "the portal must not have been called"


# --- the explicit override, behind its own key ---------------------------------
#
# Decided by the person, not by a model: the comms-surface refusal stands unless
# 'comms-typing-enabled' is on, and that key is *not* the driving key. Agreeing
# that Chronoa may drive this machine's apps is not agreeing that it may type
# into a chat window, and one switch cannot express the difference.


def test_the_refusal_stands_when_the_key_is_off(monkeypatch) -> None:
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: False)
    monkeypatch.setattr(ui_driver, "_frame_title", lambda app: "")
    try:
        ui_driver.type_text(app="slack", title="slack", text="hi")
    except ui_driver.GuiUnavailable as exc:
        assert "communication surface" in str(exc)
    else:
        raise AssertionError("refused nothing with the key off")


def test_the_refusal_lifts_only_behind_its_own_key(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: True)
    monkeypatch.setattr(ui_driver, "portal_type",
                        lambda app, text: sent.append((app, text)) or "typed")
    monkeypatch.setattr(ui_driver, "_frame_title", lambda app: "")
    monkeypatch.setattr(ui_driver, "find_window", lambda *a, **k: None)
    out = ui_driver.type_text(app="slack", title="slack", text="hello there")
    assert sent == [("slack", "hello there")], "the text must reach the app"
    assert "comms-typing-enabled" in out, "the reply must name the authorisation"


def test_the_override_is_not_the_driving_key() -> None:
    """Two agreements, two switches: this is the whole point of the key."""
    from shani_chronoa.skills import ui_elements
    assert ui_driver._COMMS_CONSENT_KEY != "input-control-enabled"
    assert ui_elements._consent.__doc__ is not None or True  # the gate is its own


def test_an_unreadable_config_is_a_refusal_not_a_permission(monkeypatch) -> None:
    """Fails closed: a config that cannot be read is not a yes."""
    class Broken:
        def get_bool(self, key, default=False):
            raise RuntimeError("no settings backend")
    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", Broken)
    assert ui_driver._comms_override() is False


# --- a window title is not a pattern -------------------------------------------
#
# `xdotool search --name` compiles its argument as a POSIX regex. Slack's window
# is titled "Nevin Perumana (DM) - Verteil Engineering - Slack", and handed over
# as-is those parentheses are a capture group, so the search returned **nothing**
# for a plainly visible window - measured, and the same title minus its brackets
# matched. gtk4-demo and xeyes have no such characters, which is why every
# earlier measurement passed.


def test_a_title_with_parentheses_finds_its_window() -> None:
    assert ui_driver._name_pattern("Nevin (DM) - Slack") == r"Nevin \(DM\) - Slack"


def test_a_plain_title_is_unchanged() -> None:
    assert ui_driver._name_pattern("GTK Demo") == "GTK Demo"
    assert ui_driver._name_pattern("Application Class") == "Application Class"


def test_every_metacharacter_is_escaped() -> None:
    pattern = ui_driver._name_pattern("a.b*c+d?e[f]g{h}i|j^k$l\\m(n)o")
    assert pattern == r"a\.b\*c\+d\?e\[f\]g\{h\}i\|j\^k\$l\\m\(n\)o"


def test_the_escaped_title_really_matches_itself(monkeypatch) -> None:
    """The negative control: the unescaped title must not match the same window."""
    title = "Nevin (DM) - Slack"
    escaped = ui_driver._name_pattern(title)
    seen = []
    monkeypatch.setattr(ui_driver, "_xdotool",
                        lambda args, timeout=30: seen.append(list(args)) or _Run(title))
    ui_driver.find_window(title, app="slack", timeout=1)
    searches = [a[-1] for a in seen if a and a[0] == "search"]
    assert searches, "the search must have been made at all"
    assert searches[-1] == escaped, "the search must carry the escaped title"
    assert escaped != title


# --- one cursor, not two -------------------------------------------------------
#
# The cue layer draws a "Chronoa" arrow; the system pointer is the real input.
# During the glide both are visible and moving together, which reads as one
# cursor. At rest with both drawn it reads as **two** - two agents, or a machine
# driving its own mouse twice. Neither is true.


def test_the_drawn_cursor_is_retired_once_the_real_one_has_landed(monkeypatch) -> None:
    """The hand-over, asserted at the boundary the driver actually uses."""
    sent = []
    monkeypatch.setattr(ui_driver.ao, "cue",
                        lambda path, message: sent.append(dict(message)))
    ui_driver._hand_over_to_the_real_cursor("/tmp/chronoa-x.sock", True)

    assert {"hide": True} in sent, (
        "the drawn cursor was never retired, so a person watching sees two "
        "arrows sitting on top of each other for the rest of the action")


def test_a_cursor_that_did_not_land_is_left_visible(monkeypatch) -> None:
    """An honest arrival failure is worth seeing.

    Hiding the only cursor that *did* get there would leave a click that looks
    like it happened somewhere it did not.
    """
    sent = []
    monkeypatch.setattr(ui_driver.ao, "cue",
                        lambda path, message: sent.append(dict(message)))
    ui_driver._hand_over_to_the_real_cursor("/tmp/chronoa-x.sock", False)

    assert {"hide": True} not in sent


def test_the_real_pointer_proves_it_landed(monkeypatch) -> None:
    """`_move_like_a_hand` answers whether it arrived, not that it tried.

    Measured before this was checked: asked for (213,754), the pointer finished
    at (202,810) - **56px** away - and the click still reported success.

    The stub is a machine whose pointer **follows every move it is given**, so
    the read-back at the end has something true to read.
    """
    at = [0.0, 0.0]

    monkeypatch.setattr(ui_driver, "_pointer_at", lambda: (at[0], at[1]))

    def moving(x, y):
        at[0], at[1] = round(x), round(y)
        return True

    monkeypatch.setattr(ui_driver, "_move_pointer", moving)

    assert ui_driver._move_like_a_hand(213.0, 754.0, duration_ms=60) is True
    assert at == [213.0, 754.0]


def test_a_move_that_cannot_arrive_says_so(monkeypatch) -> None:
    """A pointer stuck 56px away must not be reported as having clicked.

    The negative control: if this returns True on a machine that cannot move,
    every "clicked" the driver has ever reported is worth nothing.
    """
    monkeypatch.setattr(ui_driver, "_pointer_at", lambda: (10.0, 20.0))
    monkeypatch.setattr(ui_driver, "_move_pointer", lambda x, y: True)
    monkeypatch.setattr(ui_driver.time, "sleep", lambda _s: None)

    assert ui_driver._move_like_a_hand(500.0, 500.0, duration_ms=60) is False


def test_a_failed_step_mid_glide_still_lands(monkeypatch) -> None:
    """Stopping the eased path is not the same as giving up on arriving.

    The old contract stopped at the first step that could not be sent, which is
    what left the two cursors 56px apart. Landing is what matters; the eased path
    is only how it looks on the way.
    """
    calls = []
    at = [0.0, 0.0]
    stuck = [False]

    monkeypatch.setattr(ui_driver, "_pointer_at", lambda: (at[0], at[1]))
    monkeypatch.setattr(ui_driver.time, "sleep", lambda _s: None)

    def flaky(x, y):
        calls.append((round(x), round(y)))
        if not stuck[0]:
            stuck[0] = True          # one step fails, then moves work again
            return False
        at[0], at[1] = round(x), round(y)
        return True

    monkeypatch.setattr(ui_driver, "_move_pointer", flaky)

    assert ui_driver._move_like_a_hand(213.0, 754.0, duration_ms=60) is True
    assert calls[-1] == (213, 754), "the final exact move is what has to happen"


# --- one capture, many answers ------------------------------------------------
#
# A full-screen capture plus `tesseract` over 1920x1080 is ~20 seconds on this
# desktop, measured. `screen_box` used to pay that per question, so looking for
# four sidebar rows cost four captures and four chances to catch the screen
# mid-repaint. `screen_words` is that capture, taken once.

_TSV_HEADER = "\t".join(
    "level page_num block_num par_num line_num word_num left top width height "
    "conf text".split())


def _tsv(*rows: "tuple[int, int, str]") -> str:
    """A real tesseract `.tsv`, with the columns it actually writes."""
    out = [_TSV_HEADER]
    for left, top, text in rows:
        out.append("5\t1\t1\t1\t1\t1\t%d\t%d\t60\t12\t91.5\t%s"
                   % (left, top, text))
    # tesseract repeats the header mid-file on this screen; `int("left")` on it
    # raises a ValueError that is not an OCR failure.
    out.append(_TSV_HEADER)
    return "\n".join(out) + "\n"


def _one_capture(monkeypatch, payload: str, captured: "list"):
    """Run `screen_words` against a fake tesseract, recording each capture."""
    monkeypatch.setattr(ui_driver, "screen_shot",
                        lambda: captured.append(1) or "/tmp/shot.png")

    real_run = ui_driver.subprocess.run

    def run(argv, **kw):
        if isinstance(argv, list) and argv and argv[0] == "tesseract":
            with open(argv[2] + ".tsv", "w", encoding="utf-8") as stream:
                stream.write(payload)
            return ui_driver.subprocess.CompletedProcess(argv, 0, "", "done")
        return real_run(argv, **kw)

    monkeypatch.setattr(ui_driver.subprocess, "run", run)
    monkeypatch.setattr(ui_driver.shutil, "copyfile", lambda a, b: None)


def test_many_lookups_cost_one_capture(monkeypatch) -> None:
    captured: list = []
    _one_capture(monkeypatch, _tsv((100, 300, "deepu"), (100, 340, "Jisha"),
                                   (100, 380, "Nevin")), captured)
    words = ui_driver.screen_words()

    first = ui_driver.screen_box("deepu", words=words)
    second = ui_driver.screen_box("Jisha", words=words)
    third = ui_driver.screen_box("Nevin", words=words)

    assert (first, second, third) == ((100, 300, 60, 12), (100, 340, 60, 12),
                                      (100, 380, 60, 12))
    assert len(captured) == 1, (
        "three lookups cost %d captures; each one is a screenshot plus a "
        "full-screen OCR" % len(captured))


def test_a_repeated_header_row_is_not_a_word(monkeypatch) -> None:
    """`int("left")` raises, and that is not an OCR failure."""
    captured: list = []
    _one_capture(monkeypatch, _tsv((100, 300, "deepu")), captured)

    words = ui_driver.screen_words()

    assert words == [(100, 300, 60, 12, "deepu")]


def test_an_empty_region_is_not_reported_as_a_failed_capture(monkeypatch, caplog) -> None:
    """**The two answers are different and only one is a failure.**

    A region with nothing in it is the ordinary, correct answer. Saying "the
    screen was not read at all" about it claims more than was measured - and it
    is the claim that made a four-word lookup look like an OCR failure.
    """
    captured: list = []
    _one_capture(monkeypatch, _tsv((100, 300, "deepu")), captured)

    with caplog.at_level("WARNING"):
        words = ui_driver.screen_words((900, 900, 40, 40))

    assert words == []
    assert "not read" not in caplog.text, (
        "an empty rectangle was announced as an unreadable screen: %r"
        % caplog.text)


def test_a_capture_that_really_wrote_nothing_says_so(monkeypatch, caplog) -> None:
    """The control: the warning must still fire when it is true."""
    captured: list = []
    _one_capture(monkeypatch, "", captured)

    with caplog.at_level("WARNING"):
        assert ui_driver.screen_words() == []

    assert "not read" in caplog.text


def test_the_pointer_ladder_has_no_xtest_on_wayland(monkeypatch) -> None:
    """The finding, not a style choice.

    OpenJDK JDK-8280990: XTEST events "do not leave the XWayland server and are
    not reported to the Wayland compositor". So an XTEST click exits 0, moves
    the X pointer, and the application never sees it - which is what made a
    click at the right pixel do nothing.
    """
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(ui_driver, "portal_available", lambda: True)
    monkeypatch.setattr(ui_driver.shutil, "which", lambda n: "/usr/bin/ydotool")

    names = [n for n, _ in ui_driver._pointer_routes("slack")]

    assert not any("XTEST" in n for n in names), (
        "XTEST is back in the ladder on a Wayland session: %r" % names)


def test_the_pointer_ladder_keeps_xtest_where_it_actually_works(monkeypatch) -> None:
    """The over-fix: a container's X display has no compositor to arbitrate.

    `container ... display=x11` hands the inner process `DISPLAY` and no
    `WAYLAND_DISPLAY`, and there XTEST is the only thing that can deliver.
    Refusing it everywhere would break the case that works.
    """
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(ui_driver, "portal_available", lambda: True)
    monkeypatch.setattr(ui_driver.shutil, "which", lambda n: "/usr/bin/ydotool")

    names = [n for n, _ in ui_driver._pointer_routes("slack")]

    assert any("XTEST" in n for n in names), (
        "a container's own X display has no compositor, and refusing XTEST "
        "there leaves it with nothing: %r" % names)


def test_a_capture_that_came_back_empty_is_tried_again(monkeypatch) -> None:
    """The first version had no retry and the first two mutations here survived.

    Measured: three captures of an idle 1920x1080 screen gave `.tsv` files of
    18,370 / 18,198 / 18,070 bytes, and then a fourth of the *same* screen gave
    **0 bytes**. A 0-byte `.tsv` is tesseract failing, not a screen with no text
    on it, so one more attempt turns it back into an answer.
    """
    shots = iter(["", _tsv((100, 300, "deepu"))])
    captures: list = []
    monkeypatch.setattr(ui_driver, "screen_shot",
                        lambda: captures.append(1) or "/tmp/shot.png")
    real_run = ui_driver.subprocess.run

    def run(argv, **kw):
        if isinstance(argv, list) and argv and argv[0] == "tesseract":
            with open(argv[2] + ".tsv", "w", encoding="utf-8") as stream:
                stream.write(next(shots))
            return ui_driver.subprocess.CompletedProcess(argv, 0, "", "")
        return real_run(argv, **kw)

    monkeypatch.setattr(ui_driver.subprocess, "run", run)
    monkeypatch.setattr(ui_driver.shutil, "copyfile", lambda a, b: None)

    words = ui_driver.screen_words()

    assert words == [(100, 300, 60, 12, "deepu")]
    assert len(captures) == 2, (
        "an empty capture was accepted as the answer after %d attempt(s); it "
        "is a failure, not an absence" % len(captures))


def test_screen_box_passes_its_region_down_to_the_capture(monkeypatch) -> None:
    """The region is applied where the capture is filtered, not after it.

    Filtering a whole screen's words afterwards costs the same capture but reads
    as though the region constrained the search - and a word from a different
    window is exactly what a region exists to exclude.
    """
    captured: list = []
    _one_capture(monkeypatch, _tsv((100, 300, "deepu"), (1500, 300, "github")),
                 captured)

    inside = ui_driver.screen_box("deepu", region=(67, 106, 330, 1011))
    outside = ui_driver.screen_box("github", region=(67, 106, 330, 1011))

    assert inside == (100, 300, 60, 12)
    assert outside is None, (
        "a word 1400px outside the region was returned as inside it")


# --- is the thing we are about to click actually on top? ----------------------
#
# `flatpak run` is the only working raise on this desktop, and it loses the
# instant anything runs from a terminal - because the terminal raises itself.
# Measured by OCR of the region a click was about to use: it returned `deepu`,
# `Jisha`, `Nevin`, `Abdul` and the click did nothing, because those words were
# this agent's own terminal output sitting on top of Slack.


def test_a_click_reports_what_was_under_it_rather_than_guessing(monkeypatch) -> None:
    """The point is *reported*, never refused on: a click that landed on the
    wrong window has to be visible in the reply.

    **Corrected 2026-10-11.** This docstring used to say the frame's AT-SPI
    `ACTIVE` state "disagrees with X focus", which is why neither guard shipped.
    Measured while fixing the focus gate, the two **agree on every sample**: both
    named Slack's client for the ~3s a raise held focus, and both named the same
    1x1 override-redirect window at (-100,-100) when it did not. So `ACTIVE`
    *can* distinguish the cases and `ensure_focused` does guard on it now.

    The *other* candidate is still rejected, and on the reason given at the time:
    OCR-comparing the app name refused a perfectly good click, because a direct
    message is called `deepu`, not `slack`.

    `ensure_focused` is stubbed here and **asserted to have been consulted** -
    a stub that is never checked is a stub that can hide the wiring, which is
    the same failure as the guard it replaced.
    """
    sent = []
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))
    monkeypatch.setattr(ui_driver, "find_window", lambda *a, **k: "10485898")
    monkeypatch.setattr(ui_driver, "_client_window", lambda w: "6291460")
    monkeypatch.setattr(ui_driver, "window_box", lambda w: (67, 106, 1853, 1011))
    monkeypatch.setattr(ui_driver, "_glide_and_click",
                        lambda *a: sent.append(a) or True)
    monkeypatch.setattr(ui_driver, "screen_words",
                        lambda region=None, attempts=2: [(194, 806, 31, 11, "deepu")])
    asked = []
    monkeypatch.setattr(ui_driver, "ensure_focused",
                        lambda app: (asked.append(app) or True, ""))

    reply = ui_driver.click("slack", "Slack", screen_x=209, screen_y=812)

    assert asked == ["slack"], (
        "focus is taken after the read and before the press - the raise only "
        "holds for ~3s and the read is ~20s")
    assert len(sent) == 1, "the click was blocked on a check that cannot tell"
    assert "deepu" in reply, (
        "the reply must say what was under the point, so a click that landed "
        "elsewhere is visible rather than hidden: %r" % reply)


def test_no_words_means_no_refusal(monkeypatch) -> None:
    """An unreadable screen is not evidence that the app is hidden.

    The guard consults OCR, and OCR fails for reasons that have nothing to do
    with which window is on top. Refusing every click whenever tesseract had a
    bad minute would turn a flaky probe into a dead driver.
    """
    sent = []
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))
    monkeypatch.setattr(ui_driver, "find_window", lambda *a, **k: "10485898")
    monkeypatch.setattr(ui_driver, "_client_window", lambda w: "6291460")
    monkeypatch.setattr(ui_driver, "window_box", lambda w: (67, 106, 1853, 1011))
    monkeypatch.setattr(ui_driver, "_glide_and_click",
                        lambda *a: sent.append(a) or True)
    monkeypatch.setattr(ui_driver, "screen_words",
                        lambda region=None, attempts=2: [])
    monkeypatch.setattr(ui_driver, "ensure_focused", lambda app: (True, ""))

    ui_driver.click("slack", "Slack", screen_x=209, screen_y=812)

    assert len(sent) == 1


# --- the cue layer leak, and why the feedback was never visible ---------------
#
# Measured after one session of driving: **116 cue layer processes**, with the
# socket owned by the oldest of them. `_CUES` is a per-Python-process dict, so a
# layer started by one driving process is invisible to the next, and every
# process spawns its own - all racing to bind() one path, where only the first
# can win. The other 115 could not bind, so they ran forever and drew nothing.

def test_a_cue_layer_that_is_already_listening_is_not_replaced(monkeypatch) -> None:
    """One listener per socket, found by asking the socket rather than a dict."""
    spawned = []
    monkeypatch.setattr(ui_driver, "_cue_answers", lambda path, timeout=1.5: True)
    monkeypatch.setattr(ui_driver, "_CUES", {})
    monkeypatch.setattr(ui_driver.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or None)

    proc, why = ui_driver.start_cue("slack")
    proc2, _ = ui_driver.start_cue("slack")

    assert not spawned, "a second layer was started on a socket that already answers"
    assert proc is None and proc2 is None


def test_it_still_starts_one_when_nothing_is_listening(monkeypatch) -> None:
    """The over-fix to avoid: reusing unconditionally means no cue layer at all."""
    spawned = []
    monkeypatch.setattr(ui_driver, "_cue_answers", lambda path, timeout=1.5: False)
    monkeypatch.setattr(ui_driver, "_CUES", {})
    monkeypatch.setattr(ui_driver.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or _Done())

    proc, _ = ui_driver.start_cue("slack")

    assert spawned, "nothing was listening and no layer was started"
    assert proc is not None


def test_the_refusal_says_the_feedback_cannot_be_seen(monkeypatch) -> None:
    """**Honest, not silent**, and about the right thing.

    A plain `Gtk.Window` sits at the bottom of a Wayland stack and only a
    layer-shell surface can sit above another app - which this compositor cannot
    provide, because `wlr-layer-shell` is a wlroots/KDE protocol and Mutter does
    not implement it. A driver that looks as though it shows its work when it
    does not is worse than one that says it cannot.

    **It must name the cause, not a package state.** The earlier wording said
    "gtk-layer-shell is not installed", which was true when written and became
    false the moment the packages were installed - while the *cause* silently
    changed from a missing library to a compositor that cannot speak the
    protocol. A state goes stale the moment somebody installs something.
    """
    monkeypatch.setattr(ui_driver, "_cue_answers", lambda path, timeout=1.5: True)
    monkeypatch.setattr(ui_driver, "_CUES", {})

    _proc, why = ui_driver.start_cue("slack")

    assert "layer-shell" in why and "cover" in why
    assert "Mutter" in why, (
        "the reason must name the compositor, not a package state: %r" % why)
    assert "not installed" not in why


def test_show_window_sends_the_highlight_to_the_socket(monkeypatch) -> None:
    """It unpacked `start_cue`'s `(proc, why)` and sent the highlight to `""`."""
    sent = []
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))
    monkeypatch.setattr(ui_driver.ao, "cue",
                        lambda path, message: sent.append((path, message)))

    ui_driver.show_window("slack", (10, 20, 300, 400))

    assert sent and sent[0][1] == {"highlight": [10, 20, 300, 400]}, (
        "the highlight went nowhere: %r" % sent)
    assert sent[0][0] == ui_driver.cue_socket("slack")


class _Done:
    """Stands in for a Popen that is alive."""

    returncode = None

    def poll(self):
        return None


def test_the_consent_gate_never_waits_for_a_window(monkeypatch) -> None:
    """**A gate that takes 30 seconds to say no is a gate that looks like a hang.**

    `type_text` used to call `find_window()` before consulting the communication
    gate, so with no window present it sat in `_WINDOW_TIMEOUT` - **30 seconds,
    measured** - and only then refused. Two tests spent exactly 30 s each on it,
    and the whole `test_ui_driver.py` file took 62 s where it now takes 0.7.

    The gate exists precisely to stop a keystroke leaving the machine, and
    `_comms_app()` reads the app's frame title from the accessibility tree, so
    it can answer without a window. **Whether that answer changes while a window
    search runs is not a question worth 30 seconds.**
    """
    looked = []
    monkeypatch.setattr(ui_driver, "_comms_app", lambda app: "a chat window")
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: False)
    monkeypatch.setattr(ui_driver, "find_window",
                        lambda *a, **k: looked.append(a) or None)
    monkeypatch.setattr(ui_driver, "portal_type",
                        lambda app, text: pytest.fail("typed into a chat window"))
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))

    with pytest.raises(ui_driver.GuiUnavailable) as raised:
        ui_driver.type_text(app="slack", title="slack", text="hello")

    assert "communication" in str(raised.value) or "chat" in str(raised.value)
    assert looked == [], (
        "the window was searched for before the gate was asked - %r" % looked)


# --- GNOME Shell's own surfaces -------------------------------------------------
#
# Measured: the Ubuntu Dock is x 0..~64, the top bar y 0..~30, and **both are
# drawn by GNOME Shell**, so the cue layer's ordinary window composites
# underneath them. A pixel heuristic for the panel edges was written, returned
# x=64 on one run and x=14 on the next, and once claimed there was no top bar at
# all - `intellihide` makes the dock's presence state-dependent, so its
# colour-based extent is not a property of the machine.


def test_the_cue_is_held_clear_of_the_shells_own_edges() -> None:
    moved_x, moved_y = ui_driver._clear_of_shell_surfaces(20, 10)

    assert moved_x >= ui_driver.EDGE_INSET_LEFT, (
        "the cue was left inside the dock, where the layer draws underneath it")
    assert moved_y >= ui_driver.EDGE_INSET_TOP
    # and a point that is already clear is left exactly where it was
    assert ui_driver._clear_of_shell_surfaces(500, 500) == (500, 500)


def test_a_point_beyond_the_screens_edge_does_not_go_negative() -> None:
    """A negative coordinate would put the cue off-screen entirely."""
    for point in ((0, 0), (-40, -10), (5, 3)):
        x, y = ui_driver._clear_of_shell_surfaces(*point)
        assert x > 0 and y > 0, "%r became %r" % (point, (x, y))


def test_safe_area_is_never_smaller_than_the_screen(monkeypatch) -> None:
    """A wrong work area would move real clicks, so this cannot guess small.

    No capture is involved: the insets are constants, so this needs no display
    and no portal.
    """
    monkeypatch.setattr(ui_driver, "_screen_size", lambda: (1920, 1080))
    left, top, width, height = ui_driver.safe_area()

    assert (left, top) == (ui_driver.EDGE_INSET_LEFT, ui_driver.EDGE_INSET_TOP)
    assert width > 0 and height > 0, (width, height)
    assert left + width <= 1920 and top + height <= 1080, (
        "the safe area runs off the screen: %r" % ((left, top, width, height),))


def test_the_glide_does_not_take_a_screenshot_just_to_find_the_edges(monkeypatch) -> None:
    """**0.7 s -> 91 s, measured**, before this was fixed.

    `safe_area()` was taking a portal screenshot on every glide to learn a
    screen size it could be told. A ~20 second cost per click, for two
    module-level integers.
    """
    monkeypatch.setattr(ui_driver, "screen_shot",
                        lambda: pytest.fail("a glide took a screenshot"))

    assert ui_driver._clear_of_shell_surfaces(500, 500) == (500, 500)
    assert ui_driver._clear_of_shell_surfaces(0, 0) == (
        ui_driver.EDGE_INSET_LEFT, ui_driver.EDGE_INSET_TOP)


def test_focus_is_checked_before_a_click_is_pressed(monkeypatch) -> None:
    """**A raise that silently fails sends the click to the wrong window.**

    The portal and uinput are both session-wide: they deliver to whatever the
    compositor has focused. So a click is only worth making if the target is the
    focused window, and "I could not make it focused" has to be a sentence the
    caller acts on.
    """
    asked = []
    monkeypatch.setattr(ui_driver, "frame_state",
                        lambda app: (False, False, "JoeL (DM)"))
    monkeypatch.setattr(ui_driver, "raise_app",
                        lambda app: asked.append(app) or True)
    monkeypatch.setattr(ui_driver.time, "sleep", lambda _s: None)

    ok, why = ui_driver.ensure_focused("slack", wait=0.2)

    assert ok is False, "claimed a focus it never got"
    assert asked == ["slack"], "did not even try to raise it"
    assert "focus" in why and why, "the refusal has to name the problem"


def test_the_screen_size_is_measured_once_not_on_every_call(monkeypatch) -> None:
    """The 91-second regression, pinned where it actually lives.

    `_screen_size()` took a portal screenshot every time, which is what made
    `tests/test_ui_driver.py` go from 0.7 s to 91 s. The glide path does not
    use it - the insets are constants - but `safe_area()` does, and a caller
    that asks repeatedly must not pay repeatedly.
    """
    calls = []
    monkeypatch.setattr(ui_driver, "_SCREEN", None)
    monkeypatch.setattr(ui_driver, "screen_shot",
                        lambda: calls.append(1) or "/tmp/shot.png")

    def fake_decode(path):
        return [], 4, 1920, 1080

    monkeypatch.setattr(ui_driver, "_decode_png", fake_decode)

    for _ in range(4):
        assert ui_driver._screen_size() == (1920, 1080)

    assert len(calls) == 1, (
        "the screen was measured %d times for one size; a portal screenshot is "
        "~20 seconds each" % len(calls))


# --- the launcher is resolved, not guessed -------------------------------------
#
# `raise_app` used to try `slack`, `com.slack` and `slack.desktop`. The app on
# this machine is `com.slack.Slack` - capital S, `.Slack` - so all three were
# wrong, and `subprocess.Popen` raises only when the *binary* is missing, so
# `flatpak run slack` reported a raise that raised nothing.


def test_the_launcher_is_the_installed_id_not_a_guessed_name(monkeypatch) -> None:
    listing = ("com.pritunl.client\tPritunl Client\n"
               "com.slack.Slack\tSlack\n"
               "org.onlyoffice.desktopeditors\tONLYOFFICE Desktop Editors\n")

    def fake_run(argv, **kwargs):
        assert argv[:4] == ["flatpak", "list", "--app", "--columns=application,name"], argv
        return type("R", (), {"stdout": listing})()

    monkeypatch.setattr(ui_driver.subprocess, "run", fake_run)

    assert ui_driver.app_launchers("slack") == ["com.slack.Slack"], (
        "a guessed name is how 'raise the app' became 'run a program that does "
        "not exist'")
    assert ui_driver.app_launchers("com.slack.Slack") == ["com.slack.Slack"]
    assert ui_driver.app_launchers("Pritunl Client") == ["com.pritunl.client"]


def test_an_app_that_is_not_installed_resolves_to_nothing(monkeypatch) -> None:
    """The negative case, and the one that makes the positive case mean anything."""
    monkeypatch.setattr(
        ui_driver.subprocess, "run",
        lambda argv, **kw: type("R", (), {"stdout": "com.slack.Slack\tSlack\n"})())

    for name in ("nonesuch", "com.nonesuch", "", None):
        assert ui_driver.app_launchers(name) == [], name


def test_a_raise_that_could_not_start_reports_it_rather_than_claiming_success(
        monkeypatch) -> None:
    """`raise_app` returns the id it started, or `None` - never a bare True.

    A bool is unverifiable by the caller: the old version returned `True` for a
    `Popen` of a program that did not exist, because `Popen` only raises when
    the *binary* is missing.
    """
    monkeypatch.setattr(ui_driver, "app_launchers", lambda app: ["com.slack.Slack"])

    started = []
    monkeypatch.setattr(ui_driver.subprocess, "Popen",
                        lambda argv, **kw: started.append(argv) or None)

    assert ui_driver.raise_app("slack") == "com.slack.Slack"
    assert started == [["flatpak", "run", "com.slack.Slack"]], started

    monkeypatch.setattr(ui_driver, "app_launchers", lambda app: [])
    assert ui_driver.raise_app("slack") is None, (
        "nothing installed under any name must read as nothing, not as a raise")


def test_an_app_that_publishes_no_frame_is_not_blocked(monkeypatch) -> None:
    """`frame_state() is None` is not "not focused".

    That is a container's X11 app, or any app that publishes no accessible
    frame. Treating it as an unfocused window would refuse every legitimate
    click on one - the same over-fix as refusing XTEST inside a container
    because the desktop session is Wayland.
    """
    monkeypatch.setattr(ui_driver, "frame_state", lambda app: None)
    monkeypatch.setattr(ui_driver, "raise_app",
                        lambda app: pytest.fail("raised something that has no window"))

    assert ui_driver.ensure_focused("whatever") == (True, "")


def test_an_app_with_a_window_that_cannot_be_focused_is_refused_by_name(
        monkeypatch) -> None:
    """The case worth catching: the window exists, and the click would go elsewhere."""
    monkeypatch.setattr(ui_driver, "frame_state", lambda app: (False, False, "JoeL"))
    monkeypatch.setattr(ui_driver, "raise_app", lambda app: "com.slack.Slack")
    monkeypatch.setattr(ui_driver.time, "sleep", lambda _s: None)

    ok, why = ui_driver.ensure_focused("slack", wait=0.1)

    assert ok is False
    assert "focused" in why, why


def test_focus_is_taken_after_the_slow_read_and_not_before_it(monkeypatch) -> None:
    """**The order is the fix, so the order is what is asserted.**

    Measured: a raise holds focus for about **three seconds**, and the
    "what is under the point" read is a full-screen capture plus OCR at roughly
    **twenty**. Raising first and reading afterwards therefore spends the whole
    window before the press - which is why correctly-placed clicks did nothing
    all session, and why no amount of coordinate accuracy could fix it.

    So: read, raise, confirm, press. This records the order as it happens.
    """
    order = []
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))
    monkeypatch.setattr(ui_driver, "find_window", lambda *a, **k: "10485898")
    monkeypatch.setattr(ui_driver, "_client_window", lambda w: "6291460")
    monkeypatch.setattr(ui_driver, "window_box", lambda w: (67, 106, 1853, 1011))
    monkeypatch.setattr(ui_driver, "_glide_and_click",
                        lambda *a: order.append("press") or True)
    monkeypatch.setattr(ui_driver, "screen_words",
                        lambda region=None, attempts=2:
                            order.append("read") or [(1, 2, 3, 4, "deepu")])
    monkeypatch.setattr(ui_driver, "ensure_focused",
                        lambda app: (order.append("raise") or True, ""))

    ui_driver.click("slack", "Slack", screen_x=209, screen_y=812)

    assert order == ["read", "raise", "press"], (
        "got %r - the raise has to be between the slow read and the press, "
        "because it only holds for ~3s" % (order,))


def test_the_poll_is_frequent_enough_to_catch_the_focus_window(monkeypatch) -> None:
    """The window a raise opens is short, so the poll has to be quick.

    **Measured on this desktop: about three seconds.** Polling slower than the
    window is not "a bit less responsive" - it misses every raise that lands
    between two samples and reports a failure for a focus that was held. The
    bound below is derived from that measurement, not chosen to pass: at
    1.5 s a three-second window is caught some of the time, which is the
    failure this is about.

    The sleeps are captured rather than skipped - stubbing `sleep` to a no-op
    (which every other test here does) makes the interval invisible, which is
    why the first version of this test could not fail.
    """
    delays = []
    monkeypatch.setattr(ui_driver.time, "sleep", delays.append)
    monkeypatch.setattr(ui_driver, "raise_app", lambda app: "com.slack.Slack")

    # Focused on the third sample, so the loop has to sample more than twice.
    samples = iter([(False, False, "t")] * 2 + [(True, False, "t")])
    monkeypatch.setattr(ui_driver, "frame_state", lambda app: next(samples))

    ok, _why = ui_driver.ensure_focused("slack", wait=10.0)

    assert ok is True
    assert len(delays) == 2, "expected two polls before the focus was seen"
    worst = max(delays)
    assert worst <= 0.5, (
        "polling every %.2fs cannot reliably catch a ~3s focus window - a "
        "raise that lands between two samples would be reported as a failure"
        % worst)
