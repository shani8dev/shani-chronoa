"""`check_port`: can I reach that host and port?

`ping_host` answers ICMP, `dns_lookup` resolves a name, `check_internet`
answers "is the network up". None of them answers *"is my server reachable"*
or *"is port 443 open on that box"* - and those are among the commonest
questions anyone asks a machine that holds a service.

**The trap, measured.** `nc -z` reports:

    open   -> rc=0, stderr "Connection to 127.0.0.1 54005 port [tcp/*] succeeded!"
    closed -> **rc=1, and stderr is EMPTY**

So the phrase that tells a person the most - *connection refused*, meaning the
host is **up** and the **port** is the problem - is not in `nc`'s output at
all, and `rc` alone cannot distinguish "refused" from "timed out" because both
are 1. The verdict therefore comes from a connection the skill makes itself,
and `nc` supplies only the success case.

**It is a probe of this machine's path to one host, not a scan.** One address,
one port, one connection, nothing sent down it.
"""

from __future__ import annotations

import pathlib
import socket
import subprocess
import sys
import threading

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import check_port as CP  # noqa: E402


@pytest.fixture
def listener():
    """A real socket on a real ephemeral port."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    threading.Thread(target=_accept_once, args=(server,), daemon=True).start()
    yield server.getsockname()[1]
    server.close()


def _accept_once(server):
    try:
        server.accept()
    except OSError:
        pass


def test_an_open_port_is_reported_as_listening(tmp_path, monkeypatch, listener):
    """The real `nc` against a real listening socket, not a stub."""
    out = CP._run({"host": "127.0.0.1", "port": listener})
    assert "accepting connections" in out
    assert str(listener) in out


def test_a_closed_port_says_refused_not_just_failed(tmp_path, monkeypatch):
    """**rc=1 with empty stderr is the measured shape of a closed port.** The
    reason has to come from a connection of our own, because nc says nothing -
    and "refused" is the word that separates "the host is up, the service is
    not" from "the host is gone".
    """
    closed = _closed_port()
    out = CP._run({"host": "127.0.0.1", "port": closed})
    assert "refused" in out
    assert "host answered and said no" in out
    assert "did NOT accept" in out


def test_refused_is_reported_as_the_good_news_it_is(tmp_path, monkeypatch):
    """A refused connection means the machine is up. Saying only "failed"
    would make the most benign outcome read as the worst one.
    """
    out = CP._run({"host": "127.0.0.1", "port": _closed_port()})
    assert "usually the good news" in out


def test_an_unresolvable_name_is_a_name_problem_not_a_port_problem(tmp_path):
    """`nc` cannot tell these apart; the skill resolves the name first, so the
    answer names the actual fault.
    """
    out = CP._run({"host": "nonexistent.invalid.example", "port": 80})
    assert "cannot find the address" in out
    assert "name problem, not a port problem" in out


def test_a_timeout_is_not_reported_as_a_refusal(tmp_path, monkeypatch):
    """All three closed-port verdicts are rc=1 from `nc`, so a substitution
    here would report a firewalled host as one with a stopped service - the
    opposite of the useful advice.
    """
    def timed_out(*a, **k):
        raise socket.timeout()

    monkeypatch.setattr(CP.socket, "create_connection", timed_out)
    out = CP._run({"host": "127.0.0.1", "port": 9})
    assert "timed out" in out
    assert "firewall" in out
    assert "refused" not in out


def test_it_opens_exactly_one_connection_and_sends_nothing(tmp_path, monkeypatch):
    """One host, one port, one connection. A scan is a different skill and a
    different consent question.
    """
    calls = []

    def record(command, *a, **k):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(CP.subprocess, "run", record)
    CP._run({"host": "127.0.0.1", "port": 9})
    assert len(calls) == 1, calls
    argv = calls[0]
    assert argv[:2] == ["nc", "-z"]
    assert argv[-2:] == ["127.0.0.1", "9"]


def test_a_port_out_of_range_is_refused(tmp_path):
    for bad in ("0", "70000", "-1"):
        out = CP._run({"host": "127.0.0.1", "port": bad})
        assert "not a port" in out


def test_a_non_numeric_port_is_refused(tmp_path):
    out = CP._run({"host": "127.0.0.1", "port": "http"})
    assert "not a port number" in out


def test_missing_arguments_ask_for_both(tmp_path):
    out = CP._run({})
    assert "host and a port" in out
    assert CP._run({"host": "127.0.0.1"}).count("host and a port") == 1


def test_a_missing_nc_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setattr(CP.shutil, "which", lambda name: None)
    out = CP._run({"host": "127.0.0.1", "port": 9})
    assert "nc" in out


def test_a_hung_nc_is_not_a_closed_port(tmp_path, monkeypatch):
    """A check that ran out of time has said nothing about the port, and
    reporting it as closed would be the confident-wrong shape.
    """

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("nc", 15)

    monkeypatch.setattr(CP.subprocess, "run", boom)
    out = CP._run({"host": "127.0.0.1", "port": 9})
    assert "longer than" in out
    assert "closed" not in out.lower()


def _closed_port() -> int:
    """A port nothing is listening on: bound then released."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    return port