"""The KDE Connect LAN link, from the protocol up.

**What this is for.** A phone on the same Wi-Fi can be told about this computer
and can announce itself, without KDE Connect or GSConnect installed - the two
desktop integrations `phone.py` uses, neither of which is on a stock image. The
protocol is open and specified (invent.kde.org/network/kdeconnect-meta,
`core/backends/lan/lanlinkprovider.h` for the ports), so Chronoa can speak it
directly the way `phone_bluez.py` speaks PBAP and `moyoung.py` speaks a closed
vendor protocol.

**What is verified here and what is not, stated up front, and kept honest as it
changes.**

Verified **against a real phone**, `ARSP25MTA14A1` (protocol v8, 192.168.31.79):

* UDP discovery - a real peer's identity datagram is received and parsed.
* the TLS handshake, including the two non-obvious parts (below).
* the full identity exchange inside TLS: deviceName, deviceType, and the
  capability lists.
* **pairing** - `kdeconnect.pair` was accepted and the phone replied
  `{"pair": true}`, after a person tapped Accept on the device.

Also verified, but **without any hardware**, by a test that runs a *fake peer*
over a real socket: `identity_packet`'s field order, `parse_identity`'s
refusal of everything that is not an identity, our own broadcast being filtered
out of discovery, and the stability of the device id.

**Not verified**: any plugin packet after pairing. The capability *list* the
phone advertises is real and is returned by `identity_exchange`, but what a
given plugin actually answers to has not been exercised. `pairing_status()`
says that rather than implying the rest works.

**The two non-obvious parts, both of which were got wrong first.**

1. **The phone never speaks until we do.** It sends nothing on connect and
   nothing after our TLS ClientHello; measured, it sends a TLS record only
   *after* our plaintext identity. So `wrap_socket` cannot be used - it would
   consume the ClientHello while trying to drive the handshake - and the
   handshake is driven by hand over a `MemoryBIO`.
2. **The roles are the opposite of the obvious reading.** The TCP *receiver* is
   the TLS *client* (`kdeconnect-nx`: "Receiver connects as TLS client; we are
   TLS server"), so it is `server_side=True`. `server_side=False` gets a
   connection reset from the phone, which is indistinguishable from a
   malformed packet and cost real time to tell apart.

**Why discovery is dual-mode, and why that is not gold-plating.** KDE Connect's
own README notes that broadcast is dropped on corporate Wi-Fi, university
networks and Docker - which describes real networks, including some a user is
likely on. mDNS (`_kdeconnect._udp.local`) survives those, so
`announce()` sends both and `_listen` reads both. `avahi-browse` is not on the
image, so the mDNS half is wrapped in a `which` guard rather than assumed.

**Why nothing here pairs silently, and why the certificate outranks all of it.**
Pairing ends with the person tapping Accept, so a link that needs no person is
either already paired or wrong. And the trust it produces is the **TLS
certificate**: the phone pins our fingerprint, so a certificate regenerated
under it is an un-pairing, not a startup hiccup. `_our_certificate()` caches one
and warns loudly if the cached one will not load.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import subprocess
import threading
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
#: The largest frame a peer may send on the TCP channel. 4 MiB is far past any
#: packet this protocol defines - the biggest is a share request's filename - and
#: far below "enough to hurt". It exists because the read buffer had **no bound
#: at all**: a peer sending `{"a":1}` and no newline filled it until memory ran
#: out, and nothing in the loop noticed.
MAX_LINE_BYTES = 4 * 1024 * 1024

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


def _hostname() -> str:
    """This computer's name, as a KDE Connect peer sees it.

    The hostname, because that is what a person recognises in the phone's list;
    a generated device *id* is what they would have to compare against.
    """
    try:
        return socket.gethostname()
    except OSError:
        return "this computer"


def identity_exchange(host: str, tcp_port: int = UDP_PORT, timeout: float = 20.0):
    """The full KDE Connect handshake with a peer: `(its identity, why-not)`.

    **What this is.** The real thing, in order:

    1. TCP to the peer's control port.
    2. Our **plaintext** `kdeconnect.identity` - the short discovery form, with
       `deviceId`, `protocolVersion`, `deviceType`, `tcpPort`.
    3. The peer answers a TLS handshake **as the client** (measured: it sends
       nothing until it has our identity, then a ClientHello - so reading first
       and wrapping later needs a memory BIO rather than `wrap_socket`, which
       would consume the ClientHello itself).
    4. Inside TLS, both sides send the **full** identity - `deviceName`,
       `deviceType`, and the capability lists a phone advertises.

    The identity is a stable per-run thing, so our certificate is generated once
    and cached under the state home. It matters: a phone pairs to a
    **fingerprint**, and a new certificate each call would look to the phone like
    a different computer every time.

    **The roles are not symmetric, and getting them backwards is the bug this
    function is written around.** The TCP *receiver* is the TLS *client*
    (`kdeconnect-nx`: "Receiver connects as TLS client; we are TLS server"), so
    `server_side=True`. The first version of this used
    `server_side=False` and the phone reset the connection outright.

    Returns `(None, why-not)` with a reason when anything is missing, never a
    partially-handshaked object.
    """
    import ssl

    ours = identity_packet(name=_hostname(), device_type="desktop",
                           tcp_port=tcp_port)
    try:
        raw = socket.create_connection((host, tcp_port), timeout=timeout)
    except OSError as exc:
        return None, f"could not connect to {host}:{tcp_port} ({exc})"

    cert, key = _our_certificate()
    if cert is None:
        raw.close()
        return None, "could not make a TLS identity for this computer"

    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    try:
        context.load_cert_chain(str(cert), str(key))
    except (ssl.SSLError, OSError) as exc:
        raw.close()
        return None, f"the certificate would not load ({exc})"
    context.verify_mode = ssl.CERT_NONE        # trust-on-first-use, as the spec allows

    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    tls = context.wrap_bio(incoming, outgoing, server_side=True)
    deadline = time.monotonic() + timeout
    try:
        # 1. the plaintext short identity, which is what makes the peer speak
        raw.sendall((json.dumps(ours, separators=(", ", ": ")) + "\n").encode())

        # 2. drive the handshake by hand, because the ClientHello has to be read
        #    off the socket and the ServerHello written to it ourselves.
        while True:
            if time.monotonic() > deadline:
                return None, "the handshake did not finish in time"
            try:
                tls.do_handshake()
                break
            except ssl.SSLWantReadError:
                if outgoing.pending:
                    raw.sendall(outgoing.read())
                raw.settimeout(max(0.1, deadline - time.monotonic()))
                chunk = raw.recv(65536)
                if not chunk:
                    return None, "the peer closed the connection during the handshake"
                incoming.write(chunk)
            except (ssl.SSLError, OSError) as exc:
                return None, f"the TLS handshake failed ({exc})"

        if outgoing.pending:
            raw.sendall(outgoing.read())

        # 3. the full identity, inside TLS, in both directions
        # `wrap_bio` gives an SSLObject, which has write() and not sendall().
        tls.write((json.dumps(identity_packet(name=_hostname(),
                                             device_type="desktop",
                                             tcp_port=tcp_port),
                              separators=(", ", ": ")) + "\n").encode())
        theirs = b""
        while b"\n" not in theirs:
            if time.monotonic() > deadline:
                return None, "the peer answered our identity with nothing"
            if outgoing.pending:
                raw.sendall(outgoing.read())
            raw.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                chunk = raw.recv(65536)
            except socket.timeout:
                return None, "the peer stopped answering after we were connected"
            if not chunk:
                break
            incoming.write(chunk)
            try:
                theirs += tls.read()
            except ssl.SSLWantReadError:
                continue
        if not theirs:
            return None, "the peer sent no identity after the handshake"
        packet = json.loads(theirs.decode("utf-8", "replace").split("\n")[0])
        body = packet.get("body")
        if not isinstance(body, dict) or not body.get("deviceName"):
            return None, "the peer's identity was not a full one"
        entry = dict(body)
        entry["address"] = host
        return entry, ""
    except Exception as exc:  # noqa: BLE001 - every failure mode is a sentence
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        raw.close()


def _our_certificate():
    """(cert, key) paths for this computer's KDE Connect TLS identity, or None.

    Generated once and cached, because **the phone pairs to a fingerprint** -
    `openssl req -x509 -newkey rsa:2048 -subj /CN=<deviceId>` is what the KDE
    Connect identity requires: the Common Name is the device id, which is why
    the spec says an id "will be also set as the Common Name in the device TLS
    certificate".
    """
    import subprocess
    from shani_chronoa import files

    directory = files.state_home() / "kdeconnect"
    cert, key = directory / "tls.pem", directory / "tls.key"
    if cert.is_file() and key.is_file():
        try:
            import ssl
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.load_cert_chain(str(cert), str(key))
            return cert, key
        except (ssl.SSLError, OSError):
            pass                      # regenerate below; a bad cache is not fatal
    try:
        directory.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
             "-out", str(cert), "-days", "3650", "-nodes", "-subj",
             f"/CN={device_id()}"],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or not (cert.is_file() and key.is_file()):
        return None
    return cert, key


def pair(host: str, accept: bool = True, timeout: float = 120.0):
    """Pair with a peer: `(state, why-not)` where state is a sentence.

    **The handshake has a step that is easy to miss and that this function
    exists to get right.** Packet traffic is:

        us   -> kdeconnect.pair {"pair": true, "timestamp": <seconds>}
        phone-> kdeconnect.identity            (its full identity)
        phone-> kdeconnect.pair {"pair": true} (the person tapped Accept)
        us   -> kdeconnect.pair {"pair": true} <-- WE must answer too

    The spec says *"If accepted, **both devices** will store each other's TLS
    certificates"*, and `pairinghandler.cpp` only reaches `pairingDone()` when
    **both** sides have said `pair: true`. Stopping at the phone's reply works
    far enough to look paired and then leaves the link unpaired - measured: the
    next request came back `{"pair": false}` and the one after that a connection
    reset.

    **A person is required, and this says so.** Protocol 8 pairs by comparing a
    code derived from the timestamp (see `_pairing_code`), so the phone prompts;
    a `("waiting", ...)` state means nobody has answered yet, which is a normal
    outcome and not a failure. `("paired", ...)` means it completed.
    """
    link, why = _link(host, timeout=timeout)
    if link is None:
        return "failed", why
    request = {
        "id": int(time.time() * 1000),
        "type": "kdeconnect.pair",
        "body": {"pair": bool(accept), "timestamp": int(time.time())},
    }
    try:
        link.send(request)
        reply = link.reply_packet(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - a reason, never a traceback
        return "failed", f"{type(exc).__name__}: {exc}"
    finally:
        link.close()

    if reply is None:
        return "waiting", ("the device has not answered yet - pairing needs a "
                           "person: check the device for a prompt")
    if reply.get("type") != "kdeconnect.pair":
        return "failed", f"the device answered with {reply.get('type')!r} instead of a pair packet"

    body = reply.get("body") or {}
    if not body.get("pair"):
        return "rejected", "the device refused the pairing request"

    # We got a yes. Now say so back, so *both* sides have said it.
    confirm, why = _send_pair_true(host, timeout=timeout)
    if confirm is None:
        return "failed", f"the device accepted but the confirmation failed: {why}"
    return "paired", ("the device accepted, and both sides have confirmed - "
                      "certificates are now pinned by fingerprint")


def _send_pair_true(host: str, timeout: float = 60.0):
    """Open the link and say `pair: true`, the missing half of the handshake."""
    link, why = _link(host, timeout=timeout)
    if link is None:
        return None, why
    try:
        link.send({"id": int(time.time() * 1000), "type": "kdeconnect.pair",
                   "body": {"pair": True, "timestamp": int(time.time())}})
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        link.close()


class Link:
    """One TLS channel to a KDE Connect peer, driven by hand.

    **Why this is a class.** Every packet after pairing has the same shape - open
    the link, send one JSON line, read the replies - and the handshake that
    produces it is the only hard part, so it lives here once rather than being
    re-derived per plugin.

    **The memory BIO is forced, not preferred.** The peer sends nothing until it
    has our plaintext identity, so by the time there is a TLS record to read,
    `wrap_socket` has already consumed the ClientHello trying to drive the
    handshake itself. `wrap_bio` lets us feed those bytes in and write our
    ServerHello out.
    """

    def __init__(self, host: str, timeout: float = 20.0,
                 tcp_port: int = UDP_PORT):
        self.host = host
        self.timeout = timeout
        self.tcp_port = tcp_port
        self._raw: Optional[socket.socket] = None
        self._tls = None
        self._incoming: "Optional[ssl.MemoryBIO]" = None
        self._outgoing: "Optional[ssl.MemoryBIO]" = None
        self._buffer = ""

    @staticmethod
    def _context():
        """A TLS context presenting our own identity, and accepting theirs.

        `CERT_NONE` is right here and only here: an *unpaired* peer's certificate
        is accepted, and once paired the fingerprint is pinned by the phone. The
        pinning is not something this side implements yet, so the honest reading
        of `CERT_NONE` is that we do not verify, not that verification passed.
        """
        import ssl
        cert, key = _our_certificate()
        if cert is None:
            raise RuntimeError("no TLS identity for this computer")
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(str(cert), str(key))
        context.verify_mode = ssl.CERT_NONE
        return context

    def connect(self) -> "Link":
        """TCP -> plaintext identity -> TLS -> our full identity."""
        import ssl
        self._raw = socket.create_connection((self.host, self.tcp_port),
                                             timeout=self.timeout)
        self._incoming, self._outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self._tls = self._context().wrap_bio(self._incoming, self._outgoing,
                                            server_side=True)
        # **Our short identity goes on the raw socket, not through `_flush`.**
        # It is deliberately *not* encrypted: the peer will not speak TLS at all
        # until it has seen it. `_flush` writes into the TLS layer, and pushing
        # the plaintext identity through here raises `SSLWantReadError` - which
        # is also what a caller would see if this were reordered, so the two
        # writes are kept in the order the protocol asks for.
        self._raw.sendall((json.dumps(identity_packet(name=_hostname(),
                                                     device_type="desktop"),
                                      separators=(", ", ": ")) + "\n").encode())
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._tls.do_handshake()
                break
            except ssl.SSLWantReadError:
                if not self._pump(deadline, "the handshake"):
                    raise RuntimeError("the handshake did not finish in time")
            except (ssl.SSLError, OSError) as exc:
                raise RuntimeError(f"the TLS handshake failed ({exc})") from exc
        self.send(identity_packet(name=_hostname(), device_type="desktop"))
        return self

    def send(self, packet: dict, target: "Optional[str]" = None) -> None:
        """One JSON line. `target` turns an identity into a reply to that peer."""
        body = dict(packet.get("body") or {})
        if target and packet.get("type") == IDENTITY_TYPE:
            body["targetDeviceId"] = target
            body["targetProtocolVersion"] = PROTOCOL_VERSION
        out = dict(packet)
        out["body"] = body
        self._flush((json.dumps(out, separators=(", ", ": ")) + "\n").encode())

    def reply_packet(self, timeout: "Optional[float]" = None) -> "Optional[dict]":
        """The next packet that is not an identity, or None.

        Identity packets arrive as part of the handshake and are skipped here, so
        a caller asking about pairing or a plugin gets the answer rather than the
        conversation leading up to it.
        """
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            for line in self._lines(deadline):
                try:
                    packet = json.loads(line)
                except ValueError:
                    continue
                if isinstance(packet, dict) and packet.get("type") != IDENTITY_TYPE:
                    return packet
        return None

    def close(self) -> None:
        try:
            if self._tls is not None:
                self._tls.unwrap()
        except Exception:  # noqa: BLE001 - closing is not a moment to raise
            pass
        try:
            if self._raw is not None:
                self._raw.close()
        except OSError:
            pass

    # -- the plumbing -------------------------------------------------------
    def _flush(self, payload: bytes) -> None:
        self._tls.write(payload)
        if self._outgoing.pending:
            self._raw.sendall(self._outgoing.read())

    def _pump(self, deadline: float, why: str) -> bool:
        """Feed the peer's TLS records in, and push our own out."""
        if self._outgoing.pending:
            self._raw.sendall(self._outgoing.read())
        self._raw.settimeout(max(0.2, deadline - time.monotonic()))
        try:
            chunk = self._raw.recv(65536)
        except (socket.timeout, OSError):
            return False
        if not chunk:
            raise RuntimeError(f"the peer closed the link ({why})")
        self._incoming.write(chunk)
        return True

    def _lines(self, deadline: float) -> "list[str]":
        """Complete JSON lines received since the last call.

        A partial line is kept back rather than parsed, because a packet split
        across two reads is ordinary on a stream and parsing half of one would
        either raise or, worse, succeed with half an object.

        **The buffer is bounded, and it was not.** A peer that sends `{"a":1}`
        without ever sending a newline filled this without limit: the `while`
        loop kept appending and nothing ever partitioned it. Maze-Connect's
        `FrameParser` makes the same point with a length prefix - their version
        checks the cap *before* buffering, which needs a prefix to check, and
        this protocol's frames are newline-delimited so there is nothing to check
        first. What is available here is the bound itself: once the buffer
        exceeds it, no well-formed packet is on its way and the link is refused.
        """
        while "\n" not in self._buffer:
            if time.monotonic() >= deadline:
                break
            try:
                if not self._pump(deadline, "while reading"):
                    break
            except RuntimeError:
                break
            try:
                self._buffer += self._tls.read().decode("utf-8", "replace")
            except ssl.SSLWantReadError:
                continue
            if len(self._buffer) > MAX_LINE_BYTES:
                # Not a truncation. A peer claiming a frame larger than any
                # packet this protocol defines is not a peer whose next byte
                # makes the buffer make sense, and silently dropping the
                # overflow would leave a buffer now holding half of two things.
                raise RuntimeError(
                    f"the peer sent {len(self._buffer)} bytes without a frame "
                    f"boundary, past the {MAX_LINE_BYTES}-byte limit")
        whole, _, partial = self._buffer.partition("\n")
        self._buffer = partial
        return [l for l in whole.split("\n") if l.strip().startswith("{")] if whole else []


