"""Tests for `scan_network`.

A network scan is the most intrusive read in the whole package - it touches
every other device on the LAN - so the gates are tested as carefully as the
parsing:

- consent is checked before anything is executed, not after;
- the range is refused unless it is private, link-local or loopback, because a
  skill that scans whatever it is handed will eventually be talked into
  scanning the internet;
- `nmap` exiting 1 means *some hosts did not respond* and it still printed the
  rest. Discarding output on a non-zero status is the intuitive move and it
  throws away real results.

And the reporting cases that produce a clean-looking wrong answer: an empty
result is not an empty network, a missing binary is not a missing network, and
unreadable MAC addresses are not evidence of anonymous hardware.
"""

import subprocess

import pytest

from shani_chronoa import capabilities
from shani_chronoa.skills import discover_skills, scan_network

NMAP_NO_DNS = """Starting Nmap 7.95 ( https://nmap.org )
Nmap scan report for 192.168.31.1
Host is up (0.0021s latency).
MAC Address: 3C:52:82:11:22:33 (Tp-Link Technologies Co., Ltd.)
Nmap scan report for 192.168.31.128
Host is up (0.014s latency).
MAC Address: 8C:85:90:AB:CD:EF (Apple, Inc.)
Nmap done: 2 IP addresses (2 hosts up) scanned in 4.11 seconds"""

NMAP_WITH_DNS = """Nmap scan report for router.lan (192.168.31.1)
Host is up (0.0021s latency).
MAC Address: 3C:52:82:11:22:33 (Tp-Link Technologies Co., Ltd.)
Nmap scan report for iphone.local (192.168.31.128)
Host is up (0.014s latency).
MAC Address: 8C:85:90:AB:CD:EF (Apple, Inc.)"""

NMAP_NO_MAC = """Nmap scan report for 192.168.31.7
Host is up (0.03s latency).
Nmap done: 1 IP addresses (1 hosts up) scanned in 2.0 seconds"""


@pytest.fixture
def permitted(monkeypatch):
    """Consent granted, as a user who ticked the box would have it."""
    from shani_chronoa.config import ChronoaConfig
    config = ChronoaConfig()
    config.set("network-sense-enabled", "true")
    config.set("privacy-mode", "false")
    return config


@pytest.fixture
def nmap_present(monkeypatch):
    monkeypatch.setattr(scan_network.shutil, "which",
                        lambda n: "/usr/bin/nmap" if n == "nmap" else None)


def _nmap(monkeypatch, *, rc=0, out=NMAP_NO_DNS, err="", raises=None):
    seen = {}

    def fake(cmd, **kwargs):
        seen["cmd"] = cmd
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(cmd, rc, out, err)

    monkeypatch.setattr(scan_network.subprocess, "run", fake)
    return seen


class TestConsentGate:
    def test_a_denied_scan_never_runs_nmap(self, monkeypatch, nmap_present):
        """The gate has to come before the scan, not after it."""
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
        config.set("network-sense-enabled", "false")
        seen = _nmap(monkeypatch)
        out = scan_network._run({})
        assert "not permitted" in out
        assert "network-sense-enabled" in out, "the refusal must say what to change"
        assert seen == {}, "nmap was executed despite the consent gate"

    def test_the_refusal_uses_the_shared_reason_string(self, monkeypatch, nmap_present):
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
        config.set("network-sense-enabled", "false")
        assert config.sense_allowed_reason("network") in scan_network._run({})


