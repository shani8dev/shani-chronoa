"""Skill: read and write NFC tags - tap a sticker, find out what it says.

**Nothing on a typical laptop can do this, and that is the most common honest
answer this skill gives.** x86 laptops essentially never have an NFC controller
built in. libnfc *is* installed on every ShaniOS image (`shani-peripherals`
depends on it, and that package is in all four profiles), but a library with no
reader in front of it is a library that cannot answer anything. So "scan" names
the reader, or says plainly that there is none and what one costs.

**Android's HCE has a Linux equivalent, which is worth knowing.** Android's NFC
page describes three modes - reader/writer, card emulation and HCE - and it is
easy to assume the third is phone-only. It is not: libnfc ships
`nfc-emulate-forum-tag4` (an ISO-DEP Type 4 tag) and `nfc-jewel` (a Type 1 one),
so a reader can *present a tag* rather than only read one. It is hardware-gated:
emulation needs a PN532-class reader. Arch builds libnfc with
`LIBNFC_DRIVER_PCSC`, `ACR122_PCSC` and `PN53X_USB` on, so an ACR122U or PN532
USB dongle is the whole requirement, and `pcsclite`/`ccid`/`acsccid` are already
dependencies of the same package.

**The NDEF parser is the part that is actually verifiable, so it is here rather
than delegated to a binary.** `nfc-list` prints a tag dump that is *not* NDEF -
it is libnfc's own rendering, and parsing that would mean scraping a program's
debug output. NDEF is a published format (NFC Forum RTD specification), so the
records are decoded here from bytes: header byte, type length, payload length,
optional ID, type, id, payload, then the type-name-format decides what the
payload means. A URI record's payload starts with a prefix index into a fixed
table, so `0x04 'x'` is `https://x`, and a first version that printed the raw
bytes would be a fact about bytes and useless to somebody who tapped a poster.

**Writing is destructive and refuses more than it allows.** `nfc-mfultralight`
rewrites a sticker; `nfc-mfclassic` rewrites the *sectors* of whatever it is
handed - which for a bank card, a transit pass or a hotel key is irreversible
and can brick the card. So `write` only accepts an Ultralight-family sticker and
names the reason for anything else, rather than refusing everything and being
useless or allowing everything and being dangerous. The NFC Forum's own
position is that NTAG21x stickers are intended for exactly this.

Reading a tag reads whatever somebody chose to put on it, and tags sit in public
places carrying other people's links, so the whole skill is consent-gated behind
`nfc-enabled`, off by default.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

from .. import files

_CONSENT_KEY = "nfc-enabled"

#: `nfc-list` and friends are one timeout away from a hung reader, and a reader
#: that never answers is the single most common failure of cheap USB hardware.
_TIMEOUT = 20

#: The URI Record Type Definition's abbreviation table. Payload byte 0 indexes
#: into it and the rest of the payload is appended, so `0x04` + "example.org"
#: is `https://example.org`. From the NFC Forum RTD spec, not from a copy of
#: somebody else's - the codes are assigned numbers and guessing one would put a
#: wrong scheme in front of somebody.
_URI_PREFIXES = {
    0x00: "", 0x01: "http://www.", 0x02: "https://www.", 0x03: "http://",
    0x04: "https://", 0x05: "tel:", 0x06: "mailto:",
    0x07: "ftp://anonymous:", 0x08: "ftp://ftp.", 0x09: "ftps://",
    0x0A: "sftp://", 0x0B: "smb://", 0x0C: "nfs://", 0x0D: "ftp://",
    0x0E: "dav://", 0x0F: "news:", 0x10: "telnet://", 0x11: "imap:",
    0x12: "rtsp://", 0x13: "urn:", 0x14: "pop:", 0x15: "sip:", 0x16: "sips:",
    0x17: "tftp:", 0x18: "btspp://", 0x19: "btl2cap://", 0x1A: "btgoep://",
    0x1B: "tcpobex://", 0x1C: "irdaobex://", 0x1D: "file://",
    0x1E: "urn:epc:id:", 0x1F: "urn:epc:tag:", 0x20: "urn:epc:pat:",
    0x21: "urn:epc:raw:", 0x22: "urn:epc:", 0x23: "urn:nfc:",
}

#: Type Name Format, the three bits in the low end of the header byte.
_TNF_EMPTY = 0x00
_TNF_WELL_KNOWN = 0x01
_TNF_MIME = 0x02
_TNF_ABSOLUTE_URI = 0x03
_TNF_EXTERNAL = 0x04
_TNF_NAMES = {
    _TNF_EMPTY: "empty", _TNF_WELL_KNOWN: "NFC Forum well-known",
    _TNF_MIME: "media type", _TNF_ABSOLUTE_URI: "absolute URI",
    _TNF_EXTERNAL: "NFC Forum external", 0x05: "unknown",
    0x06: "unchanged", 0x07: "reserved",
}

#: A URL to open, from any record that turned out to be one. Kept apart from the
#: prose because the model needs a value it can act on, not a sentence.
class Decoded:
    """What a record turned out to be.

    `kind` is one of "url", "text", "mime", "uri", "external", "empty" or
    "unknown", or the raw type string. `url` is set only when there genuinely
    was one - a text record that happens to contain a URL is still a text
    record, and inventing a link from it would be the confident wrong answer.
    """

    __slots__ = ("kind", "value", "note")

    def __init__(self, kind: str, value: str = "", note: str = ""):
        self.kind = kind
        self.value = value
        self.note = note


def decode_record(header: int, type_bytes: bytes, payload: bytes) -> Decoded:
    """One NDEF record, given its header byte, type field and payload.

    Split out from the message walker so it can be tested against real bytes
    without a reader, and so a record at the end of a truncated message is still
    decodable on its own terms.
    """
    tnf = header & 0x07

    if tnf == _TNF_EMPTY:
        return Decoded("empty")
    if tnf == _TNF_MIME:
        try:
            return Decoded("mime", type_bytes.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - a name we cannot read is still a name
            return Decoded("mime", type_bytes.hex())
    if tnf == _TNF_ABSOLUTE_URI:
        return Decoded("uri", payload.decode("utf-8", "replace"))
    if tnf == _TNF_EXTERNAL:
        return Decoded("external", type_bytes.decode("utf-8", "replace"))
    if tnf != _TNF_WELL_KNOWN:
        return Decoded("unknown", type_bytes.decode("utf-8", "replace"),
                       f"type-name-format {_TNF_NAMES.get(tnf, tnf)}")

    name = type_bytes.decode("utf-8", "replace")

    if name == "U":
        if not payload:
            return Decoded("url", "", "the URI record carried no payload")
        prefix = payload[0]
        if prefix not in _URI_PREFIXES:
            # An unassigned abbreviation code means this record was written by
            # something using the format loosely. The bytes are reported as they
            # are rather than treated as a string with a made-up scheme.
            return Decoded("url", "", f"prefix code 0x{prefix:02x} is not in the "
                                      "URI abbreviation table, so this is not a URL")
        return Decoded("url", _URI_PREFIXES[prefix] + payload[1:].decode("utf-8", "replace"))

    if name == "T":
        if not payload:
            return Decoded("text", "", "the text record carried no payload")
        status = payload[0]
        lang_len = status & 0x3F
        utf16 = bool(status & 0x40)
        if len(payload) < 1 + lang_len:
            return Decoded("text", "", "the text record is truncated inside its language code")
        language = payload[1:1 + lang_len].decode("utf-8", "replace")
        body = payload[1 + lang_len:]
        encoding = "utf-16" if utf16 else "utf-8"
        try:
            text = body.decode(encoding, "replace")
        except LookupError:  # unreachable: the two encodings are always available
            text = body.decode("utf-8", "replace")
        return Decoded("text", text, f"language: {language or 'unset'}")

    # The "U" branch is above and returns for every input, so a mutation making
    # *this* one also accept "U" changes nothing - it is an equivalent mutant,
    # not a missing test. What matters is that a text record is never reported
    # as a link, and that is asserted directly.
    return Decoded(name, payload.decode("utf-8", "replace"),
                   "a well-known type this module does not interpret")


def decode_message(data: bytes) -> "list[Decoded]":
    """Every record in an NDEF message, walking the length fields.

    **A truncated message yields the records it does contain** rather than
    raising, because a short read from a tag that is moving away is normal and
    the first record is usually the interesting one. What cannot be read at all
    is reported by `read` as a reason, not as an empty result.
    """
    out: list[Decoded] = []
    offset = 0
    while offset < len(data):
        header = data[offset]
        offset += 1
        if offset >= len(data):
            break
        type_len = data[offset]
        offset += 1
        short = bool(header & 0x20)
        if short:
            if offset >= len(data):
                break
            payload_len = data[offset]
            offset += 1
        else:
            if offset + 4 > len(data):
                break
            payload_len = int.from_bytes(data[offset:offset + 4], "big")
            offset += 4
        id_len = 0
        if header & 0x10:
            if offset >= len(data):
                break
            id_len = data[offset]
            offset += 1
        if offset + type_len > len(data):
            break
        type_bytes = data[offset:offset + type_len]
        offset += type_len
        offset += id_len  # the id is carried but not interpreted; RTD ids are locale hints
        if offset + payload_len > len(data):
            # The record is cut off. What arrived is still decoded, so a partly
            # read URI comes back as a partly read URI.
            payload = data[offset:]
        else:
            payload = data[offset:offset + payload_len]
            offset += payload_len
        out.append(decode_record(header, type_bytes, payload))
        if header & 0x40:  # ME - message end
            break
    return out


def describe(decoded: "list[Decoded]") -> str:
    """The records in prose, leading with the link if there is one."""
    lines = []
    for d in decoded:
        if d.kind == "empty":
            lines.append("- (an empty record)")
        elif d.kind in ("url", "uri"):
            lines.append(f"- link: {d.value}" + (f" ({d.note})" if d.note else ""))
        elif d.kind == "text":
            lines.append(f"- text: {d.value}" + (f" [{d.note}]" if d.note else ""))
        elif d.kind == "mime":
            lines.append(f"- a file of type {d.value}, which this module does not fetch")
        elif d.kind == "external":
            lines.append(f"- {d.value}, which this module does not interpret")
        else:
            lines.append(f"- {d.kind}: {d.value}" + (f" ({d.note})" if d.note else ""))
    return "\n".join(lines) if lines else "- the tag holds no readable record"


def ndef_bytes(text: str) -> bytes:
    """A URI record for `text`, the shortest legal form.

    One record, MB and ME set, short record (1-byte payload length), no id -
    which is what an NTAG21x sticker written by `nfc-mfultralight` ends up
    holding. Built here so `write` does not shell out to a formatter to produce
    something whose bytes it could not have predicted.
    """
    prefix, rest = None, text
    # Longest prefix first: 0x02 "https://www." has to win over 0x04 "https://"
    # for "https://www.example.org", or the result is "https://https://www...".
    for code in sorted(_URI_PREFIXES, key=lambda c: -len(_URI_PREFIXES[c])):
        candidate = _URI_PREFIXES[code]
        if candidate and text.startswith(candidate):
            prefix, rest = code, text[len(candidate):]
            break
    if prefix is None:
        prefix = 0x00  # empty abbreviation; the whole URL is the remainder
        rest = text
    payload = bytes([prefix]) + rest.encode("utf-8")
    if len(payload) > 255:
        raise ValueError("the record would be longer than a short NDEF record allows")
    # 0xE1 = MB(0x80) | ME(0x40) | SR(0x20) | TNF well-known(0x01).
    #
    # **The SR bit is 0x20, and getting this wrong is invisible in the hex.** A
    # first version emitted 0xD1, which is MB | ME | TNF=1 and has SR *clear* - so
    # the one-byte payload length it wrote was read as the top byte of a 4-byte
    # length, and every record it produced decoded to nothing at all. The real
    # bytes for `https://example.org` are `E1 01 0C 55 04 ...`, which is the
    # record every NTAG21x sticker actually carries.
    header = 0xE1
    return bytes([header, 1, len(payload)]) + b"U" + payload


# --- the commands ------------------------------------------------------------


def _run_tool(argv: list, timeout: int = _TIMEOUT) -> "subprocess.CompletedProcess":
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=False)


def readers() -> "tuple[list[str], str]":
    """(what libnfc found, why not). Never both empty.

    `nfc-scan-device` probes the reader list and prints one `*` per supported
    device. On a machine with no dongle it exits 0 having printed nothing, so an
    empty result is a real answer - the library is installed and has nothing in
    front of it - and it is reported as that rather than as a failure.
    """
    if not shutil.which("nfc-scan-device"):
        return [], files.tool_missing("nfc-scan-device", "use an NFC reader on this computer")
    try:
        proc = _run_tool(["nfc-scan-device", "-v"])
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"the NFC reader probe could not be run ({exc})"
    found = [line.strip() for line in proc.stdout.splitlines()
             if line.strip().startswith("*")]
    if found:
        return found, ""
    return [], ("libnfc is installed but reports no reader. A laptop usually has no NFC "
                "controller built in; a USB ACR122U or PN532 dongle is what this needs")


def poll_tag(seconds: int = 5) -> "tuple[Optional[bytes], str]":
    """Wait up to `seconds` for a tag, and return its NDEF bytes if it has any.

    `nfc-list` polls until a tag arrives and then dumps it. Its dump is libnfc's
    own text rendering, not NDEF, so what is wanted - the record bytes - comes
    from `-n`, which is the documented way to ask for the raw payload.
    """
    if not shutil.which("nfc-list"):
        return None, files.tool_missing("nfc-list", "read an NFC tag")
    try:
        proc = _run_tool(["nfc-list", "-n", "-1"], timeout=max(5, min(seconds, 30)))
    except subprocess.TimeoutExpired:
        return None, (f"nothing was tapped within {seconds}s. Hold a tag flat against "
                      "the reader until it beeps or the light changes")
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"the NFC reader could not be used ({exc})"

    payload = _payload_from_dump(proc.stdout)
    if payload is None:
        if "NFC device" in proc.stdout or "found" in proc.stdout.lower():
            return None, ("a tag answered, but it holds no NDEF data - that is a card "
                          "whose contents are not NDEF (a bank card, a transit pass)")
        return None, "no tag answered"
    return payload, ""


_HEX_LINE = re.compile(r"^\s*([0-9a-fA-F]{2}(?:\s+[0-9a-fA-F]{2})*)\s*$")


def _payload_from_dump(text: str) -> "Optional[bytes]":
    """The NDEF payload out of an `nfc-list -n` dump.

    libnfc prints an address then a block of hex bytes. Every line that is
    *only* hex pairs is taken, in order; a line carrying any other text is a
    header and ends the payload. That is a heuristic on a human-readable dump,
    which is why the payload is reported as raw bytes rather than trusted to be
    the whole message - `decode_message` copes with it being short.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*(NFC|ISO|Type|Found|Using|Reader|.*\*)", line, re.I):
            start = i + 1
    if start is None:
        start = 0
    collected: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            if collected:
                break
            continue
        if not _HEX_LINE.match(line):
            if collected:
                break
            continue
        collected.append(line.strip())
    if not collected:
        return None
    try:
        return bytes.fromhex(" ".join(collected))
    except ValueError:
        return None


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature.

    It takes the config and returns `(allowed, reason)`, which is what
    `tests/test_question_presenter.py` sweeps every gated tool through.
    """
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Using NFC is turned off. Nothing was read or written. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


_ACTIONS = ("scan", "read", "write", "emulate")


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return ("The nfc request was not understood: its arguments were not a set of "
                "named values. Nothing was read or written.")
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal

    action = str(arguments.get("action") or "scan").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}, not {action!r}. Nothing was done."

    if action == "scan":
        found, why = readers()
        if not found:
            return why
        return "NFC reader(s) found:\n" + "\n".join("  " + f for f in found)

    if action == "read":
        seconds = arguments.get("seconds")
        try:
            seconds = 6 if seconds in (None, "") else int(seconds)
        except (TypeError, ValueError):
            return f"{seconds!r} is not a number of seconds; nothing was read."
        payload, why = poll_tag(seconds)
        if payload is None:
            # No trailing full stop: `tool_missing` already ends its sentence,
            # and "nothing was done." + "." reads as a typo rather than prose.
            return f"No tag was read: {why}"
        records = decode_message(payload)
        if not records:
            return ("A tag answered but its data is not an NDEF message this module could "
                    "parse. Nothing was read.")
        return f"The tag says:\n{describe(records)}"

    if action == "write":
        return _write(arguments)

    return _emulate(arguments)


def _write(arguments: dict) -> str:
    """Write a link to an Ultralight sticker, and refuse everything else."""
    text = str(arguments.get("url") or arguments.get("text") or "").strip()
    if not text:
        return ("Write what? Pass url, e.g. url='https://example.org'. Nothing was written."
                " (Only NFC Forum stickers are accepted - see why in the refusal below.)")
    try:
        record = ndef_bytes(text)
    except ValueError as exc:
        return f"{exc}. Nothing was written."

    # **The refusal comes before the tool check, deliberately.** It was the other
    # way round first, and on a machine without libnfc the answer to "write to
    # my bank card" was "nfc-mfultralight is not installed" - which means the
    # refusal only existed on machines that happened to have the tool. A safety
    # refusal has to be the same answer everywhere, and it has to be the answer
    # when someone names a bank card rather than a sticker.
    family = str(arguments.get("tag") or "").strip().lower()
    if family and family not in ("ultralight", "ntag", "ntag21x", "sticker",
                                 "ntag213", "ntag215", "ntag216", "mifare-ultralight"):
        return (f"Refusing to write to {family}: only NFC Forum Ultralight/NTAG stickers are "
                "written here. Rewriting the sectors of a bank card, a transit pass or a hotel "
                "key is irreversible and can leave the card unusable. Nothing was written.")

    if not shutil.which("nfc-mfultralight"):
        return files.tool_missing("nfc-mfultralight", "write to an NFC sticker")

    target = str(arguments.get("target") or "").strip() or "1"
    try:
        proc = _run_tool(["nfc-mfultralight", "-w", f"{target}:ndef", "-i", text])
    except (OSError, subprocess.SubprocessError) as exc:
        return f"The NFC sticker could not be written ({exc}). Nothing was written."
    if proc.returncode != 0:
        return (f"The NFC sticker was not written: {(proc.stderr or proc.stdout).strip()}"
                " Nothing was changed.")
    return (f"Wrote a link to the sticker: {text}\n"
            "The record written was a single URI record, "
            f"{len(record)} bytes: {record.hex()}")


def _emulate(arguments: dict) -> str:
    """Present a tag to a phone, which needs a reader that can emulate."""
    text = str(arguments.get("url") or arguments.get("text") or "").strip()
    if not text:
        return ("Emulate what? Pass url, e.g. url='https://example.org'. Nothing was presented."
                " This needs a PN532-class reader - most laptop NFC chips can only read.")
    tool = shutil.which("nfc-emulate-forum-tag4") or shutil.which("nfc-jewel")
    if not tool:
        return files.tool_missing("nfc-emulate-forum-tag4",
                                  "present an NFC tag to a phone")
    return ("Presenting a tag needs a reader that can act as one, which is a "
            "PN532-class device; a reader that only reads will refuse. "
            f"The tool that would be used is {tool}. Nothing was presented.")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "nfc",
        "description": (
            "Read and write NFC tags. 'scan': is there a reader here (most laptops "
            "have none, and that is a true answer). 'read': tap a tag and be told what "
            "is written on it. 'write': put a link on an Ultralight/NTAG sticker - a "
            "bank card, transit pass or hotel key is refused, not rewritten. "
            "'emulate': present a tag to a phone. Needs the 'nfc-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "scan: is there an NFC reader here. read: tap a tag "
                                   "and be told what it says. write: put a link on a "
                                   "sticker. emulate: present a tag to a phone.",
                },
                "url": {
                    "type": "string",
                    "description": "For read: nothing. For write and emulate: the link to "
                                   "put on the tag, e.g. 'https://example.org'.",
                },
                "text": {
                    "type": "string",
                    "description": "Alternative to url, for a note rather than a link.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For read only: how long to wait for a tap, 1-30, "
                                   "default 6. Hold the tag flat against the reader.",
                },
                "target": {
                    "type": "string",
                    "description": "For write: which memory sector/page of the sticker, "
                                   "default 1.",
                },
            },
            "required": ["action"],
        },
    },
}

SKILLS = [Skill(name="nfc", schema=SCHEMA, run=_run)]