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

from shani_chronoa.landline import media, sip, video

logger = logging.getLogger(__name__)

USER_AGENT = "JUICEJFV-1.3.32"
EXPIRES = 3600
KEEPALIVE_SECONDS = 25
ALLOW = "INVITE, ACK, CANCEL, BYE, OPTIONS, UPDATE, PRACK, NOTIFY, MESSAGE, INFO"
MMTEL = '+g.3gpp.icsi-ref="urn%3Aurn-7%3A3gpp-service.ims.icsi.mmtel"'
FEATURES = (MMTEL + ';+g.3gpp.iari-ref="urn%3Aurn-7%3A3gpp-application.ims.iari.rcs.jio.eucr"'
            ';+g.gsma.rcs.telephony="none"')

#: The Contact parameter that says this device can be a **video** caller.
#:
#: **This spelling is the one thing in the video path not measured against the
#: router.** What actually makes a call a video call is the `m=video` section
#: `video.with_video` writes, and that is unambiguous; this rides in the Contact
#: beside the MMTel ICSI so the network sees the capability *before* the call
#: rather than only once the SDP arrives. A tag the network does not recognise
#: is a call it will not take as video, so it is one constant here rather than a
#: string spelled out across four call sites - and the live probe is what
#: settles it, not a confident guess.
VIDEO_TAG = ";video"


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
        self.video: Optional[video.VideoRtpSession] = None
        self.started = 0.0
        #: Whether this call is on hold. A held call is still a live dialog with
        #: a live port - it is just not sending or playing - which is what lets
        #: `swap()` resume it without a new session.
        self.held = False


