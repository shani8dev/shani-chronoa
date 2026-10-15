"""The landline's phone: registers with the router and makes and takes calls.

One TLS connection to the router (port 5068), which relays to Jio's network -
the same path JioJoin uses (see provision.py for how this device is added).
Registration carries the instance id and the IMS feature tags: without them,
the community write-ups measured, calls out work but the line never rings in
(the router only forks a call to devices that advertise the telephony tags).

A deliberately small user agent: one call at a time (a second caller hears
busy), no hold or transfer, PRACK when the far end requires it, RFC 5626
double-CRLF keep-alives, and a reconnect with re-registration when the
connection drops. `on_event(kind, **info)` reports: registered, unregistered,
incoming, ringing, answered, ended.
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
import time
from typing import Callable, Dict, List, Optional

from shani_chronoa.landline import media, sip

logger = logging.getLogger(__name__)

USER_AGENT = "JUICEJFV-1.3.32"
EXPIRES = 3600
KEEPALIVE_SECONDS = 25
ALLOW = "INVITE, ACK, CANCEL, BYE, OPTIONS, UPDATE, PRACK, NOTIFY, MESSAGE, INFO"
MMTEL = '+g.3gpp.icsi-ref="urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel"'
FEATURES = (MMTEL + ';+g.3gpp.iari-ref="urn%3Aurn-7%3A3gpp-application.ims.iari.rcs.jio.eucr"'
            ';+g.gsma.rcs.telephony="none"')


def dial_user(number: str) -> str:
    """The user part to dial: Indian numbers in +91 form, as the line expects."""
    digits = "".join(ch for ch in number if ch.isdigit() or ch in "+*#")
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return digits


class Call:
    def __init__(self, call_id: str, local_tag: str, outgoing: bool, number: str) -> None:
        self.call_id = call_id
        self.local_tag = local_tag
        self.remote_tag = ""
        self.outgoing = outgoing
        self.number = number
        self.name = ""
        self.state = "dialing" if outgoing else "incoming"
        self.cseq = 1
        self.remote_target = ""
        self.route: List[str] = []
        self.local_uri = ""
        self.remote_uri = ""
        self.invite: Optional[sip.Message] = None    # theirs (incoming) or ours (outgoing)
        self.rtp: Optional[media.RtpSession] = None
        self.started = 0.0


class Line:
    def __init__(self, host: str, creds: Dict[str, str], instance: str,
                 on_event: Callable[..., None] = lambda kind, **info: None,
                 port: int = 5068, sound_factory: Optional[Callable[[], object]] = None) -> None:
        self.host, self.port = host, port
        self.realm = creds["realm"]
        self.auth_user = creds["user"]
        self.password = creds["password"]
        self.public = sip.uri_of(creds.get("public") or f"sip:{self.auth_user}")
        self.number = sip.user_of(self.public)
        # The shape the router matches its whitelist against, measured from the
        # provisioning payload's own uuid_value: uppercase hex, no urn:uuid:
        # prefix. The pjproject clients Jio's own tooling is built on carry the
        # prefix off by default and the working bridges patch it out.
        self.instance = f"<00000000-0000-1000-8000-0000{instance.upper()}>"
        self.on_event = on_event
        self.sound_factory = sound_factory
        self.registered = False
        self.call: Optional[Call] = None
        self.last_error = ""
        self._sock: Optional[ssl.SSLSocket] = None
        self._send_lock = threading.Lock()
        self._pending: Dict[str, Dict] = {}     # branch -> {"event", "responses"}
        self._reg_call_id = sip.new_call_id(host)
        self._reg_tag = sip.new_tag()
        self._reg_cseq = 0
        self._service_route: List[str] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.local_ip = ""
        self.local_port = 0

    # --- the connection --------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="landline")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.call is not None:
            try:
                self.hangup()
            except Exception:  # noqa: BLE001
                pass
        if self.registered:
            try:
                self._register(expires=0)
            except Exception:  # noqa: BLE001
                pass
        self._close()

    def _connect(self) -> None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the router's own certificate
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        raw = socket.create_connection((self.host, self.port), timeout=10)
        self._sock = ctx.wrap_socket(raw, server_hostname=self.host)
        self._sock.settimeout(None)
        self.local_ip, self.local_port = self._sock.getsockname()[:2]
        threading.Thread(target=self._reader, args=(self._sock,), daemon=True,
                         name="landline-read").start()

    def _close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self._connect()
                status = self._register()
                if status != 200:
                    self.last_error = f"the router refused registration ({status})"
                    self.on_event("unregistered", reason=self.last_error)
                    if status in (401, 403):
                        return  # wrong password: retrying will not change it
                    raise OSError(self.last_error)
                backoff = 2
                refresh_at = time.monotonic() + self._expires * 0.8
                while not self._stop.wait(KEEPALIVE_SECONDS):
                    if self._sock is None:
                        raise OSError("the connection to the router closed")
                    self._send_raw(b"\r\n\r\n")
                    if time.monotonic() >= refresh_at:
                        if self._register() != 200:
                            raise OSError("re-registration failed")
                        refresh_at = time.monotonic() + self._expires * 0.8
            except (OSError, ssl.SSLError) as exc:
                if self._stop.is_set():
                    return
                self.last_error = str(exc)
                if self.registered:
                    self.registered = False
                    self.on_event("unregistered", reason=str(exc))
                self._close()
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def _reader(self, sock) -> None:
        buffer = b""
        while True:
            try:
                data = sock.recv(65536)
            except (OSError, ssl.SSLError):
                data = b""
            if not data:
                if self._sock is sock:
                    self._close()
                for slot in list(self._pending.values()):
                    slot["event"].set()
                return
            messages, buffer = sip.split_stream(buffer + data)
            for message in messages:
                try:
                    self._dispatch(message)
                except Exception:  # noqa: BLE001 - one bad message must not end the line
                    logger.exception("landline: could not handle %r", message)

    def _send_raw(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise OSError("not connected to the router")
        with self._send_lock:
            sock.sendall(data)

    def _send(self, message: sip.Message) -> None:
        logger.debug("landline >> %s", message.first)
        self._send_raw(message.encode())

    # --- transactions ------------------------------------------------------------------
    def _via(self, branch: str) -> str:
        return f"SIP/2.0/TLS {self.local_ip}:{self.local_port};branch={branch};rport"

    def _contact(self, expires: Optional[int] = None) -> str:
        c = (f"<sip:{self.number}@{self.local_ip}:{self.local_port};transport=tls;ob>"
             f';+sip.instance="{self.instance}";{FEATURES}')
        return c + (";reg-id=1" if expires is not None else "")

    def _request(self, message: sip.Message, timeout: float = 32.0,
                 final: bool = True) -> Optional[sip.Message]:
        """Send and wait for the final response (or the first response, when final=False)."""
        branch = sip.param(message.get("Via"), "branch")
        slot = {"event": threading.Event(), "responses": [], "final": final}
        self._pending[branch] = slot
        try:
            self._send(message)
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if not slot["event"].wait(max(0.0, end - time.monotonic())):
                    break
                slot["event"].clear()
                if self._sock is None and not slot["responses"]:
                    return None
                last = slot["responses"][-1] if slot["responses"] else None
                if last is not None and (last.status >= 200 or not final):
                    return last
            return None
        finally:
            self._pending.pop(branch, None)

    def _dispatch(self, message: sip.Message) -> None:
        logger.debug("landline << %s", message.first)
        if not message.is_request:
            branch = sip.param(message.get("Via"), "branch")
            slot = self._pending.get(branch)
            if slot is not None:
                slot["responses"].append(message)
                slot["event"].set()
            elif message.method == "INVITE" and message.status >= 200:
                pass  # a repeated final response; its ACK went with the first
            if message.method == "INVITE" and 100 < message.status < 200:
                self._provisional(message)
            return
        handler = {"INVITE": self._on_invite, "ACK": self._on_ack, "BYE": self._on_bye,
                   "CANCEL": self._on_cancel}.get(message.method)
        if handler is not None:
            handler(message)
        else:
            self._send(sip.response(message, 200))  # OPTIONS, NOTIFY, UPDATE, INFO, MESSAGE

    # --- registration --------------------------------------------------------------------
    _expires = EXPIRES

    def _register(self, expires: int = EXPIRES) -> int:
        auth = ""
        for _attempt in range(2):
            self._reg_cseq += 1
            uri = f"sip:{self.realm}"
            headers = [
                ("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
                ("Route", f"<sip:{self.host}:{self.port};transport=tls;lr>"),
                ("From", f"<{self.public}>;tag={self._reg_tag}"), ("To", f"<{self.public}>"),
                ("Call-ID", self._reg_call_id), ("CSeq", f"{self._reg_cseq} REGISTER"),
                ("Contact", self._contact(expires)), ("Expires", str(expires)),
                ("Supported", "path, outbound, gruu"), ("Allow", ALLOW),
                ("User-Agent", USER_AGENT), ("P-Access-Network-Info", "IEEE-802.11"),
                ("P-Preferred-Identity", f"<{self.public}>"),
            ]
            if auth:
                headers.append(("Authorization", auth))
            reply = self._request(sip.Message(f"REGISTER {uri} SIP/2.0", headers))
            if reply is None:
                raise OSError("the router did not answer the registration")
            if reply.status == 401 and not auth:
                auth = sip.authorization(reply.get("WWW-Authenticate"), "REGISTER", uri,
                                         self.auth_user, self.password)
                continue
            if reply.status == 200 and expires:
                self._service_route = reply.all("Service-Route")
                granted = sip.param(reply.get("Contact"), "expires") or reply.get("Expires")
                self._expires = int(granted) if str(granted).isdigit() and int(granted) > 60 else EXPIRES
                if not self.registered:
                    self.registered = True
                    self.on_event("registered", number=self.number)
            elif reply.status == 200:
                self.registered = False
            return reply.status
        return 401

    # --- calls out ------------------------------------------------------------------------
    def _routes(self) -> List[str]:
        return self._service_route or [f"<sip:{self.host}:{self.port};transport=tls;lr>"]

    def dial(self, number: str) -> None:
        """Start a call; progress arrives as events. Raises when it cannot start."""
        if not self.registered:
            raise RuntimeError("the landline is not connected to the router")
        if self.call is not None:
            raise RuntimeError("a call is already in progress")
        user = dial_user(number)
        if not user:
            raise ValueError("that is not a number")
        call = Call(sip.new_call_id(self.local_ip), sip.new_tag(), True, user)
        call.local_uri, call.remote_uri = self.public, f"sip:{user}@{self.realm}"
        call.route = self._routes()
        call.rtp = media.RtpSession.bind(self.local_ip, self.sound_factory() if self.sound_factory else None)
        self.call = call
        threading.Thread(target=self._outgoing, args=(call,), daemon=True, name="landline-dial").start()

    def _invite(self, call: Call, auth: str = "") -> sip.Message:
        headers = [
            ("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
            *[("Route", r) for r in call.route],
            ("From", f"<{call.local_uri}>;tag={call.local_tag}"), ("To", f"<{call.remote_uri}>"),
            ("Call-ID", call.call_id), ("CSeq", f"{call.cseq} INVITE"),
            ("Contact", self._contact()), ("Allow", ALLOW), ("Supported", "100rel, timer"),
            ("Accept-Contact", f"*;{MMTEL}"),
            ("P-Preferred-Service", "urn:urn-7:3gpp-service.ims.icsi.mmtel"),
            ("User-Agent", USER_AGENT), ("P-Access-Network-Info", "IEEE-802.11"),
            ("P-Preferred-Identity", f"<{self.public}>"), ("Content-Type", "application/sdp"),
        ]
        if auth:
            headers.append(("Proxy-Authorization" if auth.startswith("P:") else "Authorization",
                            auth[2:] if auth.startswith("P:") else auth))
        body = media.offer(self.local_ip, call.rtp.port)
        return sip.Message(f"INVITE {call.remote_uri} SIP/2.0", headers, body)

    def _outgoing(self, call: Call) -> None:
        auth = ""
        try:
            for _attempt in range(2):
                invite = self._invite(call, auth)
                call.invite = invite
                reply = self._request(invite, timeout=180)
                if reply is None:
                    self._end(call, "the router did not answer")
                    return
                if reply.status in (401, 407) and not auth:
                    self._ack_failure(invite, reply)
                    challenge = reply.get("WWW-Authenticate" if reply.status == 401 else "Proxy-Authenticate")
                    value = sip.authorization(challenge, "INVITE", call.remote_uri, self.auth_user, self.password)
                    auth = ("P:" if reply.status == 407 else "") + value
                    call.cseq += 1
                    continue
                if reply.status >= 300:
                    self._ack_failure(invite, reply)
                    self._end(call, _why(reply), status=reply.status)
                    return
                self._answered_out(call, reply)
                return
        except Exception as exc:  # noqa: BLE001
            logger.exception("landline: the call failed")
            self._end(call, str(exc))

    def _provisional(self, reply: sip.Message) -> None:
        call = self.call
        if call is None or reply.call_id != call.call_id:
            return
        if call.state == "dialing":
            call.state = "ringing"
            self.on_event("ringing", number=call.number)
        if "100rel" in reply.get("Require"):
            call.remote_tag = sip.param(reply.get("To"), "tag") or call.remote_tag
            rseq = reply.get("RSeq")
            call.cseq += 1
            target = sip.uri_of(reply.get("Contact")) or call.remote_uri
            routes = list(reversed(reply.all("Record-Route"))) or call.route
            prack = sip.Message(f"PRACK {target} SIP/2.0", [
                ("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
                *[("Route", r) for r in routes],
                ("From", f"<{call.local_uri}>;tag={call.local_tag}"),
                ("To", f"<{call.remote_uri}>;tag={call.remote_tag}"),
                ("Call-ID", call.call_id), ("CSeq", f"{call.cseq} PRACK"),
                ("RAck", f"{rseq} {reply.get('CSeq')}")])
            threading.Thread(target=self._request, args=(prack, 10), daemon=True).start()

    def _ack_failure(self, invite: sip.Message, reply: sip.Message) -> None:
        """ACK for a non-2xx final: same branch, same CSeq number (RFC 3261 17.1.1.3)."""
        headers = [("Via", invite.get("Via")), ("Max-Forwards", "70"),
                   *[("Route", r) for r in invite.all("Route")],
                   ("From", invite.get("From")), ("To", reply.get("To")),
                   ("Call-ID", invite.call_id), ("CSeq", f"{invite.cseq[0]} ACK")]
        self._send(sip.Message(f"ACK {invite.uri} SIP/2.0", headers))

    def _answered_out(self, call: Call, reply: sip.Message) -> None:
        call.remote_tag = sip.param(reply.get("To"), "tag")
        call.remote_target = sip.uri_of(reply.get("Contact")) or call.remote_uri
        if reply.all("Record-Route"):
            call.route = list(reversed(reply.all("Record-Route")))
        self._in_dialog(call, "ACK", cseq=call.cseq)
        if self.call is not call:  # hung up while it was being answered
            self._in_dialog(call, "BYE")
            return
        if not self._start_media(call, reply.body, answer=False):
            self._in_dialog(call, "BYE")
            self._end(call, "the line answered with no sound this computer can play")
            return
        call.state, call.started = "active", time.monotonic()
        self.on_event("answered", number=call.number)

    # --- calls in ---------------------------------------------------------------------------
    def _on_invite(self, invite: sip.Message) -> None:
        call = self.call
        if call is not None and invite.call_id == call.call_id:
            return  # a re-INVITE or repeat: keep the media as it is
        if call is not None:
            self._send(sip.response(invite, 486, to_tag=sip.new_tag()))
            return
        call = Call(invite.call_id, sip.new_tag(), False, sip.user_of(invite.get("From")))
        call.name = sip.display_name(invite.get("P-Asserted-Identity") or invite.get("From"))
        asserted = invite.get("P-Asserted-Identity")
        if asserted:
            call.number = sip.user_of(asserted)
        call.invite = invite
        call.remote_tag = sip.param(invite.get("From"), "tag")
        call.remote_target = sip.uri_of(invite.get("Contact"))
        call.route = invite.all("Record-Route")
        call.local_uri, call.remote_uri = sip.uri_of(invite.get("To")), sip.uri_of(invite.get("From"))
        call.rtp = media.RtpSession.bind(self.local_ip, None)
        if media.pick(media.parse_sdp(invite.body)[2]) is None:
            self.call = None
            call.rtp.stop()
            self._send(sip.response(invite, 488, to_tag=call.local_tag))
            self.on_event("ended", number=call.number,
                          reason="a call came in with no sound this computer can play (488)")
            return
        self.call = call
        self._send(sip.response(invite, 100))
        self._send(sip.response(invite, 180, to_tag=call.local_tag,
                                extra=[("Contact", self._contact())]))
        self.on_event("incoming", number=call.number, name=call.name)

    def answer(self) -> None:
        call = self.call
        if call is None or call.outgoing or call.state != "incoming":
            raise RuntimeError("no call is ringing")
        if self.sound_factory is not None:
            call.rtp.sound = self.sound_factory()
        ip, port, codecs, dtmf = media.parse_sdp(call.invite.body)
        chosen = media.pick(codecs)
        if not ip or not port or chosen is None or not media.can_play(chosen[1]):
            self._send(sip.response(call.invite, 488, to_tag=call.local_tag))
            self._end(call, "this computer cannot play the sound this call offered (488)")
            return
        body = media.offer(self.local_ip, call.rtp.port, only=chosen[1])
        self._send(sip.response(call.invite, 200, to_tag=call.local_tag, body=body,
                                extra=[("Contact", self._contact()), ("Allow", ALLOW)]))
        call.rtp.start((ip, port), chosen[0], chosen[1], dtmf)
        call.state, call.started = "active", time.monotonic()
        self.on_event("answered", number=call.number)

    def _on_ack(self, ack: sip.Message) -> None:
        pass  # TLS is reliable: nothing to stop retransmitting

    def _on_bye(self, bye: sip.Message) -> None:
        call = self.call
        if call is None or bye.call_id != call.call_id:
            self._send(sip.response(bye, 481))
            return
        self._send(sip.response(bye, 200))
        self._end(call, "the other side hung up")

    def _on_cancel(self, cancel: sip.Message) -> None:
        call = self.call
        self._send(sip.response(cancel, 200))
        if call is not None and cancel.call_id == call.call_id and call.state == "incoming":
            self._send(sip.response(call.invite, 487, to_tag=call.local_tag))
            self._end(call, "missed call")

    # --- ending -------------------------------------------------------------------------------
    def hangup(self) -> None:
        """End, cancel or decline whatever call there is."""
        call = self.call
        if call is None:
            raise RuntimeError("there is no call")
        if not call.outgoing and call.state == "incoming":
            self._send(sip.response(call.invite, 603, to_tag=call.local_tag))
            self._end(call, "declined")
        elif call.outgoing and call.state in ("dialing", "ringing"):
            invite = call.invite
            cancel = sip.Message(f"CANCEL {invite.uri} SIP/2.0", [
                ("Via", invite.get("Via")), ("Max-Forwards", "70"),
                *[("Route", r) for r in invite.all("Route")],
                ("From", invite.get("From")), ("To", invite.get("To")),
                ("Call-ID", invite.call_id), ("CSeq", f"{invite.cseq[0]} CANCEL")])
            self._end(call, "cancelled")
            self._send(cancel)
        else:
            self._end(call, "hung up")
            threading.Thread(target=self._in_dialog, args=(call, "BYE"), daemon=True).start()

    def dtmf(self, digit: str) -> bool:
        call = self.call
        return bool(call and call.state == "active" and call.rtp and call.rtp.dtmf(digit))

    def _in_dialog(self, call: Call, method: str, cseq: Optional[int] = None) -> None:
        if cseq is None:
            call.cseq += 1
        number = call.cseq if cseq is None else cseq
        local = f"<{call.local_uri}>;tag={call.local_tag}"
        remote = f"<{call.remote_uri}>;tag={call.remote_tag}"
        message = sip.Message(f"{method} {call.remote_target or call.remote_uri} SIP/2.0", [
            ("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
            *[("Route", r) for r in call.route],
            ("From", local), ("To", remote), ("Call-ID", call.call_id),
            ("CSeq", f"{number} {method}"), ("User-Agent", USER_AGENT)])
        if method == "ACK":
            self._send(message)
        else:
            self._request(message, timeout=10)

    def _start_media(self, call: Call, body: str, answer: bool) -> bool:
        ip, port, codecs, dtmf = media.parse_sdp(body)
        chosen = media.pick(codecs)
        if not ip or not port or chosen is None or not media.can_play(chosen[1]):
            return False
        try:
            call.rtp.start((ip, port), chosen[0], chosen[1], dtmf)
        except Exception as exc:  # noqa: BLE001 - a codec that will not start ends the call
            logger.warning("landline: the sound would not start: %s", exc)
            return False
        return True

    def _end(self, call: Call, reason: str, status: int = 0) -> None:
        if self.call is call:
            self.call = None
        if call.rtp is not None:
            call.rtp.stop()
        seconds = int(time.monotonic() - call.started) if call.started else 0
        call.state = "ended"
        self.on_event("ended", number=call.number, reason=reason, seconds=seconds, status=status)


def _why(reply: sip.Message) -> str:
    return {486: "the line is busy", 600: "the line is busy", 603: "the call was declined",
            404: "that number does not exist", 480: "the number is not reachable",
            487: "cancelled", 488: "the line would not take any sound this computer can play (488)"
            }.get(reply.status, f"the network refused the call ({reply.status} {reply.reason})")
