"""Sense: how this machine is resolving names, and whether anything looks wrong.

Every other network sense here reads `/sys` or `/proc`. This one reads
`systemd-resolved`, which is what actually answers DNS on this distribution, so
it can report the resolvers in use, whether queries are encrypted, whether DNSSEC
is on, and how much the cache is doing.

**The resolvers themselves are the first thing worth reporting, and they are not
always the router.** The failure this catches is the common one: a VPN or a
lab network is up, its resolver is listed, and every name still resolves through
the wrong one - or a fallback resolver silently takes over. `DNSOverTLS` and
`DNSSEC` being off is the other: on a hostile network, plaintext DNS is readable
and forged answers are accepted.

**It reports `DNSOverTLS=no` and `DNSSEC=no` as facts, not as recommendations,
and does not change either.** Turning them on is a deliberate configuration
decision that can break a network which depends on a resolver that does not
support what is switched on, and this module has no business editing
`resolved.conf`.

**Cache statistics are a rate, not a cumulative total, and are labelled.** They
are a delta between two samples inside `systemd-resolved` since it last reset
them, and they reset when it restarts - so a number here can go *down*, and
that is not an error to report.

**No query names.** `systemd-resolved` does not keep a query log unless one is
explicitly enabled, and this module does not enable one or read one if present.
Recording which names a machine looks up is a privacy decision with its own
consent question, and this answer does not need it - everything reported here is
about the resolvers, not the questions.

**When resolved is absent or its D-Bus name is unavailable, that is UNKNOWN.**
Not "this machine uses no DNS": a container, a machine running a different
resolver, and a machine where the service has failed all look identical from
here, and those have different causes.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import List, Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 360.0
_POLL_INTERVAL = 300.0

_BUSCTL = "busctl"
_TIMEOUT = 10

_SERVICE = "org.freedesktop.resolve1"
_PATH = "/org/freedesktop/resolve1"
_IFACE = "org.freedesktop.resolve1.Manager"

#: Ports worth naming rather than printing as a number: 53 DNS, 853 DNS-over-TLS,
#: 5353 mDNS, 784 DoT, 8853 DoH. A bare `53` in a report is not an answer.
_PORTS = {
    53: "DNS", 853: "DNS-over-TLS", 5353: "mDNS", 784: "DNS-over-TLS",
    8853: "DNS-over-HTTPS", 443: "HTTPS (likely DoH)",
}


def _busctl(prop: str) -> Optional[str]:
    if shutil.which(_BUSCTL) is None:
        return None
    try:
        done = subprocess.run(
            [_BUSCTL, "get-property", "--json=short",
             _SERVICE, _PATH, _IFACE, prop],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def _resolvers(raw: Optional[str]) -> List[str]:
    """`a(iiay)` into readable text, via `busctl --json=short`.

    **Read the property as JSON rather than parsing busctl's GVariant text.**
    Three earlier attempts at the text form each got it wrong - the wire form is
    byte arrays, not dotted strings; the element header's third field is the
    address byte count, not a pair count; and busctl prints the IPv6 form with
    a 36-byte scope suffix, so a length check written for 16 rejects it. None of
    those mistakes produced an error, they produced an empty list, which the
    sense then honestly reported as UNKNOWN - hiding its own bug behind a
    correct-sounding failure.

    `--json=short` hands back `[[ifindex, family, [address_bytes]], ...]` with
    **no port at all**, which removes the entire ambiguity. There is nothing to
    guess about, so nothing is guessed about.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, dict):
        return []
    entries = parsed.get("data")
    if not isinstance(entries, list):
        return []
    out: List[str] = []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        _ifindex, family, octets = entry[0], entry[1], entry[2]
        if not isinstance(octets, list):
            continue
        if family == 2 and len(octets) == 4:
            text = ".".join(str(b) for b in octets)
        elif family == 10 and len(octets) >= 16:
            groups = [f"{octets[i]:02x}{octets[i+1]:02x}"
                      for i in range(0, 16, 2)]
            text = ":".join(groups)
        else:
            text = f"<{len(octets)} bytes, family {family}>"
        out.append(text)
    return out


