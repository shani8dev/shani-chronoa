"""A call's sound: SDP offer/answer, RTP over UDP, and the codecs, in plain Python.

8 kHz, 20 ms packets - 160 samples, which Python encodes in well under a
millisecond.

**AMR-NB is what this line actually uses.** Measured against the router: an
offer of L16 and G.711 is refused with 488; only `AMR/8000/1` connects, and the
router's answer names `mode-change-capability=1;max-red=0` at ptime 20. So AMR
is offered first - one 32-byte mode-7 (MR122, 12.2 kbps) frame per packet - and
G.711 and L16 stay behind it as fallbacks for a line that will take them.

AMR is played through the system's **opencore-amrnb**, the reference
implementation's `interf_enc`/`interf_dec` reached with `ctypes`: no SIP
library, no audio library, and no ffmpeg, which carries AMR *decoders* only and
so could never encode. When the library is not installed, AMR is simply not
offered (see `offers()`): the line negotiates G.711 instead and still connects.

Sound goes through PipeWire's own `pw-cat`, which ships with PipeWire itself:
`--record` from the default microphone, `--playback` to the default speaker.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import random
import secrets
import socket
import struct
import subprocess
import threading
import time
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

RATE = 8000
FRAME_SAMPLES = 160          # 20 ms
FRAME_BYTES = FRAME_SAMPLES * 2
AMR_PT = 96                  # the router's own answer used 96 for AMR/8000/1
L16_PT = 97
DTMF_PT = 101
#: AMR's fmtp, measured from the router's own answer. `max-red=0` is the truth
#: (no redundancy is sent); `mode-change-capability` is left at its default,
#: because every packet carries mode 7 and a claim to change modes would be a
#: capability this computer does not have. The decoder on the far end copes
#: either way: each frame's first byte says how long it is.
AMR_FMTP = "max-red=0"


# --- G.711 and L16 tables -----------------------------------------------------

def _ulaw_decode(u: int) -> int:
    u = ~u & 0xFF
    sign, exponent, mantissa = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


def _alaw_decode(a: int) -> int:
    a ^= 0x55
    sign, exponent, mantissa = a & 0x80, (a >> 4) & 0x07, a & 0x0F
    sample = (mantissa << 4) + 8
    if exponent:
        sample = (sample + 0x100) << (exponent - 1)
    return sample if sign else -sample


def _ulaw_encode(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), 32635) + 0x84
    exponent = max(0, sample.bit_length() - 8)
    exponent = min(exponent, 7)
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _alaw_encode(sample: int) -> int:
    sign = 0x80 if sample >= 0 else 0
    sample = min(abs(sample), 32767) >> 3
    if sample < 32:
        exponent, mantissa = 0, sample >> 1
    else:
        exponent = min(sample.bit_length() - 5, 7)
        mantissa = (sample >> exponent) & 0x0F
    return (sign | (exponent << 4) | mantissa) ^ 0x55


_ULAW_DEC = [_ulaw_decode(i) for i in range(256)]
_ALAW_DEC = [_alaw_decode(i) for i in range(256)]
#: Indexed by the sample as an unsigned 16-bit number.
_ULAW_ENC = bytes(_ulaw_encode(s - 65536 if s >= 32768 else s) for s in range(65536))
_ALAW_ENC = bytes(_alaw_encode(s - 65536 if s >= 32768 else s) for s in range(65536))


# --- codecs ---------------------------------------------------------------------

class Codec:
    """One 20 ms packet of PCM in, one RTP payload out, and back the other way."""

    #: The payload type and rtpmap name this codec puts in SDP.
    pt = 0
    rtpmap = ""
    #: True when playing it needs something the system may not have.
    needs_library = False

    @classmethod
    def available(cls) -> bool:
        """Whether this computer can build one: a codec may need a library."""
        return True

    def encode(self, pcm: bytes) -> bytes:
        raise NotImplementedError

    def decode(self, payload: bytes) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        """Release whatever the codec holds; the stateless ones hold nothing."""


class Ulaw(Codec):
    """G.711 µ-law (RFC 3551, payload type 0): one byte per sample, no table to load."""

    pt = 0
    rtpmap = "PCMU/8000"

    def encode(self, pcm: bytes) -> bytes:
        return bytes(_ULAW_ENC[s] for s in struct.unpack(f"<{len(pcm) // 2}H", pcm))

    def decode(self, payload: bytes) -> bytes:
        return struct.pack(f"<{len(payload)}h", *(_ULAW_DEC[b] for b in payload))


class Alaw(Codec):
    """G.711 A-law (RFC 3551, payload type 8)."""

    pt = 8
    rtpmap = "PCMA/8000"

    def encode(self, pcm: bytes) -> bytes:
        return bytes(_ALAW_ENC[s] for s in struct.unpack(f"<{len(pcm) // 2}H", pcm))

    def decode(self, payload: bytes) -> bytes:
        return struct.pack(f"<{len(payload)}h", *(_ALAW_DEC[b] for b in payload))


class L16Codec(Codec):
    """L16 (RFC 3551): the samples themselves, big-endian - no compression at all."""

    pt = L16_PT
    rtpmap = "L16/8000"

    def encode(self, pcm: bytes) -> bytes:
        n = len(pcm) // 2
        return struct.pack(f">{n}H", *struct.unpack(f"<{n}H", pcm))

    def decode(self, payload: bytes) -> bytes:
        n = len(payload) // 2
        return struct.pack(f"<{n}H", *struct.unpack(f">{n}H", payload[:n * 2]))


# --- AMR-NB, over the reference implementation's own interface ------------------

#: Speech-data bits per frame type index (RFC 4867 Table 1a / 3GPP TS 26.101):
#: modes 0-7 are 4.75 to 12.2 kbps, and index 8 is the SID comfort frame. The
#: octet-aligned payload pads each frame to a byte boundary; the
#: bandwidth-efficient one does not, so the exact bit count is what matters.
AMR_BITS = (95, 103, 118, 134, 148, 159, 204, 244, 39)
#: Octet-aligned total payload length per frame type, TOC byte included.
AMR_OCTETS = (13, 14, 16, 18, 20, 21, 27, 32, 6)

_LIBRARY: Optional[ctypes.CDLL] = None
_LIBRARY_TRIED = False


def _open_library() -> Optional[ctypes.CDLL]:
    """Load opencore-amrnb and declare how its six entry points are called.

    The library ships with nearly every Linux (Debian `libopencore-amrnb0`,
    Arch `opencore-amrnb`); it is the reference implementation's
    `interf_enc`/`interf_dec`, so a frame it writes is a frame every AMR
    decoder reads.
    """
    try:
        name = ctypes.util.find_library("opencore-amrnb") or "libopencore-amrnb.so.0"
        lib = ctypes.CDLL(name)
    except (OSError, AttributeError):
        return None
    short_p = ctypes.POINTER(ctypes.c_short)
    byte_p = ctypes.POINTER(ctypes.c_ubyte)
    try:
        lib.Encoder_Interface_init.restype = ctypes.c_void_p
        lib.Encoder_Interface_init.argtypes = [ctypes.c_int]
        lib.Encoder_Interface_Encode.restype = ctypes.c_int
        lib.Encoder_Interface_Encode.argtypes = [ctypes.c_void_p, ctypes.c_int, short_p, byte_p,
                                                 ctypes.c_int]
        lib.Encoder_Interface_exit.argtypes = [ctypes.c_void_p]
        lib.Decoder_Interface_init.restype = ctypes.c_void_p
        lib.Decoder_Interface_Decode.argtypes = [ctypes.c_void_p, byte_p, short_p, ctypes.c_int]
        lib.Decoder_Interface_exit.argtypes = [ctypes.c_void_p]
    except AttributeError:
        return None
    return lib


def _library() -> Optional[ctypes.CDLL]:
    """The opencore-amrnb library, loaded once, or None when it is not installed."""
    global _LIBRARY, _LIBRARY_TRIED
    if not _LIBRARY_TRIED:
        _LIBRARY_TRIED = True
        _LIBRARY = _open_library()
    return _LIBRARY


class AmrMissing(RuntimeError):
    """This computer has no AMR-NB to play: the library is not here."""


def _bits_of(data: bytes) -> List[int]:
    """A byte string as a list of bits, most significant first."""
    value = int.from_bytes(data, "big")
    return [(value >> (len(data) * 8 - 1 - i)) & 1 for i in range(len(data) * 8)]


def _bytes_of(bits: List[int]) -> bytes:
    """A bit list back to bytes, most significant first, zero-padded at the end.

    Left-aligned: the first bit of the list is the most significant bit of the
    first byte. Right-aligning (which is what int.to_bytes does on its own)
    would shift a run of bits that is not a whole number of bytes four bits in,
    and the frame would come back as noise.
    """
    padded = list(bits)
    while len(padded) % 8:
        padded.append(0)
    value = 0
    for bit in padded:
        value = (value << 1) | bit
    return value.to_bytes(len(padded) // 8, "big")


def _pack_efficient(frame: bytes) -> bytes:
    """One octet-aligned frame -> a bandwidth-efficient payload (RFC 4867 4.3).

    The payload is a 4-bit codec mode request (15: no request, which is what an
    IP endpoint should send), a 6-bit ToC entry (F, FT, Q), and then the frame's
    speech bits with nothing between them and no padding inside the frame.
    """
    if not frame:
        return b""
    ft = (frame[0] >> 3) & 0x0F
    q = (frame[0] >> 2) & 0x01
    if ft >= len(AMR_BITS):
        return frame
    bits = [1, 1, 1, 1]                       # CMR 15
    bits.append(0)                            # F: the last frame in this payload
    bits += [(ft >> 3) & 1, (ft >> 2) & 1, (ft >> 1) & 1, ft & 1]
    bits.append(q)
    bits += _bits_of(frame[1:])[:AMR_BITS[ft]]
    while len(bits) % 8:
        bits.append(0)                        # only the whole payload is padded
    return _bytes_of(bits)


def _unpack_efficient(payload: bytes) -> bytes:
    """A bandwidth-efficient payload -> octet-aligned frames (RFC 4867 4.3).

    Reads the 4-bit codec mode request (ignored: an IP endpoint should not set
    it), then each 6-bit ToC entry and the frame it names, and rewrites the
    frames the way the library's decoder reads them - a TOC byte whose three
    top bits carry the type, then the frame's bits byte-aligned.
    """
    bits = _bits_of(payload)
    if len(bits) < 10:
        return b""
    at = 4                                    # past the codec mode request
    out = bytearray()
    while at + 6 <= len(bits):
        follow = bits[at]
        ft = 0
        for _ in range(4):
            ft = (ft << 1) | bits[at + 1 + _]
        q = bits[at + 5]
        at += 6
        if ft >= len(AMR_BITS):
            break                             # NO_DATA or SPEECH_LOST: no frame
        out.append((ft << 3) | (q << 2))
        out.extend(_bytes_of(bits[at:at + AMR_BITS[ft]]))
        at += AMR_BITS[ft]
        if not follow:
            break
    return bytes(out)


class Amr(Codec):
    """AMR-NB (RFC 4867) through opencore-amrnb's own interface.

    Mode 7 - MR122, 12.2 kbps - is the highest bitrate AMR-NB has and the one
    the router's own clients use, so it is the least lossy of the eight and
    carries a voice best on this line. One packet is 20 ms: 160 samples in,
    32 payload bytes out.

    **The library writes the octet-aligned packing, and the far end may well
    ask for the other one.** Measured on the router: the callee's phone answers
    the INVITE with `AMR/8000/1` (octet-aligned) and then re-offers the session
    with `AMR/8000` (bandwidth-efficient), where the ToC and the frame bits are
    packed together with no padding between them. Its frames arrive as 0xf3
    where ours are 0x3c - the same mode 7, the other packing. So `packing`
    converts at the edges: the library still sees octet-aligned, because that
    is the only thing its interface takes.

    Inside a frame, the library's own first byte is the table of contents and
    is kept exactly as written: a decoder finds a frame's length by indexing
    its size table with those bits, so rewriting one desynchronises the far
    end and it hears noise.
    """

    pt = AMR_PT
    rtpmap = "AMR/8000/1"
    needs_library = True
    #: Mode 7 is MR122 (see `Mode` in the reference implementation); 0 is MR475.
    MODE = 7
    #: One mode-7 frame is the TOC byte plus 31 packed ones (ffmpeg's own table
    #: `frame_sizes_nb` says 31 for type 7, which is what the 8-mode probe
    #: confirms: 13/14/16/18/20/21/27/32 bytes for modes 0..7).
    MAX_PAYLOAD = 32
    _SLOT = MAX_PAYLOAD + 8          # the encoder may write a little past the frame

    @classmethod
    def available(cls) -> bool:
        return _library() is not None

    def __init__(self, packing: str = "oa") -> None:
        lib = _library()
        if lib is None:
            raise AmrMissing("this system has no opencore-amrnb library, so it cannot play AMR")
        # 0 dtx: no voice-activity detection, because a silent room must still
        # fill the packet. A silent frame is a frame; silence must not become a
        # shorter one.
        self._lib = lib
        self.packing = "be" if packing == "be" else "oa"
        self._enc = lib.Encoder_Interface_init(0)
        self._dec = lib.Decoder_Interface_init()
        if not self._enc or not self._dec:
            self.close()
            raise AmrMissing("opencore-amrnb would not start on this system")
        self._speech = (ctypes.c_short * FRAME_SAMPLES)()
        self._frame = (ctypes.c_ubyte * self._SLOT)()
        self._pcm = (ctypes.c_short * FRAME_SAMPLES)()

    def encode(self, pcm: bytes) -> bytes:
        if len(pcm) < FRAME_BYTES:              # a short packet still fills 20 ms
            pcm = pcm + b"\x00" * (FRAME_BYTES - len(pcm))
        self._speech[:] = struct.unpack_from(f"<{FRAME_SAMPLES}h", pcm)
        size = self._lib.Encoder_Interface_Encode(self._enc, self.MODE, self._speech,
                                                  self._frame, 0)
        if size <= 0:
            return b""
        frame = bytes(self._frame[:size])
        return _pack_efficient(frame) if self.packing == "be" else frame

    def _one(self, payload: bytes) -> bytes:
        self._lib.Decoder_Interface_Decode(self._dec, payload, self._pcm, 0)
        return struct.pack(f"<{FRAME_SAMPLES}h", *self._pcm)

    def decode(self, payload: bytes) -> bytes:
        """A payload of one or more frames -> 20 ms of PCM per frame.

        The library reads past a TOC byte for the frame's own bits, so the rest
        of the slot is zeroed: a short frame must not make it read whatever
        follows it in memory.
        """
        if not payload:
            return b"\x00" * FRAME_BYTES
        frames = payload
        if self.packing == "be" or payload[0] & 0x80:
            # A far end that re-offered bandwidth-efficient and then sent
            # octet-aligned after all is caught by the other reading.
            unpacked = _unpack_efficient(payload)
            if unpacked:
                frames = unpacked
        out = b""
        at = 0
        while at < len(frames):
            ft = (frames[at] >> 3) & 0x0F
            size = AMR_OCTETS[ft] if ft < len(AMR_OCTETS) else self._SLOT
            chunk = frames[at:at + size]
            at += size
            slot = (ctypes.c_ubyte * self._SLOT)()
            for i, byte in enumerate(chunk):
                slot[i] = byte
            for i in range(len(chunk), self._SLOT):
                slot[i] = 0
            out += self._one(slot)
        return out

    def close(self) -> None:
        """Free the codec's encoder and decoder state; safe to call twice, and
        safe on the path where construction failed half-way."""
        lib = getattr(self, "_lib", None)
        enc, dec = getattr(self, "_enc", None), getattr(self, "_dec", None)
        self._enc = self._dec = None
        if lib is None:
            return
        if enc:
            lib.Encoder_Interface_exit(enc)
        if dec:
            lib.Decoder_Interface_exit(dec)


#: The codecs, best first. AMR leads because it is the one this line takes and
#: costs 12 kbps rather than 64 or 256.
_CODECS: List[type] = [Amr, Ulaw, Alaw, L16Codec]


def available() -> List[type]:
    """The codecs this computer can actually play, best first."""
    return [cls for cls in _CODECS if cls.available()]


def offers() -> List[Tuple[int, str]]:
    """What we can play, as (payload type, rtpmap name), best first.

    A codec whose library is missing is left out rather than offered: the far
    end would answer with it and the call would die with no sound, where
    offering only what works connects on G.711 instead.
    """
    return [(cls.pt, cls.rtpmap) for cls in available()]


def can_play(pt: int) -> bool:
    """Whether `pt` is one of the payload types this computer can build."""
    return any(cls.pt == pt for cls in available())


def codec_for(pt: int, packing: str = "oa") -> Optional[Codec]:
    """A fresh codec for one of `offers()`'s payload types, or None when it is not ours.

    Fresh, and shared by nothing: the AMR encoder and decoder carry the previous
    packet in their state, so one per call keeps the two directions of a call -
    and two calls, when the router is a test's - from each other's hands.
    """
    for cls in available():
        if cls.pt == pt:
            try:
                return cls(packing) if pt == AMR_PT else cls()
            except Exception as exc:  # noqa: BLE001 - a codec that will not start is not playable
                logger.warning("landline: payload type %s (%s) would not start: %s",
                               pt, cls.rtpmap, exc)
                return None
    return None


# --- SDP ---------------------------------------------------------------------

def offer(ip: str, port: int, only: Optional[int] = None,
          direction: str = "sendrecv") -> str:
    """Our SDP: every codec we can play (an offer), or just `only` (an answer).

    `direction` is what a hold offer changes and nothing else: a held call is
    `sendonly`, and re-offering the same codec list with the same ports and a
    different direction is what makes it a hold rather than a new session.
    """
    chosen = [c for c in offers() if only is None or c[0] == only]
    pts = " ".join(str(pt) for pt, _ in chosen) + f" {DTMF_PT}"
    session = random.randint(10 ** 9, 10 ** 10)
    lines = ["v=0", f"o=- {session} {session} IN IP4 {ip}", "s=-", f"c=IN IP4 {ip}", "t=0 0",
             f"m=audio {port} RTP/AVP {pts}"]
    lines += [f"a=rtpmap:{pt} {name}" for pt, name in chosen]
    if any(pt == AMR_PT for pt, _name in chosen):
        lines.append(f"a=fmtp:{AMR_PT} {AMR_FMTP}")
    lines += [f"a=rtpmap:{DTMF_PT} telephone-event/8000", f"a=fmtp:{DTMF_PT} 0-15",
              "a=ptime:20", f"a={direction}"]
    return "\r\n".join(lines) + "\r\n"


def sdp_direction(text: str) -> str:
    """The direction an SDP asks for (`sendrecv`, `sendonly`, `recvonly`,
    `inactive`), or the RFC 3264 default of `sendrecv` when it says nothing.

    Read from anywhere in the body rather than from inside the audio section,
    because a hold offer repeats the line at session level on some far ends -
    and a direction read from the wrong scope is the wrong answer.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if line in ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"):
            return line[2:]
    return "sendrecv"


