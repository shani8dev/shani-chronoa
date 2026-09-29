"""Tests for the everyday-task skills added alongside the file skills.

The pattern under test is the one this project treats as non-negotiable: **an
error must never be reported as a clean, empty, successful-looking result.** A
directory that could not be read is not an empty directory. A search that found
nothing is not a claim about the disk. A process that has exited is not a
process that is still running.

Two of these tests exist because the first implementation of the skill was wrong
in exactly that way, and only running it showed it:

- `kill_process` decided a pid was alive with `os.path.exists("/proc/<pid>")`,
  which is true for a *zombie* - a process that has been killed and not yet
  reaped. It reported dead processes as alive and told the caller to try KILL on
  a pid that would never respond again.
- `connect_wifi` asked nmcli for `nmcli -t -f STATE network`, which is not a
  valid query; the error went to stderr, stdout was empty, and the disconnect
  branch read that as "not connected" on a machine that was connected.

The consent tests are here because the gate is the only thing standing between
a model and `rm -rf`, and a gate that is checked *after* the path is inspected
still leaks whether the path exists.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.skills import (  # noqa: E402
    add_reminder,
    close_window,
    connect_wifi,
    create_directory,
    delete_file,
    find_files,
    focus_window,
    kill_process,
    list_directory,
    list_processes,
    list_wifi_networks,
    list_windows,
    move_or_copy_file,
    open_file,
    print_file,
    read_text_file,
    search_file_contents,
    write_text_file,
)


@pytest.fixture
def granted():
    """A config with every new action consent explicitly switched on."""
    config = ChronoaConfig()
    config.set("privacy-mode", "false")
    for key in ("file-delete-enabled", "process-kill-enabled",
                "window-close-enabled", "wifi-connect-enabled",
                "input-control-enabled", "network-sense-enabled"):
        config.set(key, "true")
    return config


def _sandbox(tmp_path, monkeypatch):
    """Point the three skills that reach outside tmp_path back inside it."""
    monkeypatch.setattr(add_reminder, "_STORE",
                        tmp_path / "reminders.jsonl")
    return tmp_path


class TestEveryFailureIsNamed:
    """The shared promise, checked on the paths most likely to fake a result."""

    def test_a_missing_path_is_not_an_empty_directory(self, tmp_path):
        out = list_directory._run({"path": str(tmp_path / "nope")})
        assert "does not exist" in out
        assert "entries" not in out, "a missing path reported as an empty listing"

    def test_an_unreadable_directory_is_not_empty(self):
        out = list_directory._run({"path": "/root"})
        assert "permission denied" in out
        assert "not empty" in out or "unknown" in out, (
            "an unreadable directory must not read as an empty one")

    def test_a_file_is_not_a_directory(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hi")
        assert "is a file, not a directory" in list_directory._run({"path": str(f)})

    def test_a_search_miss_says_what_was_actually_read(self, tmp_path):
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("beta")
        out = search_file_contents._run({"text": "gamma", "path": str(tmp_path)})
        assert "No text file" in out
        assert "2 file(s) were read" in out, (
            "the reply must say how much was searched, so a miss is a fact "
            "about the search rather than about the disk")

    def test_a_truncated_listing_says_it_was_truncated(self, tmp_path):
        for i in range(6):
            (tmp_path / f"f{i}.log").write_text("x")
        out = find_files._run({"pattern": "*.log", "path": str(tmp_path),
                               "max_results": 2})
        assert "not everything" in out
        assert "2" in out


class TestTheDestructiveGates:
    """Each gate: refuses when off, and does not leak existence when it refuses."""

    def test_delete_refuses_and_hides_existence(self, tmp_path, granted):
        victim = tmp_path / "secret.txt"
        victim.write_text("x")
        config = ChronoaConfig()
        config.set("privacy-mode", "false")
        config.set("file-delete-enabled", "false")
        delete_file.ChronoaConfig = lambda: config
        try:
            out = delete_file._run({"path": str(victim)})
        finally:
            delete_file.ChronoaConfig = ChronoaConfig
        assert "Refusing" in out
        assert "exists" not in out.lower() or "no path" not in out.lower()
        assert victim.exists(), "a refused delete must not have deleted anything"

    def test_delete_removes_when_granted(self, tmp_path, granted):
        delete_file.ChronoaConfig = lambda: granted
        try:
            victim = tmp_path / "gone.txt"
            victim.write_text("bye")
            out = delete_file._run({"path": str(victim)})
        finally:
            delete_file.ChronoaConfig = ChronoaConfig
        assert "Deleted" in out and "permanent" in out
        assert not victim.exists()

    def test_delete_refuses_a_non_empty_directory_without_recursive(self, tmp_path, granted):
        delete_file.ChronoaConfig = lambda: granted
        try:
            tree = tmp_path / "t"
            (tree / "sub").mkdir(parents=True)
            out = delete_file._run({"path": str(tree)})
            assert "Refusing" in out and "recursive" in out
            assert tree.exists()
        finally:
            delete_file.ChronoaConfig = ChronoaConfig

    def test_delete_refuses_a_whole_filesystem(self, granted):
        delete_file.ChronoaConfig = lambda: granted
        try:
            out = delete_file._run({"path": "/", "recursive": True})
        finally:
            delete_file.ChronoaConfig = ChronoaConfig
        assert "Refusing" in out
        assert Path("/").exists()

    def test_kill_refuses_when_off(self, granted):
        config = ChronoaConfig()
        config.set("privacy-mode", "false")
        config.set("process-kill-enabled", "false")
        kill_process.ChronoaConfig = lambda: config
        try:
            out = kill_process._run({"pid": os.getpid()})
        finally:
            kill_process.ChronoaConfig = ChronoaConfig
        assert "Refusing" in out

    def test_kill_refuses_its_own_pid(self, granted):
        kill_process.ChronoaConfig = lambda: granted
        try:
            out = kill_process._run({"pid": os.getpid()})
        finally:
            kill_process.ChronoaConfig = ChronoaConfig
        assert "Chronoa itself" in out

    def test_close_window_refuses_when_off(self, granted):
        config = ChronoaConfig()
        config.set("privacy-mode", "false")
        config.set("window-close-enabled", "false")
        close_window.ChronoaConfig = lambda: config
        try:
            out = close_window._run({"window_id": "12345"})
        finally:
            close_window.ChronoaConfig = ChronoaConfig
        assert "Refusing" in out

    def test_connect_wifi_refuses_when_off(self, granted):
        config = ChronoaConfig()
        config.set("privacy-mode", "false")
        config.set("wifi-connect-enabled", "false")
        connect_wifi.ChronoaConfig = lambda: config
        try:
            out = connect_wifi._run({"ssid": "Anything"})
        finally:
            connect_wifi.ChronoaConfig = ChronoaConfig
        assert "Refusing" in out


class TestKillProcessKnowsAProcessIsDead:
    """The zombie bug, pinned so the naive existence check cannot come back."""

    def test_a_reaped_process_is_not_running(self):
        assert kill_process._is_running(str(os.getpid())) is True
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        assert kill_process._is_running(str(child.pid)) is False, (
            "a waited-for child is a reaped process; if this says True the "
            "check is looking at the /proc entry rather than the process state")

    def test_a_killed_but_unreaped_process_is_not_running(self):
        """The exact case the first implementation got wrong.

        `os.path.exists('/proc/<pid>')` is True here, because the entry survives
        until the parent reaps it. Only the `Z` state in /proc/<pid>/stat tells
        the truth.
        """
        child = subprocess.Popen([sys.executable, "-c",
                                  "import time; time.sleep(30)"])
        time.sleep(0.4)
        child.kill()
        time.sleep(0.3)
        try:
            exists = os.path.exists(f"/proc/{child.pid}")
            assert exists, (
                "precondition: the /proc entry should still be present, which "
                "is exactly why existence is the wrong test")
            assert kill_process._is_running(str(child.pid)) is False
        finally:
            child.wait()

    def test_a_cooperative_process_is_reported_as_exited(self, granted):
        kill_process.ChronoaConfig = lambda: granted
        try:
            child = subprocess.Popen(["sleep", "30"])
            time.sleep(0.3)
            out = kill_process._run({"pid": child.pid})
            child.wait()
        finally:
            kill_process.ChronoaConfig = ChronoaConfig
        assert "has exited" in out, (
            f"a process that took SIGTERM and died was reported as: {out}")


class TestWriteRefusesToClobber:
    def test_a_non_empty_file_is_not_replaced_silently(self, tmp_path):
        target = tmp_path / "keep.txt"
        target.write_text("original")
        out = write_text_file._run({"path": str(target), "content": "new"})
        assert "Refusing" in out
        assert target.read_text() == "original"

    def test_overwrite_replaces_and_reports_the_size(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("original")
        out = write_text_file._run({"path": str(target), "content": "new",
                                    "overwrite": True})
        assert "Replaced" in out
        assert target.read_text() == "new"

    def test_append_keeps_what_was_there(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("one\n")
        write_text_file._run({"path": str(target), "content": "two\n",
                              "append": True})
        assert target.read_text() == "one\ntwo\n"

    def test_append_and_overwrite_together_is_refused(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("x")
        out = write_text_file._run({"path": str(target), "content": "y",
                                    "append": True, "overwrite": True})
        assert "not both" in out


class TestMoveRefusesTheAwkwardCases:
    def test_a_directory_cannot_be_moved_inside_itself(self, tmp_path):
        tree = tmp_path / "t"
        (tree / "sub").mkdir(parents=True)
        out = move_or_copy_file._run({"source": str(tree),
                                      "destination": str(tree / "sub" / "t2")})
        assert "Refusing" in out
        assert tree.exists()

    def test_an_existing_destination_is_not_replaced(self, tmp_path):
        src, dst = tmp_path / "a", tmp_path / "b"
        src.write_text("A")
        dst.write_text("B")
        out = move_or_copy_file._run({"source": str(src), "destination": str(dst),
                                      "mode": "copy"})
        assert "Refusing" in out
        assert dst.read_text() == "B"

    def test_an_unknown_mode_is_refused(self, tmp_path):
        out = move_or_copy_file._run({"source": str(tmp_path),
                                      "destination": str(tmp_path / "x"),
                                      "mode": "teleport"})
        assert "'move' or 'copy'" in out

    def test_copy_then_the_source_is_still_there(self, tmp_path):
        src, dst = tmp_path / "a", tmp_path / "b"
        src.write_text("A")
        assert "Copied" in move_or_copy_file._run(
            {"source": str(src), "destination": str(dst), "mode": "copy"})
        assert src.exists() and dst.read_text() == "A"


class TestReadRefusesWhatItCannotShow:
    def test_a_binary_file_is_reported_as_binary_not_mangled(self, tmp_path):
        target = tmp_path / "b.bin"
        target.write_bytes(b"\x00\x01\x02\xff not utf-8")
        out = read_text_file._run({"path": str(target)})
        assert "not UTF-8" in out
        assert "�" not in out, "replacement characters would hide the corruption"

    def test_an_empty_file_really_is_empty(self, tmp_path):
        target = tmp_path / "e.txt"
        target.write_text("")
        assert "empty" in read_text_file._run({"path": str(target)}).lower()

    def test_a_line_past_the_end_returns_nothing_rather_than_everything(self, tmp_path):
        target = tmp_path / "a.txt"
        target.write_text("one\ntwo\n")
        out = read_text_file._run({"path": str(target), "start_line": 99})
        assert "past the end" in out
        assert "one" not in out


class TestCreateDirectoryIsIdempotent:
    def test_creating_an_existing_directory_succeeds(self, tmp_path):
        out = create_directory._run({"path": str(tmp_path)})
        assert "already exists" in out

    def test_parents_are_created(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        assert "Created" in create_directory._run({"path": str(deep)})
        assert deep.is_dir()


class TestTheDesktopSkillsRefuseWhenTheyCannotWork:
    """No partial lists, and no guessing which window."""

    def test_windows_refuse_under_wayland(self, monkeypatch, granted):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        # Consent is granted deliberately: `close_window` checks its gate before
        # the session, which is the right order - a refusal must not report
        # whether the window exists or whether this session could drive it.
        close_window.ChronoaConfig = lambda: granted
        try:
            for out in (list_windows._run({}),
                        focus_window._run({"title_contains": "x"}),
                        close_window._run({"window_id": "1"})):
                assert "Wayland" in out, f"a partial list was offered instead: {out[:80]}"
        finally:
            close_window.ChronoaConfig = ChronoaConfig

    def test_a_consent_refusal_comes_before_the_session_check(self, monkeypatch):
        """The ordering is a privacy property, so it is asserted rather than
        left to whoever reorders the branches next."""
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        config = ChronoaConfig()
        config.set("privacy-mode", "false")
        config.set("window-close-enabled", "false")
        close_window.ChronoaConfig = lambda: config
        try:
            out = close_window._run({"window_id": "1"})
        finally:
            close_window.ChronoaConfig = ChronoaConfig
        assert "Refusing" in out
        assert "Wayland" not in out, (
            "the refusal disclosed the session type, which it has no reason to "
            "tell someone whose consent was refused")

    def test_focus_needs_something_to_match(self):
        out = focus_window._run({})
        assert "window_id" in out

    def test_close_needs_something_to_close(self, granted):
        close_window.ChronoaConfig = lambda: granted
        try:
            out = close_window._run({})
        finally:
            close_window.ChronoaConfig = ChronoaConfig
        assert "guess" in out

    def test_print_refuses_a_directory(self, tmp_path):
        assert "directory" in print_file._run({"path": str(tmp_path)})

    def test_print_refuses_a_non_document(self, tmp_path):
        target = tmp_path / "archive.bin"
        target.write_bytes(b"\x00")
        out = print_file._run({"path": str(target)})
        assert "Refusing" in out and "document" in out

    def test_open_refuses_an_empty_path(self):
        assert "nothing to open" in open_file._run({})


class TestReminders:
    def test_it_writes_where_it_says(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        out = add_reminder._run({"text": "buy milk", "due": "in 2 hours"})
        assert str(tmp_path / "reminders.jsonl") in out
        assert add_reminder._STORE.exists()

    def test_a_relative_due_becomes_an_absolute_time(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        out = add_reminder._run({"text": "x", "due": "in 2 hours"})
        assert "Due " in out and "-" in out, "a relative time must be resolved"

    def test_an_unreadable_due_is_refused_not_guessed(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        out = add_reminder._run({"text": "x", "due": "whenever I feel like it"})
        assert "no due date" in out
        assert "No due time" in out

    def test_no_text_writes_nothing(self, tmp_path, monkeypatch):
        _sandbox(tmp_path, monkeypatch)
        assert "nothing to write" in add_reminder._run({})
        assert not add_reminder._STORE.exists()


class TestProcessListingIsHonest:
    def test_it_reads_proc_without_ps(self):
        out = list_processes._run({"limit": 5})
        assert "process(es)" in out
        assert "share of the CPU" in out, (
            "the CPU column must say what it measures, or a percentage of a "
            "since-boot total reads as current load")

    def test_a_filter_miss_is_scoped_to_the_search(self):
        out = list_processes._run({"name_filter": "zzz-no-such-process-zzz"})
        assert "Nothing here matches" in out

    def test_a_filter_hit_is_not_the_whole_machine(self):
        out = list_processes._run({"name_filter": "python", "limit": 3})
        assert "process(es)" in out


class TestEveryGatedSkillHasAKeyAndALabel:
    """A gate whose key has no label is a gate nobody can find."""

    def test_the_four_new_gates_are_labelled(self):
        from shani_chronoa import capabilities

        for tool, key in (("delete_file", "file-delete-enabled"),
                          ("kill_process", "process-kill-enabled"),
                          ("close_window", "window-close-enabled"),
                          ("connect_wifi", "wifi-connect-enabled")):
            assert capabilities.gated_by(tool, "") == key
            assert key in capabilities.GATE_NAMES, f"{key} has no human label"

    def test_each_gate_is_reachable_from_the_settings_window(self):
        source = Path("usr/lib/shani-chronoa/shani_chronoa/settings_window.py").read_text()
        for key in ("file-delete-enabled", "process-kill-enabled",
                    "window-close-enabled", "wifi-connect-enabled"):
            assert f'"{key}"' in source, (
                f"{key} exists in the schema with no settings row, so it can "
                f"only be turned on from a terminal")


class TestConnectWifiDoesNotGuessTheInterface:
    """Two bugs found by running the skill, neither of which had a test at first.

    Both are the same mistake the `modelfit` hardcoded-port bug was: a
    per-machine fact written down as if it were a constant. Neither shows up in
    a unit test that mocks the subprocess, and both make the skill
    permanently non-functional on the machine that did not match.
    """

    def _captured(self, monkeypatch, args, result=None):
        """Run the skill with nmcli stubbed, returning the argv it was given."""
        seen = []

        class Proc:
            returncode = result if result is not None else 0
            # nmcli's terse output is value:value per line, not FIELD:value -
            # e.g. "TestNet:802-11-wireless". A stub shaped like FIELD:value
            # would pass while the real parse was wrong.
            stdout = "TestNet:802-11-wireless\n" if result is None else ""
            stderr = "" if result is None else "boom"

        def fake_run(argv, **kwargs):
            seen.append(argv)
            return Proc()

        monkeypatch.setattr(connect_wifi.subprocess, "run", fake_run)
        return seen

    def test_the_connect_call_names_no_interface(self, monkeypatch, granted):
        """`--ifname wlan0` was hardcoded. This machine's radio is wlp0s20f3, so
        that build could never join anything, and it reported the failure as a
        wrong interface rather than as a hardcoded guess."""
        connect_wifi.ChronoaConfig = lambda: granted
        seen = self._captured(monkeypatch, None)
        try:
            connect_wifi._run({"ssid": "TestNet"})
        finally:
            connect_wifi.ChronoaConfig = ChronoaConfig
        assert seen, "nmcli was never called"
        connect_call = next(c for c in seen if "connect" in c)
        assert "ifname" not in connect_call, (
            f"a hardcoded interface is back in {connect_call}")
        assert not any(a.startswith("wlan") for a in connect_call), (
            f"a guessed interface name is back in {connect_call}")

    def test_the_disconnect_branch_asks_a_question_nmcli_answers(self, monkeypatch, granted):
        """`nmcli -t -f STATE network` is not a valid query: for `nmcli network`
        the only allowed field is NETWORKING, and STATE belongs to
        `nmcli connection`. The invalid form writes to stderr and leaves stdout
        empty, so the branch read that as 'not connected' on a machine that was
        very much connected."""
        connect_wifi.ChronoaConfig = lambda: granted
        seen = self._captured(monkeypatch, None)
        try:
            out = connect_wifi._run({})
        finally:
            connect_wifi.ChronoaConfig = ChronoaConfig
        first = seen[0]
        assert first[:2] == ["nmcli", "-t"]
        assert "connection" in first, (
            f"the active-connection query is wrong again: {first}")
        assert "--active" in first, f"it is not asking what is active: {first}"
        assert "-f" in first and first[first.index("-f") + 1] == "NAME,TYPE"
        assert "Was on: TestNet" in out, (
            f"the branch did not report the connection it found: {out}")

    def test_an_nmcli_error_is_not_read_as_not_connected(self, monkeypatch, granted):
        """The failure mode that made the bug invisible: a non-zero exit with
        empty stdout looks exactly like 'nothing is connected' unless the exit
        code is checked."""
        connect_wifi.ChronoaConfig = lambda: granted
        seen = self._captured(monkeypatch, None, result=1)
        try:
            out = connect_wifi._run({})
        finally:
            connect_wifi.ChronoaConfig = ChronoaConfig
        assert "nothing to leave" not in out, (
            f"an nmcli failure was reported as 'not connected': {out}")
        assert "Could not read" in out
