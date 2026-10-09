"""MoYoung ("Da Fit") watches: steps, sleep, training, stress, heart rate, SpO2, blood pressure.

The protocol is Gadgetbridge's (`service/devices/moyoung/` and
`devices/moyoung/MoyoungConstants.java`, read 2026-10-08), not inferred:

- Commands are written to 0xFEE2 (write without response); replies arrive as
  notifications on 0xFEE3; live steps are pushed on 0xFEE1.
- A packet is `FE EA`, a length byte pair, the command, the payload. With a
  20-byte MTU the third byte is 0x10 and the fourth the total length; above
  255 bytes the third is 0x20 + the high byte. Replies longer than one
  notification arrive in fragments and are reassembled by that length.

  **The 0x10 header is not the bug it looks like**, and that cost a live
  experiment on 2026-10-08: this watch is `MOYOUNG-V2` (the MTU variant), so
  the protocol page says the third byte is "size high byte + 32", and
  `frame()` below sends 0x10 unconditionally - which reads as a length the
  firmware is not expecting, on exactly the large writes (weather, 181 bytes).
  Measured, both headers are accepted for the same query:

      QUERY_DND with 0x10 -> reply 00000000
      QUERY_DND with 0x20 -> reply 00000000

  And the reason 0x10 is the *correct* byte here is that nothing negotiates an
  MTU: gatttool's default is 20, `gatttool -b ... -I` only reports `MTU was
  exchanged successfully: 185` after an explicit `mtu 185`, and this module
  never sends one. Gadgetbridge's own branch is `if (mtu == 20) packet[2] = 16`.
  So the 0x20 form belongs to a session that asked for a bigger MTU, which this
  is not.
- Times inside training records are seconds in the watch's own clock, which is
  GMT+8 (`WATCH_INTERNAL_TIME_ZONE`).

**Only queries and measurements are sent** - nothing that changes a setting,
and no firmware command. A measurement makes the watch light its sensor for up
to a minute and report one value. Weight is not here because the watch does not
measure it: it only stores a weight you type into its profile.

Measured on an FB BGS002 (Manufacturer "MOYOUNG-V2"). The watch takes one Low
Energy connection at a time; while the Da Fit app on a phone holds it, this
computer cannot reach it at all (it stops advertising).
"""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

CMD_SYNC_SLEEP = 50
CMD_SYNC_PAST = 51
CMD_QUERY_TRAINING = 55
CMD_MEASURE_BP = 105
CMD_MEASURE_SPO2 = 107
CMD_MEASURE_HR = 109
CMD_ADVANCED_QUERY = 0xB9
ARG_STRESS = 0x11
ARG_YESTERDAY_STEPS, ARG_DAY_BEFORE_STEPS = 1, 2
ARG_YESTERDAY_SLEEP, ARG_DAY_BEFORE_SLEEP = 3, 4

SLEEP_STAGES = {0: "awake", 1: "light", 2: "deep"}
TRAINING_TYPES = (
    "walk run biking rope badminton basketball football swim mountaineering tennis "
    "rugby golf yoga fitness dancing baseball elliptical indoor_cycling free_exercise "
    "rowing_machine trail_run skiing bowling dumbbell sit_ups on_foot indoor_walk "
    "indoor_run cricket kabaddi").split()
WATCH_TZ = timezone(timedelta(hours=8))

SERVICE, OUT, IN, LIVE_STEPS = "feea", "fee2", "fee3", "fee1"
HR_MEASUREMENT = "2a37"


class WatchError(Exception):
    """The watch could not be reached or did not answer; carries the reason."""


# --- framing ---------------------------------------------------------------------

def frame(command: int, payload: bytes = b"") -> bytes:
    """Gadgetbridge's `MoyoungPacketOut.buildPacket(mtu=20, ...)`."""
    total = len(payload) + 5
    third = 0x10 if total <= 0xFF else (0x20 + (total >> 8)) & 0xFF
    return bytes([0xFE, 0xEA, third, total & 0xFF, command & 0xFF]) + payload


def packet_length(head: bytes) -> int:
    """Total length from a first fragment, or -1 when it is not a packet start."""
    if len(head) < 4 or head[0] != 0xFE or head[1] != 0xEA:
        return -1
    high = 0 if head[2] == 0x10 else (head[2] - 0x20 if head[2] >= 0x20 else -1)
    return -1 if high < 0 else (high << 8) | head[3]


class Reassembler:
    """Fragments in, (command, payload) out - `MoyoungPacketIn.putFragment`."""

    def __init__(self):
        self.buf = b""
        self.want = -1

    def put(self, fragment: bytes) -> "Optional[tuple[int, bytes]]":
        if self.want < 0:
            self.want = packet_length(fragment)
            if self.want < 0:
                return None           # not a packet start: dropped, as Gadgetbridge does
            self.buf = b""
        self.buf += fragment
        if len(self.buf) < self.want:
            return None
        packet, self.buf, self.want = self.buf[:self.want], b"", -1
        return packet[4], packet[5:]


# --- decoders (each from the Gadgetbridge handler named beside it) ---------------

