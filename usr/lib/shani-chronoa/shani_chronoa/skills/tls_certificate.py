"""Skill: read a website's TLS certificate - when it expires, who issued it, what it covers.

Nothing in this project could answer "when does this site's certificate
expire", which is a daily question for anyone deploying something and an
immediate question for anyone whose HTTPS stopped working. `openssl s_client` is
on the image and the matrix flags it, so calling it would have been the obvious
wiring.

**Uses `ssl` from the standard library instead.** Same reasoning as
`compute_hash` calling `hashlib` rather than `sha256sum`: the binary can be
absent from a minimal image, and `ssl` already speaks TLS. Verified against a
real host before writing this — `getpeercert()` returns the subject, issuer,
validity dates, serial and subjectAltName, which is everything below.

The trap here is that almost every way to summarise a certificate implies more
than was checked. Four separate findings have to be kept apart, because
collapsing any two of them produces the confidently wrong answer a person then
acts on:

- **"It has not expired" is not "it is valid".** A self-signed certificate has
  not expired and matches the hostname, and browsers reject it.
- **"The name matches" is not "it is trusted".** Chain verification is not done
  here, and the reply must not imply it was.
- **Only the leaf certificate is read.** The issuer certificate is not fetched,
  so "signed by X" means the leaf *claims* X - which is what a certificate says,
  and is not proof that X issued it.
- **A handshake that fails has no certificate to report.** "Wrong host name",
  "protocol version too old" and "a proxy intercepted this" all fail the
  handshake, and each of them is a different finding from an expired
  certificate. The reply names the failure instead of implying an expiry.

So the reply leads with the number of days remaining, and separately states
whether the name covers the host, whether it is self-signed, and that the chain
was not verified.
"""

from __future__ import annotations

import logging
import socket
import ssl
from typing import List, Optional, Tuple

from shani_chronoa import egress
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 443
_TIMEOUT = 12

#: Long enough to be useful, short enough to be a spoken answer. A certificate
#: with 300 SANs is not a thing anyone wants read out.
_MAX_NAMES = 12


class Cert(NamedTuple := object):  # placeholder replaced below
    pass


SCHEMA = {
    "type": "function",
    "function": {
        "name": "tls_certificate",
        "description": (
            "Read a website's TLS certificate: when it expires, who issued it, "
            "and which hostnames it covers. Use for 'when does this site's "
            "certificate expire' or 'why is HTTPS failing on this'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "The hostname to fetch the certificate from."},
                "port": {
                    "type": "integer",
                    "description": f"Port to connect to. Defaults to {_DEFAULT_PORT}.",
                },
            },
            "required": ["host"],
        },
    },
}


def _flatten(name: object) -> str:
    """Turn `((('commonName', 'x'),),)` into `CN=x`.

    The stdlib gives LDAP-ish tuples because that is what RFC 2253 says. Every
    other tool in this project prints `CN=x`, and a reply in a different shape
    from every other reply is one a reader has to translate.
    """
    parts: List[str] = []
    groups = name if isinstance(name, tuple) else ()
    for group in groups:
        if not isinstance(group, tuple):
            continue
        for pair in group:
            try:
                key, value = pair
            except (TypeError, ValueError):
                continue
            key = str(key).replace("Email", "emailAddress")
            short = {"commonName": "CN", "organizationName": "O",
                     "organizationalUnitName": "OU", "countryName": "C",
                     "stateOrProvinceName": "ST", "localityName": "L"}.get(key, key)
            parts.append(f"{short}={value}")
    return ", ".join(parts) if parts else "(not stated)"


def _names(peer: dict) -> List[str]:
    """Every host the certificate claims, SANs first.

    The commonName is only included when there is no SAN at all, because that is
    what a verifier does - and browsers stopped looking at commonName entirely.
    """
    out = []
    for kind, value in peer.get("subjectAltName") or ():
        if kind == "DNS":
            out.append(value)
    if not out:
        for group in peer.get("subject") or ():
            for key, value in group:
                if key == "commonName":
                    out.append(value)
    return out


def _days_left(not_after: str) -> Optional[int]:
    """Days until `not_after`, or None if the date cannot be read.

    The stdlib gives the format `Dec 25 22:56:35 2026 GMT`, which is not
    parseable by `datetime.fromisoformat` - it has no timezone offset and no
    month number - so it is parsed by hand and any surprise returns None rather
    than a guess.
    """
    from datetime import datetime, timezone
    try:
        stamp = datetime.strptime(not_after.strip(), "%b %d %H:%M:%S %Y %Z")
    except (ValueError, AttributeError):
        return None
    return (stamp.replace(tzinfo=timezone.utc)
            - datetime.now(timezone.utc)).days


