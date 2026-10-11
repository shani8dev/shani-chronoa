"""The `security` sense, and the 5-byte efivarfs file.

The `SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c` file on this machine is
**5 bytes**, and they are not the value:

    06 00 00 00 00
    ^^^^^^^^^^^^ the boolean is the LAST byte
    ^^^^ efivarfs' 4-byte size/attributes header

`read()[0]` gives `0x06` — the high byte of the *attributes* field, which is
`0x06` on essentially every system — and would report Secure Boot **enabled on
every machine ever built**. That is the specific bug this test file exists to
prevent, and it is fed the real 5 bytes rather than a plausible-looking
fixture.

Second trap, the opposite direction: no `efivarfs` at all means the machine
booted without UEFI, or is a container, or is under a hypervisor that does not
pass variables through. All three are "undetermined", never "disabled" — a
user told their firmware security is off when the machine simply had no EFI is
misled just as badly.
"""

import pytest

from shani_chronoa.senses import security

# The real bytes from this machine, and a disabled one.
THIS_MACHINE = bytes([0x06, 0x00, 0x00, 0x00, 0x00])
SECURE_BOOT_ON = bytes([0x06, 0x00, 0x00, 0x00, 0x01])
# What a parser that reads byte 0 sees, on a machine with Secure Boot OFF.
ATTRS_ONLY = bytes([0x06, 0x00, 0x00, 0x00, 0x00])


