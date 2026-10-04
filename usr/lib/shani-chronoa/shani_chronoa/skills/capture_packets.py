"""Skill: watch the packets on one interface, read by count not by interface.

"How do I know what is actually crossing this link?" is the question that ends a
long network debugging session, and nothing in this package could answer it.
`interface_counters` says how many bytes moved and `routing_table` says where
they are *sent*; neither shows what was in them. `netprovision.py`'s own
docstring claims a lab network is legible to `tcpdump`, which is only true if
something can run it.

**This runs `tcpdump`, and says so, because that is the one thing it does that
nothing else here does: it reads packet *contents*.** Every other skill either
reads kernel counters or a text file. This one can see payloads on the wire -
including anything not encrypted, which on a machine with a browser open is a
great deal. That is why it is gated behind its own consent key rather than
folded into `network-sense-enabled`, and why the gate text says "packet
contents" in those words.

**It counts packets and names protocols; it does not print payloads.** The
`-c` count and no `-X`/`-A`/`-x` mean the output is a one-line-per-packet
summary: timestamp, source, destination, protocol, length, and the flags. That
is enough to answer reachability, a stuck handshake, a DNS lookup that never
came back, or traffic that is going somewhere unexpected - which is what this
is for. A payload dump would answer "what exactly was sent", which is a
different question with a much larger blast radius, and `tcpdump` can do it
without this skill's permission if a person runs it themselves.

**Bounded three ways, because a packet capture that does not stop is a
liability.** A packet ceiling, a wall-clock ceiling, and output rows all apply,
and each is checked. The defaults are deliberately small: a capture is an
investigation, not a monitoring daemon.

**No root, no `setuid`.** `tcpdump` drops privileges itself once it has opened
the interface; this runs it as the ordinary desktop user and, when it cannot
open the interface, reports that. An unprivileged `tcpdump` on a modern kernel
can still capture on interfaces its owner may read, so "it needs root" is not
assumed either way - the result says what actually happened.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_TCPDUMP = "tcpdump"

#: This skill's own consent key. Its own switch because it is the only
#: permission in the package that reads packet contents.
_CONSENT_KEY = "packet-capture-enabled"

#: Hard ceilings. A capture that ignores these is an open-ended capture, and the
#: user asked a question, not for a daemon.
_MAX_PACKETS = 500
_MAX_SECONDS = 30
_MAX_ROWS = 500
_DEFAULT_PACKETS = 100
_DEFAULT_SECONDS = 10

#: Interface names come from the kernel, but a caller can pass one and it ends
#: up on a command line, so it is validated here rather than trusted.
_IFACE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")

#: A BPF filter is passed to tcpdump as ONE argv element, so no shell is
#: involved. These are the checks that keep a filter expression from being a way
#: to smuggle anything: a filter is not a filename, cannot start with a shell
#: metacharacter, and is length-bounded.
_MAX_FILTER = 200
_FILTER_ALLOWED = re.compile(r"^[A-Za-z0-9_.:!<>=\[\] \t()\-/,|'\"&+*]*$")


def _missing() -> Optional[str]:
    if shutil.which(_TCPDUMP):
        return None
    return (
        f"Not done: {_TCPDUMP} is not installed, so packets cannot be read. "
        f"Install the 'tcpdump' package, then ask again."
    )


def _iface(arguments: dict) -> Tuple[Optional[str], Optional[str]]:
    raw = arguments.get("interface")
    if raw is None:
        # No interface named: any that carries traffic. tcpdump needs one, and
        # picking for the user would mean picking a namespace they did not ask
        # about, so this names the requirement instead.
        return (None,
                "Name the interface to watch, e.g. interface='enp4s0'. "
                "Interfaces are listed by interface_counters.")
    if not isinstance(raw, str) or not _IFACE.match(raw.strip()):
        return (None,
                f"'interface' must be an interface name of letters, digits, "
                f"'.', '_', ':', '-' or '@' up to 15 characters, not {raw!r}.")
    return raw.strip(), None


def _bounded_int(arguments: dict, key: str, default: int, ceiling: int,
                 minimum: int = 1) -> Tuple[int, Optional[str]]:
    value = arguments.get(key)
    if value is None:
        return default, None
    if isinstance(value, bool) or not isinstance(value, int):
        return 0, (f"'{key}' must be a whole number of {minimum} or more, "
                   f"not {value!r}.")
    if value < minimum:
        return 0, (f"'{key}' must be {minimum} or more, not {value!r}. "
                   f"The ceiling is {ceiling}.")
    return min(value, ceiling), None


def _filter(arguments: dict) -> Tuple[Optional[str], Optional[str]]:
    raw = arguments.get("filter")
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, f"'filter' must be a string, not {raw!r}."
    text = raw.strip()
    if not text:
        return None, None
    if len(text) > _MAX_FILTER:
        return None, (f"'filter' is {len(text)} characters; this accepts up to "
                      f"{_MAX_FILTER}. Put the detail in a file and use "
                      f"tcpdump yourself if you need more.")
    if not _FILTER_ALLOWED.match(text):
        return None, (
            "'filter' may only use the characters a packet filter needs "
            "(letters, digits, spaces and the usual comparison and protocol "
            "punctuation). Anything else is refused rather than passed to "
            "tcpdump, because this cannot tell a filter from something else."
        )
    return text, None


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies.

    Checked here, in the skill, rather than only in the settings window, because
    the gate is the boundary and a key nothing reads is not a gate. `network`
    listing interfaces does not need this - reading the kernel's counters is
    `interface_counters` and is ungated - so the message says what still works.
    """
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"reading network packets is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Reporting how much traffic each interface has carried "
            f"needs no permission - that reads the kernel's counters and "
            f"changes nothing. Only what is inside the packets needs this."
        )
    return True, ""


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to watch network packets: {reason}"

    # Every argument is validated BEFORE anything is run or required. Checking
    # `interface` first meant a call with both a missing interface and a
    # malformed filter reported only the missing one - and the user fixed that,
    # and hit the second problem on the next turn. A rejected call should say
    # everything wrong with it at once, so the argument checks lead.
    filt, problem = _filter(arguments)
    if problem:
        return problem
    packets, problem = _bounded_int(arguments, "packets", _DEFAULT_PACKETS,
                                    _MAX_PACKETS)
    if problem:
        return problem
    seconds, problem = _bounded_int(arguments, "seconds", _DEFAULT_SECONDS,
                                    _MAX_SECONDS)
    if problem:
        return problem

    problem = _missing()
    if problem:
        return problem

    iface, problem = _iface(arguments)
    if problem:
        return problem

    # `-n` so names are not resolved: reverse DNS on every packet can add
    # seconds and would send the addresses of everything on the wire to a DNS
    # server. `-l` so a long capture still prints as it goes.
    argv = [_TCPDUMP, "-n", "-l", "-c", str(packets), "-w", "-"]
    if iface:
        argv += ["-i", iface]
    if filt:
        argv.append(filt)

    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=seconds + 5, check=False)
    except FileNotFoundError:
        return (f"Not done: {_TCPDUMP} disappeared between the check and the "
                f"run.")
    except subprocess.TimeoutExpired:
        return (f"Not done: the capture did not finish within {seconds}s and was "
                f"stopped. Either fewer than {packets} packets crossed the "
                f"interface in that time, or tcpdump was still resolving "
                f"something. The first is itself the answer if you were "
                f"testing whether anything was flowing.")
    except OSError as exc:
        return f"Not done: {_TCPDUMP} could not be run ({exc})."

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        # Permission is spread over TWO lines - "You don't have permission to
        # perform this capture" and "(socket: Operation not permitted)". Taking
        # only the last line meant the explanation never appeared and the user
        # saw a bare errno with nothing to act on. Measured on this host:
        # rc=1, nothing on stdout, both lines on stderr.
        blob = " ".join(detail)
        if "permission" in blob.lower():
            return (
                f"Not done: {_TCPDUMP} could not open the interface. It said: "
                f"{blob}. Capturing needs permission to read the link, and this "
                f"runs as your own account rather than as root, so it will not "
                f"ask you for a password to escalate. If you want the capture, "
                f"run tcpdump yourself - and remember it reads packet contents."
            )
        return (f"Not done: {_TCPDUMP} said: "
                + (blob or f"exit {proc.returncode}"))

    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return (
            f"Nothing captured: no packets matched within {seconds}s"
            + (f" on {iface}" if iface else "")
            + (f" for filter {filt!r}" if filt else "")
            + ". That is a measurement of what crossed, not a claim that the "
              "network is idle - a capture can also miss traffic on another "
              "interface, in another network namespace, or on the other side "
              "of a router."
        )

    shown = lines[:_MAX_ROWS]
    out = [
        f"{len(lines)} packet(s) captured"
        + (f" on {iface}" if iface else "")
        + (f" matching {filt!r}" if filt else "")
        + f", out of a request for at most {packets}.",
        "These are tcpdump's summaries - address, protocol, length and flags. "
        "Payload contents are not shown.",
        "",
    ]
    out.extend(shown)
    if len(lines) > len(shown):
        out.append(f"... {len(lines) - len(shown)} more packet(s) not shown.")

    # A short, factual reading of what was there, so the caller does not have
    # to summarise a wall of lines and miscount it.
    protocols: dict = {}
    for line in shown:
        parts = line.split()
        # The protocol name follows the ">" / "<" token in tcpdump's summary.
        if len(parts) >= 4:
            token = parts[2].lstrip("<>")
            if token and not token.replace(".", "").isalpha():
                token = parts[3].rstrip(":.")
            protocols[token] = protocols.get(token, 0) + 1
    if protocols:
        out.append("")
        out.append("By protocol: " + ", ".join(
            f"{name} {count}" for name, count in
            sorted(protocols.items(), key=lambda kv: -kv[1])[:8]))
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "capture_packets",
        "description": (
            "Watch packets crossing one network interface for a few seconds, and "
            "report tcpdump's one-line-per-packet summaries: who sent to whom, "
            "which protocol, how long, and the TCP flags. This reads packet "
            "contents on the wire - it is the one thing in this package that "
            "does - so it is gated separately and shows no payload text. "
            "Bounded by packet count, seconds and output rows. Read-only: it "
            "captures and prints, and changes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": ("The interface to watch, e.g. 'enp4s0'. "
                                    "Required: interface_counters lists them."),
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
                "filter": {
                    "type": "string",
                    "description": ("A tcpdump filter expression, e.g. 'tcp port "
                                    "53' or 'host 10.0.0.5'. Passed as one "
                                    "argument, never through a shell."),
                },
            },
        },
    },
}


SKILLS = [Skill(name="capture_packets", schema=SCHEMA, run=_run)]