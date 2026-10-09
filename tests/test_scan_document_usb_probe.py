"""`scan_document`: "no scanner" is two different answers.

When the answer to "scan this" is "No scanner is connected", the usual truth
is one of two things needing different fixes: there is genuinely no scanner
plugged in, or there is one and SANE's backend for it is missing (the vendor
driver, or `sane-airscan` for a network scanner). `scanimage` answers "no
devices" either way, so it cannot tell them apart.

`sane-find-scanner` walks the USB bus itself and can, so the skill runs it
**only when SANE sees nothing** and reports which of the two it is.

Two things measured on the real binary here:

- with no scanner attached it prints an explanatory banner (`# No SCSI
  scanners found...`) and **exits 0** - so the banner prose is not a finding,
  and a line only counts when it says `found USB scanner`.
- a run that cannot answer (`sane-find-scanner` absent, or non-zero) is
  *not* "no scanner is connected" - it is reported as "could not be checked",
  because "I could not ask" and "there is none" are opposite answers.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import scan_document as sd  # noqa: E402

#: Real `sane-find-scanner` output shape when it finds a scanner: the banner
#: is prose about what it is about to do, and the finding is one line. The
#: banner is the reason a bare "scanner" substring match would be wrong.
FOUND_OUTPUT = """\

  # sane-find-scanner will now attempt to detect your scanner. If the
  # result is different from what you expected, first make sure your
  # scanner is powered up and properly connected to your computer.

found USB scanner (vendor=0x04a9 [Canon], product=0x2229 [CanoScan],
 chip=LM9832) at libusb:001:004
  # Your USB scanner was (probably) detected. It may or may not be supported
  # by SANE.
"""


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    """Stand-in `scanimage` and `sane-find-scanner` on PATH.

    `scanimage -f` answers with the SANE device list (empty by default -
    the case under test), and `sane-find-scanner` answers with `probe`.

    **`isolated=True` builds a PATH that does not contain the real tools**,
    with a `python3` symlink so the stand-ins' shebang still resolves. That
    is the only way "sane-find-scanner is not installed" can be tested on a
    machine that has it: prepending a fake dir leaves the real binary later
    in PATH and the test then measures the real tool instead of the case it
    is about.
    """
    def _install(sane_devices="", probe=FOUND_OUTPUT, probe_code=0, probe_missing=False,
                 isolated=False):
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        scanimage = bindir / "scanimage"
        scanimage.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"print({sane_devices!r}, end='')\n"
            "raise SystemExit(0)\n"
        )
        scanimage.chmod(0o755)
        if not probe_missing:
            finder = bindir / "sane-find-scanner"
            data = tmp_path / "probe.txt"
            data.write_text(probe)
            finder.write_text(
                "#!/usr/bin/env python3\n"
                f"print(open({str(data)!r}).read(), end='')\n"
                f"raise SystemExit({probe_code})\n"
            )
            finder.chmod(0o755)
        if isolated:
            (bindir / "python3").symlink_to(sys.executable)
            monkeypatch.setenv("PATH", str(bindir))
        else:
            monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
        return bindir
    return _install


def test_a_scanner_sane_cannot_open_is_named_and_explained(fake_tools):
    """**The case that had no answer: a scanner on the bus SANE cannot open.**

    The finding line is parsed out of the real output, and the answer says
    which of the two "no scanner" truths this is - with the missing-backend
    cause named, because that is the part a person can act on.
    """
    fake_tools(sane_devices="", probe=FOUND_OUTPUT)
    out = sd._run({"source": "scanner", "list_only": True})
    assert "SANE lists no scanners" in out
    assert "scanner IS on the USB bus" in out
    assert "Canon" in out              # the vendor/product from the finding
    assert "CanoScan" in out
    assert "missing backend" in out
    # The old answer is not repeated: "No scanner is connected" would be a
    # confident wrong answer here, and the point of the probe is to not say it.
    assert "No scanner is connected" not in out


def test_nothing_on_the_bus_says_so(fake_tools):
    """Real output shape for the no-scanner case: banner prose, exit 0."""
    fake_tools(sane_devices="", probe="\n  # No SCSI scanners found. If you expected "
                                      "something different, make sure that you have "
                                      "loaded a kernel SCSI driver.\n")
    out = sd._run({"source": "scanner", "list_only": True})
    assert "No scanner is connected" in out
    assert "nothing on the USB bus either" in out
    assert "missing backend" not in out


def test_a_probe_that_cannot_run_is_not_a_no(fake_tools):
    """**"I could not ask" is not "there is none".**

    With `sane-find-scanner` absent the answer says the bus could not be
    checked and still says SANE sees nothing - two facts, in one sentence,
    neither of them pretending to be the other.
    """
    fake_tools(sane_devices="", probe_missing=True, isolated=True)
    out = sd._run({"source": "scanner", "list_only": True})
    assert "No scanner is connected" in out
    assert "could not be checked" in out
    assert "nothing on the USB bus either" not in out


def test_a_failing_probe_is_not_a_no(fake_tools):
    fake_tools(sane_devices="", probe="permission denied", probe_code=1)
    out = sd._run({"source": "scanner", "list_only": True})
    assert "No scanner is connected" in out
    assert "could not be checked" in out


def test_a_scanner_sane_sees_is_listed_without_the_probe(fake_tools):
    """The probe is not run when SANE already has an answer."""
    fake_tools(sane_devices="pixma:04A91749_7AF8C1\tCanon PIXMA MG3600\n")
    out = sd._run({"source": "scanner", "list_only": True})
    assert "Scanners: pixma:04A91749_7AF8C1 (Canon PIXMA MG3600)" in out
    assert "USB bus" not in out


def test_the_scanner_sources_answer_differs_only_in_the_final_sentence(fake_tools):
    """Both paths that used to say the same thing now say the two truths."""
    fake_tools(sane_devices="", probe=FOUND_OUTPUT)
    listing = sd._run({"source": "scanner", "list_only": True})
    scanning = sd._run({"source": "scanner"})
    assert "scanner IS on the USB bus" in listing
    assert "scanner is on the USB bus" in scanning
    assert "camera" in scanning          # the other answer is still offered


def test_the_banner_is_not_mistaken_for_a_finding(fake_tools):
    """A control that can fail: a banner containing the word "scanner".

    The real tool's banner says *"will now attempt to detect your scanner"* -
    a bare `"scanner" in line` match reports every machine as having a
    scanner. Only the verb `found USB scanner` counts, and this is the case
    that proves it.
    """
    fake_tools(sane_devices="", probe="\n  # sane-find-scanner will now attempt to "
                                      "detect your scanner.\n")
    out = sd._run({"source": "scanner", "list_only": True})
    assert "No scanner is connected" in out
    assert "USB bus" not in out.replace("nothing on the USB bus either", "")
