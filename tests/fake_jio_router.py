"""A fake JioFiber router for the landline tests: provisioning and SIP, in-process.

It behaves like the real router where the landline code depends on it: the
account answer on an HTTP port, adding a device only with the texted code and
only under `nwk_intf=wifi`, a fresh password on every credentials fetch, a
digest challenge on REGISTER and a proxy challenge on INVITE, Service-Route,
180 then 200 with SDP, and RTP both ways. Everything it saw is kept so a test
can assert on it. TLS uses a throwaway self-signed certificate.
"""
from __future__ import annotations

import http.server
import json
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.landline import media, sip, video  # noqa: E402

CODE = "482913"
NUMBER = "+912040001234"
REALM = "br.wln.ims.jio.com"
PASSWORD_SEED = "routerpass"


def _free_udp_port() -> int:
    """A UDP port the router can name in an answer without owning a session on.

    Held open rather than picked at answer time, because two answers that name
    different ports for one stream are a far end that moved.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def _tls_context(directory: Path) -> "tuple[ssl.SSLContext, str]":
    key, crt = directory / "key.pem", directory / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=jiofiber", "-keyout", str(key), "-out", str(crt)],
                   check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    return ctx, str(crt)


class Router:
    """The fake router. `host` is 127.0.0.1; the three ports are chosen free."""

    def __init__(self, directory: Path, codecs=("PCMU/8000",), challenge_invite=True,
                 reoffer_mid_ring=False, empty_final_answer=False,
                 reoffer_bandwidth_efficient=False, video=None):
        self.tls, self.cert = _tls_context(directory)
        self.codecs = codecs
        self.challenge_invite = challenge_invite
        self.reoffer_mid_ring = reoffer_mid_ring
        self.empty_final_answer = empty_final_answer
        self.reoffer_bandwidth_efficient = reoffer_bandwidth_efficient
        #: What this router does about the video m-line. `None` - the default, and
        #: the shape every other test in this file assumes - is a voice-only peer
        #: that says nothing about video at all. `True` answers with a real
        #: `m=video` section, `False` with `m=video 0`, which is a *decline* and
        #: not a port.
        self.video = video
        self.update_refused = False
        self._ringed = None
        self.seen = {"http": [], "register": [], "invite": [], "rtp_in": 0,
                     "update_answer": None, "last_answer": "", "re_offer": None}
        self.added: "set[str]" = set()
        self.password = ""              # the current fetched password, regenerated each fetch
        self._pending_alias = ""
        self._sip_sock = None
        self._stop = threading.Event()
        self._rtp = None
        self.registered_contact = ""
        self._video_port = _free_udp_port()
        #: Call-IDs of dialogs this router has answered, so a re-INVITE on one
        #: is recognised as a hold rather than rung again.
        self._dialogs: "set[str]" = set()
        self.http_port = self._http(tls=False)
        self.https_port = self._http(tls=True)
        self.sip_port, self._sip_srv = self._sip()

    # --- the video half of an answer -------------------------------------------------
    def _video_answer(self, body: str) -> str:
        """This router's answer, with whatever it has to say about video added.

        **No picture packets are sent.** This router is a SIP peer, not a
        camera, and standing a second ffmpeg up to answer a question about
        negotiation would be measuring the encoder twice - the pixels are
        measured by the loopback in `test_landline_video.py`. What this pins is
        which side offered what, and where each stream ended up.
        """
        if self.video is None:
            return body
        if not self.video:
            return body.rstrip("\r\n") + "\r\nm=video 0 RTP/AVP 96\r\n"
        return video.with_video(body, "127.0.0.1", self._video_port)

    # --- the account / OTP / credentials HTTP faces -----------------------------
    def _http(self, tls: bool) -> int:
        router = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # noqa: D401
                pass

            def _reply(self, status, body="", headers=None):
                data = body.encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
                router.seen["http"].append((url.path, q, self.headers.get("Cookie", "")))
                if url.path == "/request_account":
                    return self._reply(200, json.dumps({"msisdn": "9999994321"}))
                if "OTP" in q:
                    good = q["OTP"] == CODE and "sess=" in self.headers.get("Cookie", "")
                    if good:
                        router.added.add(router._pending_alias)
                    return self._reply(200 if good else 403)
                # The IMS face: add a device, or hand back its line settings.
                if q.get("nwk_intf") != "wifi":
                    return self._reply(400, "nwk_intf must be wifi")
                alias = q.get("alias", "")
                if q.get("op_type") == "add":
                    router._pending_alias = alias
                    return self._reply(200, "", {"Set-Cookie": "sess=" + secrets.token_hex(4),
                                                 "x-amn": "9999994321"})
                if alias not in router.added:
                    return self._reply(403, "device not added")
                router.password = PASSWORD_SEED + secrets.token_hex(2)
                xml = (f'<wap-provisioningdoc><characteristic type="APPLICATION">'
                       f'<parm name="realm" value="{REALM}"/>'
                       f'<parm name="username" value="{NUMBER[1:]}@{REALM}"/>'
                       f'<parm name="private_user_identity" value="{NUMBER[1:]}@{REALM}"/>'
                       f'<parm name="public_user_identity" value="sip:{NUMBER}@{REALM}"/>'
                       f'<parm name="userpwd" value="{router.password}"/>'
                       f'</characteristic></wap-provisioningdoc>')
                return self._reply(200, xml)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        if tls:
            srv.socket = self.tls.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv.server_address[1]

    # --- the SIP face (one TLS connection) --------------------------------------
    def _sip(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        threading.Thread(target=self._sip_accept, args=(listener,), daemon=True).start()
        return port, listener

    def _sip_accept(self, listener):
        while not self._stop.is_set():
            try:
                raw, _addr = listener.accept()
            except OSError:
                return
            try:
                conn = self.tls.wrap_socket(raw, server_side=True)
            except ssl.SSLError:
                continue
            self._sip_sock = conn
            threading.Thread(target=self._sip_read, args=(conn,), daemon=True).start()

    def _send(self, conn, message: sip.Message):
        try:
            conn.sendall(message.encode())
        except OSError:
            pass

    def _sip_read(self, conn):
        buffer = b""
        while not self._stop.is_set():
            try:
                data = conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            if data.strip() == b"":
                conn.sendall(b"\r\n")      # keep-alive pong
                continue
            messages, buffer = sip.split_stream(buffer + data)
            for message in messages:
                if message.is_request:
                    self._on_request(conn, message)
                elif message.status == 200 and message.method == "INVITE":
                    self._on_answer(conn, message)
                elif message.method == "UPDATE":
                    self._on_update_answer(conn, message)

    def _on_request(self, conn, req: sip.Message):
        method = req.method
        if method == "REGISTER":
            return self._on_register(conn, req)
        if method == "INVITE":
            return self._on_invite(conn, req)
        if method == "ACK":
            return
        if method == "BYE":
            self._send(conn, sip.response(req, 200))
            if self._rtp:
                self._rtp.stop()
                self._rtp = None
            return
        if method == "PRACK":
            return self._send(conn, sip.response(req, 200))
        self._send(conn, sip.response(req, 200))

    def _on_answer(self, conn, reply: sip.Message):
        """The device answered our incoming call: ACK it and send it audio."""
        target = sip.uri_of(reply.get("Contact"))
        self._send(conn, sip.Message(f"ACK {target} SIP/2.0", [
            ("Via", reply.get("Via")), ("Max-Forwards", "70"), ("From", reply.get("From")),
            ("To", reply.get("To")), ("Call-ID", reply.call_id),
            ("CSeq", f"{reply.cseq[0]} ACK")]))
        ip, port, codecs, _dtmf = media.parse_sdp(reply.body)
        chosen = media.pick(codecs)
        if chosen and self._rtp is not None:
            self._rtp.start((ip, port), chosen[0], chosen[1])

    def _on_update_answer(self, conn, reply: sip.Message):
        """What the callee's phone does with the device's answer to its UPDATE.

        Measured live: RFC 3311 requires the 200 OK to carry an SDP answer, and
        a device that answers with no body gets the call torn down with 487.
        The far end does not ask twice, so the case is recorded here.
        """
        self.seen["update_answer"] = reply
        if reply.body.strip():
            if self._ringed is not None:
                pending, self._ringed = self._ringed, None
                self._answer_ringing(pending["conn"], pending["req"], pending["to_tag"],
                                     pending["answer"], pending["ip"], pending["port"],
                                     pending["chosen"], pending["packing"])
            return
        self.update_refused = True
        pending, self._ringed = self._ringed, None
        if pending is not None:
            time.sleep(0.3)
            self._send(conn, sip.response(pending["req"], 487, to_tag=pending["to_tag"]))

    def _on_register(self, conn, req: sip.Message):
        auth = req.get("Authorization")
        self.seen["register"].append(req)
        if not auth:
            nonce = secrets.token_hex(8)
            self._send(conn, sip.response(req, 401, to_tag=sip.new_tag(), extra=[
                ("WWW-Authenticate", f'Digest realm="{REALM}", nonce="{nonce}", qop="auth"')]))
            return
        if not sip.check_authorization(auth, "REGISTER", self.password):
            self._send(conn, sip.response(req, 403, to_tag=sip.new_tag()))
            return
        self.registered_contact = req.get("Contact")
        expires = req.get("Expires", "3600")
        self._send(conn, sip.response(req, 200, to_tag=sip.new_tag(), extra=[
            ("Contact", f"{req.get('Contact')};expires={expires}"),
            ("Service-Route", f"<sip:127.0.0.1:{self.sip_port};transport=tls;lr>")]))

    def _on_invite(self, conn, req: sip.Message):
        self.seen["invite"].append(req)
        tag = sip.param(req.get("To"), "tag")
        if tag and req.call_id in self._dialogs:
            # A re-INVITE inside a live dialog - a hold or a resume. Answered
            # with the direction and no ring, because ringing again for a call
            # that is already up is a second call and not a hold.
            return self._answer_re_offer(conn, req, tag)
        if self.challenge_invite and not req.get("Proxy-Authorization"):
            nonce = secrets.token_hex(8)
            self._send(conn, sip.response(req, 407, to_tag=sip.new_tag(), extra=[
                ("Proxy-Authenticate", f'Digest realm="{REALM}", nonce="{nonce}", qop="auth"')]))
            return
        to_tag = sip.new_tag()
        self.update_refused = False
        self._send(conn, sip.response(req, 100))
        self._send(conn, sip.response(req, 180, to_tag=to_tag, extra=[
            ("Contact", f"<sip:far@127.0.0.1:{self.sip_port};transport=tls>")]))
        ip, port, codecs, _dtmf = media.parse_sdp(req.body)
        allowed = {name.upper() for name in self.codecs}
        codecs = [(pt, name) for pt, name in codecs if name.upper() in allowed]
        chosen = media.pick(codecs)
        if chosen is None:
            return self._send(conn, sip.response(req, 488, to_tag=to_tag))
        self._rtp = media.RtpSession.bind("127.0.0.1", _Silence())
        answer = media.offer("127.0.0.1", self._rtp.port, only=chosen[1])
        answer = self._video_answer(answer)
        if self.reoffer_mid_ring:
            # What the callee's JUICE phone does on the real router: an UPDATE
            # carrying a fresh SDP offer while the INVITE is still ringing. It
            # answers the INVITE only once it has a real SDP answer, and tears
            # the call down with 487 if none comes - so the rest of this call is
            # driven from _on_update_answer, on the reader thread, and this
            # call returns rather than waiting for a message it is the one
            # reading.
            offer = media.offer("127.0.0.1", self._rtp.port, only=chosen[1])
            offer = self._video_answer(offer)
            if self.reoffer_bandwidth_efficient:
                offer = offer.replace("AMR/8000/1", "AMR/8000")
                offer = offer.replace("mode-change-capability=1", "mode-change-capability=2")
            self._ringed = {"conn": conn, "req": req, "to_tag": to_tag, "answer": answer,
                            "ip": ip, "port": port, "chosen": chosen,
                            "packing": "be" if self.reoffer_bandwidth_efficient else "oa"}
            return self._send(conn, sip.Message(
                f"UPDATE {sip.uri_of(req.get('Contact'))} SIP/2.0", [
                    ("Via", f"SIP/2.0/TLS 127.0.0.1:{self.sip_port};branch={sip.new_branch()}"),
                    ("Max-Forwards", "70"),
                    ("From", req.get("From")),
                    ("To", f"<{sip.uri_of(req.get('To'))}>;tag={to_tag}"),
                    ("Call-ID", req.call_id), ("CSeq", f"{req.cseq[0]} UPDATE"),
                    ("Content-Type", "application/sdp")], offer))
        self._ringed = None
        self._answer_ringing(conn, req, to_tag, answer, ip, port, chosen, "oa")

    def _answer_re_offer(self, conn, req: sip.Message, tag: str) -> None:
        """The answer to a hold or a resume: 200 OK carrying the direction back.

        A hold offer of `a=sendonly` - "I will send, do not send me anything" -
        is answered `a=recvonly`: we will receive and not send, which is what
        tells the holder the far end has *taken* the hold. `a=sendrecv` is
        answered `a=sendrecv` and resumes. A far end that answered a hold offer
        with `a=sendrecv` has refused it while still answering.
        """
        asked = media.sdp_direction(req.body)
        ip, port, codecs, _dtmf = media.parse_sdp(req.body)
        allowed = {name.upper() for name in self.codecs}
        chosen = media.pick([(pt, n) for pt, n in codecs if n.upper() in allowed])
        if chosen is None:
            return self._send(conn, sip.response(req, 488, to_tag=tag))
        self.seen["re_offer"] = (asked, req.body)
        body = media.offer("127.0.0.1", self._rtp.port, only=chosen[1],
                           direction={"sendonly": "recvonly", "recvonly": "sendonly",
                                      "sendrecv": "sendrecv",
                                      "inactive": "inactive"}[asked])
        self._send(conn, sip.response(req, 200, to_tag=tag, body=body, extra=[
            ("Contact", f"<sip:far@127.0.0.1:{self.sip_port};transport=tls>")]))

    def _answer_ringing(self, conn, req, to_tag, answer, ip, port, chosen, packing) -> None:
        self._ringed_packing = packing
        if packing == "be":            # A far end that re-offered bandwidth-efficient also answers the
            # INVITE with it - the packing does not go back afterwards.
            answer = answer.replace("AMR/8000/1", "AMR/8000")
        self._rtp.start((ip, port), chosen[0], chosen[1], 0, packing)
        time.sleep(0.05)
        if self.empty_final_answer:
            # What the callee does once a mid-ring re-offer has already
            # negotiated the session: the 200 OK carries no SDP of its own.
            body = ""
        else:
            body = answer
        self._send(conn, sip.response(req, 200, to_tag=to_tag, body=body, extra=[
            ("Contact", f"<sip:far@127.0.0.1:{self.sip_port};transport=tls>")]))
        self.seen["last_answer"] = body
        self._dialogs.add(req.call_id)

    def call_in(self, from_number: str = "+911112223334") -> None:
        """Ring the registered device (a call from the network to the line)."""
        conn = self._sip_sock
        contact = sip.uri_of(self.registered_contact)
        self._rtp = media.RtpSession.bind("127.0.0.1", _Silence())
        only = next((pt for pt, name in media.offers() if name.upper() in
                     {c.upper() for c in self.codecs}), None)
        body = media.offer("127.0.0.1", self._rtp.port, only=only)
        call_id = sip.new_call_id("127.0.0.1")
        invite = sip.Message(f"INVITE {contact} SIP/2.0", [
            ("Via", f"SIP/2.0/TLS 127.0.0.1:{self.sip_port};branch={sip.new_branch()}"),
            ("Max-Forwards", "70"), ("From", f'"Caller" <sip:{from_number}@{REALM}>;tag={sip.new_tag()}'),
            ("To", f"<{contact}>"), ("Call-ID", call_id), ("CSeq", "1 INVITE"),
            ("P-Asserted-Identity", f'"Caller" <sip:{from_number}@{REALM}>'),
            ("Contact", f"<sip:caller@127.0.0.1:{self.sip_port};transport=tls>"),
            ("Content-Type", "application/sdp")], body)
        self._send(conn, invite)

    def stop(self):
        self._stop.set()
        if self._rtp:
            self._rtp.stop()
        try:
            self._sip_srv.close()
        except OSError:
            pass


class _Silence:
    """An RTP sound source that gives silence and swallows what it is played."""

    def read(self) -> bytes:
        time.sleep(0.02)
        return b"\x00" * media.FRAME_BYTES

    def write(self, pcm: bytes) -> None:
        pass

    def close(self) -> None:
        pass
