"""SIP messages: parse, build, and answer a digest challenge (RFC 3261, 2617, 7616).

Only what a phone on one line needs - requests and responses over a stream
(TLS), headers kept in order with their repeats, and MD5 digest with `qop=auth`.
No SIP library: the line is the router's, and the router speaks a small,
well-defined part of SIP.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import Dict, List, Optional, Tuple

#: Compact header forms (RFC 3261 s7.3.3), expanded on parse.
_COMPACT = {"i": "call-id", "m": "contact", "e": "content-encoding", "l": "content-length",
            "c": "content-type", "f": "from", "s": "subject", "k": "supported",
            "t": "to", "v": "via"}


class Message:
    """One SIP request or response. Header names are kept as written; lookups ignore case."""

    def __init__(self, first: str, headers: Optional[List[Tuple[str, str]]] = None,
                 body: str = "") -> None:
        self.first = first
        self.headers: List[Tuple[str, str]] = list(headers or [])
        self.body = body

    # --- the first line -------------------------------------------------
    @property
    def is_request(self) -> bool:
        return not self.first.startswith("SIP/2.0 ")

    @property
    def method(self) -> str:
        if self.is_request:
            return self.first.split(" ", 1)[0]
        return self.cseq[1]

    @property
    def uri(self) -> str:
        return self.first.split(" ")[1] if self.is_request else ""

    @property
    def status(self) -> int:
        return 0 if self.is_request else int(self.first.split(" ")[1])

    @property
    def reason(self) -> str:
        return "" if self.is_request else self.first.split(" ", 2)[2] if self.first.count(" ") >= 2 else ""

    # --- headers ----------------------------------------------------------
    def get(self, name: str, default: str = "") -> str:
        name = name.lower()
        for k, v in self.headers:
            if k.lower() == name:
                return v
        return default

    def all(self, name: str) -> List[str]:
        name = name.lower()
        return [v for k, v in self.headers if k.lower() == name]

    def set(self, name: str, value: str) -> None:
        low = name.lower()
        for i, (k, _v) in enumerate(self.headers):
            if k.lower() == low:
                self.headers[i] = (name, value)
                return
        self.headers.append((name, value))

    def remove(self, name: str) -> None:
        low = name.lower()
        self.headers = [(k, v) for k, v in self.headers if k.lower() != low]

    @property
    def call_id(self) -> str:
        return self.get("Call-ID")

    @property
    def cseq(self) -> Tuple[int, str]:
        num, _, method = self.get("CSeq").partition(" ")
        return int(num or 0), method.strip()

    def encode(self) -> bytes:
        body = self.body.encode()
        lines = [self.first]
        lines += [f"{k}: {v}" for k, v in self.headers if k.lower() != "content-length"]
        lines.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode() + body

    def __repr__(self) -> str:
        return f"<sip {self.first!r}>"


def parse(raw: bytes) -> Message:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("utf-8", "replace").split("\r\n")
    headers: List[Tuple[str, str]] = []
    for line in lines[1:]:
        if line[:1] in (" ", "\t") and headers:  # a folded continuation
            k, v = headers[-1]
            headers[-1] = (k, v + " " + line.strip())
            continue
        name, _, value = line.partition(":")
        name = name.strip()
        headers.append((_COMPACT.get(name.lower(), name) if len(name) == 1 else name, value.strip()))
    return Message(lines[0], headers, body.decode("utf-8", "replace"))


def split_stream(buffer: bytes) -> Tuple[List[Message], bytes]:
    """The whole messages at the start of `buffer`, and what is left of it.

    Over a stream a message ends where Content-Length says. A bare CRLF pair
    is the RFC 5626 keep-alive (a pong is one CRLF) and is dropped.
    """
    out: List[Message] = []
    while True:
        while buffer.startswith(b"\r\n"):
            buffer = buffer[2:]
        end = buffer.find(b"\r\n\r\n")
        if end < 0:
            return out, buffer
        head = buffer[:end].decode("utf-8", "replace")
        match = re.search(r"(?im)^(?:content-length|l)\s*:\s*(\d+)", head)
        length = int(match.group(1)) if match else 0
        total = end + 4 + length
        if len(buffer) < total:
            return out, buffer
        out.append(parse(buffer[:total]))
        buffer = buffer[total:]


# --- names and parameters ---------------------------------------------------

def new_tag() -> str:
    return secrets.token_hex(5)


def new_branch() -> str:
    return "z9hG4bK" + secrets.token_hex(8)


def new_call_id(host: str) -> str:
    return f"{secrets.token_hex(10)}@{host}"


def param(value: str, name: str) -> str:
    """A `;name=value` parameter of a header (the tag of a From, the branch of a Via)."""
    match = re.search(r";\s*" + re.escape(name) + r"=([^;>\s,]+)", value, re.I)
    return match.group(1).strip('"') if match else ""


def uri_of(value: str) -> str:
    """The URI inside a name-addr ('"Bob" <sip:b@x>;tag=1' -> 'sip:b@x')."""
    match = re.search(r"<([^>]+)>", value)
    return match.group(1) if match else value.split(";", 1)[0].strip()


def user_of(value: str) -> str:
    """The user part of a URI or name-addr: the number, for a call."""
    uri = uri_of(value)
    uri = re.sub(r"^(sips?|tel):", "", uri)
    return uri.split("@", 1)[0].split(";", 1)[0]


def display_name(value: str) -> str:
    match = re.match(r'\s*"([^"]*)"', value)
    if match:
        return match.group(1)
    head = value.split("<", 1)[0].strip()
    return head if "<" in value else ""


# --- digest -------------------------------------------------------------------

def _challenge(value: str) -> Dict[str, str]:
    scheme, _, rest = value.partition(" ")
    out = {"scheme": scheme.strip().lower()}
    for match in re.finditer(r'(\w+)\s*=\s*("([^"]*)"|[^,\s]+)', rest):
        out[match.group(1).lower()] = match.group(3) if match.group(3) is not None else match.group(2)
    return out


def authorization(challenge: str, method: str, uri: str, user: str, password: str,
                  nc: int = 1, cnonce: Optional[str] = None) -> str:
    """The Authorization value answering a `WWW-Authenticate`/`Proxy-Authenticate` digest.

    MD5 and MD5-sess, with `qop=auth` when offered (`auth-int` is not, since
    nothing here signs bodies). A non-MD5 algorithm raises: answering it with
    MD5 would only fail later and say less.
    """
    c = _challenge(challenge)
    if c["scheme"] != "digest":
        raise ValueError(f"not a digest challenge: {c['scheme']}")
    algorithm = c.get("algorithm", "MD5")
    if algorithm.upper() not in ("MD5", "MD5-SESS"):
        raise ValueError(f"digest algorithm {algorithm} is not supported")
    realm, nonce = c.get("realm", ""), c.get("nonce", "")
    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
    cnonce = cnonce or secrets.token_hex(8)
    ha1 = md5(f"{user}:{realm}:{password}")
    if algorithm.upper() == "MD5-SESS":
        ha1 = md5(f"{ha1}:{nonce}:{cnonce}")
    ha2 = md5(f"{method}:{uri}")
    qops = [q.strip() for q in c.get("qop", "").split(",") if q.strip()]
    parts = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
    if "auth" in qops:
        nc_s = f"{nc:08x}"
        response = md5(f"{ha1}:{nonce}:{nc_s}:{cnonce}:auth:{ha2}")
        parts += [f'response="{response}"', "qop=auth", f"nc={nc_s}", f'cnonce="{cnonce}"']
    else:
        parts.append(f'response="{md5(f"{ha1}:{nonce}:{ha2}")}"')
    parts.append(f"algorithm={algorithm}")
    if "opaque" in c:
        parts.append(f'opaque="{c["opaque"]}"')
    return "Digest " + ", ".join(parts)


def check_authorization(header: str, method: str, password: str) -> bool:
    """For a test server: whether `header` is a right answer for `password`."""
    a = _challenge(header)
    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
    ha1 = md5(f"{a['username']}:{a['realm']}:{password}")
    ha2 = md5(f"{method}:{a['uri']}")
    if a.get("qop") == "auth":
        want = md5(f"{ha1}:{a['nonce']}:{a['nc']}:{a['cnonce']}:auth:{ha2}")
    else:
        want = md5(f"{ha1}:{a['nonce']}:{ha2}")
    return a.get("response") == want


# --- responses ------------------------------------------------------------------

REASONS = {100: "Trying", 180: "Ringing", 183: "Session Progress", 200: "OK",
           401: "Unauthorized", 407: "Proxy Authentication Required", 481: "Call/Transaction Does Not Exist",
           486: "Busy Here", 487: "Request Terminated", 488: "Not Acceptable Here",
           500: "Server Internal Error", 603: "Decline"}


def response(request: Message, status: int, to_tag: str = "", body: str = "",
             extra: Optional[List[Tuple[str, str]]] = None) -> Message:
    """A response to `request`: its Vias, From, To (tagged), Call-ID and CSeq copied back."""
    headers = [("Via", v) for v in request.all("Via")]
    headers.append(("From", request.get("From")))
    to = request.get("To")
    if to_tag and ";tag=" not in to:
        to = f"{to};tag={to_tag}"
    headers += [("To", to), ("Call-ID", request.call_id), ("CSeq", request.get("CSeq"))]
    for rr in request.all("Record-Route"):
        if status < 300 and request.method == "INVITE":
            headers.append(("Record-Route", rr))
    headers += list(extra or [])
    if body:
        headers.append(("Content-Type", "application/sdp"))
    return Message(f"SIP/2.0 {status} {REASONS.get(status, 'Unknown')}", headers, body)
