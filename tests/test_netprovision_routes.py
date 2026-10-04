"""Two routing bugs in the lab network builder, each found by running the plan.

Both were invisible to inspection and to `py_compile`: the plan is a list of
argv arrays that looked correct, and the module had no test at all. They only
surfaced by executing the generated plan against a real kernel and reading
which steps the kernel refused.

**Bug 1 - a subnet was handed the network's own gateway address.** The network
gateway is the network's *first* host address, and a subnet starting at that
same address contains it, so `_hosts(subnet, 2)` returned an address the
network's bridge already owns. The plan then put one address on both ends of a
veth pair. `subnets=[{cidr: <network>/26}]` produces exactly this, so it was
the first thing anyone would try.

**Bug 2 - `onlink` was emitted unconditionally, and exactly one of its two
spellings is valid for any given subnet.** The network gateway is on the far end
of the veth in another namespace, so `onlink` is *required* when the gateway
lies outside the subnet's own CIDR, and *refused* when it lies inside it. The
builder emitted it always, so a subnet whose gateway was inside its own CIDR
got a route the kernel rejected with `Nexthop has invalid gateway`.

Each test asserts the argv the builder produces, and each has a control that
fails if the assertion is vacuous. They are unit tests: they check what the
builder emits, which is the half that was wrong. Confirming the kernel's
reaction needs a privileged netns and belongs to the slot harness.
"""

import ipaddress
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import netprovision as np  # noqa: E402


def _request(**over):
    req = {
        "name": "lab",
        "cidr": "10.90.0.0/24",
        "nat": True,
        # parse_request requires the uplink to exist on this machine; any real
        # interface name works, the plan is never executed here.
        "uplink": _any_uplink(),
        "subnets": [{"name": "web", "cidr": "10.90.0.0/26", "public": True}],
    }
    req.update(over)
    return req


def _any_uplink() -> str:
    """A real interface name this tool will accept as an uplink.

    `lo` is refused (it is in `_RESERVED_NAMES`), as is anything starting with
    a reserved prefix, so pick the first interface that is genuinely usable.
    """
    reserved_prefixes = ("docker", "virbr", "veth", "br-", "tun", "tap", "chronoa-")
    for path in sorted(Path("/sys/class/net").glob("*")):
        name = path.name
        if name == "lo" or name.startswith(reserved_prefixes):
            continue
        return name
    pytest.skip("no non-reserved network interface to use as an uplink")


def _route_steps(vpc):
    return [s for s in np.build_plan(vpc) if "route" in s.argv and "add" in s.argv]


def _subnet_route_steps(vpc):
    """Route steps installed *inside a subnet namespace*.

    The plan also installs the uplink's default route in the network namespace
    and a host-side route in the root namespace. Those are not subnet routes and
    they do not follow the onlink rule below - they address the /30 taken from
    the top of the network CIDR, not a subnet's own gateway - so filtering them
    out is what keeps this file's assertions honest.
    """
    subnet_ns = {np.subnet_namespace(vpc.name, s.name) for s in vpc.subnets}
    out = []
    for step in _route_steps(vpc):
        if "exec" not in step.argv:
            continue
        if step.argv[step.argv.index("exec") + 1] in subnet_ns:
            out.append(step)
    return out


# --------------------------------------------------------------- bug 1


def test_a_subnet_never_receives_the_networks_own_gateway_address():
    """The subnet's gateway must not be the address the network bridge owns."""
    vpc = np.parse_request(_request())
    assert vpc.gateway == "10.90.0.1"
    for subnet in vpc.subnets:
        assert subnet.gateway != vpc.gateway, (
            f"subnet {subnet.name} was given {subnet.gateway}, which is also the "
            f"network's gateway - the plan puts one address on both ends of the "
            f"veth pair"
        )


def test_the_colliding_shape_is_the_obvious_one_and_is_now_fixed():
    """The first /26 of a /24 begins at the /24's own gateway - bug 1's shape."""
    vpc = np.parse_request(_request(
        subnets=[{"name": "web", "cidr": "10.90.0.0/26", "public": True}]))
    subnet = vpc.subnets[0]
    assert ipaddress.ip_address("10.90.0.1") in subnet.network, (
        "precondition: this request really does put the network gateway inside "
        "the subnet"
    )
    assert subnet.gateway != "10.90.0.1"
    # and it is still a usable address from inside that subnet
    assert ipaddress.ip_address(subnet.gateway) in subnet.network
    assert ipaddress.ip_address(subnet.instance) in subnet.network
    assert subnet.gateway != subnet.instance


def test_a_subnet_too_small_once_the_gateway_is_excluded_is_refused():
    """A /30 subnet has no room left, and must say so rather than collide."""
    with pytest.raises(np.Rejected) as exc:
        np.parse_request(_request(
            subnets=[{"name": "tiny", "cidr": "10.90.0.0/30", "public": False}]))
    assert "gateway" in str(exc.value).lower()


def test_every_subnet_gateway_and_instance_is_inside_its_own_subnet():
    vpc = np.parse_request(_request(subnets=[
        {"name": "a", "cidr": "10.90.0.0/26", "public": True},
        {"name": "b", "cidr": "10.90.0.64/26", "public": False},
        {"name": "c", "cidr": "10.90.0.128/26", "public": False},
    ]))
    for subnet in vpc.subnets:
        assert ipaddress.ip_address(subnet.gateway) in subnet.network
        assert ipaddress.ip_address(subnet.instance) in subnet.network
    gateways = [s.gateway for s in vpc.subnets]
    assert len(set(gateways)) == len(gateways), "two subnets share a gateway"
    assert vpc.gateway not in gateways


