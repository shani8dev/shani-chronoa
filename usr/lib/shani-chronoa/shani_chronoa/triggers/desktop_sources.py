"""Desktop and device event sources for event rules: screen lock, power, network, USB, Bluetooth, schedule, sleep, audio, journal, D-Bus, calendar, phone."""

from __future__ import annotations


import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional


from .common import (  # noqa: F401
    EVENT_AUDIODEVICE,
    EVENT_BTCONNECT,
    EVENT_CALENDAR,
    EVENT_DBUSPROP,
    EVENT_JOURNALMATCH,
    EVENT_NETSTATE,
    EVENT_PHONE,
    EVENT_SOUND,
    EVENT_POWERSTATE,
    EVENT_SCHEDULE,
    EVENT_SCREENLOCK,
    EVENT_SLEEPWAKE,
    EVENT_USBPLUG,
    Event,
    EventSignal,
    SIGNAL_OK,
    _fingerprint,
    _unavailable,
)
from .sources import (  # noqa: F401
    _run_argv,
)


# --- 7-12. desktop and device life ------------------------------------------
#
# Each reader below takes the rule's `source` - the state to be told about -
# and returns the observed state as the fingerprint, with an event only while
# that state holds (see the comment above EVENT_TYPES). Anything that cannot be
# read is SIGNAL_UNAVAILABLE with the reason, never a quiet "not in that state":
# a laptop with no readable battery is not a laptop on AC.

def _state_signal(kind: str, source: str, state: Any, hit: bool, summary: str,
                  detail: dict, moment: float) -> EventSignal:
    subject = f"{kind}:{source}"
    fingerprint = _fingerprint((kind, source, state))
    payload = {"kind": kind, "subject": subject, "snapshot": detail}
    if not hit:
        return EventSignal(status=SIGNAL_OK, fingerprint=fingerprint, detail=summary, payload=payload)
    return EventSignal(
        status=SIGNAL_OK, fingerprint=fingerprint, detail=summary, payload=payload,
        event=Event(kind=kind, subject=subject, summary=summary, fingerprint=fingerprint,
                    detail=detail, created_at=moment),
    )


def _source_parts(source: str) -> "tuple[str, str]":
    head, _, rest = source.strip().partition(":")
    return head.strip().lower(), rest.strip()


SCREENLOCK_SOURCES = ("locked", "unlocked", "idle", "active")


def _graphical_session() -> "Optional[str]":
    sid = os.environ.get("XDG_SESSION_ID", "").strip()
    if sid:
        return sid
    proc = _run_argv(["loginctl", "show-user", str(os.getuid()), "--property=Display", "--value"])
    value = (proc.stdout or "").strip() if proc is not None and proc.returncode == 0 else ""
    return value or None


def read_screenlock(source: str, now: Optional[float] = None) -> EventSignal:
    """Whether this user's graphical session is locked or idle, from logind."""
    moment = time.time() if now is None else now
    want = source.strip().lower()
    subject = f"{EVENT_SCREENLOCK}:{want}"
    if want not in SCREENLOCK_SOURCES:
        return _unavailable(EVENT_SCREENLOCK, subject,
                            f"source must be one of {', '.join(SCREENLOCK_SOURCES)}, not {source!r}")
    if shutil.which("loginctl") is None:
        return _unavailable(EVENT_SCREENLOCK, subject, "loginctl is not installed, so logind cannot be asked")
    sid = _graphical_session()
    if not sid:
        return _unavailable(EVENT_SCREENLOCK, subject, "this user has no logind session to watch")
    proc = _run_argv(["loginctl", "show-session", sid, "--property=LockedHint", "--property=IdleHint"])
    if proc is None or proc.returncode != 0:
        return _unavailable(EVENT_SCREENLOCK, subject, f"logind did not answer for session {sid}")
    props = dict(line.split("=", 1) for line in (proc.stdout or "").splitlines() if "=" in line)
    field_ = "LockedHint" if want in ("locked", "unlocked") else "IdleHint"
    raw = props.get(field_, "").strip().lower()
    if raw not in ("yes", "no"):
        return _unavailable(EVENT_SCREENLOCK, subject, f"logind reported no usable {field_} for session {sid}")
    on = raw == "yes"
    hit = on if want in ("locked", "idle") else not on
    word = {"locked": "locked", "unlocked": "unlocked", "idle": "idle", "active": "active again"}[want]
    state_word = ("locked" if on else "unlocked") if field_ == "LockedHint" else ("idle" if on else "active")
    return _state_signal(EVENT_SCREENLOCK, want, (field_, on), hit,
                         f"the session is {word}" if hit else f"the session is {state_word}",
                         {"session": sid, field_: on}, moment)


