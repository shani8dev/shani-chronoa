"""Tests for `check_updates`, `disk_usage` and `system_info`.

These three all answer a question with a number, and a number is the easiest
kind of wrong answer to ship: a missing binary, an unreadable file, or a
timed-out walk each produce a plausible-looking result if the failure is
collapsed into a default. So the bulk of what is asserted here is that an
unchecked machine is never reported as a clean one, an empty reading is never
reported as "nothing found", and a partial measurement is never presented as a
complete one.
"""

import subprocess

import pytest

from shani_chronoa import capabilities
from shani_chronoa.skills import check_updates, discover_skills, disk_usage, system_info


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


# --- disk_usage -----------------------------------------------------------

DF_OK = (
    "Filesystem      Size  Used Avail Use% Mounted on\n"
    "/dev/sda1        50G   20G   30G  40% /\n"
    "tmpfs           7.8G     0  7.8G   0% /dev/shm\n"
    "/dev/sdb1       200G  190G   10G  95% /data\n"
)


class TestDiskUsageFilesystems:
    def test_it_reports_the_filesystems(self, monkeypatch):
        monkeypatch.setattr(disk_usage.subprocess, "run",
                            lambda cmd, **kw: _proc(cmd, out=DF_OK))
        out = disk_usage._run({})
        assert "/dev/sda1" in out
        assert "48%" not in out, "the df Use% column was mangled"

    def test_pseudo_filesystems_are_excluded_from_the_command(self, monkeypatch):
        seen = {}

        def fake(cmd, **kw):
            seen["cmd"] = cmd
            return _proc(cmd, out=DF_OK)

        monkeypatch.setattr(disk_usage.subprocess, "run", fake)
        disk_usage._run({})
        assert "-x" in seen["cmd"]
        for pseudo in ("tmpfs", "devtmpfs", "squashfs"):
            assert f"-x\n{pseudo}" in "\n".join(seen["cmd"]) or pseudo in seen["cmd"]

    def test_a_failing_df_is_not_an_empty_disk(self, monkeypatch):
        monkeypatch.setattr(disk_usage.subprocess, "run",
                            lambda cmd, **kw: _proc(cmd, rc=1, err="df: cannot read"))
        out = disk_usage._run({})
        assert "Could not read filesystem usage" in out
        assert "cannot read" in out

    def test_a_header_only_df_is_not_reported_as_all_empty(self, monkeypatch):
        monkeypatch.setattr(
            disk_usage.subprocess, "run",
            lambda cmd, **kw: _proc(cmd, out="Filesystem Size Used Avail Use% Mounted on\n"))
        out = disk_usage._run({})
        assert "does not mean every filesystem is empty" in out

    def test_a_timeout_is_not_an_empty_disk(self, monkeypatch):
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 10)

        monkeypatch.setattr(disk_usage.subprocess, "run", boom)
        assert "Could not read filesystem usage" in disk_usage._run({})


