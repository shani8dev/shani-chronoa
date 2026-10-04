"""Build an isolated lab network out of Linux network namespaces.

`shani_chronoa/skills/network.py` is the surface a model talks to; this module is
the model, and `usr/bin/shani-chronoa-lab-network` is the root helper that executes it.
All three share this file so a plan cannot be validated once and executed under
different rules.

**Everything here is a Linux primitive.** A **network** is one network
namespace holding a **bridge**, with `net.ipv4.ip_forward` on so it routes
between the **veth** pairs attached to it. A **subnet** is its own namespace
with one veth to that bridge, carrying the subnet's gateway address and one
**host** address. A **public** subnet gets a default route out through the
network's uplink; a **private** one gets no default route at all, so it reaches
this network and nothing beyond it.

**The cloud vocabulary is only a mapping, never the implementation.** People
ask for these things in provider words, so the words are understood and then
expressed in `ip`/`nft` terms:

| What people ask for | What this builds |
|---|---|
| VPC / network | a network namespace holding a bridge |
| subnet | a namespace + one veth pair |
| internet gateway | a `/30` point-to-point veth pair to the host |
| NAT gateway | an nftables `masquerade`, scoped to the lab's addresses |
| route table | the kernel route table, in each namespace |
| instance / host | an address in a subnet namespace |
| security group | (not modelled - see the docstring below) |

Note the deliberate differences from the providers. There is no separate NAT
gateway object: the masquerade is one rule inside the network's own namespace
table plus one narrowly scoped rule on the host, both removed on teardown. There
is no route table object either - routes are a property of a namespace, so a
public subnet is a subnet with a default route and nothing else. Overlapping
CIDRs are permitted with a warning rather than refused (OpenStack allows this,
AWS forbids it) because in a local lab the interesting case is two labs that
overlap deliberately, and hiding that behind an error loses the reason.

**No cloud, no credentials, no egress beyond the host's own uplink.** Every
lab address is RFC1918 and every packet stays in kernel namespaces on this
machine. The single masquerade rule is the only thing that can reach the
outside world, and it matches on both the lab source range and the destination
interface, so host traffic is untouched.

**The security boundary is this file, not the helper.** A request arrives from
an LLM, so every field in it is untrusted. It is turned into a list of `argv`
*here*, from components that have passed a regex or been parsed by
`ipaddress` - never by joining strings. The helper executes those argv arrays
and re-validates the plan on load, so a hand-edited plan file cannot smuggle in
a command this module would not have produced. No shell is involved anywhere,
and there is no way to reach one: nothing in this file builds a string that a
shell would parse.

**The rejections are the interesting part**, because each one closes a way to
get a namespace that looks right and is not:

- **Names are `[a-z0-9][a-z0-9-]*`, bounded.** Interface names are capped at 15
  bytes by the kernel, and a name carrying a space, a slash or a quote is the
  classic way to turn a name argument into two arguments. Validating the shape
  before it is ever formatted into a command is the fix, and the bound is what
  forces it.
- **A CIDR must be private or link-local.** A lab network on a public prefix
  cannot route the internet into a namespace, so it produces a network that is
  built, reports success, and cannot do the one thing it was made for. Refusing
  it up front is honest; building it is not.
- **A prefix must be between /16 and /28.** /31 and /32 have no usable host
  address to hand a gateway, and anything longer than /28 is not something a
  person means by a subnet.
- **Subnets must sit inside the network and must not overlap each other.**
  Overlap is refused rather than resolved, because "which one wins" is a routing
  accident, not an answer.
- **A public subnet needs a real uplink to exist.** `nat: true` with no
  `uplink` builds a namespace whose default route has no next hop - it reports
  success and cannot reach anything. Refused at plan time instead.
- **No subnet may claim the uplink's `/30`.** It is taken from the top of the
  network's own CIDR, so a subnet overlapping it is a routing conflict with the
  one link that has to work.
- **Reserved names are refused.** `lo`, and anything starting with `veth`,
  `br-`, `docker`, `virbr`, `tun`, `tap` or `chronoa-`, because those belong to
  something this tool does not own and would be destroyed by a teardown that
  assumed otherwise.
- **A network that already exists is an error, not a repair.** Overwriting a
  live namespace because a name was reused is how you disconnect something real.

**What this deliberately does not model.** Security groups. Every provider
ships them, and every provider's version is a different shape - AWS is stateful
with per-rule ordering, Azure evaluates inbound and outbound in *opposite
directions*, OpenStack's extension is optional and its availability depends on
the enabled mechanism drivers. A local lab has no workload to protect, so a
security group here would be decoration that reads as a control. It is left out
rather than shipped as a rule list that does nothing.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

#: The kernel's `IFNAMSIZ - 1`. Every generated interface name must fit.
IFNAMSIZ = 15

#: 12 + len("br-") == 15. A network name is bounded so its bridge name always fits.
_LAB_NETWORK_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,11}$")
_SUBNET_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,9}$")

#: Prefixes this will build. See the module docstring for why each bound is
#: where it is.
_MIN_PREFIX = 16
_MAX_PREFIX = 28

#: Interfaces belonging to something else. Refused so a teardown that trusts
#: this tool's own record cannot take out a real network.
_RESERVED_PREFIXES = ("docker", "virbr", "veth", "br-", "tun", "tap", "chronoa-", "zt")
_RESERVED_NAMES = frozenset({"lo", "default", "all"})

#: Plans are written 0600 with `O_EXCL`, exactly like `argfile.py`'s envelope:
#: the values are untrusted, so they never reach a command line.
_ENVELOPE_DIRNAME = "lab-network-plans"


class Rejected(Exception):
    """A request this module will not turn into a plan, with the reason."""


class Step(NamedTuple):
    """One command, as an argv array. Never a string."""

    argv: List[str]
    note: str = ""
    optional: bool = False


class Subnet(NamedTuple):
    name: str
    cidr: str
    public: bool
    network: ipaddress.IPv4Network
    gateway: str
    instance: str


class Uplink(NamedTuple):
    """The one point-to-point link between a lab network and the host.

    This is what every provider calls an internet gateway, and it is what makes
    a public subnet able to reach anything: without it there is no next hop for
    a default route, so a "public" subnet would build fine and reach nothing.

    A /30 taken from the top of the lab network's own CIDR. `host_side` stays in
    the root namespace (this is what the host sends to and receives from the
    lab); `lab_side` lives in the lab network's namespace and is enslaved to its
    bridge, so the lab's own routing sees an ordinary link.
    """

    cidr: str
    network: ipaddress.IPv4Network
    host_side: str
    lab_side: str
    device: str


class LabNetwork(NamedTuple):
    name: str
    cidr: str
    #: The parsed network. Named `addr` rather than `network` because the
    #: instance itself is now called a network, and `network.network` was the
    #: self-reference that rename produced - readable only by remembering which
    #: of two meanings applied where.
    addr: ipaddress.IPv4Network
    gateway: str
    subnets: Tuple[Subnet, ...]
    nat: bool
    uplink: str
    link: Optional[Uplink] = None


# ---------------------------------------------------------------- validation


def _check_name(raw: object, pattern: re.Pattern, what: str, ceiling: int) -> str:
    name = str(raw or "").strip()
    if not name:
        raise Rejected(f"No {what} name given.")
    if len(name) > ceiling or not pattern.match(name):
        raise Rejected(
            f"{name!r} is not a usable {what} name. Use lower-case letters, "
            f"digits and dashes, starting with a letter or digit, at most "
            f"{ceiling} characters (it becomes part of a network interface name, "
            f"which the kernel caps at {IFNAMSIZ} bytes)."
        )
    if name in _RESERVED_NAMES or any(name.startswith(p) for p in _RESERVED_PREFIXES):
        raise Rejected(
            f"{name!r} is reserved: it looks like an interface this tool does not "
            f"own (lo, docker*, virbr*, veth*, br-*, tun*, tap*). Refusing it "
            f"rather than risk a teardown taking out a real network."
        )
    return name


def _check_cidr(raw: object, what: str) -> ipaddress.IPv4Network:
    text = str(raw or "").strip()
    if not text:
        raise Rejected(f"No {what} CIDR given.")
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise Rejected(f"{text!r} is not a network: {exc}.") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise Rejected(f"{text!r} is IPv6. This builds IPv4 namespaces only.")
    if not (network.is_private or network.is_link_local):
        raise Rejected(
            f"Refusing {network}: a lab network needs private, link-local or "
            f"loopback space. A public prefix cannot be routed into a namespace, "
            f"so the result would be a network that builds fine and cannot reach "
            f"what it was made for."
        )
    if network.is_loopback:
        raise Rejected(
            f"Refusing {network}: loopback is this machine's own, not a network "
            f"to build on top of."
        )
    if not _MIN_PREFIX <= network.prefixlen <= _MAX_PREFIX:
        raise Rejected(
            f"{network} has a /{network.prefixlen} prefix. This builds prefixes "
            f"from /{_MIN_PREFIX} to /{_MAX_PREFIX}: shorter ones have too many "
            f"addresses to be useful, and /31 and /32 have no host address to "
            f"give a gateway."
        )
    return network


def _hosts(network: ipaddress.IPv4Network, count: int) -> List[str]:
    """The first `count` usable host addresses, or a Rejected if there aren't enough."""
    usable = list(network.hosts())
    if len(usable) < count:
        raise Rejected(
            f"{network} has {len(usable)} usable address(es), which is not enough "
            f"to give a gateway and an instance."
        )
    return [str(a) for a in usable[:count]]


