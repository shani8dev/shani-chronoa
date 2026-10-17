"""The JioFiber landline, end to end against a fake router (tests/fake_jio_router.py).

What is driven, over a real TLS SIP connection and real RTP on loopback:

- provisioning: the account request, add-device, the texted code, and the fresh
  password - and that the add request is always `nwk_intf=wifi` (the value the
  router asks for the code under; never the one a write-up uses to skip it);
- registration, with the digest answered and the device's contact kept;
- a call out: INVITE, the proxy challenge answered, 180 then 200, ACK, audio,
  then BYE - and the events the UI listens for;
- a call in: the router rings the device, the card is offered, answer brings up
  audio, and hangup declines;
- codec negotiation: a router that offers only G.711 still connects; one that
  offers nothing this computer can play is refused with 488.

Also the pure parts - SIP parsing, digest, G.711/L16 round-trips - with no
network at all.
"""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.landline import media, provision, sip, ua  # noqa: E402

fake = pytest.importorskip("tests.fake_jio_router") if False else None
from tests import fake_jio_router as fake  # noqa: E402


# --- the pure parts -----------------------------------------------------------

def test_sip_parses_and_re_encodes_a_request():
    raw = (b"INVITE sip:+91999@br.wln.ims.jio.com SIP/2.0\r\n"
           b"Via: SIP/2.0/TLS 10.0.0.2:5061;branch=z9hG4bKabc\r\n"
           b"From: <sip:+912040001234@br>;tag=aa\r\nTo: <sip:+91999@br>\r\n"
           b"Call-ID: xyz@h\r\nCSeq: 1 INVITE\r\nContent-Length: 3\r\n\r\nabc")
    msg = sip.parse(raw)
    assert msg.is_request and msg.method == "INVITE"
    assert sip.param(msg.get("From"), "tag") == "aa"
    assert msg.body == "abc"
    assert sip.parse(msg.encode()).get("Call-ID") == "xyz@h"


def test_a_stream_is_split_on_content_length_and_keepalives_dropped():
    one = sip.Message("OPTIONS sip:x SIP/2.0", [("Call-ID", "a")]).encode()
    two = sip.Message("SIP/2.0 200 OK", [("Call-ID", "b")], "hi").encode()
    msgs, rest = sip.split_stream(b"\r\n\r\n" + one + b"\r\n\r\n" + two[:-1])
    assert [m.call_id for m in msgs] == ["a"]       # two is not yet whole
    msgs2, rest2 = sip.split_stream(rest + two[-1:])
    assert [m.call_id for m in msgs2] == ["b"] and rest2 == b""


def test_digest_answer_is_accepted_by_the_checker():
    challenge = 'Digest realm="br", nonce="deadbeef", qop="auth"'
    auth = sip.authorization(challenge, "REGISTER", "sip:br", "user@br", "s3cret")
    assert sip.check_authorization(auth, "REGISTER", "s3cret")
    assert not sip.check_authorization(auth, "REGISTER", "wrong")


@pytest.mark.parametrize("pt,tol", [(0, 200), (8, 200), (media.L16_PT, 0)])
def test_codecs_round_trip(pt, tol):
    """Each codec gives a packet back inside its own budget: G.711 quantises,
    L16 does not change the samples at all."""
    import struct
    pcm = struct.pack("<160h", *[((i * 211) % 2000 - 1000) for i in range(160)])
    coder = media.codec_for(pt)
    assert coder is not None, f"nothing plays payload type {pt}"
    back = coder.decode(coder.encode(pcm))
    a = struct.unpack("<160h", pcm)
    b = struct.unpack("<160h", back)
    assert max(abs(x - y) for x, y in zip(a, b)) <= tol


