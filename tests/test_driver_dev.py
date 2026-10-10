"""The driver-development skills, exercised against what this machine really is.

Two things shaped this file. The first is the platform finding: a kernel module
cannot be built on a stock ShaniOS image (no compiler, no `make`, no
`linux-headers`, no `dkms` on any desktop profile - verified from both image
matrices), so the half of this work that must work is the *honesty* of the
report and the *correctness* of the generated project. The second is that three
defects in the first version were only findable by running the thing:

- `_secure_boot_state` read the whole `/sys/kernel/security/lockdown` line as the
  answer. The real file says `[none] integrity confidentiality`, so the code
  called an unlocked machine "locked down" and claimed unsigned modules would
  be refused when they are not - the dangerous direction to be wrong in.
- `_toolchain` decided "is this absent?" twice by matching the same strings in
  two comprehensions, and the two disagreed: it printed the compiler, `make`
  and the build tree as "also missing" on the machine that had all three.
- The Kbuild template used `obj-m := mymod`, which is the form most tutorials
  show and which **fails**: measured against real headers, `obj-m := mymod.o`
  builds and `obj-m := mymod` does not.

So the build is asserted against a real one where this box can do it, and
every refusal is asserted for the *reason*, not for the presence of a word.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import driver_dev, user_driver  # noqa: E402

_UNAME = subprocess.run(["uname", "-r"], capture_output=True, text=True,
                        timeout=10, check=False).stdout.strip()


@pytest.fixture
def home():
    """The isolated HOME conftest sets up per test.

    Every path these skills take is confined to the home directory, and the
    first version of these tests built their projects under `tmp_path` instead
    - so the confinement (which is correct, and is what stops a skill reaching
    /etc) refused every one of them. Five failures that were the tests' fault
    and the product's right.
    """
    return Path.home()


@pytest.fixture
def project(home):
    """A scaffolded module inside home, for the build tests."""
    directory = home / "drivers" / "mymod"
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in driver_dev._scaffold_text("mymod").items():
        (directory / name).write_text(text)
    return directory


class TestDriverStatusIsHonest:
    def test_it_names_the_running_kernel(self):
        assert _UNAME in driver_dev._run_status({})

    def test_every_piece_is_present_absent_or_explained(self):
        """No line may assert a fact without saying how it was read.

        The failure this pins is the one the first version had: a line that said
        "locked down" with no evidence attached, which is indistinguishable
        from a line that had read something.
        """
        for line in driver_dev._run_status({}).splitlines():
            if line.strip().startswith(("C compiler", "make", "dkms")) or "build tree" in line:
                continue
            if not line.strip():
                continue
            assert "?" in line or "(" in line or "is" in line or "not" in line, line

    def test_a_missing_compiler_names_the_package_not_the_absence(self, monkeypatch):
        monkeypatch.setattr(driver_dev, "_first_present", lambda *n: None)
        out = driver_dev._run_status({})
        assert "base-devel" in out
        assert "cannot be built here" in out

    def test_dkms_alone_does_not_claim_the_build_is_impossible(self, monkeypatch):
        """dkms governs surviving a kernel update, not whether a build works.

        The first version put it in the blocking set, so a machine with a
        compiler and headers was told it could not build a module because the
        one optional piece was missing - contradicting the four lines above it.
        """
        real = driver_dev._first_present

        def only_gcc(*names):
            return "gcc" if "gcc" in names or names == ("gcc", "cc", "clang") else (
                None if "dkms" in names else real(*names))

        monkeypatch.setattr(driver_dev, "_first_present", only_gcc)
        monkeypatch.setattr(driver_dev, "_build_dir", lambda r, root=None: Path("/usr/src"))
        out = driver_dev._run_status({})
        assert "cannot be built here" not in out
        assert "Everything needed to build a module is present" in out
        assert "does not stop a build: dkms" in out


class TestSecureBootReading:
    """The lockdown file is `[active] available...`, not a bare word."""

    def _write(self, tmp_path, text):
        d = tmp_path / "sys" / "kernel" / "security"
        d.mkdir(parents=True)
        (d / "lockdown").write_text(text)
        return tmp_path

    def test_none_means_not_locked_down(self, tmp_path):
        root = self._write(tmp_path, "[none] integrity confidentiality")
        state, how = driver_dev._secure_boot_state(root)
        assert state == "not locked down", how
        assert "'none'" in how

    def test_an_enforced_mode_is_reported_as_locked(self, tmp_path):
        root = self._write(tmp_path, "[integrity] confidentiality")
        state, how = driver_dev._secure_boot_state(root)
        assert state == "locked down"
        assert "integrity" in how

    def test_an_unexpected_shape_is_unknown_rather_than_guessed(self, tmp_path):
        root = self._write(tmp_path, "none")
        state, how = driver_dev._secure_boot_state(root)
        assert state == "unknown"
        assert "not the [active] modes form" in how

    def test_no_efivars_is_unknown_because_uefi_does_not_apply(self, tmp_path):
        state, how = driver_dev._secure_boot_state(tmp_path)
        assert state == "unknown"
        assert "without UEFI" in how


class TestScaffold:
    def test_it_writes_the_five_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        out = driver_dev._run_scaffold({"name": "mymod"})
        assert "Scaffolded" in out
        directory = tmp_path / "drivers" / "mymod"
        assert sorted(p.name for p in directory.iterdir()) == [
            "99-mymod.rules", "Makefile", "README.md", "dkms.conf", "mymod.c"]

    def test_it_never_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        driver_dev._run_scaffold({"name": "mymod"})
        source = tmp_path / "drivers" / "mymod" / "mymod.c"
        source.write_text("/* mine, edited */\n")
        driver_dev._run_scaffold({"name": "mymod"})
        assert source.read_text() == "/* mine, edited */\n", \
            "a second scaffold overwrote a file the user had already edited"

    def test_the_generated_c_carries_what_a_module_needs_to_load(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        driver_dev._run_scaffold({"name": "mymod"})
        text = (tmp_path / "drivers" / "mymod" / "mymod.c").read_text()
        for needed in ("module_init", "module_exit", "MODULE_LICENSE"):
            assert needed in text, f"{needed} missing: a module without it will not load"
        assert driver_dev._c_problem(text, Path("mymod.c")) == ""

    def test_dkms_conf_keeps_dkms_own_shell_variables(self, tmp_path, monkeypatch):
        """`str.format` ate `${kernel_source_dir}` in the first version.

        The generated dkms.conf is read by dkms, not by Python, so the variable
        has to survive verbatim - and a template that raises KeyError produces
        no file at all, which is how the bug showed.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        driver_dev._run_scaffold({"name": "mymod"})
        conf = (tmp_path / "drivers" / "mymod" / "dkms.conf").read_text()
        assert "${kernel_source_dir}" in conf
        assert '${dkms_tree}' in conf
        assert 'BUILT_MODULE_NAME[0]="mymod"' in conf

    def test_the_makefile_uses_the_form_that_actually_builds(self):
        """`obj-m := NAME.o` builds; `obj-m := NAME` does not.

        Measured against real headers on this box: the bare form fails with "No
        rule to make target 'NAME', needed by 'modules.order'", and the compile
        never happens - so a scaffold whose Makefile is wrong fails at a step
        whose message points at the wrong target.
        """
        text = driver_dev._scaffold_text("mymod")["Makefile"]
        active = [ln for ln in text.splitlines() if ln.startswith("obj-m")]
        assert active == ["obj-m := mymod.o"], active

    def test_a_bad_name_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        for bad in ("My Mod", "../escape", "", "9lives", "a" * 40):
            out = driver_dev._run_scaffold({"name": bad})
            assert "not a usable module name" in out, (bad, out)

    def test_it_refuses_a_path_outside_home(self, tmp_path):
        """`tmp_path` is outside the isolated home, which is the point.

        No patching needed: conftest's HOME is elsewhere, so this is the real
        confinement being exercised rather than a fixture arranged around it.
        """
        out = driver_dev._run_scaffold({"name": "mymod", "path": str(tmp_path / "drv")})
        assert "outside your home" in out


