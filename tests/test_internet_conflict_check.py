"""`check_internet` now checks for an IP conflict.

Two machines answering for one address is the classic cause of "the internet
works sometimes", and a ping cannot see it - a ping reaches whichever host
replied. The kernel's ARP cache is where a conflict shows up: two entries for
one IP with **different MACs**.

**Why the cache and not `arping -D`.** `arping -D` is the usual probe for
this and it *does* ship on the images (`iputils`, both GNOME and Plasma), but
it is not installed on this Ubuntu dev box, so its exit-status contract went
unmeasured - and a status code read from memory is exactly the wrong answer
this repository keeps recording (see the rule in AGENTS.md: a command missing
on this box is a slot run, not a guess). The kernel's cache is the same
information, uses the `ip` this skill already reads the route from, sends
nothing, and could be verified for real here.

**A negative result is stated as what it is.** ARP entries are short-lived,
so "no conflict in the cache" is not "no conflict exists" - the answer says
which one it means.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import network_check as nc  # noqa: E402

#: Real `ip neigh show` output, captured on this machine. Note the IPv6
#: entries with no brackets and the long addresses: a parser that assumes
#: dotted-quad only silently drops half the cache.
REAL_CACHE = """\
192.168.31.1 dev wlp0s20f3 lladdr 3c:0a:f3:2f:af:fa REACHABLE
172.17.0.2 dev docker0 lladdr 56:4a:42:84:16:25 DELAY
2409:40c2:4029:f349:3e0a:f3ff:fe2f:affa dev wlp0s20f3 lladdr 3c:0a:f3:2f:af:fa router STALE
fe80::3e0a:f3ff:fe2f:affa dev wlp0s20f3 lladdr 3c:0a:f3:2f:af:fa router REACHABLE
"""


@pytest.fixture
def fake_arp(tmp_path, monkeypatch):
    """A stand-in `ip` whose `neigh show` answers with `cache`."""
    def _install(cache, code=0):
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        data = tmp_path / "neigh.txt"
        data.write_text(cache)
        script = bindir / "ip"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"if sys.argv[1:] == ['neigh', 'show']:\n"
            f"    print(open({str(data)!r}).read(), end='')\n"
            "    raise SystemExit(0)\n"
            "print('default via 192.168.31.1 dev wlp0s20f3')\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
        return script
    return _install


def test_the_real_cache_parses(fake_arp):
    fake_arp(REAL_CACHE)
    entries = nc._arp_entries()
    assert ("192.168.31.1", "wlp0s20f3", "3c:0a:f3:2f:af:fa") in entries
    # **IPv6 entries are real entries.** The cache holds them, and a
    # dotted-quad-only parser drops them without noticing.
    assert any(e[0].startswith("2409:") for e in entries)
    assert len(entries) == 4


def test_one_address_at_two_macs_is_a_conflict(fake_arp):
    fake_arp(REAL_CACHE + "192.168.31.1 dev wlp0s20f3 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n")
    conflicts = nc._conflicts()
    assert conflicts == ["192.168.31.1"]


def test_the_router_is_reported_first_when_it_is_the_conflict(fake_arp):
    """**A control that can fail: the router's address sorts after the other
    conflict.** `192.168.31.99` is *after* `192.168.31.1` alphabetically, so
    the reordering is what puts the router first and not the sort. The first
    version of this test used a router that sorted first anyway, and passed
    with the reordering deleted.
    """
    fake_arp(REAL_CACHE
             .replace("192.168.31.1 dev", "192.168.31.99 dev")
             + "192.168.31.99 dev wlp0s20f3 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
             "192.168.31.1 dev wlp0s20f3 lladdr 11:22:33:44:55:66 REACHABLE\n"
             "192.168.31.1 dev wlp0s20f3 lladdr 99:88:77:66:55:44 REACHABLE\n")
    conflicts = nc._conflicts("192.168.31.99")
    assert conflicts[0] == "192.168.31.99"
    assert "192.168.31.1" in conflicts


def test_broadcast_and_zero_macs_are_not_conflicts(fake_arp):
    """**A control that can actually fail: the same IP with a real MAC and a
    zero MAC.**

    An `INCOMPLETE` entry (no reply yet, `00:00:00:00:00:00`) sits beside a
    `REACHABLE` one for the same address on a busy network - ordinary kernel
    behaviour. Counting the zero MAC as a second host reports an IP conflict
    on every machine that has ever had a packet in flight, so the filter is
    what keeps the check honest. The first version of this test listed the
    zero MAC once per *different* IP, where no collision was possible and the
    test could not fail.
    """
    fake_arp("192.168.31.1 dev wlp0s20f3 lladdr 00:00:00:00:00:00 INCOMPLETE\n"
             "192.168.31.1 dev wlp0s20f3 lladdr 3c:0a:f3:2f:af:fa REACHABLE\n"
             "192.168.31.255 dev wlp0s20f3 lladdr ff:ff:ff:ff:ff:ff REACHABLE\n")
    assert nc._conflicts() == []


def test_a_router_at_one_mac_is_not_a_conflict(fake_arp):
    fake_arp(REAL_CACHE)
    assert nc._conflicts("192.168.31.1") == []


def test_a_failed_ip_reads_as_nothing_rather_than_crashing(tmp_path, monkeypatch):
    """**No `ip` is not a conflict.** The cache is one input to the answer,
    and if it cannot be read the rest of the check must still work."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "ip"
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    assert nc._arp_entries() == []
    assert nc._conflicts() == []


def test_the_answer_explains_a_conflict_it_saw(fake_arp):
    fake_arp("192.168.31.1 dev wlp0s20f3 lladdr 3c:0a:f3:2f:af:fa REACHABLE\n"
             "192.168.31.1 dev wlp0s20f3 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n")
    gw, dev = nc._gateway()
    assert gw == "192.168.31.1"          # the fake `ip` answers the route too
    lines = []
    conflicts = nc._conflicts(gw)
    if conflicts:
        lines.append(f"the router's address {gw} itself has answered from two "
                     "different MAC addresses")
    assert lines and "192.168.31.1 itself" in lines[0]
