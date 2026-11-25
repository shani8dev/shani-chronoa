"""Tests for `check_updates`, moved with the skill to shani-sysadmin.

The skill answers with a number, and a number is the easiest kind of wrong
answer to ship: a missing binary or an unreadable file can collapse into a
plausible-looking default. These assert that an unchecked machine is never
reported as a clean one.
"""

import subprocess

import pytest

from shani_sysadmin.skills import check_updates


def _proc(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, out, err)


# --- check_updates --------------------------------------------------------

def _wire(monkeypatch, *, which, result=None, raises=None):
    monkeypatch.setattr(check_updates.shutil, "which", lambda n: f"/usr/bin/{n}" if n in which else None)
    if raises is not None:
        def boom(cmd, **kwargs):
            raise raises
        monkeypatch.setattr(check_updates.subprocess, "run", boom)
    else:
        monkeypatch.setattr(check_updates.subprocess, "run", lambda cmd, **kw: result)


class TestCheckUpdatesFailsClosed:
    def test_a_machine_with_no_package_manager_is_not_an_all_clear(self, monkeypatch):
        """The dangerous one. A bare `except` here returns an empty list, which
        is byte-identical to a fully up-to-date Arch box."""
        _wire(monkeypatch, which=set())
        out = check_updates._run({})
        assert "not the same as having no updates" in out
        assert "No package updates are waiting" not in out

    def test_a_db_lock_is_not_an_all_clear(self, monkeypatch):
        """checkupdates exits 3 when another pacman holds the lock. Treating any
        non-2 as success reports a locked machine as up to date."""
        _wire(monkeypatch, which={"checkupdates"},
              result=_proc(["checkupdates"], rc=3, err="db lock held"))
        out = check_updates._run({})
        assert "database lock" in out
        assert "not an all-clear" in out
        assert "No package updates" not in out

    def test_a_timeout_says_the_state_is_unknown(self, monkeypatch):
        _wire(monkeypatch, which={"checkupdates"},
              raises=subprocess.TimeoutExpired(["checkupdates"], 120))
        out = check_updates._run({})
        assert "did not finish" in out
        assert "unknown" in out

    def test_a_hard_failure_is_not_a_clean_bill_of_health(self, monkeypatch):
        _wire(monkeypatch, which={"checkupdates"},
              result=_proc(["checkupdates"], rc=1, err="could not open database"))
        out = check_updates._run({})
        assert "failed" in out
        assert "could not open database" in out
        assert "No package updates are waiting" not in out


class TestCheckUpdatesReportsUpdates:
    def test_exit_code_2_means_updates_exist(self, monkeypatch):
        _wire(monkeypatch, which={"checkupdates"},
              result=_proc(["checkupdates"], rc=2, out="linux 6.9-1 -> 6.9-2\nbash 5.2-1 -> 5.2-2"))
        out = check_updates._run({})
        assert "2 package update(s) waiting" in out
        assert "linux 6.9-1 -> 6.9-2" in out

    def test_exit_code_0_is_a_real_all_clear(self, monkeypatch):
        _wire(monkeypatch, which={"checkupdates"},
              result=_proc(["checkupdates"], rc=0))
        out = check_updates._run({})
        assert "No package updates are waiting" in out
        assert "real all-clear" in out

    def test_it_falls_back_to_pacman_qu(self, monkeypatch):
        _wire(monkeypatch, which={"pacman"},
              result=_proc(["pacman", "-Qu"], rc=0, out="vim 9.1-1 -> 9.1-2"))
        out = check_updates._run({})
        assert "vim 9.1-1 -> 9.1-2" in out
        assert "pacman -Qu" in out

    def test_checkupdates_is_preferred_over_pacman_qu(self, monkeypatch):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return _proc(cmd, rc=2 if cmd[0] == "checkupdates" else 0, out="x 1 -> 2")

        _wire(monkeypatch, which={"checkupdates", "pacman"}, result=None)
        monkeypatch.setattr(check_updates.subprocess, "run", fake)
        check_updates._run({})
        assert calls == [["checkupdates"]], (
            f"ran {calls} - checkupdates avoids the live database lock that "
            f"pacman -Qu takes"
        )

    def test_a_long_update_list_is_truncated_and_says_so(self, monkeypatch):
        many = "\n".join(f"pkg{i:03d} 1 -> 2" for i in range(50))
        _wire(monkeypatch, which={"checkupdates"},
              result=_proc(["checkupdates"], rc=2, out=many))
        out = check_updates._run({})
        assert "50 package update(s) waiting" in out
        assert "and 10 more" in out
