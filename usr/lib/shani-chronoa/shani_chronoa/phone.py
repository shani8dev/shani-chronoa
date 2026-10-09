"""The user's phone, through the desktop's own phone link.

- GNOME: GSConnect (the image's gnome-shell extension). Devices are read from
  its D-Bus ObjectManager; ring / ping / share go through its daemon's own
  command line (`daemon.js --ring -d ID`, as GSConnect documents).
- Plasma: KDE Connect (`kdeconnect-cli`), battery through its D-Bus module.

Text messages (read and send) and opening the phone's dialer are here too,
each behind its own consent key in the skill (`skills/phone.py`), and sending or
dialing only after the person has confirmed the exact recipient and text. What
is still deliberately not here: reading the phone's notifications and remote
input.

The interfaces used, and where each was read (2026-10-08, upstream master):

- KDE Connect, `org.kde.kdeconnect.device.conversations` on the device object
  `/modules/kdeconnect/devices/<id>` - `activeConversations() -> av`,
  `requestAllConversationThreads()`, `requestConversation(x, i, i)` (messages
  arrive as `conversationUpdated(v)` signals, then `conversationLoaded(x, t)`),
  `replyToConversation(x, s, av)`, `sendWithoutConversation(av, s, av)`.
  Source: kdeconnect-kde `plugins/sms/conversationsdbusinterface.h`. Each
  message is the struct `(isa(s)xiixixa(xsss))` = event, body, addresses, date
  (ms), type (1 inbox, 2 sent), read, threadID, uID, subID, attachments, from
  `models/conversationmessage.h` (`operator<<(QDBusArgument&, ConversationMessage)`).
  An address in `sendWithoutConversation`'s list is a variant holding `(s)`
  (`ConversationAddress`, same file; `SmsPlugin::sendSms` qdbus_casts it).
- KDE Connect contacts: vCards synced to `$XDG_DATA_HOME/kpeoplevcard/kdeconnect-<id>/*.vcf`
  (kdeconnect-kde `plugins/contacts/contactsplugin.h`, `vcardsLocation`).
- KDE Connect dialing: there is no dial method. `org.kde.kdeconnect.device.share`
  `shareUrl(s)` at `/modules/kdeconnect/devices/<id>/share` (`plugins/share/shareplugin.h`)
  sends the URL to the phone, and kdeconnect-android's `SharePlugin.receiveUrl`
  opens it with `Intent.ACTION_VIEW` - for a `tel:` URL that is the dialer with
  the number filled in, and the person presses call on the phone.
- GSConnect: the device's GActions are exported as `org.gtk.Actions` on the
  device object path (`service/manager.js`, `export_action_group(objectPath, device)`).
  `sendSms` takes `(ss)` (number, text) and `shareUri` takes `s`
  (`service/plugins/sms.js`, `service/plugins/share.js`). GSConnect's `sendMessage`
  only ever uses `addresses[0]`, so a group reply is refused rather than sent
  to one member. Message threads are **not** on D-Bus: the sms plugin caches
  them in `$XDG_CACHE_HOME/gsconnect/<id>/sms.json` (`_threads`), written by
  `Plugin.destroy()` (`service/plugin.js`, `cacheProperties`) - so on GNOME the
  messages are as of GSConnect's last save, and the answer says how old that is.
  Contacts: `$XDG_CACHE_HOME/gsconnect/<id>/contacts.json` (`service/components/contacts.js`).

No service, no paired device and no reachable device are three different
answers, and each is reported as itself.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from typing import NamedTuple, Optional

GSCONNECT_BUS = "org.gnome.Shell.Extensions.GSConnect"
GSCONNECT_PATH = "/org/gnome/Shell/Extensions/GSConnect"
_TIMEOUT = 15


class Device(NamedTuple):
    id: str
    name: str
    reachable: bool
    paired: bool
    #: The GSConnect device object path ("" for KDE Connect, whose path is built from the id).
    path: str = ""


class PhoneUnavailable(Exception):
    """No phone link service is running - which is not "no phone"."""


def _gsconnect_daemon() -> Optional[str]:
    hits = sorted(glob.glob("/usr/share/gnome-shell/extensions/gsconnect@*/service/daemon.js")
                  + glob.glob(os.path.expanduser("~/.local/share/gnome-shell/extensions/gsconnect@*/service/daemon.js")))
    return hits[0] if hits and shutil.which("gjs") else None


def _bus():
    """The session bus. One function, so a test can point it at a private bus."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def backend() -> Optional[str]:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    kde = shutil.which("kdeconnect-cli") is not None
    gs = _gsconnect_daemon() is not None
    if "kde" in desktop or "plasma" in desktop:
        return "kdeconnect" if kde else ("gsconnect" if gs else _bluetooth())
    return "gsconnect" if gs else ("kdeconnect" if kde else _bluetooth())