class TestDiskUsageBreakdown:
    DU_OK = (
        "24M\t/etc/ssl\n"
        "12M\t/etc/X11\n"
        "4.0M\t/etc/pam.d\n"
        "4.0M\t/etc\n"
    )

    def _du(self, monkeypatch, *, out=None, err="", rc=0, raises=None, present=True):
        def fake(cmd, **kw):
            if cmd[0] != "du":
                return _proc(cmd, out=DF_OK)
            if raises is not None:
                raise raises
            return _proc(cmd, rc=rc, out=out if out is not None else self.DU_OK, err=err)

        monkeypatch.setattr(disk_usage.subprocess, "run", fake)
        monkeypatch.setattr(
            disk_usage.shutil, "which",
            lambda n: "/usr/bin/du" if (present and n == "du") else None)
        monkeypatch.setattr(disk_usage.os.path, "isdir", lambda p: True)

    def test_it_lists_the_largest_first_and_excludes_the_root_itself(self, monkeypatch):
        self._du(monkeypatch)
        out = disk_usage._run({"path": "/etc"})
        body = out.split("Largest entries", 1)[1]
        assert body.index("/etc/ssl") < body.index("/etc/X11"), "not sorted largest first"
        assert "\n  /etc\n" not in out, "the path itself was listed as its own entry"

    def test_a_timed_out_walk_is_labelled_partial(self, monkeypatch):
        """A partial list sorted largest-first is shaped exactly like a complete
        one, so the user would conclude the biggest directory was found."""
        self._du(monkeypatch, raises=subprocess.TimeoutExpired(["du"], 20))
        out = disk_usage._run({"path": "/etc"})
        assert "Giving up" in out
        assert "PARTIAL" in out
        assert "largest directories may not have been reached" in out

    def test_unreadable_paths_make_the_totals_a_lower_bound(self, monkeypatch):
        self._du(monkeypatch, err="du: cannot read directory '/etc/secret': Permission denied")
        out = disk_usage._run({"path": "/etc"})
        assert "lower bounds" in out

    def test_a_missing_du_is_a_missing_binary_not_a_full_disk(self, monkeypatch):
        self._du(monkeypatch, present=False)
        out = disk_usage._run({"path": "/etc"})
        assert "`du` is not installed" in out
        assert "Filesystem" in out, "the df half should still work"

    def test_du_stays_on_one_filesystem(self, monkeypatch):
        seen = {}

        def fake(cmd, **kw):
            if cmd[0] == "du":
                seen["cmd"] = cmd
                return _proc(cmd, out=self.DU_OK)
            return _proc(cmd, out=DF_OK)

        monkeypatch.setattr(disk_usage.subprocess, "run", fake)
        monkeypatch.setattr(disk_usage.shutil, "which", lambda n: "/usr/bin/du")
        monkeypatch.setattr(disk_usage.os.path, "isdir", lambda p: True)
        disk_usage._run({"path": "/etc"})
        assert "-x" in seen["cmd"], (
            "du crossed a filesystem boundary, so the total includes another disk"
        )

    def test_a_path_that_does_not_exist_says_so(self, monkeypatch):
        out = disk_usage._run({"path": "/no/such/place"})
        assert "does not exist" in out

    def test_no_path_means_no_walk(self, monkeypatch):
        seen = []

        def fake(cmd, **kw):
            seen.append(cmd[0])
            return _proc(cmd, out=DF_OK)

        monkeypatch.setattr(disk_usage.subprocess, "run", fake)
        disk_usage._run({})
        assert "du" not in seen, "walked the whole filesystem when no path was given"


# --- system_info ----------------------------------------------------------

