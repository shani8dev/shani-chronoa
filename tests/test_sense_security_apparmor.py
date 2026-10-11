"""The AppArmor half of the `security` sense, and what it must not claim.

The module's own docstring already named this gap before the code closed it:
*"an LSM that appears in the list is **configured**, not necessarily
**enforcing**: an LSM can be listed and its LSM in `active` but with no policy
attached, in which case it does nothing."* AppArmor appears in both lists here,
which says the module is loaded - not that a single profile is attached.

**Measured on this box, and the measurement changed the design:**

    $ aa-status
    rc=4
    stdout: apparmor module is loaded.
    stderr: You do not have enough privilege to read the profile set.

    $ aa-status --json
    rc=4, stdout: []

That `[]` is the whole reason nothing here shells out. **An unreadable answer
and an empty one are the same bytes**, so the tool's own JSON reports "this
machine has no AppArmor profiles" for a machine that has 167 installed and an
unknown number loaded. Reading `/sys` directly gives the two states that *are*
distinguishable without privilege, and reports the loaded set as undetermined.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.senses import security  # noqa: E402


class TestNoBinaryIsCalled:
    """The gate that matters most, because it is the one that can fail quietly."""

    def test_nothing_in_the_module_shells_out_to_aa_status(self):
        """`aa-status` exits 4 and prints `[]` unprivileged.

        Calling it and reading the empty JSON would state "no profiles" about a
        machine that has 167 installed. So it is not called at all - asserted
        against the module's **source**, because a test that watches the
        function miss a call made from somewhere else.
        """
        import ast
        import inspect
        # **The AST with every docstring removed, not the text.** A substring
        # search finds the module's own docstring, which explains the
        # measurement and quotes the very command name being forbidden - so the
        # first version of this failed on correct code. Collecting string
        # *constants* is not enough either, because a docstring is itself a
        # string constant; they have to be taken out of the tree first.
        tree = ast.parse(inspect.getsource(security))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    docstrings.add(id(body[0].value))
        strings = {node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant)
                   and isinstance(node.value, str)
                   and id(node) not in docstrings}
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Attribute):
                    called.add(function.attr)
                elif isinstance(function, ast.Name):
                    called.add(function.id)
        for forbidden in ("aa-status", "apparmor_status"):
            assert not any(forbidden in value for value in strings), (
                f"{forbidden} appears as a command string - the unprivileged "
                "answer is the refusal, not an empty list")
        # And nothing runs a subprocess to get it.
        assert "subprocess" not in called
        assert "Popen" not in called


class TestTheLoadedSetIsNotGuessed:
    def test_loaded_is_none_and_never_a_count(self, tmp_path, monkeypatch):
        """`loaded` is the question that matters and the one nobody can read.

        It is `None` - not `0`, not `len(installed)`. Each of those is a
        plausible-looking wrong answer.
        """
        # A real directory, because the count is produced by iterating real
        # entries and testing `is_file()` on them: a stubbed `iterdir` that
        # hands back paths which do not exist would read as an empty policy
        # directory and the count would be 0 for a reason of the fixture's
        # making.
        policy = tmp_path / "apparmor.d"
        policy.mkdir()
        for name in ("usr.bin.foo", "usr.bin.bar", "abi"):
            (policy / name).write_text("profile")
        monkeypatch.setattr(security, "_POLICY_DIR", policy)
        record = security.read_apparmor()
        assert record["loaded"] is None
        assert record["installed"] == 2      # `abi` is not a profile

    def test_the_installed_count_excludes_the_abi_directory(self, tmp_path):
        real = tmp_path / "apparmor.d"
        real.mkdir()
        (real / "abi").write_text("x")
        (real / "usr.bin.something").write_text("x")
        found = sorted(entry.name for entry in real.iterdir()
                       if entry.is_file() and not entry.name.startswith("abi"))
        assert found == ["usr.bin.something"]


class TestTheModuleParameter:
    def test_a_disabled_module_is_reported_as_disabled(self, tmp_path,
                                                       monkeypatch):
        """`N` is the dangerous direction and must never read as "enabled"."""
        enabled = tmp_path / "enabled"
        enabled.write_text("N\n")
        monkeypatch.setattr(security.sysfs, "read_text",
                            lambda path: enabled.read_text())
        monkeypatch.setattr(security, "_POLICY_DIR", tmp_path / "gone")
        record = security.read_apparmor()
        assert record["enabled"] == "N"
        assert "installed" not in record

    def test_an_unreadable_parameter_is_absent_not_false(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(security.sysfs, "read_text", lambda path: None)
        monkeypatch.setattr(security, "_POLICY_DIR", tmp_path / "gone")
        record = security.read_apparmor()
        # Not "disabled": an absent reading is not a reading of off.
        assert "enabled" not in record

    def test_an_unexpected_value_is_kept_verbatim(self, tmp_path, monkeypatch):
        """The kernel's vocabulary is not a closed list, so it is not forced
        into one. A value we do not recognise is reported, not reinterpreted."""
        enabled = tmp_path / "enabled"
        enabled.write_text("X\n")
        monkeypatch.setattr(security.sysfs, "read_text",
                            lambda path: enabled.read_text())
        monkeypatch.setattr(security, "_POLICY_DIR", tmp_path / "gone")
        assert security.read_apparmor()["enabled"] == "X"


class TestTheExistingSenseStillHolds:
    def test_the_lsm_lists_are_unaffected(self):
        """The AppArmor work added `read_apparmor`, not LSM keys.

        `runtime` is the one addition since, and it is a labelled partial
        reading rather than a second opinion about `active` - the key set is
        asserted here so a future key has to be added deliberately.
        """
        record = security.read_lsms()
        assert isinstance(record, dict)
        for key in record:
            assert key in ("configured", "active", "runtime")


class TestWhatAPersonIsActuallyTold:
    """The rendered lines, not the record.

    `read_apparmor` returning `{"enabled": "N"}` is correct and still says
    nothing to anybody - the sentence a person reads is composed elsewhere, and
    the mutation that reports a disabled module as enabled lives there. Testing
    the reader alone left that mutation green, which is the same
    "a helper can be correct and still be unreachable" defect a third time in
    two sessions.
    """

    def _render(self, monkeypatch, enabled, installed=167,
            lsms=None):
        """Drive the real `_run` with a fixed AppArmor reading.

        Every other reader is stubbed so the line under test is the only thing
        the output depends on - the boot record, the TPM and the LSM list each
        contribute sentences of their own and would otherwise drown it.
        """
        monkeypatch.setattr(security, "read_apparmor",
                            lambda: {"enabled": enabled,
                                     "installed": installed,
                                     "loaded": None})
        monkeypatch.setattr(security, "read_lsms",
                            lambda: lsms if lsms is not None
                            else {"configured": ["lockdown", "apparmor"],
                                  "active": ["lockdown", "apparmor"]})
        monkeypatch.setattr(security, "read_tpm", lambda: None)
        monkeypatch.setattr(security, "read_secure_boot", lambda: {
            "efivars_present": False, "secure_boot": "undetermined"})
        monkeypatch.setattr(security, "read_sandbox_seccomp", lambda: {})
        monkeypatch.setattr(security, "read_seccomp", lambda: None)
        return str(security._run({}))

    def test_an_enabled_module_says_enabled(self, monkeypatch):
        text = self._render(monkeypatch, "Y")
        assert "AppArmor: module enabled" in text

    def test_a_disabled_module_says_disabled_and_says_what_it_means(
            self, monkeypatch):
        """`N` is the direction that must never read as on.

        A module that is present, listed in the LSM stack, and enforcing
        nothing is the case a reader would otherwise call "secured" - the whole
        reason the module docstring warns that *configured* is not *enforcing*.
        """
        text = self._render(monkeypatch, "N")
        assert "DISABLED" in text
        assert "enforcing nothing" in text
        assert "AppArmor: module enabled" not in text

    def test_the_installed_count_is_shown(self, monkeypatch):
        assert "167 AppArmor policy file(s) installed" in self._render(
            monkeypatch, "Y")

    def test_the_loaded_set_is_always_named_as_undetermined(self, monkeypatch):
        """Whatever the module is doing, this sentence must be there.

        "Installed" is not "loaded", and a reader must not be able to conclude
        from the count that anything is attached. This is asserted for **both**
        states because the temptation to drop it is exactly when there is a
        number above it to lean on.
        """
        for enabled in ("Y", "N"):
            text = self._render(monkeypatch, enabled)
            assert "undetermined" in text, enabled
            assert "not evidence" in text, enabled

    def test_nothing_is_shown_when_apparmor_is_not_in_the_stack(self,
                                                                monkeypatch):
        """An LSM that is not there must not produce an AppArmor section.

        A section nobody asked for, printed anyway, is the shape of thing that
        makes a reader stop trusting the rest of the report.
        """
        text = self._render(monkeypatch, "Y",
                            lsms={"configured": ["landlock"],
                                  "active": ["landlock"]})
        assert "AppArmor" not in text