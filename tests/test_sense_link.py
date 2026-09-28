"""The `link` sense, and the three shapes of "no speed reading".

Measured on this machine with no root, which is the whole point of the module:

    $ ethtool enp4s0            # a wired port that is down
        Speed: Unknown!
        Duplex: Unknown! (255)
        Link detected: no
    $ ethtool wlp0s20f3          # a wireless link that is up
        Link detected: yes
        (no Speed line at all)

    $ cat /sys/class/net/enp4s0/speed      -> -1
    $ cat /sys/class/net/wlp0s20f3/speed   -> (empty)
    $ cat /sys/class/net/wlp0s20f3/operstate -> up

So there are three distinct absences — `Unknown!` from a tool, `-1` from
sysfs, an empty file from sysfs — and a fourth case that is *not* an absence:
a real link speed. A parser that greps for `Speed:` and converts gets a
confident wrong answer out of all four.
"""

import pytest

from shani_chronoa.senses import link


def _iface(root, name, *, operstate="up", carrier="1", speed=None, duplex=None,
           mtu="1500", address=None, driver=None, firmware=None):
    entry = root / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "operstate").write_text(operstate + "\n")
    (entry / "carrier").write_text(carrier + "\n")
    (entry / "mtu").write_text(mtu + "\n")
    (entry / "address").write_text((address or "02:00:00:00:00:01") + "\n")
    for field, value in (("speed", speed), ("duplex", duplex)):
        if value is not None:
            (entry / field).write_text(f"{value}\n")
    if driver:
        (entry / "device").mkdir(exist_ok=True)
        (entry / "device" / "driver").mkdir(exist_ok=True)
        (entry / "device" / "driver" / "module").write_text("e1000e\n")
    return entry


@pytest.fixture
def root(tmp_path, monkeypatch):
    net = tmp_path / "net"
    net.mkdir()
    _iface(net, "lo", operstate="unknown", carrier="0")
    monkeypatch.setattr(link, "_NET", net)
    monkeypatch.setattr(link.shutil, "which", lambda name: None)
    return net


class TestTheAbsentSpeedCases:
    def test_a_wired_port_that_is_down_reports_no_speed(self, root):
        """sysfs says -1 and ethtool says Unknown. Neither is 0 Mb/s."""
        _iface(root, "enp4s0", operstate="down", carrier="0", speed=-1,
               duplex="unknown")
        (record,) = [r for r in link.read_links() if r["interface"] == "enp4s0"]
        assert "speed_mbps" not in record
        assert record["speed"] is None

    def test_a_wireless_link_that_is_up_still_reports_no_speed(self, root):
        """The empty-file case, and the one most likely to be faked: the link is
        genuinely up and carrying traffic."""
        _iface(root, "wlp0s20f3", operstate="up", carrier="1", speed="")
        (record,) = [r for r in link.read_links() if r["interface"] == "wlp0s20f3"]
        assert record["operstate"] == "up"
        assert "speed_mbps" not in record
        assert record["speed"] is None

    def test_a_real_speed_is_reported(self, root):
        _iface(root, "eth0", speed=1000, duplex="full")
        (record,) = [r for r in link.read_links() if r["interface"] == "eth0"]
        assert record["speed_mbps"] == 1000
        assert record["duplex"] == "full"

    def test_a_zero_speed_is_not_a_measurement(self, root):
        """0 Mb/s is not a link speed; it is an absence wearing a number."""
        _iface(root, "eth0", speed=0)
        (record,) = [r for r in link.read_links() if r["interface"] == "eth0"]
        assert "speed_mbps" not in record

    def test_an_unknown_duplex_is_omitted_not_passed_through(self, root):
        _iface(root, "eth0", speed=1000, duplex="unknown")
        (record,) = [r for r in link.read_links() if r["interface"] == "eth0"]
        assert "duplex" not in record