class TestSystemInfo:
    def _with(self, monkeypatch, **files):
        def fake_read(path):
            return files.get(path)
        monkeypatch.setattr(system_info, "_read", fake_read)

    def test_it_reports_the_distribution_from_os_release(self, monkeypatch):
        self._with(monkeypatch, **{
            "/etc/os-release": 'PRETTY_NAME="ShaniOS 3.0"\nNAME="ShaniOS"\n'})
        assert "distribution: ShaniOS 3.0" in system_info._run({})

    def test_a_missing_os_release_is_unknown_not_a_guess(self, monkeypatch):
        self._with(monkeypatch)
        out = system_info._run({})
        assert "distribution: unknown" in out
        assert "/etc/os-release" in out

    def test_an_os_release_with_only_a_name_still_answers(self, monkeypatch):
        self._with(monkeypatch, **{"/etc/os-release": 'NAME="ShaniOS"\nVERSION="3.0"\n'})
        assert "distribution: ShaniOS 3.0" in system_info._run({})

    def test_a_missing_uptime_is_unknown_not_zero(self, monkeypatch):
        """0 seconds uptime would be a plausible-looking catastrophic claim."""
        self._with(monkeypatch)
        out = system_info._run({})
        assert "uptime: unknown" in out
        assert "uptime: 0" not in out

    @pytest.mark.parametrize("raw,expected", [
        ("100000.5 5000.0", "uptime: 1d 3h"),
        ("7200.0 100.0", "uptime: 2h 0m"),
        ("300.0 10.0", "uptime: 5m"),
    ])
    def test_uptime_is_humanised(self, monkeypatch, raw, expected):
        self._with(monkeypatch, **{"/proc/uptime": raw})
        assert expected in system_info._run({})

    @pytest.mark.parametrize("raw", ["", "not-a-number", "-5.0"])
    def test_unusable_uptime_is_unknown(self, monkeypatch, raw):
        self._with(monkeypatch, **{"/proc/uptime": raw})
        assert "uptime: unknown" in system_info._run({})

    def test_a_negative_uptime_is_refused_not_shown_as_a_number(self, monkeypatch):
        self._with(monkeypatch, **{"/proc/uptime": "-5.0 1.0"})
        assert "negative" in system_info._run({})

    def test_memory_reports_available_of_total(self, monkeypatch):
        self._with(monkeypatch, **{"/proc/meminfo": "MemTotal:  16384 kB\nMemAvailable:  8192 kB\n"})
        assert "memory: 8 MiB available of 16 MiB" in system_info._run({})

    def test_missing_memavailable_does_not_invent_availability(self, monkeypatch):
        self._with(monkeypatch, **{"/proc/meminfo": "MemTotal:  16384 kB\n"})
        out = system_info._run({})
        assert "available unknown" in out
        assert "MemFree" not in out

    def test_a_malformed_loadavg_is_unknown(self, monkeypatch):
        self._with(monkeypatch, **{"/proc/loadavg": "1.0 2.0"})
        assert "load: unknown" in system_info._run({})

    def test_an_unset_session_type_is_not_assumed_to_be_x11(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        out = system_info._run({})
        assert "XDG_SESSION_TYPE is not set" in out
        assert "session: x11" not in out

    def test_a_docker_marker_is_reported_as_a_container(self, monkeypatch):
        present = {"/.dockerenv"}
        contents = {"/run/systemd/container": "podman"}

        class FakePath:
            def __init__(self, value):
                self._p = str(value)

            def exists(self):
                return self._p in present

            def read_text(self, *a, **kw):
                return contents.get(self._p, "")

        monkeypatch.setattr(system_info, "Path", FakePath)
        self._with(monkeypatch)
        assert "container (docker)" in system_info._run({})

    def test_a_podman_container_is_named_from_its_marker(self, monkeypatch):
        present = {"/run/systemd/container"}
        contents = {"/run/systemd/container": "podman"}

        class FakePath:
            def __init__(self, value):
                self._p = str(value)

            def exists(self):
                return self._p in present

            def read_text(self, *a, **kw):
                return contents.get(self._p, "")

        monkeypatch.setattr(system_info, "Path", FakePath)
        self._with(monkeypatch)
        assert "container (podman)" in system_info._run({})

    def test_no_marker_and_no_dmi_is_unknown_not_a_guess(self, monkeypatch):
        class FakePath:
            def __init__(self, value):
                self._p = str(value)

            def exists(self):
                return False

            def read_text(self, *a, **kw):
                return ""

        monkeypatch.setattr(system_info, "Path", FakePath)
        self._with(monkeypatch)
        assert "virtualisation: unknown" in system_info._run({})

    def test_every_line_is_labelled(self, monkeypatch):
        """An unlabelled number in a wall of text cannot be checked by whoever
        is reading the answer."""
        self._with(monkeypatch, **{"/etc/os-release": 'PRETTY_NAME="X"\n'})
        for line in system_info._run({}).splitlines():
            assert ":" in line, f"unlabelled line: {line!r}"


# --- registry -------------------------------------------------------------

@pytest.mark.parametrize("name", ["check_updates", "disk_usage", "system_info"])
def test_each_is_discovered_and_grouped(name):
    tools, _ = discover_skills()
    names = [t["function"]["name"] for t in tools]
    assert name in names, f"{name} loads but is not in the registry"
    grouped = {c.tool: c.group for c in capabilities.find_capabilities(tools)}
    assert grouped.get(name), f"{name} is ungrouped, so the help window hides it"
    assert grouped[name] in capabilities.GROUP_ORDER
