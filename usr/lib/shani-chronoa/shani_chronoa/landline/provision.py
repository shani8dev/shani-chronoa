"""Adding this computer to the JioFiber line, the way JioJoin adds a phone.

The router owns the landline number and keeps a list of devices allowed to
use it. Adding one is the owner's decision, made with a one-time code the
router has Jio text to the account's registered mobile:

1. `account(host)` - `http://<router>:8080/request_account`: the line's number.
2. `start_add(...)` - ask the router (HTTPS, port 8443) to add this device;
   it sends the code by SMS and answers with a session cookie.
3. `confirm(...)` - send the code back with that cookie.
4. `credentials(...)` - the device's SIP identity and password. The password
   changes on every fetch, so it is fetched each time the line starts and is
   never stored.

**Always with the code.** The request says `nwk_intf=wifi`, the value under
which the router asks for one. (A community write-up describes a value that
skips it - which would let anyone on the network take the line. Not used, and
`test_landline.py` holds that.)

The device is named `Chronoa-<hostname>`, and its MAC-like id is derived from
that name the way the JioJoin desktop clients derive theirs (a djb2-style
hash), so it is the same device on every start.

Reference, read not run: github.com/sidhantjohnaind/sipserver (MIT),
create_env_jfibersip.py.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Tuple

ROUTER_NAME = "jiofiber.local.html"
ACCOUNT_PORT = 8080
IMS_PORT = 8443
SIP_PORT = 5068
TIMEOUT = 8


class ProvisionError(RuntimeError):
    pass


# --- where the router is ------------------------------------------------------

def _gateway() -> str:
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[1] == "00000000" and int(parts[3], 16) & 2:
                    return socket.inet_ntoa(bytes.fromhex(parts[2])[::-1])
    except (OSError, IndexError, ValueError):
        pass
    return ""


def find_router() -> str:
    """The router's IPv4 address: its own name first (what JioJoin looks up), then the gateway."""
    try:
        return socket.getaddrinfo(ROUTER_NAME, None, socket.AF_INET)[0][4][0]
    except (OSError, IndexError):
        return _gateway()


# --- the device's identity -------------------------------------------------------

def _hash(text: str) -> int:
    h = 0
    for b in text.encode():
        h = (h * 33 + b) & 0xFFFFFFFF
    return h


def device_id(alias: str) -> Tuple[str, str]:
    """(mac, instance hex) for `alias` - stable, so the router sees one device."""
    hex8 = f"{_hash(alias):08X}"
    swapped = "".join(reversed([hex8[i:i + 2] for i in range(0, 8, 2)]))
    mac = ":".join(swapped.zfill(12).lower()[i:i + 2] for i in range(0, 12, 2))
    return mac, swapped.zfill(8)


def default_alias() -> str:
    host = "".join(ch for ch in socket.gethostname() if ch.isalnum())[:16] or "PC"
    return f"Chronoa{host}"


# --- HTTP, as the router speaks it ------------------------------------------------

def _context() -> ssl.SSLContext:
    # The router's certificate is its own, self-signed; there is no name to check.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(host: str, port: int, path: str, tls: bool, cookie: str = "") -> Tuple[int, Dict[str, str], str]:
    """A raw GET: the router is particular about its request line, so no client library.

    The router resets a connection now and then (measured on a live JIDU6811:
    the first TLS attempt reset, the next answered 200), so a reset or an empty
    read is retried once before giving up.
    """
    data = b""
    for attempt in range(3):
        raw = socket.create_connection((host, port), timeout=TIMEOUT)
        sock = _context().wrap_socket(raw, server_hostname=host) if tls else raw
        try:
            lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Connection: close"]
            if cookie:
                lines.append(f"Cookie: {cookie}")
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (ConnectionResetError, ssl.SSLError, socket.timeout):
            data = b""
        finally:
            sock.close()
        if data.startswith(b"HTTP/"):
            break
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    try:
        status = int(lines[0].split(" ")[1])
    except (IndexError, ValueError):
        raise ProvisionError("the router answered with something that is not HTTP")
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers.setdefault(k.strip().lower(), v.strip())
    if "chunked" in headers.get("transfer-encoding", ""):
        body = _dechunk(body)
    return status, headers, body.decode("utf-8", "replace")


