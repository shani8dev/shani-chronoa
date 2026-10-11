"""Tests for `network_activity`, and the two measurements that shaped it.

The fixtures are **captured from a real ShaniOS `@blue` slot**, because the
whole design turns on what that machine actually did:

| measured | what it forces |
|---|---|
| `nethogs -t -c 3` returned six lines of `unknown TCP/0/0<TAB>0<TAB>0` **while `curl` was downloading 100 MB** | rc=0 is not a measurement, and "no rows" is not "idle" |
| the slot has **3 processes**, and `nethogs` maps a socket by reading `/proc/<pid>/fd` | the attribution failure is the *machine*, so it must be named as such |
| `/proc/net/dev` twice, 2s apart, during live traffic: `wlp0s20f3: rx=14822B tx=993686B` | interface rates need no binary and no privilege, and are the floor |
| a real row: `192.168.31.202:40782-64.239.109.193:443/0/0<TAB>98.5879<TAB>1.31367` | tab-separated, KB, and the `-` delimiter is unsafe to split on |
| the same dev box attributes **14 of 29** sockets to a pid, unprivileged | the join is real, not theoretical |

That second row is the one worth stating plainly: on the slot **every** nethogs
row is unattributed, and reporting those as zero-rate connections would tell a
person with a saturated link that nothing is happening.
"""

from __future__ import annotations

import signal
import subprocess

import pytest

from shani_chronoa.skills import network_activity as na
from shani_chronoa.skills import port_owner

# --- captured: nethogs -t on the real slot, with curl pulling 100 MB --------
NETHOGS_ALL_UNKNOWN = """\
Adding local address: 192.168.31.202
Adding local address: 2409:40c2:122e:50e0:1c58:4de1:6848:aa46
Ethernet link detected

Refreshing:
unknown TCP/0/0\t0\t0

Refreshing:
unknown TCP/0/0\t0\t0

Refreshing:
unknown TCP/0/0\t0\t0
"""

# --- captured: the same command with traffic flowing and rows attributed -----
NETHOGS_WITH_ROWS = """\
Adding local address: 192.168.31.202
Ethernet link detected

Refreshing:
192.168.31.202:40782-64.239.109.193:443/0/0\t98.5879\t1.31367
192.168.31.202:52802-172.65.90.23:443/0/0\t0.247266\t1.31582
2409:40c2:122e:50e0:1c58:4de1:6848:aa46:33018-2607:6bc0::10:443/0/0\t0.0398437\t0.0390625
unknown TCP/0/0\t0\t0
"""


@pytest.fixture
def nethogs(monkeypatch):
    """Answer `nethogs` by outcome."""

    def install(out="", rc=0, err="", present=True, raises=None):
        monkeypatch.setattr(na.shutil, "which",
                            lambda n: "/usr/bin/nethogs" if present else None)
        calls = []

        def run(argv, **kwargs):
            calls.append(list(argv))
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(argv, rc, out, err)
        monkeypatch.setattr(na.subprocess, "run", run)
        return calls
    return install


@pytest.fixture
def netdev(monkeypatch):
    """Two readings of `/proc/net/dev`, or a refusal."""

    def install(first=None, second=None, error=None):
        reads = []

        def fake_sleep(_seconds):
            return None

        def fake_open(path, **kwargs):
            if error is not None:
                raise OSError(error)
            if path != "/proc/net/dev":
                raise OSError(f"unexpected read of {path}")
            body = first if not reads else second
            reads.append(body)
            return _TextFile(body or "")

        monkeypatch.setattr(na, "open", fake_open, raising=False)
        monkeypatch.setattr("builtins.open", fake_open)
        monkeypatch.setattr(na.time, "sleep", fake_sleep)
        return reads
    return install


class _TextFile:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._text


# --- captured shape of /proc/net/dev ---------------------------------------
NETDEV_BUSY = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 11007     112    0    0    0     0          0         0     11007     112    0    0    0     0       0          0
 wlp0s20f3: 14822     96    0    0    0     0          0         0    993686    741    0    0    0     0       0          0
"""

NETDEV_QUIET = NETDEV_BUSY.replace("wlp0s20f3: 14822     96    0    0    0     0          0         0    993686    741",
                                   "wlp0s20f3: 15000     98    0    0    0     0          0         0    995000    743")

#: A 64-bit counter wrapping between the two readings. The subtraction goes
#: negative, and the honest answer is "unknown" rather than a huge rate.
NETDEV_WRAPPED = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 11007     112    0    0    0     0          0         0     11007     112    0    0    0     0       0          0
 wlp0s20f3: 5        1    0    0    0     0          0         0    7         1    0    0    0     0       0          0
"""


