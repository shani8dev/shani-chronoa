"""The phone over plain Bluetooth: no app on the phone, only its own profiles.

Used by `phone.py` when neither GSConnect nor KDE Connect is present. Every
route here is a standard profile the phone already serves, measured on a real
Android phone (acer ZX) on 2026-10-08:

- **Messages: MAP** through obexd (`org.bluez.obex`, session bus).
  `MessageAccess1.ListMessages` on `inbox`/`sent` gives, per message, the text
  in `Subject` (Android puts the body there, up to ~256 characters), a
  `Timestamp` as `YYYYMMDDTHHMMSS`, `SenderAddress`/`RecipientAddress`, `Read`
  and a numeric `ConversationId`. Sending is `PushMessage` of a bMessage file.
- **Contacts: PBAP.** `PhonebookAccess1.Select("int", "pb")` then `PullAll` to
  a vCard file. 813 contacts and 508 call-history entries on that phone.
- **Calls: HFP** through PipeWire's telephony service (`skills/bluetooth_call`),
  which *places* the call - unlike the app links, which can only open the
  dialer for the person to press call.
- **Files: OBEX Object Push** (`ObjectPush1.SendFile`).
- **Battery:** bluez's `org.bluez.Battery1` on the device, from HFP.

**obexd drops a session the moment the D-Bus client that created it goes
away**, so every function below keeps one connection open for the whole
session. Calling it from `busctl`, one process per call, made every session
vanish between creating it and using it - which reads exactly like the phone
refusing.

Android also has to allow it, per device: Settings > Bluetooth > this computer
> "Contacts and call history" and "Text messages". Without that the phone
answers PBAP with an OBEX error (0x46 measured) and MAP not at all.

Not possible over Bluetooth, and said so rather than faked: ringing the phone
and taking a photo on it. A watch does those through its own app on the phone;
on this side that app is GSConnect / KDE Connect.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from typing import Optional

_OBEX = "org.bluez.obex"
_CLIENT = "org.bluez.obex.Client1"
_MAP = "org.bluez.obex.MessageAccess1"
_PBAP = "org.bluez.obex.PhonebookAccess1"
_OPP = "org.bluez.obex.ObjectPush1"
_TRANSFER = "org.bluez.obex.Transfer1"
_TIMEOUT_MS = 60_000
_TRANSFER_SECONDS = 60
#: How many messages per folder a listing asks the phone for.
LIST_COUNT = 200


class BluetoothPhoneError(Exception):
    """The phone was reachable but the request failed; carries the reason."""


def _run(argv: "list[str]") -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)


def phones() -> "list[tuple[str, str, bool]]":
    """(address, name, connected) for every paired device bluez calls a phone."""
    if not shutil.which("bluetoothctl"):
        return []
    out = []
    for line in (_run(["bluetoothctl", "devices", "Paired"]).stdout or "").splitlines():
        m = re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line.strip())
        if not m:
            continue
        info = _run(["bluetoothctl", "info", m.group(1)]).stdout or ""
        if re.search(r"^\s*Icon:\s*phone\b", info, re.M):
            out.append((m.group(1), m.group(2).strip(),
                        bool(re.search(r"^\s*Connected:\s*yes", info, re.M))))
    return out


def ensure_music_bridge() -> bool:
    """Start bluez's own `mpris-proxy` user unit, which shows a phone's player
    (AVRCP) as an MPRIS player that `media_control` already drives.

    Shipped in bluez-utils on Arch and Ubuntu and enabled, but measured exiting
    (inactive since 18:57 the day it was found), and with it gone the phone's
    music is invisible. Starting an installed, enabled unit of the user's own
    session is the built-in route; nothing is written. True when it is running.
    """
    if not shutil.which("systemctl"):
        return False
    if _run(["systemctl", "--user", "is-active", "--quiet", "mpris-proxy.service"]).returncode == 0:
        return True
    return _run(["systemctl", "--user", "start", "mpris-proxy.service"]).returncode == 0


# --- the phone's music player, straight from bluez (AVRCP) -------------------------

_PLAYER = "org.bluez.MediaPlayer1"
AVRCP_METHODS = {"play": "Play", "pause": "Pause", "next": "Next", "previous": "Previous", "stop": "Stop"}


def avrcp_players() -> "list[tuple[str, str]]":
    """(object path, device name) of every phone media player bluez exposes."""
    if not shutil.which("busctl"):
        return []
    tree = _run(["busctl", "--system", "tree", "org.bluez"]).stdout or ""
    paths = sorted(set(re.findall(r"(/org/bluez/hci\d+/dev_[0-9A-F_]+/(?:avrcp/)?player\d+)\b", tree)))
    out = []
    for path in paths:
        address = re.search(r"dev_([0-9A-F_]+)", path).group(1).replace("_", ":")
        name = next((n for a, n, _c in phones() if a == address), address)
        out.append((path, name))
    return out


def _player_prop(path: str, prop: str) -> str:
    return (_run(["busctl", "--system", "get-property", "org.bluez", path, _PLAYER, prop]).stdout or "").strip()


def avrcp_status(path: str) -> "tuple[str, str]":
    """(Playing/Paused/Stopped, "Title by Artist" or "")."""
    status = _player_prop(path, "Status").split(" ", 1)[-1].strip('"').capitalize() or "Unknown"
    track = _player_prop(path, "Track")
    title = re.search(r'"Title" s "((?:[^"\\]|\\.)*)"', track)
    artist = re.search(r'"Artist" s "((?:[^"\\]|\\.)*)"', track)
    from shani_chronoa.phone import clean_text
    now = clean_text(title.group(1)) if title else ""
    if now and artist and artist.group(1):
        now += f" by {clean_text(artist.group(1))}"
    return status, now


def avrcp(path: str, action: str) -> "tuple[bool, str]":
    """Play, pause, next, previous or stop on the phone's player; toggle reads the state first."""
    if action == "toggle":
        action = "pause" if avrcp_status(path)[0] == "Playing" else "play"
    method = AVRCP_METHODS.get(action)
    if method is None:
        return False, f"the phone's player cannot {action}"
    before = avrcp_status(path)
    r = _run(["busctl", "--system", "call", "org.bluez", path, _PLAYER, method])
    if r.returncode != 0:
        return False, (r.stderr or "").strip()[:160]
    # Accepted is not done: measured, Play was accepted and the state stayed
    # "paused" (no music app active on the phone). Read the state back.
    want = {"play": "Playing", "pause": "Paused", "stop": "Stopped"}.get(action)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        now = avrcp_status(path)
        if (want and now[0] == want) or (not want and now != before):
            return True, ""
        time.sleep(0.25)
    if want:
        return False, (f"the phone accepted it but its player is still {now[0].lower()} - "
                       "open a music app on the phone first")
    return True, "sent; the phone did not report a new track"