class TestRangeBounds:
    @pytest.mark.parametrize("target", [
        "8.8.8.0/24", "1.1.1.1/32", "0.0.0.0/0", "2606:4700::/32", "93.184.216.0/24",
    ])
    def test_public_space_is_refused(self, target):
        refusal = scan_network._scannable(target)
        assert refusal is not None, f"{target} was accepted for scanning"
        assert "local network" in refusal

    @pytest.mark.parametrize("target", ["10.0.0.0/8", "172.16.0.0/12", "127.0.0.0/8"])
    def test_a_private_range_that_is_too_big_is_refused_with_a_count(self, target):
        refusal = scan_network._scannable(target)
        assert refusal is not None
        assert "254 max" in refusal, "did not say what the limit is"

    @pytest.mark.parametrize("target", [
        "192.168.1.0/24", "10.0.0.0/24", "192.168.31.128/25", "169.254.1.0/24",
        # The RFC 5737 documentation range. `is_private` means "not globally
        # reachable", so Python calls it private - correctly, since it is
        # reserved and unroutable, and scanning it reaches nobody.
        "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
        # IPv6 only works at a narrow prefix, because a /64 is 1.8e19 addresses.
        "fd00::/124", "fd00:1::/120",
    ])
    def test_a_sensible_non_public_range_is_accepted(self, target):
        assert scan_network._scannable(target) is None

    @pytest.mark.parametrize("target", ["fe80::/64", "2001:db8::/32"])
    def test_an_ipv6_lan_prefix_is_refused_for_its_size(self, target):
        """A /64 is the standard IPv6 LAN prefix and holds 1.8e19 addresses, so
        it cannot be enumerated - that is the design of IPv6, not a limitation
        to be fixed by raising the cap. Pinned so the cap is not raised.
        """
        import ipaddress
        assert ipaddress.ip_network(target).is_private, "premise: it is private"
        refusal = scan_network._scannable(target)
        assert refusal is not None
        assert "254 max" in refusal
        assert "Narrow the range" in refusal, "did not say how to proceed"

    @pytest.mark.parametrize("target", ["not-a-range", "", "999.1.1.1/24", "192.168.1.0/99"])
    def test_a_malformed_range_is_named_not_crashed_on(self, target):
        refusal = scan_network._scannable(target)
        assert refusal is not None
        assert "not a valid network range" in refusal

    def test_a_refused_range_stops_the_scan(self, monkeypatch, permitted, nmap_present):
        seen = _nmap(monkeypatch)
        out = scan_network._run({"range": "8.8.8.0/24"})
        assert "Refusing" in out
        assert seen == {}, "a refused range was still scanned"


class TestMissingNmap:
    def test_a_missing_binary_is_not_an_empty_network(self, monkeypatch, permitted):
        monkeypatch.setattr(scan_network.shutil, "which", lambda n: None)
        out = scan_network._run({})
        assert "`nmap` is not installed" in out
        assert "not the same as finding no devices" in out

    def test_an_undeterminable_subnet_asks_for_a_range(self, monkeypatch, permitted, nmap_present):
        monkeypatch.setattr(scan_network, "_local_subnet", lambda: None)
        out = scan_network._run({})
        assert "Could not work out which network" in out
        assert "range=" in out, "did not tell the caller how to proceed"


class TestPartialResults:
    def test_exit_1_still_reports_the_hosts_that_answered(self, monkeypatch, permitted, nmap_present):
        """nmap exits 1 when some hosts did not respond. Treating any non-zero
        status as failure discards the results it did print."""
        _nmap(monkeypatch, rc=1, out=NMAP_NO_DNS)
        out = scan_network._run({"range": "192.168.31.0/24"})
        assert "2 host(s) responded" in out
        assert "192.168.31.1" in out
        assert "Some hosts did not respond" in out

    def test_a_hard_failure_is_not_mistaken_for_a_clean_scan(self, monkeypatch, permitted, nmap_present):
        _nmap(monkeypatch, rc=2, err="nmap: you need to be root")
        out = scan_network._run({"range": "192.168.31.0/24"})
        assert "nmap failed" in out
        assert "you need to be root" in out

    def test_a_timeout_does_not_look_like_a_small_network(self, monkeypatch, permitted, nmap_present):
        _nmap(monkeypatch, raises=subprocess.TimeoutExpired(["nmap"], 120))
        out = scan_network._run({"range": "192.168.31.0/24"})
        assert "Giving up" in out
        assert "unknown" in out
        assert "not a short network" in out

    def test_an_empty_result_is_not_an_empty_network(self, monkeypatch, permitted, nmap_present):
        _nmap(monkeypatch, out="Nmap done: 0 IP addresses (0 hosts up)")
        out = scan_network._run({"range": "192.168.31.0/24"})
        assert "does not mean the network is empty" in out