# --------------------------------------------------------------------------
# nethogs finding nothing is not the machine finding nothing
# --------------------------------------------------------------------------

class TestNoRowsIsNotAnIdleMachine:

    def test_all_unknown_rows_are_reported_as_an_attribution_failure(
            self, nethogs):
        # **The defect this skill exists to not have.** Measured: six lines of
        # `unknown TCP/0/0` while a 100 MB download was in flight.
        nethogs(out=NETHOGS_ALL_UNKNOWN)
        rows, why = na._nethogs()
        assert rows == []
        assert "attributed none of its" in why
        assert "/proc/<pid>/fd" in why, (
            "the reason has to name the mechanism, or the person is told "
            "their network is idle")

    def test_unknown_rows_are_never_returned_as_zero_rate_connections(
            self, nethogs):
        nethogs(out=NETHOGS_ALL_UNKNOWN)
        rows, _ = na._nethogs()
        assert all(r[2] or r[3] for r in rows), (
            "a row with no program behind it is not a connection with zero "
            "traffic")

    def test_a_real_zero_rate_row_is_still_a_row(self, nethogs):
        # The converse must hold, or the rule above would hide a real
        # connection that happens to be idle at this instant.
        out = "192.168.31.202:40000-1.2.3.4:443/0/0\t0.0\t0.0\n"
        nethogs(out=out)
        rows, why = na._nethogs()
        assert len(rows) == 1 and why == ""

    def test_rc_zero_with_no_output_is_not_reported_as_idle(self, nethogs):
        nethogs(out="")
        rows, why = na._nethogs()
        assert rows == []
        assert "no connections at all" in why
        assert "not" not in why.split("not")[0][-20:]

    def test_it_says_which_half_is_missing_not_just_that_something_is(
            self, nethogs):
        nethogs(out=NETHOGS_ALL_UNKNOWN)
        out = na._describe()
        assert "Per-program rates: not known" in out
        assert "interface numbers above are still real" in out, (
            "dropping the interface half would lose a measurement that WAS "
            "taken")


# --------------------------------------------------------------------------
# The crash trap, which nethogs shares with every tool that links libpcap
# --------------------------------------------------------------------------

class TestACrashIsNotAnEmptyResult:

    def test_a_signalled_tool_is_named_as_a_crash(self, nethogs):
        nethogs(rc=-signal.SIGSEGV)
        rows, why = na._nethogs()
        assert rows == []
        assert "CRASHED" in why and "SIGSEGV" in why
        assert "not known" in why

    def test_a_crash_is_not_the_same_word_as_a_refusal(self, nethogs):
        nethogs(rc=-signal.SIGSEGV)
        crash = na._nethogs()[1]
        nethogs(rc=1, err="no permission to capture\n")
        refusal = na._nethogs()[1]
        assert crash != refusal
        assert "permission" in refusal

    def test_an_ordinary_non_zero_exit_is_not_called_a_crash(self, nethogs):
        nethogs(rc=1, err="nethogs: requires CAP_NET_RAW\n")
        _, why = na._nethogs()
        assert "CRASHED" not in why

    def test_a_missing_nethogs_names_its_package(self, nethogs):
        nethogs(present=False)
        rows, why = na._nethogs()
        assert rows == [] and "nethogs package" in why


# --------------------------------------------------------------------------
# Parsing the real line shape
# --------------------------------------------------------------------------