def battery(address: str) -> Optional[int]:
    """The phone's battery percent as it reports it over HFP, or None."""
    if not shutil.which("busctl"):
        return None
    path = "/org/bluez/hci0/dev_" + address.replace(":", "_")
    got = _run(["busctl", "--system", "get-property", "org.bluez", path,
                "org.bluez.Battery1", "Percentage"])
    m = re.match(r"y\s+(\d+)", (got.stdout or "").strip())
    return int(m.group(1)) if got.returncode == 0 and m else None


# --- one obexd session, held on one connection ---------------------------------

#: One obexd session at a time. A phone serves one PBAP client at once: the Phone
#: panel loading contacts and call history together got `OBEX Connect failed
#: with 0x53` (busy) on the second and a refusal on the first (measured).
_ONE_AT_A_TIME = threading.Lock()


class _Session:
    """`with _Session(address, "map") as s:` - created and removed on one connection."""

    def __init__(self, address: str, target: str):
        self.address, self.target = address, target
        self.path = ""

    def __enter__(self):
        if not _ONE_AT_A_TIME.acquire(timeout=_TRANSFER_SECONDS * 2):
            raise BluetoothPhoneError("the phone is still busy with another request")
        try:
            return self._open()
        except BaseException:
            _ONE_AT_A_TIME.release()
            raise

    def _open(self):
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self._GLib = GLib
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            # Every transfer's status, seen from before the transfer exists: obexd
            # unexports a finished transfer at once, so polling it afterwards
            # cannot tell "complete" from "the phone refused" (both just vanish).
            self.statuses: "dict[str, str]" = {}
            # Its own context: called from the Phone panel's worker threads, where
            # GTK owns the default one and iterating it here would dispatch nothing.
            self._ctx = GLib.MainContext.new()
            self._ctx.push_thread_default()
            self._sub = self.bus.signal_subscribe(
                _OBEX, "org.freedesktop.DBus.Properties", "PropertiesChanged", None, _TRANSFER, 0,
                lambda _c, _s, path, _i, _n, params: self.statuses.__setitem__(
                    str(path), str(dict(params.unpack()[1]).get("Status") or self.statuses.get(str(path), ""))))
            self.path = self.call("/org/bluez/obex", _CLIENT, "CreateSession", "(sa{sv})",
                                  (self.address, {"Target": GLib.Variant("s", self.target)}))[0]
        except Exception as exc:  # noqa: BLE001 - GLib.Error carries the reason
            if getattr(self, "_sub", None):
                self.bus.signal_unsubscribe(self._sub)
                self._ctx.pop_thread_default()
                self._sub = None
            raise BluetoothPhoneError(_why(exc, self.target)) from exc
        return self

    def call(self, path: str, iface: str, method: str, sig: "str | None" = None, args=()):
        variant = self._GLib.Variant(sig, args) if sig else None
        return self.bus.call_sync(_OBEX, path, iface, method, variant, None, 0,
                                  _TIMEOUT_MS, None).unpack()

    def wait(self, transfer: str) -> None:
        """Until a transfer completes; raises on error, refusal or timeout."""
        deadline = time.monotonic() + _TRANSFER_SECONDS
        while time.monotonic() < deadline:
            while self._ctx.iteration(False):
                pass
            status = self.statuses.get(transfer, "")
            if not status:
                try:
                    status = self.call(transfer, "org.freedesktop.DBus.Properties", "Get", "(ss)",
                                       (_TRANSFER, "Status"))[0]
                except Exception:  # noqa: BLE001 - gone: decided by what the signal said
                    status = self.statuses.get(transfer, "gone")
            if status == "complete":
                return
            if status == "error":
                raise BluetoothPhoneError("the phone refused or stopped the transfer")
            if status == "gone":
                raise BluetoothPhoneError("the transfer ended without saying it completed")
            time.sleep(0.1)
        raise BluetoothPhoneError("the transfer did not finish in time - was it accepted on the phone?")

    def __exit__(self, *exc):
        try:
            if getattr(self, "_sub", None):
                self.bus.signal_unsubscribe(self._sub)
                self._ctx.pop_thread_default()
            if self.path:
                try:
                    self.call("/org/bluez/obex", _CLIENT, "RemoveSession", "(o)", (self.path,))
                except Exception:  # noqa: BLE001 - already gone is fine
                    pass
        finally:
            _ONE_AT_A_TIME.release()
        return False