def _subnet_hosts(subnet: ipaddress.IPv4Network, gateway: str,
                  count: int) -> List[str]:
    """A subnet's own gateway and instance addresses, skipping `gateway`.

    **The network's gateway address is skipped, and it has to be.** A network's
    gateway is `_hosts(network, 1)[0]` — the network's *first* host address — and
    a subnet that starts at that same address contains it. So the naive
    `_hosts(subnet, 2)` hands the subnet a gateway that the network's bridge
    already owns, and the plan then puts the same address on both ends of one
    veth pair: `br-<network>` in the network namespace and `vh..n` in the subnet
    namespace. Two namespaces either side of a link answering to one address is
    an ARP fight neither side wins, so a subnet built that way is built, reports
    success, and cannot reach its own gateway.

    It is not a rare shape: it is what `subnets=[{cidr: <network>/26}]` produces,
    because the first /26 carved out of a /24 begins at the /24's own gateway.
    Verified against the running kernel rather than reasoned about — a route
    inserted toward an address the outgoing interface owns is rejected outright
    with `Nexthop has invalid gateway`, and the address collision shows up as
    `FAILED` neighbour entries on both ends.
    """
    reserved = {gateway}
    usable = [a for a in subnet.hosts() if str(a) not in reserved]
    if len(usable) < count:
        raise Rejected(
            f"{subnet} has {len(usable)} address(es) left once the network's own "
            f"gateway ({gateway}) is excluded, which is not enough to give a "
            f"gateway and a host address. Use a longer prefix (/27 or shorter) "
            f"for this subnet, or move it so it does not contain {gateway}."
        )
    return [str(a) for a in usable[:count]]


