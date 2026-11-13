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
are 1. The verdict therefore comes from a connection the skill makes itself -
and since 2026-11-13 that connection is the whole check: `nc` is not run, and
openbsd-netcat is no longer needed for it.

**It is a probe of this machine's path to one host, not a scan.** One address,
one port, one connection, nothing sent down it.
"""

from __future__ import annotations

import pathlib
import shutil
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
    """A real connection to a real listening socket, not a stub."""
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


def test_it_opens_exactly_one_connection_and_runs_nothing(tmp_path, monkeypatch):
    """One host, one port, one connection, and no program started for it.
    A scan is a different skill and a different consent question.
    """
    calls = []

    def record(address, *a, **k):
        calls.append(address)
        raise ConnectionRefusedError()

    def no_program(*a, **k):
        raise AssertionError("check_port started a program")

    monkeypatch.setattr(CP.socket, "create_connection", record)
    monkeypatch.setattr(subprocess, "run", no_program)
    CP._run({"host": "127.0.0.1", "port": 9})
    assert calls == [("127.0.0.1", 9)]


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


def test_it_works_without_netcat(tmp_path, monkeypatch, listener):
    """No program on PATH at all, and the answer is the same."""
    monkeypatch.setenv("PATH", "")
    assert "accepting connections" in CP._run({"host": "127.0.0.1", "port": listener})


def _closed_port() -> int:
    """A port nothing is listening on: bound then released."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    return port