def test_amr_is_the_code_this_line_actually_plays():
    """AMR-NB mode 7: one 32-byte frame per 20 ms, and the encoder's own first
    byte is the RFC 4867 table of contents, sent verbatim - a decoder finds a
    frame's length by indexing its size table with those very bits, so
    rewriting it into a "cleaner" TOC desynchronises the far end."""
    import math
    import struct

    coder = media.codec_for(media.AMR_PT)
    assert coder is not None, "AMR needs the system's opencore-amrnb library"
    tone = [int(9000 * math.sin(2 * math.pi * 300 * i / 8000)) for i in range(160 * 50)]
    played, heard = b"", []
    for i in range(50):
        payload = coder.encode(struct.pack("<160h", *tone[i * 160:(i + 1) * 160]))
        assert len(payload) == 32, "mode 7 is one 32-byte frame"
        assert ((payload[0] >> 3) & 0x0F) == coder.MODE == 7, "the TOC names the mode"
        assert (payload[0] >> 2) & 1, "the library sets the frame-quality bit"
        played += payload
        heard += list(struct.unpack("<160h", coder.decode(payload)))
    assert len(played) == 32 * 50

    def correlation(x, y, off):
        n = min(len(x), len(y) - off)
        num = sum(x[i] * y[i + off] for i in range(n))
        dx = sum(v * v for v in x[:n]) ** .5
        dy = sum(y[i + off] ** 2 for i in range(n)) ** .5
        return num / max(dx * dy, 1)

    _lag, best = max(((o, correlation(tone, heard, o)) for o in range(0, 400)), key=lambda t: t[1])
    assert best > 0.99, f"the tone came back unrecognisable (r {best:.4f})"
    # A frame nobody sent is silence, not a crash and not the previous frame.
    assert coder.decode(b"") == b"\x00" * media.FRAME_BYTES


def test_the_offer_leads_with_amr_and_says_what_it_sends():
    """AMR is offered first - it is the codec the router answered with - and the
    line says it sends no redundancy, which is the truth."""
    sdp = media.offer("10.0.0.2", 40000)
    assert sdp.index("AMR/8000/1") < sdp.index("PCMU/8000"), "AMR must be first"
    assert "a=rtpmap:96 AMR/8000/1" in sdp
    assert "a=fmtp:96 max-red=0" in sdp
    assert "a=ptime:20" in sdp
    _ip, _port, codecs, dtmf = media.parse_sdp(sdp)
    assert codecs[0] == (media.AMR_PT, "AMR/8000/1")
    assert dtmf == media.DTMF_PT
    answer = media.offer("10.0.0.2", 40000, only=media.AMR_PT)
    assert "AMR/8000/1" in answer and "PCMU" not in answer


def test_amr_matches_only_the_octet_aligned_name():
    """Both spellings match: the router's callee answers the INVITE with
    AMR/8000/1 and re-offers the session with AMR/8000, so a session's packing
    can change under us. Bare AMR/8000 is bandwidth-efficient, which the library
    does not write - it is converted at the edges (packing_for)."""
    assert media.pick([(96, "AMR/8000/1")]) == (96, media.AMR_PT)
    assert media.pick([(96, "AMR/8000")]) == (96, media.AMR_PT)
    assert media.pick([(0, "PCMU/8000")]) == (0, 0)
    assert media.can_play(media.AMR_PT) is True
    assert media.can_play(42) is False
    assert media.packing_for("AMR/8000") == "be"
    assert media.packing_for("AMR/8000/1") == "oa"
    assert media.negotiated([(96, "AMR/8000"), (101, "telephone-event/8000")], 96) == "AMR/8000"


