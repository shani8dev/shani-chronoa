"""Skill: ask a DNS server what a name actually resolves to, and what else is published about it.

The Lookup tab in GNOME's `gnome-nettool` is a front end for `dig`, and nothing
in this package did it. Every other network question here answers from this
machine - its own interfaces, its own routing table, its own sockets - while
"what does `example.com` resolve to, and who is authoritative for it" is a
question about the world's DNS and cannot be answered locally at all.

**`dig` rather than `getaddrinfo`.** The obvious wiring for "resolve this name"
is Python's own resolver, which needs no binary. That is the wrong tool here for
two reasons, both of which the question usually turns on:

- `getaddrinfo` collapses everything to an address list. It cannot answer "is
  there an MX record", "when does this expire", "which nameserver was asked",
  or "did the lookup fail because NXDOMAIN or because no server answered" - and
  those are exactly what someone asking a DNS question wants. `dig` is the tool
  whose output answers them.
- `getaddrinfo` goes through NSS, which on a desktop means resolving over
  mDNS/Avahi as well as DNS, so "what does the DNS say" and "what will this
  program connect to" can legitimately disagree. Both are worth knowing and they
  are not the same question, so this reports the DNS answer and says which
  resolver it used.

**Privacy mode gates it, because it is the one skill here that asks somebody
else's server about a name.** `ping_host` and `trace_route` obey the same switch
and for the same reason: a DNS lookup tells a resolver which names this machine
is interested in. That is a smaller disclosure than a packet trace, and a real
one, and a machine in privacy mode should not make it unasked. The message says
so rather than silently returning nothing.

**Refusals are reported as refusals.** NXDOMAIN and SERVFAIL are different
answers with different fixes, and this distinguishes them instead of printing
both as "no result" - which is the shape of the bug where a typo and a broken
resolver look identical.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from shani_chronoa import egress, files
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_DIG = "dig"
_TIMEOUT = 20

#: Record types worth offering, in the order a person usually wants them. `ANY`
#: is deliberately absent: it is refused by many public resolvers and its
#: response is not a useful superset.
_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "SRV", "CAA", "PTR")

#: A hostname or an address literal. Deliberately strict: this string is put in
#: an argv array (no shell is involved anywhere), but a name carrying a space or
#: a leading dash could still reach `dig` as an option, so the shape is checked
#: here rather than trusted.
_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,253}[A-Za-z0-9])?$")

#: `dig`'s section headers, so a response can be read rather than pasted.
_SECTION = re.compile(r"^;;\s*->>HEADER<<-|\n;;\s*QUESTION:|\n;;\s+(\w+) QTYPE", re.M)


def _name(value: object) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(value, str) or not value.strip():
        return None, "Give a name or address to look up, e.g. name='example.com'."
    text = value.strip()
    if not _NAME.match(text):
        return None, (
            f"{text!r} is not a hostname or address I can look up. A name is "
            f"letters, digits, dots and dashes, starting and ending with a "
            f"letter or digit."
        )
    return text, None


def _rtype(value: object) -> Tuple[str, Optional[str]]:
    if value is None:
        return "A", None
    if not isinstance(value, str):
        return "", f"'type' must be a record type name, not {value!r}."
    text = value.strip().upper()
    if text not in _TYPES:
        return "", (f"Record type must be one of: {', '.join(_TYPES)}. "
                    f"Not {text!r}.")
    return text, None


def _parse(text: str) -> Tuple[Dict[str, List[str]], str, str]:
    """Split `dig +noall +answer +comments` into status, answers and a note."""
    status = ""
    note = ""
    answers: Dict[str, List[str]] = {}
    # The status line looks like: ;; status: NXDOMAIN, id: 1234, flags: ...
    match = re.search(r"status:\s*([A-Z0-9]+)", text)
    if match:
        status = match.group(1)
    if "SERVER FAILED" in text:
        status = status or "SERVFAIL"
    if "connection timed out" in text.lower() or "no servers could be reached" in text.lower():
        note = ("No nameserver answered. That is different from the name not "
                "existing - the query may never have left this machine.")

    # Answer lines are "name ttl class type rdata", with the name possibly
    # semicolon-compressed in a multi-line RRset.
    for line in text.splitlines():
        if not line or line.startswith(";"):
            continue
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        _owner, _ttl, _cls, rtype, rdata = parts
        if not re.match(r"^[A-Z0-9]+$", rtype):
            continue
        # A compressed RRset keeps the rdata on its own line, which is how a
        # multi-value TXT or MX arrives.
        answers.setdefault(rtype, []).append(rdata.strip())
    return answers, status, note


def _describe(answers: Dict[str, List[str]], rtype: str, status: str,
              note: str) -> str:
    if note and not answers:
        return note
    if status == "NXDOMAIN" and not answers:
        return (
            f"NXDOMAIN: the nameserver says that name does not exist at all. "
            f"That is a real answer, not a failure - check the spelling, or "
            f"that the domain is registered."
        )
    if not answers:
        if status and status not in ("NOERROR", ""):
            return (f"The nameserver answered {status}, with no records of type "
                    f"{rtype}. The name exists but has no {rtype} record, or the "
                    f"zone refused the query.")
        return (
            f"No {rtype} records for that name. The lookup succeeded - there is "
            f"simply nothing of that type, which for a name that does resolve "
            f"usually means asking for the wrong type."
        )

    out = []
    for rtype_key, values in answers.items():
        for value in values:
            out.append(f"  {rtype_key:<6} {value}")
    lines = ["The DNS answer:"]
    lines.extend(out)
    # RFC 7505's "null MX" - published on purpose to say a domain does NOT
    # accept mail. Printing it as `0 .` is correct and completely unreadable,
    # and it is the kind of answer that gets mistaken for a broken lookup.
    if answers.get("MX") and all(v.strip() in ("0 .", "0.") for v in answers["MX"]):
        lines.append("")
        lines.append(
            "  That MX is the 'null MX' (RFC 7505): the domain is published "
            "as deliberately not accepting mail, which is why there is no mail "
            "server listed. It is a real setting by whoever controls the "
            "domain, not a missing record."
        )
    if len(answers) > 1:
        lines.append("")
        lines.append(f"({len(answers)} record type(s) came back; the type you "
                     f"asked for was {rtype}.)")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    name, problem = _name(arguments.get("name"))
    if problem:
        return problem
    rtype, problem = _rtype(arguments.get("type"))
    if problem:
        return problem

    if shutil.which(_DIG) is None:
        return files.tool_missing(_DIG, f"look up {name} in DNS")

    # Same switch as ping_host and trace_route, for the same reason: the query
    # goes to a resolver that is not this machine.
    if egress.privacy_mode_enabled():
        return (
            f"Privacy mode is on, so I did not look {name} up - that sends the "
            f"name to a DNS server outside this machine, which tells it what "
            f"this computer is interested in. Turn privacy mode off to allow it."
        )

    argv = [_DIG, "+noall", "+answer", "+comments", "+time=3", "+tries=1",
            "-t", rtype, name]
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (f"The lookup of {name} did not finish within {_TIMEOUT} seconds, "
                f"so no answer is reported. That is a timeout talking to the "
                f"resolver, not a statement about whether {name} exists.")
    except OSError as exc:
        return f"Could not run dig: {exc}."

    combined = done.stdout + done.stderr
    answers, status, note = _parse(combined)
    if not answers and not status and not note:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return ("The lookup produced no answer and no status"
                + (f": {detail[-1]}" if detail else ".")
                + " dig said nothing usable, so nothing is reported about "
                  f"{name}.")

    egress.record(f"skill:dns_lookup", name, method=rtype, privacy_mode=False)

    out = [f"DNS lookup of {name}, type {rtype}."]
    out.append(_describe(answers, rtype, status, note))
    out.append("")
    out.append("This is what the DNS said. The system's own resolver also "
               "considers mDNS/Avahi names, so a name that resolves here but "
               "not above is usually a local device on the network, not a "
               "public host.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "dns_lookup",
        "description": (
            "Look a hostname or address up in DNS and report what came back: "
            "the addresses, or the mail servers, name servers, TXT records, "
            "certificate records or other records a name publishes. Distinguishes "
            "'this name does not exist' from 'the resolver did not answer', "
            "because those have different fixes. Obeys privacy mode, since the "
            "query goes to a resolver outside this machine. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The hostname or address to look up, e.g. 'example.com'.",
                },
                "type": {
                    "type": "string",
                    "description": ("Record type: " + ", ".join(_TYPES)
                                    + ". Defaults to A."),
                },
            },
        },
    },
}


SKILLS = [Skill(name="dns_lookup", schema=SCHEMA, run=_run)]