def _dechunk(body: bytes) -> bytes:
    out = b""
    while body:
        size_line, _, rest = body.partition(b"\r\n")
        size = int(size_line.split(b";")[0] or b"0", 16)
        if size == 0:
            break
        out += rest[:size]
        body = rest[size + 2:]
    return out


def _ims_query(alias: str, mac: str, add: bool) -> str:
    params = {
        "terminal_sw_version": "RCSAndrd", "terminal_vendor": alias, "terminal_model": alias,
        "SMS_port": 0, "act_type": "volatile", "IMSI": "", "msisdn": "", "IMEI": "", "vers": 0,
        "token": "", "rcs_state": 0, "rcs_version": "5.1B", "rcs_profile": "joyn_blackbird",
        "client_vendor": "JUIC", "default_sms_app": 2, "default_vvm_app": 0, "device_type": "vvm",
        "client_version": "JSEAndrd-1.0", "mac_address": mac, "alias": alias,
        # The value under which the router asks for the owner's code. Never another.
        "nwk_intf": "wifi",
    }
    if add:
        params["op_type"] = "add"
    return "/?" + "&".join(f"{k}={urllib.parse.quote(str(v), safe=':')}" for k, v in params.items())


# --- the four steps -----------------------------------------------------------------

def account(host: str) -> Dict[str, str]:
    try:
        status, _h, body = _get(host, ACCOUNT_PORT, "/request_account", tls=False)
    except OSError as exc:
        raise ProvisionError(f"the router at {host} did not answer on port {ACCOUNT_PORT} ({exc}) - "
                             "is this computer on the JioFiber network?")
    if status != 200:
        raise ProvisionError(f"the router refused the account request (HTTP {status})")
    try:
        return json.loads(body)
    except ValueError:
        raise ProvisionError("the router's account answer was not JSON")


def start_add(host: str, alias: str) -> Tuple[str, str]:
    """Ask the router to add this device; Jio texts the owner a code. (cookie, masked number)."""
    mac, _ = device_id(alias)
    try:
        status, headers, body = _get(host, IMS_PORT, _ims_query(alias, mac, add=True), tls=True)
    except OSError as exc:
        raise ProvisionError(f"the router did not answer on port {IMS_PORT} ({exc})")
    if status != 200:
        raise ProvisionError(f"the router would not start adding this device (HTTP {status}"
                             f"{': ' + body.strip()[:120] if body.strip() else ''})")
    cookie = "; ".join(part.split(";", 1)[0] for part in [headers.get("set-cookie", "")] if part)
    number = headers.get("x-amn", "")
    return cookie, (f"...{number[-4:]}" if len(number) >= 4 else number)


def confirm(host: str, code: str, cookie: str) -> bool:
    code = "".join(ch for ch in code if ch.isdigit())
    if not code:
        return False
    status, _h, _b = _get(host, IMS_PORT, f"/?OTP={code}", tls=True, cookie=cookie)
    return status == 200


def credentials(host: str, alias: str) -> Dict[str, str]:
    """This device's SIP identity and a fresh password; raises when it is not on the list."""
    mac, _ = device_id(alias)
    status, _h, body = _get(host, IMS_PORT, _ims_query(alias, mac, add=False), tls=True)
    if status != 200:
        raise ProvisionError(f"the router did not hand over this device's line (HTTP {status}) - "
                             "it may need adding again")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise ProvisionError("the router's line settings were not XML")
    values = {p.get("name"): p.get("value") for p in root.iter("parm") if p.get("name")}
    password = values.get("userpwd") or ""
    if not password:
        raise ProvisionError("the router's line settings had no password - this device is not added yet")
    realm = values.get("realm") or values.get("home_network_domain_name") or "br.wln.ims.jio.com"
    user = values.get("private_user_identity") or values.get("username") or ""
    for prefix in ("sips:", "sip:"):
        if user.startswith(prefix):
            user = user[len(prefix):]
    public = values.get("public_user_identity") or ""
    return {"realm": realm, "user": user, "password": password, "public": public}


# --- what is kept between starts (no password) ------------------------------------------

def state_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "shani-chronoa" / "landline.json"


def load() -> Dict[str, str]:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}


def save(state: Dict[str, str]) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v for k, v in state.items() if k != "password"}, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(path)