POWER_SUPPLY_DIR = Path("/sys/class/power_supply")


def _read_power() -> "tuple[Optional[bool], Optional[int], str]":
    """(on AC, battery percent, battery status) from sysfs; None where it cannot be read."""
    ac, levels, status = None, [], ""
    if not POWER_SUPPLY_DIR.is_dir():
        return None, None, ""
    for dev in sorted(POWER_SUPPLY_DIR.iterdir()):
        def attr(name: str) -> str:
            try:
                return (dev / name).read_text().strip()
            except OSError:
                return ""
        kind = attr("type")
        if kind in ("Mains", "USB", "USB_C", "USB_PD"):
            if attr("online") in ("0", "1"):
                ac = bool(ac) or attr("online") == "1"
        elif kind == "Battery" and attr("scope") != "Device":  # a mouse's battery is not the machine's
            if attr("capacity").isdigit():
                levels.append(int(attr("capacity")))
                status = status or attr("status")
    level = round(sum(levels) / len(levels)) if levels else None
    if ac is None and status:
        ac = status != "Discharging"
    return ac, level, status


def read_powerstate(source: str, now: Optional[float] = None) -> EventSignal:
    """on-battery, on-ac, battery-below:N, battery-above:N or charged, from /sys/class/power_supply."""
    moment = time.time() if now is None else now
    head, rest = _source_parts(source)
    subject = f"{EVENT_POWERSTATE}:{source.strip().lower()}"
    if head not in ("on-battery", "on-ac", "battery-below", "battery-above", "charged"):
        return _unavailable(EVENT_POWERSTATE, subject, "source must be on-battery, on-ac, battery-below:N, "
                                                       f"battery-above:N or charged, not {source!r}")
    ac, level, status = _read_power()
    detail = {"on_ac": ac, "battery_percent": level, "battery_status": status}
    if head in ("on-battery", "on-ac"):
        if ac is None:
            return _unavailable(EVENT_POWERSTATE, subject, "no power supply reports whether it is online")
        hit = (not ac) if head == "on-battery" else ac
        return _state_signal(EVENT_POWERSTATE, source.strip().lower(), ("ac", ac), hit,
                             "running on battery" if not ac else "running on AC power", detail, moment)
    if level is None:
        return _unavailable(EVENT_POWERSTATE, subject, "this machine has no battery that reports a charge level")
    if head == "charged":
        full = status == "Full" or (level >= 100 and bool(ac))
        return _state_signal(EVENT_POWERSTATE, "charged", ("full", full), full,
                             f"the battery is {'fully charged' if full else f'at {level}%'}", detail, moment)
    if not rest.isdigit() or not 1 <= int(rest) <= 99:
        return _unavailable(EVENT_POWERSTATE, subject, f"{head} needs a percentage from 1 to 99, e.g. {head}:20")
    limit = int(rest)
    hit = level < limit if head == "battery-below" else level > limit
    return _state_signal(EVENT_POWERSTATE, f"{head}:{limit}", (head, limit, hit), hit,
                         f"the battery is at {level}% ({'below' if level < limit else 'above'} {limit}%)",
                         detail, moment)