class Line:
    def __init__(self, host: str, creds: Dict[str, str], instance: str,
                 on_event: Callable[..., None] = lambda kind, **info: None,
                 port: int = 5068, sound_factory: Optional[Callable[[], object]] = None,
                 video_factory: Optional[Callable[[], video.VideoRtpSession]] = None) -> None:
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
        self.video_factory = video_factory
        self.registered = False
        #: Every live call, oldest first. One call is what this line did before
        #: hold landed; two is what makes hold worth having, because a hold with
        #: nothing to swap *to* is a mute with extra packets.
        self.calls: List[Call] = []
        #: The room a conference mixes into, or None before `merge()`. Held by
        #: the line rather than by the conference call because every participant
        #: sends it, and the conference dialog is only the network's side of it.
        self.mixer: Optional[media.Mixer] = None
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
        #: Whether this machine can be a video caller. None means "not asked yet":
        #: `video.available()` shells out to ffmpeg, and the Contact header is
        #: built on every request in a call.
        self._video_ok: Optional[bool] = None

    @property
    def call(self) -> Optional[Call]:
        """The call the line is *on*: not held, and the oldest when all are.

        With one call - everything here until hold landed - that is simply the
        call, so every existing reader is unchanged. With two it is the live one,
        which is what the strip, the events and the hangup button are about; the
        held one is reachable through `calls`.
        """
        for call in self.calls:
            if not call.held:
                return call
        return self.calls[0] if self.calls else None

    def _video_capable(self) -> bool:
        """Whether to offer video at all, asked of the machine that would send it.

        Cached because `video.available()` is a subprocess and `_contact()` runs
        on every REGISTER and every in-dialog request. A machine with no camera
        or no ffmpeg answers False and gets the audio-only line it always did -
        which is the whole point of asking rather than assuming.
        """
        if self._video_ok is None:
            try:
                self._video_ok, _why = video.available()
            except Exception:  # noqa: BLE001 - an unprobeable camera is not a camera
                self._video_ok = False
        return bool(self._video_ok)

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
        # While stopping, a request still in the reader thread (a BYE 200, say)
        # must not write to a closing socket - that raced to a BrokenPipe at
        # shutdown. Silently drop it; the line is going away.
        if self._stop.is_set():
            return
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

    def _contact(self, expires: Optional[int] = None, focus: bool = False) -> str:
        c = (f"<sip:{self.number}@{self.local_ip}:{self.local_port};transport=tls;ob>"
             f';+sip.instance="{self.instance}";{FEATURES}')
        if self._video_capable():
            c += VIDEO_TAG
        # `isfocus` (RFC 4579) says *this* endpoint is the conference's focus -
        # the one that holds the room and tells everybody else to come to it.
        # It is what makes a dialog a conference rather than a call.
        if focus:
            c += ";isfocus"
        return c + (";reg-id=1" if expires is not None else "")

    def _body(self, call: Call, only: Optional[int] = None) -> str:
        """Our SDP body, with the video m-line when this machine has a camera.

        The audio offer is built exactly as it was and the section is appended
        after it, because RFC 4566 puts session-level lines first and media
        sections last - a body whose `m=video` precedes the `m=audio` it belongs
        after is a different document, not a formatting choice.
        """
        body = media.offer(self.local_ip, call.rtp.port, only=only)   # type: ignore[union-attr]
        if call.video is not None:
            body = video.with_video(body, self.local_ip, call.video.port)
        return body

    def _start_video(self, call: Call, body: str) -> bool:
        """Point our camera and our screen at the far end's video m-line.

        `m=video 0` is a **decline, not a port**: the far end answered
        audio-only, and taking 0 as a port would send every packet nowhere while
        reporting success. A body with no `m=video` at all is a far end that does
        not do video - the ordinary case for a phone on a voice call, and the
        reason this is not an error.

        A failure here never takes the call down: audio is already flowing, and
        a call that ends because the picture would not start is a worse outcome
        than a call that carries sound and nothing else.
        """
        if call.video is None:
            return False
        ip, port, codecs = video.parse_video(body)
        if not ip or not port:
            return False
        pt = codecs[0][0] if codecs else video.H264_PT
        try:
            call.video.start((ip, port), pt)
        except Exception as exc:  # noqa: BLE001 - a picture that will not start is not a reason to lose audio
            logger.warning("landline: the picture would not start: %s", exc)
            return False
        return True

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
                   "CANCEL": self._on_cancel, "UPDATE": self._on_update}.get(message.method)
        if handler is not None:
            handler(message)
        else:
            self._send(sip.response(message, 200))  # OPTIONS, NOTIFY, INFO, MESSAGE

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
        live = self.call
        if live is not None:
            if live.state != "active":
                raise RuntimeError("a call is already in progress")
            # A second number while a call is live: the first is held and the
            # second rings. That is what a phone does, and it is what makes
            # hold worth having - a hold with nothing to swap *to* is a mute
            # with extra packets. Refusing the second number would leave the
            # first call with no way to be put aside and picked back up.
            if not self.hold():
                return
        user = dial_user(number)
        if not user:
            raise ValueError("that is not a number")
        call = Call(sip.new_call_id(self.local_ip), sip.new_tag(), True, user)
        call.local_uri, call.remote_uri = self.public, f"sip:{user}@{self.realm}"
        call.route = self._routes()
        call.rtp = media.RtpSession.bind(self.local_ip, self.sound_factory() if self.sound_factory else None)
        if self.video_factory is not None and self._video_capable():
            try:
                call.video = self.video_factory()
            except Exception as exc:  # noqa: BLE001 - no free port pair is no video, not no call
                logger.warning("landline: no video session for this call: %s", exc)
        self.calls.append(call)
        threading.Thread(target=self._outgoing, args=(call,), daemon=True, name="landline-dial").start()

    def _invite(self, call: Call, auth: str = "", focus: bool = False) -> sip.Message:
        headers = [
            ("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
            *[("Route", r) for r in call.route],
            ("From", f"<{call.local_uri}>;tag={call.local_tag}"), ("To", f"<{call.remote_uri}>"),
            ("Call-ID", call.call_id), ("CSeq", f"{call.cseq} INVITE"),
            ("Contact", self._contact(focus=focus)), ("Allow", ALLOW),
            ("Supported", "100rel, timer"),
            ("Accept-Contact", f"*;{MMTEL}"),
            ("P-Preferred-Service", "urn:urn-7:3gpp-service.ims.icsi.mmtel"),
            ("User-Agent", USER_AGENT), ("P-Access-Network-Info", "IEEE-802.11"),
            ("P-Preferred-Identity", f"<{self.public}>"), ("Content-Type", "application/sdp"),
        ]
        if auth:
            headers.append(("Proxy-Authorization" if auth.startswith("P:") else "Authorization",
                            auth[2:] if auth.startswith("P:") else auth))
        body = self._body(call)
        return sip.Message(f"INVITE {call.remote_uri} SIP/2.0", headers, body)

    def _outgoing(self, call: Call) -> None:
        auth = ""
        try:
            for _attempt in range(2):
                invite = self._invite(call, auth, focus=self.mixer is not None
                                      and call.number == "Conference")
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

    def hold(self) -> bool:
        """Put the live call on hold: a re-INVITE that says we are only sending.

        `a=sendonly` rather than `a=inactive` because the far end should still
        hear *something* - hold tone or comfort noise - where `inactive` is a
        stream it has to decide what to do with. Measured on the fake router's
        own answer: a far end that answers a hold offer with `a=recvonly` has
        taken it, and one that answers `a=inactive` has said it will stop too.

        **The RTP is gated here and not only by the SDP.** A held call sends
        nothing at all - not silence, which would be a packet claiming a live
        microphone - and does not play what arrives, because a far end that has
        not honoured the hold yet would otherwise be heard over the call that
        replaced it.

        Returns whether the hold took. A far end that refuses the re-INVITE has
        said no, and reporting a hold that did not happen is the chip-reads-one-
        thing-while-the-machine-does-another defect this line has already had.
        """
        call = self.call
        if call is None or call.state != "active" or call.held:
            return False
        if not self._re_offer(call, "sendonly"):
            return False
        call.held = True
        if call.rtp is not None:
            call.rtp.held = True
        return True

    #: The conference factory, as the MMTel specification spells it: the `mmtel`
    #: public service identity at `conf-factory.<home domain>`. **Unverified
    #: against this router** - it is the form the standard names, and whether
    #: Jio's IMS hosts one here is a live question, not a fact this line has.
    CONF_FACTORY = "sip:mmtel@conf-factory.{realm}"

    def merge(self) -> bool:
        """Put every live call into one conference, mixing on this computer.

        Two dialogs, and the difference between them is which way the audio
        goes: the participants keep their own connection to their own far end,
        and the conference dialog is the network's side of the room. Each
        participant's `rtp.sound` becomes a room input, and the room is what
        their own stream carries back out - so a person hears everybody and
        nobody hears themselves.

        **Unverified against the real network.** The SIP below is checked
        against the fake router and the mixing is measured as arithmetic, but
        whether Jio's IMS answers `mmtel@conf-factory` at all, and accepts an
        `isfocus` Contact from a UE, is a live question this line has not asked.
        So `merge()` starts the dialog rather than claiming a conference: the
        events say `answered` when the factory takes it, and `ended` with the
        reason when it does not.

        Refuses rather than starting something nobody asked for: at least two
        calls, no more than `media.MAX_CALLS` of them.
        """
        live = [c for c in self.calls if not c.held]
        if len(live) < 2:
            return False
        if len(self.calls) + 1 > media.MAX_CALLS:
            return False
        room = media.Mixer()
        for call in live:
            # What this participant says goes into the room, and what the room
            # says comes out over the same stream. The `RtpSession` re-points
            # its two threads rather than starting another pair, so there is
            # exactly one sender and one receiver per far end either way.
            call.rtp.sound = media.MixInput(call, room)
        self.mixer = room
        conference = Call(sip.new_call_id(self.local_ip), sip.new_tag(), True, "Conference")
        conference.name = f"{len(live)} people"
        conference.local_uri, conference.remote_uri = self.public, self.CONF_FACTORY.format(
            realm=self.realm)
        conference.route = self._routes()
        conference.rtp = media.RtpSession.bind(self.local_ip, room)
        self.calls.append(conference)
        threading.Thread(target=self._outgoing, args=(conference,), daemon=True,
                         name="landline-conference").start()
        return True

    def swap(self) -> bool:
        """Trade the live call for the held one - hold this, resume that.

        Both halves of one action, because that is what Swap means on a phone:
        with two calls, "which one am I on" toggles, and a press that only
        held the current one would need a second press to do anything.
        """
        live = self.call
        held = [c for c in self.calls if c.held]
        if live is None or not held:
            return False
        # Swap trades two *answered* calls. A ringing call has no session whose
        # direction could be changed, and resuming into one would report a
        # call came back that was never there - so it is a refusal and not a
        # half-action.
        if any(c.state != "active" for c in [live, *held]):
            return False
        if not self._re_offer(live, "sendonly"):
            return False
        live.held = True
        if live.rtp is not None:
            live.rtp.held = True
        for call in held:
            if self._re_offer(call, "sendrecv"):
                call.held = False
                if call.rtp is not None:
                    call.rtp.held = False
        return True

    def _re_offer(self, call: Call, direction: str) -> bool:
        """A re-INVITE that changes only the direction the sound goes.

        Same Call-ID, same tags, the same ports and the same codec - so the far
        end sees a direction change on a live session rather than a new one. The
        codec is the one already negotiated rather than a fresh offer: re-offering
        every codec we can play mid-call invites the far end to renegotiate.
        """
        if call.rtp is None or not call.remote_target:
            return False
        try:
            body = media.offer(self.local_ip, call.rtp.port, only=call.rtp.codec,
                               direction=direction)
            reply = self._in_dialog(call, "INVITE", body=body)
        except Exception as exc:  # noqa: BLE001 - a re-offer we cannot send is a refusal
            logger.warning("landline: the re-offer would not send: %s", exc)
            return False
        if reply is None:
            logger.warning("landline: the router did not answer the re-offer")
            return False
        if reply.status != 200:
            # A non-2xx final to an INVITE is ACKed on the CSeq it arrived with
            # (RFC 3261 17.1.1.3) and leaves the dialog standing, so the CSeq
            # does not go back - a CSeq that moved backwards would collide with
            # a transaction the far end has already answered.
            self._in_dialog(call, "ACK", cseq=call.cseq)
            logger.warning("landline: the far end refused the re-offer (%s %s)",
                           reply.status, reply.reason)
            return False
        return True

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
        if call not in self.calls:  # hung up while it was being answered
            self._in_dialog(call, "BYE")
            return
        # A renegotiated call answers the INVITE with no SDP of its own - the
        # session parameters are the ones the mid-call re-offer already agreed
        # (measured: the callee's phone re-offers while ringing and the 200 OK
        # that follows carries a body only when nothing renegotiated it). Taking
        # that as "no sound this computer can play" ended a call that was, by
        # then, fully connected.
        if not reply.body.strip():
            if call.rtp.coder is not None:
                call.state, call.started = "active", time.monotonic()
                self.on_event("answered", number=call.number)
                return
            self._in_dialog(call, "BYE")
            self._end(call, "the line answered with no sound at all")
            return
        if not self._start_media(call, reply.body, answer=False):
            self._in_dialog(call, "BYE")
            self._end(call, "the line answered with no sound this computer can play")
            return
        call.state, call.started = "active", time.monotonic()
        if call.video is not None and not self._start_video(call, reply.body):
            # A far end that answers with no video m-line, or with `m=video 0`,
            # is a voice call - which is the ordinary case, so the picture stops
            # and the sound carries on.
            call.video.stop()
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
        if self.video_factory is not None and self._video_capable():
            try:
                call.video = self.video_factory()
            except Exception as exc:  # noqa: BLE001 - an incoming call is still answerable without video
                logger.warning("landline: no video session for this call: %s", exc)
        if media.pick(media.parse_sdp(invite.body)[2]) is None:
            # Never added, so nothing to take back out.
            call.rtp.stop()
            if call.video is not None:
                call.video.stop()
            self._send(sip.response(invite, 488, to_tag=call.local_tag))
            self.on_event("ended", number=call.number,
                          reason="a call came in with no sound this computer can play (488)")
            return
        self.calls.append(call)
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
        body = self._body(call, only=chosen[1])
        self._send(sip.response(call.invite, 200, to_tag=call.local_tag, body=body,
                                extra=[("Contact", self._contact()), ("Allow", ALLOW)]))
        packing = media.packing_for(media.negotiated(codecs, chosen[0]))
        call.rtp.start((ip, port), chosen[0], chosen[1], dtmf, packing)
        if call.video is not None and not self._start_video(call, call.invite.body):
            call.video.stop()
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

    def _on_update(self, update: sip.Message) -> None:
        """The far end re-offering the session while the call is still early.

        Measured on the router: the callee's own phone sends an UPDATE carrying
        an SDP offer a fraction of a second after 180 Ringing, before it has
        been answered. RFC 3311 requires the 200 OK to that to carry an SDP
        answer, and answering it with no body - which is what the generic
        handler did - leaves the far end with nothing to accept, so it waits
        and then tears the call down with 487. The answer is ours to give, and
        it is the same one `answer()` gives a ringing INVITE.

        The media is started here too, because a re-offer names the port the
        far end is now sending from: without it the call would connect with no
        audio until the 200 OK re-pointed it, and an answered call that has
        been silent sounds broken rather than merely late.
        """
        call = self.call
        if call is None or update.call_id != call.call_id:
            self._send(sip.response(update, 200))
            return
        if not update.body.strip():
            # A session timer refresh or a hold-offer: nothing to negotiate.
            self._send(sip.response(update, 200, to_tag=call.local_tag,
                                    extra=[("Contact", self._contact())]))
            return
        ip, port, codecs, dtmf = media.parse_sdp(update.body)
        chosen = media.pick(codecs)
        if not ip or not port or chosen is None or not media.can_play(chosen[1]):
            self._send(sip.response(update, 488, to_tag=call.local_tag))
            self._end(call, "the sound re-offered mid-call is not one this computer can play")
            return
        if call.rtp.sound is None and self.sound_factory is not None:
            call.rtp.sound = self.sound_factory()
        body = media.offer(self.local_ip, call.rtp.port, only=chosen[1])
        self._send(sip.response(update, 200, to_tag=call.local_tag, body=body,
                                extra=[("Contact", self._contact())]))
        packing = media.packing_for(media.negotiated(codecs, chosen[0]))
        try:
            call.rtp.start((ip, port), chosen[0], chosen[1], dtmf, packing)
        except Exception as exc:  # noqa: BLE001 - a re-offer we cannot take ends the call
            logger.warning("landline: the re-offered sound would not start: %s", exc)
            self._end(call, "this computer could not take the sound re-offered mid-call")
            return
        if call.video is not None and not self._start_video(call, update.body):
            call.video.stop()

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

    def _in_dialog(self, call: Call, method: str, cseq: Optional[int] = None,
                   body: str = "") -> Optional[sip.Message]:
        """A request inside a live dialog: same Call-ID, same tags, next CSeq.

        Returns the far end's response - the reason `hold()` can tell a hold
        that took from one that was refused - or None for the ACK, which is sent
        on the reliable TLS connection and has nothing to wait for.
        """
        if cseq is None:
            call.cseq += 1
        number = call.cseq if cseq is None else cseq
        local = f"<{call.local_uri}>;tag={call.local_tag}"
        remote = f"<{call.remote_uri}>;tag={call.remote_tag}"
        headers = [("Via", self._via(sip.new_branch())), ("Max-Forwards", "70"),
                   *[("Route", r) for r in call.route],
                   ("From", local), ("To", remote), ("Call-ID", call.call_id),
                   ("CSeq", f"{number} {method}"), ("User-Agent", USER_AGENT)]
        if body:
            headers.append(("Content-Type", "application/sdp"))
        message = sip.Message(f"{method} {call.remote_target or call.remote_uri} SIP/2.0",
                              headers, body)
        if method == "ACK":
            self._send(message)
            return None
        return self._request(message, timeout=10)

    def _start_media(self, call: Call, body: str, answer: bool) -> bool:
        ip, port, codecs, dtmf = media.parse_sdp(body)
        chosen = media.pick(codecs)
        if not ip or not port or chosen is None or not media.can_play(chosen[1]):
            return False
        packing = media.packing_for(media.negotiated(codecs, chosen[0]))
        try:
            call.rtp.start((ip, port), chosen[0], chosen[1], dtmf, packing)
        except Exception as exc:  # noqa: BLE001 - a codec that will not start ends the call
            logger.warning("landline: the sound would not start: %s", exc)
            return False
        return True

    def _end(self, call: Call, reason: str, status: int = 0) -> None:
        self.calls = [c for c in self.calls if c is not call]
        if call.rtp is not None:
            call.rtp.stop()
        if call.video is not None:
            call.video.stop()
        seconds = int(time.monotonic() - call.started) if call.started else 0
        call.state = "ended"
        self.on_event("ended", number=call.number, reason=reason, seconds=seconds, status=status)


def _why(reply: sip.Message) -> str:
    return {486: "the line is busy", 600: "the line is busy", 603: "the call was declined",
            404: "that number does not exist", 480: "the number is not reachable",
            487: "cancelled", 488: "the line would not take any sound this computer can play (488)"
            }.get(reply.status, f"the network refused the call ({reply.status} {reply.reason})")
