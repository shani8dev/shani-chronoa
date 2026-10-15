#!/usr/bin/env python3
"""A KDE Connect phone on loopback - the LAN pairing harness.

`lan_link.Link` has a handshake with two non-obvious parts (the TCP receiver is
the TLS *client*; the phone says nothing until it has our plaintext identity),
and until this existed no test had completed it: `TestThePeerIsPinned` stubs
the identity, and every link died on a `TypeError` before opening a socket
without anything noticing. This plays the phone's side the way
`lan_link`'s docstring describes the real one (protocol 8):

    computer -> TCP connect, plaintext identity line
    phone    -> TLS ClientHello (phone is the TLS client)
    both     -> identity packet inside TLS
    computer -> kdeconnect.pair {"pair": true}
    phone    -> kdeconnect.pair {"pair": true|false}   (a person tapped)
    computer -> kdeconnect.pair {"pair": true}         (on a second link)

`answer` is what the "person" does: "accept", "reject" or "ignore". Every
packet received is kept in `received`, and every clipboard packet given to
`clipboard(text)` is sent on the next link, so the bridge can be driven too.
"""
from __future__ import annotations

import json
import pathlib
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

PHONE_ID = "f" * 32


def _identity(name: str = "Fake Pixel", device_id: str = PHONE_ID) -> dict:
    return {"id": int(time.time() * 1000), "type": "kdeconnect.identity",
            "body": {"deviceId": device_id, "deviceName": name, "deviceType": "phone",
                     "protocolVersion": 8, "tcpPort": 1716,
                     "incomingCapabilities": ["kdeconnect.pair", "kdeconnect.clipboard",
                                              "kdeconnect.notification"],
                     "outgoingCapabilities": ["kdeconnect.clipboard"]}}


def _cert(directory: pathlib.Path, device_id: str = PHONE_ID) -> "tuple[str, str]":
    cert, key = directory / "phone.pem", directory / "phone.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", f"/CN={device_id}"], check=True, capture_output=True)
    return str(cert), str(key)


class FakePhone:
    def __init__(self, answer: str = "accept", delay: float = 0.2,
                 identity_inside_tls: bool = True, device_id: str = PHONE_ID) -> None:
        self.answer = answer
        self.device_id = device_id
        self.delay = delay
        self.identity_inside_tls = identity_inside_tls
        self.received: "list[dict]" = []
        self.plain_identities: "list[dict]" = []
        self.links = 0
        self._outbox: "list[dict]" = []
        self._dir = pathlib.Path(tempfile.mkdtemp(prefix="fake-kdeconnect-"))
        cert, key = _cert(self._dir, device_id)
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        self._ctx.load_cert_chain(cert, key)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self.port = self._listener.getsockname()[1]
        self._stop = False

    def clipboard(self, text: str) -> None:
        self._outbox.append({"id": int(time.time() * 1000), "type": "kdeconnect.clipboard",
                             "body": {"content": text, "timestamp": int(time.time() * 1000)}})

    def start(self) -> "FakePhone":
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop = True
        try:
            self._listener.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._stop:
            try:
                raw, _addr = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw: socket.socket) -> None:
        self.links += 1
        raw.settimeout(10)
        line = b""
        while not line.endswith(b"\n"):
            chunk = raw.recv(1)
            if not chunk:
                return
            line += chunk
        self.plain_identities.append(json.loads(line))
        try:
            tls = self._ctx.wrap_socket(raw, server_side=False)
        except (ssl.SSLError, OSError):
            raw.close()
            return
        stream = tls.makefile("rwb")

        def send(packet: dict) -> None:
            stream.write((json.dumps(packet) + "\n").encode())
            stream.flush()

        if self.identity_inside_tls:
            send(_identity(device_id=self.device_id))
        for packet in self._outbox:
            send(packet)
        self._outbox.clear()
        try:
            for raw_line in stream:
                packet = json.loads(raw_line)
                self.received.append(packet)
                if packet.get("type") == "kdeconnect.pair" and packet["body"].get("pair"):
                    if any(p.get("type") == "kdeconnect.pair" for p in self.received[:-1]):
                        continue          # the computer's confirmation: done
                    time.sleep(self.delay)
                    if self.answer == "accept":
                        send({"id": 2, "type": "kdeconnect.pair", "body": {"pair": True}})
                    elif self.answer == "reject":
                        send({"id": 2, "type": "kdeconnect.pair", "body": {"pair": False}})
        except (OSError, ValueError, ssl.SSLError):
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def got(self, kind: str) -> "list[dict]":
        return [p for p in self.received if p.get("type") == kind]


def main() -> int:
    phone = FakePhone(answer=(sys.argv[1] if len(sys.argv) > 1 else "accept")).start()
    print(f"fake phone on 127.0.0.1:{phone.port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        phone.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