def _nmcli_fields(line: str) -> "list[str]":
    """Split nmcli -t output, which escapes ':' inside a field as '\\:'."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur, esc = cur + ch, False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def read_netstate(source: str, now: Optional[float] = None) -> EventSignal:
    """online, offline, limited, connected:NAME or disconnected:NAME, from NetworkManager."""
    moment = time.time() if now is None else now
    head, name = _source_parts(source)
    subject = f"{EVENT_NETSTATE}:{source.strip()}"
    if head not in ("online", "offline", "limited", "connected", "disconnected") or \
            (head in ("connected", "disconnected")) != bool(name):
        return _unavailable(EVENT_NETSTATE, subject, "source must be online, offline, limited, "
                                                     f"connected:NAME or disconnected:NAME, not {source!r}")
    if shutil.which("nmcli") is None:
        return _unavailable(EVENT_NETSTATE, subject, "nmcli is not installed, so NetworkManager cannot be asked")
    if head in ("online", "offline", "limited"):
        proc = _run_argv(["nmcli", "-t", "-f", "CONNECTIVITY", "general"])
        value = (proc.stdout or "").strip().lower() if proc is not None and proc.returncode == 0 else ""
        if value not in ("full", "limited", "portal", "none"):
            return _unavailable(EVENT_NETSTATE, subject,
                                f"NetworkManager reported connectivity {value or 'nothing'!r}, which is not a state")
        state = {"full": "online", "none": "offline"}.get(value, "limited")
        return _state_signal(EVENT_NETSTATE, head, ("connectivity", state), state == head,
                             f"the network is {state}" + (" (captive portal)" if value == "portal" else ""),
                             {"connectivity": value}, moment)
    proc = _run_argv(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"])
    if proc is None or proc.returncode != 0:
        return _unavailable(EVENT_NETSTATE, subject, "NetworkManager did not list its active connections")
    active = [_nmcli_fields(line)[0] for line in (proc.stdout or "").splitlines() if line.strip()]
    up = any(name.lower() == a.lower() for a in active)
    hit = up if head == "connected" else not up
    return _state_signal(EVENT_NETSTATE, f"{head}:{name}", ("connection", name.lower(), up), hit,
                         f"{name} is {'connected' if up else 'not connected'}", {"active": active}, moment)


USB_DEVICES_DIR = Path("/sys/bus/usb/devices")


def _usb_devices() -> "Optional[list[str]]":
    if not USB_DEVICES_DIR.is_dir():
        return None
    out = []
    for dev in sorted(USB_DEVICES_DIR.iterdir()):
        def attr(name: str) -> str:
            try:
                return (dev / name).read_text(errors="replace").strip()
            except OSError:
                return ""
        vid, pid = attr("idVendor"), attr("idProduct")
        if not vid or dev.name.startswith("usb"):  # a root hub is the controller, not a plugged device
            continue
        label = " ".join(x for x in (attr("manufacturer"), attr("product")) if x) or "unnamed device"
        out.append(f"{label} [{vid}:{pid}]")
    return out


def read_usbplug(source: str, now: Optional[float] = None) -> EventSignal:
    """any, plugged:TEXT or unplugged:TEXT (TEXT matches the name or vendor:product id)."""
    moment = time.time() if now is None else now
    head, text = _source_parts(source)
    subject = f"{EVENT_USBPLUG}:{source.strip()}"
    if head not in ("any", "plugged", "unplugged") or (head != "any") != bool(text):
        return _unavailable(EVENT_USBPLUG, subject, f"source must be any, plugged:NAME or unplugged:NAME, not {source!r}")
    devices = _usb_devices()
    if devices is None:
        return _unavailable(EVENT_USBPLUG, subject, f"{USB_DEVICES_DIR} is not readable here")
    if head == "any":
        return _state_signal(EVENT_USBPLUG, "any", ("set", tuple(devices)), True,
                             "USB devices now: " + ("; ".join(devices) or "none"), {"devices": devices}, moment)
    matches = [d for d in devices if text.lower() in d.lower()]
    present = bool(matches)
    hit = present if head == "plugged" else not present
    return _state_signal(EVENT_USBPLUG, f"{head}:{text}", ("present", text.lower(), present), hit,
                         (f"{matches[0]} is plugged in" if present else f"no USB device matching {text!r} is plugged in"),
                         {"matches": matches}, moment)


def read_btconnect(source: str, now: Optional[float] = None) -> EventSignal:
    """connected:NAME or disconnected:NAME, from bluetoothctl (NAME or MAC address)."""
    moment = time.time() if now is None else now
    head, name = _source_parts(source)  # a MAC address keeps its colons: only the first one splits
    subject = f"{EVENT_BTCONNECT}:{source.strip()}"
    if head not in ("connected", "disconnected") or not name:
        return _unavailable(EVENT_BTCONNECT, subject, f"source must be connected:NAME or disconnected:NAME, not {source!r}")
    if shutil.which("bluetoothctl") is None:
        return _unavailable(EVENT_BTCONNECT, subject, "bluetoothctl is not installed")
    proc = _run_argv(["bluetoothctl", "devices", "Connected"])
    if proc is None or proc.returncode != 0:
        return _unavailable(EVENT_BTCONNECT, subject, "bluetoothd did not answer (is the bluetooth service running?)")
    connected = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == "Device":
            connected.append((parts[1], parts[2].strip()))
    up = any(name.lower() in (mac.lower(), label.lower()) or name.lower() in label.lower() for mac, label in connected)
    hit = up if head == "connected" else not up
    return _state_signal(EVENT_BTCONNECT, f"{head}:{name}", ("connected", name.lower(), up), hit,
                         f"{name} is {'connected' if up else 'not connected'}",
                         {"connected": [label for _, label in connected]}, moment)


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SCHEDULE_GRACE_MINUTES = 15.0


def parse_schedule(source: str) -> "tuple[Optional[tuple], str]":
    """('days', {weekday...}, hour, minute) | ('hourly', minute) | ('every', minutes), or (None, why)."""
    text = " ".join(source.strip().lower().split())
    m = re.fullmatch(r"every (\d{1,4}) ?(?:m|min|mins|minutes?)", text)
    if m:
        n = int(m.group(1))
        return ((("every", n), "") if 5 <= n <= 1440 else (None, "every N minutes needs N from 5 to 1440"))
    m = re.fullmatch(r"hourly(?: (?:at )?:?(\d{1,2}))?", text)
    if m:
        minute = int(m.group(1) or 0)
        return (("hourly", minute), "") if minute < 60 else (None, "hourly :MM needs a minute below 60")
    m = re.fullmatch(r"(daily|every day|weekdays|weekends|[a-z,\- ]+?) (?:at )?(\d{1,2}):(\d{2})", text)
    if not m:
        return None, ("use 'daily 08:00', 'weekdays 18:30', 'weekends 10:00', 'mon,wed,fri 07:15', "
                      "'hourly :05' or 'every 30 minutes'")
    which, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
    if hour > 23 or minute > 59:
        return None, f"{hour:02d}:{minute:02d} is not a time of day"
    if which in ("daily", "every day"):
        days = set(range(7))
    elif which == "weekdays":
        days = set(range(5))
    elif which == "weekends":
        days = {5, 6}
    else:
        days = set()
        for part in re.split(r"[ ,]+", which):
            if "-" in part:
                a, _, b = part.partition("-")
                if a[:3] not in _DAYS or b[:3] not in _DAYS:
                    return None, f"{part!r} is not a range of days"
                i, j = _DAYS.index(a[:3]), _DAYS.index(b[:3])
                days.update(range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1)))
            elif part[:3] in _DAYS:
                days.add(_DAYS.index(part[:3]))
            elif part:
                return None, f"{part!r} is not a day of the week"
    return ("days", frozenset(days), hour, minute), ""


def last_occurrence(spec: tuple, moment: float) -> float:
    """The latest scheduled instant at or before `moment`, in local time."""
    if spec[0] == "every":
        step = spec[1] * 60
        return moment - (moment % step)
    lt = time.localtime(moment)
    if spec[0] == "hourly":
        candidate = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, spec[1], 0, 0, 0, -1))
        return candidate if candidate <= moment else candidate - 3600
    _, days, hour, minute = spec
    for back in range(0, 8):
        day = time.localtime(moment - back * 86400)
        candidate = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, hour, minute, 0, 0, 0, -1))
        if day.tm_wday in days and candidate <= moment:
            return candidate
    return float("-inf")


def read_schedule(source: str, grace_minutes: float = SCHEDULE_GRACE_MINUTES,
                  now: Optional[float] = None) -> EventSignal:
    """A calendar time arriving. Late by more than the grace (the machine slept), it is skipped, not replayed."""
    moment = time.time() if now is None else now
    subject = f"{EVENT_SCHEDULE}:{' '.join(source.strip().lower().split())}"
    spec, why = parse_schedule(source)
    if spec is None:
        return _unavailable(EVENT_SCHEDULE, subject, why)
    grace = max(1.0, min(float(grace_minutes), 240.0)) * 60
    occurrence = last_occurrence(spec, moment)
    late = moment - occurrence
    hit = 0 <= late <= grace
    when = time.strftime("%a %H:%M", time.localtime(occurrence)) if occurrence > float("-inf") else "never"
    summary = (f"it is time: {source.strip()} ({when})" if hit else
               f"{source.strip()} last came round at {when}" + (" - too long ago to act on" if late > grace else ""))
    return _state_signal(EVENT_SCHEDULE, subject.split(":", 1)[1], ("at", occurrence), hit, summary,
                         {"occurrence": occurrence, "late_seconds": late}, moment)


def _suspended_seconds() -> "Optional[float]":
    """Seconds this boot has spent suspended: BOOTTIME counts suspend, MONOTONIC does not."""
    try:
        # never below zero: never-suspended, the two reads differ by microseconds either way
        return max(0.0, time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC))
    except (AttributeError, OSError):
        return None


def read_sleepwake(source: str, now: Optional[float] = None) -> EventSignal:
    """'resumed': fires once after each resume. Polling cannot see the brief
    PreparingForSleep window, but the suspended-time counter only ever grows
    while asleep, so each resume is a new fingerprint."""
    moment = time.time() if now is None else now
    want = source.strip().lower()
    subject = f"{EVENT_SLEEPWAKE}:{want}"
    if want != "resumed":
        return _unavailable(EVENT_SLEEPWAKE, subject, f"source must be 'resumed', not {source!r}")
    slept = _suspended_seconds()
    if slept is None:
        return _unavailable(EVENT_SLEEPWAKE, subject, "this kernel exposes no CLOCK_BOOTTIME")
    bucket = int(slept // 5)  # 5 s grain: the two clocks are read a few microseconds apart
    return _state_signal(EVENT_SLEEPWAKE, want, ("slept", bucket), True,
                         f"the machine resumed (suspended {slept / 60:.0f} min in total this boot)",
                         {"suspended_seconds": round(slept)}, moment)


def _audio_nodes() -> "Optional[list[str]]":
    proc = _run_argv(["pw-dump", "--no-colors"], timeout=10)
    if proc is None or proc.returncode != 0:
        return None
    try:
        objects = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    out = []
    for obj in objects:
        props = ((obj.get("info") or {}).get("props") or {}) if isinstance(obj, dict) else {}
        kind = props.get("media.class", "")
        if kind in ("Audio/Sink", "Audio/Source"):
            label = props.get("node.description") or props.get("node.nick") or props.get("node.name") or "?"
            out.append(f"{'output' if kind == 'Audio/Sink' else 'input'}: {label}")
    return sorted(out)


def read_audiodevice(source: str, now: Optional[float] = None) -> EventSignal:
    """any, added:NAME or removed:NAME - outputs and inputs PipeWire knows, by description."""
    moment = time.time() if now is None else now
    head, text = _source_parts(source)
    subject = f"{EVENT_AUDIODEVICE}:{source.strip()}"
    if head not in ("any", "added", "removed") or (head != "any") != bool(text):
        return _unavailable(EVENT_AUDIODEVICE, subject, f"source must be any, added:NAME or removed:NAME, not {source!r}")
    if shutil.which("pw-dump") is None:
        return _unavailable(EVENT_AUDIODEVICE, subject, "pw-dump (pipewire) is not installed")
    nodes = _audio_nodes()
    if nodes is None:
        return _unavailable(EVENT_AUDIODEVICE, subject, "PipeWire did not answer (no audio session running?)")
    if head == "any":
        return _state_signal(EVENT_AUDIODEVICE, "any", ("set", tuple(nodes)), True,
                             "audio devices now: " + ("; ".join(nodes) or "none"), {"devices": nodes}, moment)
    present = any(text.lower() in n.lower() for n in nodes)
    hit = present if head == "added" else not present
    return _state_signal(EVENT_AUDIODEVICE, f"{head}:{text}", ("present", text.lower(), present), hit,
                         f"an audio device matching {text!r} is {'present' if present else 'gone'}",
                         {"devices": nodes}, moment)


MAX_JOURNAL_PATTERN = 200


def read_journalmatch(source: str, now: Optional[float] = None) -> EventSignal:
    """PATTERN, or unit:NAME:PATTERN - the newest journal entry matching it.

    The fingerprint is that entry's cursor, so arming records the newest
    existing match as the baseline and only a later one fires. The pattern goes
    to journalctl's own --grep as one argv element - never through a shell.
    """
    moment = time.time() if now is None else now
    text = source.strip()
    subject = f"{EVENT_JOURNALMATCH}:{text}"
    unit = ""
    if text.startswith("unit:") and text.count(":") >= 2:
        _, unit, text = text.split(":", 2)
        if not re.fullmatch(r"[\w@.\-]{1,128}", unit):
            return _unavailable(EVENT_JOURNALMATCH, subject, f"{unit!r} is not a unit name")
    if not text or len(text) > MAX_JOURNAL_PATTERN:
        return _unavailable(EVENT_JOURNALMATCH, subject,
                            f"the pattern must be 1 to {MAX_JOURNAL_PATTERN} characters")
    if shutil.which("journalctl") is None:
        return _unavailable(EVENT_JOURNALMATCH, subject, "journalctl is not installed")
    argv = ["journalctl", "--no-pager", "-q", "-o", "json", "-n", "1", "-r", "--case-sensitive=false",
            f"--grep={text}", "--since=-7d"]
    if unit:
        argv += ["-u", unit]
    proc = _run_argv(argv, timeout=15)
    if proc is None:
        return _unavailable(EVENT_JOURNALMATCH, subject, "journalctl did not answer")
    err = (proc.stderr or "").lower()
    if "insufficient permissions" in err or "no journal files" in err:
        return _unavailable(EVENT_JOURNALMATCH, subject,
                            "this user cannot read the system journal (needs the wheel or systemd-journal group)")
    if proc.returncode not in (0, 1):
        return _unavailable(EVENT_JOURNALMATCH, subject, (proc.stderr or "journalctl failed").strip()[:160])
    line = (proc.stdout or "").strip().splitlines()[:1]
    if not line:
        return _state_signal(EVENT_JOURNALMATCH, subject.split(":", 1)[1], ("cursor", None), False,
                             f"no journal entry in the last 7 days matches {text!r}", {}, moment)
    try:
        entry = json.loads(line[0])
    except json.JSONDecodeError:
        return _unavailable(EVENT_JOURNALMATCH, subject, "journalctl printed something that is not JSON")
    message = str(entry.get("MESSAGE", ""))[:200]
    who = entry.get("_SYSTEMD_UNIT") or entry.get("SYSLOG_IDENTIFIER") or "?"
    return _state_signal(EVENT_JOURNALMATCH, subject.split(":", 1)[1], ("cursor", entry.get("__CURSOR")), True,
                         f"{who}: {message}", {"unit": who, "message": message}, moment)


_BUS_NAME = re.compile(r"[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+")
_OBJ_PATH = re.compile(r"/(?:[\w]+(?:/[\w]+)*)?")
_PROP = re.compile(r"[A-Za-z_]\w{0,127}")


def read_dbusprop(source: str, now: Optional[float] = None) -> EventSignal:
    """'system|session NAME PATH INTERFACE PROPERTY [=VALUE]' - one D-Bus property.

    Without =VALUE every change fires; with it, only the transition into that
    value. Reading a property is read-only by D-Bus's own rules (Get, never
    Set), and every token is validated against D-Bus's own grammar first.
    """
    moment = time.time() if now is None else now
    parts = source.split()
    subject = f"{EVENT_DBUSPROP}:{' '.join(parts)}"
    want = None
    if parts and parts[-1].startswith("="):
        want = parts.pop()[1:]
    if len(parts) != 5 or parts[0] not in ("system", "session"):
        return _unavailable(EVENT_DBUSPROP, subject,
                            "source must be 'system|session NAME PATH INTERFACE PROPERTY [=VALUE]'")
    bus, name, path, iface, prop = parts
    if not (_BUS_NAME.fullmatch(name) and _OBJ_PATH.fullmatch(path) and _BUS_NAME.fullmatch(iface)
            and _PROP.fullmatch(prop)):
        return _unavailable(EVENT_DBUSPROP, subject, "a bus name, path, interface or property is malformed")
    if shutil.which("busctl") is None:
        return _unavailable(EVENT_DBUSPROP, subject, "busctl is not installed")
    proc = _run_argv(["busctl", "--system" if bus == "system" else "--user", "--json=short",
                      "get-property", name, path, iface, prop], timeout=10)
    if proc is None or proc.returncode != 0:
        return _unavailable(EVENT_DBUSPROP, subject,
                            f"the property could not be read: {((proc.stderr if proc else '') or '').strip()[:160]}")
    try:
        value = json.loads(proc.stdout or "null").get("data")
    except (json.JSONDecodeError, AttributeError):
        return _unavailable(EVENT_DBUSPROP, subject, "busctl printed something that is not JSON")
    shown = json.dumps(value)[:120]
    hit = True if want is None else str(value).lower() == want.lower() or shown.strip('"').lower() == want.lower()
    return _state_signal(EVENT_DBUSPROP, subject.split(":", 1)[1], ("value", shown), hit,
                         f"{iface}.{prop} is {shown}", {"value": value}, moment)


def read_calendar(source: str, now: Optional[float] = None) -> EventSignal:
    """'starts-in:MINUTES' - the next timed event starting within that many minutes.

    The fingerprint is that event's identity and start, so each event fires
    once as it comes into the window; an empty window is quiet. A calendar that
    cannot be read is UNAVAILABLE, never "nothing coming up".
    """
    from shani_chronoa import eds_calendar as cal

    moment = time.time() if now is None else now
    head, rest = _source_parts(source)
    subject = f"{EVENT_CALENDAR}:{source.strip()}"
    if head != "starts-in" or not rest.isdigit() or not 1 <= int(rest) <= 240:
        return _unavailable(EVENT_CALENDAR, subject, "source must be starts-in:MINUTES (1-240), e.g. starts-in:10")
    try:
        upcoming = [e for e in cal.events_between(moment, moment + int(rest) * 60)
                    if not e.all_day and e.start >= moment]
    except cal.CalendarUnavailable as exc:
        return _unavailable(EVENT_CALENDAR, subject, str(exc))
    nxt = upcoming[0] if upcoming else None
    state = (nxt.uid, nxt.start) if nxt else None
    summary = (f"{nxt.summary} starts in {max(0, int((nxt.start - moment) // 60))} min"
               + (f" @ {nxt.location}" if nxt.location else "")) if nxt else f"nothing starts in the next {rest} min"
    return _state_signal(EVENT_CALENDAR, f"starts-in:{rest}", ("next", state), nxt is not None, summary,
                         {"event": nxt._asdict() if nxt else None}, moment)


def read_phone(source: str, now: Optional[float] = None) -> EventSignal:
    """connected[:NAME], disconnected[:NAME] or battery-below:N - the paired phone, via GSConnect/KDE Connect."""
    from shani_chronoa import phone as ph

    moment = time.time() if now is None else now
    head, rest = _source_parts(source)
    subject = f"{EVENT_PHONE}:{source.strip()}"
    if head not in ("connected", "disconnected", "battery-below") or \
            (head == "battery-below" and not (rest.isdigit() and 1 <= int(rest) <= 99)):
        return _unavailable(EVENT_PHONE, subject,
                            "source must be connected[:NAME], disconnected[:NAME] or battery-below:N (1-99)")
    try:
        devs = [d for d in ph.devices() if d.paired]
    except ph.PhoneUnavailable as exc:
        return _unavailable(EVENT_PHONE, subject, str(exc))
    if head == "battery-below":
        levels = [(d, ph.battery(d)) for d in devs if d.reachable]
        levels = [(d, b) for d, b in levels if b]
        if not levels:
            return _unavailable(EVENT_PHONE, subject, "no reachable paired phone reports its battery")
        d, (pct, charging) = min(levels, key=lambda x: x[1][0])
        low = pct < int(rest) and not charging
        return _state_signal(EVENT_PHONE, f"battery-below:{rest}", ("low", d.id, low), low,
                             f"{d.name} battery is at {pct}%" + (" and charging" if charging else ""),
                             {"device": d.name, "battery": pct, "charging": charging}, moment)
    named = [d for d in devs if not rest or rest.lower() in d.name.lower()]
    if not named:
        return _unavailable(EVENT_PHONE, subject, f"no paired phone matches {rest!r}" if rest else "no phone is paired")
    up = any(d.reachable for d in named)
    hit = up if head == "connected" else not up
    who = ", ".join(d.name for d in named)
    return _state_signal(EVENT_PHONE, f"{head}:{rest}", ("reachable", rest.lower(), up), hit,
                         f"{who} is {'connected' if up else 'not connected'}", {"devices": [d.name for d in named]},
                         moment)


#: seconds of microphone per check, and the default confidence a sound must reach
SOUND_LISTEN_SECONDS = 3.0
SOUND_FLOOR = 0.3


def read_sound(source: str, now: Optional[float] = None, listen=None) -> EventSignal:
    """NAME or NAME:CONFIDENCE - a sound heard by the microphone ("doorbell", "knock", "dog", "baby:0.4").

    Each check records a few seconds, labels them with the sounds model
    (AudioSet's 527 classes) and deletes the recording; only the labels are
    kept. NAME is matched as part of a label, so "door" also matches "doorbell".
    `listen` is a seam for tests (production records the real microphone).
    """
    from shani_chronoa import sounds
    moment = time.time() if now is None else now
    head, rest = _source_parts(source)
    subject = f"{EVENT_SOUND}:{source.strip()}"
    if not head or not re.fullmatch(r"[a-z][a-z ,()'-]{1,40}", head):
        return _unavailable(EVENT_SOUND, subject, "source is the name of a sound, like doorbell, knock, dog or baby:0.4")
    try:
        floor = float(rest) if rest else SOUND_FLOOR
    except ValueError:
        return _unavailable(EVENT_SOUND, subject, f"{rest!r} is not a confidence between 0.05 and 0.95")
    if not 0.05 <= floor <= 0.95:
        return _unavailable(EVENT_SOUND, subject, "the confidence must be between 0.05 and 0.95")
    if listen is None:
        problem = sounds.problem()
        if problem:
            return _unavailable(EVENT_SOUND, subject, problem)
        listen = sounds.listen
    try:
        heard = listen(SOUND_LISTEN_SECONDS)
    except (RuntimeError, ValueError, OSError) as exc:
        return _unavailable(EVENT_SOUND, subject, str(exc))
    found = sounds.matches(heard, head, floor)
    summary = (f"heard {found.name.lower()} ({found.probability:.0%})" if found
               else f"no {head} heard (heard: {sounds.describe(heard)})")
    return _state_signal(EVENT_SOUND, f"{head}:{floor:g}", ("heard", found is not None), found is not None, summary,
                         {"heard": [h._asdict() for h in heard[:5]]}, moment)