class TestParsing:
    def test_vendor_and_mac_are_read_from_the_lines_that_carry_them(self):
        """They are not on the 'scan report' line. Reading only that line gives
        bare IPs and throws away the vendor that identifies the device."""
        hosts = scan_network._parse_hosts(NMAP_NO_DNS.splitlines())
        assert hosts[0]["address"] == "192.168.31.1"
        assert hosts[0]["vendor"] == "Tp-Link Technologies Co., Ltd."
        assert hosts[0]["mac"] == "3C:52:82:11:22:33"
        assert hosts[1]["vendor"] == "Apple, Inc."

    def test_resolved_names_are_split_from_the_address(self):
        hosts = scan_network._parse_hosts(NMAP_WITH_DNS.splitlines())
        assert hosts[0]["name"] == "router.lan"
        assert hosts[0]["address"] == "192.168.31.1"
        assert hosts[1]["name"] == "iphone.local"
        assert hosts[1]["address"] == "192.168.31.128"

    def test_an_unresolvable_address_is_not_given_an_empty_name_field_as_if_named(self):
        hosts = scan_network._parse_hosts(NMAP_NO_DNS.splitlines())
        assert all(h["name"] == "" for h in hosts)

    def test_unreadable_hardware_addresses_are_stated_not_glossed(self, monkeypatch, permitted, nmap_present):
        _nmap(monkeypatch, out=NMAP_NO_MAC)
        out = scan_network._run({"range": "192.168.31.0/24"})
        assert "No hardware addresses were read" in out
        assert "not the same as a network of generic hardware" in out

    def test_the_unresolved_hint_appears_only_when_names_were_actually_skipped(self):
        without = scan_network._describe(NMAP_NO_DNS.splitlines(), False)
        assert "resolve_names=true" in without
        with_names = scan_network._describe(NMAP_WITH_DNS.splitlines(), True)
        assert "resolve_names=true" not in with_names

    def test_reverse_dns_is_off_unless_asked_for(self, monkeypatch, permitted, nmap_present):
        seen = _nmap(monkeypatch)
        scan_network._run({"range": "192.168.31.0/24"})
        assert "-n" in seen["cmd"], "reverse DNS was on by default, disclosing each host"
        seen = _nmap(monkeypatch)
        scan_network._run({"range": "192.168.31.0/24", "resolve_names": True})
        assert "-n" not in seen["cmd"]

    def test_the_scan_is_host_discovery_only_not_a_port_scan(self, monkeypatch, permitted, nmap_present):
        """A port sweep is a different and far more intrusive request; it must
        not arrive by default from a question about what is on the network."""
        seen = _nmap(monkeypatch)
        scan_network._run({"range": "192.168.31.0/24"})
        assert "-sn" in seen["cmd"]
        for flag in ("-p", "-sS", "-sV", "-A", "-O"):
            assert flag not in seen["cmd"], f"{flag} turns this into a port scan"


class TestRegistry:
    def test_it_is_discovered_and_grouped(self):
        tools, _ = discover_skills()
        names = [t["function"]["name"] for t in tools]
        assert "scan_network" in names, "the skill loads but is not in the registry"
        grouped = {c.tool: c.group for c in capabilities.find_capabilities(tools)}
        assert grouped.get("scan_network"), "scan_network is ungrouped, so help hides it"
        assert grouped["scan_network"] in capabilities.GROUP_ORDER

    def test_its_schema_is_valid(self):
        schema = scan_network._SCHEMA
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "scan_network"
        props = schema["function"]["parameters"]["properties"]
        assert set(props) == {"range", "resolve_names"}
        assert props["resolve_names"]["default"] is False
