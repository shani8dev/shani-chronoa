"""Quick Ask: one question, one answer, nothing kept unless asked for.

Alpaca has this and it earns its place for a reason that has nothing to do with
Alpaca: most things a person asks an assistant are not a conversation. "What
time is it in Lisbon", "is my disk full", "how do I spell that" - opening the
window, waiting for the chat to draw, reading the whole transcript afterwards,
and then having the question sit in a saved conversation forever is a lot of
ceremony for an answer that takes four seconds.

So this window does the opposite of the main one, on purpose:

- it does not touch the conversation. Nothing asked here is written to the
  transcript, indexed, or searched. Keeping an answer is an explicit act (the
  Keep checkbox), never a side effect of asking;
- it does not speak. A popup that answers out loud in a room is a popup that
  embarrasses people. This is the one surface with no audio path at all;
- it does not take the microphone. Voice is a main-window action on purpose:
  pushing to talk is a mode, and a mode nobody asked to enter is worse than no
  mode.

The window is a `Gtk.Window` rather than a dialog because it has to survive the
main window being closed and reopened - in background mode the main window is
usually *not* there, and this is what makes the assistant reachable at all.

**`on_ask` is a callback, not a coroutine, on purpose.** Asking is the
application's own assistant, run through its own AsyncBridge on its own thread;
a popup that called `asyncio.run()` in a GTK handler would either block the
window for the length of the answer or introduce a second event loop that ends
up touching the same assistant from two threads. The contract is
`on_ask(text, done)` and the caller decides where the answer comes back.
"""

from __future__ import annotations

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')

from gi.repository import Gtk  # type: ignore

logger = logging.getLogger(__name__)


class QuickAskWindow(Gtk.Window):
    """A single question field and the answer to it.

    `on_ask(text, done)` must call `done(answer)` with a string, or
    `done(exception)`. `on_keep(question, answer)` is called only when the Keep
    box was ticked before asking.
    """

    def __init__(self, application=None, on_ask=None, on_keep=None) -> None:
        super().__init__(application=application)
        self.set_title("Quick Ask")
        self.set_resizable(False)
        self.set_default_size(460, -1)
        self._on_ask = on_ask
        self._on_keep = on_keep
        self._busy = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(14)
        box.set_margin_end(14)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Ask something…")
        # A placeholder is not an accessible name; without this a screen reader
        # announces the field as "text" (shani-testbed a11y-lint, chronoa-voice).
        self._entry.update_property([Gtk.AccessibleProperty.LABEL], ["Question to ask"])
        self._entry.connect("activate", self._on_activate)
        box.append(self._entry)

        self._answer = Gtk.Label()
        self._answer.set_wrap(True)
        self._answer.set_xalign(0.0)
        self._answer.set_selectable(True)
        self._answer.set_visible(False)
        box.append(self._answer)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._keep = Gtk.CheckButton(label="Keep in the conversation")
        self._keep.set_tooltip_text(
            "Off: the question and answer are not saved anywhere. "
            "On: they are added to the open conversation as a normal turn."
        )
        actions.append(self._keep)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        actions.append(spacer)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda _b: self.close())
        actions.append(close)
        self._send_button = Gtk.Button(label="Ask")
        self._send_button.add_css_class("suggested-action")
        self._send_button.connect("clicked", self._on_activate)
        actions.append(self._send_button)
        box.append(actions)

        self.set_child(box)
        self.connect("close-request", self._on_close_request)
        self.connect("notify::visible", self._on_shown)

    def _on_shown(self, _window, _param) -> None:
        """Take the focus when the window appears.

        Quick Ask exists to be typed into, and it is most often raised by a
        shortcut while another window has the focus - so without this the first
        keystroke goes to whatever was behind it.
        """
        if self.get_visible():
            self._entry.grab_focus()

    # -- asking ----------------------------------------------------------

    def _on_activate(self, _widget) -> None:
        """Ask what is in the field. Ignored while an answer is in flight.

        Two questions in flight from one popup means two answers racing for one
        label, and the user reads whichever finished second. The second press is
        refused rather than queued.
        """
        if self._busy or self._on_ask is None:
            return
        text = self._entry.get_text().strip()
        if not text:
            return
        keep = self._keep.get_active()
        self._busy = True
        self._send_button.set_sensitive(False)
        self._answer.set_visible(False)
        try:
            self._on_ask(text, lambda result, keep=keep: self._on_answer(text, result, keep))
        except Exception as exc:                       # noqa: BLE001 - shown in the window
            self._on_answer(text, exc, keep)

    def _on_answer(self, question: str, result, keep: bool) -> None:
        """Show the answer, and keep it only if it was asked to be kept."""
        self._busy = False
        self._send_button.set_sensitive(True)
        if isinstance(result, BaseException):
            # Say what went wrong rather than leaving the previous answer up: an
            # error that leaves the last answer on screen reads as "it answered
            # the same thing again". And it is never kept - "Keep" is consent to
            # save an answer, and an error message is not one.
            answer = f"Could not answer: {result}"
            logger.warning("Quick Ask failed: %s", result)
            self._answer.set_text(answer)
            self._answer.set_visible(True)
            return
        answer = str(result or "")
        self._answer.set_text(answer)
        self._answer.set_visible(True)
        if keep and self._on_keep is not None:
            try:
                self._on_keep(question, answer)
            except Exception:                          # noqa: BLE001 - a kept turn must not kill the answer
                logger.exception("Quick Ask could not keep the turn")

    def ask_now(self, text: str) -> None:
        """Put a question in the field, show the window and ask it.

        For a caller that already has a question in hand and does not want it to
        become a conversation — the answer comes back through `on_ask` and is
        dropped rather than recorded.

        **This docstring used to claim `--ask=` called it, and it does not.**
        `--ask=` goes through the main window's own `submit_text()`, so the
        question and its answer *are* recorded in the transcript, which is the
        right behaviour for a desktop custom shortcut ("say something to
        Chronoa") and is what
        `test_gateway_submitted_text_reaches_the_window_entry` pins. This
        method answers a different question — one that should leave no trace —
        and has no caller in the tree today.
        """
        self._entry.set_text(text)
        self.present()
        self._on_activate(self._entry)

    def _on_close_request(self, _window) -> bool:
        """Close even while an answer is in flight.

        The answer then arrives with nothing to show it, which is why `_busy` is
        not used to refuse the close: a person who closes a popup means it.
        """
        return False