@pytest.fixture
def efivars(tmp_path, monkeypatch):
    root = tmp_path / "efivars"
    root.mkdir()
    monkeypatch.setattr(security, "_EFIVARS", root)
    return root


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    root = tmp_path / "security"
    root.mkdir()
    monkeypatch.setattr(security, "_SECURITY", root)
    return root


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(security.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


def _var(efivars, name, content):
    (efivars / name).write_bytes(content)


class TestTheEfivarfsOffset:
    def test_the_value_is_the_last_byte_not_the_first(self, efivars):
        _var(efivars, security._SECURE_BOOT, SECURE_BOOT_ON)
        assert security._efi_bool(security._SECURE_BOOT) is True

    def test_the_real_bytes_from_this_machine_read_as_disabled(self, efivars):
        _var(efivars, security._SECURE_BOOT, THIS_MACHINE)
        assert security._efi_bool(security._SECURE_BOOT) is False, (
            "byte 0 of this file is 0x06 for the ATTRIBUTES, so reading it as "
            "the value would report Secure Boot enabled on every machine"
        )
        assert THIS_MACHINE[0] != THIS_MACHINE[-1], (
            "the control case: if first and last byte were equal this test "
            "could not tell a right parser from a wrong one"
        )

    def test_a_truncated_variable_is_undetermined_not_false(self, efivars):
        """Fewer than 5 bytes means the header or the value is missing, and
        guessing False reports a broken read as a security state."""
        _var(efivars, security._SECURE_BOOT, bytes([0x06, 0x00]))
        assert security._efi_bool(security._SECURE_BOOT) is None

    def test_a_missing_variable_is_undetermined(self, efivars):
        assert security._efi_bool(security._SECURE_BOOT) is None

    def test_a_mode_byte_uses_the_same_offset(self, efivars):
        _var(efivars, security._SETUP_MODE, bytes([0x06, 0, 0, 0, 0x01]))
        assert security._efi_mode(security._SETUP_MODE) == 1


class TestNoEfivarsIsNotDisabled:
    def test_a_machine_with_no_efivars_is_undetermined(self, kernel, tmp_path, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", tmp_path / "no-efivars")
        monkeypatch.setattr(security, "_SECURITY", kernel)
        (kernel / "lsm").write_text("apparmor,lockdown\n")
        content = security._run({}).content
        line = [l for l in content.splitlines() if l.startswith("Secure Boot:")][0]
        assert "undetermined" in line, f"reported as: {line!r}"
        assert "disabled" not in line, (
            f"a machine with no EFI was reported as disabled: {line!r}"
        )
        assert "booted without UEFI" in content

    def test_an_existing_variable_that_does_not_read_is_undetermined(self, efivars, kernel, granted):
        _var(efivars, security._SECURE_BOOT, bytes([0x00]))
        (kernel / "lsm").write_text("lockdown\n")
        content = security._run({}).content
        assert "Secure Boot: undetermined" in content


class TestSetupMode:
    def test_setup_mode_1_means_the_keys_are_not_installed(self, efivars, kernel, granted):
        _var(efivars, security._SECURE_BOOT, SECURE_BOOT_ON)
        _var(efivars, security._SETUP_MODE, bytes([0x06, 0, 0, 0, 0x01]))
        (kernel / "lsm").write_text("lockdown\n")
        content = security._run({}).content
        assert "Secure Boot: enabled" in content
        assert "platform keys are not installed" in content, (
            "Secure Boot reads as enabled while the firmware has no keys - the "
            "variable is set but nothing is being validated"
        )

    def test_setup_mode_0_says_nothing_extra(self, efivars, kernel, granted):
        _var(efivars, security._SECURE_BOOT, SECURE_BOOT_ON)
        _var(efivars, security._SETUP_MODE, bytes([0x06, 0, 0, 0, 0x00]))
        (kernel / "lsm").write_text("lockdown\n")
        assert "platform keys are not installed" not in security._run({}).content


class TestLsmsAreNotAScore:
    def test_configured_and_active_are_both_reported(self, kernel, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown,capability,landlock,yama,apparmor,ima,evm\n")
        (kernel / "lsm_active").write_text("lockdown,apparmor\n")
        content = security._run({}).content
        assert "LSMs configured:" in content and "ima" in content
        assert "LSMs active: lockdown, apparmor" in content
        assert security._run({}).metadata["lsms_active"] == 2

    def test_configured_but_none_active_is_called_out(self, kernel, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown,apparmor\n")
        (kernel / "lsm_active").write_text("")
        content = security._run({}).content
        assert "no LSM is active" in content
        assert "without a policy attached" in content, (
            "a configured LSM with no policy does nothing, and saying so is the "
            "difference between a list and an assessment"
        )

    def test_the_output_disclaims_being_an_assessment(self, kernel, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown\n")
        (kernel / "lsm_active").write_text("")
        assert "no setting here is a security assessment" in security._run({}).content

    def test_no_lsm_file_is_not_zero_lsms(self, kernel, monkeypatch, granted):
        """A missing `lsm` file is "could not read", which is different from a
        present file listing nothing."""
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        monkeypatch.setattr(security, "read_tpm", lambda: None)
        (kernel / "lsm_active").write_text("apparmor\n")
        percept = security._run({})
        assert percept.metadata["lsms_configured"] == 0
        assert "LSMs configured" not in percept.content

    def test_an_empty_lsm_active_is_nothing_enforcing_not_nothing_readable(self, kernel, monkeypatch, granted):
        """The bug this found: an empty active list and a missing file were
        indistinguishable, and the empty case is the one that matters."""
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("apparmor,lockdown\n")
        (kernel / "lsm_active").write_text("")
        assert security.read_lsms()["active"] == []
        content = security._run({}).content
        assert "no LSM is active" in content
        assert "LSMs active:" not in content


class TestAnAbsentLsmActiveIsNotAnEmptyOne:
    """**Measured on this machine: `/sys/kernel/security/lsm_active` does not
    exist.** The directory holds `apparmor evm ima integrity lockdown lsm tpm0`
    and nothing else, so the reader records no `active` key at all.

    The renderer tested `not lsms.get("active")`, which is also true when the
    key is *absent* - so the sense printed "no LSM is active: a configured LSM
    is not an enforcing one without a policy attached" on a machine with
    Landlock, AppArmor, IMA and EVM all loaded. That is the exact collapse
    `read_lsms`'s own docstring claims to have fixed, reintroduced one function
    later, and it is the failure this whole repo records: an unreadable answer
    rendered as a confident empty one.
    """

    def _machine(self, kernel, monkeypatch):
        """This box's real securityfs: seven configured, no `lsm_active`, four
        of them with a runtime directory."""
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        monkeypatch.setattr(security, "read_tpm", lambda: None)
        monkeypatch.setattr(security, "read_apparmor",
                            lambda: {"enabled": "Y", "installed": 167,
                                     "loaded": None})
        (kernel / "lsm").write_text(
            "lockdown,capability,landlock,yama,apparmor,ima,evm\n")
        for name in ("lockdown", "apparmor", "ima", "evm"):
            (kernel / name).mkdir()
        return kernel

    def test_the_reader_records_no_active_key_at_all(self, kernel, monkeypatch):
        self._machine(kernel, monkeypatch)
        record = security.read_lsms()
        assert "active" not in record
        assert len(record["configured"]) == 7

    def test_an_absent_interface_is_not_reported_as_nothing_active(
            self, kernel, monkeypatch, granted):
        self._machine(kernel, monkeypatch)
        content = security._run({}).content
        assert "no LSM is active" not in content, (
            "no LSM is active was reported on a machine with four of them "
            "loaded, because an absent file was read as an empty one")
        assert "could not be read" in content
        assert "no lsm_active interface" in content

    def test_the_metadata_count_is_none_not_zero(self, kernel, monkeypatch, granted):
        """`len(lsms.get("active", []))` answered 0 for "I could not ask".

        A consumer reading `0` cannot tell it apart from "nothing is
        enforcing", which is the whole distinction this record exists for.
        """
        self._machine(kernel, monkeypatch)
        metadata = security._run({}).metadata
        assert metadata["lsms_active"] is None
        assert metadata["lsms_configured"] == 7
        assert metadata["lsms_runtime"] == 4

    def test_a_present_and_empty_interface_still_says_nothing_is_active(
            self, kernel, monkeypatch, granted):
        """The fix must not have disarmed the other branch.

        Present-and-empty is the state worth warning about, so it has to keep
        its sentence once the absent case took the other branch.
        """
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown,apparmor\n")
        (kernel / "lsm_active").write_text("")
        content = security._run({}).content
        assert "no LSM is active" in content
        assert "could not be read" not in content
        assert security._run({}).metadata["lsms_active"] == 0

    def test_the_runtime_list_names_only_what_exists(self, kernel, monkeypatch):
        self._machine(kernel, monkeypatch)
        assert security.read_lsms()["runtime"] == ["apparmor", "evm", "ima",
                                                   "lockdown"]

    def test_the_runtime_list_is_a_floor_and_says_so(self, kernel, monkeypatch,
                                                     granted):
        """Three of the seven have no directory, and that is not evidence.

        securityfs creates an entry only for LSMs that keep runtime state, so
        `capability`, `landlock` and `yama` are absent here whether or not they
        are enforcing. Printing "4 of 7" without that would be a confident count
        about a question the data cannot answer.
        """
        self._machine(kernel, monkeypatch)
        content = security._run({}).content
        assert "4 of them have a runtime interface" in content
        assert "a floor, not a count" in content
        assert "whether or not it is enforcing" in content

    def test_no_runtime_entries_says_nothing_rather_than_counting_zero(
            self, kernel, monkeypatch, granted):
        """With nothing instantiated there is no claim to make, and printing
        "0 of them have a runtime interface" would read as "none is enforcing"
        - the same absence-shaped claim in a different sentence."""
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        monkeypatch.setattr(security, "read_tpm", lambda: None)
        monkeypatch.setattr(security, "read_apparmor",
                            lambda: {"enabled": "N", "loaded": None})
        (kernel / "lsm").write_text("capability,landlock,yama\n")
        content = security._run({}).content
        assert "could not be read" in content
        assert "runtime interface in" not in content

    def test_the_apparmor_half_does_not_depend_on_lsm_active(
            self, kernel, monkeypatch, granted):
        """AppArmor appearing in `configured` is enough on its own.

        The AppArmor branch keys on the two lists joined, so it survives the
        missing `active` half - which is the whole point, because that is the
        state this kernel is in.
        """
        self._machine(kernel, monkeypatch)
        content = security._run({}).content
        assert "AppArmor: module enabled" in content
        assert "167 AppArmor policy file(s) installed" in content
        assert "undetermined" in content


class TestTpm:
    def test_a_present_tpm_is_reported_with_its_version(self, kernel, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown\n")
        tpm = kernel / "tpm0"
        tpm.mkdir()
        (tpm / "tpm_version_major").write_text("2\n")
        content = security._run({}).content
        assert "TPM: present, version 2" in content
        assert security._run({}).metadata["tpm"] is True

    def test_no_tpm_device_is_stated_not_guessed(self, kernel, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown\n")
        assert "TPM: none exposed" in security._run({}).content
        assert security._run({}).metadata["tpm"] is False


class TestSeccomp:
    def test_the_mode_is_reported_verbatim(self, kernel, monkeypatch, granted, tmp_path):
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown\n")
        monkeypatch.setattr(security, "read_seccomp", lambda: "2")
        assert "seccomp mode for this process: 2" in security._run({}).content

    def test_a_missing_status_is_simply_omitted(self, kernel, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", kernel.parent / "no-efi")
        (kernel / "lsm").write_text("lockdown\n")
        monkeypatch.setattr(security, "read_seccomp", lambda: None)
        assert "seccomp" not in security._run({}).content


class TestDegradeRatherThanRefuse:
    def test_a_container_reads_nothing_and_says_so(self, kernel, tmp_path, monkeypatch, granted):
        monkeypatch.setattr(security, "_EFIVARS", tmp_path / "no-efi")
        monkeypatch.setattr(security, "_SECURITY", tmp_path / "no-security")
        monkeypatch.setattr(security, "read_seccomp", lambda: None)
        percept = security._run({})
        assert percept.metadata["determined"] is False
        assert "container or a virtual machine" in percept.content
        assert "not the same as a machine whose security is off" in percept.content

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(security.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(security._run({}), str)
