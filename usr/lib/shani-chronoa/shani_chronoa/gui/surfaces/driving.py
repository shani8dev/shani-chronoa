"""Can Chronoa drive an app on this desktop? The surface for `ui_driver`.

**Why a panel.** `ui_driver` is the largest module in the package and the one
whose answer is least knowable from outside it. It drives an app by putting a
real pointer and real keys into it, and *whether that can work here* is four
different facts this repo established by measurement and then kept in prose:
XTEST forwards to the compositor only through the portal on Wayland, Electron
apps publish no accessibility tree unless they are told to, a full-screen
capture is the coordinate space that works, and the cue layer is an ordinary
window a terminal can cover. A person cannot check any of those from outside.

**Every row is a question the module was asked, not a claim it made.**
`knowable()` opens the display and asks its size; `windows()` asks the window
list; the cue layer's socket is pinged. A row that could not be answered says
*which* it could not and stops there, because "the X display would not answer"
and "this has no X display" are different facts and only one of them is fixable.

**The driving itself is not offered.** `click`, `type` and `key` act on whatever
window is in front, and `ui_elements` is the tool that does it - one call per
turn, over the accessibility tree, with the model deciding. This page answers
"can it" and shows what is there. A "click at 50%" button in front of a person
who has not chosen a window is a control that cannot act, which is the
dead-control class this repo keeps meeting.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

from shani_chronoa import ui_driver
from shani_chronoa.gui.surfaces import common

logger = logging.getLogger(__name__)

TITLE = "Driving"
ICON = "input-gaming-symbolic"
SECTION = "Acting"
#: **Built when opened, not at start.** Its portal row asks the RemoteDesktop
#: portal for a real session - which is the point of the row, and also a
#: request for remote control of the desktop. Built in the start-up prebuild
#: pass, that request went out the moment Chronoa launched, and measured in a
#: shani-testbed slot (2026-10-23) the app then stopped answering AT-SPI: every
#: query timed out and the setup wizard's buttons never reached a screen
#: reader. Bisected over all 39 panels to this one, and within it to
#: `ui_driver._portal_input()`. The same arrangement `diagnostics.py` uses.
PREBUILD = False
SUBTITLE = ("What Chronoa can do with a mouse and a keyboard on this desktop, "
            "and where it has to say it cannot.")


class Row:
    """One fact, and whether it is one the module actually answered.

    `UNKNOWN` is a state in its own right, not a fallback: a probe that could
    not be asked must not read as a negative answer, which is the shape this
    repo records for every sense with a binary that can be missing.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"

    def __init__(self, label: str, value: str, kind: str = KNOWN,
                 detail: str = "") -> None:
        self.label, self.value, self.kind = label, value, kind
        self.detail = detail


def _off_thread(work: Callable[[], Any], done: Callable[[Any], None]) -> None:
    """`work()` on a worker, `done(result)` back on the GTK thread.

    Every question here touches a display server, a socket or a subprocess, and
    `find_window` can wait up to thirty seconds for a window that never appears.
    """

    def runner() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - a sentence, not a traceback
            logger.warning("driving probe failed", exc_info=True)
            result = exc
        GLib.idle_add(finish, result)

    def finish(result) -> bool:
        try:
            done(result)
        except Exception:  # noqa: BLE001
            logger.warning("could not show the result", exc_info=True)
        return False

    threading.Thread(target=runner, daemon=True).start()


