"""The Phone surface: the phone, from this computer.

Calls (a dialer, answer, hang up, recent calls), messages (conversations, a
thread, reply), contacts (search, call, text), the phone's music, and file
transfer. It adds no transport: everything goes through `phone.py` (GSConnect,
KDE Connect, or plain Bluetooth through `phone_bluez`), `skills/bluetooth_call`
and `skills/media_control` - the same code the assistant uses, behind the same
consent keys, so a switch that stops the assistant stops this panel too.

**The watch half moved to `wearables.py` on 2026-10-13, and the title moved with
it.** This panel used to be titled "Watch", with an argument for why: it was
*what a smartwatch does with the phone*, so a sidebar reading "Phone" would send
a newcomer to place a call and hand them wristwatch integration instead. That
argument was sound **for what the panel then held** - and it stopped being true
when the watch half moved out, because a panel of calls, messages, contacts,
music and files is exactly what its title said it was. Leaving "Watch" on a
panel with no watch in it would have been the error the comment was written
against, in the other direction.

What moved and why is written out at the top of `wearables.py`: the GATT reads
were already its subject, and `skills/watch.py`'s MoYoung protocol is a
different one that happens to answer to the same `bluetooth-gatt-enabled` switch.
Splitting one switch's two protocols across two panels meant the only page that
could set `watch-companion-enabled` was not the page about the watch.

**Nothing slow runs on the main thread.** Listing messages over Bluetooth MAP
and pulling contacts over PBAP each take seconds (measured ~24 s for 813
contacts plus 200 messages), so every read and every action runs in a thread
and hands its result back with `GLib.idle_add`.

**Outgoing things happen only on a button the person pressed** - Call, Send -
after they typed the number or the text. That press is the confirmation; the
assistant's own route (`skills/phone.py`) asks separately because there the
model, not the person, chose the words.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from shani_chronoa import markdown_lite  # noqa: E402
from shani_chronoa import phone as ph  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

#: **"Phone", and it says what the panel now holds.** It read "Watch" until
#: 2026-10-13, with a careful argument: the panel was *what a smartwatch does
#: with the phone*, and a title of "Phone" would have sent a newcomer to place a
#: call and handed them wristwatch integration instead. **A title that promises a
#: thing the panel does not do is worse than an unclear one** - that argument was
#: right about the old contents and became wrong the moment the watch half moved
#: to `wearables.py`. Calls, messages, contacts, music and files are what is in
#: here, so "Phone" is now the honest word, and the watch is one panel away
#: under the name that describes it.
TITLE = "Phone"
ICON = "call-start-symbolic"
SECTION = "Phone and remote"
SUBTITLE = ("Calls, messages, contacts, music and files on your phone - through "
            "the phone link, or plain Bluetooth when there is none. Your watch "
            "and other Bluetooth devices are on the Wearables panel.")
#: Reading the phone takes seconds; build this panel when it is opened, not at start.
PREBUILD = False

CONTROL_KEY = "phone-control-enabled"
READ_KEY = "phone-messages-read-enabled"
#: What each switch is called **in Settings**. This panel used to print the key
#: itself - `phone-messages-send-enabled` and `bluetooth-call-enabled` went on
#: screen as sentences - which is the storage name, not the one a person ever
#: sees. These are the labels the rows carry in `settings_window/privacy.py`, so
#: the sentence names the switch the same way Settings does, and can be found by
#: reading either place.
GATE_NAMES = {
    "phone-control-enabled":
        "Let Chronoa use your paired phone",
    "phone-messages-send-enabled":
        "Let Chronoa send texts and place calls from your phone",
    "bluetooth-call-enabled":
        "Let Chronoa call through your paired phone",
}

#: Both keys are written in `settings_window/privacy.py` and nowhere else, so it
#: is the only page on which either answer can change.
GATE_TARGET = "settings:privacy"

SEND_KEY = "phone-messages-send-enabled"
CALL_KEY = "bluetooth-call-enabled"

ROW_CSS = "phone-row"


def _allowed(config: Any, key: str) -> bool:
    """Fails closed: no settings, or settings that cannot answer, is "no"."""
    try:
        return bool(config.get_bool(key, False))
    except Exception:  # noqa: BLE001
        return False


def _in_thread(work: Callable[[], Any], done: Callable[[Any], None]) -> None:
    """Run `work` off the main thread and hand its result (or exception) to `done`."""
    def run():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            logger.warning("phone surface: %s", exc, exc_info=True)
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True).start()


def _tool(name: str, arguments: dict) -> str:
    from shani_chronoa import tools
    return str(tools.execute_tool(name, arguments))


def _when(ms: int) -> str:
    if not ms:
        return ""
    t = ms / 1000
    return time.strftime("%H:%M" if time.time() - t < 86400 else "%d %b %H:%M", time.localtime(t))


def _clear(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def _label(text: str, dim: bool = False, wrap: bool = True) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=0.0, wrap=wrap, selectable=False)
    lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)  # a long number or link must not widen the page
    if dim:
        lab.add_css_class("dim-label")
    return lab


def _human_size(path) -> str:
    """`4.7 MB`, and "unknown size" when `stat` fails.

    A received file's *size* is the second thing anybody asks - the first is its
    name - and a row with a size nobody can read is a number that reads as
    complete.
    """
    try:
        number = float(path.stat().st_size)
    except OSError:
        return "unknown size"
    if number < 1024:
        return f"{int(number)} B"
    for unit in ("kB", "MB", "GB"):
        number /= 1024.0
        if number < 1024:
            return f"{number:.1f} {unit}"
    return f"{number:.1f} TB"


def _button(label: str, on_click: Callable[[], None], suggested: bool = False,
            icon: str = "") -> Gtk.Button:
    btn = Gtk.Button(label=label) if not icon else Gtk.Button(icon_name=icon, tooltip_text=label)
    if suggested:
        btn.add_css_class("suggested-action")
    btn.connect("clicked", lambda _b: on_click())
    btn.set_valign(Gtk.Align.CENTER)
    return btn


def _list() -> Gtk.ListBox:
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class("boxed-list")
    return lb


def _preview(body: str, width: int = 80) -> str:
    """A message body as one readable line, cut on a grapheme boundary.

    **`markdown_lite.clip`, not `body[:80]`.** A message body is sender- or
    model-supplied text, and a hard slice cuts a grapheme cluster — "👨‍👩‍👧‍👦"
    is seven code points held together by ZWJ, and cutting after the second
    leaves a ZWJ with no emoji, which draws the missing-glyph box in a row
    somebody is trying to read. `clip` counts columns, so a CJK-heavy message
    does not overflow the row either.

    A named function rather than an inline expression, because the only way to
    test the inline version was to build the whole panel.
    """
    line = (body or "(no text)").replace("\n", " ")
    return markdown_lite.clip(line, width)


def _item(title: str, subtitle: str = "", suffix: Optional[Gtk.Widget] = None) -> Gtk.Widget:
    # `common.row` escapes the title itself since 2026-10-11; this used to
    # hand-roll it, which is the workaround that made the helper's own contract
    # untrue. Escaping in both places now would double-escape and show `&amp;`.
    row = common.row(title or "(unknown)", subtitle, suffix=suffix)
    row.add_css_class(ROW_CSS)
    return row


class PhonePanel:
    def __init__(self, app: Any):
        self.app = app
        try:
            self.config = getattr(app, "config", None)
        except Exception:  # noqa: BLE001
            self.config = None
        self.device = None
        self.book: "list[tuple[str, tuple[str, ...]]]" = []
        self.status_recorder = common.StatusRecorder()

    # --- shared bits ---------------------------------------------------------

    def ok(self, key: str) -> bool:
        return _allowed(self.config, key)

    def name_for(self, number: str) -> str:
        return ph.name_for(number, self.book) if self.book else ""

    def notice(self, box: Gtk.Box, text: str) -> None:
        _clear(box)
        box.append(_label(text, dim=True))

    # --- the page ------------------------------------------------------------

    def build(self) -> Gtk.Widget:
        page, set_content = common.surface(TITLE, SUBTITLE)
        page.status = self.status_recorder.status
        body = common.page_body(12)
        for side in ("top", "bottom", "start", "end"):
            getattr(body, f"set_margin_{side}")(12)
        # The dot in the sidebar is whatever this row says: drawn through the
        # recorder and redrawn when the phone answers, never set without a row.
        self.status_box = common.page_body(0)
        body.append(self.status_box)
        if not self.ok(CONTROL_KEY):
            # **This branch is the dead end the panel is built around**, and it
            # had the same two defects as the notes below: it printed the
            # storage key `phone-control-enabled` and then told the person to
            # "Turn it on in Settings, under Privacy" with nothing to press.
            # It is also the branch that returns earliest, so on a machine where
            # the gate is shut - which is the default - this panel had *no*
            # interactive widget on it at all. Measured: a walk of the built
            # panel found `[]`.
            self.set_status(common.STATUS_OFF, "Using your phone is turned off",
                            "Nothing was asked of your phone. In Settings, under "
                            f"Privacy: \"{GATE_NAMES[CONTROL_KEY]}\".")
            # The switch by name, turned on here, and the panel rebuilds.
            body.append(common.consent_banner(
                self.app, CONTROL_KEY, "Nothing was asked of your phone.",
                on_change=lambda: common.reload_surface(self.app, "phone")))
            set_content(common.scrolled(body))
            return page
        self.set_status(common.STATUS_UNKNOWN, "Looking for your phone...")

        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        switcher = Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER)
        body.append(switcher)
        body.append(stack)
        for name, title, builder in (("calls", "Calls", self.calls_tab),
                                     ("messages", "Messages", self.messages_tab),
                                     ("contacts", "Contacts", self.contacts_tab),
                                     ("music", "Music", self.music_tab),
                                     ("files", "Files", self.files_tab)):
            stack.add_titled(builder(), name, title)
        set_content(common.scrolled(body))
        self.find_phone()
        return page

    def set_status(self, status: str, summary: str, detail: str = "") -> None:
        _clear(self.status_box)
        self.status_box.append(self.status_recorder.row(status, summary, detail))

    def find_phone(self) -> None:
        def work():
            phones = [d for d in ph.devices() if d.paired]
            dev = next((d for d in phones if d.reachable), phones[0] if phones else None)
            battery = ph.battery(dev) if dev is not None and dev.reachable else None
            return dev, battery, ph.backend()

        def done(result):
            if isinstance(result, Exception):
                self.set_status(common.STATUS_ATTENTION, "No phone", str(result))
                return
            dev, battery, which = result
            self.device = dev
            if dev is None:
                self.set_status(common.STATUS_ATTENTION, "No phone is paired",
                                "Pair it over Bluetooth for calls and texts")
                self.status_box.append(common.connect_button(
                    self.app, "bluetooth", "Pair a phone"))
                return
            how = {"bluetooth": "Bluetooth", "gsconnect": "GSConnect",
                   "kdeconnect": "KDE Connect"}.get(str(which), str(which))
            self.set_status(
                common.STATUS_OK if dev.reachable else common.STATUS_ATTENTION,
                f"{dev.name} - {'connected' if dev.reachable else 'not connected'}",
                f"through {how}" + (f", battery {battery[0]}%" if battery else ""))
            self.load_contacts()
            self.load_history()
            self.load_conversations()
        _in_thread(work, done)

    # --- calls ---------------------------------------------------------------

    #: The dialer's look, installed once per display by `calls_tab`. Here,
    #: not in the window's stylesheet, so the panel looks the same wherever it
    #: is shown (a lone render showed square grey keys without it).
    DIALER_CSS = b"""