def lab_network_namespace(name: str) -> str:
    return name


def subnet_namespace(network: str, subnet: str) -> str:
    return f"{network}--{subnet}"


def bridge_name(network: str) -> str:
    return f"br-{network}"


def _veth_names(index: int) -> Tuple[str, str]:
    """Short, unique, and always within IFNAMSIZ.

    A veth named after the network and subnet would blow the 15-byte limit on
    any realistic name, and a truncated name is a *different* interface from the
    one the teardown looks for - so these are positional instead of descriptive.
    """
    host, inside = f"vh{index:02x}h", f"vh{index:02x}n"
    assert len(host) <= IFNAMSIZ and len(inside) <= IFNAMSIZ
    return host, inside


def _uplink_names() -> Tuple[str, str]:
    """The uplink pair. `u00` cannot collide with a subnet's `vh01h`-style name."""
    host, lab = "uh0h", "uh0n"
    assert len(host) <= IFNAMSIZ and len(lab) <= IFNAMSIZ
    return host, lab


def _make_uplink(network: ipaddress.IPv4Network, uplink_device: str) -> Optional[Uplink]:
    """Derive the /30 point-to-point link from the top of `network`.

    Computed with integer arithmetic rather than `subnets(new_prefix=30)`: a
    /16 lab would materialise 16,384 objects to use the last one.

    `lab_side` is the *first* host so the gateway convention (`_hosts(...)[0]`)
    is the same everywhere in this module, and `host_side` is the second, which
    is the address the lab's default route points at.
    """
    last_start = int(network.broadcast_address) - 3
    top = ipaddress.ip_network(f"{ipaddress.IPv4Address(last_start)}/30")
    if not top.subnet_of(network):
        return None  # a /31 or /32 lab: no room for a point-to-point link
    lab_side, host_side = _hosts(top, 2)
    return Uplink(str(top), top, host_side, lab_side, uplink_device)