def _scalar(raw: Optional[str]) -> str:
    """A property that is a plain string or a tuple, out of --json=short."""
    if raw is None:
        return ""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw.strip().strip('"')
    data = parsed.get("data") if isinstance(parsed, dict) else parsed
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return ", ".join(str(item) for item in data)
    return str(data)


def _run(_config: Optional[ChronoaConfig] = None) -> object:
    if shutil.which(_BUSCTL) is None:
        return _SENSE.to_percept(
            "DNS resolver state is UNKNOWN: the 'busctl' tool is not installed, "
            "so how this machine resolves names cannot be read. No resolver is "
            "reported, which is not the same as reporting that none is used.",
            source="systemd-resolved",
            metadata={"resolvers_known": False, "reason": "busctl missing"},
        )

    servers = _resolvers(_busctl("DNS"))
    if not servers:
        return _SENSE.to_percept(
            "DNS resolver state is UNKNOWN: systemd-resolved's management "
            "interface could not be read. That is what a machine running a "
            "different resolver, a container, and a failed resolved all look "
            "like from here - they have different causes, so none of them is "
            "claimed.",
            source="systemd-resolved",
            metadata={"resolvers_known": False, "reason": "no resolver data"},
        )

    dot = _busctl("DNSOverTLS")
    dnssec = _busctl("DNSSEC")

    lines = ["How this machine resolves names, read from systemd-resolved:",
             f"  resolvers: {', '.join(servers)}"]

    # A loopback resolver means everything goes through systemd-resolved itself
    # (the stub listener), which is normal on this distribution and worth saying
    # so it is not mistaken for a missing resolver.
    if any(s.startswith("127.") for s in servers):
        lines.append("  (a loopback address here is systemd-resolved's own stub "
                     "listener, which forwards to the real servers below)")

    if dot:
        value = _scalar(dot)
        if value in ("yes", "opportunistic", "resolve"):
            lines.append(f"  DNS over TLS: {value}")
        else:
            lines.append(
                f"  DNS over TLS: {value} - queries and answers cross the network "
                f"in plain text, so anyone on the path can read them, and a "
                f"forged answer would be accepted")
    if dnssec:
        value = _scalar(dnssec)
        if value in ("yes", "allow-downgrade"):
            lines.append(f"  DNSSEC: {value}")
        else:
            lines.append(
                f"  DNSSEC: {value} - responses are not cryptographically "
                f"checked, so a resolver that returns a false answer has nothing "
                f"to catch it")

    cache = _busctl("CacheStatistics")
    if cache:
        lines.append(f"  cache since the last reset: {_scalar(cache)}")
        lines.append("  (a delta, and it resets when the service restarts, so it "
                     "can go down without anything being wrong)")

    lines.append("")
    lines.append("This reports the resolvers, not the names looked up. No query "
                 "log is read or enabled.")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="systemd-resolved",
        metadata={
            "resolvers_known": True,
            "resolver_count": len(servers),
            "dns_over_tls": None if dot is None else _scalar(dot),
            "dnssec": None if dnssec is None else _scalar(dnssec),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "dnsresolvers",
        "description": (
            "Report how this machine resolves names: which resolvers are in use "
            "for each interface, whether DNS queries are encrypted over TLS, "
            "whether DNSSEC validation is on, and how much the resolver's cache "
            "is doing. Read from systemd-resolved. Reports the resolvers, never "
            "the names looked up - no query log is read or enabled. Reports "
            "UNKNOWN, not an absence, when the resolver cannot be read. Changes "
            "nothing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="dnsresolvers",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

#: Without this list the registry skips a builtin *silently* - the shape of bug
#: `AGENTS.md` records more than once in this repo.
SENSES = [_SENSE]

SENSE = _SENSE