/* The Phone panel's dialer, shaped like a car's: one big number,
   round keys a thumb can hit, one green call and one red end. */
.dialer-number {
    font-size: 30px;
    font-weight: 300;
    letter-spacing: 2px;
    background: none;
    border: none;
    box-shadow: none;
    min-height: 48px;
}
.dialer-match { font-size: 14px; }
.dialer-key {
    min-width: 68px;
    min-height: 68px;
    border-radius: 9999px;
    padding: 0;
}
.dialer-key .digit { font-size: 24px; font-weight: 500; }
.dialer-key .letters { font-size: 10px; letter-spacing: 1px; opacity: 0.6; }
.dialer-call, .dialer-end, .dialer-answer {
    min-width: 64px;
    min-height: 64px;
    border-radius: 9999px;
    padding: 0;
    color: white;
}
.dialer-call, .dialer-answer { background: #26a269; }
.dialer-call:hover, .dialer-answer:hover { background: #2ec27e; }
.dialer-end { background: #c01c28; }
.dialer-end:hover { background: #e01b24; }
.dialer-call image, .dialer-end image, .dialer-answer image { -gtk-icon-size: 28px; }
.dialer-incall {
    padding: 12px 18px;
    border-radius: 18px;
}
"""

    #: Letters under each key, as on every phone keypad.
    KEY_LETTERS = {"2": "ABC", "3": "DEF", "4": "GHI", "5": "JKL", "6": "MNO",
                   "7": "PQRS", "8": "TUV", "9": "WXYZ", "0": "+", "1": "", "*": "", "#": ""}

    def calls_tab(self) -> Gtk.Widget:
        """A dialer shaped like a car's: one big number, round keys, one green call.

        **Why this shape.** The old tab was an entry, a grid of 56px square
        buttons and a row of three text buttons ("Answer", "Hang up", "Call
        status") that looked identical whether a call was ringing or not. In a
        car - the other place a computer dials for you - the screen is read at
        a glance and hit with a thumb: the number is the biggest thing on it,
        the keys are round and large, the only colours are green to call and
        red to end, and typing digits shows the contact they match. The call
        controls are one card, shown as a call bar, with the audio route
        beside them where it matters.
        """
        display = Gdk.Display.get_default()
        if display is not None and not getattr(PhonePanel, "_css_installed", False):
            provider = Gtk.CssProvider()
            provider.load_from_data(self.DIALER_CSS, len(self.DIALER_CSS))
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            PhonePanel._css_installed = True
        outer = Adw.Clamp(maximum_size=420)
        box = common.page_body(14)
        outer.set_child(box)

        # The in-call bar: answer / end, and where the audio goes.
        incall = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        incall.add_css_class("card")
        incall.add_css_class("dialer-incall")
        self.answer_button = self._round_button("call-start-symbolic", "Answer",
                                                "dialer-answer", lambda: self.call_action("answer"))
        self.end_button = self._round_button("call-stop-symbolic", "Hang up",
                                             "dialer-end", lambda: self.call_action("hangup"))
        incall.append(self.answer_button)
        incall.append(self.end_button)
        route = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        route.append(_label("Call audio", dim=True, wrap=False))
        toggles = Gtk.Box(spacing=0)
        toggles.add_css_class("linked")
        self.on_pc = Gtk.ToggleButton(label="This PC")
        self.on_phone = Gtk.ToggleButton(label="Phone", group=self.on_pc)
        self.on_pc.connect("toggled", lambda b: b.get_active() and self.route("pc"))
        self.on_phone.connect("toggled", lambda b: b.get_active() and self.route("phone"))
        toggles.append(self.on_pc)
        toggles.append(self.on_phone)
        route.append(toggles)
        incall.append(route)
        status = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Call status")
        status.add_css_class("flat")
        status.add_css_class("circular")
        status.connect("clicked", lambda _b: self.call_action("status"))
        status.update_property([Gtk.AccessibleProperty.LABEL], ["Call status"])
        incall.append(status)
        box.append(incall)

        # The number: as big as the screen allows, with backspace beside it.
        display = Gtk.Box(spacing=6)
        self.number = Gtk.Entry(hexpand=True, placeholder_text="Number to call",
                                input_purpose=Gtk.InputPurpose.PHONE, xalign=0.5)
        self.number.add_css_class("dialer-number")
        self.number.connect("activate", lambda _e: self.call(self.number.get_text()))
        self.number.connect("changed", lambda _e: self._show_match())
        backspace = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text="Delete the last digit")
        backspace.add_css_class("flat")
        backspace.add_css_class("circular")
        backspace.connect("clicked", lambda _b: self.number.set_text(self.number.get_text()[:-1]))
        backspace.update_property([Gtk.AccessibleProperty.LABEL], ["Delete the last digit"])
        spacer = Gtk.Box()
        spacer.set_size_request(34, -1)
        display.append(spacer)
        display.append(self.number)
        display.append(backspace)
        box.append(display)
        self.match = _label("", dim=True, wrap=False)
        self.match.set_xalign(0.5)
        self.match.add_css_class("dialer-match")
        box.append(self.match)

        keys = Gtk.Grid(column_spacing=18, row_spacing=12, halign=Gtk.Align.CENTER)
        for i, k in enumerate("123456789*0#"):
            keys.attach(self._dial_key(k), i % 3, i // 3, 1, 1)
        box.append(keys)

        call = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=4)
        self.call_button = self._round_button("call-start-symbolic", "Call", "dialer-call",
                                              lambda: self.call(self.number.get_text()))
        call.append(self.call_button)
        box.append(call)

        self.call_note = _label("", dim=True)
        self.call_note.set_xalign(0.5)
        self.call_note.set_justify(Gtk.Justification.CENTER)
        self._call_note_row, self._call_note_btn = self._note_row()
        self._call_note_row.set_halign(Gtk.Align.CENTER)
        self._call_note_row.append(self.call_note)
        box.append(self._call_note_row)
        self.devices_note = _label("", dim=True)
        self.devices_note.add_css_class("caption")
        box.append(self.devices_note)
        self.show_route()
        head = _label("Recent calls")
        head.add_css_class("heading")
        box.append(head)
        self.history = common.page_body(0)
        box.append(self.history)
        return outer

    @staticmethod
    def _round_button(icon: str, label: str, css: str, on_click: Callable[[], None]) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon, tooltip_text=label, valign=Gtk.Align.CENTER)
        button.add_css_class(css)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: on_click())
        return button

    def _dial_key(self, key: str) -> Gtk.Button:
        face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, valign=Gtk.Align.CENTER)
        digit = Gtk.Label(label=key)
        digit.add_css_class("digit")
        letters = Gtk.Label(label=self.KEY_LETTERS.get(key, "") or " ")
        letters.add_css_class("letters")
        face.append(digit)
        face.append(letters)
        button = Gtk.Button(child=face)
        button.add_css_class("dialer-key")
        button.update_property([Gtk.AccessibleProperty.LABEL], [key])
        button.connect("clicked", lambda _b: self.number.set_text(self.number.get_text() + key))
        # Hold 0 for "+", as every phone does.
        if key == "0":
            hold = Gtk.GestureLongPress()
            hold.connect("pressed", lambda *_: self.number.set_text(self.number.get_text() + "+"))
            button.add_controller(hold)
        return button

    def _show_match(self) -> None:
        """The contact the typed digits belong to, under the number."""
        typed = "".join(ch for ch in self.number.get_text() if ch.isdigit() or ch == "+")
        name = self.name_for(typed) if len(typed) >= 6 else ""
        self.match.set_text(name)

    def _route_button(self, visible: bool = True) -> Gtk.Button:
        """A wired route to the one page that writes all three gates."""
        button = common.wire_page_button(
            common.open_settings_button("Open Privacy settings", GATE_TARGET),
            self.app, GATE_TARGET)
        button.set_visible(visible)
        return button

    def _note_row(self) -> "tuple[Gtk.Box, Gtk.Button]":
        """A dim note that can also hold the way out of it.

        The note used to be a bare `Gtk.Label` appended straight to the page, so
        a sentence naming a switch had nowhere to put a button for it. The
        button is created once and shown only when the gate is actually off.
        """
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # **"Turn on", for the switch the note names** - set by `_say_gate`,
        # since one note row reports whichever gate the last action hit.
        button = Gtk.Button(label="Turn on")
        button.add_css_class("suggested-action")
        button._gate_key = ""
        button.connect("clicked", self._turn_on_gate)
        button.set_visible(False)
        row.append(button)
        return row, button

    def _turn_on_gate(self, button: Gtk.Button) -> None:
        key = getattr(button, "_gate_key", "")
        config = getattr(self.app, "config", None)
        if not key or config is None:
            return
        config.set(key, "true")
        button.set_visible(False)
        parent = button.get_parent()
        note = parent.get_first_child() if parent is not None else None
        while note is not None and not isinstance(note, Gtk.Label):
            note = note.get_next_sibling()     # the note follows the button
        if note is not None:
            note.set_text(f"\"{GATE_NAMES.get(key, key)}\" is on - try again.")

    def _say_gate(self, note: Gtk.Label, button: Gtk.Button, key: str,
                  what: str) -> None:
        """Say which switch is off, by the name Settings gives it, and offer it.

        **The route appears only while the gate is off**, following
        `gui/surfaces/devices.py`: granted has nothing to fix, so a button would
        open Privacy settings and change nothing - a worse dead end than the
        sentence it replaced.
        """
        name = GATE_NAMES.get(key, key)
        note.set_text(f"{what} turned off (\"{name}\").")
        off = not self.ok(key)
        button._gate_key = key
        button.set_tooltip_text(f"Turn on \"{name}\", the same switch as in Settings")
        button.set_visible(off)

    def call(self, number: str) -> None:
        number = (number or "").strip()
        if not number:
            self.call_note.set_text("Type a number first.")
            return
        if not self.ok(SEND_KEY):
            self._say_gate(self.call_note, self._call_note_btn,
                          SEND_KEY, "Calling is")
            return
        if self.device is None:
            self.call_note.set_text("No phone is connected.")
            return
        self.call_note.set_text(f"Calling {number}...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.call_note.set_text(f"Not called: {result}")
            elif ph.places_calls():
                self.call_note.set_text(f"Calling {number} through {dev.name}. Hang up here or on the phone.")
            else:
                self.call_note.set_text(f"Opened the dialer on {dev.name} with {number} - press call there.")
        _in_thread(lambda: ph.dial(dev, number), done)

    def show_route(self) -> None:
        """Which way the toggle points, and the PC's speaker and mic by name."""
        def work():
            from shani_chronoa.skills import bluetooth_call as bc
            from shani_chronoa import pipewire
            gws = bc.gateways()
            where = bc.audio_route(gws[0][0])[0] if gws else ""
            names = []
            for target in ("@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"):
                out = pipewire.run_wpctl("inspect", target)
                text = getattr(out, "stdout", out) or ""
                hit = [ln.split("=", 1)[1].strip().strip('"') for ln in str(text).splitlines()
                       if "node.description" in ln]
                names.append(hit[0] if hit else "unknown")
            return where, names

        def done(result):
            if isinstance(result, Exception):
                self.devices_note.set_text(f"Call audio route unknown: {result}")
                return
            where, (speaker, mic) = result
            self._routing = True
            (self.on_pc if where == "pc" else self.on_phone).set_active(True)
            self._routing = False
            self.devices_note.set_text(f"On this PC a call uses the speaker '{speaker}' and the "
                                       f"microphone '{mic}' (the system defaults; change them in Sound settings).")
        _in_thread(work, done)

    def route(self, where: str) -> None:
        if getattr(self, "_routing", False):
            return
        if not self.ok(CALL_KEY):
            self._say_gate(self.call_note, self._call_note_btn,
                          CALL_KEY, "Moving call audio is")
            return
        self.call_note.set_text("...")
        _in_thread(lambda: _tool("bluetooth_call", {"action": "audio", "to": where}),
                   lambda r: self.call_note.set_text(str(r).split(" (unverified")[0]))

    def call_action(self, action: str) -> None:
        self.call_note.set_text("...")
        _in_thread(lambda: _tool("bluetooth_call", {"action": action}),
                   lambda r: self.call_note.set_text(str(r)))

    def load_history(self) -> None:
        if self.device is None or ph.backend() != "bluetooth":
            self.notice(self.history, "Recent calls are read over Bluetooth only.")
            return
        if not self.ok(READ_KEY):
            self.notice(self.history, f"Reading call history is off ('{READ_KEY}').")
            return
        self.notice(self.history, "Reading recent calls...")
        from shani_chronoa import phone_bluez
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.history, f"Could not read recent calls: {result}")
                return
            _clear(self.history)
            lb = _list()
            for c in result[:30]:
                who = c["name"] or self.name_for(c["number"]) or c["number"]
                lb.append(_item(who, f"{c['kind'] or 'call'} - {_when(c['date'])}",
                                _button("Call", lambda n=c["number"]: self.call(n), icon="call-start-symbolic")))
            self.history.append(lb if result else _label("No recent calls.", dim=True))
        _in_thread(lambda: phone_bluez.call_history(dev.id, 30), done)

    # --- messages -------------------------------------------------------------

    def messages_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        self.threads = common.page_body(0)
        box.append(self.threads)
        self.thread_title = _label("")
        box.append(self.thread_title)
        self.thread_view = common.page_body(4)
        box.append(self.thread_view)
        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.to = Gtk.Entry(placeholder_text="To (number)", width_chars=14)
        self.text = Gtk.Entry(hexpand=True, placeholder_text="Message")
        self.text.connect("activate", lambda _e: self.send())
        compose.append(self.to)
        compose.append(self.text)
        compose.append(_button("Send", self.send, suggested=True))
        box.append(compose)
        self.send_note = _label("", dim=True)
        self._send_note_row, self._send_note_btn = self._note_row()
        self._send_note_row.append(self.send_note)
        box.append(self._send_note_row)
        return box

    def load_conversations(self) -> None:
        if not self.ok(READ_KEY):
            self.notice(self.threads, f"Reading messages is off ('{READ_KEY}' in Settings, Privacy).")
            return
        self.notice(self.threads, "Reading messages...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.threads, f"Could not read messages: {result}")
                return
            _clear(self.threads)
            lb = _list()
            for m in result[:40]:
                who = ", ".join(self.name_for(a) or a for a in m.addresses) or "(unknown)"
                preview = _preview(m.body)
                lb.append(_item(who, f"{_when(m.date)}{'' if m.read else ' - unread'}: {preview}",
                                _button("Open", lambda m=m, who=who: self.open_thread(m, who))))
            self.threads.append(lb if result else _label("No messages.", dim=True))
        _in_thread(lambda: ph.conversations(dev), done)

    def open_thread(self, latest, who: str) -> None:
        self.thread_title.set_text(f"With {who}")
        self.to.set_text(latest.addresses[0] if latest.addresses else "")
        self.notice(self.thread_view, "Reading the conversation...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.thread_view, f"Could not read it: {result}")
                return
            _clear(self.thread_view)
            for m in result:
                lab = _label(f"{'Them' if m.incoming else 'You'} ({_when(m.date)}): {m.body or '(no text)'}")
                lab.set_halign(Gtk.Align.START if m.incoming else Gtk.Align.END)
                self.thread_view.append(lab)
        _in_thread(lambda: ph.thread_messages(dev, latest.thread, 20), done)

    def send(self) -> None:
        number, text = self.to.get_text().strip(), self.text.get_text().strip()
        if not number or not text:
            self.send_note.set_text("Type a number and a message.")
            return
        if not self.ok(SEND_KEY):
            self._say_gate(self.send_note, self._send_note_btn,
                          SEND_KEY, "Sending is")
            return
        if self.device is None:
            self.send_note.set_text("No phone is connected.")
            return
        self.send_note.set_text("Sending...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.send_note.set_text(f"Not sent: {result}")
            else:
                self.text.set_text("")
                self.send_note.set_text(f"Handed to {dev.name} to send to {number}. "
                                        "The phone does not report delivery back.")
        _in_thread(lambda: ph.send_sms(dev, (number,), text), done)

    # --- contacts ---------------------------------------------------------------

    def contacts_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        self.search = Gtk.SearchEntry(placeholder_text="Search contacts")
        self.search.connect("search-changed", lambda _e: self.show_contacts())
        box.append(self.search)
        self.contact_list = common.page_body(0)
        box.append(self.contact_list)
        return box

    def load_contacts(self) -> None:
        if not self.ok(READ_KEY):
            self.notice(self.contact_list, f"Reading contacts is off ('{READ_KEY}').")
            return
        self.notice(self.contact_list, "Reading contacts...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.contact_list, f"Could not read contacts: {result}")
                return
            self.book = sorted(result, key=lambda c: c[0].lower())
            self.show_contacts()
        _in_thread(lambda: ph.contacts(dev), done)

    def show_contacts(self) -> None:
        if not self.book:
            return
        q = self.search.get_text().strip().lower()
        hits = [c for c in self.book if q in c[0].lower() or any(q in n for n in c[1])] if q else self.book
        _clear(self.contact_list)
        lb = _list()
        for name, numbers in hits[:60]:
            suffix = Gtk.Box(spacing=4)
            suffix.append(_button("Call", lambda n=numbers[0]: self.call(n), icon="call-start-symbolic"))
            suffix.append(_button("Text", lambda n=numbers[0]: self.to.set_text(n), icon="mail-message-new-symbolic"))
            lb.append(_item(name, ", ".join(numbers), suffix))
        self.contact_list.append(lb)
        if len(hits) > 60:
            self.contact_list.append(_label(f"{len(hits) - 60} more - search to narrow.", dim=True))

    # --- music ------------------------------------------------------------------

    def music_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        self.now = _label("Press Refresh to see what is playing.", dim=True)
        box.append(self.now)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        for label, icon, action in (("Previous", "media-skip-backward-symbolic", "previous"),
                                    ("Play or pause", "media-playback-start-symbolic", "toggle"),
                                    ("Next", "media-skip-forward-symbolic", "next"),
                                    ("Refresh", "view-refresh-symbolic", "status")):
            row.append(_button(label, lambda a=action: self.music(a), icon=icon))
        box.append(row)
        box.append(_label("Phone music appears through bluez's mpris-proxy, which must be "
                          "running (systemctl --user start mpris-proxy).", dim=True))
        return box

    def music(self, action: str) -> None:
        def work():
            if ph.backend() == "bluetooth":
                from shani_chronoa import phone_bluez
                phone_bluez.ensure_music_bridge()
            return _tool("media_control", {"action": action})
        _in_thread(work,
                   lambda r: self.now.set_text(str(r).split(" (unverified")[0]))

    # --- files ------------------------------------------------------------------

    def files_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        box.append(_button("Send a file to the phone...", self.pick_file, suggested=True))
        self.file_note = _label("The phone asks you to accept each file.", dim=True)
        box.append(self.file_note)
        box.append(_label("Files from the phone: share them to this computer over Bluetooth "
                          "(Share > Bluetooth on the phone). Chronoa asks before saving each one, "
                          "into your Downloads folder.", dim=True))
        # **What actually arrived.** The sentence above has been here since the
        # receiver landed, and it was the whole of it: a person who shared
        # something to this computer had no way to see what had been saved or
        # where, so the only confirmation was a notification they might have
        # missed. `obex_receive` writes into Downloads by name and never
        # overwrites, so the folder *is* the record - it just was not shown.
        self.received = common.page_body(0)
        box.append(self.received)
        box.append(_label("", dim=True))            # the note, filled below
        self.received_note = box.get_last_child()
        _in_thread(self._received_work, self._received_done)
        return box

    def _received_work(self):
        """What has arrived over Bluetooth, newest first.

        `Downloads/` is the whole list on purpose: `obex_receive.deliver()` moves
        a completed file there and `free_path` never lets two share a name, so
        the folder is the store. Keeping a second index of it is how a shelf
        comes to disagree with the files it is describing.

        Sorted by `(mtime, name)` - the name as tiebreaker, because two files
        this machine wrote in the same instant are exactly what happens when a
        test seeds them, and what happens whenever a receiver moves two files
        quickly. Directory order is not a claim about anything, and the row
        above it says "newest first".
        """
        import pathlib
        from shani_chronoa import obex_receive
        folder = pathlib.Path(obex_receive.downloads())
        try:
            found = sorted((p for p in folder.iterdir() if p.is_file()),
                           key=lambda p: (p.stat().st_mtime, p.name),
                           reverse=True)
        except OSError as exc:
            raise RuntimeError(f"could not read {folder} ({exc})") from exc
        return folder, found[:40]

    def _received_done(self, result):
        if isinstance(result, Exception):
            self.received_note.set_text(f"Could not read Downloads: {result}")
            return
        folder, found = result
        _clear(self.received)
        if not found:
            self.notice(self.received, "Nothing has arrived yet.")
            return
        self.received_note.set_text(f"{len(found)} file(s) in {folder}, newest first.")
        for path in found:
            self.received.append(common.row(path.name, _human_size(path)))

    def pick_file(self) -> None:
        dialog = Gtk.FileDialog(title="Send to your phone", modal=True)
        root = self.file_note.get_root()

        def chosen(dlg, result):
            try:
                f = dlg.open_finish(result)
            except GLib.Error as exc:
                if exc.code != Gtk.DialogError.DISMISSED:   # cancelled is silence; anything else is said
                    self.file_note.set_text(f"The file dialog did not open: {exc.message}")
                return
            if f is not None and f.get_path():
                self.send_file(f.get_path())
        dialog.open(root if isinstance(root, Gtk.Window) else None, None, chosen)

    def send_file(self, path: str) -> None:
        if self.device is None:
            self.file_note.set_text("No phone is connected.")
            return
        import os
        name = os.path.basename(path)
        self.file_note.set_text(f"Sending {name} - accept it on the phone...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.file_note.set_text(f"Not sent: {result}")
                return
            ok, why = result
            self.file_note.set_text(f"Sent {name} to {dev.name}." if ok else f"Not sent: {why}")
        _in_thread(lambda: ph.act(dev, "share", path), done)


def build(app: Any) -> Gtk.Widget:
    return PhonePanel(app).build()