def _bluetooth() -> Optional[str]:
    """The no-app route (`phone_bluez`), when bluez has a paired phone at all."""
    from shani_chronoa import phone_bluez
    return "bluetooth" if phone_bluez.phones() else None


def places_calls() -> bool:
    """Whether `dial` places the call (Bluetooth HFP) or only opens the dialer."""
    return backend() == "bluetooth"


def _run(argv: "list[str]") -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)


def devices() -> "list[Device]":
    which = backend()
    if which is None:
        raise PhoneUnavailable("no phone link is installed (GSConnect on GNOME, KDE Connect on Plasma) "
                               "and no phone is paired over Bluetooth")
    if which == "bluetooth":
        from shani_chronoa import phone_bluez
        return [Device(a, n, up, True) for a, n, up in phone_bluez.phones()]
    if which == "kdeconnect":
        everyone = _run(["kdeconnect-cli", "-l", "--id-name-only"])
        if everyone.returncode != 0:
            raise PhoneUnavailable(f"KDE Connect did not answer ({(everyone.stderr or '').strip()[:120]})")
        reachable = _run(["kdeconnect-cli", "-a", "--id-only"])
        up = set((reachable.stdout or "").split())
        out = []
        for line in (everyone.stdout or "").splitlines():
            ident, _, name = line.strip().partition(" ")
            if ident:
                out.append(Device(ident, name.strip() or ident, ident in up, True))
        return out
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        reply = _bus().call_sync(
            GSCONNECT_BUS, GSCONNECT_PATH, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            None, None, Gio.DBusCallFlags.NONE, _TIMEOUT * 1000, None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error: the extension is off, or no session
        raise PhoneUnavailable(f"GSConnect is not running ({str(exc)[:120]}); it is a GNOME Shell extension "
                               "that has to be switched on") from exc
    out = []
    for _path, ifaces in reply.unpack()[0].items():
        d = ifaces.get("org.gnome.Shell.Extensions.GSConnect.Device")
        if d:
            out.append(Device(str(d.get("Id", "")), str(d.get("Name", "")), bool(d.get("Connected")),
                              bool(d.get("Paired")), str(_path)))
    return out


def battery(device: Device) -> "tuple[int, bool] | None":
    """(percent, charging) where the link reports it; None where it does not.

    Over Bluetooth the phone reports a percent and nothing about charging, so
    `charging` is False there - "not reported as charging", never a claim.
    """
    if backend() == "bluetooth":
        from shani_chronoa import phone_bluez
        pct = phone_bluez.battery(device.id)
        return None if pct is None else (pct, False)
    if backend() != "kdeconnect" or shutil.which("busctl") is None:
        return None
    base = ["busctl", "--user", "get-property", "org.kde.kdeconnect",
            f"/modules/kdeconnect/devices/{device.id}/battery", "org.kde.kdeconnect.device.battery"]
    charge, charging = _run(base + ["charge"]), _run(base + ["isCharging"])
    m = re.search(r"-?\d+", charge.stdout or "")
    if charge.returncode != 0 or not m or int(m.group(0)) < 0:
        return None
    return int(m.group(0)), "true" in (charging.stdout or "")


def act(device: Device, action: str, payload: str = "") -> "tuple[bool, str]":
    """ring, ping or share (a path or URL) to one paired, reachable device."""
    which = backend()
    if which == "bluetooth":
        from shani_chronoa import phone_bluez
        if action != "share" or re.match(r"^https?://", payload):
            return False, ("over plain Bluetooth a phone cannot be made to ring, pinged, or sent a "
                           "link - those need GSConnect or KDE Connect and its app on the phone. "
                           "Files can be sent")
        try:
            phone_bluez.send_file(device.id, payload)
        except phone_bluez.BluetoothPhoneError as exc:
            return False, str(exc)
        return True, ""
    if which == "kdeconnect":
        argv = {"ring": ["kdeconnect-cli", "-d", device.id, "--ring"],
                "ping": ["kdeconnect-cli", "-d", device.id, "--ping-msg", payload or "Ping from Chronoa"],
                "share": ["kdeconnect-cli", "-d", device.id, "--share", payload]}[action]
    else:
        daemon = _gsconnect_daemon()
        flag = "--share-link" if action == "share" and re.match(r"^https?://", payload) else "--share-file"
        argv = {"ring": ["gjs", "-m", daemon, "-d", device.id, "--ring"],
                "ping": ["gjs", "-m", daemon, "-d", device.id, "--ping"],
                "share": ["gjs", "-m", daemon, "-d", device.id, flag, payload]}[action]
    proc = _run(argv)
    return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()[:200]


# --- Text messages, contacts and the dialer --------------------------------

KDECONNECT_BUS = "org.kde.kdeconnect"
KDE_CONVERSATIONS = "org.kde.kdeconnect.device.conversations"
KDE_SHARE = "org.kde.kdeconnect.device.share"
GTK_ACTIONS = "org.gtk.Actions"
#: How long to wait for the phone to answer a request for its threads/messages.
#: KDE Connect fills its cache asynchronously, so an empty first answer is
#: "not fetched yet", not "no messages".
FETCH_SECONDS = 6.0
#: Android's MessageBox values, as both links carry them (1 inbox, 2 sent).
_TYPE_INBOX = 1


class PhoneError(Exception):
    """The link is there but the request failed - reported as itself."""


class Message(NamedTuple):
    thread: str
    addresses: "tuple[str, ...]"
    body: str
    date: int          # milliseconds since the epoch, as Android reports it
    incoming: bool
    read: bool


#: Bidirectional overrides and isolates, plus C0/C1 controls other than tab and
#: line breaks. A message or contact name containing them can make a link
#: *display* as somewhere it does not go (U+202E turns "gpj.exe" into
#: "exe.jpg"). Rule taken from Maze Connect's PROTOCOL.md (`shareText`).
_UNSAFE = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean_text(text: str) -> str:
    """Text from the phone with direction overrides and control characters removed."""
    return _UNSAFE.sub("", str(text or ""))


def _clean(m: "Message") -> "Message":
    return m._replace(body=clean_text(m.body), addresses=tuple(clean_text(a) for a in m.addresses))


def _kde_path(device: Device) -> str:
    return f"/modules/kdeconnect/devices/{device.id}"


def _call(bus_name: str, path: str, iface: str, method: str, params=None, reply: "str | None" = None):
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    try:
        out = _bus().call_sync(bus_name, path, iface, method, params,
                               GLib.VariantType.new(reply) if reply else None,
                               Gio.DBusCallFlags.NONE, _TIMEOUT * 1000, None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error: the plugin is off, the device is gone
        raise PhoneError(f"{method} failed: {str(exc)[:160]}") from exc
    return out.unpack() if out is not None else ()


def _kde_message(value) -> "Message | None":
    """One `(isa(s)xiixixa(xsss))` struct; tolerant of a shorter or odd tuple."""
    try:
        event, body, addrs, date, mtype, read, thread = value[:7]
    except (TypeError, ValueError):
        return None
    addresses = tuple(str(a[0] if isinstance(a, (tuple, list)) else a) for a in (addrs or ()))
    return Message(str(thread), addresses, str(body or ""), int(date or 0), int(mtype) == _TYPE_INBOX, bool(read))


def _gs_message(d: dict) -> "Message | None":
    if not isinstance(d, dict):
        return None
    addresses = tuple(str(a.get("address", "")) for a in (d.get("addresses") or []) if isinstance(a, dict))
    return Message(str(d.get("thread_id", "")), addresses, str(d.get("body") or ""), int(d.get("date") or 0),
                   int(d.get("type") or 0) == _TYPE_INBOX, bool(d.get("read")))


def _cache_home() -> str:
    configured = os.environ.get("XDG_CACHE_HOME", "")
    return configured if configured and os.path.isabs(configured) else os.path.expanduser("~/.cache")


def _gs_threads(device: Device) -> "tuple[dict, float | None]":
    """GSConnect's cached threads and the cache's mtime (None if there is none)."""
    import json
    path = os.path.join(_cache_home(), "gsconnect", device.id, "sms.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        mtime = os.path.getmtime(path)
    except (OSError, ValueError):
        return {}, None
    threads = data.get("_threads") if isinstance(data, dict) else None
    return (threads if isinstance(threads, dict) else {}), mtime


def gsconnect_cache_age(device: Device) -> "float | None":
    """Seconds since GSConnect last saved its messages, or None if it never has."""
    import time
    _threads, mtime = _gs_threads(device)
    return None if mtime is None else max(0.0, time.time() - mtime)


def _bt_messages(device: Device) -> "list[Message]":
    from shani_chronoa import phone_bluez
    try:
        rows = phone_bluez.message_rows(device.id)
    except phone_bluez.BluetoothPhoneError as exc:
        raise PhoneError(str(exc)) from exc
    return [Message(r["thread"], (r["address"],), r["body"], r["date"], r["incoming"], r["read"])
            for r in rows]


def _conversations(device: Device) -> "list[Message]":
    """The latest message of every thread, newest first."""
    if backend() == "bluetooth":
        latest: "dict[str, Message]" = {}
        for m in _bt_messages(device):
            if m.thread not in latest or m.date > latest[m.thread].date:
                latest[m.thread] = m
        return sorted(latest.values(), key=lambda m: m.date, reverse=True)
    if backend() == "kdeconnect":
        import time
        path = _kde_path(device)
        got = _call(KDECONNECT_BUS, path, KDE_CONVERSATIONS, "activeConversations", None, "(av)")
        latest = list(got[0]) if got else []
        if not latest:
            # The daemon's cache is empty until something asks the phone.
            _call(KDECONNECT_BUS, path, KDE_CONVERSATIONS, "requestAllConversationThreads")
            deadline = time.monotonic() + FETCH_SECONDS
            while not latest and time.monotonic() < deadline:
                time.sleep(0.25)
                got = _call(KDECONNECT_BUS, path, KDE_CONVERSATIONS, "activeConversations", None, "(av)")
                latest = list(got[0]) if got else []
        out = [m for m in (_kde_message(v) for v in latest) if m is not None]
    else:
        threads, _mtime = _gs_threads(device)
        out = []
        for msgs in threads.values():
            parsed = [m for m in (_gs_message(d) for d in (msgs or [])) if m is not None]
            if parsed:
                out.append(max(parsed, key=lambda m: m.date))
    return sorted(out, key=lambda m: m.date, reverse=True)


def _thread_messages(device: Device, thread: str, limit: int = 10) -> "list[Message]":
    """Up to `limit` most recent messages of one thread, oldest first."""
    if backend() == "bluetooth":
        return sorted((m for m in _bt_messages(device) if m.thread == str(thread)),
                      key=lambda m: m.date)[-limit:]
    if backend() != "kdeconnect":
        threads, _mtime = _gs_threads(device)
        parsed = [m for m in (_gs_message(d) for d in (threads.get(str(thread)) or [])) if m is not None]
        return sorted(parsed, key=lambda m: m.date)[-limit:]
    import time
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    bus = _bus()
    path = _kde_path(device)
    got: dict = {}
    state = {"loaded": False, "last": time.monotonic()}

    def on_signal(_conn, _sender, _path, _iface, signal, params):
        if signal == "conversationUpdated":
            m = _kde_message(params.unpack()[0])
            if m is not None and m.thread == str(thread):
                got[(m.date, m.body)] = m
                state["last"] = time.monotonic()
        elif signal == "conversationLoaded" and str(params.unpack()[0]) == str(thread):
            state["loaded"] = True

    ctx = GLib.MainContext.new()
    ctx.push_thread_default()
    sub = bus.signal_subscribe(None, KDE_CONVERSATIONS, None, path, None,
                               Gio.DBusSignalFlags.NONE, on_signal)
    try:
        _call(KDECONNECT_BUS, path, KDE_CONVERSATIONS, "requestConversation",
              GLib.Variant("(xii)", (int(thread), 0, max(1, int(limit)))))
        deadline = time.monotonic() + FETCH_SECONDS
        while time.monotonic() < deadline:
            while ctx.iteration(False):
                pass
            quiet = time.monotonic() - state["last"] > 0.5
            if (state["loaded"] or got) and quiet:
                break
            time.sleep(0.05)
    finally:
        bus.signal_unsubscribe(sub)
        ctx.pop_thread_default()
    msgs = sorted(got.values(), key=lambda m: m.date)
    if not msgs:
        # The cache still has the thread's latest message; better than nothing.
        msgs = [m for m in _conversations(device) if m.thread == str(thread)]
    return msgs[-limit:]


def _contacts(device: Device) -> "list[tuple[str, tuple[str, ...]]]":
    """(name, numbers) for every contact the link has synced from the phone."""
    import json
    out: "list[tuple[str, tuple[str, ...]]]" = []
    if backend() == "bluetooth":
        from shani_chronoa import phone_bluez
        try:
            return phone_bluez.contacts(device.id)
        except phone_bluez.BluetoothPhoneError as exc:
            raise PhoneError(str(exc)) from exc
    if backend() == "kdeconnect":
        from shani_chronoa import files
        folder = files.data_home() / "kpeoplevcard" / f"kdeconnect-{device.id}"
        for card in sorted(glob.glob(str(folder / "*.vcf")) + glob.glob(str(folder / "*.vcard"))):
            try:
                text = open(card, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            text = re.sub(r"\r?\n[ \t]", "", text)        # unfold continuation lines (RFC 6350 3.2)
            name, numbers = "", []
            for line in text.splitlines():
                key, _, value = line.partition(":")
                field = key.split(";")[0].split(".")[-1].upper()
                if field == "FN":
                    name = value.strip()
                elif field == "TEL" and value.strip():
                    numbers.append(re.sub(r"^tel:", "", value.strip(), flags=re.I))
            if name and numbers:
                out.append((name, tuple(numbers)))
        return out
    path = os.path.join(_cache_home(), "gsconnect", device.id, "contacts.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out
    for c in (data.values() if isinstance(data, dict) else []):
        if not isinstance(c, dict):
            continue
        numbers = tuple(str(n.get("value", "")) for n in (c.get("numbers") or []) if isinstance(n, dict) and n.get("value"))
        if c.get("name") and numbers:
            out.append((str(c["name"]), numbers))
    return out


def digits(number: str) -> str:
    return re.sub(r"\D", "", number or "")


def same_number(a: str, b: str) -> bool:
    """Equal phone numbers, tolerating a country code on one side only."""
    da, db = digits(a), digits(b)
    if not da or not db:
        return (a or "").strip().lower() == (b or "").strip().lower()
    n = min(len(da), len(db), 9)
    return da == db if n < 7 else da[-n:] == db[-n:]


def name_for(address: str, book: "list[tuple[str, tuple[str, ...]]]") -> str:
    for name, numbers in book:
        if any(same_number(address, n) for n in numbers):
            return name
    return ""


def _activate(device: Device, action: str, parameter) -> None:
    from gi.repository import GLib
    if not device.path:
        raise PhoneError("GSConnect did not report this device's object path")
    _call(GSCONNECT_BUS, device.path, GTK_ACTIONS, "Activate",
          GLib.Variant("(sava{sv})", (action, [parameter], {})))


def send_sms(device: Device, addresses: "tuple[str, ...]", text: str, thread: str = "") -> None:
    """Hand one text message to the phone. Raises PhoneError when the link refuses.

    Neither link reports delivery back over D-Bus, so success here means "the
    phone link accepted it", and the caller says exactly that.
    """
    from gi.repository import GLib
    if not text.strip():
        raise PhoneError("there is no message text")
    if not addresses and not thread:
        raise PhoneError("there is no recipient")
    if backend() == "bluetooth":
        from shani_chronoa import phone_bluez
        if len(addresses) != 1:
            raise PhoneError("over Bluetooth a text goes to one number at a time")
        try:
            phone_bluez.send_sms(device.id, addresses[0], text)
        except phone_bluez.BluetoothPhoneError as exc:
            raise PhoneError(str(exc)) from exc
        return
    if backend() == "kdeconnect":
        path = _kde_path(device)
        if thread:
            _call(KDECONNECT_BUS, path, KDE_CONVERSATIONS, "replyToConversation",
                  GLib.Variant("(xsav)", (int(thread), text, [])))
        else:
            _call(KDECONNECT_BUS, path, KDE_CONVERSATIONS, "sendWithoutConversation",
                  GLib.Variant("(avsav)", ([GLib.Variant("(s)", (a,)) for a in addresses], text, [])))
        return
    if len(addresses) != 1:
        raise PhoneError("GSConnect can only send to a single number (its sendMessage uses only the first "
                         "address), so a group reply is not sent from here")
    _activate(device, "sendSms", GLib.Variant("(ss)", (addresses[0], text)))


def dial(device: Device, number: str) -> str:
    """Open the phone's dialer with `number` filled in; returns the tel: URI sent.

    Neither link can place a call from the desktop: there is no dial method in
    KDE Connect's or GSConnect's D-Bus API. Sharing a `tel:` URI is the one
    route both have, and Android opens it with ACTION_VIEW, which is the dialer
    with the number entered - the person presses call on the phone.
    """
    from gi.repository import GLib
    plain = re.sub(r"[\s().-]", "", number or "")
    if not re.fullmatch(r"\+?\d{3,}", plain):
        raise PhoneError(f"{number!r} is not a phone number")
    uri = f"tel:{plain}"
    if backend() == "bluetooth":
        from shani_chronoa import phone_bluez
        try:
            phone_bluez.dial(device.id, plain)
        except phone_bluez.BluetoothPhoneError as exc:
            raise PhoneError(str(exc)) from exc
        return uri
    if backend() == "kdeconnect":
        _call(KDECONNECT_BUS, _kde_path(device) + "/share", KDE_SHARE, "shareUrl", GLib.Variant("(s)", (uri,)))
    else:
        _activate(device, "shareUri", GLib.Variant("s", uri))
    return uri


def conversations(device: Device) -> "list[Message]":
    """The latest message of every thread, newest first - screened (`clean_text`)."""
    return [_clean(m) for m in _conversations(device)]


def thread_messages(device: Device, thread: str, limit: int = 10) -> "list[Message]":
    """Up to `limit` most recent messages of one thread, oldest first - screened."""
    return [_clean(m) for m in _thread_messages(device, thread, limit)]


def contacts(device: Device) -> "list[tuple[str, tuple[str, ...]]]":
    """(name, numbers) for every contact - names and numbers screened."""
    return [(clean_text(n), tuple(clean_text(x) for x in nums)) for n, nums in _contacts(device)]