class TestEthtoolParsing:
    def _with_ethtool(self, root, monkeypatch, stdout, path=None):
        monkeypatch.setattr(link.shutil, "which", lambda n: "/usr/sbin/ethtool")
        import subprocess

        class Result:
            pass

        def fake_run(argv, **kwargs):
            r = Result()
            r.stdout = stdout
            r.stderr = ""
            return r

        monkeypatch.setattr(link.subprocess, "run", fake_run)
        return path

    def test_the_real_ethtool_output_of_a_down_port(self, root, monkeypatch):
        """Verbatim from this machine. `Unknown!` must not become 0."""
        self._with_ethtool(root, monkeypatch, """Settings for enp4s0:
	Speed: Unknown!
	Duplex: Unknown! (255)
	Link detected: no
""")
        _iface(root, "enp4s0", operstate="down", carrier="0", speed=-1)
        (record,) = [r for r in link.read_links() if r["interface"] == "enp4s0"]
        found = record["ethtool"]
        assert found["speed"] is None
        assert found["carrier"] is False
        assert "speed_mbps" not in record

    def test_the_real_ethtool_output_of_a_wireless_link(self, root, monkeypatch):
        """Also verbatim: a wireless interface emits no Speed line at all, so a
        parser keyed on that line simply finds nothing and must not invent 0."""
        self._with_ethtool(root, monkeypatch, """Settings for wlp0s20f3:
	Link detected: yes
""")
        _iface(root, "wlp0s20f3", operstate="up", carrier="1", speed="")
        (record,) = [r for r in link.read_links() if r["interface"] == "wlp0s20f3"]
        assert record["ethtool"]["carrier"] is True
        assert "speed_mbps" not in record

    def test_a_real_gigabit_link(self, root, monkeypatch):
        self._with_ethtool(root, monkeypatch, """Settings for eth0:
	Speed: 1000Mb/s
	Duplex: Full
	Port: Twisted Pair
	Link detected: yes
""")
        _iface(root, "eth0", speed=1000, duplex="full")
        (record,) = [r for r in link.read_links() if r["interface"] == "eth0"]
        assert record["speed_mbps"] == 1000
        assert record["ethtool"]["duplex"] == "Full"

    def test_ethtool_being_absent_is_not_a_link_failure(self, root, monkeypatch):
        monkeypatch.setattr(link.shutil, "which", lambda n: None)
        _iface(root, "eth0", speed=1000, duplex="full")
        (record,) = [r for r in link.read_links() if r["interface"] == "eth0"]
        assert "ethtool" not in record
        assert record["speed_mbps"] == 1000, "sysfs still works without the tool"


class TestBridgeDetection:
    def test_interfaces_sharing_a_hardware_address_are_flagged(self, root, monkeypatch):
        """A bridge, bond or container network otherwise looks like several
        separate links to several places."""
        _iface(root, "br0", address="02:00:00:00:00:aa")
        _iface(root, "veth1", address="02:00:00:00:00:aa")
        _iface(root, "eth0", address="02:00:00:00:00:bb")
        monkeypatch.setattr(link.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        content = link._run({}).content
        assert "share a hardware address" in content
        assert "02:00:00:00:00:aa" in content
        assert "02:00:00:00:00:bb" not in content.split("share a hardware")[0]

    def test_distinct_addresses_are_not_flagged(self, root, monkeypatch):
        _iface(root, "eth0", address="02:00:00:00:00:01")
        _iface(root, "eth1", address="02:00:00:00:00:02")
        monkeypatch.setattr(link.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        assert "share a hardware address" not in link._run({}).content

    def test_loopback_is_never_listed(self, root, monkeypatch):
        _iface(root, "eth0")
        names = {r["interface"] for r in link.read_links()}
        assert "lo" not in names


class TestEndToEnd:
    def test_it_explains_a_missing_speed_rather_than_reporting_zero(self, root, monkeypatch):
        _iface(root, "wlp0s20f3", operstate="up", carrier="1", speed="")
        monkeypatch.setattr(link.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        percept = link._run({})
        assert "reports no speed" in percept.content
        assert "neither is a measurement" in percept.content
        assert percept.metadata["with_speed"] == 0

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(link.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(link._run({}), str)

    def test_an_unreadable_sysfs_is_not_claimed_to_mean_no_network(self, tmp_path, monkeypatch):
        """A missing `/sys/class/net` is a fact about this process's
        permissions, not about the machine having no network — so it is
        returned as prose, and `read_links()` is what distinguishes empty from
        unreadable."""
        monkeypatch.setattr(link, "_NET", tmp_path / "does-not-exist")
        monkeypatch.setattr(link.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        result = link._run({})
        assert isinstance(result, str), "an empty list must not read as a Percept"
        assert "fact about what could be read" in result
        assert link.read_links() == []
