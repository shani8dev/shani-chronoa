"""Connect a device: Bluetooth, an Android phone over adb, a KDE Connect phone.

**Why one window.** Every device feature here - the Phone panel, Wearables, the
Android panel, the phone bridge - started from "a device that is already
connected", and the way to get one connected was somewhere else: GNOME
Settings for Bluetooth, a terminal for `adb pair`, and nothing at all for KDE
Connect pairing, which was written but unreachable. This is that step, shaped
like Setup: one question per page, the next action pinned at the bottom, and a
last page that says what the device is now good for and where to use it.

The three backends are separate modules with their own fakes in `tools/`:
`bt_pair` (BlueZ D-Bus, our own agent), `adb_pair` (adb by USB or wireless
debugging), `lan_link` (KDE Connect pairing). This file only asks and shows.

**Nothing slow on the main loop.** Bluetooth is asynchronous D-Bus on this
context; adb and the LAN link are blocking calls, run on threads that hand
results back with `GLib.idle_add`. Each route's loops carry a token: leaving the
page or closing the window bumps it, and a stale result is dropped rather than
painted onto a page that has moved on.
"""
from __future__ import annotations

import logging
import socket
import threading
from types import SimpleNamespace
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

logger = logging.getLogger(__name__)

ROUTES = ("bluetooth", "android", "kdeconnect")

#: Icon per `bt_pair.kind_of`, all in Adwaita.
KIND_ICON = {"audio": "audio-headphones-symbolic", "phone": "phone-symbolic",
             "input": "input-keyboard-symbolic", "computer": "computer-symbolic",
             "watch": "alarm-symbolic", "other": "bluetooth-symbolic"}
KIND_WORDS = {"audio": "Headphones or speaker", "phone": "Phone", "input": "Keyboard, mouse or controller",
              "computer": "Computer", "watch": "Watch or fitness band", "other": "Bluetooth device"}

_CSS = b"""
.connect-code { font-size: 40px; font-weight: 800; letter-spacing: 6px;
                font-family: monospace; }
.connect-hero { opacity: 0.9; }
.connect-route row { min-height: 72px; }
.connect-route row image.route-icon { -gtk-icon-size: 32px; }
"""


def _css() -> None:
    display = Gdk.Display.get_default()
    if display is None or getattr(_css, "done", False):
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS, len(_CSS))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css.done = True


def _label(text: str, *classes: str, wrap: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=wrap, justify=Gtk.Justification.CENTER,
                      max_width_chars=44)
    for css in classes:
        label.add_css_class(css)
    return label


def _button(label: str, on_click: Callable, style: str = "") -> Gtk.Button:
    button = Gtk.Button(label=label)
    button.add_css_class("pill")
    if style:
        button.add_css_class(style)
    button.connect("clicked", lambda *_: on_click())
    return button


def _spinner() -> Gtk.Widget:
    spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
    spinner.set_size_request(32, 32)
    return spinner


def _off_thread(work: Callable, done: Callable) -> None:
    def run() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown as a sentence
            logger.info("connect-device worker failed", exc_info=True)
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True, name="chronoa-connect").start()