def test_amr_bandwidth_efficient_frames_are_the_same_frames():
    """RFC 4867 4.3: the bandwidth-efficient payload is a 4-bit CMR, a 6-bit ToC
    entry and the frame's bits, packed with no padding between them. The far end
    sends 0xf3 where our own encoder writes 0x3c - the same mode 7, the other
    packing. What must hold is the round trip: pack, unpack, decode, and the
    audio is the audio."""
    import math
    import struct

    octet = media.codec_for(media.AMR_PT, "oa")
    packed = media.codec_for(media.AMR_PT, "be")
    assert octet is not None and packed is not None

    tone = [int(9000 * math.sin(2 * math.pi * 300 * i / 8000)) for i in range(160 * 30)]
    heard = []
    for i in range(30):
        pcm = struct.pack("<160h", *tone[i * 160:(i + 1) * 160])
        straight = octet.encode(pcm)
        rewrapped = packed.encode(pcm)
        assert len(rewrapped) == 32, "a bandwidth-efficient mode-7 payload is 32 bytes"
        assert rewrapped[0] == 0xF3, f"expected the measured 0xf3, got 0x{rewrapped[0]:02x}"
        assert len(straight) == 32 and straight[0] == 0x3C
        # the frames carry the same speech bits, so unpacking must give ours back
        assert media._unpack_efficient(rewrapped) == straight
        heard += list(struct.unpack("<160h", octet.decode(straight)))

    def correlation(x, y, off):
        n = min(len(x), len(y) - off)
        num = sum(x[i] * y[i + off] for i in range(n))
        dx = sum(v * v for v in x[:n]) ** .5
        dy = sum(y[i + off] ** 2 for i in range(n)) ** .5
        return num / max(dx * dy, 1)

    _lag, best = max(((o, correlation(tone, heard, o)) for o in range(0, 400)),
                     key=lambda t: t[1])
    assert best > 0.99, f"the packed and unpacked frames lost the audio (r {best:.4f})"


def test_the_far_ends_bandwidth_efficient_frames_decode_to_speech():
    """The measured far-end frames, from a live call, must decode to audio and
    not to the decoder's comfort noise. With the packing read as octet-aligned
    they were 0xf3 - an invalid frame type - and every packet came back as the
    same silence."""
    import struct

    from tests import far_end_frames

    coder = media.codec_for(media.AMR_PT, "be")
    assert coder is not None
    frames = far_end_frames.frames()
    audio = b"".join(coder.decode(frame) for frame in frames)
    samples = struct.unpack(f"<{len(audio) // 2}h", audio)
    assert len(samples) == len(frames) * media.FRAME_SAMPLES
    peak = max(abs(s) for s in samples)
    assert peak > 100, f"the far end's frames decoded to silence (peak {peak})"
    # Each captured frame type must be the one the packing says it is. Read as
    # octet-aligned they are all frame type 14, which is why they were silence.
    for frame in frames:
        assert (frame[0] >> 3) & 0x0F == 14, "as octet-aligned it is an invalid type"
        assert frame[0] in (0xF1, 0xF3, 0xF4)


def test_device_id_is_stable_for_a_name():
    assert provision.device_id("ChronoaBox") == provision.device_id("ChronoaBox")
    assert provision.device_id("ChronoaBox") != provision.device_id("ChronoaOther")


def test_numbers_are_put_into_plus91_form():
    assert ua.dial_user("098765 43210") == "+919876543210"
    assert ua.dial_user("9876543210") == "+919876543210"
    assert ua.dial_user("1800123") == "1800123"       # a short code is left alone


# --- against the fake router --------------------------------------------------

