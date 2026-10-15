"""Connect an Android phone to adb - by USB, or by Wi-Fi with wireless debugging.

**Why this exists.** `skills/android_device` and the Android panel work on a
phone adb can already reach, and said "connect it with USB debugging" with no
help doing it. The two ways in are both adb's own:

- **USB.** Plug in, turn on USB debugging, tap *Allow* on the phone. adb shows
  the phone as `unauthorized` until the tap, then `device`. Nothing to send -
  this only watches `adb devices -l` and says which step is next.
- **Wi-Fi (Android 11+, "Wireless debugging").** Pairing is a one-time
  exchange, then a connection:
  1. *QR code* - what Android Studio does. The computer shows
     `WIFI:T:ADB;S:<name>;P:<password>;;`; the phone scans it and advertises
     `_adb-tls-pairing._tcp` under `<name>` on mDNS; the computer finds that
     and runs `adb pair <ip:port> <password>`.
  2. *Pairing code* - the phone shows an address and a six-digit code;
     `adb mdns services` usually finds the address, and the person types the
     code.
  Either way the phone then advertises `_adb-tls-connect._tcp` and
  `adb connect` to it makes the phone a `device`. A paired phone reconnects
  later without pairing again.

Everything here is a short `adb` call on the caller's thread - the window runs
them off the main loop. The test double is `tools/fake_adb.py`, put first on
PATH; no test talks to a real phone.
"""
from __future__ import annotations

import re
import secrets
import shutil
import string
import subprocess
import time
from typing import Dict, List, Optional, Tuple

TIMEOUT = 20
PAIRING = "_adb-tls-pairing._tcp"
CONNECT = "_adb-tls-connect._tcp"


def available() -> bool:
    return shutil.which("adb") is not None


def _adb(*args: str, timeout: int = TIMEOUT) -> "Tuple[int, str]":
    try:
        proc = subprocess.run(["adb", *args], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, "adb (android-tools) is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"adb {args[0]} did not answer in {timeout}s"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def devices() -> List[Dict[str, str]]:
    """`adb devices -l` as dicts: serial, state, model, and how it is attached."""
    code, out = _adb("devices", "-l")
    if code != 0:
        return []           # adb's complaint is not a list of phones
    found = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("List of devices", "*")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        info = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        serial = parts[0]
        found.append({
            "serial": serial, "state": parts[1],
            "model": info.get("model", "").replace("_", " "),
            "via": "usb" if "usb" in info or not re.search(r"[:.]\d+$|_adb-tls-", serial)
                   else "wifi",
        })
    return found


#: What each adb state asks of the person, in the order the USB steps happen.
STATE_STEP = {
    "unauthorized": "Tap Allow on the phone (\"Allow USB debugging?\").",
    "authorizing": "Tap Allow on the phone (\"Allow USB debugging?\").",
    "offline": "The phone stopped answering - unplug it and plug it in again.",
    "no permissions": "This computer cannot open the USB device - try another cable or port.",
    "device": "Connected.",
}


def mdns_services() -> List[Dict[str, str]]:
    """What phones advertise for adb on this network: name, kind, address.

    `kind` is "pairing" (a phone showing a pairing code or scanning our QR) or
    "connect" (a phone with wireless debugging on, ready for `adb connect`).
    """
    code, out = _adb("mdns", "services", timeout=10)
    if code != 0:
        return []
    found = []
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s+(_adb-tls-(?:pairing|connect)\._tcp)\.?\s+(\S+:\d+)\s*$",
                     line.strip())
        if m:
            found.append({"name": m.group(1),
                          "kind": "pairing" if m.group(2) == PAIRING else "connect",
                          "address": m.group(3)})
    return found


def qr_secret() -> "Tuple[str, str, str]":
    """(service name, password, QR text) for "Pair device with QR code"."""
    name = "chronoa-" + "".join(secrets.choice(string.ascii_lowercase + string.digits)
                                for _ in range(6))
    password = "".join(secrets.choice(string.digits) for _ in range(8))
    return name, password, f"WIFI:T:ADB;S:{name};P:{password};;"


def qr_png(text: str) -> Optional[bytes]:
    """The QR code as PNG bytes, or None without `qrencode`."""
    if shutil.which("qrencode") is None:
        return None
    try:
        proc = subprocess.run(["qrencode", "-s", "8", "-m", "2", "-o", "-", "--", text],
                              capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def pair(address: str, code: str) -> "Tuple[bool, str]":
    """`adb pair <ip:port> <code>`. (ok, sentence)."""
    address, code = address.strip(), re.sub(r"\s", "", code or "")
    if not re.match(r"^[\w.\-\[\]:]+:\d+$", address):
        return False, "that is not an address - it looks like 192.168.1.20:37099"
    if not code.isdigit():
        return False, "the pairing code is the digits the phone shows"
    rc, out = _adb("pair", address, code, timeout=30)
    if rc == 0 and "Successfully paired" in out:
        return True, "paired"
    if "Wrong password" in out or "password" in out.lower():
        return False, "the code did not match - check it on the phone and type it again"
    return False, _last_line(out) or "the phone did not answer"


def connect(address: str) -> "Tuple[bool, str]":
    """`adb connect <ip:port>`. (ok, sentence)."""
    rc, out = _adb("connect", address.strip(), timeout=20)
    low = out.lower()
    if rc == 0 and ("connected to" in low) and "failed" not in low and "cannot" not in low:
        return True, "connected"
    return False, _last_line(out) or "the phone did not answer"


def wait_for_pairing_service(name: str, timeout: float = 120.0,
                             cancelled=lambda: False) -> Optional[str]:
    """The address of the phone that scanned our QR (`name`), or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not cancelled():
        for service in mdns_services():
            if service["kind"] == "pairing" and service["name"] == name:
                return service["address"]
        time.sleep(1.0)
    return None


def connect_after_pairing(paired_address: str, timeout: float = 20.0,
                          cancelled=lambda: False) -> "Tuple[bool, str]":
    """Find the paired phone's connect service (same IP) and `adb connect`.

    The connect port is not the pairing port; the phone advertises it once
    wireless debugging is on. Matching by IP is what Android Studio does.
    """
    host = paired_address.rsplit(":", 1)[0]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not cancelled():
        for service in mdns_services():
            if service["kind"] == "connect" and service["address"].rsplit(":", 1)[0] == host:
                return connect(service["address"])
        time.sleep(1.0)
    return False, ("paired, but the phone is not advertising a connection - keep "
                   "Wireless debugging open on it, or type the address it shows "
                   "under \"IP address & Port\"")


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
