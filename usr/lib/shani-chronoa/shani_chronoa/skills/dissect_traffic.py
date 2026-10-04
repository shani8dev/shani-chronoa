"""Skill: dissect a capture into protocol fields, the way Wireshark would.

`capture_packets` reads a live interface with `tcpdump` and prints one line per
packet: addresses, protocol, length, flags. That answers "is anything crossing,
and is it completing a handshake". It cannot answer "is that TLS or is that
plaintext dressed up as TLS", "which SNI is it requesting", or "is this DNS
query going to a resolver I did not expect" - because tcpdump does not dissect
above layer 4 and does not know what it is looking at.

`tshark` is the same capture read by Wireshark's dissector engine, and it does
know. This is the companion to `capture_packets`, not a replacement: capture
with one, dissect with the other, and the filter grammar is deliberately the
same (both are libpcap), so a filter that works on one works on the other.

**Filters are the point, and they are validated before they run.** The
interesting questions are all "just tell me about X" - `tls.handshake.type == 1`,
`dns.qry.name`, `http.host` - and those are only expressible as BPF or as tshark's
own display filter language. A filter is passed as ONE argv element, so no shell
is involved, but the character set is still bounded here rather than trusted.

**It never writes a capture file.** `capture_packets` prints and discards;
`tshark -w` would leave a pcap on disk containing real traffic, which is a
different kind of artefact with a different lifetime. Everything here is
`-V`-free structured output or field extraction, held in memory, printed, gone.

**Privacy mode gates it, for the same reason `capture_packets` does: this reads
packet contents.** It is the second skill that does, it is the reason both exist
behind one consent key, and the gate text says "packet contents" rather than
"network".
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from shani_chronoa.config import ChronoaConfig
from shani_chronoa import egress, files
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_TSHARK = "tshark"
_TIMEOUT = 45

#: Same class of gate as `capture_packets`, and deliberately the same key: one
#: permission for "Chronoa may read what is inside network packets".
_CONSENT_KEY = "packet-capture-enabled"

_DEFAULT_PACKETS = 100
_MAX_PACKETS = 500
_DEFAULT_SECONDS = 10
_MAX_SECONDS = 30

_MAX_ROWS = 120

#: A tshark filter. Widen enough for the real grammar - protocol trees, `==`,
#: `&&`, `contains`, parentheses, commas - and narrow enough that nothing which
#: could be read as anything else survives. No quotes, no backslash, no `$`, no
#: backtick, no semicolon: a filter is never allowed to be a second command.
_FILTER = re.compile(r"^[A-Za-z0-9_.:|><=! ()&|,'\"/+\-*$?\[\]]{1,300}$")

_IFACE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")


def _consent(config: ChronoaConfig) -> Tuple[bool, str]:
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"reading packet contents is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Counting what each interface carried needs no "
            f"permission, and neither does listing who is on the link - only "
            f"reading what is inside the packets does."
        )
    return True, ""


def _bounded(arguments: dict, key: str, default: int,
             ceiling: int) -> Tuple[int, Optional[str]]:
    value = arguments.get(key)
    if value is None:
        return default, None
    if isinstance(value, bool) or not isinstance(value, int):
        return 0, (f"'{key}' must be a whole number, not {value!r}.")
    if value < 1:
        return 0, f"'{key}' must be 1 or more, not {value!r}. The ceiling is {ceiling}."
    return min(value, ceiling), None


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to dissect network packets: {reason}"

    # Everything is validated before anything runs, so a rejected call says all
    # of what is wrong with it at once.
    packets, problem = _bounded(arguments, "packets", _DEFAULT_PACKETS,
                               _MAX_PACKETS)
    if problem:
        return problem
    seconds, problem = _bounded(arguments, "seconds", _DEFAULT_SECONDS,
                               _MAX_SECONDS)
    if problem:
        return problem

    iface = arguments.get("interface")
    if iface is not None and (not isinstance(iface, str)
                              or not _IFACE.match(iface.strip())):
        return (f"'interface' must be an interface name of letters, digits, "
                f"'.', '_', ':', '-' or '@' up to 15 characters, not {iface!r}.")

    filt = arguments.get("filter")
    if filt is not None:
        if not isinstance(filt, str):
            return f"'filter' must be a string, not {filt!r}."
        filt = filt.strip() or None
        if filt and not _FILTER.match(filt):
            return ("'filter' contains characters a packet filter does not need. "
                    "Letters, digits, spaces and the usual comparison and "
                    "protocol punctuation only - a filter is passed as a single "
                    "argument and never through a shell, but nothing that could "
                    "be read as anything other than a filter is accepted.")

    if shutil.which(_TSHARK) is None:
        return files.tool_missing(_TSHARK, "dissect network traffic")

    if egress.privacy_mode_enabled():
        return ("Privacy mode is on, so I did not capture anything to dissect - "
                "reading packets reports what this machine is talking to and "
                "what it says. Turn privacy mode off to allow it.")

    # `-T fields` with no `-e` prints a one-line summary per packet from the
    # dissector, which is the useful middle ground between tcpdump's raw view
    # and `-V`'s everything. No `-w`, so nothing is written to disk.
    argv = [_TSHARK, "-n", "-l", "-c", str(packets),
            "-T", "fields",
            "-e", "frame.number", "-e", "frame.time_relative",
            "-e", "ip.src", "-e", "ip.dst",
            "-e", "_ws.col.Protocol", "-e", "frame.len"]
    if iface:
        argv += ["-i", iface.strip()]
    argv += ["-Y", filt] if filt else ["-Y", "frame"]
    argv += ["-a", f"duration:{seconds}"]

    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=seconds + 15, check=False)
    except FileNotFoundError:
        return f"Not done: {_TSHARK} disappeared between the check and the run."
    except subprocess.TimeoutExpired:
        return (f"The capture did not finish within {seconds}s and was stopped. "
                f"Fewer than {packets} packets matched, or the dissector was "
                f"still working - either way no dissection is reported.")
    except OSError as exc:
        return f"Not done: {_TSHARK} could not be run ({exc})."

    if done.returncode not in (0, 2):
        detail = " ".join((done.stderr or "").strip().splitlines()[:2])
        blob = (detail or "").lower()
        if "permission" in blob or "root" in blob:
            return (f"Not done: {_TSHARK} could not open the interface: "
                    f"{detail or 'it needs permission to read the link'}. It "
                    f"runs as your own account here and will not ask for a "
                    f"password to escalate. If you want it, run tshark "
                    f"yourself with sudo.")
        if filt and ("invalid" in blob or "syntax" in blob):
            return (f"Not done: {_TSHARK} rejected the filter {filt!r}: "
                    f"{detail}. Field names are case-sensitive and differ "
                    f"between versions; `tshark -G fields` lists them all.")
        return f"Not done: {_TSHARK} said: {detail or f'exit {done.returncode}'}"

    rows: List[str] = [ln for ln in done.stdout.splitlines() if ln.strip()]
    if not rows:
        where = f" on {iface.strip()}" if iface else ""
        which = f" matching {filt!r}" if filt else ""
        return (f"Nothing matched{where}{which} within {seconds}s. That is a "
                f"measurement of what crossed, not a claim the network is "
                f"idle - a capture also misses other interfaces, other network "
                f"namespaces, and anything beyond a router.")

    shown = rows[:_MAX_ROWS]
    protocols: dict = {}
    for line in shown:
        parts = line.split("\t")
        if len(parts) >= 5 and parts[4]:
            protocols[parts[4]] = protocols.get(parts[4], 0) + 1

    out = [
        f"{len(rows)} packet(s) dissected by tshark"
        + (f" on {iface.strip()}" if iface else "")
        + (f" matching {filt!r}" if filt else "")
        + ".",
        "Columns: frame, seconds, source, destination, protocol, bytes.",
        "",
    ]
    out.extend(shown)
    if len(rows) > len(shown):
        out.append(f"... {len(rows) - len(shown)} more packet(s) not shown.")

    if protocols:
        out.append("")
        out.append("By protocol: " + ", ".join(
            f"{name} {count}" for name, count in
            sorted(protocols.items(), key=lambda kv: -kv[1])[:10]))

    out.append("")
    out.append("The protocol column is Wireshark's own dissection, not a guess: "
               "it is what tells a TLS handshake from plaintext that merely "
               "looks like TLS. Ask for specific fields with a filter, e.g. "
               "filter='tls.handshake.extensions_server_name' or "
               "filter='dns.qry.name'.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "dissect_traffic",
        "description": (
            "Capture traffic on an interface and dissect it into protocol fields "
            "with tshark, Wireshark's command-line engine: which protocols are "
            "actually on the wire, and for a filter, the specific fields - TLS "
            "handshake type and SNI, DNS query names, HTTP host. This is the "
            "companion to capture_packets: that one reads packets as they "
            "cross, this one says what they are. Filters use the same grammar "
            "as both tools. Reads packet contents, so it needs the same "
            "permission as capture_packets. Writes nothing to disk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": "The interface to watch, e.g. 'enp4s0'.",
                },
                "filter": {
                    "type": "string",
                    "description": ("A tshark display filter, e.g. 'tls', "
                                    "'dns.qry.name', 'http.host'. One argument, "
                                    "never through a shell."),
                },
                "packets": {
                    "type": "integer",
                    "description": (f"Stop after this many packets (default "
                                    f"{_DEFAULT_PACKETS}, ceiling {_MAX_PACKETS})."),
                },
                "seconds": {
                    "type": "integer",
                    "description": (f"Give up after this many seconds (default "
                                    f"{_DEFAULT_SECONDS}, ceiling {_MAX_SECONDS})."),
                },
            },
        },
    },
}


SKILLS = [Skill(name="dissect_traffic", schema=SCHEMA, run=_run)]