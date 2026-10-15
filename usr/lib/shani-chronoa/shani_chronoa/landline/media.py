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
from typing import List, Optional, Tuple

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


class Amr(Codec):
    """AMR-NB (RFC 4867) through opencore-amrnb's own interface.

    Mode 7 - MR122, 12.2 kbps - is the highest bitrate AMR-NB has and the one
    the router's own clients use, so it is the least lossy of the eight and
    carries a voice best on this line. One packet is 20 ms: 160 samples in,
    32 payload bytes out.

    **The encoder's own bytes are the payload, sent verbatim.** Its first byte
    is the RFC 4867 table of contents (frame type in bits 7-3, the wrapper's
    quality bit in bit 2), and a decoder finds a frame's length by indexing its
    size table with those same bits - so rewriting the byte into a "cleaner"
    TOC desynchronises the far end and it hears noise. The decoder here is the
    library's mirror: it reads the same byte back off each frame.
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

    def __init__(self) -> None:
        lib = _library()
        if lib is None:
            raise AmrMissing("this system has no opencore-amrnb library, so it cannot play AMR")
        # 0 dtx: no voice-activity detection, because a silent room must still
        # fill the packet. A silent frame is a frame; silence must not become a
        # shorter one.
        self._lib = lib
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
        size = self._lib.Encoder_Interface_Encode(self._enc, self.MODE, self._speech, self._frame, 0)
        return bytes(self._frame[:size]) if size > 0 else b""

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            return b"\x00" * FRAME_BYTES
        # The library reads past the TOC byte for the frame's own bits, so the
        # rest of the slot is zeroed: a short frame must not make it read the
        # next packet's memory.
        for i, byte in enumerate(payload[:self._SLOT]):
            self._frame[i] = byte
        for i in range(len(payload), self._SLOT):
            self._frame[i] = 0
        self._lib.Decoder_Interface_Decode(self._dec, self._frame, self._pcm, 0)
        return struct.pack(f"<{FRAME_SAMPLES}h", *self._pcm)

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


def codec_for(pt: int) -> Optional[Codec]:
    """A fresh codec for one of `offers()`'s payload types, or None when it is not ours.

    Fresh, and shared by nothing: the AMR encoder and decoder carry the previous
    packet in their state, so one per call keeps the two directions of a call -
    and two calls, when the router is a test's - from each other's hands.
    """
    for cls in available():
        if cls.pt == pt:
            try:
                return cls()
            except Exception as exc:  # noqa: BLE001 - a codec that will not start is not playable
                logger.warning("landline: payload type %s (%s) would not start: %s",
                               pt, cls.rtpmap, exc)
                return None
    return None


# --- SDP ---------------------------------------------------------------------

def offer(ip: str, port: int, only: Optional[int] = None) -> str:
    """Our SDP: every codec we can play (an offer), or just `only` (an answer)."""
    chosen = [c for c in offers() if only is None or c[0] == only]
    pts = " ".join(str(pt) for pt, _ in chosen) + f" {DTMF_PT}"
    session = random.randint(10 ** 9, 10 ** 10)
    lines = ["v=0", f"o=- {session} {session} IN IP4 {ip}", "s=-", f"c=IN IP4 {ip}", "t=0 0",
             f"m=audio {port} RTP/AVP {pts}"]
    lines += [f"a=rtpmap:{pt} {name}" for pt, name in chosen]
    if any(pt == AMR_PT for pt, _name in chosen):
        lines.append(f"a=fmtp:{AMR_PT} {AMR_FMTP}")
    lines += [f"a=rtpmap:{DTMF_PT} telephone-event/8000", f"a=fmtp:{DTMF_PT} 0-15",
              "a=ptime:20", "a=sendrecv"]
    return "\r\n".join(lines) + "\r\n"


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

    AMR matches only as `AMR/8000/1` (octet-aligned). The encoder writes
    octet-aligned frames, so a far end answering bare `AMR/8000` is left
    unmatched on purpose rather than sent frames it reads as the wrong lengths.
    """
    ours = {name.upper(): pt for pt, name in offers()}
    for pt, name in codecs:
        mine = ours.get(name.upper())
        if mine is not None:
            return pt, mine
    return None


def local_ip_towards(host: str) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((host, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


# --- sound ---------------------------------------------------------------------

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

    def start(self, remote: Tuple[str, int], pt: int, codec: int, dtmf_pt: int = 0) -> None:
        coder = codec_for(codec)
        if coder is None:
            raise ValueError(f"payload type {codec} is not a sound this computer can play")
        self.remote, self.pt, self.codec, self.dtmf_pt = remote, pt, codec, dtmf_pt
        self.coder = coder
        if self.sound is None:
            self.sound = PwCat()
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