class TestBuildGate:
    def test_the_gate_comes_before_anything_else_is_confirmed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(driver_dev, "_consent", lambda: (False, "because I said so"))
        out = driver_dev._run_build({"name": "mymod", "path": str(tmp_path)})
        assert "because I said so" in out
        assert "does not exist" not in out, \
            "the refusal confirmed a directory state, which a consent answer must not"

    def test_a_missing_toolchain_is_named_rather_than_shown_as_compiler_output(self,
                                                                              project,
                                                                              monkeypatch):
        monkeypatch.setattr(driver_dev, "_consent", lambda: (True, ""))
        monkeypatch.setattr(driver_dev, "_first_present", lambda *n: None)
        monkeypatch.setattr(driver_dev, "_build_dir", lambda release, root=None: None)
        out = driver_dev._run_build({"name": "mymod", "path": str(project)})
        assert "base-devel" in out
        assert "linux-headers" in out, "the missing build tree must name the package that provides it"
        assert ".." not in out, f"a doubled sentence break reads as two messages: {out}"
        assert "make exited" not in out, "a refusal reported itself as a build attempt"


@pytest.mark.skipif(not _UNAME or not Path(f"/lib/modules/{_UNAME}/build").exists(),
                    reason="needs kernel build headers for the running kernel")