class ConnectDeviceWindow(Adw.Window):
    """The "Connect a device" window. `route` opens straight on one of `ROUTES`."""

    def __init__(self, application=None, route: str = "", *,
                 bt_address: Optional[str] = None, config=None,
                 transient_for: Optional[Gtk.Window] = None) -> None:
        super().__init__(title="Connect a device", default_width=480, default_height=640)
        if isinstance(application, Gtk.Application):
            self.set_application(application)
        _css()
        if transient_for is not None:
            self.set_transient_for(transient_for)
        self.app = application
        self.config = config if config is not None else getattr(application, "config", None)
        self._bt_address = bt_address
        self._token = 0
        self._pairing = None
        self._bt_target = None
        self._pages: "dict[str, SimpleNamespace]" = {}
        self.view = Adw.NavigationView()
        self.view.connect("popped", self._on_popped)
        self.set_content(self.view)
        self.connect("close-request", self._on_close)
        self.view.add(self._start_page().page)
        if route in ROUTES:
            self.go(route)

    # -- page plumbing ------------------------------------------------------
    def _page(self, tag: str, title: str, icon: str, heading: str, body: str = "") -> SimpleNamespace:
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=12, margin_bottom=24, margin_start=18, margin_end=18)
        hero = Gtk.Image(icon_name=icon, pixel_size=64)
        hero.add_css_class("connect-hero")
        head = _label(heading, "title-2")
        text = _label(body, "dim-label")
        text.set_visible(bool(body))
        for widget in (hero, head, text):
            box.append(widget)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(content)
        clamp = Adw.Clamp(maximum_size=520, child=box)
        toolbar.set_content(Gtk.ScrolledWindow(child=clamp, vexpand=True,
                                               hscrollbar_policy=Gtk.PolicyType.NEVER))
        footer = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12,
                         margin_bottom=18, margin_start=18, margin_end=18)
        toolbar.add_bottom_bar(footer)
        page = Adw.NavigationPage(title=title, tag=tag, child=toolbar)
        ns = SimpleNamespace(page=page, content=content, footer=footer,
                             hero=hero, heading=head, body=text)
        self._pages[tag] = ns
        return ns

    @staticmethod
    def _say(ns: SimpleNamespace, heading: str = None, body: str = None, icon: str = None) -> None:
        if heading is not None:
            ns.heading.set_label(heading)
        if body is not None:
            ns.body.set_label(body)
            ns.body.set_visible(bool(body))
        if icon is not None:
            ns.hero.set_from_icon_name(icon)

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            box.remove(child)
            child = following

    def go(self, tag: str) -> None:
        build = {"bluetooth": self._bt_page, "android": self._adb_how_page,
                 "kdeconnect": self._lan_page, "adb-usb": self._adb_usb_page,
                 "adb-wifi": self._adb_wifi_page}[tag]
        self._token += 1
        self.view.push(build().page)

    def _finish(self, ok: bool, heading: str, body: str, *,
                icon: str = "", extra: "list[Gtk.Widget]" = (),
                actions: "list[tuple[str, Callable, str]]" = ()) -> SimpleNamespace:
        """The last page, replacing everything after the start page."""
        self._token += 1
        ns = self._page("done", "Connected" if ok else "Not connected",
                        icon or ("object-select-symbolic" if ok else "dialog-warning-symbolic"),
                        heading, body)
        for widget in extra:
            ns.content.append(widget)
        for label, callback, style in actions:
            ns.footer.append(_button(label, callback, style))
        if ok:
            ns.footer.append(_button("Connect another", self._again))
        ns.footer.append(_button("Done", self.close, "suggested-action" if ok else ""))
        self.view.replace([self._pages["start"].page, ns.page])
        return ns

    def _again(self) -> None:
        self._token += 1
        self.view.replace([self._pages["start"].page])

    def _show_panel(self, name: str) -> None:
        window = getattr(self.app, "window", None)
        show = getattr(window, "_show_surface", None)
        if callable(show):
            show(name)
            window.present()
        self.close()

    def _on_popped(self, _view, page) -> None:
        # Whatever that page was waiting for is moot - except a code page,
        # which is popped by answering it while the pairing it belongs to is
        # still running (bumping here dropped every paired result).
        if page.get_tag() == "bt-code":
            return
        self._token += 1
        if page.get_tag() == "bluetooth" and self._pairing is not None:
            self._pairing.close()
            self._pairing = None

    def _on_close(self, *_a) -> bool:
        self._token += 1
        if self._pairing is not None:
            self._pairing.cancel_pairing()
            self._pairing.close()
            self._pairing = None
        return False

    def _config_on(self, key: str) -> bool:
        try:
            return bool(self.config and self.config.get_bool(key, False))
        except Exception:  # noqa: BLE001
            return False

    def _switch(self, key: str, title: str, subtitle: str,
                on_change: Optional[Callable[[bool], None]] = None) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=self._config_on(key))

        def changed(*_a) -> None:
            if self.config is not None:
                self.config.set(key, "true" if row.get_active() else "false")
            if on_change is not None:
                on_change(row.get_active())
        row.connect("notify::active", changed)
        return row

    @staticmethod
    def _group(*rows: Gtk.Widget, title: str = "") -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title)
        for row in rows:
            group.add(row)
        return group

    # -- start --------------------------------------------------------------
    def _start_page(self) -> SimpleNamespace:
        ns = self._page("start", "Connect a device", "preferences-system-devices-symbolic",
                        "What are you connecting?")
        routes = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        routes.add_css_class("boxed-list")
        routes.add_css_class("connect-route")
        for route, icon, title, subtitle in (
            ("bluetooth", "bluetooth-symbolic", "Bluetooth",
             "Headphones, speakers, keyboards, watches, fitness bands, a phone for calls"),
            ("android", "phone-symbolic", "Android phone",
             "By USB cable or Wi-Fi - files, screenshots, apps"),
            ("kdeconnect", "network-wireless-symbolic", "Phone with KDE Connect",
             "Same Wi-Fi - send files, notifications, ask Chronoa from the phone"),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            image = Gtk.Image(icon_name=icon)
            image.add_css_class("route-icon")
            row.add_prefix(image)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, t=route: self.go(t))
            routes.append(row)
        ns.content.append(routes)
        return ns

    # -- Bluetooth ----------------------------------------------------------
    def _bt_page(self) -> SimpleNamespace:
        from shani_chronoa import bt_pair
        ns = self._page("bluetooth", "Bluetooth", "bluetooth-symbolic",
                        "Put your device in pairing mode",
                        "Usually: hold its power or Bluetooth button until a light "
                        "flashes. It appears below - the closest first.")
        ns.power = Adw.ActionRow(title="Bluetooth is off", visible=False)
        ns.power.add_suffix(_button("Turn on", self._bt_power_on, "suggested-action"))
        ns.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        ns.list.add_css_class("boxed-list")
        ns.looking = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        ns.looking.append(_spinner())
        ns.looking.append(_label("Looking for devices...", "dim-label", wrap=False))
        ns.content.append(self._group(ns.power))
        ns.content.append(ns.looking)
        ns.content.append(ns.list)
        if self._pairing is not None:
            self._pairing.close()
        self._pairing = bt_pair.Pairing(self._bt_address, on_devices=self._bt_refresh_soon,
                                        on_prompt=self._bt_prompt)
        ok, why = self._pairing.open()
        if not ok:
            self._pairing = None
            self._say(ns, "Bluetooth is not available", why[:1].upper() + why[1:] + ".",
                      "bluetooth-disabled-symbolic")
            ns.looking.set_visible(False)
            return ns
        self._bt_start(ns)
        return ns

    def _bt_start(self, ns) -> None:
        powered = self._pairing.powered
        ns.power.set_visible(not powered)
        ns.looking.set_visible(powered)
        if powered:
            self._pairing.start_discovery(
                lambda ok, why: ok or self._say(ns, body=f"Could not search: {why}."))
        self._bt_refresh()

    def _bt_power_on(self) -> None:
        ns = self._pages["bluetooth"]

        def done(ok, why) -> None:
            if ok:
                GLib.timeout_add(400, lambda: (self._pairing and self._bt_start(ns), False)[1])
            else:
                self._say(ns, body=f"Could not turn Bluetooth on: {why}.")
        self._pairing.power_on(done)

    def _bt_refresh_soon(self) -> None:
        if not getattr(self, "_bt_refresh_queued", False):
            self._bt_refresh_queued = True
            GLib.timeout_add(150, lambda: (self._bt_refresh(), False)[1])

    def _bt_refresh(self) -> None:
        self._bt_refresh_queued = False
        ns = self._pages.get("bluetooth")
        if ns is None or self._pairing is None:
            return
        ns.power.set_visible(not self._pairing.powered)
        self._clear(ns.list)
        devices = self._pairing.nearby()
        ns.list.set_visible(bool(devices))
        for device in devices:
            ns.list.append(self._bt_row(device))

    def _bt_row(self, device) -> Adw.ActionRow:
        signal = ("" if device.rssi is None else
                  " · strong signal" if device.rssi > -60 else
                  " · weak signal" if device.rssi < -80 else "")
        row = Adw.ActionRow(title=GLib.markup_escape_text(device.name),
                            subtitle=KIND_WORDS.get(device.kind, "") + signal)
        row.add_prefix(Gtk.Image(icon_name=KIND_ICON.get(device.kind, "bluetooth-symbolic"),
                                 pixel_size=24))
        gatt_first = device.le and device.kind in ("watch", "other")
        button = Gtk.Button(label="Connect" if gatt_first else "Pair",
                            valign=Gtk.Align.CENTER)
        button.add_css_class("suggested-action")
        button.connect("clicked", lambda *_: self._bt_pair(device, connect_only=gatt_first))
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row

    def _bt_pair(self, device, connect_only: bool = False) -> None:
        self._bt_target = device
        ns = self._page("bt-working", device.name, KIND_ICON.get(device.kind, "bluetooth-symbolic"),
                        ("Connecting to " if connect_only else "Pairing with ") + device.name,
                        "Keep it close, and in pairing mode.")
        ns.content.append(_spinner())
        ns.footer.append(_button("Cancel", self._bt_cancel))
        self.view.push(ns.page)
        token = self._token
        done = lambda ok, why: token == self._token and self._bt_done(device, ok, why, connect_only)  # noqa: E731
        (self._pairing.connect_only if connect_only else self._pairing.pair)(device.path, done)

    def _bt_cancel(self) -> None:
        if self._pairing is not None:
            self._pairing.cancel_pairing()
            self._pairing.start_discovery()
        self.view.pop_to_tag("bluetooth")

    def _bt_prompt(self, prompt) -> None:
        """BlueZ asked something: show it as its own page, answer from there."""
        if prompt.kind == "cancel":
            if self.view.find_page("bt-code") is not None:
                self.view.pop_to_tag("bt-working")
            return
        device = prompt.device
        heading, body, icon = {
            "confirm": (f"Does {device} show this code?", "Pair only if both screens show the same number.",
                        "dialog-password-symbolic"),
            "show-passkey": (f"Type this code on {device}", "Then press Enter on it.", "input-keyboard-symbolic"),
            "show-pin": (f"Type this PIN on {device}", "Then press Enter on it.", "input-keyboard-symbolic"),
            "enter-pin": (f"Enter the PIN for {device}", "Car kits and older headsets often use 0000 or 1234 - "
                          "check its manual.", "dialog-password-symbolic"),
            "enter-passkey": (f"Enter the code {device} shows", "", "dialog-password-symbolic"),
            "authorize": (f"Pair with {device}?", "It is asking to pair without a code.", "bluetooth-symbolic"),
        }[prompt.kind]
        ns = self._page("bt-code", "Pairing", icon, heading, body)
        if prompt.code:
            ns.content.append(_label(prompt.code, "connect-code", wrap=False))
        if prompt.kind in ("enter-pin", "enter-passkey"):
            entry = Adw.EntryRow(title="PIN" if prompt.kind == "enter-pin" else "Code")
            if prompt.kind == "enter-passkey":
                entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            ns.content.append(self._group(entry))
            send = lambda: self._bt_answer(prompt, entry.get_text().strip() or None)  # noqa: E731
            entry.connect("entry-activated", lambda *_: send())
            ns.footer.append(_button("Cancel", lambda: self._bt_answer(prompt, None)))
            ns.footer.append(_button("Pair", send, "suggested-action"))
        elif prompt.kind in ("confirm", "authorize"):
            ns.footer.append(_button("No, cancel" if prompt.kind == "confirm" else "Decline",
                                     lambda: self._bt_answer(prompt, None), "destructive-action"))
            ns.footer.append(_button("Yes, pair" if prompt.kind == "confirm" else "Pair",
                                     lambda: self._bt_answer(prompt, True), "suggested-action"))
        else:
            ns.content.append(_spinner())
            ns.footer.append(_button("Cancel", self._bt_cancel))
        self.view.push(ns.page)

    def _bt_answer(self, prompt, value) -> None:
        prompt.answer(value)
        if self.view.find_page("bt-code") is not None and self.view.get_visible_page().get_tag() == "bt-code":
            self.view.pop()

    def _bt_done(self, device, ok: bool, why: str, connect_only: bool) -> None:
        if self._pairing is not None:
            self._pairing.close()
            self._pairing = None
        if not ok:
            actions = [("Try again", lambda: self._again() or self.go("bluetooth"), "suggested-action")]
            if device.le and not connect_only:
                # A GATT wearable that refuses to bond still connects.
                actions.insert(0, ("Connect without pairing",
                                   lambda: self._again() or self._bt_retry_connect(device), ""))
            self._finish(False, f"{device.name} did not {'connect' if connect_only else 'pair'}",
                         why[:1].upper() + why[1:] + ".", actions=actions)
            return
        self._finish(True, f"{device.name} is {'connected' if connect_only else 'paired'}",
                     self._bt_next_words(device, why), icon=KIND_ICON.get(device.kind, "object-select-symbolic"),
                     actions=self._bt_next_actions(device))

    def _bt_retry_connect(self, device) -> None:
        self.go("bluetooth")
        self._bt_pair(device, connect_only=True)

    @staticmethod
    def _bt_next_words(device, why: str) -> str:
        note = "" if why in ("paired and connected", "connected") else f" ({why}.)"
        return {
            "phone": "Chronoa can now place calls and read texts through it. On the phone, allow "
                     "\"Contacts and call history\" and \"Text messages\" for this computer "
                     "(Settings › Bluetooth › this computer).",
            "watch": "Its readings - heart rate, battery, steps - are on the Wearables panel.",
            "audio": "If sound does not switch to it, pick it in the system sound menu.",
            "input": "It is ready to use.",
        }.get(device.kind, "It reconnects by itself from now on.") + note

    def _bt_next_actions(self, device) -> "list":
        return {"phone": [("Open Phone", lambda: self._show_panel("phone"), "")],
                "watch": [("Open Wearables", lambda: self._show_panel("wearables"), "")],
                }.get(device.kind, [("Open Devices", lambda: self._show_panel("devices"), "")]
                      if device.le else [])

    # -- Android over adb ---------------------------------------------------
    def _adb_how_page(self) -> SimpleNamespace:
        from shani_chronoa import adb_pair
        ns = self._page("android", "Android phone", "phone-symbolic",
                        "How will it be connected?",
                        "Either way, first turn on Developer options on the phone: "
                        "Settings › About phone › tap Build number seven times.")
        if not adb_pair.available():
            self._say(ns, "Android tools are not installed",
                      "Connecting an Android phone needs adb, from the android-tools "
                      "package.", "dialog-warning-symbolic")
            return ns
        routes = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        routes.add_css_class("boxed-list")
        routes.add_css_class("connect-route")
        for tag, icon, title, subtitle in (
            ("adb-usb", "media-removable-symbolic", "USB cable", "Any Android version"),
            ("adb-wifi", "network-wireless-symbolic", "Wi-Fi",
             "Android 11 or newer, on the same network - Wireless debugging"),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            image = Gtk.Image(icon_name=icon)
            image.add_css_class("route-icon")
            row.add_prefix(image)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, t=tag: self.go(t))
            routes.append(row)
        ns.content.append(routes)
        return ns

    def _steps(self, *steps: str) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        for number, text in enumerate(steps, 1):
            row = Adw.ActionRow(title=text, title_lines=3)
            badge = _label(str(number), "title-4", wrap=False)
            badge.set_size_request(24, -1)
            row.add_prefix(badge)
            group.add(row)
        return group

    def _adb_usb_page(self) -> SimpleNamespace:
        from shani_chronoa import adb_pair
        ns = self._page("adb-usb", "USB cable", "media-removable-symbolic", "Connect with a USB cable")
        ns.content.append(self._steps(
            "On the phone: Settings › System › Developer options › turn on USB debugging.",
            "Plug the phone into this computer.",
            "Tap Allow on the phone when it asks to allow USB debugging."))
        ns.status = Adw.ActionRow(title="Waiting for the phone...", subtitle="Plug it in")
        ns.status.add_prefix(_spinner())
        ns.content.append(self._group(ns.status))
        token = self._token

        def poll() -> None:
            if token != self._token:
                return
            _off_thread(adb_pair.devices, seen)

        def seen(result) -> None:
            if token != self._token:
                return
            usb = [d for d in (result if isinstance(result, list) else []) if d["via"] == "usb"]
            ready = [d for d in usb if d["state"] == "device"]
            if ready:
                self._adb_done(ready[0])
                return
            if usb:
                ns.status.set_title(adb_pair.STATE_STEP.get(usb[0]["state"], f"The phone is {usb[0]['state']}"))
                ns.status.set_subtitle(usb[0]["model"] or usb[0]["serial"])
            GLib.timeout_add(1500, lambda: (poll(), False)[1])
        poll()
        return ns

    def _adb_wifi_page(self) -> SimpleNamespace:
        from shani_chronoa import adb_pair
        ns = self._page("adb-wifi", "Wi-Fi", "network-wireless-symbolic", "Pair over Wi-Fi",
                        "On the phone: Settings › System › Developer options › "
                        "Wireless debugging - turn it on.")
        token = self._token
        name, password, text = adb_pair.qr_secret()
        png = adb_pair.qr_png(text)
        if png:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_size_request(220, 220)
            picture.set_can_shrink(True)
            picture.set_halign(Gtk.Align.CENTER)
            picture.add_css_class("card")
            ns.content.append(_label("Tap \"Pair device with QR code\" and scan this:", wrap=True))
            ns.content.append(picture)
            ns.qr_status = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
            ns.qr_status.append(_spinner())
            ns.qr_status_label = _label("Waiting for the phone to scan...", "dim-label", wrap=False)
            ns.qr_status.append(ns.qr_status_label)
            ns.content.append(ns.qr_status)
            _off_thread(lambda: adb_pair.wait_for_pairing_service(
                name, timeout=300, cancelled=lambda: token != self._token),
                lambda address: self._adb_scanned(token, address, password))
        ns.address = Adw.EntryRow(title="IP address & port")
        ns.code = Adw.EntryRow(title="Wi-Fi pairing code")
        ns.code.set_input_purpose(Gtk.InputPurpose.DIGITS)
        ns.code.connect("entry-activated", lambda *_: self._adb_pair_code(ns))
        group = self._group(ns.address, ns.code, title="Or with a pairing code" if png else "")
        group.set_description("Tap \"Pair device with pairing code\" and type what it shows.")
        ns.content.append(group)
        ns.footer.append(_button("Pair", lambda: self._adb_pair_code(ns), "suggested-action"))

        def services(result) -> None:
            if token != self._token or not isinstance(result, list):
                return
            pairing = [s for s in result if s["kind"] == "pairing" and not s["name"].startswith("chronoa-")]
            if pairing and not ns.address.get_text():
                ns.address.set_text(pairing[0]["address"])
            GLib.timeout_add(2000, lambda: (token == self._token and _off_thread(
                adb_pair.mdns_services, services), False)[1])
        _off_thread(adb_pair.mdns_services, services)
        return ns

    def _adb_scanned(self, token: int, address, password: str) -> None:
        if token != self._token or not address:
            return
        ns = self._pages["adb-wifi"]
        ns.qr_status_label.set_label("Scanned - pairing...")
        _off_thread(lambda: self._adb_pair_and_connect(address, password, token),
                    lambda result: token == self._token and self._adb_wifi_result(result))

    def _adb_pair_code(self, ns) -> None:
        address, code = ns.address.get_text().strip(), ns.code.get_text().strip()
        token = self._token
        self._say(ns, body="Pairing...")
        _off_thread(lambda: self._adb_pair_and_connect(address, code, token),
                    lambda result: token == self._token and self._adb_wifi_result(result))

    def _adb_pair_and_connect(self, address: str, code: str, token: int):
        from shani_chronoa import adb_pair
        ok, why = adb_pair.pair(address, code)
        if not ok:
            return ("pair", why, None)
        ok, why = adb_pair.connect_after_pairing(address, cancelled=lambda: token != self._token)
        if not ok:
            return ("connect", why, None)
        ready = [d for d in adb_pair.devices() if d["state"] == "device" and d["via"] == "wifi"]
        return ("ok", "", ready[0] if ready else {"model": "", "serial": address})

    def _adb_wifi_result(self, result) -> None:
        if isinstance(result, Exception):
            result = ("pair", str(result), None)
        stage, why, device = result
        if stage == "ok":
            self._adb_done(device)
            return
        ns = self._pages["adb-wifi"]
        self._say(ns, body=("Not paired: " if stage == "pair" else "") + why[:1].upper() + why[1:] + ".")

    def _adb_done(self, device: dict) -> None:
        name = device.get("model") or "The phone"
        gate = self._switch("phone-control-enabled", "Let Chronoa use this phone",
                            "Battery, screenshots, apps and files - the same switch as in Settings")
        self._finish(True, f"{name} is connected",
                     "Over Wi-Fi it reconnects while Wireless debugging is on; by cable, "
                     "whenever it is plugged in.", icon="phone-symbolic",
                     extra=[self._group(gate)],
                     actions=[("Open Android", lambda: self._show_panel("android"), "")])

    # -- KDE Connect --------------------------------------------------------
    def _lan_page(self) -> SimpleNamespace:
        from shani_chronoa import lan_link
        ns = self._page("kdeconnect", "KDE Connect", "network-wireless-symbolic",
                        "Open KDE Connect on your phone",
                        "Both on the same Wi-Fi. KDE Connect is free on F-Droid and the Play Store.")
        paired = lan_link.paired_device()
        if paired:
            row = Adw.ActionRow(title=f"Paired with {GLib.markup_escape_text(paired.get('deviceName') or 'a phone')}",
                                subtitle=f"at {paired.get('address')} - pairing another replaces it")
            row.add_suffix(_button("Forget", lambda: (lan_link.forget_pairing(), self._again(),
                                                       self.go("kdeconnect"))))
            ns.content.append(self._group(row))
        ns.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        ns.list.add_css_class("boxed-list")
        ns.looking = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        ns.looking.append(_spinner())
        ns.looking.append(_label("Looking on this network...", "dim-label", wrap=False))
        consent = self._switch("lan-phone-enabled", "Let Chronoa look for phones on this network",
                               "It announces this computer and listens for phones",
                               on_change=lambda on: on and self._lan_listen(ns))
        if not self._config_on("lan-phone-enabled"):
            ns.content.append(self._group(consent))
        ns.content.append(ns.looking)
        ns.content.append(ns.list)
        ns.found = {}
        ns.looking.set_visible(self._config_on("lan-phone-enabled"))
        if self._config_on("lan-phone-enabled"):
            self._lan_listen(ns)
        return ns

    def _lan_listen(self, ns) -> None:
        from shani_chronoa import lan_link
        ns.looking.set_visible(True)
        token = self._token
        name = socket.gethostname()

        def look():
            lan_link.announce(name)
            return lan_link.listen_once(3.0)

        def found(result) -> None:
            if token != self._token:
                return
            for entry in (result if isinstance(result, list) else []):
                if entry.get("deviceType") in ("phone", "tablet", None, ""):
                    ns.found[entry.get("deviceId")] = entry
            self._clear(ns.list)
            for entry in ns.found.values():
                ns.list.append(self._lan_row(entry))
            ns.list.set_visible(bool(ns.found))
            _off_thread(look, found)
        _off_thread(look, found)

    def _lan_row(self, entry: dict) -> Adw.ActionRow:
        row = Adw.ActionRow(title=GLib.markup_escape_text(entry.get("deviceName") or "A phone"),
                            subtitle=f"{(entry.get('deviceType') or 'device').capitalize()} · {entry.get('address')}")
        row.add_prefix(Gtk.Image(icon_name="phone-symbolic", pixel_size=24))
        button = Gtk.Button(label="Pair", valign=Gtk.Align.CENTER)
        button.add_css_class("suggested-action")
        button.connect("clicked", lambda *_: self._lan_pair(entry))
        row.add_suffix(button)
        return row

    def _lan_pair(self, entry: dict) -> None:
        from shani_chronoa import lan_link
        name = entry.get("deviceName") or "the phone"
        ns = self._page("lan-wait", name, "phone-symbolic", f"Tap Accept on {name}",
                        f"KDE Connect on the phone is asking whether to pair with "
                        f"{socket.gethostname()}.")
        ns.content.append(_spinner())
        ns.footer.append(_button("Cancel", lambda: self.view.pop_to_tag("kdeconnect")))
        self._token += 1
        self.view.push(ns.page)
        token = self._token
        port = int(entry.get("tcpPort") or lan_link.UDP_PORT)
        _off_thread(lambda: lan_link.pair(entry.get("address", ""), timeout=60.0, tcp_port=port),
                    lambda result: token == self._token and self._lan_done(entry, result))

    def _lan_done(self, entry: dict, result) -> None:
        name = entry.get("deviceName") or "The phone"
        if isinstance(result, Exception):
            result = ("failed", str(result))
        state, why = result
        if state != "paired":
            heading = {"waiting": f"Nobody accepted on {name}",
                       "rejected": f"{name} declined"}.get(state, f"Could not pair with {name}")
            self._finish(False, heading, why[:1].upper() + why[1:] + ".",
                         actions=[("Try again", lambda: self._again() or self.go("kdeconnect"),
                                   "suggested-action")])
            return
        bridge = self._switch("lan-bridge-enabled", "Let this phone ask Chronoa",
                              "Text you share from the phone becomes a question; the answer "
                              "comes back as a notification. It asks before doing anything.",
                              on_change=self._set_bridge)
        self._finish(True, f"{name} is paired",
                     "Send it files from Chronoa, and only this phone is accepted from now on.",
                     icon="phone-symbolic", extra=[self._group(bridge)],
                     actions=[("Open Phone link", lambda: self._show_panel("phone_link"), "")])

    def _set_bridge(self, on: bool) -> None:
        from shani_chronoa import lan_bridge
        _off_thread(lambda: lan_bridge.set_enabled(on, self.config), lambda _r: None)