def parse_request(request: dict) -> LabNetwork:
    """Validate an untrusted request and return the resource it describes.

    Raises `Rejected` with a sentence fit to show a person. This is the only
    place a request is ever interpreted.
    """
    if not isinstance(request, dict):
        raise Rejected("The request was not an object, so there was nothing to build.")
    name = _check_name(request.get("name"), _LAB_NETWORK_NAME, "network", 11)
    network = _check_cidr(request.get("cidr"), "network")
    gateway, = _hosts(network, 1)

    raw_subnets = request.get("subnets")
    if not isinstance(raw_subnets, list) or not raw_subnets:
        raise Rejected(
            "A network needs at least one subnet. Give subnets=[{name, cidr, "
            "public}] - public subnets get a default route out through the "
            "uplink and masquerade, private ones can only reach this network."
        )
    if len(raw_subnets) > 8:
        raise Rejected(
            f"{len(raw_subnets)} subnets is more than this will build (8 max). "
            f"Each one is a namespace and a veth pair."
        )

    nat = bool(request.get("nat", True))
    uplink = str(request.get("uplink") or "").strip()
    link: Optional[Uplink] = None
    if nat:
        if not uplink:
            raise Rejected(
                "nat is on but no uplink was given, so a public subnet's default "
                "route would have no next hop. Give uplink='<interface on this "
                "machine>' (the one that reaches the internet), or set nat=false "
                "for a lab that stays entirely inside itself."
            )
        if not uplink.replace(".", "").replace(":", "").isalnum():
            raise Rejected(
                f"{uplink!r} is not an interface name. It has to be one of this "
                f"machine's interfaces, e.g. 'eth0'."
            )
        if not Path(f"/sys/class/net/{uplink}").exists():
            known = sorted(p.name for p in Path("/sys/class/net").glob("*"))
            raise Rejected(
                f"There is no interface called {uplink!r} on this machine. "
                f"Present: {', '.join(known)}."
            )
        if uplink in _RESERVED_NAMES or any(uplink.startswith(p) for p in _RESERVED_PREFIXES):
            raise Rejected(
                f"{uplink!r} is an interface this tool reserves for its own use "
                f"(docker*, virbr*, veth*, br-*, chronoa-*). Point the uplink at "
                f"a real machine interface."
            )
        link = _make_uplink(network, uplink)
        if link is None:
            raise Rejected(
                f"{network} is too small to hold a point-to-point link as well as "
                f"a subnet. Use a /28 or shorter prefix."
            )

    subnets: List[Subnet] = []
    seen_names = set()
    seen_nets: List[ipaddress.IPv4Network] = []
    for index, raw in enumerate(raw_subnets):
        if not isinstance(raw, dict):
            raise Rejected(f"Subnet {index + 1} was not an object.")
        sub_name = _check_name(raw.get("name"), _SUBNET_NAME, "subnet", 10)
        if sub_name in seen_names:
            raise Rejected(f"Two subnets are both called {sub_name!r}.")
        seen_names.add(sub_name)
        sub_net = _check_cidr(raw.get("cidr"), f"subnet {sub_name!r}")
        if not sub_net.subnet_of(network):
            raise Rejected(
                f"Subnet {sub_name!r} is {sub_net}, which is not inside the network's "
                f"{network}. A subnet outside it cannot be attached to it."
            )
        if link is not None and sub_net.overlaps(link.network):
            raise Rejected(
                f"Subnet {sub_name!r} ({sub_net}) overlaps the uplink link "
                f"{link.cidr}. That /30 is the only path out, so a subnet may not "
                f"claim it. Use a smaller subnet range, or a shorter lab prefix."
            )
        for other in seen_nets:
            if sub_net.overlaps(other):
                raise Rejected(
                    f"Subnet {sub_name!r} ({sub_net}) overlaps {other}. Refused "
                    f"rather than resolved: which one wins would be a routing "
                    f"accident, not an answer."
                )
        seen_nets.append(sub_net)
        sub_gateway, instance = _subnet_hosts(sub_net, gateway, 2)
        subnets.append(Subnet(sub_name, str(sub_net), bool(raw.get("public", False)),
                              sub_net, sub_gateway, instance))

    return LabNetwork(name, str(network), network, gateway, tuple(subnets), nat, uplink, link)


# --------------------------------------------------------------------- plan


def _sysctl_publish(name: str, value: str, network: str) -> Step:
    return Step(["ip", "netns", "exec", network, "sysctl", "-qw", f"{name}={value}"],
                f"enable {name} inside {network}", optional=True)


def _nft_chain(table: str, hook: str, kind: str) -> Step:
    """One nftables base chain. The braces are a single argv element, which is
    what the shell would have passed after quoting it - no shell here."""
    return Step(["nft", "add", "chain", "ip", table, hook,
                 f"{{ type {kind} hook {hook} priority 100 ; }}"],
                f"{hook} chain in {table}")


def _host_sysctl(name: str, value: str) -> Step:
    """A host-namespace sysctl. Optional: it is a global host setting, and a
    host that refuses it (a locked-down sysctl, a container) still gets a lab
    that works between its own subnets."""
    return Step(["sysctl", "-qw", f"{name}={value}"], f"enable {name} on the host",
                optional=True)