def _why(exc: Exception, target: str) -> str:
    text = str(exc)
    what = {"map": "Text messages", "pbap": "Contacts and call history"}.get(target, "")
    if "0x53" in text:
        return "the phone is busy with another request - try again in a moment"
    if "0x46" in text or "Forbidden" in text or "Timed out" in text:
        return (f"the phone did not allow it - on the phone, open Bluetooth settings for this "
                f"computer and turn on '{what}'" if what else "the phone did not allow it")
    if "ServiceUnknown" in text or "org.bluez.obex" in text and "not provided" in text:
        return "obexd (bluez's file and message service) is not running"
    return text.split(":")[-1].strip()[:160] or "the phone did not answer"


# --- messages (MAP) --------------------------------------------------------------

def _epoch_ms(stamp: str) -> int:
    try:
        return int(datetime.strptime(stamp[:15], "%Y%m%dT%H%M%S").timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def thread_key(address: str) -> str:
    """One key per correspondent: the last 9 digits of a number, so a number
    with and without its country code is one conversation; a non-number
    (a business sender, an e-mail address) as itself, lowercased."""
    digits = re.sub(r"\D", "", address)
    return digits[-9:] if len(digits) >= 7 else address.strip().lower()


def message_rows(address: str, count: int = LIST_COUNT) -> "list[dict]":
    """Every listed message in inbox and sent, as plain dicts, newest first.

    Each: thread, address, body, date (ms), incoming, read.
    """
    from gi.repository import GLib
    rows = []
    with _Session(address, "map") as s:
        try:
            s.call(s.path, _MAP, "SetFolder", "(s)", ("telecom/msg",))
            for folder, incoming in (("inbox", True), ("sent", False)):
                listed = s.call(s.path, _MAP, "ListMessages", "(sa{sv})",
                                (folder, {"MaxCount": GLib.Variant("q", count)}))[0]
                for f in listed.values():
                    who = f.get("SenderAddress") if incoming else f.get("RecipientAddress")
                    rows.append({
                        # Not ConversationId: measured on the acer ZX, all 225 listed
                        # messages carried the *same* one, so every message became one
                        # thread. The other party is the conversation.
                        "thread": thread_key(str(who or "")),
                        "address": str(who or ""),
                        "body": str(f.get("Subject") or ""),
                        "date": _epoch_ms(str(f.get("Timestamp") or "")),
                        "incoming": incoming,
                        "read": bool(f.get("Read")) if incoming else True,
                    })
        except BluetoothPhoneError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BluetoothPhoneError(_why(exc, "map")) from exc
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def bmessage(number: str, text: str) -> str:
    """A MAP bMessage (MAP 1.0, section 3.1.3) for one SMS to one number.

    LENGTH counts the bytes from BEGIN:MSG through END:MSG, line breaks
    included - a wrong count is the most common reason a phone rejects a push.
    """
    msg = f"BEGIN:MSG\r\n{text}\r\nEND:MSG\r\n"
    return ("BEGIN:BMSG\r\nVERSION:1.0\r\nSTATUS:READ\r\nTYPE:SMS_GSM\r\n"
            "FOLDER:telecom/msg/outbox\r\nBEGIN:BENV\r\n"
            f"BEGIN:VCARD\r\nVERSION:2.1\r\nTEL:{number}\r\nEND:VCARD\r\n"
            "BEGIN:BBODY\r\nCHARSET:UTF-8\r\n"
            f"LENGTH:{len(msg.encode('utf-8'))}\r\n{msg}"
            "END:BBODY\r\nEND:BENV\r\nEND:BMSG\r\n")


def send_sms(address: str, number: str, text: str) -> None:
    """Push one SMS to the phone's outbox, which the phone then sends."""
    from gi.repository import GLib
    fd, path = tempfile.mkstemp(prefix="chronoa-bmsg-", suffix=".bmsg", dir=_private_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(bmessage(number, text))
        with _Session(address, "map") as s:
            try:
                s.call(s.path, _MAP, "SetFolder", "(s)", ("telecom/msg",))
                transfer = s.call(s.path, _MAP, "PushMessage", "(ssa{sv})",
                                  (path, "outbox", {"Charset": GLib.Variant("s", "utf8")}))[0]
                s.wait(transfer)
            except BluetoothPhoneError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BluetoothPhoneError(_why(exc, "map")) from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --- contacts (PBAP) ----------------------------------------------------------------

def _private_dir() -> str:
    """A directory only this user can read, for files holding contacts or messages."""
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    path = os.path.join(base, "chronoa-phone")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def parse_vcards(text: str) -> "list[tuple[str, tuple[str, ...]]]":
    """(name, numbers) for every vCard with both; folding undone (RFC 6350 3.2)."""
    text = re.sub(r"\r?\n[ \t]", "", text)
    out, name, numbers = [], "", []
    for line in text.splitlines():
        key, _, value = line.partition(":")
        field = key.split(";")[0].split(".")[-1].upper()
        if field == "BEGIN":
            name, numbers = "", []
        elif field == "FN":
            name = value.strip()
        elif field == "TEL" and value.strip():
            numbers.append(re.sub(r"^tel:", "", value.strip(), flags=re.I))
        elif field == "END" and value.strip().upper() == "VCARD" and name and numbers:
            out.append((name, tuple(numbers)))
    return out


def contacts(address: str) -> "list[tuple[str, tuple[str, ...]]]":
    """The phone's contacts, pulled over PBAP and never kept on disk afterwards."""
    fd, path = tempfile.mkstemp(prefix="chronoa-pb-", suffix=".vcf", dir=_private_dir())
    os.close(fd)
    try:
        with _Session(address, "pbap") as s:
            try:
                s.call(s.path, _PBAP, "Select", "(ss)", ("int", "pb"))
                transfer = s.call(s.path, _PBAP, "PullAll", "(sa{sv})", (path, {}))[0]
                s.wait(transfer)
            except BluetoothPhoneError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BluetoothPhoneError(_why(exc, "pbap")) from exc
        with open(path, encoding="utf-8", errors="replace") as fh:
            return parse_vcards(fh.read())
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def parse_call_history(text: str) -> "list[dict]":
    """name, number, kind (missed/received/dialed) and date (ms) per PBAP call vCard.

    The kind and time are one property: `X-IRMC-CALL-DATETIME;MISSED:20261008T101010`
    (or `;TYPE=MISSED`), from the IrMC spec PBAP adopted.
    """
    text = re.sub(r"\r?\n[ \t]", "", text)
    out, cur = [], {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        parts = [x.upper() for x in key.split(";")]
        field = parts[0].split(".")[-1]
        if field == "BEGIN":
            cur = {"name": "", "number": "", "kind": "", "date": 0}
        elif field == "FN":
            cur["name"] = value.strip()
        elif field == "TEL" and not cur.get("number"):
            cur["number"] = re.sub(r"^tel:", "", value.strip(), flags=re.I)
        elif field == "X-IRMC-CALL-DATETIME":
            kinds = [x.replace("TYPE=", "") for x in parts[1:]]
            cur["kind"] = next((k.lower() for k in kinds if k in ("MISSED", "RECEIVED", "DIALED")), "")
            cur["date"] = _epoch_ms(value.strip())
        elif field == "END" and cur and (cur.get("number") or cur.get("name")):
            out.append(cur)
            cur = {}
    return out


def call_history(address: str, count: int = 50) -> "list[dict]":
    """The phone's recent calls (newest first), pulled over PBAP, never kept on disk."""
    from gi.repository import GLib
    fd, path = tempfile.mkstemp(prefix="chronoa-cch-", suffix=".vcf", dir=_private_dir())
    os.close(fd)
    try:
        with _Session(address, "pbap") as s:
            try:
                s.call(s.path, _PBAP, "Select", "(ss)", ("int", "cch"))
                transfer = s.call(s.path, _PBAP, "PullAll", "(sa{sv})",
                                  (path, {"MaxCount": GLib.Variant("q", count)}))[0]
                s.wait(transfer)
            except BluetoothPhoneError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BluetoothPhoneError(_why(exc, "pbap")) from exc
        with open(path, encoding="utf-8", errors="replace") as fh:
            return sorted(parse_call_history(fh.read()), key=lambda c: c["date"], reverse=True)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --- calls (HFP) and files (OPP) ----------------------------------------------------

def dial(address: str, number: str) -> None:
    """Place a call through the phone's hands-free gateway."""
    from shani_chronoa.skills import bluetooth_call as bc
    try:
        gateways = bc.gateways()
    except bc.TelephonyUnavailable as exc:
        raise BluetoothPhoneError(str(exc)) from exc
    path = next((p for p, _n, a in gateways if a.upper() == address.upper()), None)
    if path is None:
        raise BluetoothPhoneError("the phone is not connected as a hands-free gateway, so a call "
                                  "cannot be placed through it")
    ok, why = bc._gateway_call(path, "Dial", "s", number)
    if not ok:
        raise BluetoothPhoneError(f"the phone refused the call ({why})")


def send_file(address: str, path: str) -> None:
    """Send one file to the phone with OBEX Object Push; the phone asks to accept it."""
    with _Session(address, "opp") as s:
        try:
            transfer = s.call(s.path, _OPP, "SendFile", "(s)", (path,))[0]
            s.wait(transfer)
        except BluetoothPhoneError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BluetoothPhoneError(_why(exc, "opp")) from exc
