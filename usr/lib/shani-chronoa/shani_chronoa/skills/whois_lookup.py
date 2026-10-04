"""Skill: who a domain or IP address is registered to, and when it expires.

The Whois tab in GNOME's `gnome-nettool` is a front end for the `whois` client,
and nothing in this package did it. It answers a question nothing else here can:
not "is this host up" (that is `ping_host`) and not "what does this name resolve
to" (that is `dns_lookup`), but "who is accountable for this domain and when does
it lapse".

**It reads a public registry, so privacy mode gates it — for a different reason
than `dns_lookup` does, and the difference is stated.** A DNS lookup discloses
one name to a resolver. A whois lookup sends a domain or an address to a
whois server run by someone else and asks to be told the registrant. The answer
is usually an organisation rather than a person, but not always, and on a domain
registered through a privacy proxy it returns the proxy. Neither fact is a reason
to pretend the query is local, so it obeys the same switch and says why.

**`whois` rather than parsing the registry over the wire.** The command is
installed on the image, knows where the relevant servers are per TLD, and
handles the referral chain that a raw TCP connection to `whois.verisign-grs.com`
would not. Doing it by hand would mean shipping a TLD-to-server table that goes
stale, which is the maintenance burden this avoids.

**A rate-limit refusal is reported as a rate limit, not as "no such domain".**
This is the single most important honesty rule here. Public whois servers refuse
queries aggressively — often several per IP — and their refusal text ("LIMIT
EXCEEDED", "Too many requests", a bare connection close) reads, to a parser that
has not been taught otherwise, exactly like a domain that is not registered. That
error leads someone to conclude a domain is free when it is not, which is a
conclusion with consequences.

**Whois is best-effort by nature, and says so.** It is rate-limited, several
registries redact it, and the format has not been standardised since 1982. The
report therefore prints what came back with the field names the server actually
used rather than pretending to a stable schema.
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

_WHOIS = "whois"
_TIMEOUT = 25

#: A domain or an IPv4/IPv6 literal. Checked here because this value reaches an
#: argv array, and whois treats a leading `-` as an option.
_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._:-]{0,253}[A-Za-z0-9])?$")

#: Refusal wording seen from public whois servers. Matching these is what keeps
#: "you are being rate limited" from being reported as "not registered".
_LIMITED = (
    "limit exceeded", "too many requests", "rate limit", "quota exceeded",
    "too many queries", "query rate", "abuse", "blocked",
    "your ip", "try again later", "server is busy", "too fast",
)

#: Fields worth surfacing, in the order a person wants them. The key is matched
#: against what the server actually printed, because there is no standard.
_FIELDS = (
    ("domain name", ("domain name", "domain name:", "domain")),
    ("registrar", ("registrar:", "registrar ")),
    ("created", ("creation date", "created", "registered on", "registered:")),
    ("expires", ("registry expiry date", "expiry date", "expires", "expiration")),
    ("updated", ("updated date", "last updated", "changed")),
    ("status", ("domain status", "status:", "state")),
    ("nameservers", ("name server", "nserver", "nameserver")),
)

#: A single whois record is thousands of lines of boilerplate. Enough to answer
#: "who and when", not enough to bury that answer.
_MAX_LINES = 400


def _target(value: object) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(value, str) or not value.strip():
        return None, "Give a domain or IP address, e.g. domain='example.com'."
    text = value.strip().lower()
    if not _NAME.match(text):
        return None, (f"{text!r} is not something whois can look up. Give a "
                      f"domain like 'example.com' or an IP address.")
    return text, None


def _fields(lines: List[str]) -> List[str]:
    """Pull the interesting fields out, keeping the server's own labels."""
    out: List[str] = []
    seen = set()
    for label, keys in _FIELDS:
        for line in lines:
            low = line.lower()
            if not any(k in low for k in keys):
                continue
            if ":" not in line:
                continue
            value = line.split(":", 1)[1].strip()
            if not value or value.lower() in seen:
                continue
            seen.add(value.lower())
            out.append(f"  {label:<12} {value}")
            break
    return out


def _run(arguments: dict) -> str:
    target, problem = _target(arguments.get("domain"))
    if problem:
        return problem

    if shutil.which(_WHOIS) is None:
        return files.tool_missing(_WHOIS, f"look up {target}")

    if egress.privacy_mode_enabled():
        return (
            f"Privacy mode is on, so I did not look {target} up. A whois query "
            f"asks a registry about this domain or address and can return the "
            f"person or organisation that registered it. Turn privacy mode off "
            f"to allow it."
        )

    try:
        done = subprocess.run([_WHOIS, target], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (f"The whois lookup of {target} did not finish within "
                f"{_TIMEOUT} seconds, so nothing is reported. No answer is not "
                f"the same as 'no such domain'.")
    except OSError as exc:
        return f"Could not run whois: {exc}."

    text = (done.stdout or "") + "\n" + (done.stderr or "")
    low = text.lower()

    if any(marker in low for marker in _LIMITED):
        detail = [ln.strip() for ln in text.splitlines() if ln.strip()][:3]
        return (
            f"Not done: the whois server refused the query rather than "
            f"answering it"
            + (f" ({detail[0][:120]})" if detail else "")
            + ". Public whois servers rate-limit by address, and that refusal is "
              "NOT evidence that the domain is unregistered - it means nobody "
              "was asked. Try again later, or query the registry directly."
        )

    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return (f"The whois server answered with nothing for {target}. An empty "
                f"answer is not the same as 'not registered' - the server may "
                f"have refused without saying so.")

    not_found = any(m in low for m in (
        "no match", "not found", "no entries found", "no data found",
        "domain not found", "no object found", "status: free", "status: available",
    ))
    fields = _fields(lines)
    if not_found and not fields:
        return (f"No registration found for {target}. Every whois server "
                f"redacts or omits some data, so if you expected a domain here, "
                f"the registry may simply not publish it.")

    shown = lines[:_MAX_LINES]
    out = [f"Whois for {target}. This is what the registry published, which is "
           f"best-effort: registries redact, and the format predates most of "
           f"the web."]
    if fields:
        out.append("")
        out.extend(fields)
    else:
        out.append("")
        out.append("  (no standard fields were recognised in this response)")
    out.append("")
    out.append("Raw response, as the server sent it:")
    out.extend("  " + ln for ln in shown)
    if len(lines) > len(shown):
        out.append(f"  ... {len(lines) - len(shown)} more line(s) not shown.")

    egress.record(f"skill:whois_lookup", target, method="whois",
                  privacy_mode=False)
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "whois_lookup",
        "description": (
            "Look up who a domain or IP address is registered to: the registrar, "
            "when it was created, when it expires, its status and its name "
            "servers. Obeys privacy mode, because the answer can name a person. "
            "Tells a registry's rate-limit refusal apart from 'this domain is "
            "not registered', which are very different facts. Read-only, though "
            "it necessarily contacts a registry outside this machine."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "The domain or IP address, e.g. 'example.com' or '1.1.1.1'.",
                },
            },
        },
    },
}


SKILLS = [Skill(name="whois_lookup", schema=SCHEMA, run=_run)]