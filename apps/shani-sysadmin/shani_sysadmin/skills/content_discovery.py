"""Skill: what paths does my own web server answer on?

"Which endpoints does this server expose?" is the question gobuster answers
by trying a wordlist of paths and reporting the ones that exist. It is a
legitimate tool for testing a server you run - and a fuzzing tool for
servers you do not, so it is gated exactly like `scan_network`: behind the
`network` sense consent, and restricted to private, link-local or loopback
targets. A tool that will fuzz any URL it is handed is a tool that will
fuzz the internet on a request an LLM was talked into making from a
harmless-sounding question; the range rule is the same one
`scan_network` applies to its nmap range, applied to a URL's host.

**One mode, one wordlist, bounded.** `gobuster dir` against the URL the
person names, with their wordlist (a small built-in list would be a guess
about which server this is; the person supplies the list, as they would on
the command line). Output is the paths that exist, capped, because a
wordlist can be enormous and the answer is the paths, not the attempt log.

**What this is not.** It is not a port scanner (that is `scan_network`'s
refusal, and still belongs in its own gated skill), not vulnerability
scanning, and not credentials testing. It enumerates paths on a server the
person named, on a network they consented to have scanned.
"""

from __future__ import annotations

import ipaddress
import shutil
import subprocess
from urllib.parse import urlparse

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 120
_MAX_ROWS = 30


def _network_allowed() -> "tuple[bool, str]":
    """The `network` sense gate, in scan_network's shape."""
    config = ChronoaConfig()
    if config.sense_allowed("network"):
        return True, ""
    return False, config.sense_allowed_reason("network")


def _is_private_target(host: str) -> bool:
    """Whether `host` is a private, link-local or loopback target."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name: refuse unless it is clearly local. Resolving it here would
        # send the name to a DNS server (the disclosure scan_network avoids),
        # so names that are not obviously local are not resolved at all.
        return host in ("localhost",) or host.endswith((".local", ".lan", ".home", ".internal"))
    return (address.is_private or address.is_loopback
            or address.is_link_local or address.is_reserved)


def _run(arguments: dict) -> str:
    allowed, reason = _network_allowed()
    if not allowed:
        return f"Refusing to enumerate paths: the network sense is off ({reason})."

    url = str(arguments.get("url") or "").strip()
    if not url:
        return "I need the address of the server to check (its own, not any server)."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "I only check http and https addresses."
    host = parsed.hostname or ""
    if not host:
        return f"{url!r} has no host, so there is no server to check."
    if not _is_private_target(host):
        return (f"{host} is not a private, loopback or local address, so I "
                f"will not enumerate its paths - content discovery against "
                f"a public server is not something I do. Point me at a "
                f"server on your own network.")

    wordlist = str(arguments.get("wordlist") or "").strip()
    if not wordlist:
        return ("I need a wordlist of paths to try - the file path on this "
                "machine (e.g. /usr/share/wordlists/common.txt). I do not "
                "ship one, because which list is right depends on the server.")

    if shutil.which("gobuster") is None:
        return files.tool_missing("gobuster", "enumerate paths on a web server")

    from pathlib import Path
    if not Path(wordlist).is_file():
        return f"The wordlist {wordlist!r} is not a file on this machine."

    try:
        proc = subprocess.run(
            ["gobuster", "dir", "-u", url, "-w", wordlist, "-q"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (f"Enumerating {url} did not finish within {_TIMEOUT}s, so "
                f"no complete result is reported.")
    except OSError as exc:
        return f"Not done: gobuster could not be run ({exc})."

    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if proc.returncode != 0 and not lines:
        detail = (proc.stderr or "").strip().splitlines()
        return (f"Not done: gobuster said: "
                + (detail[-1] if detail else f"exit {proc.returncode}"))
    if not lines:
        return (f"No paths from {wordlist} answered on {url}. That is "
                f"gobuster's answer for that list - the server may have "
                f"none of them, or may refuse the requests.")
    shown = lines[:_MAX_ROWS]
    out = [f"{len(lines)} path(s) answered on {url} from {wordlist}:", ""]
    out.extend(shown)
    if len(lines) > len(shown):
        out.append(f"... {len(lines) - len(shown)} more path(s) not shown.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "content_discovery",
        "description": (
            "Enumerate which paths exist on a web server on the local "
            "network, using gobuster with a wordlist the person supplies. "
            "Use for 'what endpoints does my server expose', 'is /admin "
            "public on my site'. Private, loopback and local targets only, "
            "behind the network consent. Reports the paths that answered; "
            "changes nothing on the server."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The server's address, e.g. http://192.168.1.10.",
                },
                "wordlist": {
                    "type": "string",
                    "description": "Path to the wordlist file on this machine.",
                },
            },
            "required": ["url", "wordlist"],
        },
    },
}

SKILLS = [Skill(name="content_discovery", schema=SCHEMA, run=_run)]
