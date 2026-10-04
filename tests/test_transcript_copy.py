"""The transcript's copy buttons, driven as the user drives them.

Alpaca copies code per block, and it is worth having for the obvious reason: a
reply that is three sentences of explanation around one command has two
different things worth copying, and copying the whole reply to get
`sudo systemctl restart NetworkManager` means also pasting the prose into the
terminal.

Every test here emits a real `clicked` on a real button in a real
`TranscriptView` and reads the clipboard back through GDK. Asserting on the
handler's arguments instead would pass just as happily if the button were never
connected - which is the specific bug this repo has shipped before (the orb
button existed, looked right, and did nothing).
"""

import time

import gi
import pytest

gi.require_version("Gtk", "4.0")

pytest.importorskip("gi")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa.gui.blocks import parse  # noqa: E402
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402

REPLY = ("Run this after the edit:\n\n"
         "```bash\nsudo systemctl restart NetworkManager\n```\n\n"
         "It takes a second and the panel icon comes back on its own.")

#: The code copy moved into the code block's own header when replies became
#: blocks (`gui/blocks.py`), which is where it belongs: the block knows which
#: lines are its own, so the turn no longer has to guess.
CODE_BUTTON = "Copy this code"
BLOCK_SAVE = "Save this script"


def _buttons(widget):
    """Every *visible* button under a widget, in tree order.

    Invisible matters: the transcript's "jump to the newest message" button lives
    in the tree at all times and is hidden until the reader scrolls away, so a
    test counting buttons has to mean the ones a person could press.
    """
    found, stack = [], [widget]
    while stack:
        node = stack.pop(0)
        if isinstance(node, Gtk.Button) and node.get_visible():
            found.append(node)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def _read_clipboard(button):
    """One read of the clipboard, on the GTK main loop, with a timeout."""
    loop = GLib.MainLoop()
    read = {"text": None, "done": False}

    def finished(clipboard, result, _data):
        try:
            read["text"] = clipboard.read_text_finish(result)
        except GLib.Error as exc:                      # pragma: no cover - a real clipboard failure
            read["text"] = f"<error {exc.message}>"
        read["done"] = True
        loop.quit()
        return False

    button.get_clipboard().read_text_async(None, finished, None)
    GLib.timeout_add(3000, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
    loop.run()
    return read["text"]


def _clipboard_after_clicking(button, expect=None):
    """Emit `clicked`, then read the clipboard GDK actually holds.

    **Copying is asynchronous, and so is the read.** On X11 the button hands
    the text to GDK, which publishes a selection the next reader requests; a
    read issued in the same breath can still see the previous owner. Measured
    in a full-suite run: the same test that always passed alone read the
    *previous* test's text three times, because the machine was busy enough for
    the round trip to take longer than the read.

    So when the caller knows what it expects, this waits for it - up to three
    seconds - rather than comparing against whichever value happened to be
    there. A copy that never happened still fails: the wait ends with
    something else, and `assert read == expect` is the test's own claim.
    """
    button.emit("clicked")
    text = _read_clipboard(button)
    if expect is not None:
        deadline = time.monotonic() + 3.0
        while text != expect and time.monotonic() < deadline:
            time.sleep(0.05)
            text = _read_clipboard(button)
    return text


def _view_with(reply):
    view = TranscriptView()
    view.add_assistant_turn(reply)
    return view, {button.get_tooltip_text(): button for button in _buttons(view)}


class TestTheCodeButton:
    def test_a_reply_with_a_command_offers_both_copies(self):
        _view, buttons = _view_with(REPLY)
        assert set(buttons) == {"Copy this reply", CODE_BUTTON, BLOCK_SAVE}

    def test_copying_the_code_leaves_the_prose_and_the_fences_behind(self):
        _view, buttons = _view_with(REPLY)
        assert _clipboard_after_clicking(
            buttons[CODE_BUTTON],
            "sudo systemctl restart NetworkManager") == \
            "sudo systemctl restart NetworkManager"

    def test_copying_the_reply_still_gives_the_whole_reply(self):
        """The code button is an addition, not a replacement: the explanation is
        worth copying too."""
        _view, buttons = _view_with(REPLY)
        assert _clipboard_after_clicking(buttons["Copy this reply"], REPLY) == REPLY

    def test_a_reply_without_a_block_offers_only_one_button(self):
        """A second button that copies nothing is a control that lies."""
        _view, buttons = _view_with("Root has **38G** free of 120G.")
        assert set(buttons) == {"Copy this reply"}

    def test_each_block_copies_only_its_own_code(self):
        """Two blocks means two buttons, and each one copies its own lines. A
        single button holding both would hand the user a script with an
        explanation wedged into the middle of it."""
        reply = "First:\n\n```sh\npacman -Syu\n```\n\nThen:\n\n```sh\nreboot\n```"
        view = TranscriptView()
        view.add_assistant_turn(reply)
        from shani_chronoa.gui import blocks as reply_blocks
        found = []

        def walk(node):
            if isinstance(node, reply_blocks.CodeBlock):
                found.append(node)
            child = node.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(view)
        assert [block.code for block in found] == ["pacman -Syu", "reboot"]
        assert len([b for b in _buttons(view) if b.get_tooltip_text() == CODE_BUTTON]) == 2

    def test_an_unclosed_fence_is_not_offered_as_code(self):
        """There is no end to it, so copying it would hand over a partial
        command that runs."""
        _view, buttons = _view_with("Try this:\n\n```bash\nsudo systemctl restart")
        assert set(buttons) == {"Copy this reply"}

    def test_a_table_gets_its_own_controls_not_a_code_block(self):
        """A table is not code: it copies as TSV and saves as CSV, and offering
        "Run this in a terminal" for a table of disk sizes would be nonsense."""
        view = TranscriptView()
        view.add_assistant_turn("| a | b |\n|---|---|\n| 1 | 2 |")
        assert "Copy this code" not in {b.get_tooltip_text() for b in _buttons(view)}
        assert {"Copy as TSV", "Save as CSV"} <= {b.get_tooltip_text() for b in _buttons(view)}
        from shani_chronoa.gui import blocks as reply_blocks
        tables = []

        def walk(node):
            if isinstance(node, reply_blocks.TableBlock):
                tables.append(node)
            child = node.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(view)
        assert tables and tables[0].as_tsv() == "a\tb\n1\t2"
        assert tables[0].as_csv() == "a,b\r\n1,2\r\n"


class TestCodeBlocks:
    """`code_blocks` used to live in widgets.py and is now `blocks.parse`: one
    parser, so "what the copy button copies" and "what the window shows" cannot
    disagree about where a fence is."""

    @staticmethod
    def codes(text):
        return [str(block.payload) for block in parse(text) if block.kind == "code"]

    def test_only_the_contents_are_returned(self):
        assert self.codes("a\n```python\nprint(1)\n```\nb") == ["print(1)"]

    def test_the_language_tag_is_not_part_of_the_code(self):
        assert self.codes("```bash\nls\n```") == ["ls"]

    def test_no_text_is_no_blocks(self):
        assert self.codes("") == []

    def test_an_unclosed_fence_is_not_a_block(self):
        """There is no end to it, so treating it as code would copy a partial
        command that runs."""
        assert self.codes("```bash\nsudo systemctl restart") == []
