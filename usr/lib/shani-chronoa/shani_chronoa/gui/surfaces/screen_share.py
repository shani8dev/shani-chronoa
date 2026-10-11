"""Screen share: this computer's screen, on a paired phone.

`skills/screen_to_phone.py` shipped the whole capability - the ScreenCast portal
round trip, the VP8 pipeline, the one-viewer server - and reached **no GUI at
all**: the only way to use it was a model deciding to call it. The scan that
found `screen_stream.py` unreachable is the one this repository has recorded
finding five dead modules with, and this is the sixth.

**Every control here goes through `tools.execute_tool`, not a copy.** That is the
same shape the Triggers panel uses, and it is load-bearing rather than tidy: the
gate (`screen-share-enabled`), the consent re-check, the sandbox and the audit
origin are the chat path's, so a panel that called the skill's helpers directly
would run a screen-share outside the switches a person set.

**The probe is slow and the UI says so.** `action=status` runs the launcher's
`--check`, which imports GStreamer and reads the kernel's address table - up to
**90 seconds**. It cannot run on the thread that draws the window, so every action
goes through a worker and reports back on an idle, the shape
`settings_window/privacy.py:_probe_worker` already uses.
"""

from __future__ import annotations

import threading
from typing import Any

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402

TITLE = "Screen share"
ICON = "send-to-symbolic"
SECTION = "Acting"
SUBTITLE = ("This computer's screen, on a paired phone over Bluetooth. Ask "
            "first to see whether this machine can - the check reads "
            "GStreamer and the Bluetooth address, and takes a while.")


class _ShareSurface:
    def __init__(self, app: Any, set_content) -> None:
        self._app = app
        self._body = common.page_body()
        for side in ("start", "end", "top", "bottom"):
            getattr(self._body, f"set_margin_{side}")(12)
        set_content(common.scrolled(self._body))

        self._group = common.group()
        self._body.append(self._group)
        self._row = common.row("Can this machine share?",
                               "", suffix=self._buttons())
        self._group.add(self._row)
        self._body.append(common.group(
            "", "Sharing opens the desktop's own ScreenCast portal, so the "
                "portal's chooser appears and you pick what to share. The "
                "phone sees it over Bluetooth; nothing is sent elsewhere."))
        #: What the last action said, in full - a truncated one-line label is not
        #: a report somebody can act on.
        self._report = common.row("What happened last", "(nothing yet)")
        _report_group = common.group()
        _report_group.add(self._report)
        self._body.append(_report_group)
        self._busy = False

    def _buttons(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._check = Gtk.Button(label="Check")
        self._check.set_tooltip_text("Ask whether this machine can share (takes up to 90 s)")
        self._check.connect("clicked", lambda _b: self._run("status"))

        self._start = Gtk.Button(label="Start")
        self._start.set_tooltip_text("Open the portal and stream to the paired phone")
        self._start.connect("clicked", lambda _b: self._run("start"))

        self._stop = Gtk.Button(label="Stop")
        self._stop.set_tooltip_text("Close the portal session and stop streaming")
        self._stop.connect("clicked", lambda _b: self._run("stop"))

        for widget in (self._check, self._start, self._stop):
            box.append(widget)
        return box

    # -- the controls --------------------------------------------------------

    def _run(self, action: str) -> None:
        """One action through the real dispatch path, off the drawing thread.

        `execute_tool_outcome` is the chat path: the consent gate, the sandbox and
        the audit origin all belong to it. The returned text is the skill's own
        answer, so what the panel shows and what the model is told are the same
        sentence rather than two that can disagree.
        """
        if self._busy:
            return
        self._busy = True
        for widget in (self._check, self._start, self._stop):
            widget.set_sensitive(False)
        self._say(f"{action}… running it")

        def work():
            from shani_chronoa import tools
            try:
                result = tools.execute_tool_outcome(
                    "screen_to_phone", {"action": action},
                    origin=tools.ORIGIN_USER)
                text = (result.text or "").strip() or f"{action} produced no answer"
            except Exception as exc:  # noqa: BLE001 - a worker must still say something
                text = f"{action} failed: {type(exc).__name__}: {exc}"
            GLib.idle_add(lambda: (self._finished(action, text), False)[1])

        threading.Thread(target=work, daemon=True,
                         name="chronoa-screen-share").start()

    def _finished(self, action: str, text: str) -> None:
        self._busy = False
        for widget in (self._check, self._start, self._stop):
            widget.set_sensitive(True)
        self._say(text)

    def _say(self, text: str) -> None:
        """The last answer, in full - and the panel is re-read on every showing.

        A worker's report is the only evidence a person gets from a 90-second
        probe, so a truncated label would hide the part that names the missing
        package.
        """
        self._report.set_subtitle(str(text))


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _ShareSurface(app, set_content)
    page.surface = surface
    page.status = lambda: ""
    return page


__all__ = ["TITLE", "ICON", "SECTION", "build"]