def parse_sdp(text: str) -> Tuple[str, int, List[Tuple[int, str]], int]:
    """(address, port, [(pt, 'NAME/rate')...] in their order, their DTMF pt or 0)."""
    ip, port, order, names, dtmf = "", 0, [], {}, 0
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("c=IN IP4 "):
            ip = line.split()[-1]
        elif line.startswith("m=audio "):
            parts = line.split()
            port = int(parts[1])
            order = [int(p) for p in parts[3:] if p.isdigit()]
        elif line.startswith("a=rtpmap:"):
            pt, _, name = line[9:].partition(" ")
            names[int(pt)] = name.strip()
    static = {0: "PCMU/8000", 8: "PCMA/8000"}
    codecs = [(pt, names.get(pt) or static.get(pt, "")) for pt in order]
    for pt, name in codecs:
        if name.lower().startswith("telephone-event"):
            dtmf = pt
    return ip, port, [(pt, n) for pt, n in codecs if not n.lower().startswith("telephone-event")], dtmf


def pick(codecs: List[Tuple[int, str]]) -> Optional[Tuple[int, int]]:
    """(their pt, our pt) for the first of theirs we can play, or None.

    Both AMR spellings match: `AMR/8000/1` (octet-aligned) and `AMR/8000`
    (bandwidth-efficient). Measured on the router, the same callee answers the
    INVITE with the first and re-offers the session with the second, so the
    packing a session negotiates can change under us - and the library only
    writes octet-aligned, so the other one is converted at the edges
    (`packing_for`).
    """
    ours = {}
    for pt, name in offers():
        ours[name.upper()] = pt
    for pt, name in codecs:
        mine = ours.get(name.upper())
        if mine is None and name.strip().upper() in ("AMR/8000", "AMR/8000/1"):
            mine = ours.get("AMR/8000/1")
        if mine is not None:
            return pt, mine
    return None