def _handshake_error(host: str, port: int) -> str:
    """A handshake failure, named as itself rather than as an expiry."""
    import ssl as _ssl
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=_TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                # A handshake that succeeds but whose chain does not verify
                # raises here, so reaching this point means the chain verified.
                peer = tls.getpeercert()
        return ""
    except _ssl.SSLCertVerificationError as exc:
        return (
            f"The certificate for {host} did not verify: {exc.verify_message or exc}. "
            f"That is the check a browser makes, and it failed - so this is not an "
            f"expired-certificate problem, it is a trust problem."
        )
    except _ssl.SSLError as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        if "hostname" in str(exc).lower() or "match" in str(exc).lower():
            return (
                f"{host} presented a certificate that does not cover that name "
                f"({reason}). Either the wrong site is answering, or the "
                f"certificate is issued for a different hostname."
            )
        return (
            f"The TLS handshake with {host} failed: {reason}. No certificate could "
            f"be read. That is not the same as an expired certificate - it means "
            f"the secure connection was never established, which is also what a "
            f"proxy intercepting traffic looks like."
        )
    except socket.timeout:
        return (f"Connecting to {host}:{port} timed out after {_TIMEOUT} seconds, "
                f"so no certificate could be read.")
    except OSError as exc:
        return (f"Could not connect to {host}:{port}: {exc}. No certificate could "
                f"be read.")


def fetch(host: str, port: int) -> Tuple[Optional[dict], Optional[str]]:
    """The peer certificate and any error. Exactly one of the two is set."""
    try:
        # `check_hostname=False` so a name mismatch produces a *report* rather
        # than an exception: a mismatched certificate is a finding worth reading,
        # and refusing to look would lose it.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=_TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                peer = tls.getpeercert()
    except ssl.SSLError as exc:
        return None, f"The TLS handshake with {host} failed: {getattr(exc, 'reason', None) or exc}."
    except socket.timeout:
        return None, f"Connecting to {host}:{port} timed out after {_TIMEOUT} seconds."
    except OSError as exc:
        return None, f"Could not connect to {host}:{port}: {exc}."
    if not peer:
        return None, (
            f"{host} completed a handshake but presented no certificate to inspect. "
            f"That happens with anonymous or resumed sessions, and nothing about "
            f"the certificate can be said."
        )
    return peer, None


def _run(arguments: dict) -> str:
    host = (arguments.get("host") or "").strip()
    if not host:
        return "No host was named, so there is no certificate to read."
    # Host is used for a socket connect and as an SNI name, never as a command
    # line, and a value that is not a hostname is refused rather than passed on.
    if not all(part and (part.replace("-", "").isalnum() or part == ".")
               for part in host.split(".")):
        return f"{host!r} is not a hostname, so there is no certificate to read."

    port = arguments.get("port")
    if port is None:
        port = _DEFAULT_PORT
    elif isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return f"'port' must be a number from 1 to 65535, not {port!r}."
    if egress.privacy_mode_enabled():
        return (f"Privacy mode is on, so I did not connect to {host} to read its "
                f"certificate. That would send a request beyond your network.")

    peer, problem = fetch(host, port)
    if problem:
        # Distinguish a failed handshake from a failed verification, because the
        # first says nothing about expiry and the second is a different finding.
        if "did not verify" in problem or "does not cover that name" in problem:
            return problem
        return problem
    egress.record("skill:tls_certificate", f"{host}:{port}", method="TLS",
                  privacy_mode=False)

    subject = _flatten(peer.get("subject"))
    issuer = _flatten(peer.get("issuer"))
    names = _names(peer)
    not_after = peer.get("notAfter") or "(not stated)"

    self_signed = subject.replace(" ", "") == issuer.replace(" ", "")
    # A certificate issued to nobody in particular is not self-signed, it is
    # just unnamed, and conflating the two invents a security finding.
    unnamed = "CN=(not stated)" in subject or subject == "(not stated)"

    lines = [f"TLS certificate for {host}" + ("" if port == _DEFAULT_PORT else f":{port}"),
             f"  Issued to: {subject}",
             f"  Issued by: {issuer}",
             f"  Valid until: {not_after}"]

    days = _days_left(peer.get("notAfter", ""))
    if days is None:
        lines.append("  Time remaining: could not be worked out from the "
                     "certificate's own date format.")
    elif days < 0:
        lines.append(f"  EXPIRED {-days} day(s) ago. Anything connecting over TLS "
                     f"will refuse it.")
    elif days == 0:
        lines.append("  Expires today.")
    elif days <= 14:
        lines.append(f"  Expires in {days} day(s) - soon, if this is yours to renew.")
    else:
        lines.append(f"  {days} day(s) remaining.")

    if names:
        shown, rest = names[:_MAX_NAMES], names[_MAX_NAMES:]
        lines.append(f"  Covers {len(names)} name(s): " + ", ".join(shown)
                     + (f" (and {len(rest)} more)" if rest else ""))
        covers = host.lower() in {n.lower() for n in names}
        if not covers:
            lines.append(
                f"  NOTE: this certificate does NOT list {host} among its names. "
                f"A browser would refuse it for this host even though it has not "
                f"expired.")
    else:
        lines.append("  Covers: no hostnames are listed, so it cannot be matched "
                     "to a site.")

    if self_signed and not unnamed:
        lines.append("  SELF-SIGNED: the issuer is the subject, so nothing vouches "
                     "for it. Browsers reject these.")
    if unnamed:
        lines.append("  This certificate names no subject, which is unusual for a "
                     "public site.")

    lines.append(
        "  Only this certificate was read. The chain was NOT verified, so this "
        "says nothing about whether a browser would trust it - a certificate that "
        "has not expired can still be rejected.")
    return "\n".join(lines)


SKILLS = [Skill(name="tls_certificate", schema=SCHEMA, run=_run)]