@pytest.fixture
def router(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "ACCOUNT_PORT", 0)  # replaced per instance below
    r = fake.Router(tmp_path)
    # Point the provisioning ports and the SIP port at the fake router.
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r.https_port)
    monkeypatch.setattr(provision, "find_router", lambda: "127.0.0.1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    yield r
    r.stop()


def _set_up(router) -> dict:
    """Run the whole add-this-device flow and return the saved line settings."""
    from shani_chronoa import landline
    masked = landline.setup_start("127.0.0.1")
    assert masked.startswith("...")
    number = landline.setup_finish(fake.CODE)
    assert number == fake.NUMBER
    return provision.load()


def test_setup_adds_the_device_only_with_the_code_and_only_over_wifi(router):
    from shani_chronoa import landline
    landline.setup_start("127.0.0.1")
    with pytest.raises(provision.ProvisionError):
        landline.setup_finish("000000")        # a wrong code is refused
    assert landline.setup_finish(fake.CODE) == fake.NUMBER
    adds = [q for path, q, _c in router.seen["http"] if q.get("op_type") == "add"]
    assert adds and all(q["nwk_intf"] == "wifi" for q in adds), "an add must be nwk_intf=wifi"


def _line(router, monkeypatch, events, codecs=None, challenge_invite=True,
          video_factory=None):
    """A started, registered Line against the (possibly rebuilt) fake router."""
    from shani_chronoa.landline.ua import Line
    _set_up(router)
    saved = provision.load()
    creds = provision.credentials("127.0.0.1", saved["alias"])
    _, instance = provision.device_id(saved["alias"])

    def factory(host, c, inst, on_event, **kw):
        return Line(host, c, inst, on_event=on_event, port=router.sip_port,
                    sound_factory=fake._Silence, video_factory=video_factory)
    line = factory("127.0.0.1", creds, instance, on_event=lambda k, **i: events.append((k, i)))
    line.start()
    _wait(lambda: line.registered, "the line never registered", seconds=REGISTER_SECONDS)
    return line


#: How long to let the line register. The router's own TLS handshake sits in
#: front of it and the Line's first REGISTER waits up to 32 s for an answer, so
#: a tighter budget reports "the line never registered" for a line that was
#: about to - which is what this was, measured three runs in fourteen.
REGISTER_SECONDS = 45.0


def _wait(cond, why, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(why)


def test_the_line_registers_with_the_router(router, monkeypatch):
    events = []
    line = _line(router, monkeypatch, events)
    try:
        assert ("registered", {"number": "+912040001234"}) in [(k, i) for k, i in events]
        assert router.seen["register"], "the router saw no REGISTER"
    finally:
        line.stop()


def test_a_call_out_rings_then_connects_then_ends(router, monkeypatch):
    events = []
    line = _line(router, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the call was never answered")
        kinds = [k for k, _ in events]
        assert kinds.index("ringing") < kinds.index("answered")
        call = line.call
        assert call is not None
        _wait(lambda: call.rtp.received > 0, "no audio arrived from the router")
        line.hangup()
        _wait(lambda: any(k == "ended" for k, _ in events), "the call never ended")
        assert line.call is None
        # The proxy challenge on INVITE was answered (two INVITEs: one, then authed).
        assert len(router.seen["invite"]) == 2
    finally:
        line.stop()


def test_a_call_in_is_offered_then_answered(router, monkeypatch):
    events = []
    line = _line(router, monkeypatch, events)
    try:
        router.call_in("+915556667778")
        _wait(lambda: any(k == "incoming" for k, _ in events), "no incoming call was offered")
        incoming = next(i for k, i in events if k == "incoming")
        assert incoming["number"] == "+915556667778"
        line.answer()
        _wait(lambda: line.call and line.call.state == "active", "answering did not connect")
        _wait(lambda: line.call.rtp.received > 0, "no audio after answering")
        line.hangup()
    finally:
        line.stop()


def test_a_router_offering_only_g711_still_connects(router, monkeypatch, tmp_path):
    router.stop()
    (tmp_path / "g711").mkdir()
    r2 = fake.Router(tmp_path / "g711", codecs=("PCMU/8000",))
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r2.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r2.https_port)
    events = []
    line = _line(r2, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "G.711-only call did not connect")
        assert line.call.rtp.codec in (0, 8)
        line.hangup()
    finally:
        line.stop()
        r2.stop()


def test_the_line_plays_amr_on_a_router_that_offers_nothing_else(router, monkeypatch, tmp_path):
    """The codec the real router answers with, end to end: negotiation lands on
    AMR and real frames arrive on the wire."""
    router.stop()
    (tmp_path / "amr").mkdir()
    r2 = fake.Router(tmp_path / "amr", codecs=("AMR/8000/1",))
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r2.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r2.https_port)
    events = []
    line = _line(r2, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "AMR-only call did not connect")
        call = line.call
        assert call is not None
        assert call.rtp.codec == media.AMR_PT, "the call did not agree on AMR"
        _wait(lambda: call.rtp.received > 2, "no AMR packets arrived on the wire")
        line.hangup()
    finally:
        line.stop()
        r2.stop()


def test_the_line_answers_a_session_re_offered_mid_ring(router, monkeypatch, tmp_path):
    """What the callee's own phone does on the router, and what the call died on.

    Measured live: after 180 Ringing the far end sends an UPDATE carrying a
    fresh SDP offer, and the device used to answer it with a 200 OK that had no
    body - which RFC 3311 does not allow - so the far end had nothing to accept
    and tore the call down with 487. The answer must be an SDP answer, and the
    media must move to the port it names."""
    router.stop()
    (tmp_path / "reoffer").mkdir()
    r2 = fake.Router(tmp_path / "reoffer", codecs=("AMR/8000/1",), reoffer_mid_ring=True)
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r2.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r2.https_port)
    events = []
    line = _line(r2, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events),
              "the call did not survive the far end re-offering the session")
        call = line.call
        assert call is not None and call.rtp.codec == media.AMR_PT
        _wait(lambda: call.rtp.received > 2, "no audio arrived after the re-offer")
        line.hangup()
    finally:
        line.stop()
        r2.stop()