def decode_steps(data: bytes) -> "Optional[dict]":
    """`handleStepsHistory`: steps, distance (m), calories as three little-endian uint24."""
    if len(data) != 9:
        return None
    u24 = lambda i: data[i] | data[i + 1] << 8 | data[i + 2] << 16  # noqa: E731
    return {"steps": u24(0), "distance_m": u24(3), "calories": u24(6)}


def decode_sleep(data: bytes, day: "datetime") -> "list[dict]":
    """`handleSleepHistory`: (stage, start hour, start minute) triples.

    A start at 20:00 or later belongs to the evening before `day`. Each stage
    lasts until the next one starts; the last one has no known end.
    """
    if len(data) % 3 or not any(data):
        # Measured on the FB BGS002: a night with no sleep recorded is one
        # all-zero triple, which is not "awake at midnight".
        return []
    starts = []
    for i in range(0, len(data), 3):
        stage, hour, minute = data[i], data[i + 1], data[i + 2]
        if hour > 23 or minute > 59:
            continue
        when = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if hour >= 20:
            when -= timedelta(days=1)
        starts.append((when, SLEEP_STAGES.get(stage, f"stage {stage}")))
    out = []
    for i, (when, stage) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else None
        out.append({"start": when, "end": end, "stage": stage,
                    "minutes": int((end - when).total_seconds() // 60) if end else None})
    return out


def sleep_summary(segments: "list[dict]") -> dict:
    total = {"awake": 0, "light": 0, "deep": 0}
    for s in segments:
        if s["minutes"] is not None and s["stage"] in total:
            total[s["stage"]] += s["minutes"]
    asleep = total["light"] + total["deep"]
    return {"asleep_minutes": asleep, **{f"{k}_minutes": v for k, v in total.items()},
            "from": segments[0]["start"] if segments else None,
            "to": segments[-1]["start"] if segments else None}


def watch_time(seconds: int) -> datetime:
    """`WatchTimeToLocalTime`: the watch's clock is GMT+8 wall time."""
    wall = datetime.fromtimestamp(seconds, WATCH_TZ).replace(tzinfo=None)
    return wall


def decode_training(data: bytes) -> "list[dict]":
    """`handleTrainingData`: 24-, 26- or 30-byte records, all-zero ones skipped."""
    import struct
    size = 24 if len(data) % 24 == 0 else 26 if len(data) % 26 == 0 else 30 if len(data) % 30 == 0 else 0
    if not size:
        return []
    out = []
    for i in range(0, len(data), size):
        rec = data[i:i + size]
        body = rec if size == 24 else rec[2:]
        if not any(body):
            continue
        p = 0 if size == 24 else 2
        start, end = struct.unpack_from("<II", rec, p)
        p += 8
        valid_s = struct.unpack_from("<h", rec, p)[0]
        p += 2
        avg_hr = 0
        if size == 26:
            avg_hr = rec[p]
        p += 1
        kind = rec[p]
        p += 1
        steps, distance = struct.unpack_from("<II", rec, p)
        p += 8
        if size == 26:
            calories = struct.unpack_from("<I", rec, p)[0]
        else:
            calories = struct.unpack_from("<h", rec, p)[0]
            if size == 30:
                avg_hr = rec[p + 2]
        out.append({"start": watch_time(start), "end": watch_time(end), "active_seconds": valid_s,
                    "type": TRAINING_TYPES[kind] if kind < len(TRAINING_TYPES) else f"type {kind}",
                    "steps": steps, "distance_m": distance, "calories": calories,
                    "avg_hr": avg_hr or None})
    return out


def decode_stress_day(payload: bytes, day: datetime) -> "list[tuple[datetime, int]]":
    """`handleStressPacket` case 0x03: half-hour slots from midnight, 0 = none.

    Gadgetbridge reads 26 slots (to 13:00). The FB BGS002 sends 48 - the whole
    day (measured: a 51-byte reply) - so every slot present is read.
    """
    if len(payload) < 3 or payload[0] != ARG_STRESS or payload[1] != 0x03:
        return []
    out = []
    for i, value in enumerate(payload[3:3 + 48]):
        if value:
            when = day.replace(hour=i // 2, minute=30 * (i % 2), second=0, microsecond=0)
            if when <= datetime.now():
                out.append((when, value))
    return out


# --- talking to the watch ------------------------------------------------------------

def _die_with_parent() -> None:
    """In the gatttool child: be killed when the process that started it dies.

    A SIGTERM'd caller never reaches `close()`, and gatttool (in its own
    session) then kept the watch's only connection - measured: the next
    connection could not list a single characteristic.
    """
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001 - best effort; close() still covers the normal path
        pass


def _hold_watch_lock(mac: str, seconds: float):
    """An exclusive flock per watch, shared by every process.

    The watch takes one connection at a time and each skill call runs in its own
    sandboxed process, so a thread lock would not stop two panel buttons pressed
    together from connecting at once - the second gets nothing.
    """
    import fcntl
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    fh = open(os.path.join(base, f"chronoa-watch-{mac.replace(':', '')}.lock"), "w")
    end = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.monotonic() > end:
                fh.close()
                raise WatchError("the watch is busy with another request")
            time.sleep(0.2)


_NOTIFY = re.compile(r"Notification handle = (0x[0-9a-f]+) value: ((?:[0-9a-f]{2} ?)+)", re.I)
_READ = re.compile(r"Characteristic value/descriptor: ((?:[0-9a-f]{2} ?)+)", re.I)
#: Interactive gatttool prints `char value handle: 0x0045, uuid: 0000fee2-...`;
#: the one-shot mode prints `=` instead. Both are accepted.
_CHAR = re.compile(r"char value handle\s*[:=]\s*(0x[0-9a-f]+),\s*uuid\s*[:=]\s*0000([0-9a-f]{4})-", re.I)


class Watch:
    """`with Watch(mac) as w: w.request(...)` - one gatttool session, held open.

    gatttool's interactive mode, because it is the route that holds this watch:
    bluez's own LE link is dropped within a second (see AGENTS.md), and the
    non-interactive mode cannot subscribe and write in one connection.
    """

    def __init__(self, mac: str, timeout: float = 25.0):
        self.mac, self.timeout = mac, timeout
        self.proc: Optional[subprocess.Popen] = None
        self.pending = ""
        self.handles: "dict[str, int]" = {}
        self.reasm = Reassembler()
        self.inbox: "list[tuple[int, bytes]]" = []
        self.live_steps: Optional[bytes] = None
        self.heart_rates: "list[int]" = []

    def __enter__(self):
        if not shutil.which("gatttool"):
            raise WatchError("gatttool (bluez) is not installed")
        self._lock = _hold_watch_lock(self.mac, self.timeout * 3)
        self.proc = subprocess.Popen(["gatttool", "-b", self.mac, "-I"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     start_new_session=True, preexec_fn=_die_with_parent)
        try:
            self._say("connect")
            self._until(lambda: "Connection successful" in self.pending, self.timeout,
                        "the watch did not accept a connection - it may be out of range, or "
                        "held by the Da Fit app on a phone (it takes one connection at a time)")
            self.pending = ""
            self._say("characteristics")
            self._until(lambda: all(f"0000{u}-" in self.pending for u in (OUT, IN)), 10,
                        "the watch connected but did not list its characteristics - it is busy "
                        "(another connection holds it) or dropped the link; try again")
            for handle, uuid in _CHAR.findall(self.pending):
                self.handles[uuid.lower()] = int(handle, 16)
            self.pending = ""
            for uuid in (IN, LIVE_STEPS, HR_MEASUREMENT):
                if uuid in self.handles:   # the CCCD follows the value on this watch (0x48, 0x43)
                    self._say(f"char-write-req 0x{self.handles[uuid] + 1:04x} 0100")
                    time.sleep(0.4)
            self._pump(0.5)
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self) -> None:
        lock, self._lock = getattr(self, "_lock", None), None
        try:
            self._close_proc()
        finally:
            if lock is not None:
                lock.close()                 # releases the flock

    def _close_proc(self) -> None:
        if self.proc is None:
            return
        try:
            self._say("disconnect")
            self._say("exit")
            self.proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        self.proc = None

    # -- plumbing (the transport: everything above request() goes through these)

    def _write(self, chunk: bytes) -> None:
        """One write-without-response to 0xFEE2."""
        self._say(f"char-write-cmd 0x{self.handles[OUT]:04x} {chunk.hex()}")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _say(self, line: str) -> None:
        self.proc.stdin.write((line + "\n").encode())
        self.proc.stdin.flush()

    def _pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        fd = self.proc.stdout.fileno()
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            ready, _, _ = select.select([fd], [], [], min(left, 0.1))
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    return
                text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", chunk.decode("utf-8", "replace"))
                self.pending += text.replace("\r", "\n")
                self._take()

    def _take(self) -> None:
        by_handle = {h: u for u, h in self.handles.items()}
        for handle, hexes in _NOTIFY.findall(self.pending):
            uuid = by_handle.get(int(handle, 16))
            if uuid:
                self._on_notify(uuid, bytes.fromhex(hexes.replace(" ", "")))
        self.pending = _NOTIFY.sub("", self.pending)

    def _on_notify(self, uuid: str, data: bytes) -> None:
        """One notification, from either transport."""
        if uuid == IN:
            done = self.reasm.put(data)
            if done:
                self.inbox.append(done)
        elif uuid == LIVE_STEPS:
            self.live_steps = data
        elif uuid == HR_MEASUREMENT:
                # A heart-rate measurement started with command 109 streams here
            # (the standard 0x2A37), not as a reply on 0xFEE3 - measured: 70 s
            # of silence on 0xFEE3 while the sensor was lit.
            from shani_chronoa.skills.bluetooth_gatt import decode_heart_rate
            bpm, note = decode_heart_rate(data.hex())
            if bpm and "contact LOST" not in (note or ""):
                self.heart_rates.append(int(bpm))

    def _until(self, ok, seconds: float, why: str) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._pump(0.2)
            if ok():
                return
            if self.proc.poll() is not None:
                break
        raise WatchError(why)

    # -- requests

    def request(self, command: int, payload: bytes = b"", seconds: float = 8.0,
                prefix: bytes = b"", valid_at: int = -1) -> "Optional[bytes]":
        """Send one command and return the payload of its reply, or None on silence.

        A reply counts when it starts with `prefix` and, with `valid_at` >= 0,
        when the byte there is a reading (not 0 or 0xFF). Plain data rather than a
        callback, so the same request can be sent to the companion over a socket.
        """
        def match(body: bytes) -> bool:
            if not body.startswith(prefix):
                return False
            return valid_at < 0 or (len(body) > valid_at and body[valid_at] not in (0, 0xFF))
        packet = frame(command, payload)
        for i in range(0, len(packet), 20):
            self._write(packet[i:i + 20])
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._pump(0.2)
            for i, (cmd, body) in enumerate(self.inbox):
                if cmd == (command & 0xFF) and match(body):
                    return self.inbox.pop(i)[1]
        return None

    def measure(self, what: str, seconds: float = 70.0) -> "Optional[tuple[int, ...]]":
        command, start, stop = MEASUREMENTS[what]
        try:
            if what == "heart_rate":
                self.heart_rates = []
                self.request(command, start, seconds=0.2)
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    self._pump(0.3)
                    reply = next((b for c, b in self.inbox if c == command and b and b[0] not in (0, 0xFF)), None)
                    if reply:
                        return (reply[0],)
                    if len(self.heart_rates) >= 3:
                        # The sensor settles over its first readings; the last of three is used.
                        return (self.heart_rates[-1],)
                return (self.heart_rates[-1],) if self.heart_rates else None
            body = self.request(command, start, seconds=seconds, valid_at=1 if what == "blood_pressure" else 0)
        finally:
            self.request(command, stop, seconds=0.5)
        if not body:
            return None
        return (body[1], body[2]) if what == "blood_pressure" else (body[0],)

    def read_char(self, uuid: str) -> "Optional[bytes]":
        """One standard characteristic by its 16-bit UUID (e.g. "2a19", battery), or None."""
        if uuid not in self.handles:
            return None
        self.pending = ""
        self._say(f"char-read-hnd 0x{self.handles[uuid]:04x}")
        try:
            self._until(lambda: bool(_READ.search(self.pending)), 8, "no answer")
        except WatchError:
            return None
        m = _READ.search(self.pending)
        self.pending = ""
        return bytes.fromhex(m.group(1).replace(" ", "")) if m else None

    def battery(self) -> "Optional[int]":
        raw = self.read_char("2a19")
        return raw[0] if raw else None

    def read_steps_now(self) -> "Optional[dict]":
        if LIVE_STEPS not in self.handles:
            return None
        self._say(f"char-read-hnd 0x{self.handles[LIVE_STEPS]:04x}")
        self._until(lambda: bool(_READ.search(self.pending)) or self.live_steps is not None, 8,
                    "the watch did not answer the steps read")
        m = _READ.search(self.pending)
        raw = bytes.fromhex(m.group(1).replace(" ", "")) if m else self.live_steps
        self.pending = ""
        return decode_steps(raw or b"")


class BluezWatch(Watch):
    """The same watch through bluez's own GATT API - no gatttool.

    **This is the transport ShaniOS has.** No Arch package ships gatttool
    (bluez-utils has bluetoothctl and mpris-proxy; bluez-deprecated-tools has
    hcitool, rfcomm and sdptool - checked against the Arch package files
    2026-10-09), so a gatttool-only watch worked on the Ubuntu dev box and on no
    Shanios install at all.

    It needs bluez to hold the link, which this watch refused with its old
    pairing (the link dropped within a second of "Connection successful");
    `connect()` falls back to gatttool where it exists.
    """

    _BLUEZ = "org.bluez"
    _CHAR = "org.bluez.GattCharacteristic1"
    _DEV = "org.bluez.Device1"

    def __enter__(self):
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self._GLib = GLib
        self._lock = _hold_watch_lock(self.mac, self.timeout * 3)
        self._ctx = GLib.MainContext.new()
        self._ctx.push_thread_default()
        self._subs, self._notifying, self._connected = [], [], False
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self.device = "/org/bluez/hci0/dev_" + self.mac.replace(":", "_")
            self._subs.append(self.bus.signal_subscribe(
                self._BLUEZ, "org.freedesktop.DBus.Properties", "PropertiesChanged", None, None,
                Gio.DBusSignalFlags.NONE, self._on_props))
            if not self._prop(self.device, self._DEV, "Connected"):
                try:
                    self._call(self.device, self._DEV, "Connect", timeout=self.timeout)
                except Exception as exc:  # noqa: BLE001 - said below with the reason
                    raise WatchError(f"bluez could not connect to the watch ({str(exc)[-120:]})") from exc
            end = time.monotonic() + self.timeout
            while not self._prop(self.device, self._DEV, "ServicesResolved"):
                if time.monotonic() > end or not self._prop(self.device, self._DEV, "Connected"):
                    raise WatchError("the watch connected over bluez but dropped the link before its "
                                     "services resolved - its pairing keys may be stale; pair it again")
                self._pump(0.2)
            self._connected = True
            self.paths = self._find_characteristics()
            if OUT not in self.paths or IN not in self.paths:
                raise WatchError("the watch has no MoYoung command channel (0xFEE2/0xFEE3)")
            for uuid in (IN, LIVE_STEPS, HR_MEASUREMENT):
                if uuid in self.paths:
                    self._call(self.paths[uuid], self._CHAR, "StartNotify")
                    self._notifying.append(self.paths[uuid])
            self.handles = {u: i for i, u in enumerate(self.paths)}   # the protocol code only tests membership
            self._pump(0.3)
            return self
        except BaseException:
            self.close()
            raise

    def _call(self, path, iface, method, args=None, timeout: float = 10.0):
        return self.bus.call_sync(self._BLUEZ, path, iface, method, args, None, 0, int(timeout * 1000), None)

    def _prop(self, path, iface, name):
        try:
            return self._call(path, "org.freedesktop.DBus.Properties", "Get",
                              self._GLib.Variant("(ss)", (iface, name))).unpack()[0]
        except Exception:  # noqa: BLE001 - an object that is gone has no properties
            return None

    def _find_characteristics(self) -> "dict[str, str]":
        objects = self._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects").unpack()[0]
        found = {}
        for path, ifaces in objects.items():
            char = ifaces.get(self._CHAR)
            if char and path.startswith(self.device + "/"):
                uuid = str(char.get("UUID", "")).lower()
                if uuid.startswith("0000") and uuid.endswith("-0000-1000-8000-00805f9b34fb"):
                    found.setdefault(uuid[4:8], path)
        return found

    def _on_props(self, _conn, _sender, path, _iface, _signal, params) -> None:
        iface, changed, _gone = params.unpack()
        if iface == self._DEV and path == getattr(self, "device", None) and changed.get("Connected") is False:
            self._connected = False
        if iface != self._CHAR or "Value" not in changed:
            return
        uuid = next((u for u, p in getattr(self, "paths", {}).items() if p == path), None)
        if uuid:
            self._on_notify(uuid, bytes(changed["Value"]))

    def _pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            while self._ctx.iteration(False):
                pass
            if time.monotonic() >= end:
                return
            time.sleep(0.02)

    def _write(self, chunk: bytes) -> None:
        GLib = self._GLib
        self._call(self.paths[OUT], self._CHAR, "WriteValue", GLib.Variant(
            "(aya{sv})", (list(chunk), {"type": GLib.Variant("s", "command")})))

    def alive(self) -> bool:
        return self._connected

    def read_char(self, uuid: str) -> "Optional[bytes]":
        if uuid not in self.paths:
            return None
        try:
            return bytes(self._call(self.paths[uuid], self._CHAR, "ReadValue",
                                    self._GLib.Variant("(a{sv})", ({},))).unpack()[0])
        except Exception:  # noqa: BLE001
            return None

    def read_steps_now(self) -> "Optional[dict]":
        if LIVE_STEPS not in self.paths:
            return None
        raw = self._call(self.paths[LIVE_STEPS], self._CHAR, "ReadValue",
                         self._GLib.Variant("(a{sv})", ({},))).unpack()[0]
        return decode_steps(bytes(raw))

    def close(self) -> None:
        lock, self._lock = getattr(self, "_lock", None), None
        try:
            for path in getattr(self, "_notifying", []):
                try:
                    self._call(path, self._CHAR, "StopNotify")
                except Exception:  # noqa: BLE001
                    pass
            self._notifying = []
            if getattr(self, "_connected", False):
                # Let go, as gatttool did: a phone's Da Fit cannot reach a watch we hold.
                try:
                    self._call(self.device, self._DEV, "Disconnect")
                except Exception:  # noqa: BLE001
                    pass
                self._connected = False
            for sub in getattr(self, "_subs", []):
                self.bus.signal_unsubscribe(sub)
            self._subs = []
            if getattr(self, "_ctx", None) is not None:
                self._ctx.pop_thread_default()
                self._ctx = None
        finally:
            if lock is not None:
                lock.close()


class DirectWatch:
    """`with DirectWatch(mac) as w:` - `open_watch` as a context manager."""

    def __init__(self, mac: str):
        self.mac, self.w = mac, None

    def __enter__(self):
        self.w = open_watch(self.mac)
        return self.w

    def __exit__(self, *exc):
        if self.w is not None:
            self.w.close()
        return False


def open_watch(mac: str):
    """A direct connection: bluez's GATT API first, gatttool where installed.

    bluez first because it is what every Shanios install has; gatttool second
    because it holds this watch when bluez cannot (stale LE keys, measured).
    """
    try:
        return BluezWatch(mac).__enter__()
    except WatchError as first:
        if not shutil.which("gatttool"):
            raise
        try:
            return Watch(mac).__enter__()
        except WatchError as second:
            raise WatchError(f"{second} (bluez said: {first})") from second


# --- what a caller asks for -----------------------------------------------------------

def health_summary(mac: str) -> dict:
    """Everything the watch keeps, read in one connection. No measurement is started."""
    today = datetime.now()
    out: dict = {}
    with connect(mac) as w:
        out["steps_today"] = w.read_steps_now()
        for arg, key in ((ARG_YESTERDAY_STEPS, "steps_yesterday"), (ARG_DAY_BEFORE_STEPS, "steps_2_days_ago")):
            body = w.request(CMD_SYNC_PAST, bytes([arg]), prefix=bytes([arg]))
            out[key] = decode_steps(body[1:]) if body else None
        body = w.request(CMD_SYNC_SLEEP)
        night = decode_sleep(body or b"", today)
        if not night:
            body = w.request(CMD_SYNC_PAST, bytes([ARG_YESTERDAY_SLEEP]),
                             prefix=bytes([ARG_YESTERDAY_SLEEP]))
            night = decode_sleep(body[1:] if body else b"", today - timedelta(days=1))
        out["sleep"] = night
        out["sleep_summary"] = sleep_summary(night) if night else None
        body = w.request(CMD_QUERY_TRAINING)
        out["training"] = decode_training(body or b"")
        body = w.request(CMD_ADVANCED_QUERY, bytes([ARG_STRESS, 0x03, 0x00]),
                         prefix=bytes([ARG_STRESS, 0x03]))
        out["stress_today"] = decode_stress_day(body or b"", today)
    return out


MEASUREMENTS = {"heart_rate": (CMD_MEASURE_HR, b"\x00", b"\xff"),
                "blood_oxygen": (CMD_MEASURE_SPO2, b"\x00", b"\xff"),
                "blood_pressure": (CMD_MEASURE_BP, b"\x00\x00\x00", b"\xff\xff\xff")}


def measure(mac: str, what: str, seconds: float = 70.0) -> "Optional[tuple[int, ...]]":
    """Start one measurement on the wrist, wait for the result, stop it.

    heart_rate -> (bpm,), blood_oxygen -> (percent,), blood_pressure ->
    (systolic, diastolic). None when the watch reported nothing usable - not
    worn, or moved during the reading.
    """
    with connect(mac) as w:
        return w.measure(what, seconds)


# --- settings, time, notifications, music (all from Gadgetbridge's settings classes) ----

#: name -> (query command, set command, kind, values). Kinds follow
#: devices/moyoung/settings/: bool is one byte 0/1; int is set big-endian and
#: read back little-endian ("that's how the protocol is designed"); byte is one
#: byte; enum maps a word to its byte.
SETTINGS = {
    "step_goal": (38, 22, "int", None),
    "time_format": (39, 23, "enum", {"12h": 0, "24h": 1}),
    "units": (42, 26, "enum", {"metric": 0, "imperial": 1}),
    "raise_to_wake": (40, 24, "bool", None),
    "other_app_notifications": (44, 28, "bool", None),
    "sedentary_reminder": (45, 29, "bool", None),
    "heart_rate_auto_interval": (47, 31, "byte", None),
    "breathing_light": (0x88, 120, "bool", None),
    "power_saving": (0xA4, 0x94, "bool", None),
}
CMD_QUERY_DND, CMD_SET_DND = 0x81, 113
CMD_SYNC_TIME, CMD_SEND_MESSAGE = 49, 65
CMD_SET_MUSIC_INFO, CMD_SET_MUSIC_STATE = 68, 123
CMD_SET_USER_INFO = 18
#: A set is not answered; the watch needs this long before a query reports the
#: new value (measured: read back after 0.5 s, nothing; after 2 s, the new goal).
SET_SETTLE = 2.0
NOTIFY_TYPES = {"call": 0, "sms": 1, "whatsapp": 8, "other": 11}


def _decode_setting(kind: str, values, body: bytes):
    if not body:
        return None
    if kind == "int":
        return int.from_bytes(body[:4], "little") if len(body) >= 4 else None
    if kind == "bool":
        return bool(body[0]) if body[0] in (0, 1) else None
    if kind == "enum":
        return next((k for k, v in values.items() if v == body[0]), f"value {body[0]}")
    return body[0]


def _encode_setting(kind: str, values, value) -> bytes:
    if kind == "int":
        return int(value).to_bytes(4, "big")
    if kind == "bool":
        return bytes([1 if value in (True, 1, "on", "true", "yes") else 0])
    if kind == "enum":
        if value not in values:
            raise WatchError(f"must be one of {', '.join(values)}")
        return bytes([values[value]])
    return bytes([int(value) & 0xFF])


def decode_dnd(body: bytes) -> "Optional[dict]":
    """`MoyoungSettingTimeRange.decode`: two little-endian minute-of-day shorts."""
    if len(body) < 4:
        return None
    start, end = int.from_bytes(body[0:2], "little"), int.from_bytes(body[2:4], "little")
    return {"start": f"{start // 60:02d}:{start % 60:02d}", "end": f"{end // 60:02d}:{end % 60:02d}",
            "on": start != end}


def read_settings(w: "Watch") -> dict:
    out = {}
    for name, (query, _set, kind, values) in SETTINGS.items():
        out[name] = _decode_setting(kind, values, w.request(query, seconds=3) or b"")
    out["do_not_disturb"] = decode_dnd(w.request(CMD_QUERY_DND, seconds=3) or b"")
    return out


def change_setting(w: "Watch", name: str, value) -> "tuple[bool, object]":
    """Set one setting and read it back; (took effect, value now on the watch)."""
    if name == "do_not_disturb":
        start, end = value                      # "22:00", "07:00"
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        w.request(CMD_SET_DND, bytes([sh, sm, eh, em]), seconds=SET_SETTLE)
        now = decode_dnd(w.request(CMD_QUERY_DND, seconds=5) or b"")
        return bool(now and now["start"] == f"{sh:02d}:{sm:02d}" and now["end"] == f"{eh:02d}:{em:02d}"), now
    if name not in SETTINGS:
        raise WatchError(f"unknown setting {name!r}; known: {', '.join(list(SETTINGS) + ['do_not_disturb'])}")
    query, setter, kind, values = SETTINGS[name]
    w.request(setter, _encode_setting(kind, values, value), seconds=SET_SETTLE)
    now = _decode_setting(kind, values, w.request(query, seconds=5) or b"")
    wanted = int(value) if kind == "int" else _decode_setting(kind, values, _encode_setting(kind, values, value))
    return now == wanted, now


def time_payload(now: "Optional[datetime]" = None) -> bytes:
    """`setTime`: local wall time expressed in the watch's GMT+8 clock, then 8."""
    now = now or datetime.now()
    watch_seconds = int(now.replace(tzinfo=WATCH_TZ).timestamp())
    return watch_seconds.to_bytes(4, "big") + b"\x08"


def message_payload(kind: str, sender: str, text: str) -> bytes:
    """`onNotification`: type byte, then "sender:text" - the watch splits at the first ':'."""
    sender = (sender or "")[:32].replace(":", ";")
    body = (text or " ")[:512]
    return bytes([NOTIFY_TYPES.get(kind, 11) & 0xFF]) + f"{sender}:{body}".encode("utf-8")


def user_info_payload(height_cm: int, weight_kg: int, age: int, gender: str) -> bytes:
    """`MoyoungSettingUserInfo.encode`: height, weight, age, gender (male 0, female 1)."""
    return bytes([int(height_cm) & 0xFF, int(weight_kg) & 0xFF, int(age) & 0xFF,
                  1 if str(gender).lower().startswith("f") else 0])


def call_payload(caller: str) -> bytes:
    """`onSetCallState` CALL_INCOMING: notification type 0 with the caller - the watch rings."""
    return bytes([NOTIFY_TYPES["call"]]) + (caller or "Unknown caller")[:32].encode("utf-8")


#: `NOTIFICATION_TYPE_CALL_OFF_HOOK` (-1): the call was answered or ended - stop ringing.
CALL_OFF_HOOK = b"\xff"


def tell_watch(mac: str, payload: bytes) -> None:
    """Connect, send one notification payload, disconnect."""
    with connect(mac) as w:
        w.request(CMD_SEND_MESSAGE, payload, seconds=0.5)


# --- one connection, shared: the companion's socket ---------------------------------

def socket_path(mac: str) -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, f"chronoa-watch-{mac.replace(':', '')}.sock")


class CompanionClient:
    """The same calls as `Watch`, answered by the companion that holds the watch.

    The watch takes one connection. While `watch_companion` keeps it (so the
    watch's own buttons reach this computer), every other caller - the skill in
    its sandboxed process, the panel - asks through this socket instead of
    opening a second connection that the watch would refuse.
    """

    def __init__(self, path: str):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _rpc(self, method: str, *args, timeout: float = 160.0):
        import json
        import socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(self.path)
            s.sendall((json.dumps({"method": method, "args": [
                {"hex": a.hex()} if isinstance(a, (bytes, bytearray)) else a for a in args]}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        reply = json.loads(data or b"{}")
        if "error" in reply:
            raise WatchError(reply["error"])
        value = reply.get("result")
        if isinstance(value, dict) and set(value) == {"hex"}:
            return bytes.fromhex(value["hex"])
        return tuple(value) if isinstance(value, list) else value

    def request(self, command, payload=b"", seconds=8.0, prefix=b"", valid_at=-1):
        return self._rpc("request", command, bytes(payload), seconds, bytes(prefix), valid_at)

    def read_steps_now(self):
        return self._rpc("read_steps_now")

    def measure(self, what, seconds=70.0):
        return self._rpc("measure", what, seconds)


def connect(mac: str):
    """The companion's shared connection when it holds the watch, else a direct one."""
    import socket
    path = socket_path(mac)
    if os.path.exists(path):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(path)
            return CompanionClient(path)
        except OSError:
            pass                                # a stale socket: nobody is listening
    return DirectWatch(mac)


# --- weather (onSendWeather: today 67, sunrise/sunset 0xB5, location 69, forecast 66) ---

CMD_SET_WEATHER_FUTURE, CMD_SET_WEATHER_TODAY = 66, 67
CMD_SET_WEATHER_LOCATION, CMD_SET_SUNRISE_SUNSET = 69, 0xB5

#: How much of the place name goes into the two location-bearing weather packets.
#:
#: **This is cosmetic. It prevents nothing, and an earlier version of this
#: comment claimed it fixed the watch restarting. It does not - that claim was
#: wrong twice over and is withdrawn here.**
#:
#: What actually happened, measured 2026-10-08:
#:
#: - An early bisection sent each weather packet alone and blamed `0xB5`, because
#:   the link died right after it was written. Re-running showed the *same*
#:   15-byte packet both surviving and killing it, so the packet was never the
#:   variable.
#: - Bounding the label to 6 characters changed nothing - the watch still died.
#:
#: The control that explains all of it: connected, sending **nothing at all** -
#: no sync-time, no weather, no music - the watch still dropped, at 150s. It does
#: that on its own. Every drop observed in this session was the watch rebooting
#: on a schedule, and writing a packet just before the drop made it look causal.
#:
#: So the bound stays only because a short name is what the watch's own 4-char
#: UTF-16 slot in the `today` packet implies, and the label here is a geocoder's
#: "City, Region, Country". Reading it as protection against a restart is wrong.
WEATHER_LABEL_CHARS = 6
#: How much of the place name may go into `CMD_SET_SUNRISE_SUNSET` (0xB5).
#:
#: **This bound does NOT stop the watch restarting, and an earlier version of
#: this comment claimed that it did. It was falsified on 2026-10-08.**
#:
#: What was measured, sending 0xB5 alone and watching the link:
#:
#: | location chars | payload | watch |
#: |---|---|---|
#: | 6 (`"Sangli"`) | 15 B | survived, 3 times |
#: | 8 (`"Sangli12"`) | 17 B | rebooted |
#: | 9, 13, 24 | 18, 22, 35 B | rebooted |
#:
#: That looked like the cause and was not. With the label bounded to 6 and the
#: whole four-packet weather sequence sent through the real code path, the watch
#: still restarted within 10s - and a "both pushes off" run intended as a control
#: turned out to have weather still enabled and dropped at 65s. So: the weather
#: push is reliably associated with the restart, **no single packet or byte count
#: has been shown to be responsible**, and the bisection that pointed at 0xB5 was
#: not reproducible (the same 15-byte packet both survived and killed it).
#:
#: The bound is kept because a short place name is what the watch's own 4-char
#: UTF-16 slot in the `today` packet implies, not because it is known to be safe
#: against this. Anything treating it as a fix is misreading it.
#: The watch's icons (MoyoungConstants.WEATHER_*).
W_CLOUDY, W_FOGGY, W_OVERCAST, W_RAINY, W_SNOWY, W_SUNNY, W_WIND, W_HAZE = range(8)


def wmo_icon(code) -> int:
    """An Open-Meteo WMO weather code -> the watch's icon (Gadgetbridge maps OWM codes the same way)."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return W_HAZE
    if code in (0, 1):
        return W_SUNNY
    if code == 2:
        return W_OVERCAST
    if code == 3:
        return W_CLOUDY
    if code in (45, 48):
        return W_FOGGY
    if 71 <= code <= 77 or code in (85, 86):
        return W_SNOWY
    if 51 <= code <= 67 or 80 <= code <= 82 or code >= 95:
        return W_RAINY
    return W_HAZE


def _temp(value) -> int:
    """A Celsius reading as the signed byte the watch takes."""
    try:
        return max(-100, min(100, round(float(value)))) & 0xFF
    except (TypeError, ValueError):
        return (-100) & 0xFF                      # Gadgetbridge's "no value"


def weather_packets(current: dict, daily: dict, label: str, updated: datetime) -> "list[tuple[int, bytes]]":
    """The four packets Gadgetbridge sends, from Open-Meteo's `current` and `daily`."""
    def day(key, i):
        values = daily.get(key) or []
        return values[i] if i < len(values) else None
    icon = wmo_icon(current.get("weather_code"))
    temp = _temp(current.get("temperature_2m"))
    city = (label or "")[:4].ljust(4).encode("utf-16-be")
    today = bytes([0, icon, temp]) + "    ".encode("utf-16-be") + city          # no PM2.5
    sun = []
    for key in ("sunrise", "sunset"):
        stamp = str(day(key, 0) or "")
        try:
            t = datetime.fromisoformat(stamp)
            sun += [t.hour, t.minute]
        except ValueError:
            sun += [0, 0]
    # Bounded because the label here is a geocoder's "City, Region, Country"
    # while the watch's own today-packet slot is 4 characters. NOT a fix for the
    # restart - see WEATHER_LABEL_CHARS for what was actually falsified.
    # **Both** location-bearing packets get the bounded name. An earlier fix
    # bounded only `sunrise` (0xB5) and the watch still restarted, which is this
    # line: `CMD_SET_WEATHER_LOCATION` (69) carries the same label unbounded, so
    # half the fix changed nothing observable. Fixing one packet and not the
    # other reads as "the fix did not work" when it was "the fix was half done".
    short = (label or "").strip()[:WEATHER_LABEL_CHARS]
    sunrise = bytes([0, icon, temp, 0, 0] + sun) + short.encode("utf-8")
    location = f"{updated:%H:%M} {short}".strip().encode("utf-8")
    future = bytes([icon, _temp(day("temperature_2m_max", 0)), _temp(day("temperature_2m_min", 0))])
    for i in range(1, 8):
        if day("weather_code", i) is None:
            future += bytes([W_HAZE, (-100) & 0xFF, (-100) & 0xFF])
        else:
            future += bytes([wmo_icon(day("weather_code", i)), _temp(day("temperature_2m_max", i)),
                             _temp(day("temperature_2m_min", i))])
    return [(CMD_SET_WEATHER_TODAY, today), (CMD_SET_SUNRISE_SUNSET, sunrise),
            (CMD_SET_WEATHER_LOCATION, location), (CMD_SET_WEATHER_FUTURE, future)]