class TestBuildForReal:
    """A real Kbuild, not a mocked one. This is the whole point of the module."""

    def test_it_produces_a_module_the_kernel_can_read(self, project, monkeypatch):
        monkeypatch.setattr(driver_dev, "_consent", lambda: (True, ""))
        directory = project
        out = driver_dev._run_build({"name": "mymod", "path": str(directory)})
        assert "Built" in out, out
        ko = directory / "mymod.ko"
        assert ko.exists()
        # modinfo is the kernel's own reader: if it can parse the artifact, the
        # module is real and loadable in shape.
        info = subprocess.run(["modinfo", str(ko)], capture_output=True, text=True,
                              timeout=30, check=False)
        assert "mymod" in info.stdout, info.stdout + info.stderr
        assert driver_dev.POST_CONDITION({"name": "mymod", "path": str(directory)}, tool="driver_build")[0] is True

    def test_a_leftover_module_does_not_verify_a_failed_build(self, project, monkeypatch):
        """Freshness, because a refused build leaves the old .ko sitting there.

        This is the `pdf_pages` defect: matching mere existence verifies work
        that never happened.
        """
        directory = project
        (directory / "mymod.ko").write_bytes(b"")
        os.utime(directory / "mymod.c", (9e9, 9e9))
        ok, detail = driver_dev.POST_CONDITION({"name": "mymod", "path": str(directory)}, tool="driver_build")
        assert ok is False
        assert "older than" in detail

    def test_no_module_at_all_is_a_failure_not_an_unknown(self, project):
        directory = project
        ok, detail = driver_dev.POST_CONDITION({"name": "mymod", "path": str(directory)}, tool="driver_build")
        assert ok is False
        assert "no .ko" in detail