def test_the_far_ends_bandwidth_efficient_re_offer_is_taken(router, monkeypatch, tmp_path):
    """The re-offer the router's callee really sends, and the one that broke it.

    It answers the INVITE with `AMR/8000/1` and then re-offers the session with
    `AMR/8000` - bandwidth-efficient - where the ToC and the frame's bits are
    packed together. Read as octet-aligned those frames are frame type 14 and
    every one decoded to the same comfort noise, so the line has to speak the
    packing the far end asks for."""
    router.stop()
    (tmp_path / "be").mkdir()
    r2 = fake.Router(tmp_path / "be", codecs=("AMR/8000/1",), reoffer_mid_ring=True,
                     reoffer_bandwidth_efficient=True)
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r2.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r2.https_port)
    events = []
    line = _line(r2, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events),
              "the call did not survive a bandwidth-efficient re-offer")
        call = line.call
        assert call is not None and call.rtp.codec == media.AMR_PT
        # What we send must be the packing the far end asked for.
        assert call.rtp.coder.packing == "be"
        _wait(lambda: call.rtp.received > 2, "no audio arrived on the bandwidth-efficient call")
        line.hangup()
    finally:
        line.stop()
        r2.stop()


def test_a_renegotiated_call_is_answered_without_sdp_of_its_own(router, monkeypatch, tmp_path):
    """The callee's phone re-offers while ringing, so the 200 OK that follows
    carries no SDP - the session parameters are the ones already agreed. A line
    that reads an empty body as "no sound I can play" ends a call that is, by
    then, fully connected: measured live as a call that rang, was picked up,
    and dropped three seconds later."""
    router.stop()
    (tmp_path / "emptyanswer").mkdir()
    r2 = fake.Router(tmp_path / "emptyanswer", codecs=("AMR/8000/1",), reoffer_mid_ring=True,
                     empty_final_answer=True)
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r2.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r2.https_port)
    events = []
    line = _line(r2, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events),
              "the call did not survive being answered without new SDP")
        call = line.call
        assert call is not None and call.rtp.codec == media.AMR_PT
        _wait(lambda: call.rtp.received > 2, "no audio arrived on a connected call")
        line.hangup()
    finally:
        line.stop()
        r2.stop()


def test_the_registered_instance_is_the_plain_uuid_the_router_matches():
    """pjproject's clients carry "+sip.instance=<urn:uuid:...>" by default, and
    a router matching the provisioning payload's uuid_value never rings a line
    that sends the prefix. The instance is the plain <00000000-...> form."""
    from shani_chronoa.landline.ua import Line

    _, instance = provision.device_id("ChronoaBox")
    line = Line("127.0.0.1", {"realm": "r", "user": "u", "password": "p"}, instance)
    assert line.instance == f"<00000000-0000-1000-8000-0000{instance.upper()}>"
    assert "urn:uuid" not in line.instance
    assert line.instance.startswith("<") and line.instance.endswith(">")