class TestParsingTheRealRow:

    def test_every_fixture_row_actually_parses(self, nethogs):
        """**Added because the shipped regex matched nothing at all.**

        The first version anchored its numeric columns and asked for `\\S+?`
        fields on the left, which failed on *every* input - and three tests
        still passed, because they asserted on the surrounding behaviour (the
        banner lines being skipped, the crash being named) and never on a row
        being read. A parser that never matches is indistinguishable from a
        quiet machine, so the fixture is asserted to yield exactly the three
        real connections in it.
        """
        nethogs(out=NETHOGS_WITH_ROWS)
        rows, why = na._nethogs()
        assert why == ""
        assert len(rows) == 3, (
            f"parsed {len(rows)} rows from a fixture with 3 real connections")
        assert all(remote for _, remote, _, _ in rows)

    def test_a_tab_separated_row_is_parsed_into_three_values(self, nethogs):
        nethogs(out=NETHOGS_WITH_ROWS)
        rows, why = na._nethogs()
        assert why == ""
        sent = next(r for r in rows if "64.239.109.193" in r[1])
        assert sent[2] == pytest.approx(98.5879)
        assert sent[3] == pytest.approx(1.31367)

    def test_an_ipv6_pair_is_not_split_into_three(self, nethogs):
        # **The delimiter trap.** The pair is `local-remote`, and an IPv6 local
        # address is full of colons, so splitting on anything but the FIRST dash
        # destroys every IPv6 row. Measured: a real v6 row in the fixture above.
        nethogs(out=NETHOGS_WITH_ROWS)
        rows, _ = na._nethogs()
        six = [r for r in rows if "2607" in r[1]]
        assert len(six) == 1, f"IPv6 rows parsed as {len(six)}"
        assert six[0][1].startswith("2607:6bc0::10:443")

    def test_the_banner_lines_are_not_rows(self, nethogs):
        # `Adding local address:`, `Ethernet link detected`, `Refreshing:` all
        # appear in real output and none is a connection.
        nethogs(out=NETHOGS_WITH_ROWS)
        rows, _ = na._nethogs()
        assert all("Adding" not in r[1] for r in rows)
        assert all("Refreshing" not in r[1] for r in rows)

    def test_the_proto_and_pid_columns_are_not_part_of_the_remote(
            self, nethogs):
        nethogs(out=NETHOGS_WITH_ROWS)
        rows, _ = na._nethogs()
        for _, remote, _, _ in rows:
            assert "/0/0" not in remote

    def test_a_timeout_is_not_reported_as_no_traffic(self, nethogs):
        nethogs(raises=subprocess.TimeoutExpired(["nethogs"], 30))
        rows, why = na._nethogs()
        assert rows == [] and "sampling window" in why


# --------------------------------------------------------------------------
# Interface rates: the half that always works
# --------------------------------------------------------------------------

class TestInterfaceRates:

    def test_bytes_are_differenced_over_the_window(self, netdev):
        netdev(first=NETDEV_BUSY, second=NETDEV_QUIET)
        rates, why = na._interface_rates()
        assert why == ""
        rx, tx = rates["wlp0s20f3"]
        assert (rx, tx) == (178, 1314), "counters must be differenced, not read"

    def test_loopback_is_excluded_even_when_it_is_moving(self, netdev):
        # **This test used the same `lo` numbers in both readings, so `lo`
        # never moved and the exclusion was never exercised** - a mutation that
        # removed the `lo` filter left it green. The measured shape of the bug
        # is that `lo` doubles every byte and is always busy on a live machine,
        # so the second reading has to differ.
        # `lo` moves on BOTH halves (it doubles every packet) and the wifi
        # interface moves too, so the test can assert both "lo is excluded"
        # and "the real interface is still reported" - a fixture where only
        # `lo` moved would make the empty result ambiguous.
        second = NETDEV_BUSY.replace(
            "    lo: 11007     112    0    0    0     0          0         0     11007     112",
            "    lo: 999999     500    0    0    0     0          0         0     999999     500").replace(
            " wlp0s20f3: 14822     96", " wlp0s20f3: 24822     140")
        netdev(first=NETDEV_BUSY, second=second)
        rates, _ = na._interface_rates()
        assert rates, "the busy interface must still be reported"
        assert "lo" not in rates, (
            "loopback doubles every packet; leaving it in makes a quiet "
            "machine look busy")

    def test_a_quiet_interface_is_left_out_rather_than_shown_as_zero(
            self, netdev):
        quiet = NETDEV_BUSY.replace("wlp0s20f3: 14822", "wlp0s20f3: 14822") \
                          .replace("    993686", "    993686")
        netdev(first=NETDEV_BUSY, second=quiet)
        rates, _ = na._interface_rates()
        assert "wlp0s20f3" not in rates

    def test_a_wrapped_counter_is_unknown_not_a_huge_rate(self, netdev):
        netdev(first=NETDEV_BUSY, second=NETDEV_WRAPPED)
        rates, _ = na._interface_rates()
        assert all(rx >= 0 and tx >= 0 for rx, tx in rates.values())
        assert "wlp0s20f3" not in rates, (
            "a negative difference is a wrap, and reporting it as a "
            "terabyte-rate interface is the confident wrong answer")

    def test_an_unreadable_proc_is_named_not_swallowed(self, netdev):
        netdev(error="No such file or directory")
        rates, why = na._interface_rates()
        assert rates == {}
        assert "/proc/net/dev" in why

    def test_a_missing_counter_line_is_skipped_not_fatal(self, netdev):
        # A device added mid-window, or a malformed line, must not raise.
        truncated = NETDEV_BUSY.replace(
            " wlp0s20f3: 14822     96    0    0    0     0          0         0    993686    741    0    0    0     0       0          0",
            " wlp9: broken\n")
        netdev(first=NETDEV_BUSY, second=truncated)
        rates, _ = na._interface_rates()
        assert isinstance(rates, dict)


