"""The Phone surface: what a smartwatch does with the phone, from this computer.

Calls (a dialer, answer, hang up, recent calls), messages (conversations, a
thread, reply), contacts (search, call, text), the phone's music, and finding a
watch or band. It adds no transport: everything goes through `phone.py` (GSConnect,
KDE Connect, or plain Bluetooth through `phone_bluez`), `skills/bluetooth_call`,
`skills/media_control` and `find_device` - the same code the assistant uses,
behind the same consent keys, so a switch that stops the assistant stops this
panel too.

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
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from shani_chronoa import phone as ph  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Phone"
ICON = "call-start-symbolic"
SECTION = "Acting"
SUBTITLE = ("Calls, messages, contacts and music on your phone, and finding your watch - "
            "through the phone link, or plain Bluetooth when there is none.")
#: Reading the phone takes seconds; build this panel when it is opened, not at start.
PREBUILD = False

CONTROL_KEY = "phone-control-enabled"
READ_KEY = "phone-messages-read-enabled"
SEND_KEY = "phone-messages-send-enabled"
CALL_KEY = "bluetooth-call-enabled"
GATT_KEY = "bluetooth-gatt-enabled"
COMPANION_KEY = "watch-companion-enabled"

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


def _item(title: str, subtitle: str = "", suffix: Optional[Gtk.Widget] = None) -> Gtk.Widget:
    row = common.row(GLib.markup_escape_text(title or "(unknown)"), subtitle, suffix=suffix)
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
            self.set_status(common.STATUS_OFF, "Using your phone is turned off",
                            f"Nothing was asked of your phone, because '{CONTROL_KEY}' is off. "
                            "Turn it on in Settings, under Privacy.")
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
                                     ("files", "Files", self.files_tab),
                                     ("watch", "Watch", self.find_tab)):
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
                self.set_status(common.STATUS_ATTENTION, "No phone is paired")
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

    def calls_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        dial = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.number = Gtk.Entry(hexpand=True, placeholder_text="Number to call",
                                input_purpose=Gtk.InputPurpose.PHONE)
        self.number.connect("activate", lambda _e: self.call(self.number.get_text()))
        dial.append(self.number)
        dial.append(_button("Call", lambda: self.call(self.number.get_text()), suggested=True))
        box.append(dial)
        keys = Gtk.Grid(column_spacing=6, row_spacing=6, halign=Gtk.Align.CENTER)
        for i, k in enumerate("123456789*0#"):
            b = Gtk.Button(label=k, width_request=56)
            b.connect("clicked", lambda _b, k=k: self.number.set_text(self.number.get_text() + k))
            keys.attach(b, i % 3, i // 3, 1, 1)
        box.append(keys)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)
        actions.append(_button("Answer", lambda: self.call_action("answer")))
        actions.append(_button("Hang up", lambda: self.call_action("hangup")))
        actions.append(_button("Call status", lambda: self.call_action("status")))
        box.append(actions)
        route = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        route.append(_label("Call audio:", wrap=False))
        self.on_pc = Gtk.ToggleButton(label="This PC")
        self.on_phone = Gtk.ToggleButton(label="Phone", group=self.on_pc)
        self.on_pc.connect("toggled", lambda b: b.get_active() and self.route("pc"))
        self.on_phone.connect("toggled", lambda b: b.get_active() and self.route("phone"))
        route.append(self.on_pc)
        route.append(self.on_phone)
        box.append(route)
        self.devices_note = _label("", dim=True)
        box.append(self.devices_note)
        self.call_note = _label("", dim=True)
        box.append(self.call_note)
        self.show_route()
        box.append(_label("Recent calls"))
        self.history = common.page_body(0)
        box.append(self.history)
        return box

    def call(self, number: str) -> None:
        number = (number or "").strip()
        if not number:
            self.call_note.set_text("Type a number first.")
            return
        if not self.ok(SEND_KEY):
            self.call_note.set_text(f"Calling is turned off ('{SEND_KEY}' in Settings, Privacy).")
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
            self.call_note.set_text(f"Moving call audio is off ('{CALL_KEY}' in Settings, Privacy).")
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
        box.append(self.send_note)
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
                preview = (m.body or "(no text)").replace("\n", " ")[:80]
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
            self.send_note.set_text(f"Sending is turned off ('{SEND_KEY}' in Settings, Privacy).")
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
        return box

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

    # --- find a watch -----------------------------------------------------------

    def find_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        self.find_note = _label("", dim=True)
        self.wearables = common.page_body(0)
        box.append(self.wearables)
        box.append(self.find_note)
        if not self.ok(GATT_KEY):
            self.notice(self.wearables, f"Reaching your watch is off ('{GATT_KEY}' in Settings, Privacy).")
            return box
        keep = Gtk.Box(spacing=8)
        keep_switch = Gtk.Switch(active=self.ok(COMPANION_KEY), valign=Gtk.Align.CENTER)
        keep_label = _label("Keep my watch connected to this computer - its find-my-phone, music and "
                            "camera buttons then work here. The Da Fit app on your phone cannot reach "
                            "the watch while this is on.", dim=True)
        keep_label.set_hexpand(True)
        keep.append(keep_label)
        keep.append(keep_switch)

        def toggled(sw, _pspec):
            if getattr(sw, "_reverting", False):
                return
            try:
                self.config.set(COMPANION_KEY, "true" if sw.get_active() else "false")
            except Exception as exc:  # noqa: BLE001 - shown, and the switch put back once
                self.find_note.set_text(f"Could not change it: {exc}")
                sw._reverting = True
                sw.set_active(not sw.get_active())
                sw._reverting = False
        keep_switch.connect("notify::active", toggled)
        box.prepend(keep)

        def work():
            from shani_chronoa.skills import bluetooth_gatt as bg
            from shani_chronoa import phone_bluez
            phones = {a for a, _n, _c in phone_bluez.phones()}
            return [(m, n) for m, n in bg.paired() if bg.looks_like_low_energy(m) and m not in phones]

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.wearables, f"Could not list devices: {result}")
                return
            _clear(self.wearables)
            lb = _list()
            for _mac, name in result:
                note = _label("", dim=True)
                note.set_selectable(True)
                health = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=4,
                                     row_spacing=4, max_children_per_line=6)
                for label, args in (("Activity & sleep", {"action": "summary"}),
                                    ("Heart rate", {"action": "measure", "what": "heart_rate"}),
                                    ("Blood oxygen", {"action": "measure", "what": "blood_oxygen"}),
                                    ("Blood pressure", {"action": "measure", "what": "blood_pressure"})):
                    health.append(_button(label, lambda a=args, n=name, l=note: self.watch(n, a, l)))
                health.append(_button("Battery", lambda n=name, l=note: self.watch_read(n, "battery level", l)))
                buttons = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=4,
                                      row_spacing=4, max_children_per_line=6)
                buttons.append(_button("Settings", lambda n=name, l=note: self.watch(n, {"action": "settings"}, l)))
                buttons.append(_button("Sync time", lambda n=name, l=note: self.watch(n, {"action": "sync_time"}, l)))
                buttons.append(_button("Info", lambda n=name, l=note: self.watch_info(n, l)))
                buttons.append(_button("Find", lambda n=name: self.find(n), suggested=True))
                msg = Gtk.Box(spacing=6)
                text = Gtk.Entry(hexpand=True, placeholder_text="Message to show on the watch")
                msg.append(text)
                msg.append(_button("Send to watch", lambda n=name, t=text, l=note: self.watch(
                    n, {"action": "notify", "sender": "Chronoa", "text": t.get_text()}, l)))
                # Plain labels, not an ActionRow: a list row outside a list
                # raised gtk_list_box_row_grab_focus criticals (measured).
                card = common.page_body(6)
                for side in ("top", "bottom", "start", "end"):
                    getattr(card, f"set_margin_{side}")(12)
                title = _label(name)
                title.add_css_class("heading")
                card.append(title)
                card.append(_label("Bluetooth Low Energy watch or band", dim=True))
                card.append(_label("Health", dim=True))
                card.append(health)
                card.append(_label("Watch", dim=True))
                card.append(buttons)
                card.append(msg)
                card.append(note)
                lb.append(card)
                self.watch_read(name, "battery level", note)
            self.wearables.append(lb if result else _label("No watch or band is paired.", dim=True))
        _in_thread(work, done)
        return box

    def watch_read(self, name: str, what: str, note: Gtk.Label) -> None:
        note.set_text(f"Reading {what}...")
        _in_thread(lambda: _tool("bluetooth_gatt", {"action": "read", "device": name, "characteristic": what}),
                   lambda r: note.set_text(str(r)))

    def watch(self, name: str, arguments: dict, note: Gtk.Label) -> None:
        slow = arguments.get("action") == "measure"
        note.set_text("Measuring - keep the watch on and still, up to a minute..." if slow else "Asking the watch...")
        _in_thread(lambda: _tool("watch", {**arguments, "device": name}),
                   lambda r: note.set_text(str(r).split(" (unverified")[0]))

    def watch_info(self, name: str, note: Gtk.Label) -> None:
        note.set_text("Reading the watch's details...")

        def work():
            parts = []
            for what in ("manufacturer name", "firmware revision", "software revision", "battery level"):
                parts.append(_tool("bluetooth_gatt", {"action": "read", "device": name, "characteristic": what}))
            return "\n".join(parts)
        _in_thread(work, lambda r: note.set_text(str(r)))

    def find(self, name: str) -> None:
        self.find_note.set_text(f"Sending {name} the find signal...")
        _in_thread(lambda: _tool("find_device", {"device": name}),
                   lambda r: self.find_note.set_text(str(r).split(" (unverified")[0]))


def build(app: Any) -> Gtk.Widget:
    return PhonePanel(app).build()