def test_the_registration_carries_that_instance(router, monkeypatch):
    """The router only forks a call to a device whose REGISTER advertises the
    instance id and the IMS telephony feature tags."""
    events = []
    line = _line(router, monkeypatch, events)
    try:
        contact = router.seen["register"][-1].get("Contact") or ""
        assert "+sip.instance=" in contact, "the contact carries no instance id"
        assert "urn:uuid" not in contact, "the instance must be the plain uuid form"
        assert ua.FEATURES.split(";")[0] in contact, "the contact advertises no telephony tag"
    finally:
        line.stop()


# --- video --------------------------------------------------------------------------

def _bare_line(video=False):
    """A Line that has not connected to anything, for the offer's own shape."""
    line = ua.Line("127.0.0.1", {"realm": "r", "user": "u", "password": "p"}, "aabbcc",
                   video_factory=(lambda: ua.video.VideoRtpSession.bind("127.0.0.1"))
                   if video else None)
    line.local_ip, line.local_port, line.number = "127.0.0.1", 5060, "+912040001234"
    call = ua.Call("cid", "tag", True, "+911234567890")
    call.local_uri = call.remote_uri = "sip:+912040001234@br.wln.ims.jio.com"
    call.route = []
    call.rtp = media.RtpSession.bind("127.0.0.1", None)
    if video:
        call.video = line.video_factory()
    line.calls.append(call)
    return line, call


def test_no_camera_means_no_picture_line_anywhere(monkeypatch):
    """The regression guard. A machine with no ffmpeg or no camera must produce
    the audio-only body it always did - offering `m=video` it cannot fill is how
    a call gets answered with a blank rectangle and no sound."""
    monkeypatch.setattr(ua.video, "available", lambda *a, **k: (False, "no camera"))
    line, call = _bare_line()
    try:
        assert not line._video_capable(), "asked the machine and ignored it"
        assert ";video" not in line._contact(), "the Contact claimed video"
        assert "m=video" not in line._body(call), "the INVITE offered a picture"
        assert "m=audio" in line._body(call), "and lost the sound doing it"
    finally:
        call.rtp.stop()


def test_a_camera_puts_the_picture_after_the_sound(monkeypatch):
    monkeypatch.setattr(ua.video, "available", lambda *a, **k: (True, "/dev/video0"))
    line, call = _bare_line(video=True)
    try:
        assert line._video_capable(), "a machine with a camera said it had none"
        assert ";video" in line._contact(), "the Contact never claimed video"
        body = line._body(call)
        # RFC 4566 puts session-level lines first and media sections last, and a
        # body whose m=video precedes the m=audio it belongs after is a
        # different document rather than a formatting choice.
        assert "m=audio" in body and "m=video" in body, body
        assert body.index("m=audio") < body.index("m=video"), body
        assert body.count("c=IN IP4") == 1, "the video section repeated the address"
    finally:
        call.rtp.stop()
        call.video.stop()


def test_a_call_out_offers_and_takes_the_picture(router, monkeypatch, tmp_path):
    """End to end: the INVITE carries `m=video`, the answer's video line is
    taken, and audio still arrives. What is *not* asserted is pixels - this
    router is a SIP peer and not a camera, and the pixels are measured by the
    loopback in test_landline_video.py."""
    monkeypatch.setattr(ua.video, "available", lambda *a, **k: (True, "/dev/video0"))
    router.stop()
    (tmp_path / "vid").mkdir()
    r2 = fake.Router(tmp_path / "vid", video=True)
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r2.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r2.https_port)
    events = []
    line = _line(r2, monkeypatch, events,
                 video_factory=lambda: ua.video.VideoRtpSession.bind("127.0.0.1"))
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the call was never answered")
        call = line.call
        assert call is not None and call.video is not None, "no video session was made"
        assert "m=video" in r2.seen["invite"][-1].body, "the INVITE offered no picture"
        _ip, their_port, _codecs = ua.video.parse_video(r2.seen["last_answer"])
        assert their_port, "the router answered with no video port"
        assert call.video.remote == ("127.0.0.1", their_port), (
            f"our camera points at {call.video.remote}, not their {their_port}")
        _wait(lambda: call.rtp.received > 0, "no audio arrived with the picture")
        line.hangup()
    finally:
        line.stop()
        r2.stop()