# --------------------------------------------------------------------------
# Attribution: measured, or said not to be
# --------------------------------------------------------------------------

class TestAttribution:

    def test_an_unattributed_socket_is_not_given_a_guess(self, nethogs,
                                                         monkeypatch):
        # The rates are real; the owner is not known. Claiming a program here
        # is a guess about who is spending the user's bandwidth.
        nethogs(out="192.168.31.202:40782-64.239.109.193:443/0/0\t98.5\t1.3\n")
        monkeypatch.setattr(na, "_owners_by_pair", lambda: ({}, []))
        out = na._describe()
        assert "unattributed socket" in out
        assert "98.5 KB" in out or "96.2 MB" in out or "98" in out

    def test_partial_attribution_says_so(self, nethogs, monkeypatch):
        nethogs(out=("192.168.31.202:40782-1.2.3.4:443/0/0\t98.5\t1.3\n"
                     "192.168.31.202:40783-5.6.7.8:443/0/0\t50.0\t0.5\n"))
        monkeypatch.setattr(na, "_owners_by_pair",
                            lambda: ({"192.168.31.202:40782-1.2.3.4:443": _owner()},
                                     []))
        out = na._describe()
        assert "could not be attributed" in out

    def test_a_named_program_is_shown_with_its_rate(self, nethogs,
                                                    monkeypatch):
        nethogs(out="192.168.31.202:40782-1.2.3.4:443/0/0\t98.5\t1.3\n")
        monkeypatch.setattr(na, "_owners_by_pair",
                            lambda: ({"192.168.31.202:40782-1.2.3.4:443": _owner()},
                                     []))
        out = na._describe()
        assert "curl" in out
        assert "MB" in out or "KB" in out


def _owner():
    return port_owner.Owner(1234, "curl", "curl -s https://example.org/big.bin")


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------

class TestItIsReachable:

    def test_the_handler_is_importable_by_name(self):
        assert na._run.__name__ == "_run"
        assert na._run.__name__.isidentifier()

    def test_the_schema_registers_under_its_own_name(self):
        assert na.SKILLS[0].name == "network_activity"
        assert na.SCHEMA["function"]["name"] == "network_activity"

    def test_it_takes_no_parameters(self):
        assert na.SCHEMA["function"]["parameters"]["properties"] == {}

    def test_it_reuses_port_owners_attribution_rather_than_rewalking_proc(self):
        # Two independent walks of /proc would be two answers to one question,
        # and this repo records that as a defect shape.
        src = open(na.__file__, encoding="utf-8").read()
        assert "port_owner._read_all()" in src
        assert '"/proc/[0-9]*/fd' not in src, (
            "the skill must not grow its own /proc walk beside the one "
            "port_owner already documents")

    def test_the_join_key_is_built_from_the_decoded_addresses(self, monkeypatch):
        """**The text scan above could not fail on the key itself**, because
        every test that exercised the join stubbed `_owners_by_pair` out. So
        the key is built here from a real `port_owner` socket and compared with
        what `_split_pair` produces from nethogs' own spelling - the two have to
        agree or every row silently reads as unattributed.
        """
        from shani_chronoa.skills.port_owner import Socket

        # **The real field names**, taken from `port_owner.Socket` itself
        # rather than from memory - it is a flat NamedTuple and a nested-pair
        # guess is exactly what raised `AttributeError` in the first version.
        row = Socket(kind="tcp", local="192.168.31.202", port=40782,
                     peer="64.239.109.193", peer_port=443,
                     state="ESTABLISHED", uid=0, inode=42)
        owner = port_owner.Owner(1234, "curl", "curl -s https://x/big.bin")
        monkeypatch.setattr(port_owner, "_read_all", lambda: ([row], []))
        monkeypatch.setattr(port_owner, "_attributes",
                            lambda sockets: {42: owner})
        by_pair, _ = na._owners_by_pair()
        expected = "192.168.31.202:40782-64.239.109.193:443"
        assert expected in by_pair, (
            f"the join key is {sorted(by_pair)}, which does not match "
            f"nethogs' own spelling")
        # And the row parser must produce that same key from nethogs' text.
        line = "192.168.31.202:40782-64.239.109.193:443/0/0\t98.5\t1.3"
        match = na._NETHOGS_ROW.match(line)
        assert match is not None
        local, remote = na._split_pair(match.group("pair"))
        assert f"{local}-{remote}" == expected