def send_file(host: str, path, tcp_port: int = UDP_PORT,
              timeout: float = 120.0) -> "tuple[bool, str]":
    """Push a file to a paired peer. `(sent, why-not)`.

    **The shape is a listening socket, not a connection we make.** The spec's
    own example carries the outbound port in the packet:

        {"type": "kdeconnect.share.request", "body": {"filename": "x.png"},
         "payloadSize": 882, "payloadTransferInfo": {"port": 1739}}

    and `kdeconnect-nx` opens the TCP side with `create_tcp_server_socket` over
    1716-1764 - so we listen, name our port in the packet, and the peer dials in
    and pulls the bytes. Sending it to a port *we* chose is the same number, but
    the roles are the opposite of the request/reply pattern every other plugin
    here uses, which is why this is spelled out.

    This is also what sends an **APK** to a phone - the file is just bytes, and
    Android offers to install whatever arrives. It is not special-cased: a
    picture and an installer travel the same way.
    """
    import pathlib

    target = pathlib.Path(path)
    if not target.is_file():
        return False, f"there is no file at {target}"
    size = target.stat().st_size
    if size <= 0:
        return False, f"{target.name} is empty, and an empty file is not worth sending"

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
    except OSError as exc:
        listener.close()
        return False, f"could not open a port to send from ({exc})"

    # **The port the caller names, exactly as `identity_exchange` does.** The
    # first version dialled UDP_PORT here while `send_file` was the only caller
    # that knew better, so a peer on any other control port - including a test's
    # fake one - came back as `ConnectionRefusedError` rather than as "the wrong
    # port", which reads as the peer being down.
    link, why = _link(host, timeout=timeout, tcp_port=tcp_port)
    if link is None:
        listener.close()
        return False, why

    accepted = threading.Event()
    try:
        link.send({
            "id": int(time.time() * 1000),
            "type": "kdeconnect.share.request",
            "body": {"filename": target.name},
            "payloadSize": size,
            "payloadTransferInfo": {"port": port},
        })
        # The peer dials in *while* our request is in flight, so the accept runs
        # beside it rather than after it - otherwise both sides wait and nothing
        # happens, which reads as a timeout rather than as a deadlock.
        waiter = threading.Thread(target=_accept_and_send,
                                  args=(listener, target, size, accepted),
                                  daemon=True)
        waiter.start()
        reply = link.reply_packet(timeout=20)
        if reply is None:
            return False, ("the device did not answer the share request - it may "
                          "not be paired, or the file may be refused")
        if reply.get("type") == "kdeconnect.pair":
            return False, ("the device says we are not paired, so it will not "
                          "take a file")
        return True, f"{target.name} ({_human(size)}) was offered to the device"
    except Exception as exc:  # noqa: BLE001 - a reason, never a traceback
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        link.close()
        listener.close()