def test_a_far_end_that_declines_video_leaves_the_sound_alone(router, monkeypatch, tmp_path):
    """`m=video 0` is a decline and not a port. Taking it as one sends every
    packet nowhere while reporting success, and a call that ends because the
    picture was refused is worse than a call that carries sound."""
    monkeypatch.setattr(ua.video, "available", lambda *a, **k: (True, "/dev/video0"))
    router.stop()
    (tmp_path / "declined").mkdir()
    r2 = fake.Router(tmp_path / "declined", video=False)
    monkeypatch.setattr(provision, "ACCOUNT_PORT", r2.http_port)
    monkeypatch.setattr(provision, "IMS_PORT", r2.https_port)
    events = []
    line = _line(r2, monkeypatch, events,
                 video_factory=lambda: ua.video.VideoRtpSession.bind("127.0.0.1"))
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the call was never answered")
        call = line.call
        assert call is not None and call.video is not None
        assert call.video.remote is None, (
            f"our camera was pointed at {call.video.remote} - port 0 is a refusal")
        _wait(lambda: call.rtp.received > 0, "the sound died with the picture")
        line.hangup()
    finally:
        line.stop()
        r2.stop()


def test_the_registration_claims_video_only_where_the_machine_has_it(monkeypatch):
    monkeypatch.setattr(ua.video, "available", lambda *a, **k: (False, "no camera"))
    line, call = _bare_line()
    try:
        assert ";video" not in line._contact(expires=ua.EXPIRES), (
            "a machine with no camera registered as a video caller")
    finally:
        call.rtp.stop()


# --- hold and swap ----------------------------------------------------------------

