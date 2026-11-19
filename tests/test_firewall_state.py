"""The firewall sense must not hang (or prompt for a password) on a firewalld
whose `--state` calls the write `config` method. Measured: firewalld 2.5.2 on
Python 3.14.9, where `firewall-cmd --state` calls `FirewallD1.config` (a write
action gated behind AUTH_SELF) and hangs on the auth, while `--get-default-zone`
is read-only (the passwordless `info` action) and answers instantly. The sense
uses the read-only call first so a status poll never prompts."""

import shutil
import subprocess

import pytest

from shani_chronoa.senses import firewall


def _proc(stdout="", rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _firewall_cmd_present(monkeypatch):
    """The sense returns None early when firewall-cmd is absent; make it present
    so the branch under test is reachable on any machine."""
    real = shutil.which

    def fake(cmd):
        if cmd == "firewall-cmd":
            return "/usr/bin/firewall-cmd"
        return real(cmd)

    monkeypatch.setattr(firewall.shutil, "which", fake)


def _stub(monkeypatch, responses):
    monkeypatch.setattr(
        firewall,
        "_run_cmd",
        lambda argv, env=None, timeout=None: responses.get(tuple(argv)),
    )


def test_running_reads_the_default_zone_without_prompting(monkeypatch):
    # --get-default-zone answers (read-only, passwordless); --state must not be
    # called at all, because on the broken build it triggers a password prompt.
    _stub(
        monkeypatch,
        {
            ("firewall-cmd", "--get-default-zone"): _proc("public\n"),
            ("firewall-cmd", "--state"): None,
        },
    )
    state = firewall.firewalld_state()
    assert state["active"] is True
    assert "running" in state["detail"]
    assert "default zone public" in state["detail"]


def test_running_when_only_state_answers(monkeypatch):
    _stub(
        monkeypatch,
        {
            ("firewall-cmd", "--get-default-zone"): None,
            ("firewall-cmd", "--state"): _proc("running\n"),
        },
    )
    state = firewall.firewalld_state()
    assert state["active"] is True


def test_not_running(monkeypatch):
    _stub(
        monkeypatch,
        {
            ("firewall-cmd", "--get-default-zone"): None,
            ("firewall-cmd", "--state"): _proc("not running\n"),
        },
    )
    state = firewall.firewalld_state()
    assert state["active"] is False


def test_unknown_when_both_fail(monkeypatch):
    _stub(
        monkeypatch,
        {
            ("firewall-cmd", "--get-default-zone"): None,
            ("firewall-cmd", "--state"): None,
        },
    )
    state = firewall.firewalld_state()
    assert state["active"] is None, "could not determine, never a confident no"
    assert "could not be read" in state["detail"]