class _DrivingView(Gtk.Box):
    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._app = app
        self.status_recorder = common.StatusRecorder()
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.append(self._status_slot)
        self._facts_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._facts_slot.set_vexpand(True)
        self.append(self._facts_slot)
        self.append(self._cue_row())
        self._refresh_status()
        self._refresh()

    # -- the one-line answer ------------------------------------------------

    def _refresh_status(self) -> None:
        common.clear(self._status_slot)
        try:
            knowable = ui_driver.knowable()
        except Exception as exc:  # noqa: BLE001 - could not ask is not "no"
            knowable = f"could not ask ({exc})"
        if knowable is True:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK, "An app's position can be known here",
                "so a click can be aimed at a named control, and the pointer "
                "can be read back afterwards"))
        elif knowable is False:
            # **The answer is a sentence; the essay is the tooltip.** The
            # portal sentence explains what to do about it, and it is four
            # lines of it - so it was the body's text, on the panel's own
            # alert row, which is the one place a person reads a page
            # because something is wrong.
            row = self.status_recorder.row(
                common.STATUS_ATTENTION, "No display this driver can measure",
                "this app is a native Wayland window, so nothing on this "
                "machine can inject input into it")
            # **Appended, not replaced**: `common.row()` puts the row's full
            # text in the tooltip for a row that has to wrap it, and that is
            # worth keeping.
            hover = row.get_tooltip_text() or ""
            row.set_tooltip_text(
                (hover + " - " if hover else "") + ui_driver._portal_sentence())
            self._status_slot.append(row)
        else:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_UNKNOWN, "Could not tell whether a position is "
                "knowable", str(knowable)))

    # -- the facts, each asked of the module --------------------------------

    def _refresh(self) -> None:
        group = common.group()
        for row in self._rows():
            group.add(self._row_widget(row))
        _replace(self._facts_slot, common.scrolled(group, 400))

    def _rows(self) -> "List[Row]":
        return [self._knowable_row(), self._portal_row(), self._on_top_row(),
                self._cue_row_fact(), *self._window_rows()]

    def _knowable_row(self) -> Row:
        try:
            knowable = ui_driver.knowable()
        except Exception as exc:  # noqa: BLE001
            return Row("Screen", f"could not be read ({exc})", Row.UNKNOWN)
        if not knowable:
            return Row("Screen", "no display answered", Row.UNKNOWN,
                       "the driver has no X display to measure a window in")
        return Row("Screen", "answered", Row.KNOWN,
                   "an element's position can be read, so a click can be aimed "
                   "and then checked")

    def _on_top_row(self) -> Row:
        """Whether a window is actually the thing on top - the click question.

        **`ui_driver.is_on_top()` had no caller in the tree.** The other rows here
        ask whether a position can be read, whether the portal answers and where
        the windows are; none of them asks the one that decides whether a click
        lands where the reply says it did.

        That is not hypothetical, and the function's own docstring is the record
        of it: `flatpak run com.slack.Slack` is the only working way to raise the
        window, and the moment anything runs from a terminal **the terminal raises
        itself** and covers Slack's left column. Measured by OCR of the region a
        click was about to use, it read `deepu`, `Jisha`, `Nevin`, `Abdul` - the
        agent's own terminal output - and every click at the right pixel for a
        whole afternoon was a click on the terminal.

        **All three answers are said, because there are three.** `False` means the
        window's text is not on screen, so something is over it. `True` means it
        is. `None` is *could not tell* - no capture, no OCR - and
        `is_on_top`'s own docstring is emphatic that it is deliberately not
        `True`: "a check that answers 'yes' when it could not look has no power to
        refuse anything." So it reads as unknown here too, not as a pass.
        """
        # **A real window's title is the probe.** `is_on_top` defaults `probe` to
        # the window's title, and the title is the one thing a cover does not
        # display - a terminal shows titles too, which is the whole reason the
        # function prefers a probe and falls back to the title only when nothing
        # better was given. Asking about a named window rather than "the screen"
        # is what turns "something is over it" into a statement about a window.
        probe = ""
        try:
            found = ui_driver.windows()
            if found:
                first = found[0]
                probe = (first[0] if isinstance(first, (list, tuple)) and first
                         else str(first))
        except Exception:  # noqa: BLE001 - no list is the "could not tell" case
            probe = ""
        try:
            on_top = ui_driver.is_on_top(probe)
        except Exception as exc:  # noqa: BLE001 - an unexpected shape, said plainly
            return Row("Window on top", f"could not be asked ({type(exc).__name__})",
                       Row.UNKNOWN)
        if on_top is None:
            return Row("Window on top", "could not tell", Row.UNKNOWN,
                       "this needs a full-screen capture and OCR, and there was "
                       "no text to read. That is not a pass: a click may still "
                       "land on whatever is over the window.")
        if on_top:
            return Row("Window on top", "on top", Row.KNOWN,
                       "its own text is on screen, so a click aimed inside it "
                       "reaches it")
        return Row("Window on top", "something is over it", Row.UNKNOWN,
                   "the window's text is not on screen - on this desktop the "
                   "usual cover is this agent's own terminal, which raises itself "
                   "the moment anything runs from it. A click aimed inside the "
                   "window would land on the cover instead.")

    def _portal_row(self) -> Row:
        """The RemoteDesktop portal - the route a native Wayland window takes.

        **`_portal_input()` is the question.** It raises `GuiUnavailable` with
        the reason when the portal cannot be reached, and returns a `RemoteInput`
        when it can. Asking anything else - a version string, a socket, the
        presence of a binary - answers a different question: `ConnectToEIS`
        being *introspectable* and the portal *working* are the two things this
        repo has already caught disagreeing once.
        """
        try:
            ui_driver._portal_input()
        except ui_driver.GuiUnavailable as exc:
            return Row("RemoteDesktop portal", f"did not answer: {exc}",
                       Row.UNKNOWN,
                       "a native Wayland window cannot be driven without it")
        except Exception as exc:  # noqa: BLE001 - an unexpected shape, said plainly
            return Row("RemoteDesktop portal",
                       f"could not be asked ({type(exc).__name__})", Row.UNKNOWN)
        return Row("RemoteDesktop portal", "answered", Row.KNOWN,
                   "keystrokes and clicks can reach a native Wayland window, "
                   "after the person allows it once")

    def _cue_row_fact(self) -> Row:
        socket = ui_driver.cue_socket("driving")
        if ui_driver._cue_answers(socket, timeout=1.0):
            return Row("Cue layer", "answering on its socket", Row.KNOWN,
                       "a driven click would be visible before it happened")
        return Row("Cue layer", "not running", Row.KNOWN,
                   "the driving still works; it would just not be seen")

    def _window_rows(self) -> "List[Row]":
        try:
            found = ui_driver.windows()
        except Exception as exc:  # noqa: BLE001
            return [Row("Windows", f"could not be listed ({exc})", Row.UNKNOWN)]
        if not found:
            return [Row("Windows", "none answered", Row.KNOWN,
                        "no app is running that this driver could reach")]
        out = []
        for entry in found[:8]:
            title = (entry[0] if isinstance(entry, (list, tuple)) and entry
                     else str(entry))
            out.append(Row("Window", str(title), Row.KNOWN))
        if len(found) > 8:
            out.append(Row("Windows", f"and {len(found) - 8} more", Row.KNOWN))
        return out

    def _row_widget(self, row: Row) -> Gtk.Widget:
        """One fact row: the fact's name is the title, always.

        **Not `common.status_row()` for the unknown rows**, which is what the
        first version used - and that helper puts the status *word* in the title
        and folds the summary into the subtitle. Right for a panel's own health
        row, where the word is the scan target; wrong here, because a person
        scanning this page is looking for *which* fact, and a column reading
        "Could not determine" four times makes them read the prose to find out.

        So the fact name leads in both states, and `kind` is what keeps the two
        apart visually: the value itself carries the verdict ("answered" against
        "no display answered"), and the detail of an unanswered row is prefixed
        with the word, so a reader scanning the subtitles sees *could not be
        asked* next to the ones that were. **The field was set and never read
        while that branch was missing** - a mutation swapping KNOWN for UNKNOWN
        stayed green against all 17 tests, which is how it was found.
        """
        detail = row.detail
        if row.kind == Row.UNKNOWN:
            detail = "could not be asked - " + detail if detail else \
                "could not be asked"
        return common.row(f"{row.label}: {row.value}", detail)

    # -- the cue layer, started on purpose ----------------------------------

    def _cue_row(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(12 if side in ("start", "end") else 8)
        self.cue_button = Gtk.Button(label="Show the cue")
        self.cue_button.set_tooltip_text(
            "Draw the cursor and the ring a driven click uses, so driving on "
            "this desktop can be seen")
        _label(self.cue_button, "Start the cue layer")
        self.cue_button.connect("clicked", self._on_cue)
        box.append(self.cue_button)
        self.cue_note = _dim("It is an ordinary window: raised, and raised again "
                             "on every cue, but a terminal that asks for the "
                             "front covers it.")
        box.append(self.cue_note)
        return box

    def _on_cue(self, _button: Gtk.Button) -> None:
        self.cue_button.set_sensitive(False)
        self.cue_button.set_label("Starting...")
        _off_thread(lambda: ui_driver.start_cue("driving"), self._cue_started)

    def _cue_started(self, result) -> None:
        self.cue_button.set_sensitive(True)
        self.cue_button.set_label("Show the cue")
        if isinstance(result, Exception):
            self.cue_note.set_label(f"Could not start it: {result}")
            return
        _process, why = result
        self.cue_note.set_label(why or "The cue layer is up.")
        GLib.idle_add(self._refresh)


def _replace(container: Gtk.Widget, widget: Gtk.Widget) -> None:
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        container.remove(child)
        child = following
    container.append(widget)


def _dim(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    label.add_css_class("dim-label")
    common.wrap_label(label, 56)
    return label


def _label(widget: Gtk.Widget, label: str) -> None:
    try:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    except Exception:  # noqa: BLE001
        logger.debug("no accessible label on %r", type(widget).__name__)


def build(app: Any) -> Gtk.Widget:
    view = _DrivingView(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(view)
    for name in ("cue_button", "cue_note", "_refresh", "_refresh_status",
                 "_on_cue"):
        setattr(page, name, getattr(view, name))
    page.status = view.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "SECTION", "SUBTITLE", "build"]