class TestPostConditionKnowsWhichToolRan:
    """One module, three skills, and one check that was written for the wrong two.

    `verification.post_condition_for` reads a single module-level
    `POST_CONDITION`, so `driver_status` and `driver_scaffold` were being checked
    for a `.ko` - and running `driver_scaffold` for real on this machine printed
    **"reported success but verification failed: no .ko file is in ..."** about a
    call whose own last line is *"This is source, not a built module."* A
    post-condition describing another tool's output calls a correct action a
    failure, which is the confident-wrong-answer shape the repo keeps recording.

    The framework already passes the tool name when a check takes two arguments
    (`verification._call`), precisely for the module holding several skills, so
    the dispatch belongs there rather than in `Skill`.
    """

    def _scaffold(self, directory, monkeypatch):
        monkeypatch.setattr(driver_dev.files, "resolve_in_home",
                            lambda raw: directory, raising=False)
        out = driver_dev._run_scaffold({"name": "mymod", "path": "unused"})
        assert "Scaffolded" in out, out
        return out

    def test_a_status_query_is_unverified_not_a_failed_write(self):
        assert driver_dev.POST_CONDITION({}, tool="driver_status") is None

    def test_scaffold_is_verified_by_its_source_not_by_a_ko(self, project, monkeypatch):
        self._scaffold(project, monkeypatch)
        ok, detail = driver_dev.POST_CONDITION({"name": "mymod", "path": "unused"},
                                               tool="driver_scaffold")
        assert ok is True, detail
        assert "obj-m := mymod.o" in detail

    def test_a_scaffold_that_wrote_nothing_fails_its_own_check(self, home):
        # An EMPTY directory, not the `project` fixture: that one is scaffolded by
        # definition, so pointing this at it asserted the opposite of what it says.
        directory = home / "drivers" / "never-scaffolded"
        directory.mkdir(parents=True)
        ok, detail = driver_dev.POST_CONDITION({"name": "mymod", "path": str(directory)},
                                               tool="driver_scaffold")
        assert ok is False
        assert "did not leave" in detail

    def test_the_scaffold_check_catches_a_kbuild_line_that_does_not_build(self, project,
                                                                         monkeypatch):
        """The line is the claim, so it is what is read back off the disk."""
        self._scaffold(project, monkeypatch)
        (project / "Makefile").write_text("obj-m := mymod\n")
        ok, detail = driver_dev.POST_CONDITION({"name": "mymod", "path": "unused"},
                                               tool="driver_scaffold")
        assert ok is False, detail
        assert "obj-m := mymod.o" in detail

    def test_scaffold_needs_no_ko_and_build_still_does(self, project, monkeypatch):
        """The two checks do not bleed into each other."""
        self._scaffold(project, monkeypatch)
        assert driver_dev.POST_CONDITION(
            {"name": "mymod", "path": "unused"}, tool="driver_scaffold")[0] is True
        ok, detail = driver_dev.POST_CONDITION(
            {"name": "mymod", "path": "unused"}, tool="driver_build")
        assert ok is False, detail
        assert "no .ko" in detail

    def test_the_framework_passes_the_tool_name_it_proves(self, project, monkeypatch):
        """Not just the signature - that `verify()` really supplies it.

        Otherwise the dispatch above is dead code nobody calls.
        """
        from shani_chronoa import verification
        self._scaffold(project, monkeypatch)
        result = verification.verify("shani_chronoa.skills.driver_dev",
                                     {"name": "mymod", "path": "unused"},
                                     tool="driver_scaffold")
        assert result.verdict.value == "verified", result.evidence


class TestI2cGuard:
    """The denylist's first version could not fire on any input at all.

    It was a dict keyed `(address, register)` holding what are plainly register
    ranges, so every lookup missed. A guard that never fires is a refusal that
    exists only in the source, which is why these call the helper directly
    rather than only going through the skill.
    """

    def test_the_block_select_registers_are_refused(self):
        for reg in (0x00, 0x01, 0x0F, 0x10, 0x11):
            assert user_driver._forbidden_register(reg, "0x01"), \
                f"register 0x{reg:02x} is a command register and was allowed"

    def test_an_ordinary_data_register_is_allowed(self):
        assert user_driver._forbidden_register(0x0C, "0x01") == ""

    def test_an_erase_word_is_refused_at_any_register(self):
        assert "erase" in user_driver._forbidden_register(0x20, "chip erase")

    def test_a_plain_value_that_merely_looks_close_is_allowed(self):
        """'blank' is in the word list; a legitimate value must survive it."""
        assert user_driver._forbidden_register(0x20, "0xFF") == ""


