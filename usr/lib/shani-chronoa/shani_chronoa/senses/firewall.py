"""Sense: whether a firewall is actually enforcing anything.

`security` covers Secure Boot, firmware signing and which processes can escalate.
It does not answer the question most people mean by "is this machine protected",
which is whether packets are being filtered at all.

That question has more wrong answers than it looks. The trap is that a firewall
being *installed* is not the same as it being *on*: `ufw` ships present and
inactive on a great many systems, `firewalld` runs on a different one, and a
machine can have all three tools absent and still be behind a router's NAT, which
is not a firewall and protects nothing from anything on the same network. A sense
that answered "installed" would be right often enough to be trusted and wrong
exactly when it mattered.

So every backend is reported separately and each one distinguishes *present*,
*active* and *could not be determined* - the same three-state rule the security
sense uses for Secure Boot, for the same reason.

Honesty rules:

- **No firewall tool is UNKNOWN, never "unprotected."** A machine with no
  `ufw` binary is not a machine with no filtering; it is a machine whose filtering
  this sense could not inspect.
- A backend that is installed but inactive is reported as inactive, and the
  default policy is quoted where it can be read, because "inactive" and
  "active but default allow" are different levels of not-protected.
- Being behind a NAT is not reported as protection. It is not.
"""

from __future__ import annotations

import logging
import shutil
from typing import List, Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import subproc

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0
_TIMEOUT = 20


def _run_cmd(argv, env=None):
    """This module's seam over `subproc.run` (tests replace it), with the module's timeout."""
    return subproc.run(argv, timeout=_TIMEOUT, env=env)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "firewall",
        "description": (
            "Report whether packet filtering is actually active, checking ufw, "
            "firewalld and the nftables/iptables ruleset, and distinguishing a "
            "firewall that is installed from one that is enforcing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def ufw_state() -> Optional[dict]:
    """ufw's own view, or None when ufw cannot answer."""
    if shutil.which("ufw") is None:
        return None
    proc = _run_cmd(["ufw", "status"])
    if proc is None or proc.returncode != 0:
        # ufw needs root even to report. Say so rather than "no firewall".
        return {"present": True, "active": None,
                "detail": "ufw is installed but its status needs root, so its "
                          "state is UNKNOWN from an unprivileged account"}
    text = proc.stdout
    active = "Status: active" in text
    return {"present": True, "active": active,
            "detail": text.strip().splitlines()[0] if text.strip() else ""}


def firewalld_state() -> Optional[dict]:
    if shutil.which("firewall-cmd") is None:
        return None
    proc = _run_cmd(["firewall-cmd", "--state"])
    if proc is None or proc.returncode != 0:
        return {"present": True, "active": None,
                "detail": "firewalld is installed but --state needs root, so its "
                          "state is UNKNOWN from an unprivileged account"}
    running = "running" in proc.stdout
    return {"present": True, "active": running,
            "detail": proc.stdout.strip()}


def nftables_default() -> Optional[str]:
    """The base chain policy from nftables, or None if it cannot be read."""
    if shutil.which("nft") is None:
        return None
    proc = _run_cmd(["nft", "list", "ruleset"])
    if proc is None or proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if "policy" in line:
            for token in line.split():
                if token in ("accept", "drop", "reject"):
                    return token
    return "no base chain policy line found"


def iptables_default() -> Optional[str]:
    if shutil.which("iptables") is None:
        return None
    proc = _run_cmd(["iptables", "-S"])
    if proc is None or proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("-P INPUT"):
            parts = line.split()
            if len(parts) >= 3:
                return parts[2]
    return "no INPUT policy line found"


def _run(arguments: dict) -> Union[str, Percept]:
    ufw = ufw_state()
    fwd = firewalld_state()
    nft = nftables_default()
    ipt = iptables_default()

    backends = [b for b in (ufw, fwd) if b is not None]
    if not backends and nft is None and ipt is None:
        return (
            "Packet filtering is UNKNOWN: neither ufw, firewalld, nft nor "
            "iptables is present, so nothing could be inspected. That is not "
            "the same as a machine with no filtering, and it is definitely not "
            "the same as an unprotected one - check what the network provides."
        )

    lines: List[str] = []
    active_any = False
    unknown_any = False

    if ufw is not None:
        state = ("active" if ufw["active"] else
                 "installed but INACTIVE" if ufw["active"] is False else
                 "state UNKNOWN")
        lines.append(f"  ufw: {state}"
                     + (f" ({ufw['detail']})" if ufw["detail"] else ""))
        active_any = active_any or ufw["active"] is True
        unknown_any = unknown_any or ufw["active"] is None
    else:
        lines.append("  ufw: not installed")

    if fwd is not None:
        state = ("running" if fwd["active"] else
                 "installed but NOT running" if fwd["active"] is False else
                 "state UNKNOWN")
        lines.append(f"  firewalld: {state}")
        active_any = active_any or fwd["active"] is True
        unknown_any = unknown_any or fwd["active"] is None
    else:
        lines.append("  firewalld: not installed")

    if nft is not None:
        lines.append(f"  nftables base chain policy: {nft}"
                     + ("" if nft in ("drop", "reject") else
                        "  <- a base policy of accept filters nothing by default"))
    if ipt is not None:
        lines.append(f"  iptables INPUT policy: {ipt}"
                     + ("" if ipt in ("DROP", "REJECT") else
                        "  <- a default ACCEPT permits everything not explicitly listed"))

    if active_any:
        verdict = "A firewall is active."
    elif unknown_any:
        verdict = ("Whether a firewall is enforcing is UNKNOWN - a tool is "
                   "installed but its state could not be read without root.")
    else:
        verdict = ("No firewall is active. Every backend is either absent or "
                   "inactive, so this machine is not filtering incoming traffic "
                   "itself.")

    verdict += ("\nNote: being behind a router's NAT is not protection. It "
                "blocks unsolicited inbound connections from the internet and "
                "does nothing about other machines on the same network.")
    verdict += "\n" + "\n".join(lines)

    return _SENSE.to_percept(
        verdict,
        source="firewall",
        metadata={
            "ufw": None if ufw is None else ufw["active"],
            "firewalld": None if fwd is None else fwd["active"],
            "nftables_policy": nft,
            "iptables_policy": ipt,
            "any_active": active_any,
            "any_unknown": unknown_any,
        },
    )


_SENSE = Sense(
    name="firewall",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
