"""The transcript's copy buttons: which text each one puts on the clipboard.

Alpaca copies code per block, and it is worth having for the obvious reason: a
reply that is three sentences of explanation around one command has two
different things worth copying, and copying the whole reply to get
`sudo systemctl restart NetworkManager` means also pasting the prose into the
terminal.

**Where the interception is, and why.** An earlier version emitted a real
`clicked` and read the value back with `Gdk.Clipboard.read_text_async`, which is
the stronger claim - it proves the copy landed. It does not work here, and the
measurements say why:

- After `tests/test_portal_input.py` runs, the async read is issued on a healthy
  default display (`GdkX11Clipboard`, the same object as
  `Gdk.Display.get_default()`, correct session bus, no `GDK_BACKEND` override)
  and its callback **never fires** - three seconds, sixty attempts, `None` every
  time - while identical code passes in isolation.
- The synchronous alternative, `Gdk.Clipboard.get_content()`, is worse: it
  returns the value cached *before* the click, so a button that wrote the right
  answer still reads as having written nothing. Measured with a sentinel.

Both are reads through this machine's X server, and it will not answer. So the
clipboard write is intercepted at its own boundary instead - `blocks.py:
_copy_to_clipboard` and `widgets.py:_on_copy_clicked`, the one place each path
hands text to GDK - and the *buttons, signals and payloads are all real*.

That still catches the bug the read-back was there for. Asserting only on a
handler's arguments would pass happily if a button were never connected, which
is the specific failure this repo has shipped before (the orb button existed,
looked right, and did nothing): here every test clicks a **real** button and
inspects what crossed the clipboard boundary, so an unconnected button writes
nothing and fails.
"""


import gi
import pytest

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

pytest.importorskip("gi")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import blocks  # noqa: E402
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402


CODE = "sudo systemctl restart NetworkManager"
REPLY = (f"Run this after the edit:\n\n```bash\n{CODE}\n```\n\n"
         "It takes a second and the panel icon comes back on its own.")
CODE_BUTTON = "Copy this code"
REPLY_BUTTON = "Copy this reply"


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


@pytest.fixture
def transcript(monkeypatch):
    """A real `TranscriptView` holding `REPLY`, with the clipboard boundary
    recorded instead of performed.

    Both paths are intercepted at the last line before GDK: a code block copies
    through `blocks._copy_to_clipboard`, the whole reply through
    `TranscriptView._on_copy_clicked`. Recording either one is recording "this
    is the text that would have been on the clipboard".
    """
    copied = []

    def record_blocks(widget, text):
        copied.append(("block", text))

    def record_reply(button, text):
        copied.append(("reply", text))

    monkeypatch.setattr(blocks, "_copy_to_clipboard", record_blocks)
    monkeypatch.setattr(TranscriptView, "_on_copy_clicked",
                        staticmethod(record_reply))
    view = TranscriptView()
    view.add_assistant_turn(REPLY)
    buttons = {}
    for widget in _walk(view):
        if isinstance(widget, Gtk.Button):
            buttons.setdefault(widget.get_tooltip_text(), widget)
    return view, buttons, copied


def test_a_reply_with_a_command_offers_both_copies(transcript):
    _view, buttons, _copied = transcript
    assert {CODE_BUTTON, REPLY_BUTTON} <= set(buttons)


def test_copying_the_code_leaves_the_prose_and_the_fences_behind(transcript):
    _view, buttons, copied = transcript
    buttons[CODE_BUTTON].emit("clicked")
    assert copied == [("block", CODE)]
    written = copied[0][1]
    assert "```" not in written, "the fences are markup, not the code"
    assert "Run this after" not in written, "the explanation is not the command"
    assert "bash" not in written, "the language tag is markup too"


def test_copying_the_reply_still_gives_the_whole_reply(transcript):
    _view, buttons, copied = transcript
    buttons[REPLY_BUTTON].emit("clicked")
    assert copied == [("reply", REPLY)]


def test_a_reply_with_no_command_offers_only_the_reply(transcript, monkeypatch):
    """One button for one thing worth copying; a second that copies the same
    text twice is noise in the transcript."""
    view = TranscriptView()
    view.add_assistant_turn("Just a sentence, with no fence in it.")
    tooltips = {w.get_tooltip_text()
                for w in _walk(view) if isinstance(w, Gtk.Button)}
    assert REPLY_BUTTON in tooltips
    assert CODE_BUTTON not in tooltips


def test_an_unconnected_button_copies_nothing(monkeypatch):
    """The control for the whole file.

    Each interception point is a module-level function a caller can reach, so
    the risk is a button that is never wired to one. Built here by hand, with no
    `connect`, and clicked for real: nothing crosses the boundary, which is what
    a broken button looks like from the user's side.
    """
    copied = []
    monkeypatch.setattr(blocks, "_copy_to_clipboard",
                        lambda widget, text: copied.append(text))
    view = TranscriptView()
    view.add_assistant_turn(REPLY)
    unconnected = Gtk.Button()
    unconnected.set_tooltip_text(CODE_BUTTON)
    unconnected.emit("clicked")
    assert copied == [], "an unconnected button copied something"


def test_a_copy_button_confirms_and_then_reverts(monkeypatch):
    """A copy button that gives no feedback cannot be told from one that failed.

    Recorded across the real handler, because the confirmation is a side effect
    of the same call that copies.
    """
    icons = []

    class _Button:
        def get_clipboard(self):
            class _Clip:
                def set_content(self, provider):
                    icons.append("copied")
            return _Clip()

        def set_icon_name(self, name):
            icons.append(name)

    TranscriptView._on_copy_clicked(_Button(), CODE)
    assert "object-select-symbolic" in icons, "no confirmation that it worked"
    assert icons[0] == "copied", "it confirmed before it copied"