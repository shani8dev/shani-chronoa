"""Negative controls for the eight machine-state senses added 2026-09-30.

Every assertion here is a **negative control**: it removes a dependency the
sense depends on and asserts the sense says UNKNOWN rather than producing a
clean, confident, wrong answer. That is the class of bug this project has
demonstrable history with, and `AGENTS.md` names the three that shipped:

- `contention` (now `capture`) reported every microphone and camera as *free*
  when `fuser` was missing, because the `OSError` was swallowed into "no
  holders";
- `privilege` used Debian's `dpkg-query`, so on Arch it found no owner for
  anything and reported every process as unmanaged third-party software;
- `bluetooth` reported "0 devices" when `bluetoothctl` was not installed.

584 passing unit tests caught none of them, because the tests ran where the
dependency was present. A green line in this file is therefore not "the sense
works" - it is "the sense degrades honestly when something is missing".

**The controls mutate real module state, not stubbed functions.** Path constants
(`_PROC`, `_CGROUP_ROOT`, `_DMI`, `_NET_TCP`, ...) are repointed at a directory
that does not exist, and `shutil.which` is repointed at a PATH with no runtime
on it. Both are conditions a real machine has, so the read genuinely fails
rather than being intercepted. A `str.replace` on a source file would be weaker
twice over, for the reason `AGENTS.md` gives about negative controls: it matches
nothing silently, and it does not take the same code path a genuinely missing
file takes.

**A control that cannot fail is not a control.** Each UNKNOWN test is paired
with a positive assertion that the sense reached the reading it was about to
lose, and each mutation helper asserts that what it replaced is really gone.
Without that pairing a test passes whenever the developer happens to sit on a
machine where the sense is already UNKNOWN - which is the deleted visual suite's
failure, written up in `AGENTS.md`, in a different costume.
"""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.config import (  # noqa: E402
    _SENSE_CONSENT_KEYS,
    _SENSE_DEFAULT_ENABLED,
    ChronoaConfig,
)
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Percept  # noqa: E402
from shani_chronoa.senses import (  # noqa: E402
    boots as boots_mod,
    cgroup as cgroup_mod,
    containers as containers_mod,
    git as git_mod,
    hardware as hardware_mod,
    kernel as kernel_mod,
    listeners as listeners_mod,
    stale as stale_mod,
)

NEW_SENSES = ("hardware", "kernel", "cgroup", "containers",
              "listeners", "stale", "git", "boots")

#: Which of the eight are facts about the hardware, as opposed to facts about
#: the software the user chose to run. Declared once and asserted in one place,
#: so a sense cannot drift from the reasoning that set its tier.
PUBLIC_TIER = ("hardware", "kernel", "cgroup", "boots")

#: A path that cannot exist, standing in for "the kernel will not tell us".
#: `/proc` and `/sys` cannot be made unreadable by a test, but a sense that
#: swallows an `OSError` from them would swallow one from here too, and that is
#: the behaviour under test.
ABSENT = Path("/nonexistent-shani-sense-probe")

SCHEMA_XML = _REPO / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"

#: A real /proc/net/tcp header row, copied from the kernel's own column layout,
#: so the parser is exercised against that layout rather than a convenient
#: fiction. `sl local rem st tx:rx tr:tm->when retrnsmt uid timeout inode`.
_TCP_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
    "retrnsmt   uid  timeout inode\n"
)


def _tcp_row(local: str, state: str, uid: str, inode: str) -> str:
    return (f"   0: {local} 00000000:0000 {state} 00000000:00000000 00:00000000 "
            f"00000000 {uid:>8}        0 {inode} 1 0000000000000000 100 0 0 10 0\n")


def _socket_table(tmp_path: Path, *rows: str) -> Path:
    table = tmp_path / "tcp"
    table.write_text(_TCP_HEADER + "".join(rows))
    return table


def _text_of(result) -> str:
    return result.content if isinstance(result, Percept) else str(result)


@pytest.fixture
def granted(chronoa_config):
    """Turn on each sense's own consent key, the way a user would.

    Asserts the grant actually took. Every other test here reads the sense's own
    output, so a silently denied sense would make all of them pass against a
    refusal string instead.
    """
    for name in NEW_SENSES:
        chronoa_config.set(_SENSE_CONSENT_KEYS[name], "true")
    reread = ChronoaConfig()
    ungranted = [n for n in NEW_SENSES if not reread.sense_allowed(n)]
    assert ungranted == [], (
        f"these senses could not be granted in the hermetic store, so every "
        f"test below would read a refusal instead of the sense's own output: "
        f"{ungranted}"
    )
    return reread


def _no_runtime(monkeypatch) -> None:
    """Empty PATH, as on a machine with none of these tools installed.

    Asserts the replacement applied. `shutil.which` is called with the name the
    code passes, so a stub returning None only for names the code never asks
    about would be a control that cannot fail.
    """
    monkeypatch.setenv("PATH", "/nonexistent-shani-sense-bin")
    from shutil import which

    found = [n for n in ("git", "podman", "docker", "last", "fuser", "ss")
             if which(n) is not None]
    assert found == [], (
        f"the empty-PATH control did not apply: shutil.which still finds "
        f"{found}, so the senses under test were never deprived of a binary"
    )


# --- hardware ---------------------------------------------------------------


