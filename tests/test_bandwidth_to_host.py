"""`bandwidth_to_host`: how fast is this machine to *that* machine?

`speed_test` measures this machine to the internet. This is the other
question - "my laptop is slow copying to the NAS", "is the switch the
bottleneck" - and iperf3 is the tool for it, shipping in shani-tools-network
on both images.

**Two measured shapes, both from a real `@blue` slot (iperf 3.21):**

    [  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec    0            sender
    [  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec                  receiver

    iperf3: error - unable to connect to server - server may have stopped   rc=1

**The `receiver` line is the one to quote.** `sender` is the sending host's
own view and `receiver` the far end's, and on a real network they differ -
with the receiver's being the one that reflects what actually arrived. Taking
the first match reports the more flattering of the two.

**And iperf3 needs a server on the far end.** There is no way to measure a
peer that is not listening, so "nothing is listening" is a *different answer*
from "the link is slow", and the refusal says which command the other machine
needs.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import bandwidth_to_host as BW  # noqa: E402

#: The captured transfer, verbatim, both roles.
TRANSFER = """[ ID] Interval           Transfer     Bitrate         Retr
[  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec    0            sender
[  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec                  receiver
"""

#: A real network, where sender and receiver disagree: the far end is what
#: matters and it is the slower number.
TRANSFER_LOSSY = """[ ID] Interval           Transfer     Bitrate         Retr
[  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec   12            sender
[  5]   0.00-2.00   sec  9.40 GBytes  40.4 Gbits/sec    3            receiver
"""

REFUSAL = "iperf3: error - unable to connect to server - server may have stopped\n"


def _fake_iperf3(tmp_path, monkeypatch, stdout=TRANSFER, rc=0, stderr=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "iperf3"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({rc})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_a_completed_transfer_reports_the_receivers_rate(tmp_path, monkeypatch):
    _fake_iperf3(tmp_path, monkeypatch)
    out = BW._run_skill({"host": "192.0.2.10", "seconds": 2})
    assert "far end received it at" in out
    assert "40.9 Gbit/s" in out


def test_the_receiver_is_chosen_not_the_first_line(tmp_path, monkeypatch):
    """**On a real network the two figures differ**, and the receiver's is the
    slower and truthful one. Taking the first match reports 40.9 where the
    data actually arrived at 40.4.
    """
    _fake_iperf3(tmp_path, monkeypatch, stdout=TRANSFER_LOSSY)
    out = BW._run_skill({"host": "192.0.2.10", "seconds": 2})
    # Asserted on the **label**, not only the value: "the receiver's rate is
    # present" is also true of a parser quoting the sender, which is what the
    # first version of this test allowed.
    assert "far end received it at **40.4 Gbit/s**" in out
    assert "reported sending at 40.9 Gbit/s" in out
    assert "what the network cost" in out


def test_nothing_listening_is_not_a_slow_link(tmp_path, monkeypatch):
    """Measured refusal, rc=1. Conflating it with a slow transfer sends
    someone looking at their network when nothing is running on the far end.
    """
    _fake_iperf3(tmp_path, monkeypatch, stdout="", rc=1, stderr=REFUSAL)
    out = BW._run_skill({"host": "192.0.2.10", "seconds": 2})
    assert "Nothing is listening" in out
    assert "iperf3 -s" in out
    assert "not" in out.lower()


def test_loopback_is_named_as_this_machines_own(tmp_path, monkeypatch):
    """Measuring 127.0.0.1 is a real measurement and not the network - so it
    is labelled, or a loopback figure reads as a link speed.
    """
    _fake_iperf3(tmp_path, monkeypatch)
    out = BW._run_skill({"host": "127.0.0.1", "seconds": 2})
    assert "loopback" in out


def test_gbit_rates_are_shown_in_readable_units(tmp_path, monkeypatch):
    """40.9 Gbits/sec is 40.9 Gbit/s, not 40900 Mbit/s; the unit is read from
    the tool's own text rather than assumed.
    """
    _fake_iperf3(tmp_path, monkeypatch)
    out = BW._run_skill({"host": "192.0.2.10", "seconds": 2})
    assert "Gbit/s" in out


def test_a_transfer_with_no_parsable_row_is_unknown(tmp_path, monkeypatch):
    """A zero exit with nothing this reads has proved nothing - which is
    different from a very slow network.
    """
    _fake_iperf3(tmp_path, monkeypatch, stdout="iperf3: warning: nothing to do\n")
    out = BW._run_skill({"host": "192.0.2.10", "seconds": 2})
    assert "did not run" in out or "printed no transfer line" in out


def test_a_hung_transfer_is_not_a_slow_one(tmp_path, monkeypatch):
    _fake_iperf3(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("iperf3", 120)

    monkeypatch.setattr(BW.subprocess, "run", boom)
    out = BW._run_skill({"host": "192.0.2.10", "seconds": 2})
    assert "did not finish" in out


def test_arguments_are_bounded(tmp_path, monkeypatch):
    _fake_iperf3(tmp_path, monkeypatch)
    assert "outside the 1-60s range" in BW._run_skill({"seconds": 999})
    assert "not a port number" in BW._run_skill({"port": "http"})
    assert "not a number of seconds" in BW._run_skill({"seconds": "soon"})


def test_a_missing_iperf3_names_its_real_package(tmp_path, monkeypatch):
    """The hint is `iperf3`, so the sentence is a package someone can
    install - not "the 'the package that provides it' package", which is what
    an entry-less table produces.
    """
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = BW._run_skill({})
    assert "'iperf3' package" in out
    assert "the package that provides it" not in out


def test_it_sends_no_data_the_caller_did_not_ask_for(tmp_path, monkeypatch):
    """A measurement tool that also copied something would be a very
    expensive way to find out how fast the disk is.
    """
    log = tmp_path / "argv"
    script = tmp_path / "bin" / "iperf3"
    script.parent.mkdir(exist_ok=True)
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.write({TRANSFER!r})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    BW._run_skill({"host": "192.0.2.10", "seconds": 2})
    argv = log.read_text().split()
    for flag in ("-u", "--udp", "-R", "-b", "-n", "-l", "-f"):
        assert flag not in argv, f"bandwidth_to_host must not send {flag}"
    assert argv[:2] == ["-c", "192.0.2.10"]