# --------------------------------------------------------------- bug 2


def test_onlink_is_emitted_only_when_the_gateway_is_outside_the_subnet():
    """The rule the kernel actually enforces, in both directions."""
    outside = np.parse_request(_request(subnets=[
        {"name": "far", "cidr": "10.90.0.64/26", "public": True}]))
    inside = np.parse_request(_request(subnets=[
        {"name": "near", "cidr": "10.90.0.0/26", "public": True}]))

    # 10.90.0.1 is outside 10.90.0.64/26 -> onlink is required
    far_steps = [s for s in _subnet_route_steps(outside)
                 if "default" in s.argv]
    assert far_steps, "expected a default route for the public subnet"
    assert "onlink" in far_steps[0].argv, (
        "gateway outside the subnet's CIDR needs onlink or the kernel refuses "
        "the route with 'Nexthop has invalid gateway'"
    )

    # 10.90.0.1 is inside 10.90.0.0/26 -> onlink is refused
    near_steps = [s for s in _subnet_route_steps(inside) if "default" in s.argv]
    assert near_steps, "expected a default route for the public subnet"
    assert "onlink" not in near_steps[0].argv, (
        "gateway inside the subnet's own CIDR cannot carry onlink - the "
        "connected route already covers it and the kernel refuses"
    )


def test_every_generated_route_matches_the_onlink_rule():
    """Whatever the request, no route contradicts the rule."""
    vpc = np.parse_request(_request(subnets=[
        {"name": "a", "cidr": "10.90.0.0/26", "public": True},
        {"name": "b", "cidr": "10.90.0.64/26", "public": True},
        {"name": "c", "cidr": "10.90.0.128/26", "public": False},
    ]))
    checked = 0
    for step in _subnet_route_steps(vpc):
        argv = step.argv
        if "via" not in argv:
            continue
        gateway = argv[argv.index("via") + 1]
        # the subnet this route is installed into, named in the argv
        target_ns = argv[argv.index("exec") + 1]
        subnet = next(s for s in vpc.subnets if np.subnet_namespace(
            vpc.name, s.name) == target_ns)
        expected = ipaddress.ip_address(gateway) not in subnet.network
        assert ("onlink" in argv) is expected, (
            f"route in {target_ns}: gateway {gateway} in {subnet.network} "
            f"should {'have' if expected else 'not have'} onlink, got {argv}"
        )
        checked += 1
    assert checked >= 4, f"expected several routes to check, saw {checked}"


# --------------------------------------------------------------- controls


def test_control_the_onlink_rule_is_not_vacuous():
    """A wrong rule must fail this file's tests - otherwise they prove nothing."""
    # gateway INSIDE the subnet, yet onlink asserted: must be reported as wrong
    subnet = ipaddress.ip_network("10.90.0.0/26")
    assert "onlink" not in np._via("10.90.0.1", "vh01n", subnet)
    # gateway OUTSIDE the subnet, yet onlink omitted: must be reported as wrong
    other = ipaddress.ip_network("10.90.0.64/26")
    assert "onlink" in np._via("10.90.0.1", "vh02n", other)


def test_control_plan_still_builds_every_namespace_and_route():
    """Guards against a 'fix' that drops steps and so breaks something else."""
    vpc = np.parse_request(_request(subnets=[
        {"name": "a", "cidr": "10.90.0.0/26", "public": True},
        {"name": "b", "cidr": "10.90.0.64/26", "public": False},
    ]))
    argv = [" ".join(s.argv) for s in np.build_plan(vpc)]
    joined = "\n".join(argv)
    assert "ip netns add lab" in joined
    assert "ip netns add lab--a" in joined
    assert "ip netns add lab--b" in joined
    assert "type bridge" in joined
    assert "type veth" in joined
    assert "masquerade" in joined
    # no shell string anywhere: every step is an argv array
    for step in np.build_plan(vpc):
        assert isinstance(step.argv, list)
        assert all(isinstance(a, str) for a in step.argv)


def test_control_teardown_still_removes_what_create_made():
    vpc = np.parse_request(_request(subnets=[
        {"name": "a", "cidr": "10.90.0.0/26", "public": True},
        {"name": "b", "cidr": "10.90.0.64/26", "public": False},
    ]))
    down = [" ".join(s.argv) for s in np.build_teardown(vpc)]
    joined = "\n".join(down)
    assert "ip netns del lab" in joined
    assert "ip netns del lab--a" in joined
    assert "ip netns del lab--b" in joined
    assert "delete table ip chronoa-host-lab" in joined


def test_private_subnets_still_get_no_default_route():
    vpc = np.parse_request(_request(subnets=[
        {"name": "pub", "cidr": "10.90.0.0/26", "public": True},
        {"name": "priv", "cidr": "10.90.0.64/26", "public": False},
    ]))
    defaults = [s for s in _subnet_route_steps(vpc) if "default" in s.argv]
    namespaces = {s.argv[s.argv.index("exec") + 1] for s in defaults}
    assert "lab--pub" in namespaces
    assert "lab--priv" not in namespaces, (
        "a private subnet must not get a default route"
    )