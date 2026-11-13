"""Skill: can I reach that host and port?

Nothing asked. `ping_host` says whether a machine answers ICMP, `dns_lookup`
resolves a name, and `check_internet` answers "is the network up" - so
"Is my server reachable?", "why can nothing connect to it?" and "is port 443
open on that box?" had no answer at all, and they are among the most common
questions anyone asks a machine that holds a service.

It opens one TCP connection (Python's `socket`) and closes it without
sending anything. It used to run `nc -z` (openbsd-netcat) for that, which
needed a package for what the standard library does, and told less: see below.

**It is a probe of *this* machine's path to a host, not a scan.** One address,
one port, one connection, and nothing is sent down it. It is the same shape as
`ping_host` and `scan_network` is deliberately not: this opens exactly the one
socket the person asked about.

**Why the connect is ours, measured with `nc -z`:** a closed port gave
rc=1 and an EMPTY stderr, the same rc as a timeout - so "connection refused",
the phrase that tells a person the host is *up* and the *port* is the
problem, was not in its output at all. The connect's own error says it:

- **refused** - something is there and said no. The host is up, the port is
  not listening. This is a different fault from a host that is off.
- **timed out** - the packets went nowhere. Usually a firewall, or a host that
  is down.
- **unreachable / no route** - no network path at all.

"""

from __future__ import annotations

import socket

from shani_chronoa.skills import Skill



def _why_closed(host: str, port: int) -> str:
    """A refused/timeout/unreachable verdict, from a real connect.

    "" when it connected: the port is open.
    """
    try:
        with socket.create_connection((host, port), timeout=5):
            return ""
    except ConnectionRefusedError:
        return ("refused - the host answered and said no. The machine is up "
                "and nothing is listening on that port")
    except socket.timeout:
        return ("timed out - the packets went nowhere. Usually a firewall "
                "dropping them, or a host that is off")
    except socket.gaierror as exc:
        return f"the name could not be resolved ({exc.strerror or exc})"
    except OSError as exc:
        return f"unreachable ({exc.strerror or exc})"


def _run(arguments: dict) -> str:
    host = str(arguments.get("host") or "").strip()
    raw_port = str(arguments.get("port") or "").strip()
    if not host or not raw_port:
        return ("I need a host and a port. Ask me about one address and one "
                "port, like 'is my server on port 8080'.")
    try:
        port = int(raw_port)
    except ValueError:
        return f"{raw_port!r} is not a port number."
    if not 1 <= port <= 65535:
        return f"{port} is not a port a machine can listen on (1-65535)."

    # Name resolution first: an unresolvable name is a different answer from an
    # unreachable host.
    try:
        address = socket.gethostbyname(host)
    except socket.gaierror as exc:
        return (f"I cannot find the address for {host} ({exc.strerror or exc}). "
                "That is a name problem, not a port problem.")

    # One connection of our own: it is both the check and its reason (nc,
    # which this used to run, said nothing at all for a closed port - and
    # needed a package for what the standard library does).
    reason = _why_closed(host, port)
    if not reason:
        return (f"{host} ({address}) is accepting connections on port {port}. "
                "Something is listening there.")

    answer = f"{host} ({address}) did NOT accept a connection on port {port}: {reason}."
    if "refused" in reason:
        answer += ("\n\nThat is usually the good news: the host is up and the "
                   "service on it is not running, or is running on a "
                   "different port.")
    return answer


SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_port",
        "description": (
            "Check whether a specific port on a specific host accepts "
            "connections from this machine, and say why it does not - refused "
            "(the host is up, nothing is listening), timed out (a firewall, "
            "or a host that is off), or the name does not resolve. Use for "
            "'is my server reachable', 'why can nothing connect to it', 'is "
            "port 443 open on that box'. Opens exactly one connection and "
            "sends nothing; it is not a scan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string",
                         "description": "Hostname or IP address to connect to."},
                "port": {"type": "integer",
                         "description": "The TCP port number, 1-65535."},
            },
            "required": ["host", "port"],
        },
    },
}

SKILLS = [Skill(name="check_port", schema=SCHEMA, run=_run)]