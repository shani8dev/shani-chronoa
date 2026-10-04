"""Skill: is this host reachable, and how slow is it - aimed at one named host.

`check_internet` answers "is the internet working" by walking a **fixed** ladder
and takes no arguments, so it cannot be pointed at anything. `trace_route` takes
a host but answers "where does the path break", not "does this answer and how
long does it take". Neither answers the question people actually ask when
something is slow: *ping this, that one is laggy*.

This is the Ping tab of `gnome-nettool` (Arch `extra`, 42.0-4). That app builds
`ping -b [-c N] -n <host>` and reads the same summary line read here. **The `-b`
is not copied.** In iputils `-b` means "allow pinging a broadcast address"; it is
harmless for a unicast host and pointless for one, so it is dropped rather than
carried over for fidelity's sake. `-n` *is* kept: it stops ping resolving each
reply's address back to a name, which is a per-reply DNS query to a third party
that buys nothing here.

Honesty rules, because every one of these is a way to be confidently wrong:

- **100% loss is not "the host is down".** ICMP is filtered by a great many
  hosts and by most cloud firewalls, and a filtered host is up and serving
  perfectly well. The reply says "did not answer" and names the alternatives.
- **Three different failures look identical in the summary line** - 100% loss
  with zero received is all of "no route to host", "filtered", and "wrong
  address". `ping`'s own exit status separates them (2 for a name it cannot
  resolve, 1 for no replies), and that is used rather than guessed at from the
  percentage.
- **A name that does not resolve is not an unreachable host.** Those are
  different faults with different fixes, and DNS is the one worth naming.
- **`-n` means the reply's address is never reverse-resolved**, so an answer
  from a host with a PTR record still reads as a bare address. Nothing here
  claims to know who answered.
- **A refused reply is not an error.** `Destination Port Unreachable` from an
  ICMP-hostile middlebox still counts as the host being reachable in the only
  sense that matters, and is reported as such rather than as loss.
- **The measurement is 56 bytes by default**, which is what ping sends. A path
  that drops large packets behaves differently and this does not test it.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import egress, files
from shani_chronoa.skills import Skill

#: Long enough for a lossy link to say something, short enough to be an answer.
_TIMEOUT = 20
_DEFAULT_COUNT = 5
_MIN_COUNT = 1
_MAX_COUNT = 20

#: `rtt min/avg/max/mdev = 0.029/0.037/0.048/0.007 ms`, and the same line
#: without the `rtt` prefix on a host that answers but never completes a
#: measurement.
_RTT = re.compile(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms")
_LOSS = re.compile(r"(\d+(?:\.\d+)?)% packet loss")
_SENT = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "ping_host",
        "description": (
            "Ping a named host or IP address and report whether it answers, "
            "how long the replies take and how many were lost. Use this when a "
            "specific host is slow or unreachable; 'check_internet' only answers "
            "whether the internet works at all."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Hostname or IP address to ping, e.g. 'archlinux.org' or '1.1.1.1'.",
                },
                "count": {
                    "type": "integer",
                    "description": (
                        f"How many packets to send ({_MIN_COUNT}-{_MAX_COUNT}, "
                        f"default {_DEFAULT_COUNT}). More gives a steadier average "
                        f"and takes proportionally longer."
                    ),
                },
            },
            "required": ["host"],
        },
    },
}


def _valid_host(raw: str) -> str | None:
    """A host that is safe to hand to ping, or None with the reason.

    Refused rather than escaped: this process runs no shell, so the real risk is
    not metacharacters but a value that is not a host at all - a stray `--help`,
    or a whole sentence, which ping would then treat as further options or as a
    set of addresses.
    """
    host = raw.strip()
    if not host:
        return None
    if host.startswith("-"):
        return (
            f"Refusing to ping {host!r}: it starts with a dash, so it would be "
            f"read as an option to ping rather than as a host."
        )
    if any(c.isspace() for c in host):
        return f"Refusing to ping {host!r}: a host is one name, not several words."
    if len(host) > 253:
        return f"Refusing to ping {host!r}: that is longer than any hostname can be."
    return host


def _pick_binary(host: str) -> tuple[str, str | None]:
    """(binary, refusal) - `ping6` for an IPv6 literal, else `ping`."""
    if ":" in host:
        # An IPv6 literal, and `ping` on this image is `iputils` ping, which
        # handles v6 itself; ping6 is the older name and is used when present so
        # either image works.
        for name in ("ping6", "ping"):
            if shutil.which(name):
                return name, None
        return "ping6", files.tool_missing("ping6", "ping an IPv6 address")
    if shutil.which("ping"):
        return "ping", None
    return "ping", files.tool_missing("ping", "ping a host")


def _explain(refused: bool, count: int, received: int) -> str:
    """Why nothing answered, when nothing answered."""
    if refused:
        return (
            "Ping itself refused to send anything. That is a permission or "
            "sysctl problem on this machine (ping normally needs either "
            "capability net_raw or ping_group_range), not a fault at the far "
            "end - the packets never left."
        )
    if count <= 0:
        return (
            "No packet was sent, so nothing can have been lost. The address was "
            "not usable."
        )
    return (
        f"{received} of {count} answered. With no reply at all the cause is one "
        f"of: this host has no route to that address, something in between drops "
        f"or refuses the traffic, the far end filters ICMP, or the address is "
        f"simply not there. ICMP filtering is common - especially on cloud hosts "
        f"and phones - and it is the first thing to rule out, not the last."
    )


def _run(arguments: dict) -> str:
    raw = str(arguments.get("host") or "")
    host = _valid_host(raw)
    if host is None:
        return (
            f"Refusing to ping {raw!r}: a host is one name or address, not "
            f"several words or an option."
        )

    count = arguments.get("count", _DEFAULT_COUNT)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = _DEFAULT_COUNT
    count = max(_MIN_COUNT, min(_MAX_COUNT, count))

    # Privacy mode draws the line at this machine's own network, the same place
    # `check_internet` draws it: a ping to the router is a local measurement, a
    # ping to a public address is a probe leaving the building.
    local = egress.is_on_this_network(f"http://{host}/")
    if egress.privacy_mode_enabled() and not local:
        return (
            f"Privacy mode is on, so I did not ping {host} - that sends packets "
            f"off this network. I can still ping a host on your own network."
        )

    binary, refusal = _pick_binary(host)
    if refusal:
        return refusal

    cmd = [binary, "-c", str(count), "-W", "2", "-n", host]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT + count * 2, check=False)
    except subprocess.TimeoutExpired:
        return (
            f"Ping to {host} did not finish. Whether {host} answers is unknown - "
            f"a partial result is not a slow one."
        )
    except OSError as exc:
        return f"Could not run {binary}: {exc}."

    out = proc.stdout + proc.stderr

    # Exit 2 from iputils is a name or address it could not resolve. That is a
    # DNS fault and is a different thing from an unreachable host, and it is the
    # one case where the percentage loss tells you nothing at all.
    if proc.returncode == 2 or "Name or service not known" in out or "unknown host" in out:
        return (
            f"{host} does not resolve to an address, so there was nothing to "
            f"ping. That is a name problem (this machine's DNS, or the name "
            f"itself), not an unreachable host."
        )
    if "permission denied" in out.lower() or "Operation not permitted" in out:
        return _explain(True, count, 0)

    sent = received = 0
    match = _SENT.search(out)
    if match:
        sent, received = int(match.group(1)), int(match.group(2))
    loss_match = _LOSS.search(out)
    loss = loss_match.group(1) if loss_match else None

    rtt = _RTT.search(out)
    lines = []
    if rtt:
        low, avg, high, jitter = (float(v) for v in rtt.groups())
        lines.append(
            f"{host} answered: {received}/{sent or count} replies, "
            f"min {low:.1f} ms, average {avg:.1f} ms, highest {high:.1f} ms, "
            f"variation {jitter:.1f} ms."
        )
    elif received:
        lines.append(f"{host} answered {received} of {sent or count} times.")

    # A refused reply still proves the host is there, and saying "unreachable"
    # would be wrong.
    refused = "Destination Port Unreachable" in out
    if received == 0:
        return "\n".join(filter(None, [
            f"{host} did not answer: {sent or count} sent, 0 received"
            + (f", {loss}% loss." if loss else "."),
            _explain(refused, sent or count, 0),
        ]))

    if refused:
        lines.append(
            "At least one reply was an ICMP 'destination port unreachable', "
            "which still proves something is there and reachable - it is a "
            "middlebox refusing echo requests rather than the host being down."
        )
    if loss and float(loss) > 0:
        lines.append(
            f"{loss}% of packets were lost. Loss on the path is real even when "
            f"the host answers, and it is what makes a connection feel slow "
            f"rather than merely distant."
        )
    return " ".join(lines)


SKILLS = [Skill(name="ping_host", schema=SCHEMA, run=_run)]