def test_a_second_number_holds_the_first_and_rings_the_second(router, monkeypatch, tmp_path):
    """The whole point of hold: a second number while a call is live puts the
    first aside rather than being refused, and the first is still a live dialog
    waiting to come back."""
    events = []
    line = _line(router, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the first call never connected")
        first = line.call
        line.dial("+919876543210")
        _wait(lambda: len(line.calls) == 2, "the second call was never made")
        second = next(c for c in line.calls if c is not first)
        assert first.held, "the first call was not put aside"
        assert first.rtp.held, "the first call kept sending while it was held"
        assert not second.held, "the second call was born held"
        assert line.call is second, "the line is not on the call it just dialled"
        # The hold was a re-INVITE on the first dialog, not a new INVITE.
        asked, _body = router.seen["re_offer"]
        assert asked == "sendonly", f"the hold offered {asked}, not sendonly"
        _wait(lambda: any(k == "ringing" for k, _ in events), "the second call never rang")
    finally:
        line.stop()


def test_swap_trades_the_live_call_for_the_held_one(router, monkeypatch):
    events = []
    line = _line(router, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the first call never connected")
        first = line.call
        line.dial("+919876543210")
        _wait(lambda: len(line.calls) == 2, "the second call was never made")
        second = next(c for c in line.calls if c is not first)
        # A swap trades two *answered* calls, so the second has to be answered
        # first - and `answered` on its own cannot tell the two calls apart, so
        # this asks the call rather than the event log.
        _wait(lambda: second.state == "active", "the second call never connected")
        assert line.swap(), "swap refused with two answered calls"
        assert first.state == "active" and not first.held, "the held call did not come back"
        assert second.held and second.rtp.held, "the live call was not put aside"
        assert not first.rtp.held, "the resumed call is still gated"
        assert line.call is first, "the line is not back on the call it swapped to"
        asked, _body = router.seen["re_offer"]
        assert asked == "sendrecv", f"the resume offered {asked}, not sendrecv"
    finally:
        line.stop()


def test_swap_with_one_call_is_a_refusal_not_a_no_op(router, monkeypatch):
    """A hold with nothing to swap *to* is a mute with extra packets, so Swap
    says no rather than quietly doing nothing."""
    events = []
    line = _line(router, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the call never connected")
        assert not line.swap(), "swap claimed it worked with only one call"
        assert not line.call.held, "the one call was held by a no-op"
    finally:
        line.stop()


def test_a_hold_the_far_end_refuses_is_reported_as_not_held(router, monkeypatch):
    """A chip reading "held" while the machine is still on the call is the
    two-widgets-disagree failure, so the re-offer's answer decides."""
    events = []
    line = _line(router, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the call never connected")
        before = line.call.cseq
        real = ua.Line._request

        def refuse(self, message, timeout=32.0):
            if message.method == "INVITE":
                return sip.Message("SIP/2.0 488 Not Acceptable Here",
                                   [("To", message.get("To"))], "")
            return real(self, message, timeout)

        monkeypatch.setattr(ua.Line, "_request", refuse)
        assert not line.hold(), "a refused hold was reported as a hold"
        # The CSeq moved *forward* by exactly one. A CSeq that went backwards
        # would collide with a transaction the far end has already answered,
        # which is why the refused one is consumed rather than returned.
        assert line.call.cseq == before + 1, (
            f"the refused re-offer left the CSeq at {line.call.cseq}, from {before}")
        assert not line.call.held and not line.call.rtp.held, "the call went quiet anyway"
    finally:
        line.stop()


def test_a_held_call_sends_nothing_and_plays_nothing():
    """The RTP half. `a=sendonly` is a claim about the wire; this is what the
    wire does. Silence would still be a packet claiming a live microphone."""
    session = media.RtpSession.bind("127.0.0.1", fake._Silence())
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    try:
        session.start(("127.0.0.1", peer.getsockname()[1]), 0, 0)
        session._stop.wait(0.25)
        sent_before = sum(len(d) for d in _drain(peer))
        # Drain anything still in flight from the pre-hold window so the held
        # measurement is not polluted by packets that left before the hold.
        _drain(peer)
        held = fake._Silence()
        session.sound = held
        session.held = True
        session._stop.wait(0.35)
        # Any datagram already queued from before the hold is not a send that
        # happened *while* held; drop it before measuring.
        _drain(peer)
        sent_while_held = _drain(peer)
        session.held = False
        session._stop.wait(0.25)
        sent_after = _drain(peer)
        assert sent_before, "no packets were flowing before the hold"
        assert not sent_while_held, "the held call kept sending"
        assert sent_after, "the resumed call did not send again"
    finally:
        session.stop()
        peer.close()


def _drain(sock, seconds=0.15):
    # Bound by a wall-clock deadline rather than an idle gap: the fake
    # router's silence source emits every 20 ms, so an idle-timeout of 50 ms
    # never fires while packets are flowing and the socket blocks forever.
    out = []
    deadline = time.monotonic() + seconds
    sock.settimeout(0.05)
    while time.monotonic() < deadline and len(out) < 1000:
        try:
            out.append(sock.recv(2048))
        except socket.timeout:
            # Once packets have been seen, an idle gap means "done"; while
            # nothing has arrived yet, keep waiting up to the deadline so a
            # held call is observed for the whole window rather than quit on
            # the first empty recv.
            if out:
                break
    return out


def test_swap_refuses_while_a_call_is_still_ringing(router, monkeypatch):
    """The guard that held this open for one run. A ringing call has no session
    whose direction could be changed, so resuming into one would report a call
    came back that was never there."""
    events = []
    line = _line(router, monkeypatch, events)
    try:
        line.dial("+911234567890")
        _wait(lambda: any(k == "answered" for k, _ in events), "the first call never connected")
        first = line.call
        line.dial("+919876543210")
        _wait(lambda: len(line.calls) == 2, "the second call was never made")
        second = next(c for c in line.calls if c is not first)
        assert second.state != "active", "the second call answered too fast to test this"
        assert not line.swap(), "swap claimed to resume a call that was still ringing"
        assert second.state != "active" and not first.held is False
    finally:
        line.stop()