def _via(gateway: str, device: str,
         subnet: ipaddress.IPv4Network) -> List[str]:
    """`via <gateway> dev <device>`, plus `onlink` only when it is required.

    **`onlink` is not unconditional, and getting this wrong fails the route.**
    It tells the kernel "take this next hop as directly reachable on this link,
    do not resolve it through the routing table". The network's gateway is on
    the far end of this veth, in another namespace, so from inside the subnet
    namespace there is genuinely no route to it and `onlink` is *needed* -
    without it the kernel answers `Nexthop has invalid gateway`.

    But `onlink` is also *refused* when the next hop is already covered by the
    interface's own connected route: the kernel has `subnet` on-link via the
    kernel-installed `proto kernel scope link` route, and asserting the same
    reachability again contradicts it. Measured both ways on the running kernel,
    because the rule is not guessable:

    - gateway outside the subnet's CIDR (a /24 lab gateway, a /26 subnet):
      `onlink` **required** - without it, `Nexthop has invalid gateway`.
    - gateway inside the subnet's own CIDR but not its address (which is what
      `_subnet_hosts()` now guarantees, since the network gateway is excluded):
      `onlink` **refused** - with it, `Nexthop has invalid gateway`.

    So exactly one of the two spellings is valid for any given subnet, and
    emitting the wrong one produces a network that is built, reported as
    successful, and has no route out of it.
    """
    args = ["via", gateway, "dev", device]
    if ipaddress.ip_address(gateway) not in subnet:
        args.append("onlink")
    return args


