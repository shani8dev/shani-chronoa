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
            "NXDOMAIN: the nameserver says that name does not exist at all. "
            "That is a real answer, not a failure - check the spelling, or "
            "that the domain is registered."
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


def _dnssec(name: str, rtype: str) -> Tuple[str, str]:
    """(`delv`'s verdict sentence, reason).

    **`dig` cannot answer this at all.** `dig +dnssec` asks for DNSSEC records
    and shows the RRSIGs, which is the evidence; `delv` is the tool that
    *validates the chain and states the outcome*, and its first line is the
    verdict itself - measured here as `; fully validated`.

    That distinction matters because the two halves of a DNSSEC failure look
    identical from `dig`: a name that is unsigned and a name whose signature
    does not verify both come back as "some answers, look at the RRSIGs". One
    is expected for a domain that never signed; the other means the answer is
    being forged or the key is broken. `delv` names which.

    `(reason)` is non-empty when the question could not be asked, and the
    caller must not present an absent verdict as a clean one.
    """
    if shutil.which("delv") is None:
        return "", ("delv (the bind package) is not installed, so the DNSSEC "
                    "chain could not be validated")
    try:
        proc = subprocess.run(["delv", name, rtype], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", "delv did not answer within the time allowed"
    except OSError as exc:
        return "", str(exc)
    # delv's verdict is its first `;` comment line, whatever the exit status.
    verdict = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith(";"):
            text = line.lstrip("; ").strip()
            if text and not text.lower().startswith("this query"):
                verdict = text
                break
    if not verdict:
        detail = (proc.stderr or "").strip().splitlines()
        return "", (detail[-1] if detail
                    else f"delv exited {proc.returncode} without a verdict")
    return verdict, ""


#: `host`'s own answers, measured on this box:
#:
#:     example.com has address 172.66.147.243
#:     example.com has IPv6 address 2606:4700:8de5:72db:f2de:da2:ef6b:ff98
#:     example.com mail is handled by 0 .
#:     example.com descriptive text "v=spf1 -all"
#:     3.2.1.in-addr.arpa domain name pointer one.one.one.one.     (reverse)
#:
#: The type is passed with `-t`, which `host` shares with `dig`, so the same
#: rtype works for both.
_HOST_PATTERNS = [
    ("A", "has address"),
    ("AAAA", "has IPv6 address"),
    ("MX", "mail is handled by"),
    ("TXT", "descriptive text"),
    ("NS", "name server"),
    ("SOA", "has SOA record"),
    ("CNAME", "is an alias"),
]

#: `host -t PTR 1.2.3.4` prints `4.3.2.1.in-addr.arpa domain name pointer ...`.
_PTR = "domain name pointer"


def _host_records(name: str, rtype: str) -> "tuple[str, dict]":
    """(rendered, raw). An empty rendered string means it did not work."""
    try:
        proc = subprocess.run(["host", "-t", rtype, name],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return "", {}
    if proc.returncode not in (0, 1):
        # host exits 1 for NXDOMAIN and for a name with no record of the
        # requested type, which are different answers; both go through the
        # empty-output path below.
        return "", {}
    rows = []
    for line in (proc.stdout or "").splitlines():
        text = line.strip()
        if not text or text.startswith(";"):
            continue
        # **`host` puts NXDOMAIN in its *output text*, not in a recognised
        # status field**: `Host foo not found: 3(NXDOMAIN)`. The first version
        # carried that through as an answer line, so "the name does not exist"
        # was reported in the same shape as an address - which is precisely the
        # conflation this module exists to avoid.
        if text.lower().startswith("host ") and "not found" in text.lower():
            return "", {"status": "NXDOMAIN"}
        if "has no " in text.lower() and "record" in text.lower():
            return "", {"status": "NODATA"}
        rows.append(text)
    if not rows:
        return "", {}
    return "\n".join(rows), {"tool": "host"}


def _host_describe(lines: "list[str]", name: str, rtype: str) -> str:
    """`host`'s lines, reworded into the same shape `_describe` returns, so a
    caller cannot tell which tool answered from the answer's structure."""
    if rtype == "PTR":
        # **`rstrip(".")`** - `host`'s pointer records are fully qualified and
        # already end in a dot, so the first version printed
        # "The name is: one.one.one.one.."
        pointers = [line.split(_PTR, 1)[1].strip().rstrip(".")
                    for line in lines if _PTR in line]
        if pointers:
            return f"The name is: {', '.join(pointers)}."
        return "No pointer record came back."
    if not lines:
        return "There is no answer."
    interesting = [line for line in lines if not line.startswith(";;")]
    return "; ".join(interesting)


def _run(arguments: dict) -> str:
    name, problem = _name(arguments.get("name"))
    if problem:
        return problem
    rtype, problem = _rtype(arguments.get("type"))
    if problem:
        return problem

    # **`dig` is not the only thing that can ask, and refusing without it
    # meant a machine with only `host` got nothing.** `host` ships in the same
    # `bind` package on both images and answers every type the schema offers,
    # so the question now has a fallback rather than a refusal. `nslookup` is
    # deliberately not chained: its output is prefixed with its own resolver
    # address and a "Non-authoritative answer" banner that varies between
    # implementations, so a parser written to it would break on the variation.
    if shutil.which(_DIG) is None and shutil.which("host") is None:
        return files.tool_missing("dig", f"look up {name} in DNS")

    # Same switch as ping_host and trace_route, for the same reason: the query
    # goes to a resolver that is not this machine.
    if egress.privacy_mode_enabled():
        return (
            f"Privacy mode is on, so I did not look {name} up - that sends the "
            f"name to a DNS server outside this machine, which tells it what "
            f"this computer is interested in. Turn privacy mode off to allow it."
        )

    egress.record("skill:dns_lookup", name, method=rtype, privacy_mode=False)

    if shutil.which(_DIG) is None:
        # **`host` is not `dig` with a different name, and the difference has
        # to be visible.** `host` cannot report the answer's TTL, does not
        # quote the status line, and prints NXDOMAIN as an exit status rather
        # than a word. So the answer says which tool ran, and the sections the
        # fallback cannot fill are reported as absent rather than as "none".
        rendered, meta = _host_records(name, rtype)
        if not rendered:
            # **The status, when `host` gave one, is the answer - not a refusal
            # to look.** "The name does not exist" and "nothing was heard back"
            # are different facts and both are reportable.
            status = meta.get("status", "")
            if status == "NXDOMAIN":
                return (f"**{name} does not exist.** The resolver said NXDOMAIN, "
                        "which is an assertion that there is no such name - not "
                        "a failure to look, and not a name with no record of "
                        "this type.")
            if status == "NODATA":
                return (f"**{name} exists, but has no {rtype} record.** That is "
                        "a different answer from the name not existing at all, "
                        "and both are worth separating: a domain with no MX "
                        "record still resolves.")
            return (f"No answer for {name} (type {rtype}) came back from "
                    "`host`. An empty answer is not the same as the name being "
                    "absent, and nothing is claimed about which it was.")
        lines = [l for l in rendered.splitlines() if l.strip()]
        out = [f"DNS lookup of {name}, type {rtype}."]
        out.append(_host_describe(lines, name, rtype))
        out.append("")
        out.append("Answered by `host`, because `dig` is not installed on this "
                   "machine. A TTL and a response status are not in that "
                   "output, so neither is claimed here.")
        out.append("")
        out.append("This is what the DNS said. The system's own resolver also "
                   "considers mDNS/Avahi names, so a name that resolves here "
                   "but not above is usually a local device on the network, "
                   "not a public host.")
        out.append("")
        out.append("DNSSEC: unknown - delv (the bind package) is not installed, "
                   "or the answer came from `host`, which does not validate. "
                   "Nothing is claimed about this answer's authenticity.")
        return "\n".join(out)

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

    out = [f"DNS lookup of {name}, type {rtype}."]
    out.append(_describe(answers, rtype, status, note))
    out.append("")
    out.append("This is what the DNS said. The system's own resolver also "
               "considers mDNS/Avahi names, so a name that resolves here but "
               "not above is usually a local device on the network, not a "
               "public host.")

    # The signing verdict, which is a different question from the lookup and
    # the one `dig` cannot answer. Appended as its own labelled section so it
    # is never read as part of the address answer.
    verdict, why = _dnssec(name, rtype)
    out.append("")
    if verdict:
        out.append(f"DNSSEC: {verdict}.")
        if "fully validated" in verdict.lower() or "secure" in verdict.lower():
            out.append("  The chain of trust from a root key to this record "
                       "checks out, so the answer above cannot have been "
                       "altered in transit.")
    else:
        out.append(f"DNSSEC: unknown - {why}. Nothing is claimed about "
                   "whether this answer was signed.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "dns_lookup",
        "description": (
            "Look a hostname or address up in DNS and report what came back: "
            "the addresses, or the mail servers, name servers, TXT records, "
            "certificate records or other records a name publishes, and whether the "
            "answer is DNSSEC-signed and valid. Distinguishes "
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