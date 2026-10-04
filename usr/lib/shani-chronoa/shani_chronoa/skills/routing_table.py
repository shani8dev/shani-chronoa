"""Skill: the routing table - which way traffic leaves this machine.

This is the "Netstat -> Routing table" view of `gnome-nettool` (Arch `extra`,
42.0-4), which shells out to `netstat -rn -A inet` (Arch `net-tools`). Nothing
else in this package could answer it: `my_ip_address` gives addresses, the
`network` *sense* gives whether a link is up, and `trace_route` gives the path
to one far host. None of them say which interface a connection would leave by.

**Read with `ip -j route`, not by running `netstat`.** `netstat` is deprecated
on Linux precisely because `iproute2` replaced it, and hand-decoding
`/proc/net/route` was tried first and was wrong twice - see below. `ip -j` is
also already a dependency of this project (`my_ip_address`, `check_internet`,
`scan_network` all call it), so nothing new is required for it to be present.

**Both of the hand-decoded traps, kept because both shipped and both produced a
plausible wrong answer rather than an error:**

- **`/proc/net/route` stores each address as a reversed host-order hex word.**
  `011FA8C0` is 192.168.31.1. And the *mask* column is a netmask, not a prefix
  length, so decoding it with the same "is it zero, say unspecified" rule that
  the destination needs is what **silently dropped the IPv4 default route**:
  the default route's mask is `00000000`, the decoder returned the string
  `unspecified` for it, the netmask conversion raised, and the row was skipped.
  The reply then said "no default route" on a machine that had one.
- **`/proc/net/ipv6_route` does *not* reverse bytes the way `/proc/net/route`
  does** - it stores addresses in network order already. Applying the v4 rule to
  both turns a good link-local next hop into a different plausible address.
  Worse, that file merges the `local` and `multicast` tables into the same list
  with no table column, so the reply filled up with `::/0 dev lo` and six
  `ff00::/8` rows that `ip route` reports as being in *other* tables. Reading it
  at all means decoding an undocumented flags column, which is a worse trade
  than using the tool that already knows.

So `/proc/net/route` remains as an IPv4-only fallback for an image without
`iproute2`, and when it is used the reply says so - because it cannot report
IPv6, `linkdown`, route protocol or scope, and those are exactly the fields
that make the difference between "a route exists" and "a route that works".

Honesty rules:

- **The routing table is not the route traffic actually took.** Policy routing,
  custom tables and per-UID tables can send a given packet somewhere this table
  does not show. The reply names the table it read.
- **IPv4 and IPv6 are separate stacks.** A default route in one says nothing
  about the other, and v4-without-v6 is ordinary rather than broken.
- **A metric is a tie-breaker, not a priority.** Two default routes with
  different metrics both exist and the lower one wins; showing only the winner
  would hide the fallback that is there on purpose.
- **`linkdown` means the interface is administratively down**, so its routes
  exist and carry no traffic. That is the single most useful word in this
  output and it is why `/proc` was not enough.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_PROC_ROUTE = Path("/proc/net/route")

_RTF_UP = 0x0001
_RTF_GATEWAY = 0x0002

_TIMEOUT = 10
_MAX_ROWS = 40

SCHEMA = {
    "type": "function",
    "function": {
        "name": "routing_table",
        "description": (
            "Show this machine's routing table: which interface traffic leaves "
            "by, which router forwards it, and for IPv4 and IPv6 separately. "
            "Read-only, and changes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "family": {
                    "type": "string",
                    "description": "'ipv4', 'ipv6', or 'both' (default).",
                },
                "interface": {
                    "type": "string",
                    "description": "Only routes through this interface, e.g. 'wlan0'.",
                },
            },
        },
    },
}


class Route:
    """One main-table route, in the form a person reads it."""

    __slots__ = ("family", "destination", "gateway", "device", "metric", "down", "protocol")

    def __init__(self, family, destination, gateway, device, metric, down, protocol):
        self.family = family
        self.destination = destination
        self.gateway = gateway
        self.device = device
        self.metric = metric
        self.down = down
        self.protocol = protocol

    @property
    def is_default(self) -> bool:
        return self.destination in ("default", "0.0.0.0/0", "::/0")

    @property
    def prefix(self) -> int:
        if self.is_default:
            return 0
        try:
            return ipaddress.ip_network(self.destination, strict=False).prefixlen
        except ValueError:
            return 0

    @property
    def sort_key(self):
        return (-self.prefix, self.metric, self.device)


def _via_ip(family: str) -> list:
    """Routes from `ip -j route`, which is authoritative about its own tables."""
    flag = "-6" if family == "ipv6" else "-4"
    try:
        proc = subprocess.run(["ip", "-j", flag, "route", "show"],
                              capture_output=True, text=True, timeout=_TIMEOUT,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return [None, "ip could not be run."]
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return [None, "ip failed" + (f": {detail[-1]}" if detail else ".")]
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        return [None, "ip printed output that is not JSON."]
    routes = []
    for row in rows if isinstance(rows, list) else []:
        flags = row.get("flags") or []
        routes.append(Route(
            family=family,
            destination=str(row.get("dst") or "default"),
            gateway=str(row.get("gateway") or ""),
            device=str(row.get("dev") or "(none)"),
            metric=int(row["metric"]) if str(row.get("metric", "")).isdigit() else 0,
            down="linkdown" in flags,
            protocol=str(row.get("protocol") or ""),
        ))
    return [routes, None]


def _decode_word(raw: str) -> str:
    """`011FA8C0` -> `192.168.31.1`, reversing the host-order hex word."""
    raw = raw.strip()
    if len(raw) != 8 or not all(c in "0123456789abcdefABCDEF" for c in raw):
        return ""
    try:
        return str(ipaddress.IPv4Address(bytes.fromhex(raw)[::-1]))
    except ValueError:
        return ""


def _via_proc_v4() -> tuple:
    """IPv4 routes straight out of `/proc/net/route`. IPv4 only, and lossy.

    Two things are deliberately *not* done here, because doing them is what
    dropped the default route: the all-zero destination is printed as
    `0.0.0.0` rather than being routed through the "is this unspecified"
    branch, and the mask is decoded as a plain address with no such branch at
    all, so `00000000` becomes a valid `/0` instead of the word "unspecified".
    """
    try:
        text = _PROC_ROUTE.read_text()
    except OSError as exc:
        return [None, f"{_PROC_ROUTE} could not be read ({exc.strerror or exc})."]
    routes = []
    for index, line in enumerate(text.splitlines()):
        if index == 0 or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        device, destination, gateway, flags, metric, mask = (
            parts[0], parts[1], parts[2], parts[3], parts[6], parts[7])
        text_address = _decode_word(destination) or "0.0.0.0"
        text_mask = _decode_word(mask) or "0.0.0.0"
        try:
            network = ipaddress.IPv4Network((text_address, text_mask), strict=False)
            flag_value = int(flags, 16)
        except ValueError:
            continue
        routes.append(Route(
            family="ipv4",
            destination=str(network),
            gateway=_decode_word(gateway) if flag_value & _RTF_GATEWAY else "",
            device=device,
            metric=int(metric) if metric.isdigit() else 0,
            down=not bool(flag_value & _RTF_UP),
            protocol="",
        ))
    return [routes, None]


def _format(route: Route) -> str:
    if route.is_default:
        where = f"via {route.gateway}" if route.gateway else "directly connected"
    else:
        where = f"via {route.gateway}" if route.gateway else "directly connected"
    bits = [f"dev {route.device}"]
    if route.metric:
        bits.append(f"metric {route.metric}")
    if route.protocol:
        bits.append(f"from {route.protocol}")
    if route.down:
        bits.append("LINK DOWN - carries no traffic")
    return f"  {route.destination:<22} {where:<30} " + " ".join(bits)


def _render(family: str, routes: list, wanted: str, source: str) -> list:
    out = [f"{family.upper()} routing table (main) - read with {source}:"]
    rows = [r for r in routes if not wanted or r.device == wanted]
    if not rows:
        out.append("  no routes"
                   + (f" through interface {wanted!r}." if wanted else ".")
                   + (" Nothing in this table sends traffic anywhere."
                      if not wanted else ""))
        return out
    shown = sorted(rows, key=lambda r: r.sort_key)[:_MAX_ROWS]
    defaults = [r for r in shown if r.is_default]
    if defaults:
        out.append(" default route" + ("s" if len(defaults) > 1 else "") + ":")
        out.extend(_format(r) for r in defaults)
        others = [r for r in shown if not r.is_default]
        if not others:
            return out
    elif wanted:
        # Filtering by interface is what removed the default route, not its
        # absence. Saying "NO default route" here would blame the table for
        # a filter the caller asked for.
        out.append(f" no default route through {wanted!r}. That is this "
                   f"filter, not a gap in the machine's routing.")
        others = shown
    else:
        out.append(" NO default route - anything not matched below has nowhere to go.")
        others = shown

    out.append(" specific routes:")
    out.extend(_format(r) for r in others)
    if len(rows) > len(shown):
        out.append(f"   ... {len(rows) - len(shown)} more not shown (limit {_MAX_ROWS}).")
    if any(r.down for r in shown):
        out.append("  A LINK DOWN route is installed but its interface is not "
                   "carrying traffic, so it is not a working route.")
    return out


def _run(arguments: dict) -> str:
    family = str(arguments.get("family") or "both").lower()
    if family not in ("ipv4", "ipv6", "both"):
        return f"Family must be 'ipv4', 'ipv6' or 'both', not {family!r}."
    wanted = str(arguments.get("interface") or "").strip()

    have_ip = shutil.which("ip") is not None
    out = []
    for name in ("ipv4", "ipv6"):
        if family not in ("both", name):
            continue
        if have_ip:
            routes, problem = _via_ip(name)
            source = "ip -j route"
        elif name == "ipv4":
            routes, problem = _via_proc_v4()
            source = _PROC_ROUTE.name
        else:
            out.append("IPV6 routing table: not read. This machine has no `ip` "
                       "(iproute2), and /proc/net/ipv6_route merges the local and "
                       "multicast tables into the main list with no way to tell "
                       "them apart, so reporting it would be reporting routes that "
                       "are not in the main table at all.")
            continue
        if problem or routes is None:
            out.append(f"{name.upper()} routing table: {problem} Nothing was read.")
            continue
        out.extend(_render(name, routes, wanted, source))

    if have_ip:
        out.append(" Main table only: policy routing (ip rule) or a custom table "
                   "can send a given packet somewhere else, so this is where "
                   "traffic goes by default, not a trace of any one connection.")
    else:
        out.append(f" Read from {_PROC_ROUTE.name} because `ip` (iproute2) is not "
                   f"installed, which means: IPv4 only, and no link state, route "
                   f"protocol or scope. Install iproute2 for the full table.")
    return "\n".join(out)


SKILLS = [Skill(name="routing_table", schema=SCHEMA, run=_run)]