def build_plan(network: LabNetwork) -> List[Step]:
    """The full argv list that creates `network`, in order.

    The ordering is load-bearing in three places, and each was a working-looking
    bug before it was corrected by running the plan:

    1. **A veth cannot be enslaved from another namespace.** `ip link set X
       master br-...` run via `ip netns exec network` resolves `X` *inside* network, so
       attaching a pair whose host end is still in the root namespace silently
       addresses nothing. Both ends are moved into the namespace that will own
       them before any `master` is set.
    2. **A route cannot cover the lab's own CIDR from inside a subnet.** The
       kernel already installs a connected route for the subnet's own prefix, so
       `ip route add <lab-cidr>` fails with `RTNETLINK answers: File exists`
       and a catch-all also shadows the very link it needs. Each subnet instead
       gets one explicit route per *other* subnet, and `onlink` because the
       lab's gateway is not on the subnet's own link.
    3. **A default route needs a next hop that exists.** Pointing it at the
       subnet's own gateway (the subnet's first address) makes it point at the
       machine making the route. It points at the lab network's gateway, which
       is what actually routes.
    """
    steps: List[Step] = []
    bridge = bridge_name(network.name)

    # --- the lab network's own namespace: a bridge acting as its router.
    steps.append(Step(["ip", "netns", "add", network.name],
                      f"create namespace {network.name}"))
    steps.append(Step(["ip", "netns", "exec", network.name, "ip", "link", "add",
                        "name", bridge, "type", "bridge"], f"bridge {bridge}"))
    steps.append(Step(["ip", "netns", "exec", network.name, "ip", "addr", "add",
                        f"{network.gateway}/{network.addr.prefixlen}", "dev", bridge],
                      f"gateway {network.gateway} on {bridge}"))
    steps.append(Step(["ip", "netns", "exec", network.name, "ip", "link", "set",
                        bridge, "up"], f"bring {bridge} up"))
    steps.append(_sysctl_publish("net.ipv4.ip_forward", "1", network.name))

    # --- the uplink: one /30 point-to-point veth pair, host side in the root
    # namespace. This is what a provider calls an internet gateway, and it is
    # the only reason a public subnet can reach anything at all.
    if network.link is not None:
        link = network.link
        uh_host, uh_lab = _uplink_names()
        steps += [
            Step(["ip", "link", "add", uh_host, "type", "veth", "peer",
                  "name", uh_lab], f"uplink pair {link.cidr}"),
            # The lab end goes into the lab namespace and onto its bridge; the
            # host end stays here so the host can route to the lab.
            Step(["ip", "link", "set", uh_lab, "netns", network.name],
                 f"move {uh_lab} into {network.name}"),
            Step(["ip", "netns", "exec", network.name, "ip", "link", "set",
                  uh_lab, "master", bridge], f"attach {uh_lab} to {bridge}"),
            Step(["ip", "netns", "exec", network.name, "ip", "addr", "add",
                  f"{link.lab_side}/{link.network.prefixlen}", "dev", uh_lab],
                 f"uplink {link.lab_side} inside {network.name}"),
            Step(["ip", "netns", "exec", network.name, "ip", "link", "set",
                  uh_lab, "up"], f"bring {uh_lab} up"),
            Step(["ip", "netns", "exec", network.name, "ip", "route", "add",
                  "default", "via", link.host_side, "dev", uh_lab],
                 f"default route for {network.name} out of {network.link.device}"),
            Step(["ip", "addr", "add",
                  f"{link.host_side}/{link.network.prefixlen}", "dev", uh_host],
                 f"uplink {link.host_side} on the host"),
            Step(["ip", "link", "set", uh_host, "up"], f"bring {uh_host} up"),
            # Return traffic is un-NATed by conntrack to a lab address that is
            # not on this link's /30, so the host needs an explicit route or it
            # would send replies out its own default route.
            Step(["ip", "route", "add", network.cidr, "via", link.lab_side,
                  "dev", uh_host], f"host route to {network.cidr}"),
            _host_sysctl("net.ipv4.ip_forward", "1"),
        ]

    # --- the subnets: one namespace and one veth pair each.
    for index, subnet in enumerate(network.subnets, start=1):
        ns = subnet_namespace(network.name, subnet.name)
        host_veth, inside_veth = _veth_names(index)
        others = [s for s in network.subnets if s.name != subnet.name]
        steps += [
            Step(["ip", "netns", "add", ns], f"subnet {subnet.name} namespace"),
            Step(["ip", "link", "add", host_veth, "type", "veth",
                  "peer", "name", inside_veth], f"veth pair for {subnet.name}"),
            Step(["ip", "link", "set", inside_veth, "netns", ns],
                 f"move {inside_veth} into {ns}"),
            # Both ends into their owners before either is enslaved or addressed.
            Step(["ip", "link", "set", host_veth, "netns", network.name],
                 f"move {host_veth} into {network.name}"),
            Step(["ip", "netns", "exec", network.name, "ip", "link", "set",
                  host_veth, "master", bridge], f"attach {host_veth} to {bridge}"),
            Step(["ip", "netns", "exec", network.name, "ip", "link", "set",
                  host_veth, "up"], f"bring {host_veth} up"),
            Step(["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"],
                 f"loopback up in {ns}"),
            Step(["ip", "netns", "exec", ns, "ip", "link", "set", inside_veth, "up"],
                 f"bring {inside_veth} up in {ns}"),
            Step(["ip", "netns", "exec", ns, "ip", "addr", "add",
                   f"{subnet.gateway}/{subnet.network.prefixlen}", "dev", inside_veth],
                  f"subnet gateway {subnet.gateway}"),
            Step(["ip", "netns", "exec", ns, "ip", "addr", "add",
                   f"{subnet.instance}/{subnet.network.prefixlen}", "dev", "lo"],
                  f"host address {subnet.instance}"),
        ]
        # One explicit route per peer subnet: a catch-all for the lab's whole
        # CIDR would collide with the connected route the kernel just installed.
        for other in others:
            steps.append(Step(["ip", "netns", "exec", ns, "ip", "route", "add",
                               other.cidr]
                              + _via(network.gateway, inside_veth, subnet.network),
                              f"route to subnet {other.name} ({other.cidr})"))
        if subnet.public:
            if network.link is None:
                raise Rejected(
                    f"Subnet {subnet.name!r} is public but this network has no "
                    f"uplink, so its default route would have no next hop. Give "
                    f"an uplink, or make the subnet private."
                )
            # The gateway is the lab network's, not this subnet's own address.
            steps.append(Step(["ip", "netns", "exec", ns, "ip", "route", "add",
                               "default"] + _via(network.gateway, inside_veth,
                                                  subnet.network),
                              f"default route out of {subnet.name}"))

    # --- masquerade. Exactly one rule, in the root namespace, in a table this
    # tool owns and deletes by name.
    #
    # It matches on *both* the lab source range and the destination interface,
    # so host traffic cannot match it and enabling a lab does not change how any
    # existing connection behaves. There is deliberately no second masquerade
    # inside the lab namespace: that was tried first and is wrong, because the
    # inner rule rewrites the source to the uplink's own address (10.77.255.253
    # here), which is *outside* the lab CIDR the host rule matches on - so the
    # packet would leave with an unroutable source and simply vanish. One
    # masquerade also means the lab keeps its real source addresses end to end,
    # which is what makes `tcpdump` inside a subnet legible.
    if network.nat and network.link is not None:
        host_table = f"chronoa-host-{network.name}"
        steps += [
            Step(["nft", "add", "table", "ip", host_table],
                 f"host nftables table {host_table}"),
            _nft_chain(host_table, "postrouting", "nat"),
            Step(["nft", "add", "rule", "ip", host_table, "postrouting",
                  "ip", "saddr", network.cidr, "oifname", network.uplink, "masquerade"],
                 f"masquerade {network.cidr} out of {network.uplink}"),
        ]
    return steps


