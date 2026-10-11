"""What's running: everything Chronoa started, and the button to stops it.

The request behind this panel, in a person's words: *"it needs a force stop
button - manually blocking those processes for beginners could be a headache,
otherwise they face heavy resource usage."*

**That was not a feature request, it was a measured gap.** `app/application.py`
starts six background watchers unconditionally at startup - the incoming-call
watcher, the message watcher, the file-sharing receiver, the watch companion, the
phone-keyboard service and the Bluetooth music remote. All six are written with a
`stop()`. **A scan of the whole tree found that stop called by nothing, and a grep
over `gui/` and `app/tray.py` found none of the six mentioned anywhere on
screen.** So the capability existed, was tested in isolation, and was unreachable
- the dead-code class this repo records five times over, in the form that hurts:
a machine holding a microphone, a Bluetooth link or a gigabyte of RAM with
quitting the app as the only lever.

It is not theoretical either. Measured on this machine on 2026-10-11: a
`llama-server` up **7h36m holding 1.09 GB**, and two `whisper-cli` runs up **44 and
2 minutes on 3-4 second WAVs holding 4.7 GB each** - **9 GB recovered by killing
four PIDs**, with 9 GB available before and 18 after. Nothing on screen had said
any of it was running, and nothing could have turned it off.

Three rules this page is held to, each because a version without it lied:

1. **A stop that does not exist says so.** A row with a dead Stop button is the
   dead-control class; where there is genuinely no stop, the row names it and
   says what it is holding.
2. **Nothing here claims a stop it did not observe.** Every `stop()` returns a
   sentence and a row only reads "stopped" when the component itself confirms it.
   `presence.py`'s Settings control already does this - it re-reads
   `presence.detect()` after applying and reports "Could not do that: <reason>"
   when the state did not move.
3. **Stopping runs off the main loop.** `local_llm.stop_service()` is a
   `systemctl` call and `sense_scheduler.stop()` joins a thread; either on the
   GTK thread is the freeze `Diagnostics` was fixed for.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import background, common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "What's running"
ICON = "applications-system"
SECTION = "Acting"
SUBTITLE = ("Everything Chronoa started, and the button that stops each one. "
            "Nothing here runs unless you turned it on.")

_STOP_ALL_HEADING = "Stop everything?"
_STOP_ALL_BODY = ("Takes back your microphone, the local model server, the phone "
                  "and watch connections and the sense pollers. You can turn any "
                  "of them on again afterwards.")


def _labels(widget: Gtk.Widget) -> List[Gtk.Label]:
    found: List[Gtk.Label] = []

    def walk(node: Any) -> None:
        child = node.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                found.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


class _Page:
    """The panel and the three things a caller can do to it.

    A class rather than a closure pile because the group has to be rebuilt from
    the live inventory on `showing` - a wake word can start listening while the
    app is open, and a list that was true when the panel was built is a lie about
    the machine two minutes later.
    """

    def __init__(self, app: Any):
        self.app = app
        self.page, self._set_content = common.surface(TITLE, SUBTITLE)
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.root.set_margin_top(12)
        self.root.set_margin_bottom(12)
        self.root.set_margin_start(12)
        self.root.set_margin_end(12)
        self._note = Gtk.Label(label="")
        self._note.set_xalign(0.0)
        self._note.set_halign(Gtk.Align.START)
        self._note.add_css_class("dim-label")
        common.wrap_label(self._note)
        self._build()

    # --- building -----------------------------------------------------------

    def _build(self) -> None:
        child = self.root.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.root.remove(child)
            child = following

        found = background.inventory(self.app)
        stoppable = found.stoppable()

        if not found.items:
            self.root.append(common.empty_state(
                "application-x-executable-symbolic", "Nothing is running",
                "Nothing Chronoa started is running right now."))
        else:
            if stoppable:
                self.root.append(self._stop_all_button())
            self.root.append(self._group(found, stoppable))
        self._set_content(self.root)

    def _stop_all_button(self) -> Gtk.Widget:
        button = Gtk.Button(label="Stop everything")
        button.add_css_class("destructive-action")
        button.set_tooltip_text(
            "Stop every background thing Chronoa can stop, in the safe order: "
            "the microphone first, the model server second.")
        button.connect("clicked", self._ask_then_stop_all)
        return button

    def _group(self, found: "background.Inventory",
              stoppable: List[Any]) -> Gtk.Widget:
        group = common.group("Running now", _group_text(found, stoppable))
        for item in found.items:
            group.add(self._row(item))
        return group

    def _row(self, item: background.Running) -> Gtk.Widget:
        """One running thing: what it holds, and either a Stop or the reason."""
        state = "Running" if item.running else "Stopped"
        button: Optional[Gtk.Button] = None
        if item.stop is not None:
            button = Gtk.Button(label="Stop" if item.running else "Stopped")
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text(
                f"Stop {item.label.lower()}" if item.running else "Already stopped")
            button.set_sensitive(item.running)
            button.connect("clicked", lambda _b, i=item, b=button:
                           self._stop_one(i, b))
        row = common.row(item.label,
                         subtitle=f"{item.holds} - {item.detail}. {state}.",
                         suffix=button)
        if button is None:
            # Name it in the row, not only in a tooltip: the reason a control is
            # missing is more useful than the absence of one.
            question = (item.why_not
                        or "this one cannot be stopped without quitting Chronoa")
            for label in _labels(row):
                if item.label in (label.get_text() or ""):
                    label.set_tooltip_text(question)
                    break
        return row

    # --- stopping -----------------------------------------------------------

    def _stop_one(self, item: background.Running, button: Gtk.Button) -> None:
        """Stop one thing, off the main loop, reporting what actually happened.

        The button is disabled for the duration because a stop that is a
        `systemctl` call or a bounded thread join cannot be answered instantly,
        and a re-press during one would race the first.
        """
        button.set_sensitive(False)
        button.set_label("Stopping...")

        def work() -> None:
            sentence = item.request_stop()

            def done() -> bool:
                self._say(sentence)
                self._build()
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True,
                         name=f"stop-{item.key}").start()

    def _ask_then_stop_all(self, button: Gtk.Button) -> None:
        """The confirmation, because this control has no single obvious effect.

        Cancel is both the default and the close response - a dialog whose default
        button is the destructive one is one a person dismisses with Return.
        """
        if not common.adw_ready():
            self._stop_all()
            return
        dialog = Adw.AlertDialog(heading=_STOP_ALL_HEADING, body=_STOP_ALL_BODY)
        dialog.add_response("cancel", "Leave it running")
        dialog.add_response("stop", "Stop everything")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def responded(_dialog: Any, response: str) -> None:
            if response == "stop":
                self._stop_all()

        dialog.connect("response", responded)
        dialog.present(self.page.get_root() or self.page)

    def _stop_all(self) -> None:
        found = background.inventory(self.app)

        def work() -> None:
            said, refused = found.force_stop_all()

            def done() -> bool:
                self._say_all(said, refused)
                self._build()
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True,
                         name="stop-all-background").start()

    # --- reporting ----------------------------------------------------------

    def _say(self, sentence: str) -> None:
        logger.info("stopped a background thing: %s", sentence)
        self._note.set_text(sentence)

    def _say_all(self, said: List[str], refused: List[Any]) -> None:
        """**The negative half is the point.** A button that stops eight of ten
        things and says "stopped" has reported a success that is partial, which
        is the ECC #2463 failure this file records - so the two that fought back
        are named in the same sentence.
        """
        parts = []
        if said:
            parts.append("Stopped " + ", ".join(
                each.split(":")[0] for each in said) + ".")
        if refused:
            parts.append("Could not stop " + ", ".join(
                item.label for item in refused)
                + " - those have no way to stop them from here.")
        if not parts:
            parts.append("Nothing was running.")
        sentence = " ".join(parts)
        logger.info("%s", sentence)
        self._note.set_text(sentence)

    # --- reloading ----------------------------------------------------------

    def rebuild(self) -> None:
        """Read the live inventory again."""
        self._build()


def _group_text(found: "background.Inventory", stoppable: List[Any]) -> str:
    if not found.items:
        return "Nothing is running."
    if not stoppable:
        return "Everything running has no way to stop it from here."
    return (f"{len(stoppable)} of {len(found.items)} can be stopped from here. "
            f"The rest say why not.")


def build(app: Any) -> Gtk.Widget:
    """The panel. `Adw.NavigationPage` pushes it, and `showing` re-reads."""
    page = _Page(app)
    widget = page.page
    if hasattr(widget, "connect"):
        widget.connect("showing", lambda _w: page.rebuild())
    return widget