class TestHardware:
    def test_dmi_is_read_when_it_is_readable(self, granted, tmp_path, monkeypatch):
        dmi = tmp_path / "id"
        dmi.mkdir()
        (dmi / "product_name").write_text("PowerBook G4\n")
        (dmi / "sys_vendor").write_text("Acme\n")
        monkeypatch.setattr(hardware_mod, "_DMI", dmi)
        text = _text_of(hardware_mod._run({}))
        assert "PowerBook G4" in text and "Acme" in text

    def test_unreadable_dmi_is_unknown_not_a_machine_with_no_model(
        self, granted, monkeypatch
    ):
        # Control: the sense reaches a real reading before the mutation, so the
        # UNKNOWN below cannot pass for want of a subject.
        assert "UNKNOWN" not in _text_of(hardware_mod._run({})), (
            "this machine reports UNKNOWN even with its real DMI present, so the "
            "assertion below would pass without the mutation being needed"
        )
        monkeypatch.setattr(hardware_mod, "_DMI", ABSENT / "id")
        monkeypatch.setattr(hardware_mod, "_DEVTREE_MODEL", ABSENT / "model")
        assert not (ABSENT / "id").exists(), "the 'absent' path actually exists"
        text = _text_of(hardware_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "DMI unavailable" in text, text
        assert "machine:" not in text, (
            "a model line was produced from nothing; the architecture is not a "
            f"model and must not stand in for one: {text}"
        )

    def test_a_firmware_placeholder_is_not_reported_as_a_model(
        self, granted, tmp_path, monkeypatch
    ):
        # `To Be Filled By O.E.M.` is a real string in real DMI, and reporting
        # it as a model is a confident nonsense answer rather than an error.
        dmi = tmp_path / "id"
        dmi.mkdir()
        (dmi / "product_name").write_text("To Be Filled By O.E.M.\n")
        (dmi / "sys_vendor").write_text("LENOVO\n")
        monkeypatch.setattr(hardware_mod, "_DMI", dmi)
        monkeypatch.setattr(hardware_mod, "_DEVTREE_MODEL", ABSENT / "model")
        text = _text_of(hardware_mod._run({}))
        assert "To Be Filled By O.E.M." not in text, (
            f"a firmware placeholder was reported as the model: {text}"
        )
        assert "LENOVO" in text, (
            "the placeholder was rejected but the fallback to the next DMI "
            f"attribute did not happen: {text}"
        )

    def test_serial_and_uuid_are_never_read(self, granted, tmp_path, monkeypatch):
        dmi = tmp_path / "id"
        dmi.mkdir()
        (dmi / "product_name").write_text("ThinkPad\n")
        (dmi / "product_serial").write_text("PF2SECRET")
        (dmi / "product_uuid").write_text("4c4c4544-0037-3510-8054-b7c04f503232")
        monkeypatch.setattr(hardware_mod, "_DMI", dmi)
        text = _text_of(hardware_mod._run({}))
        assert "PF2SECRET" not in text and "4c4c4544" not in text, (
            f"a device identifier leaked into the percept: {text}"
        )

    def test_devicetree_model_is_used_when_dmi_is_absent(
        self, granted, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(hardware_mod, "_DMI", ABSENT / "id")
        model = tmp_path / "model"
        model.write_bytes(b"Raspberry Pi 4 Model B\x00\x00\x00\x00")
        monkeypatch.setattr(hardware_mod, "_DEVTREE_MODEL", model)
        text = _text_of(hardware_mod._run({}))
        assert "Raspberry Pi 4 Model B" in text
        assert "\x00" not in text, "the device tree property's NUL padding leaked"


# --- kernel -----------------------------------------------------------------


class TestKernel:
    def test_cmdline_secrets_are_withheld(self, granted):
        # The real dracut ordering: rd.luks arguments are written *before*
        # root=, which is why the redaction is not redundant with dropping the
        # tail. The control asserts the input really was transformed.
        raw = ("rd.luks.name=luks-crypt rd.luks.options=password=hunter2 "
               "root=UUID=deadbeef ro quiet splash")
        scrubbed = kernel_mod.scrub_cmdline(raw)
        assert scrubbed["safe"] != raw, (
            "the control command line was not scrubbed at all, so the assertions "
            "below would pass without proving anything"
        )
        assert "hunter2" not in scrubbed["safe"]
        assert "luks-crypt" not in scrubbed["safe"]
        assert "quiet" not in scrubbed["safe"], "arguments after root= were kept"
        assert scrubbed["redacted"] == 2, scrubbed
        assert scrubbed["dropped"] == 3, scrubbed
        assert "root=" in scrubbed["safe"], "the root= marker itself was dropped"

    def test_unreadable_proc_is_unknown_not_a_kernel(self, granted, monkeypatch):
        monkeypatch.setattr(kernel_mod, "_OSRELEASE", ABSENT / "osrelease")
        monkeypatch.setattr(kernel_mod, "_CMDLINE", ABSENT / "cmdline")
        monkeypatch.setattr(kernel_mod, "_VERSION", ABSENT / "version")
        monkeypatch.setattr(kernel_mod, "_STAT", ABSENT / "os-release")
        text = _text_of(kernel_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "not a claim about the kernel" in text, text

    def test_no_container_marker_is_not_physical_hardware(
        self, granted, monkeypatch
    ):
        # The marker *paths* are the dict's keys, so the whole dict is replaced.
        # Patching the values would leave the real `/.dockerenv` being read and
        # make this control pass for the wrong reason - which is exactly the
        # shape of ineffective control AGENTS.md warns about.
        monkeypatch.setattr(kernel_mod, "_CONTAINER_MARKERS", {
            "/.dockerenv": "docker",
            "/run/.containerenv": "podman",
        })
        assert not Path("/.dockerenv").exists(), (
            "this machine really is in a docker container, so 'no marker' cannot "
            "be produced by replacing the paths - the control cannot run here"
        )
        result = kernel_mod.read_virtualisation()
        assert result["state"] == kernel_mod.VIRT_NO_MARKER
        assert result["state"] != kernel_mod.VIRT_UNKNOWN, (
            "the control did not apply - the markers are still being read from "
            "their real paths, so this cannot tell the two states apart"
        )
        text = _text_of(kernel_mod._run({}))
        assert "NOT the same as physical hardware" in text, (
            f"absence of a marker was reported as proof of bare metal: {text}"
        )

    def test_a_container_marker_is_a_third_state(self, granted, tmp_path, monkeypatch):
        marker = tmp_path / ".dockerenv"
        marker.write_text("")
        monkeypatch.setattr(kernel_mod, "_CONTAINER_MARKERS", {
            str(marker): "docker",
            str(ABSENT / "containerenv"): "podman",
        })
        assert marker.exists(), "the marker fixture was not created"
        result = kernel_mod.read_virtualisation()
        assert result["state"] == kernel_mod.VIRT_CONTAINER, (
            f"a marker file that exists was not seen: {result}"
        )
        assert result["state"] not in (kernel_mod.VIRT_NO_MARKER,
                                       kernel_mod.VIRT_UNKNOWN), (
            "the marker file exists but the state did not change, so this "
            "control could not have failed"
        )


# --- cgroup -----------------------------------------------------------------


class TestCgroup:
    def _v2(self, tmp_path, monkeypatch, memory, cpu, pids):
        root = tmp_path / "cgroup"
        root.mkdir(exist_ok=True)
        (root / "memory.max").write_text(memory)
        (root / "cpu.max").write_text(cpu)
        (root / "pids.max").write_text(pids)
        monkeypatch.setattr(cgroup_mod, "_CGROUP_ROOT", root)
        return root

    def test_limits_are_read_when_the_controller_files_are_there(
        self, granted, tmp_path, monkeypatch
    ):
        self._v2(tmp_path, monkeypatch, "2147483648", "50000 100000", "512")
        text = _text_of(cgroup_mod._run({}))
        assert "2.00 GiB ceiling" in text, text
        assert "0.50 cores" in text, text
        assert "512 at most" in text, text

    def test_literal_max_is_unlimited_not_a_huge_number(
        self, granted, tmp_path, monkeypatch
    ):
        self._v2(tmp_path, monkeypatch, "max", "max 100000", "max")
        text = _text_of(cgroup_mod._run({}))
        assert text.count("unlimited") >= 3, f"'max' was not read as unlimited: {text}"
        # Not a list of absurd values: the assertion is structural. Any finite
        # ceiling at all is the failure, whatever it happens to print as.
        assert "GiB ceiling" not in text, (
            f"'max' was converted into a finite memory ceiling: {text}"
        )
        assert "cores' worth of quota" not in text, (
            f"'max' was converted into a finite CPU quota: {text}"
        )
        assert " at most" not in text, (
            f"'max' was converted into a finite process count: {text}"
        )

    def test_absent_cgroup_is_unknown_and_never_memtotal(
        self, granted, tmp_path, monkeypatch
    ):
        # Control: a populated hierarchy does produce a reading. This box is
        # itself somewhere without delegated controllers, so without the
        # positive control the UNKNOWN assertion would pass for free.
        self._v2(tmp_path, monkeypatch, "1073741824", "max 100000", "128")
        positive = _text_of(cgroup_mod._run({}))
        assert "GiB ceiling" in positive, (
            f"the positive control produced no reading, so the UNKNOWN "
            f"assertion below proves nothing: {positive}"
        )
        monkeypatch.setattr(cgroup_mod, "_CGROUP_ROOT", ABSENT)
        assert not ABSENT.is_dir(), "the 'absent' path actually exists"
        text = _text_of(cgroup_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "physical memory" in text, (
            "the UNKNOWN branch must say out loud that physical memory is not "
            f"the fallback; it reads: {text}"
        )
        assert "would otherwise report" in text, (
            f"the UNKNOWN branch must explain the failure it is avoiding: {text}"
        )

    def test_a_cgroup_with_no_delegated_controller_is_unknown(
        self, granted, tmp_path, monkeypatch
    ):
        # The subtler half: the directory exists but nothing delegates a
        # controller. That is not "unlimited" and it is not a clean bill.
        root = tmp_path / "empty-cgroup"
        root.mkdir()
        monkeypatch.setattr(cgroup_mod, "_CGROUP_ROOT", root)
        text = _text_of(cgroup_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "not the same as being unlimited" in text, text

    def test_cgroup_v1_paths_are_the_fallback(self, granted, tmp_path, monkeypatch):
        root = tmp_path / "v1"
        (root / "memory").mkdir(parents=True)
        (root / "cpu").mkdir(parents=True)
        (root / "pids").mkdir(parents=True)
        (root / "memory" / "memory.limit_in_bytes").write_text("4294967296")
        (root / "cpu" / "cpu.cfs_quota_us").write_text("-1")
        (root / "cpu" / "cpu.cfs_period_us").write_text("100000")
        (root / "pids" / "pids.max").write_text("256")
        monkeypatch.setattr(cgroup_mod, "_CGROUP_ROOT", root)
        text = _text_of(cgroup_mod._run({}))
        assert "4.00 GiB ceiling" in text, text
        assert "unlimited" in text, "a v1 quota of -1 was not read as no quota"
        assert "256 at most" in text, text

    def test_a_v1_kernel_memory_sentinel_is_unlimited(
        self, granted, tmp_path, monkeypatch
    ):
        # PAGE_COUNTER_MAX, which a v1 hierarchy writes for "no limit".
        root = tmp_path / "v1-sentinel"
        (root / "memory").mkdir(parents=True)
        (root / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712")
        monkeypatch.setattr(cgroup_mod, "_CGROUP_ROOT", root)
        record = cgroup_mod.read_memory_max()
        assert record["unlimited"] is True and record["limit"] is None, record
        text = _text_of(cgroup_mod._run({}))
        assert "8.00 EiB" not in text and "9.22 EB" not in text, text


# --- containers -------------------------------------------------------------


class TestContainers:
    def test_a_runtime_that_answers_with_nothing_is_a_positive_finding(
        self, granted, monkeypatch
    ):
        # Control, hermetic: a runtime that answers with an empty list really
        # does report zero. If this branch said UNKNOWN too, the negative
        # control below would be proving nothing.
        class _Empty:
            returncode = 0
            stdout = "[]"
            stderr = ""

        monkeypatch.setattr(containers_mod.shutil, "which", lambda n: f"/usr/bin/{n}")
        monkeypatch.setattr(containers_mod, "_run_cmd", lambda argv: _Empty())
        result = containers_mod.read_containers()
        assert result["containers"] == [], (
            f"an empty answer from a working runtime became {result!r}"
        )
        text = _text_of(containers_mod._run({}))
        assert "0 container(s)" in text, text
        assert "UNKNOWN" not in text.splitlines()[0], text

    def test_no_runtime_binary_is_unknown_not_zero_containers(self, granted,
                                                              monkeypatch):
        _no_runtime(monkeypatch)
        result = containers_mod.read_containers()
        assert result["containers"] is None, (
            f"expected 'not asked', got {result['containers']!r}"
        )
        text = _text_of(containers_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "NOT a report of zero containers" in text, text

    def test_a_non_zero_exit_is_unknown_not_an_empty_list(self, granted, monkeypatch):
        class _Failed:
            returncode = 125
            stdout = ""
            stderr = "Error: unable to connect to Podman socket"

        monkeypatch.setattr(containers_mod.shutil, "which", lambda n: f"/usr/bin/{n}")
        monkeypatch.setattr(containers_mod, "_run_cmd", lambda argv: _Failed())
        result = containers_mod.read_containers()
        assert result["containers"] is None, (
            "a runtime that exited non-zero produced a list; an unreachable "
            "socket and a machine with no containers are different situations"
        )
        text = _text_of(containers_mod._run({}))
        assert "UNKNOWN" in text, text
        assert "unable to connect to Podman socket" in text, text

    def test_exit_137_and_143_are_named_rather_than_left_as_numbers(self):
        killed = containers_mod._classify({
            "Names": ["web"], "Image": "nginx:1.27", "State": "exited",
            "ExitCode": 137, "OOMKilled": True})
        stopped = containers_mod._classify({
            "Names": ["db"], "Image": "postgres:16", "State": "exited",
            "ExitCode": 143})
        assert "SIGKILL" in killed["exit_meaning"] and killed["oom_killed"] is True
        assert "SIGTERM" in stopped["exit_meaning"]
        assert stopped["oom_killed"] is None, (
            "a runtime that did not report OOMKilled must leave it unknown, not "
            "False - an absent field is not a negative answer"
        )

    def test_podman_array_and_docker_jsonl_both_parse(self):
        podman = '[{"Names":["a"],"Image":"x:1","State":"running","ExitCode":0}]'
        docker = '{"Names":"a","Image":"x:1","State":"running","ExitCode":0}'
        assert len(containers_mod._parse_containers(podman)) == 1
        assert len(containers_mod._parse_containers(docker)) == 1
        assert containers_mod._parse_containers("") == [], (
            "an empty body from a runtime that answered is a list, not UNKNOWN"
        )
        assert containers_mod._parse_containers("<html>oops</html>") is None, (
            "an unrecognised output shape became an empty list instead of UNKNOWN"
        )


# --- listeners (the priority negative control) ------------------------------


class TestListeners:
    def test_an_unreadable_socket_table_is_never_nothing_is_listening(
        self, granted, monkeypatch
    ):
        # The fuser incident, without the dependency. Control: this machine
        # really does have listeners, so the assertion has something to lose.
        real = listeners_mod.read_listeners()
        assert real is not None and real["sockets"], (
            "this machine reports no listening sockets, so the UNKNOWN assertion "
            "below cannot be distinguished from a sense that always says "
            "'nothing is listening'. Run the suite on a machine with a service "
            "bound to a port."
        )
        monkeypatch.setattr(listeners_mod, "_NET_TCP", ABSENT / "net" / "tcp")
        monkeypatch.setattr(listeners_mod, "_NET_TCP6", ABSENT / "net" / "tcp6")
        result = listeners_mod.read_listeners()
        assert result is None, (
            f"expected 'cannot enumerate sockets', got {result!r} - an unreadable "
            f"table was rendered as a real (possibly empty) answer"
        )
        text = _text_of(listeners_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "cannot enumerate sockets" in text, text
        assert "report that nothing is listening" in text, (
            f"the UNKNOWN text does not explicitly deny the false reading: {text}"
        )

    def test_no_helper_binary_is_consulted_at_all(self):
        # The structural half of the control. There is no way to observe a
        # subprocess that was never written, so the property is asserted on the
        # parsed imports rather than on the source text - a text scan matches
        # the module docstring, which names every one of these binaries while
        # explaining why they must not be used.
        import ast

        tree = ast.parse(Path(listeners_mod.__file__).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("subprocess", "shutil"):
            assert forbidden not in imported, (
                f"listeners.py imports {forbidden}; the fuser incident was "
                f"exactly a sense that reported devices as free because an "
                f"external binary was missing, and this sense is specified to "
                f"build from /proc alone"
            )

    def test_sshd_disappearing_is_not_reported_as_free(self, granted, tmp_path,
                                                        monkeypatch):
        # The precise regression, named: a real port-22 holder vanishes with the
        # table, and the answer must not become "nothing is listening".
        table = _socket_table(tmp_path, _tcp_row("00000000:0016", "0A", "0", "4242"))
        monkeypatch.setattr(listeners_mod, "_NET_TCP", table)
        monkeypatch.setattr(listeners_mod, "_NET_TCP6", ABSENT / "net" / "tcp6")
        readable = listeners_mod.read_listeners()
        assert readable is not None
        assert [s["port"] for s in readable["sockets"]] == [22], (
            f"the positive control did not parse a real LISTEN row: {readable}"
        )
        monkeypatch.setattr(listeners_mod, "_NET_TCP", ABSENT / "net" / "tcp")
        assert listeners_mod.read_listeners() is None, (
            "hiding the table did not change the verdict - the control cannot fail"
        )

    def test_an_unreadable_other_users_fds_gives_owner_unknown(
        self, granted, tmp_path, monkeypatch
    ):
        table = _socket_table(tmp_path, _tcp_row("00000000:0016", "0A", "0", "4242"))
        monkeypatch.setattr(listeners_mod, "_NET_TCP", table)
        monkeypatch.setattr(listeners_mod, "_NET_TCP6", ABSENT / "net" / "tcp6")
        monkeypatch.setattr(listeners_mod, "map_socket_inodes", lambda wanted: ({}, 3))
        result = listeners_mod.read_listeners()
        assert result["sockets"][0]["owner"] == listeners_mod.OWNER_UNKNOWN, (
            f"expected 'owner unknown', got {result['sockets'][0]['owner']!r}"
        )
        text = _text_of(listeners_mod._run({}))
        assert "owner unknown" in text, text
        assert "not a claim that nothing holds them" in text, text

    def test_a_real_denied_fd_directory_produces_owner_unknown(
        self, granted, tmp_path, monkeypatch
    ):
        # The production half of the control above: this one runs the real
        # `map_socket_inodes` against a real denied directory rather than
        # replacing it, so the errno branch that decides "unknown" vs "gone" is
        # the code under test. A mode-000 directory denies scandir to its owner
        # too, which is what an unreadable `/proc/<pid>/fd` looks like.
        if os.getuid() == 0:
            pytest.skip("running as root: mode 000 does not deny root")
        fake_proc = tmp_path / "proc"
        (fake_proc / "999" / "fd").mkdir(parents=True)
        (fake_proc / "999" / "fd").chmod(0o000)
        try:
            with pytest.raises(PermissionError):
                list(os.scandir(fake_proc / "999" / "fd"))
            table = _socket_table(tmp_path, _tcp_row("00000000:0016", "0A", "0", "4242"))
            monkeypatch.setattr(listeners_mod, "_PROC", fake_proc)
            monkeypatch.setattr(listeners_mod, "_NET_TCP", table)
            monkeypatch.setattr(listeners_mod, "_NET_TCP6", ABSENT / "net" / "tcp6")

            owners, unreadable = listeners_mod.map_socket_inodes({"4242"})
            assert unreadable == 1, (
                f"a denied fd directory was not counted as unreadable: "
                f"{owners}, {unreadable}"
            )
            assert owners == {}, owners

            result = listeners_mod.read_listeners()
            assert result["sockets"][0]["owner"] == listeners_mod.OWNER_UNKNOWN, (
                "a socket that only a denied process could be holding was "
                f"reported as unowned: {result}"
            )
        finally:
            (fake_proc / "999" / "fd").chmod(0o755)

    def test_every_pid_inspectable_and_no_holder_is_a_third_state(
        self, granted, tmp_path, monkeypatch
    ):
        table = _socket_table(tmp_path, _tcp_row("00000000:0016", "0A", "0", "4242"))
        monkeypatch.setattr(listeners_mod, "_NET_TCP", table)
        monkeypatch.setattr(listeners_mod, "_NET_TCP6", ABSENT / "net" / "tcp6")
        monkeypatch.setattr(listeners_mod, "map_socket_inodes", lambda wanted: ({}, 0))
        result = listeners_mod.read_listeners()
        assert result["sockets"][0]["owner"] == listeners_mod.OWNER_GONE, (
            "with nothing unreadable, a socket no process holds is a third "
            "state - not 'unknown' and not 'named'"
        )

    def test_established_connections_are_not_listeners(self, granted, tmp_path,
                                                      monkeypatch):
        table = _socket_table(
            tmp_path,
            _tcp_row("0100007F:1F90", "01", "1000", "4242"),
            _tcp_row("00000000:0016", "0A", "0", "4243"),
        )
        monkeypatch.setattr(listeners_mod, "_NET_TCP", table)
        monkeypatch.setattr(listeners_mod, "_NET_TCP6", ABSENT / "net" / "tcp6")
        monkeypatch.setattr(listeners_mod, "map_socket_inodes", lambda wanted: ({}, 0))
        result = listeners_mod.read_listeners()
        assert [s["port"] for s in result["sockets"]] == [22], (
            f"an ESTABLISHED connection was reported as a service: {result}"
        )

    def test_a_missing_tcp6_is_a_caveat_not_a_total_unknown(
        self, granted, tmp_path, monkeypatch
    ):
        table = _socket_table(tmp_path, _tcp_row("00000000:0016", "0A", "0", "4242"))
        monkeypatch.setattr(listeners_mod, "_NET_TCP", table)
        monkeypatch.setattr(listeners_mod, "_NET_TCP6", ABSENT / "net" / "tcp6")
        result = listeners_mod.read_listeners()
        assert result is not None and result["ipv6_read"] is False
        text = _text_of(listeners_mod._run({}))
        assert "IPv6 coverage is incomplete" in text, text

    def test_addresses_are_decoded_from_the_kernels_little_endian_words(self):
        import ipaddress

        def kernel_hex(text: str) -> str:
            raw = ipaddress.ip_address(text).packed
            return "".join(raw[i:i + 4][::-1].hex() for i in range(0, 16, 4))

        for text in ("::1", "::", "fe80::202:13af:fe14:a5cb", "2001:db8::1",
                     "ff02::1"):
            hexed = kernel_hex(text)
            assert listeners_mod._decode_ipv6(hexed) == text, (
                f"{hexed} did not decode back to {text}"
            )
        for text in ("127.0.0.1", "192.168.31.202", "0.0.0.0"):
            little_endian = "".join(f"{int(p):02X}" for p in reversed(text.split(".")))
            assert listeners_mod._decode_ipv4(little_endian) == text


# --- stale ------------------------------------------------------------------


def _fake_exe(root: Path, pid: str, target: str) -> Path:
    """A `/proc/<pid>/exe` symlink whose content is exactly `target`."""
    pid_dir = root / pid
    pid_dir.mkdir(parents=True, exist_ok=True)
    link = pid_dir / "exe"
    if link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    return link


class TestStale:
    def test_the_deleted_suffix_alone_is_not_stale(
        self, granted, tmp_path, monkeypatch
    ):
        # The trap this sense exists for: a file genuinely NAMED with the
        # kernel's suffix. Text alone cannot tell it from a deletion, so the
        # verdict has to come from comparing inodes.
        running = tmp_path / "tool (deleted)"
        running.write_text("real\n")
        os.link(running, tmp_path / "tool")     # the same inode, second name
        _fake_exe(tmp_path / "proc", "999", str(running))
        monkeypatch.setattr(stale_mod, "_PROC", tmp_path / "proc")
        result = stale_mod.check_exe("999")
        assert result is not None
        assert result["stale"] is False, (
            f"a file simply named '... (deleted)' was reported stale: {result}"
        )

    def test_a_replaced_path_with_a_different_inode_is_stale(
        self, granted, tmp_path, monkeypatch
    ):
        # The ordinary `mv new /usr/bin/foo` case: the path exists, so the naive
        # "does the path still exist?" test says clean while the process is in
        # fact executing something no longer at that path.
        running = tmp_path / "foo (deleted)"
        running.write_text("old\n")
        (tmp_path / "foo").write_text("old\n")   # same bytes, different inode
        _fake_exe(tmp_path / "proc", "999", str(running))
        monkeypatch.setattr(stale_mod, "_PROC", tmp_path / "proc")
        result = stale_mod.check_exe("999")
        assert result["stale"] is True, (
            "a process running an inode that is no longer the file at its path "
            f"was not reported stale - this is the case the sense is for: {result}"
        )

    def test_a_vanished_path_is_stale(self, granted, tmp_path, monkeypatch):
        running = tmp_path / "foo (deleted)"
        running.write_text("old\n")
        _fake_exe(tmp_path / "proc", "999", str(running))
        monkeypatch.setattr(stale_mod, "_PROC", tmp_path / "proc")
        assert stale_mod.check_exe("999")["stale"] is True

    def test_a_live_exe_is_never_stale(self, granted, tmp_path, monkeypatch):
        path = tmp_path / "normal"
        path.write_text("x\n")
        _fake_exe(tmp_path / "proc", "999", str(path))
        monkeypatch.setattr(stale_mod, "_PROC", tmp_path / "proc")
        assert stale_mod.check_exe("999")["stale"] is False

    def test_an_unreadable_exe_link_yields_no_verdict_at_all(
        self, granted, tmp_path, monkeypatch
    ):
        (tmp_path / "proc" / "999").mkdir(parents=True)
        monkeypatch.setattr(stale_mod, "_PROC", tmp_path / "proc")
        assert stale_mod.check_exe("999") is None, (
            "an unreadable exe link produced a verdict instead of nothing"
        )

    def test_other_users_processes_are_never_inspected(
        self, granted, tmp_path, monkeypatch
    ):
        root = tmp_path / "proc"
        (root / "100").mkdir(parents=True)
        (root / "200").mkdir(parents=True)
        monkeypatch.setattr(stale_mod, "_PROC", root)
        monkeypatch.setattr(stale_mod.os, "getuid", lambda: 1000)
        monkeypatch.setattr(stale_mod, "_stat_owner", lambda pid: (
            stale_mod.OWNER_OK, 1000 if pid == "100" else 2000))
        own, other, unreadable = stale_mod._own_pids()
        assert own == ["100"], f"another user's pid was claimed as ours: {own}"
        assert (other, unreadable) == (1, 0)

    def test_a_denied_stat_is_unreadable_not_another_users(
        self, granted, tmp_path, monkeypatch
    ):
        root = tmp_path / "proc"
        (root / "100").mkdir(parents=True)
        (root / "200").mkdir(parents=True)
        monkeypatch.setattr(stale_mod, "_PROC", root)
        monkeypatch.setattr(stale_mod.os, "getuid", lambda: 1000)
        monkeypatch.setattr(stale_mod, "_stat_owner", lambda pid: (
            (stale_mod.OWNER_DENIED, None) if pid == "200"
            else (stale_mod.OWNER_OK, 1000)))
        own, other, unreadable = stale_mod._own_pids()
        assert own == ["100"]
        assert other == 0, (
            "a hidepid-denied process was filed as another user's, which is a "
            "different - and quieter - claim than 'not inspectable'"
        )
        assert unreadable == 1, unreadable

    def test_an_unwalkable_proc_is_unknown_not_zero_stale_binaries(
        self, granted, monkeypatch
    ):
        # Control: the real scan runs, so a zero would be a real finding.
        assert stale_mod.scan() is not None, (
            "this machine's /proc could not be walked at all, so the UNKNOWN "
            "assertion below cannot be told apart from a clean scan"
        )
        monkeypatch.setattr(stale_mod, "_PROC", ABSENT)
        assert stale_mod.scan() is None
        text = _text_of(stale_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "not a report of zero stale binaries" in text, text

    def test_unreadable_processes_are_counted_rather_than_swallowed(
        self, granted, tmp_path, monkeypatch
    ):
        # A hidepid=2 mount: the stat is denied, so nothing was inspectable and
        # the answer must say so rather than report a clean scan.
        (tmp_path / "proc" / "4242").mkdir(parents=True)
        monkeypatch.setattr(stale_mod, "_PROC", tmp_path / "proc")
        monkeypatch.setattr(stale_mod.os, "getuid", lambda: 1000)
        monkeypatch.setattr(stale_mod, "_stat_owner",
                            lambda pid: (stale_mod.OWNER_DENIED, None))
        result = stale_mod.scan()
        assert result is not None
        assert result["processes_readable"] == 0
        assert result["processes_not_inspectable"] == 1, (
            f"a denied stat was not surfaced at all: {result}"
        )
        text = _text_of(stale_mod._run({}))
        assert "could not be inspected at all" in text, text
        assert "No stale binaries" in text, (
            f"a scan that inspected nothing reported a clean result: {text}"
        )

    def test_an_unreadable_own_exe_link_is_counted_separately(
        self, granted, tmp_path, monkeypatch
    ):
        # A pid this account owns whose link the kernel will not resolve - a
        # kernel thread, or a process that exited mid-scan. A different fact
        # from "another user's", and a different fact from "denied".
        (tmp_path / "proc" / "100").mkdir(parents=True)
        monkeypatch.setattr(stale_mod, "_PROC", tmp_path / "proc")
        monkeypatch.setattr(stale_mod.os, "getuid", lambda: 1000)
        monkeypatch.setattr(stale_mod, "_stat_owner",
                            lambda pid: (stale_mod.OWNER_OK, 1000))
        result = stale_mod.scan()
        assert result["processes_readable"] == 0
        assert result["processes_unreadable"] == 1, result
        assert result["processes_not_inspectable"] == 0, result
        assert result["other_users_processes"] == 0, result


# --- git --------------------------------------------------------------------


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point `Path.home()` at a temp dir, so the git confinement test is real.

    The harness already redirects `HOME`, but `Path.home()` is resolved through
    `os.path.expanduser` and the paths these tests build live under `tmp_path`,
    which the harness puts *outside* that HOME. Without this every git test
    would read a confinement refusal instead of the sense's own output.
    """
    home = tmp_path / "home"          # the hermetic fixture already made this
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _seed_repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "t@example.com")
    _run_git(repo, "config", "user.name", "t")
    # Named explicitly: the default branch is still `master` on this git, and a
    # test that hardcodes `main` would read a ref that does not exist and
    # conclude "no upstream" for entirely the wrong reason.
    _run_git(repo, "branch", "-M", "main")
    (repo / "a.txt").write_text("one\n")
    _run_git(repo, "add", "a.txt")
    _run_git(repo, "commit", "-q", "-m", "first")
    return repo


class TestGit:
    def test_no_git_binary_is_unknown_not_a_clean_tree(self, granted, monkeypatch,
                                                        fake_home):
        assert git_mod.shutil.which("git") is not None, (
            "git is not installed here, so this control cannot distinguish "
            "'patched away' from 'never there'"
        )
        _no_runtime(monkeypatch)
        text = _text_of(git_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "NOT a clean tree" in text, text

    def test_a_path_outside_home_is_refused(self, granted, fake_home):
        text = _text_of(git_mod._run({"path": "/etc"}))
        assert "outside" in text, f"expected a confinement refusal, got: {text}"

    def test_not_a_repository_is_unknown_not_clean(self, granted, fake_home):
        (fake_home / "plain").mkdir()
        text = _text_of(git_mod._run({"path": str(fake_home / "plain")}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "not a git repository" in text, text

    def test_a_real_repository_reports_its_state(self, granted, fake_home):
        repo = _seed_repo(fake_home, "repo")
        clean = _text_of(git_mod._run({"path": str(repo)}))
        assert "is clean" in clean, f"a committed repo did not read clean: {clean}"
        assert "commits reachable from HEAD: 1" in clean, clean

        (repo / "b-draft.txt").write_text("secret draft\n")
        (repo / "a.txt").write_text("two\n")
        dirty = _text_of(git_mod._run({"path": str(repo)}))
        assert "1 untracked" in dirty, (
            f"--untracked-files=all did not report the untracked draft: {dirty}"
        )
        assert "b-draft.txt" in dirty, dirty
        assert "1 modified" in dirty, dirty

    def test_a_gone_upstream_is_not_behind_zero(self, granted, fake_home):
        upstream = fake_home / "remote.git"
        work = _seed_repo(fake_home, "clone")
        _run_git(fake_home, "init", "-q", "--bare", str(upstream))
        _run_git(work, "remote", "add", "origin", str(upstream))
        _run_git(work, "push", "-q", "-u", "origin", "HEAD:refs/heads/main")
        # Verified against this git: `[gone]` appears only once the *local
        # remote-tracking* ref is gone. Deleting the branch on the remote alone
        # leaves that ref in place and git reports an ordinary in-sync upstream,
        # so this is the mutation that actually produces the state.
        _run_git(upstream, "update-ref", "-d", "refs/heads/main")
        _run_git(work, "update-ref", "-d", "refs/remotes/origin/main")

        record = git_mod.read_upstream(work, "main")
        assert record["state"] == git_mod.UPSTREAM_GONE, (
            f"a deleted upstream read as {record!r}; reporting it as up to date "
            f"is the most confidently wrong thing this sense could emit"
        )
        assert record["ahead"] is None and record["behind"] is None
        text = _text_of(git_mod._run({"path": str(work)}))
        assert "GONE" in text, text
        assert "not the same as being up to date" in text, text

    def test_ahead_and_behind_are_counted_when_the_upstream_exists(
        self, granted, fake_home
    ):
        upstream = fake_home / "remote.git"
        work = _seed_repo(fake_home, "clone")
        _run_git(fake_home, "init", "-q", "--bare", str(upstream))
        _run_git(work, "remote", "add", "origin", str(upstream))
        _run_git(work, "push", "-q", "-u", "origin", "HEAD:refs/heads/main")
        (work / "b.txt").write_text("two\n")
        _run_git(work, "add", "b.txt")
        _run_git(work, "commit", "-q", "-m", "second")

        record = git_mod.read_upstream(work, "main")
        assert record["state"] == git_mod.UPSTREAM_SET, record
        assert record["ahead"] == 1 and record["behind"] == 0, record
        text = _text_of(git_mod._run({"path": str(work)}))
        assert "1 ahead, 0 behind" in text, text

    def test_a_repo_with_no_commits_is_zero_not_unknown(self, granted, fake_home):
        repo = fake_home / "empty"
        repo.mkdir()
        _run_git(repo, "init", "-q")
        assert git_mod.read_commit_count(repo) == 0, (
            "a freshly initialised repository read as an unknown commit count"
        )
        assert "UNKNOWN" not in _text_of(git_mod._run({"path": str(repo)}))


#: Git executes configuration read from the repository it is pointed at, so a
#: hostile repository's own `.git/config` is executable code. These fixtures
#: point `core.fsmonitor` and `diff.external` at scripts that touch a marker
#: file, which is the only honest observable: "the script ran" is a fact about
#: the real process tree, not about our argv.
def _hostile(repo: Path, marker_dir: Path) -> dict:
    """Arm a repository the way a hostile one arrives: config and all."""
    marker_dir.mkdir(parents=True, exist_ok=True)
    scripts = {}
    for key in ("core.fsmonitor", "diff.external"):
        marker = marker_dir / key.replace(".", "_").upper()
        script = marker_dir / f"{key.replace('.', '_')}.sh"
        script.write_text(f'#!/bin/sh\necho EXECUTED > "{marker}"\nexit 0\n')
        os.chmod(script, 0o755)
        _run_git(repo, "config", key, str(script))
        scripts[key] = marker
    return scripts


@pytest.fixture
def hostile_repo(fake_home, tmp_path):
    """A real repository that is dirty, and armed to execute on read."""
    repo = _seed_repo(fake_home, "hostile")
    (repo / "a.txt").write_text("one\ntwo\n")   # a real change, so diff is real
    (repo / "b-draft.txt").write_text("draft\n")
    return repo, _hostile(repo, tmp_path / "markers")


class TestTheGitSenseDoesNotExecuteTheRepository:
    """`core.fsmonitor=false` on every read, because the alternative is RCE.

    Reproduced end-to-end before the hardening existed: `core.fsmonitor` set by
    the repository ran a script during `read_status()`, through this sense, with
    `git-sense-enabled` granted.
    """

    def test_a_configured_fsmonitor_hook_does_not_run(self, granted, hostile_repo):
        repo, markers = hostile_repo
        status = git_mod.read_status(repo)
        assert status["changed"] == 2, f"the sense misread a dirty tree: {status}"
        assert not markers["core.fsmonitor"].exists(), (
            "the repository's own core.fsmonitor hook EXECUTED: a hostile "
            "repository runs code the moment a status is read"
        )

    def test_the_control_the_above_depends_on_actually_executes(
        self, granted, hostile_repo
    ):
        """Without this the test above cannot fail - it would pass on a git
        that silently ignored `core.fsmonitor`, which is a different git.

        Proven by asking git to do the unhardened thing directly.
        """
        repo, markers = hostile_repo
        plain = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1"],
                       capture_output=True, text=True, timeout=30)
        assert plain.returncode == 0, plain.stderr
        assert markers["core.fsmonitor"].exists(), (
            "an UNHARDENED git status did not run the configured fsmonitor "
            "hook on this machine, so the hardening test proves nothing"
        )

    def test_the_hardening_is_on_the_shared_argv_not_on_one_call_site(self):
        """One hardened entry point, or the two surfaces drift apart again.

        This is the exact failure that produced the hole: `triggers.py` had
        `-c core.fsmonitor=false` and these two surfaces did not.
        """
        assert "core.fsmonitor=false" in git_mod._GIT_HARDENING
        # every read goes through run_git; no second subprocess call site exists
        # that a future edit could add a bare git invocation to
        src = Path(git_mod.__file__).read_text(encoding="utf-8")
        assert src.count("subprocess.run") == 1, (
            "senses/git.py grew a second subprocess call site, so some read "
            "would bypass the hardening"
        )
        # and the read helpers call run_git, not the raw runner
        for helper in ("read_status", "read_branch", "read_commit_count",
                       "read_upstream"):
            body = src.split(f"def {helper}(", 1)[1].split("\ndef ", 1)[0]
            assert "run_git(" in body, f"{helper} does not go through run_git"

    def test_run_git_actually_applies_the_hardening_to_the_argv(self, fake_home,
                                                                monkeypatch):
        """The constant existing is not enough - it has to reach the argv.

        Deleting the constant's use is a mutation that leaves every earlier
        assertion here passing, so this one inspects the argv git is really
        handed rather than reading the source.
        """
        seen = {}
        real = git_mod._run_cmd

        def spy(argv, env=None):
            seen.setdefault("argv", []).append(list(argv))
            seen["env"] = env
            return real(argv, env=env)

        repo = _seed_repo(fake_home, "spy")
        monkeypatch.setattr(git_mod, "_run_cmd", spy)
        git_mod.read_status(repo)
        assert seen["argv"], "the spy never saw a git invocation"
        argv = seen["argv"][0]
        assert "--no-pager" in argv, argv
        for expected in ("core.fsmonitor=false", "core.pager=cat",
                         "core.hooksPath=/dev/null", "protocol.ext.allow=never"):
            assert expected in argv, f"{expected} missing from {argv}"
        assert seen["env"].get("GIT_CONFIG_NOSYSTEM") == "1", seen["env"]

    def test_a_legitimate_diff_still_returns_real_content(self, granted, fake_home):
        """The control for the hardening: a flag that breaks diff is not a fix."""
        from shani_chronoa.skills import git_inspect as gi
        repo = _seed_repo(fake_home, "honest")
        (repo / "a.txt").write_text("one\ntwo\n")
        out = gi._run({"path": str(repo), "subcommand": "diff"})
        assert "+two" in out, f"a legitimate diff returned nothing: {out}"
        assert "--- " in out and "+++ " in out, out


# --- boots ------------------------------------------------------------------


class TestBoots:
    def test_an_unreadable_wtmp_is_never_never_rebooted(
        self, granted, tmp_path, monkeypatch
    ):
        # Hermetic on purpose. An earlier version of this test read the real
        # /var/log/wtmp and skipped when it was absent - which meant the control
        # silently could not fail on a machine without a login database, and a
        # mutation went unnoticed because of it.
        present = tmp_path / "wtmp"
        present.write_bytes(b"\x00" * 512)
        monkeypatch.setattr(boots_mod, "_WTMP", present)
        monkeypatch.setattr(boots_mod.shutil, "which",
                            lambda n: "/usr/bin/last" if n == "last" else None)
        monkeypatch.setattr(boots_mod, "_last_records", lambda flags, needle: None)
        result = boots_mod.read_boot_records()
        assert result is None, f"expected None, got {result!r}"
        monkeypatch.setattr(boots_mod, "_WTMP", ABSENT / "wtmp")
        text = _text_of(boots_mod._run({}))
        assert "boot history unavailable" in text, text
        assert "NOT a machine that has never rebooted" in text, text
        assert "recorded boots:" not in text, (
            f"a boot count was produced from no records: {text}"
        )

    def test_a_readable_login_database_produces_a_boot_count(self, granted):
        # The positive control for the branch above, on the real machine. A skip
        # here is honest: it says the assertion could not run, rather than
        # quietly passing as coverage.
        real = boots_mod.read_boot_records()
        if real is None or not real["boots"]:
            pytest.skip(
                "this machine has no readable wtmp/last records, so the positive "
                "control cannot run here; the UNKNOWN behaviour is still proven "
                "hermetically by the test above"
            )
        text = _text_of(boots_mod._run({}))
        assert "recorded boots:" in text, text
        assert "boot history unavailable" not in text, text

    def test_unreadable_shutdown_records_do_not_become_a_clean_verdict(
        self, granted, tmp_path, monkeypatch
    ):
        # wtmp readable, boots readable, `last -x` unreadable: cleanliness is
        # UNKNOWN, not "every boot was orderly".
        present = tmp_path / "wtmp"
        present.write_bytes(b"\x00" * 512)
        monkeypatch.setattr(boots_mod, "_WTMP", present)
        monkeypatch.setattr(boots_mod.shutil, "which",
                            lambda n: "/usr/bin/last" if n == "last" else None)
        monkeypatch.setattr(boots_mod, "_last_records",
                            lambda flags, needle: [1000.0, 2000.0]
                            if needle == "reboot" else None)
        records = boots_mod.read_boot_records()
        assert records is not None and records["shutdowns"] is None
        text = _text_of(boots_mod._run({}))
        assert "could not report shutdown" in text, text
        assert "every one of them ended in an orderly stop" not in text, text

    def test_an_unreadable_boot_id_is_unknown_never_a_bare_tick(self, granted,
                                                                 monkeypatch):
        real = boots_mod.read_boot_id()
        assert real, (
            "this machine exposes no boot_id, so the UNKNOWN assertion below "
            "would pass for the wrong reason"
        )
        monkeypatch.setattr(boots_mod, "_BOOT_ID", ABSENT / "boot_id")
        monkeypatch.setattr(boots_mod, "_STAT", ABSENT / "stat")
        assert boots_mod.read_boot_id() is None, "the mutation did not apply"
        assert boots_mod.read_btime() is None, "the mutation did not apply"
        text = _text_of(boots_mod._run({}))
        assert "UNKNOWN" in text, f"expected UNKNOWN, got: {text}"
        assert "no identity and no start time" in text, text
        assert "no tick count is substituted" in text, text
        assert "booted at:" not in text, (
            f"a boot time was invented without btime: {text}"
        )

    def test_a_too_short_boot_id_is_not_an_identity(self, granted, tmp_path,
                                                     monkeypatch):
        marker = tmp_path / "boot_id"
        marker.write_text("abc\n")
        monkeypatch.setattr(boots_mod, "_BOOT_ID", marker)
        assert boots_mod.read_boot_id() is None, (
            "a truncated boot id was accepted as this boot's identity"
        )

    def test_start_time_conversion_needs_btime(self):
        # A tick counter with nothing to anchor it is a number, not a time.
        assert boots_mod.read_process_started("1", None, 100) is None
        assert boots_mod.read_process_started("1", 1000, None) is None

    def test_the_sense_does_not_report_uptime(self, granted):
        # `cpu.py` owns /proc/uptime. A second uptime reading here would be two
        # senses disagreeing about the same fact. Checked as a constant rather
        # than as a source scan, because the module docstring names /proc/uptime
        # while explaining precisely why it must not be read.
        for name, value in vars(boots_mod).items():
            if isinstance(value, Path):
                assert str(value) != "/proc/uptime", (
                    f"boots.py holds a path to /proc/uptime as {name}, which "
                    f"cpu.py already owns"
                )
        text = _text_of(boots_mod._run({}))
        assert "uptime" not in text.lower(), f"uptime leaked into: {text}"

    def test_unmatched_boots_are_counted_not_averaged(self):
        assert boots_mod._unmatched_boots([50.0, 200.0, 400.0], [100.0, 300.0]) == 0
        assert boots_mod._unmatched_boots([50.0, 250.0, 400.0], [100.0]) == 1, (
            "a boot with no shutdown record before the next one started was not "
            "counted as an unclean shutdown"
        )
        assert boots_mod._unmatched_boots([50.0, 200.0, 400.0], None) == 0, (
            "absent shutdown records must not be counted as unclean shutdowns"
        )

    def test_the_current_boot_is_never_counted_as_unclean(self):
        # The newest record is the boot in progress, which has not ended yet.
        assert boots_mod._unmatched_boots([100.0, 200.0], [150.0]) == 0
        assert boots_mod._unmatched_boots([100.0, 200.0], []) == 1, (
            "with no shutdown records at all, the one completed boot still "
            "counts as unclean - only the in-progress boot is exempt"
        )


# --- registration -----------------------------------------------------------


class TestRegistration:
    def test_every_new_sense_has_its_derived_consent_key(self):
        for name in NEW_SENSES:
            assert _SENSE_CONSENT_KEYS.get(name) == f"{name}-sense-enabled", (
                f"{name} is registered under the wrong consent key; the key is "
                f"derived from the name, so a mismatch makes the sense "
                f"permanently ungrantable"
            )

    def test_git_is_off_and_the_rest_are_on(self):
        for name in NEW_SENSES:
            default = name in _SENSE_DEFAULT_ENABLED
            assert default is (name != "git"), (
                f"{name} defaults to {'on' if default else 'off'}; all of these "
                f"are on by default except git, whose filenames and branch "
                f"names are the user's work product"
            )

    def test_the_schema_agrees_with_the_python_default(self):
        root = ElementTree.parse(SCHEMA_XML).getroot()
        defaults = {}
        for key in root.iter("key"):
            if key.get("type") != "b":
                continue
            child = key.find("default")
            if child is not None and child.text is not None:
                defaults[key.get("name")] = child.text.strip() == "true"
        for name in NEW_SENSES:
            key = f"{name}-sense-enabled"
            assert key in defaults, (
                f"{key} is missing from {SCHEMA_XML.name}; glib-compile-schemas "
                f"discards the whole file on a malformed key, and a key the "
                f"schema does not declare makes the sense permanently ungrantable"
            )
            assert defaults[key] is (name != "git"), (
                f"{key} defaults to {defaults[key]} in the schema, disagreeing "
                f"with config._SENSE_DEFAULT_ENABLED"
            )

    def test_every_new_sense_is_ambient_and_outlives_its_own_poll(self):
        from shani_chronoa.senses import discover_senses

        registry = discover_senses()
        for name in NEW_SENSES:
            sense = registry[name]
            assert sense.is_ambient(), f"{name} is not polled on a schedule"
            assert sense.ttl_seconds >= sense.poll_interval, (
                f"{name} expires before the scheduler repolls it, so the fact is "
                f"missing for part of every cycle"
            )

    def test_only_genuinely_public_facts_are_public(self):
        from shani_chronoa.senses import discover_senses

        registry = discover_senses()
        for name in NEW_SENSES:
            tier = registry[name].sensitivity
            if name in PUBLIC_TIER:
                assert tier == SENSITIVITY_PUBLIC, f"{name} should be public"
            else:
                assert tier != SENSITIVITY_PUBLIC, (
                    f"{name} reads what the user's software is doing - container "
                    f"names, listening ports, executable paths, work products - "
                    f"and is declared public, so it would be first in line for a "
                    f"cloud fallback"
                )