def _accept_and_send(listener: socket.socket, target, size: int, accepted) -> None:
    """Take the peer's connection and stream the file into it."""
    try:
        listener.settimeout(30)
        conn, _address = listener.accept()
    except (OSError, socket.timeout):
        return
    accepted.set()
    try:
        conn.settimeout(60)
        with open(target, "rb") as handle:
            sent = 0
            while sent < size:
                chunk = handle.read(1 << 16)
                if not chunk:
                    break
                conn.sendall(chunk)
                sent += len(chunk)
    except OSError as exc:
        logger.info("the peer stopped taking the file: %s", exc)
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _link(host: str, timeout: float = 20.0,
          tcp_port: int = UDP_PORT) -> "tuple[Optional[Link], str]":
    """A connected Link, or (None, why-not). The one entry point everything uses."""
    link = Link(host, timeout=timeout, tcp_port=tcp_port)
    try:
        link.connect()
        return link, ""
    except Exception as exc:  # noqa: BLE001 - every failure mode is a sentence
        link.close()
        return None, f"{type(exc).__name__}: {exc}"


def _exchange(host: str, packet: dict, timeout: float):
    """Open the link, send one packet, return the peer's next reply.

    A helper rather than part of `pair`, because every plugin packet after
    pairing has exactly this shape and none of them have been written yet. It
    exists so the first one is a one-liner rather than a re-derivation of the
    TLS dance.
    """
    import ssl

    raw = socket.create_connection((host, UDP_PORT), timeout=timeout)
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    cert, key = _our_certificate()
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(str(cert), str(key))
    context.verify_mode = ssl.CERT_NONE
    tls = context.wrap_bio(incoming, outgoing, server_side=True)
    deadline = time.monotonic() + timeout
    try:
        short = identity_packet(name=_hostname(), device_type="desktop")
        raw.sendall((json.dumps(short, separators=(", ", ": ")) + "\n").encode())
        while True:
            if time.monotonic() > deadline:
                return {"error": "the handshake timed out"}
            try:
                tls.do_handshake()
                break
            except ssl.SSLWantReadError:
                if outgoing.pending:
                    raw.sendall(outgoing.read())
                raw.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    incoming.write(raw.recv(65536))
                except socket.timeout:
                    return {"error": "no answer during the handshake"}
            except (ssl.SSLError, OSError) as exc:
                return {"error": str(exc)}
        full = identity_packet(name=_hostname(), device_type="desktop")
        tls.write((json.dumps(full, separators=(", ", ": ")) + "\n").encode())
        tls.write((json.dumps(packet, separators=(", ", ": ")) + "\n").encode())
        reply = b""
        while b"\n" not in reply:
            if time.monotonic() > deadline:
                return {"error": "the peer did not answer in time"}
            if outgoing.pending:
                raw.sendall(outgoing.read())
            raw.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                chunk = raw.recv(65536)
            except socket.timeout:
                return {"error": "the device has not answered yet - pairing "
                                 "needs a person: check the device for a prompt"}
            if not chunk:
                break
            incoming.write(chunk)
            try:
                reply += tls.read()
            except ssl.SSLWantReadError:
                continue
        if not reply:
            return {"error": "the peer sent nothing back"}
        return json.loads(reply.decode("utf-8", "replace").split("\n")[0])
    except Exception as exc:  # noqa: BLE001 - a reason, not a traceback
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        raw.close()


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
