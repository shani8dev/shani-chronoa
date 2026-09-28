"""Tests for `printing` — what this machine can print to and scan from.

Two stacks, two very different availability stories on a real install, and the
whole point of the sense is that the difference is visible rather than implied:

- CUPS arrives with `shani-printer`, which is in the gnome/cosmic/plasma
  Packages-Desktop manifests but *not* in server, kiosk or gamescope. So the
  printer half works on some profiles and must degrade honestly on the rest.
- `sane-utils` was declared in no manifest at all, so the scanner half has had
  no binary anywhere. "No scanner found" and "no scanner software" are entirely
  different statements and the sense must not conflate them.

The distinction this sense exists to get right: `lpstat -e` returning nothing
means no printers are **configured**, not that no printers **exist**. A machine
with a printer plugged in and never added in CUPS is a machine with a printer.
Reporting that as "no printers" is the same class of error as a camera reported
as disabled when it is not.
"""

import subprocess

import pytest

from shani_chronoa import capabilities
from shani_chronoa.senses import discover_senses, printing
from shani_chronoa.config import _SENSE_DEFAULT_ENABLED

# Real output shapes, captured from this machine and from CUPS/SANE conventions
LPSTAT_RUNNING = "scheduler is running\n"
LPSTAT_NOT_RUNNING = "scheduler is not running\n"
LPSTAT_NO_DEFAULT = "no system default destination\n"
SCANIMAGE_NONE = (
    "\nNo scanners were identified. If you were expecting something different,\n"
    "check that the scanner is plugged in, turned on and detected by the\n"
    "sane-find-scanner tool (if appropriate).\n"
)
_TIMEOUT_FOR_TEST = 20
SCANIMAGE_FOUND = (
    "device `epson2:libusb:04e8:0131:50' is a EPSON DS-530 flatbed scanner\n"
    "device `net:host:epson2:libusb:04e8:0131' is a EPSON DS-530 on host\n"
)


