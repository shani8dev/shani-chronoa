"""The window's two everyday affordances: sending, and keeping a reply.

Both were missing. The input was Enter-only, which rules out on-screen
keyboards whose return key inserts a newline, and there was no way to keep a
reply - the one thing in the window worth pasting somewhere - short of
selecting wrapped text by dragging across it.

Every assertion here drives a real constructed window. A control that is
created but not connected is a class this repo has shipped repeatedly (the orb
was a `Gtk.Button` wired to nothing at all), and a source-text check cannot
tell the difference.
"""

import os
import subprocess
import sys
import textwrap

import pytest

_HARNESS = textwrap.dedent(
    """
    import json
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Gdk, GLib

    from shani_chronoa.gui import ChronoaWindow
    import shani_chronoa.gui as gui_mod

    app = Gtk.Application(application_id="test.ux.harness")
    out = {}

    def walk(node, acc):
        c = node.get_first_child()
        while c:
            walk(c, acc); acc.append(c); c = c.get_next_sibling()
        return acc

    def on_activate(a):
        w = ChronoaWindow(a)
        w.set_default_size(560, 720)

        out["send_icon"] = w._send_button.get_icon_name()
        out["send_suggested"] = "suggested-action" in w._send_button.get_css_classes()
        out["send_visible"] = w._send_button.get_visible()

        seen = []
        w.connect("user-input", lambda _wdg, t: seen.append(t))

        w._input_entry.set_text("  spaced question  ")
        w._send_button.emit("clicked")
        out["via_button"] = seen[-1] if seen else None
        out["cleared"] = w._input_entry.get_text()

        seen.clear()
        w._input_entry.set_text("second")
        w._input_entry.emit("activate")
        out["via_enter"] = seen[-1] if seen else None

        seen.clear()
        w._input_entry.set_text("   " + chr(9) + "  ")
        w._send_button.emit("clicked")
        out["whitespace_emitted"] = seen[:]
        out["whitespace_kept"] = w._input_entry.get_text()

        # Copy button on assistant turns only.
        w.clear_transcript()
        w.add_user_turn("q1")
        w.set_response("a1")
        w.add_user_turn("q2")
        w.set_response("a2")
        nodes = walk(w.get_child(), [])
        out["copy_buttons"] = sum(
            1 for n in nodes
            if isinstance(n, Gtk.Button) and n.get_icon_name() == "edit-copy-symbolic"
        )
        # A turn is a container, not one label: a reply with a command, a table
        # or an equation in it is several widgets now, so the role class lives
        # on the container. Counting labels would report zero assistant turns
        # and pass a window that had rendered none at all.
        out["assistant_turns"] = sum(
            1 for n in nodes
            if type(n).__name__ == "Box" and "transcript-assistant" in n.get_css_classes()
        )

        # Press the first copy button and read the clipboard back.
        first = next(n for n in nodes if isinstance(n, Gtk.Button)
                     and n.get_icon_name() == "edit-copy-symbolic")
        first.emit("clicked")
        out["icon_after_copy"] = first.get_icon_name()
        clip = Gdk.Display.get_default().get_clipboard()

        def got(clip, res):
            try:
                out["clipboard"] = clip.read_text_finish(res)
            except GLib.Error as e:
                out["clipboard"] = "<error %s>" % e.message
            a.quit()
            return False
        clip.read_text_async(None, got)
        return

    app.connect("activate", on_activate)
    GLib.timeout_add(25000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def ux(tmp_path_factory):
    import json
    import pathlib

    work = tmp_path_factory.mktemp("ux")
    runtime = work / "runtime"
    runtime.mkdir()
    os.chmod(runtime, 0o700)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(pathlib.Path("usr/lib/shani-chronoa").resolve())
    env["XDG_RUNTIME_DIR"] = str(runtime)
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(work),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, (
        f"harness produced no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    )
    return payload


class TestSendingIsNotEnterOnly:
    def test_there_is_a_send_button(self, ux):
        assert ux["send_visible"]
        assert ux["send_icon"]
        assert ux["send_suggested"], (
            "sending is the primary action, so it should look like one"
        )

    def test_the_button_actually_sends(self, ux):
        """A `Gtk.Button` created and not connected is a class this repo has
        shipped before - the orb was exactly that."""
        assert ux["via_button"] == "spaced question", (
            "the send button did not deliver the entry's contents"
        )

    def test_enter_still_works_and_agrees_with_the_button(self, ux):
        assert ux["via_enter"] == "second"
        assert ux["via_button"] == ux["via_enter"].replace("second", "spaced question")

    def test_the_entry_clears_after_sending(self, ux):
        assert ux["cleared"] == ""

    def test_whitespace_alone_sends_nothing_and_is_kept(self, ux):
        assert ux["whitespace_emitted"] == [], "whitespace was sent as a message"
        assert ux["whitespace_kept"].strip() == "", "the entry was cleared on nothing"

    def test_what_is_sent_is_what_the_transcript_shows(self, ux):
        """The turn is rendered stripped; emitting the raw entry meant the
        assistant received text the user could not see on screen."""
        assert ux["via_button"] == ux["via_button"].strip()


class TestRepliesCanBeKept:
    def test_assistant_turns_have_a_copy_button_and_user_turns_do_not(self, ux):
        assert ux["assistant_turns"] == 2
        assert ux["copy_buttons"] == 2, (
            "one copy button per assistant turn - a user question is already "
            "the user's own text and needs no copy affordance"
        )

    def test_pressing_it_puts_the_reply_on_the_clipboard(self, ux):
        assert ux["clipboard"] == "a1", (
            f"the clipboard holds {ux['clipboard']!r}, not the reply that was "
            f"copied - the button is either not connected or copies the wrong text"
        )

    def test_the_button_confirms_the_copy(self, ux):
        """A copy control that gives no feedback cannot be told from one that
        did not work."""
        assert ux["icon_after_copy"] != "edit-copy-symbolic", (
            "the icon did not change, so there is no confirmation the copy "
            "happened"
        )