def build_teardown(network: LabNetwork) -> List[Step]:
    """Removal, most-dependent-first, so nothing is torn down while in use.

    Deleting a network namespace does not destroy the devices in it: the kernel
    moves them back to the initial namespace. So every interface this plan
    created inside the lab is still on the host afterwards and each is removed
    here, `optional` because a namespace that was never created brings nothing
    back. Missing that is how a "destroyed" lab leaves stray `br-` and `vh`
    interfaces behind on the machine forever.
    """
    bridge = bridge_name(network.name)
    steps: List[Step] = []
    for subnet in reversed(network.subnets):
        ns = subnet_namespace(network.name, subnet.name)
        steps.append(Step(["ip", "netns", "del", ns], f"remove subnet {subnet.name} namespace"))
    if network.link is not None:
        steps.append(Step(["ip", "route", "del", network.cidr, "via",
                           network.link.lab_side, "dev", _uplink_names()[0]],
                          f"drop host route to {network.cidr}", optional=True))
    if network.nat:
        steps.append(Step(["nft", "delete", "table", "ip", f"chronoa-host-{network.name}"],
                          f"drop host masquerade for {network.cidr}", optional=True))
    steps.append(Step(["ip", "netns", "del", network.name],
                      f"remove network namespace {network.name}", optional=True))
    # Everything the lab namespace gave back to the host, in dependency order.
    for index in range(len(network.subnets), 0, -1):
        host_veth, _ = _veth_names(index)
        steps.append(Step(["ip", "link", "del", host_veth],
                          f"drop {host_veth} left on the host", optional=True))
    if network.link is not None:
        uh_host, _ = _uplink_names()
        # Deleting one end of a veth pair removes the peer, so this clears both.
        steps.append(Step(["ip", "link", "del", uh_host],
                          f"drop {uh_host} left on the host", optional=True))
    steps.append(Step(["ip", "link", "del", bridge],
                      f"drop {bridge} left on the host", optional=True))
    return steps


def render_plan(network: LabNetwork, steps: List[Step], title: str) -> str:
    lines = [title, f"  network {network.name}  {network.cidr}  gateway {network.gateway}"
                   f"  uplink {network.uplink or 'none'}"
                   f"  masquerade {'on' if network.nat else 'off'}"]
    for subnet in network.subnets:
        lines.append(
            f"    subnet {subnet.name:<10} {subnet.cidr:<18} "
            f"gateway {subnet.gateway:<15} instance {subnet.instance:<15} "
            f"{'public' if subnet.public else 'private (no route out)'}")
    lines.append(f"  {len(steps)} step(s):")
    for number, step in enumerate(steps, start=1):
        suffix = "  (optional)" if step.optional else ""
        lines.append(f"   {number:>3}. {' '.join(step.argv)}{suffix}")
    return "\n".join(lines)


# ------------------------------------------------------------------- record


def state_dir() -> Path:
    """Where the record of what was built lives. Resolved per call.

    A module-level constant would point the test suite at a real user's home,
    which `tests/conftest.py` has a guard against.
    """
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / "lab-networks"


def record_path() -> Path:
    return state_dir() / "lab-networks.json"


def load_record() -> Dict[str, dict]:
    try:
        data = json.loads(record_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_record(entries: Dict[str, dict]) -> None:
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written through a temporary file in the same directory and renamed, so a
    # reader never sees a half-written record - the same reason `PerceptStore`
    # renames into place.
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".networks-")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(entries, stream, indent=2, sort_keys=True)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def envelope_dir() -> Path:
    return state_dir() / _ENVELOPE_DIRNAME


def write_envelope(plan: dict) -> Path:
    """The plan, as a 0600 file created with `O_EXCL`.

    `O_EXCL` on a fresh mkstemp, 0600 before a byte is written, and the only
    thing put on the command line afterwards is this path. That is the whole
    reason an untrusted value never reaches `pkexec`'s argv.
    """
    directory = envelope_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    handle, temporary = tempfile.mkstemp(dir=str(directory), prefix="plan-", suffix=".json")
    path = Path(temporary)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w") as stream:
            json.dump(plan, stream)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def read_envelope(path: str) -> dict:
    """Load a plan and check it is ours, is not a symlink, and is not readable
    by anyone else. A plan the helper will act on is a root action, so the file
    it came from is treated as untrusted input too."""
    target = Path(path)
    if target.is_symlink():
        raise Rejected(f"{path} is a symlink, and a plan must be a real file.")
    info = target.stat()
    if info.st_mode & 0o077:
        raise Rejected(
            f"{path} is readable or writable by other users (mode "
            f"{info.st_mode & 0o777:o}). A plan that turns into a root command "
            f"has to be private to the user who asked for it."
        )
    try:
        plan = json.loads(target.read_text())
    except (OSError, ValueError) as exc:
        raise Rejected(f"{path} could not be read as a plan: {exc}.") from exc
    if not isinstance(plan, dict) or plan.get("format") != "chronoa-network-plan/1":
        raise Rejected(
            f"{path} is not a Chronoa network plan. The helper will not act on a "
            f"file it did not recognise."
        )
    return plan