def _wire(monkeypatch, *, which=("lpstat", "scanimage"), printer="", rc="",
          default="no system default destination\n", scan=SCANIMAGE_NONE,
          scan_rc=0, raises=None):
    """Install fake lpstat/scanimage, and report which ones were invoked."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[0])
        if raises is not None:
            raise raises
        if cmd[0] == "lpstat":
            if "-r" in cmd:
                return subprocess.CompletedProcess(cmd, 0, LPSTAT_RUNNING, "")
            if "-d" in cmd:
                return subprocess.CompletedProcess(cmd, 0, default, "")
            return subprocess.CompletedProcess(cmd, 0, printer, rc)
        return subprocess.CompletedProcess(cmd, scan_rc, scan, "")

    monkeypatch.setattr(
        printing.shutil, "which",
        lambda n: f"/usr/bin/{n}" if n in which else None)
    monkeypatch.setattr(printing.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def permitted(monkeypatch):
    from shani_chronoa.config import ChronoaConfig
    config = ChronoaConfig()
    config.set("printing-sense-enabled", "true")
    config.set("privacy-mode", "false")
    return config


class TestPrinters:
    def test_a_configured_printer_is_named(self, monkeypatch, permitted):
        _wire(monkeypatch, printer="HP_LaserJet\nOffice_Printer\n")
        out = printing._run({})
        assert "HP_LaserJet" in out
        assert "Office_Printer" in out

    def test_no_configured_printers_is_not_no_printers(self, monkeypatch, permitted):
        """`lpstat -e` empty means nothing is set up in CUPS. A printer can be
        plugged in and never added, and saying 'no printers' would be wrong."""
        _wire(monkeypatch, printer="")
        out = printing._run({})
        assert "none are configured in cups" in out.lower()
        assert "does not mean none exist" in out.lower()

    def test_a_missing_cups_is_a_missing_stack_not_an_empty_one(self, monkeypatch, permitted):
        _wire(monkeypatch, which=())
        out = printing._run({})
        assert "not installed" in out
        assert "not the same as" in out

    def test_a_stopped_scheduler_cannot_answer_the_question(self, monkeypatch, permitted):
        """A CUPS daemon that is down is not a machine with no printers."""
        def fake(cmd, **kwargs):
            if cmd[0] == "lpstat" and "-r" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, LPSTAT_NOT_RUNNING, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(printing.shutil, "which", lambda n: f"/usr/bin/{n}")
        monkeypatch.setattr(printing.subprocess, "run", fake)
        out = printing._run({})
        assert "not running" in out
        assert "unknown" in out, "a stopped scheduler must read as unknown, not as a fact"
        assert "not an absence of printers" in out

    def test_the_default_destination_is_reported_when_there_is_one(self, monkeypatch, permitted):
        _wire(monkeypatch, printer="HP_LaserJet\n",
              default="system default destination: HP_LaserJet\n")
        assert "HP_LaserJet" in printing._run({})


class TestScanners:
    def test_a_detected_scanner_is_named_with_its_description(self, monkeypatch, permitted):
        _wire(monkeypatch, printer="", scan=SCANIMAGE_FOUND)
        out = printing._run({})
        assert "EPSON DS-530" in out
        assert "2 detected" in out

    def test_the_no_scanner_message_is_sanes_own_and_is_not_an_error(self, monkeypatch, permitted):
        _wire(monkeypatch, printer="", scan=SCANIMAGE_NONE)
        out = printing._run({})
        assert "none detected" in out.lower()
        assert "not installed" not in out.lower(), (
            "SANE ran and reported nothing, which is different from SANE missing"
        )
        assert "scanimage ran" in out, "did not say that the tool actually answered"

    def test_a_missing_sane_names_the_ARCH_package(self, monkeypatch, permitted):
        """The Arch package is `sane` (shani-scanner -> sane-airscan -> sane).
        `sane-utils` is the Debian name for the same software and does not exist
        in the Arch repos, so a refusal naming it would send a user after a
        package they cannot install. Asserted in both directions."""
        _wire(monkeypatch, which=("lpstat",))
        out = printing._run({})
        assert "`sane`" in out, "did not name the Arch package"
        assert "scanner" in out.lower()
        assert "not an Arch package" in out or "sane-utils" not in out

    def test_scanimage_failing_is_not_no_scanners(self, monkeypatch, permitted):
        _wire(monkeypatch, printer="", scan="", scan_rc=1)
        out = printing._run({})
        assert "could not" in out.lower() or "unknown" in out.lower()
        assert "no scanners" not in out.lower()

    def test_the_two_halves_are_reported_independently(self, monkeypatch, permitted):
        """Printers can work while scanners cannot - they are different stacks
        with different availability, and one must not suppress the other."""
        _wire(monkeypatch, printer="HP_LaserJet\n", which=("lpstat",))
        out = printing._run({})
        assert "HP_LaserJet" in out, "the printer half was lost because SANE is missing"
        assert "`sane`" in out, "the printer half was lost because SANE is missing"


class TestSafety:
    def test_it_never_prints_or_scans_anything(self, monkeypatch, permitted):
        """This is a read-only inventory. Printing paper or putting an image
        into a scanner are physical side effects and belong in gated actuators.

        Checked on the argv list, not by substring: 'lp' is a substring of
        'lpstat', so a substring test passes or fails for the wrong reason.
        """
        seen = []
        _wire(monkeypatch, printer="HP_LaserJet\n", scan=SCANIMAGE_FOUND)

        real = printing.subprocess.run

        def record(cmd, **kwargs):
            seen.append(list(cmd))
            return real(cmd, **kwargs)

        monkeypatch.setattr(printing.subprocess, "run", record)
        printing._run({})
        for cmd in seen:
            assert cmd[0] != "lp", "the sense invoked the lp print command"
            assert cmd[0] != "scanimage" or set(cmd[1:]) == {"-L"}, (
                f"scanimage was called with something other than a listing: {cmd}"
            )

    def test_only_read_only_subcommands_are_used(self, monkeypatch, permitted):
        seen = []
        _wire(monkeypatch, printer="HP_LaserJet\n", scan=SCANIMAGE_FOUND)

        real = printing.subprocess.run

        def record(cmd, **kwargs):
            seen.append(list(cmd))
            return real(cmd, **kwargs)

        monkeypatch.setattr(printing.subprocess, "run", record)
        printing._run({})
        for cmd in seen:
            if cmd[0] == "lpstat":
                assert set(cmd[1:]) <= {"-r", "-e", "-d"}, f"unexpected lpstat flags: {cmd}"
            elif cmd[0] == "scanimage":
                assert cmd[1:] == ["-L"], f"unexpected scanimage flags: {cmd}"
            else:
                raise AssertionError(f"the sense invoked an unexpected tool: {cmd}")

    def test_a_timeout_does_not_read_as_no_printers(self, monkeypatch, permitted):
        _wire(monkeypatch, printer="",
              raises=subprocess.TimeoutExpired(["lpstat"], _TIMEOUT_FOR_TEST))
        out = printing._run({})
        assert "unknown" in out.lower()


class TestRegistry:
    def test_it_is_registered_and_gated(self):
        senses = discover_senses()
        assert "printing" in senses, "the sense loads but is not in the registry"
        assert senses["printing"].sensitivity in ("public", "personal", "private")

    def test_it_is_not_on_by_default(self):
        """Printer and scanner inventory is environment enumeration; it should
        be a deliberate grant, like the other peripheral senses."""
        assert "printing" not in _SENSE_DEFAULT_ENABLED

    def test_its_consent_key_exists_in_the_schema(self):
        import pathlib
        schema = pathlib.Path(
            "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text()
        assert 'name="printing-sense-enabled"' in schema, (
            "no consent key, so the sense is permanently ungrantable"
        )

    def test_it_appears_in_the_settings_surface(self):
        tools_schema = printing._SCHEMA
        assert tools_schema["type"] == "function"
        assert tools_schema["function"]["name"] == "printing"
        assert capabilities.find_capabilities is not None