class TestI2cSkill:
    def test_a_write_is_refused_before_the_tool_is_even_looked_for(self, monkeypatch):
        monkeypatch.setattr(user_driver, "_consent", lambda: (False, "no consent here"))
        monkeypatch.setattr(user_driver.shutil, "which", lambda name: None)
        out = user_driver._run_i2c({"action": "write", "bus": 1, "address": "0x48",
                                    "register": "0x0c", "value": "0x01"})
        assert "no consent here" in out
        assert "not installed" not in out, \
            "the destructive refusal only fired because the binary was missing"

    def test_the_eeprom_refusal_fires_even_with_consent_granted(self, monkeypatch):
        monkeypatch.setattr(user_driver, "_consent", lambda: (True, ""))
        out = user_driver._run_i2c({"action": "write", "bus": 1, "address": "0x50",
                                    "register": "0x00", "value": "0x01"})
        assert "Refusing to write register 0x00" in out
        assert "Nothing was written" in out
        assert "datasheet" in out, "the rule must present itself as a rule"

    def test_a_reserved_address_is_refused_before_anything_runs(self):
        for address in ("0x00", "0x02", "0x78", "0xFF"):
            out = user_driver._run_i2c({"action": "read", "bus": 1, "address": address,
                                        "register": "0x00"})
            assert "not a 7-bit I2C address" in out, (address, out)

    def test_an_absent_bus_says_so_rather_than_reporting_a_device_fault(self):
        out = user_driver._run_i2c({"action": "read", "bus": 200, "address": "0x48",
                                    "register": "0x00"})
        assert "/dev/i2c-200 does not exist" in out

    def test_a_bus_with_no_device_node_is_not_called_a_missing_bus(self, monkeypatch):
        """Measured on a real slot: i2cdetect lists i2c-0/1 with no /dev nodes.

        Those are two different facts with two different fixes - a udev rule
        versus a different bus - and the first version of this said "there is no
        such bus", which was wrong on the machine it was measured on.
        """
        monkeypatch.setattr(user_driver, "_i2c_buses",
                            lambda: (["0", "1"], "from i2cdetect -l (2 bus)"))
        monkeypatch.setattr(user_driver.Path, "exists", lambda self: False)
        out = user_driver._run_i2c({"action": "read", "bus": 1, "address": "0x48",
                                    "register": "0x00"})
        assert "Bus 1 exists" in out
        assert "no device node" in out
        assert "there is no such bus" not in out

    def test_a_non_numeric_bus_asks_the_question(self):
        assert "Which I2C bus?" in user_driver._run_i2c({"bus": "one", "address": "0x48",
                                                        "register": "0x00"})

    def test_hex_addresses_are_accepted(self, monkeypatch):
        """A model that writes 0x48 must not be told it is malformed."""
        monkeypatch.setattr(user_driver.Path, "exists", lambda self: True)
        seen = {}

        class _Proc:
            returncode = 0
            stdout = "0x1f"
            stderr = ""

        monkeypatch.setattr(user_driver.subprocess, "run",
                            lambda cmd, **kw: (seen.update(cmd=cmd), _Proc())[1])
        out = user_driver._run_i2c({"action": "read", "bus": 1, "address": "0x48",
                                    "register": "0x0c"})
        assert seen["cmd"] == ["i2cget", "-y", "1", "0x48", "0x0c"]
        assert "0x1f" in out

    def test_a_failed_tool_call_reports_no_claim_about_the_device(self, monkeypatch):
        monkeypatch.setattr(user_driver.Path, "exists", lambda self: True)
        monkeypatch.setattr(user_driver.shutil, "which", lambda name: "/usr/bin/i2cget")

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "Run as root?"

        monkeypatch.setattr(user_driver.subprocess, "run", lambda cmd, **kw: _Proc())
        out = user_driver._run_i2c({"action": "read", "bus": 1, "address": "0x48",
                                    "register": "0x0c"})
        assert "Nothing is known about the device's state" in out
        assert "Run as root?" in out


class TestDeviceProbe:
    def test_it_reports_something_for_every_route(self):
        out = user_driver._run_probe({})
        for route in ("I2C:", "Video:", "FUSE mounts:"):
            assert route in out

    def test_an_absent_helper_names_the_package(self, monkeypatch):
        monkeypatch.setattr(user_driver.shutil, "which", lambda name: None)
        monkeypatch.setattr(user_driver.Path, "exists", lambda self: False)
        out = user_driver._run_probe({})
        assert "v4l-utils" in out
        assert "fuse3" in out

    def test_buses_are_ordered_numerically_not_as_strings(self, tmp_path, monkeypatch):
        """`sorted()` on strings puts 10 before 2, which reads as a wrong number."""
        (tmp_path / "i2c-2").write_text("")
        (tmp_path / "i2c-10").write_text("")
        (tmp_path / "i2c-1").write_text("")
        monkeypatch.setattr(user_driver, "_dev_nodes",
                            lambda pattern: ["i2c-1", "i2c-2", "i2c-10"])
        buses, _note = user_driver._i2c_buses()
        assert buses == ["1", "2", "10"], buses