def make_plan(network: LabNetwork, action: str, steps: List[Step]) -> dict:
    """A plan file's contents.

    The *whole* request travels in the plan, not just the network's name and CIDR,
    because `revalidate()` rebuilds the request from it and re-derives every
    step. A plan that carried only a name could not be checked against anything.
    """
    return {
        "format": "chronoa-network-plan/1",
        "action": action,
        "network": network.name,
        "cidr": network.cidr,
        "nat": network.nat,
        "uplink": network.uplink,
        "subnets": [
            {"name": s.name, "cidr": s.cidr, "public": s.public} for s in network.subnets
        ],
        "steps": [{"argv": list(step.argv), "optional": step.optional,
                   "note": step.note} for step in steps],
    }


def revalidate(plan: dict) -> Tuple[str, Vpc, List[Step]]:
    """Rebuild the expected plan from a loaded file and insist they match.

    This is the check that makes an edited plan file harmless. The request is
    parsed again and the steps are regenerated; anything that differs is
    refused. So a plan can only ever contain commands this module would have
    produced from a request it accepts - there is no path by which a plan file
    becomes an arbitrary root command.
    """
    action = str(plan.get("action") or "")
    if action not in ("create", "destroy"):
        raise Rejected(f"Unknown plan action {action!r}.")
    # `nat` has to come from the plan, not be assumed: assuming True made every
    # private-only lab's destroy plan fail to revalidate, because a NAT network
    # now requires an uplink that a private lab never had.
    network = parse_request({
        "name": plan.get("network"),
        "cidr": plan.get("cidr"),
        "nat": plan.get("nat"),
        "uplink": plan.get("uplink") or "",
        "subnets": plan.get("subnets") or [],
    })
    expected = build_plan(network) if action == "create" else build_teardown(network)
    supplied = plan.get("steps")
    if not isinstance(supplied, list):
        raise Rejected("The plan has no steps, so there is nothing to check.")
    wanted = [list(step.argv) for step in expected]

    seen = []
    for position, entry in enumerate(supplied, start=1):
        if not isinstance(entry, dict) or not isinstance(entry.get("argv"), list):
            raise Rejected(f"Step {position} of the plan is not an argv array.")
        argv = [str(value) for value in entry["argv"]]
        if position > len(wanted):
            raise Rejected(
                f"The plan has {len(supplied)} steps but this build only runs "
                f"{len(wanted)}. Refusing a plan with steps added to it."
            )
        if argv != wanted[position - 1]:
            raise Rejected(
                f"Step {position} of the plan is {argv!r}, but this build's step "
                f"{position} is {wanted[position - 1]!r}. Refusing a plan that "
                f"does not match its own request."
            )
        seen.append(argv)

    # A teardown may legitimately skip steps that already succeeded, so a short
    # plan is allowed there. A create plan may not be short: a half-built network
    # that reports success is the one outcome worth refusing.
    if len(seen) != len(wanted) and action == "create":
        raise Rejected(
            f"The plan has {len(seen)} of the {len(wanted)} steps this build "
            f"requires, ending at {seen[-1]!r}. A network built part-way is not "
            f"a network; refusing rather than reporting a partial build as done."
        )
    return action, network, expected[:len(seen)]


def describe(network: LabNetwork) -> str:
    """What this network should look like once built - the read-back contract."""
    lines = [
        f"network {network.name}  {network.cidr}  gateway {network.gateway}",
        f"  namespace {network.name}, bridge {bridge_name(network.name)}, forwarding on",
    ]
    if network.link is not None:
        lines.append(
            f"  uplink {network.link.cidr}: {network.link.host_side} on the host, "
            f"{network.link.lab_side} inside the network, out of {network.uplink}")
    else:
        lines.append("  no uplink: this network cannot reach anything outside itself")
    for subnet in network.subnets:
        reach = "the internet, through the uplink" if subnet.public else \
                "only this network - no default route is installed"
        lines.append(
            f"  subnet {subnet.name} ({'public' if subnet.public else 'private'}) "
            f"{subnet.cidr} in namespace {subnet_namespace(network.name, subnet.name)}: "
            f"gateway {subnet.gateway}, host {subnet.instance}. It can reach {reach}.")
    return "\n".join(lines)
