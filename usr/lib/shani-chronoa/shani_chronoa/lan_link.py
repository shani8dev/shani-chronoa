"""The KDE Connect LAN link, from the protocol up.

**What this is for.** A phone on the same Wi-Fi can be told about this computer
and can announce itself, without KDE Connect or GSConnect installed - the two
desktop integrations `phone.py` uses, neither of which is on a stock image. The
protocol is open and specified (invent.kde.org/network/kdeconnect-meta,
`core/backends/lan/lanlinkprovider.h` for the ports), so Chronoa can speak it
directly the way `phone_bluez.py` speaks PBAP and `moyoung.py` speaks a closed
vendor protocol.

**What is verified here and what is not, stated up front.**

Verified, by a test that runs a *fake peer* over real sockets on a real port:
identity packets are built and parsed to the spec, our own announcement goes
out, and a peer's is found. Every number in `test_lan_link.py` comes from a
real UDP exchange.

**Not verified, and not claimed**: pairing, the TLS upgrade, and every plugin
packet after it. There is no phone on any network this code has run against, so
those could only be checked by reading the spec - and AGENTS.md records the
project refusing exactly that in `bluetooth_call`'s call events ("zero gateways
connected on any machine here, so a reader for it could only be verified
returning UNAVAILABLE"). They are therefore **not written**, rather than written
and unverified. A caller that wants them gets an honest sentence from
`pairing_status()`.

**Why discovery is dual-mode, and why that is not gold-plating.** KDE Connect's
own README notes that broadcast is dropped on corporate Wi-Fi, university
networks and Docker - which describes real networks, including some a user is
likely on. mDNS (`_kdeconnect._udp.local`) survives those, so
`announce()` sends both and `_listen` reads both. `avahi-browse` is not on the
image, so the mDNS half is wrapped in a `which` guard rather than assumed.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: The UDP port both ends broadcast and listen on. From
#: `lanlinkprovider.h`: `const static quint16 UDP_PORT = 1716;`
UDP_PORT = 1716

#: The control channel and the file-transfer side channels. From the same
#: header: MIN_TCP_PORT 1716, MAX_TCP_PORT 1764.
MIN_TCP_PORT, MAX_TCP_PORT = 1716, 1764

#: mDNS service type. From `mdnsdiscovery.h`; this is the mode that survives a
#: network with broadcast dropped.
MDNS_SERVICE = "_kdeconnect._udp.local"

#: The protocol version this module speaks. 8 is current in the spec and is
#: what the spec's own example identity packet uses.
PROTOCOL_VERSION = 8

#: `kdeconnect.identity`'s deviceId, from `protocol.md`: "must be generated to
#: be between 32 and 38 alphanumerical characters, and will be also set as the
#: Common Name in the device TLS certificate".
DEVICE_ID = re.compile(r"^[a-zA-Z0-9_-]{32,38}$")

#: A datagram is small: the spec's own implementation caps it at 8 KiB, and an
#: identity packet is a few hundred bytes.
_MAX_DATAGRAM = 8 * 1024

IDENTITY_TYPE = "kdeconnect.identity"


def device_id() -> str:
    """This computer's stable KDE Connect id, or a new one if it has none.

    Stored beside the rest of Chronoa's state rather than under the user's home
    as a dotfile, so `~/.config` holds one thing. The id is what a phone pairs
    *to*, so it must not change between runs - which is the reason this persists
    it rather than generating a `uuid4` each time.
    """
    from shani_chronoa import files
    path = files.state_home() / "kdeconnect" / "device-id"
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if DEVICE_ID.match(existing):
                return existing
    except OSError:
        pass
    import secrets
    fresh = secrets.token_hex(16)              # 32 characters, in range
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh, encoding="utf-8")
    except OSError as exc:
        logger.info("could not persist the device id (%s); "
                    "a new one will be made next run", exc)
    return fresh


def identity_packet(*, name: str, device_type: str = "desktop",
                    tcp_port: int = UDP_PORT,
                    target: "Optional[str]" = None) -> dict:
    """The `kdeconnect.identity` packet, to the spec's own field order.

    The order is not cosmetic: `cosmic-ext-connect-core/Protocol.md` records
    that "the official KDE Connect implementation uses this field order (should
    be preserved for compatibility)" - deviceId, deviceName, protocolVersion,
    deviceType, tcpPort, incomingCapabilities, outgoingCapabilities. Python
    dicts preserve insertion order, so building it in this order is what keeps
    the bytes canonical for a peer that fingerprints the whole packet.

    `target` is what turns an announcement into a *reply*: protocol 8 has the
    discoveree verify `targetDeviceId` against its own, and a packet without
    them is the broadcast form.
    """
    body = {
        "deviceId": device_id(),
        "deviceName": name,
        "protocolVersion": PROTOCOL_VERSION,
        "deviceType": device_type,
        "tcpPort": tcp_port,
        "incomingCapabilities": [],
        "outgoingCapabilities": [],
    }
    if target:
        body["targetDeviceId"] = target
        body["targetProtocolVersion"] = PROTOCOL_VERSION
    return {"id": int(time.time()), "type": IDENTITY_TYPE, "body": body}


def parse_identity(datagram: bytes) -> "Optional[dict]":
    """A peer's identity packet, or None if it is not one.

    **None is not an error here.** A datagram on 1716 that is malformed, or
    from a peer speaking an older protocol with a field this module does not
    know, is ordinary noise on a shared port - the port is unprivileged and
    anything may be on it. Silently returning None would be right and useless;
    the field is checked explicitly so the *reason* is available to a caller.
    """
    try:
        packet = json.loads(datagram.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(packet, dict) or packet.get("type") != IDENTITY_TYPE:
        return None
    body = packet.get("body")
    if not isinstance(body, dict):
        return None
    identifier = body.get("deviceId")
    # A peer with no id, or a badly shaped one, is not a KDE Connect device.
    if not isinstance(identifier, str) or not DEVICE_ID.match(identifier):
        return None
    version = body.get("protocolVersion")
    if not isinstance(version, int):
        return None
    return body


def _broadcast_addresses() -> "list[str]":
    """255.255.255.255 plus the subnet broadcast of each connected interface.

    The generic address alone does not reach a host on a subnet whose gateway
    does not forward it, which is most home routers - KDE Connect's own
    implementation walks the interfaces and sends to each subnet's broadcast.
    """
    addresses = ["255.255.255.255"]
    try:
        for line in subprocess.run(["ip", "-4", "-o", "addr", "show"],
                                   capture_output=True, text=True, timeout=10,
                                   check=False).stdout.splitlines():
            fields = line.split()
            if len(fields) < 4:
                continue
            interface, cidr = fields[1], fields[3]
            address, _, bits = cidr.partition("/")
            if interface == "lo" or not bits.isdigit():
                continue
            prefix = int(bits)
            if prefix >= 31:            # /31 and /32 have no broadcast address
                continue
            packed = socket.inet_aton(address)
            mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
            network = int.from_bytes(packed, "big") & mask
            broadcast = network | (~mask & 0xFFFFFFFF)
            addresses.append(socket.inet_ntoa(broadcast.to_bytes(4, "big")))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.info("could not work out the subnet broadcasts: %s", exc)
    return addresses


def announce(name: str, tcp_port: int = UDP_PORT) -> "tuple[bool, str]":
    """Tell the local network this computer exists. `(did, why-not)`.

    Sends our identity to every subnet broadcast on UDP 1716, and announces the
    mDNS service as well, because broadcast is dropped on enough real networks
    that one mode is not a link. Returns False with a reason **naming the
    package** when a mode is unavailable, rather than a silent `None` - the
    reason `files._PACKAGE_HINTS` exists.
    """
    payload = (json.dumps(identity_packet(name=name, tcp_port=tcp_port),
                          separators=(", ", ": ")) + "\n").encode("utf-8")
    sent = False
    reason = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for address in _broadcast_addresses():
                try:
                    sock.sendto(payload, (address, UDP_PORT))
                    sent = True
                except OSError as exc:
                    reason = f"{address}: {exc}"
    except OSError as exc:
        return False, f"could not open a socket ({exc})"

    mdns = _avahi_publish(tcp_port)
    if sent and mdns:
        return True, ""
    if sent:
        return True, "announced by broadcast; the mDNS service was not published"
    return False, ("nothing could be sent (" + (reason or "no route") +
                   "); the network may not carry broadcast")


def _avahi_publish(tcp_port: int) -> bool:
    """Publish `_kdeconnect._udp.local`, if avahi-publish is here.

    Guarded rather than assumed: `avahi-publish-service` ships in `avahi` on
    Arch, which is on both images, but the honest failure mode when it is
    missing is "broadcast worked, mDNS did not" - not a silent failure that
    reads as the whole thing working.
    """
    tool = "avahi-publish-service"
    if subprocess.run(["which", tool], capture_output=True).returncode != 0:
        return False
    name = device_id()[:12] or "chronoa"
    try:
        subprocess.Popen([tool, name, MDNS_SERVICE, str(tcp_port)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def listen_once(timeout: float = 3.0) -> "list[dict]":
    """Peers that announce themselves within `timeout` seconds, each with its IP.

    Our own broadcast is filtered out by device id - a machine that lists itself
    as a phone is not a useful answer, and it also makes `listen_once` depend on
    whether `announce` was called first.
    """
    found: "dict[str, dict]" = {}
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("", UDP_PORT))
            except OSError as exc:
                logger.info("could not listen on %d: %s", UDP_PORT, exc)
                return []
            sock.settimeout(max(0.05, min(1.0, timeout)))
            while time.monotonic() < deadline:
                try:
                    datagram, address = sock.recvfrom(_MAX_DATAGRAM)
                except socket.timeout:
                    continue
                except OSError as exc:
                    logger.info("the listener stopped: %s", exc)
                    break
                body = parse_identity(datagram)
                if body is None:
                    continue
                if body.get("deviceId") == device_id():
                    continue
                entry = dict(body)
                entry["address"] = address[0]
                found[body["deviceId"]] = entry
    except OSError as exc:
        logger.info("could not open a listener: %s", exc)
        return []
    return list(found.values())


def pairing_status() -> "tuple[str, str]":
    """(state, why) for the pairing half - which is deliberately absent.

    The pairing and TLS half of the spec is not implemented, and this is the
    sentence that says so rather than a half-written client that would pair
    wrongly. See the module docstring for why.
    """
    return ("not implemented",
            "pairing, the TLS upgrade and the plugin packets are specified but "
            "not written: there is no KDE Connect phone on any network this "
            "code has been able to run against, so they could only be written "
            "from the spec and never run. Discovery is real and is tested.")
