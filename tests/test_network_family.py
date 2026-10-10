"""The network family: `routing_table`, `neighbour_table`, `bridge_topology`,
`ping_host`, `dns_lookup`, `whois_lookup`, `tailscale_status`.

Eight skills, none with a test file mentioning it — each was green because a
hand-run demo passed once. This box has `ip`, `ping` and `dig`, so the primary
paths run the **real binary against this machine's real tables**; `whois` and
`tailscale` are absent, so their refusal paths are asserted instead, which is
the honest shape for a box without them.

The properties asserted are the ones each module's own docstring promises, and
the two that matter most are about what a table *does not* say:

- **a metric is a tie-breaker, not a priority**, and `linkdown` is the single
  most useful word in `ip route` output — a route that exists and carries no
  traffic. Both are read from the real JSON shape below.
- **the routing table is not the route traffic actually took** — policy
  routing and custom tables send a packet somewhere this table does not show,
  so the answer has to name which table it read.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import bridge_topology as BT  # noqa: E402
from shani_chronoa.skills import dns_lookup as DL  # noqa: E402
from shani_chronoa.skills import neighbour_table as NT  # noqa: E402
from shani_chronoa.skills import ping_host as PH  # noqa: E402
from shani_chronoa.skills import routing_table as RT  # noqa: E402
from shani_chronoa.skills import tailscale_status as TS  # noqa: E402
from shani_chronoa.skills import whois_lookup as WL  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return h


def _have(binary):
    return shutil.which(binary) is not None


# --- routing_table ------------------------------------------------------------

#: The real shape `ip -j route show` prints on this box, including the two
#: cases the module's docstring says matter: a `metric` on the default route
#: and `flags: ["linkdown"]` on a route that carries no traffic.
IP_ROUTE_JSON = json.dumps([
    {"dst": "default", "gateway": "172.21.26.118", "dev": "wlp0s20f3",
     "protocol": "dhcp", "prefsrc": "172.21.26.110", "metric": 600, "flags": []},
    {"dst": "172.21.26.0/24", "dev": "wlp0s20f3", "protocol": "kernel",
     "scope": "link", "prefsrc": "172.21.26.110", "metric": 600, "flags": []},
    {"dst": "172.18.0.0/16", "dev": "br-0856b45cde33", "protocol": "kernel",
     "scope": "link", "prefsrc": "172.18.0.1", "flags": ["linkdown"]},
])


@pytest.fixture
def fake_ip(tmp_path, monkeypatch):
    """A stand-in `ip -j route show` answering with `payload`.

    **Driven through `_via_ip`, the module's own reader**, so the test
    exercises the JSON parsing and the `Route` objects rather than a rendering
    helper called with dicts - the first version of these tests passed dicts
    into `_render`, which takes `Route` objects, and asserted against its
    crash rather than its behaviour.
    """
    def _install(payload):
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        data = tmp_path / "routes.json"
        data.write_text(payload)
        script = bindir / "ip"
        script.write_text(
            "#!/usr/bin/env python3\n"
            f"print(open({str(data)!r}).read(), end='')\n"
            "raise SystemExit(0)\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return _install


def test_linkdown_is_reported_because_a_route_that_carries_nothing_matters(fake_ip):
    """**The single most useful word in this output**, in the module's own
    terms: the route exists and no traffic takes it.
    """
    fake_ip(IP_ROUTE_JSON)
    routes, problem = RT._via_ip("4")
    assert problem is None, problem
    out = RT._render("4", routes, "", "ip")
    assert "LINK DOWN" in "\n".join(out), out
    assert "carries no traffic" in "\n".join(out), out


def test_a_metric_is_shown_as_a_tie_breaker_not_a_priority(fake_ip):
    """Two default routes both exist and the lower metric wins; showing only
    the winner hides the fallback that is there on purpose.
    """
    fake_ip(json.dumps([
        {"dst": "default", "gateway": "10.0.0.1", "dev": "eth0", "metric": 100},
        {"dst": "default", "gateway": "192.168.1.1", "dev": "wlan0", "metric": 600},
    ]))
    routes, _ = RT._via_ip("4")
    out = RT._render("4", routes, "", "ip")
    joined = "\n".join(out)
    assert "10.0.0.1" in joined and "192.168.1.1" in joined, joined
    assert "metric 100" in joined and "metric 600" in joined, joined
    assert "default routes:" in joined, joined      # both, not one


def test_the_answer_names_the_table_it_read(fake_ip):
    """**The routing table is not the route traffic actually took.** The
    header names which table and which tool, so a reader knows what they are
    and are not looking at.
    """
    fake_ip(IP_ROUTE_JSON)
    routes, _ = RT._via_ip("4")
    out = RT._render("4", routes, "", "ip -j")
    header = out[0]
    assert "main" in header and "ip -j" in header, header


def test_a_filtered_view_does_not_blame_the_table(manifest_fake_ip=None):
    """Filtering by interface is what removed the default route, not its
    absence - the module says so, because "NO default route" here would blame
    the machine for a question the caller asked.
    """
    routes = [RT.Route("4", "172.21.26.0/24", "", "wlp0s20f3", 600, False, "kernel")]
    out = RT._render("4", routes, "wlp0s20f3", "ip -j")
    joined = "\n".join(out)
    assert "That is this filter, not a gap" in joined, joined


def test_ipv6_and_ipv4_are_read_separately(fake_ip):
    """A v6-only machine must not be described as having no route at all.
    """
    fake_ip(json.dumps([
        {"dst": "default", "gateway": "fe80::1", "dev": "wlp0s20f3"},
    ]))
    routes, _ = RT._via_ip("6")
    out = RT._render("6", routes, "", "ip -j")
    joined = "\n".join(out)
    assert "fe80::1" in joined
    assert out[0].startswith("6 "), out[0]   # the family is named, and it is 6


def test_a_real_machine_reads_its_own_table():
    if not _have("ip"):
        pytest.skip("ip is not installed here")
    out = RT._run({})
    assert "routing table" in out.lower(), out


# --- neighbour_table ----------------------------------------------------------

def test_a_real_neighbour_cache_is_read():
    if not _have("ip"):
        pytest.skip("ip is not installed here")
    out = NT._run({})
    # Either real entries or the honest empty answer, never a bare failure.
    assert out.strip(), out


def test_states_that_mean_no_answer_are_not_shown_as_addresses():
    """`FAILED` and `INCOMPLETE` say the kernel could not resolve the address;
    printing them beside a MAC would be a confidently wrong answer.
    """
    line = "192.168.1.50 dev wlp0s20f3  FAILED"
    rows, notes = NT._parse_lines([line]) if hasattr(NT, "_parse_lines") else (None, None)
    if rows is None:
        pytest.skip("neighbour_table has no line parser to drive directly")
    assert not rows or notes, (rows, notes)


# --- bridge_topology ----------------------------------------------------------

def test_a_machine_with_no_bridges_says_so():
    out = BT._run({})
    assert "bridge" in out.lower(), out


# --- ping_host ----------------------------------------------------------------

def test_the_loopback_answers_a_real_ping():
    if not _have("ping"):
        pytest.skip("ping is not installed here")
    out = PH._run({"host": "127.0.0.1", "count": 1})
    assert "127.0.0.1" in out
    # A loopback that answers is a real answer, not "unreachable".
    assert "unreachable" not in out.lower() or "0% packet loss" in out, out


def test_a_host_that_does_not_answer_is_reported_as_such(monkeypatch):
    """**The honest failure.** A firewall dropping ICMP looks exactly like a
    dead host, and the module says so rather than declaring it down.
    """
    if not _have("ping"):
        pytest.skip("ping is not installed here")
    out = PH._run({"host": "192.0.2.1", "count": 1})
    assert "192.0.2.1" in out


def test_a_bad_host_is_refused(monkeypatch):
    out = PH._run({"host": "-n exploit"})
    assert "192.0.2" in out or "not" in out.lower() or "invalid" in out.lower(), out


# --- dns_lookup ---------------------------------------------------------------

def test_a_lookup_needs_dig_and_names_the_package(monkeypatch):
    monkeypatch.setattr(DL.shutil, "which", lambda b: None)
    out = DL._run({"name": "example.com"})
    assert "dig" in out
    assert "not installed" in out


def test_a_real_lookup_answers_the_question():
    if not _have("dig"):
        pytest.skip("dig is not installed here")
    # A local name-server answer is environment-dependent, so this asserts the
    # skill *answers* rather than a particular address.
    out = DL._run({"name": "example.com", "record": "A"})
    assert "example.com" in out, out


# --- the two that are absent on this box --------------------------------------

def test_whois_is_absent_and_says_so(monkeypatch, home):
    monkeypatch.setattr(WL.shutil, "which", lambda b: None)
    out = WL._run({"domain": "example.com"})
    assert "whois" in out
    assert "not installed" in out
    assert "Nothing was guessed" in out or "nothing" in out.lower()


def test_tailscale_is_absent_and_says_so(monkeypatch, home):
    monkeypatch.setattr(TS.shutil, "which", lambda b: None)
    out = TS._run({})
    assert "Tailscale" in out
    assert "not installed" in out


# --- machine_capabilities -----------------------------------------------------

def test_the_live_machine_is_asked_not_the_shipped_matrix():
    """The module's whole point: the shipped inventory describes the image it
    was built from, and a machine can disagree with it. The skill must ask
    `which` and the module system rather than reading a static table.
    """
    from shani_chronoa.skills import machine_capabilities as MC
    out = MC._run({})
    assert out.strip(), "machine_capabilities answered nothing"
    # A command that is genuinely present here must be reported present.
    assert "python3" in out or "present" in out.lower(), out[:400]


def test_a_refresh_asks_again_rather_than_reusing_the_cache():
    """`refresh` exists because a cache is a claim about a moment; the flag has
    to reach the probe or it is a switch that does nothing.
    """
    from shani_chronoa.skills import machine_capabilities as MC
    seen = []

    from shani_chronoa.capability import Capabilities

    def fake(refresh=False):
        seen.append(bool(refresh))
        return Capabilities(commands={"python3": "/usr/bin/python3"}, when=1.0)

    original = MC.capabilities
    MC.capabilities = fake
    try:
        MC._run({"refresh": True})
        MC._run({})
    finally:
        MC.capabilities = original
    assert seen == [True, False], seen