def packing_for(name: str) -> str:
    """Which AMR packing an rtpmap name negotiates: 'be' for bare AMR/8000."""
    return "be" if name.strip().upper() == "AMR/8000" else "oa"


def negotiated(codecs: List[Tuple[int, str]], pt: int) -> str:
    """The rtpmap name the far end used for payload type `pt`, or ''."""
    for their_pt, name in codecs:
        if their_pt == pt:
            return name
    return ""


def local_ip_towards(host: str) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((host, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


# --- sound ---------------------------------------------------------------------

# --- conferencing -----------------------------------------------------------------

def mix_sum(frames: Sequence[bytes]) -> bytes:
    """The saturating sum of whole 20 ms frames - the mixer's arithmetic, alone.

    Pulled out so a test can check the *rule* rather than the thread: what six
    people overlapping sounds like is arithmetic, and arithmetic that is only
    reachable through a mixer is arithmetic nobody checks.
    """
    out = [0] * (FRAME_BYTES // 2)
    for frame in frames:
        clipped = frame[:FRAME_BYTES]
        for index, value in enumerate(struct.unpack(f"<{len(clipped)//2}h", clipped)):
            out[index] += value
    return struct.pack(f"<{len(out)}h",
                       *[max(-32768, min(32767, v)) for v in out])


class MixInput:
    """One participant's audio: what they say goes in, what the room says comes out.

    Attached as a call's `sound` so the far end's decoded frames land in the
    mixer rather than on the speaker - a conference hears everybody, and a
    participant who is not in the room is a participant who hears nothing.
    """

    def __init__(self, speaker: object, mixer: "Mixer") -> None:
        self.speaker, self.mixer = speaker, mixer
        mixer.join(speaker)

    def write(self, pcm: bytes) -> None:
        self.mixer.add(self.speaker, pcm)

    def read(self) -> bytes:
        return self.mixer.read(self.speaker)

    def close(self) -> None:
        self.mixer.drop(self.speaker)


class Mixer:
    """Sums the calls in a conference into the one stream the conference sends.

    **Each participant has their own queue, and a frame is delivered to every
    queue but its author's.** A single shared buffer is the obvious shape and it
    is wrong in a way a test caught: the first participant to call `read()` takes
    the whole room, so with two people only one of them hears anything and the
    other hears silence for the whole call. Per-queue delivery costs a copy per
    participant per frame, which at five parties and twenty frames a second is
    nothing, and it is the only shape where "everyone hears everyone" is true by
    construction rather than by timing.

    Excluding the author's own frames is the other half: the mix goes *out* over
    that participant's stream, so a room that fed its own output back in would
    amplify itself until it howled.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: "dict[object, list]" = {}

    def add(self, speaker: object, pcm: bytes) -> None:
        """One participant's frame, into every queue but theirs."""
        if not pcm:
            return
        with self._lock:
            # The author is in the room from the moment they speak, even if
            # nothing has called `join` for them yet - a frame delivered to a
            # room with no queues in it is a frame nobody ever hears.
            self._queues.setdefault(speaker, [])
            for who, queue in self._queues.items():
                if who is not speaker:
                    queue.append(pcm)

    def join(self, speaker: object) -> None:
        """Add a participant to the room, so they hear what is said from now on."""
        with self._lock:
            self._queues.setdefault(speaker, [])

    def read(self, speaker: object) -> bytes:
        """One 20 ms frame of the room minus `speaker`, ready for the codec.

        Whatever has arrived since the last read is summed; when nothing has, the
        frame is silence, because a conference with one person talking is a
        conference with a gap where the others are.
        """
        with self._lock:
            queue = self._queues.setdefault(speaker, [])
            # Copied first, then cleared in place: `frames, queue[:] = queue, []`
            # binds both names to the *same* list and then empties it, so the
            # frames are gone before `mix_sum` sees them and every participant
            # hears silence for the whole call. Measured.
            frames, queue[:] = queue[:], []
        return mix_sum(frames)

    def drop(self, speaker: object) -> None:
        with self._lock:
            self._queues.pop(speaker, None)

    def close(self) -> None:
        pass


MAX_CALLS = 5     #: 3GPP MMTel conferencing is five, ours included.


class PwCat:
    """The microphone and the speaker, through `pw-cat` (PipeWire's own tool)."""

    def __init__(self) -> None:
        fmt = ["--format", "s16", "--rate", str(RATE), "--channels", "1"]
        self.rec = subprocess.Popen(["pw-cat", "--record", "--media-role", "Phone", *fmt, "-"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.play = subprocess.Popen(["pw-cat", "--playback", "--media-role", "Phone", *fmt, "-"],
                                     stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self) -> bytes:
        return self.rec.stdout.read(FRAME_BYTES) or b""

    def write(self, pcm: bytes) -> None:
        try:
            self.play.stdin.write(pcm)
            self.play.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def close(self) -> None:
        for proc in (self.rec, self.play):
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                proc.kill()


class RtpSession:
    """Send and receive one call's audio. `sound` has read()/write()/close().

    Packets from any address but the one the SDP named are dropped, so nothing
    else on the network can talk into a call.
    """

    def __init__(self, sock: socket.socket, sound) -> None:
        self.sock = sock
        self.sound = sound
        self.remote: Optional[Tuple[str, int]] = None
        self.pt = 0          # what we send (their payload type number)
        self.codec = 0       # which encoder that is
        self.coder: Optional[Codec] = None
        self.dtmf_pt = 0
        self.ssrc = secrets.randbits(32)
        self.seq = random.randint(0, 0xFFFF)
        self.ts = random.randint(0, 0xFFFFFFFF)
        self.received = 0
        #: Whether this stream is on hold. Gated inside the two loops rather
        #: than by stopping them, because `stop()` ends the call and a resume
        #: has to be a direction change and not a new session.
        self.held = False
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()

    @classmethod
    def bind(cls, ip: str, sound=None) -> "RtpSession":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((ip, 0))
        return cls(sock, sound)

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def start(self, remote: Tuple[str, int], pt: int, codec: int, dtmf_pt: int = 0,
              packing: str = "oa") -> None:
        """Send and receive `codec` (one of `offers()`'s payload types) to `remote`.

        Called once per call, and again when the far end re-offers the session
        mid-call (the callee's phone sends an UPDATE with SDP while it rings,
        RFC 3311) or answers the INVITE after such a re-offer: the second call
        re-points the same two threads instead of starting another pair, which
        would double the packets and hold two codecs at once.
        """
        coder = codec_for(codec, packing)
        if coder is None:
            raise ValueError(f"payload type {codec} is not a sound this computer can play")
        self.remote, self.pt, self.codec, self.dtmf_pt = remote, pt, codec, dtmf_pt
        if self.coder is not None:
            self.coder.close()
        self.coder = coder
        if self.sound is None:
            self.sound = PwCat()
        if self._threads:
            return
        for target in (self._send_loop, self._recv_loop):
            t = threading.Thread(target=target, daemon=True, name="landline-rtp")
            t.start()
            self._threads.append(t)

    def _packet(self, pt: int, payload: bytes, marker: bool = False, ts: Optional[int] = None) -> bytes:
        with self._lock:
            self.seq = (self.seq + 1) & 0xFFFF
            header = struct.pack("!BBHII", 0x80, (0x80 if marker else 0) | pt, self.seq,
                                 self.ts if ts is None else ts, self.ssrc)
        return header + payload

    def _send_loop(self) -> None:
        first = True
        next_at = time.monotonic()
        while not self._stop.is_set():
            if self.held:
                # A held call sends nothing at all - not silence. Comfort noise
                # is a packet claiming a live microphone, and the far end has
                # already been told by the SDP that there is not one.
                self._stop.wait(0.05)
                continue
            pcm = self.sound.read()
            if not pcm:
                # A source that has nothing (a closed mic) still keeps time.
                pcm = b"\0" * FRAME_BYTES
                next_at += 0.02
                time.sleep(max(0.0, next_at - time.monotonic()))
            try:
                self.sock.sendto(self._packet(self.pt, self.coder.encode(pcm), marker=first),
                                self.remote)
            except OSError:
                break
            first = False
            with self._lock:
                self.ts = (self.ts + FRAME_SAMPLES) & 0xFFFFFFFF

    def _recv_loop(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if self.remote and addr[0] != self.remote[0]:
                continue
            if len(data) < 12 or data[0] >> 6 != 2:
                continue
            cc = data[0] & 0x0F
            pt = data[1] & 0x7F
            payload = data[12 + 4 * cc:]
            if data[0] & 0x20 and payload:  # padding
                payload = payload[:-payload[-1]]
            if pt != self.pt or self.coder is None:
                continue
            self.received += 1
            # A held call's audio is not played. The receive thread keeps
            # running - and keeps counting, because what the far end sent is a
            # fact whether or not we listened - so a resume is a direction
            # change and not a new session. Without this, a far end that has not
            # honoured the hold yet is heard over the call that replaced it.
            if self.held and self.sound is not None:
                continue
            try:
                self.sound.write(self.coder.decode(payload))
            except Exception:  # noqa: BLE001 - one bad frame must not end the call
                logger.debug("landline: a frame would not decode", exc_info=True)

    def dtmf(self, digit: str) -> bool:
        """One keypad digit as RFC 4733 events (3 starts, 3 ends), when they take them."""
        events = {**{str(i): i for i in range(10)}, "*": 10, "#": 11}
        if not self.dtmf_pt or digit not in events or self.remote is None:
            return False
        with self._lock:
            ts = self.ts
        for i, duration in enumerate((160, 320, 480, 800, 800, 800)):
            end = 0x80 if i >= 3 else 0
            payload = struct.pack("!BBH", events[digit], end | 10, duration)
            self.sock.sendto(self._packet(self.dtmf_pt, payload, marker=i == 0, ts=ts), self.remote)
            time.sleep(0.02)
        return True

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1)
        if self.coder is not None:
            self.coder.close()
            self.coder = None
        if self.sound is not None:
            self.sound.close()
        try:
            self.sock.close()
        except OSError:
            pass
