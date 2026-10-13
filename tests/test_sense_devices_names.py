"""The readable names `lspci` and `lsusb` add to the devices sense.

These two readers existed, and nothing called them. The module docstring and
the sense's own schema description both claimed *"the tools are enrichment: used
when present, and the output says which it is showing"* - and neither half was
true, so the false claim sat in the text the model is offered.

Wiring them was not a one-line change: **both readers threw away the key**, so
even a caller holding the list could not have matched a name to a device.
`_lspci` kept `line.split(": ", 1)[-1]` and nothing else; `_lsusb` returned whole
lines. These tests pin the two joins, and - more to the point - pin that the
provenance sentence appears when a tool named something and is **absent** when it
did not, which is the half that was false.

The captured output below is real, from `lspci`/`lsusb` on this machine. It is
chosen so each join can be told apart from a plausible wrong one:

* the PCI slot is `lspci`'s `BB:DD.F`, which is the **last seven characters** of
  the sysfs name `0000:BB:DD.F`;
* the USB key is `vid:pid`, and deliberately **not** `Bus NNN Device NNN`,
  because those are different numbering schemes - measured here, `lsusb` calls
  `30c9:003a` "Bus 003 Device 002" while sysfs names that device `3-8`. A test
  fixture keyed on the bus/device number would look right and join wrong.
"""

import pytest

from shani_chronoa.senses import devices


# Captured verbatim from this machine, trimmed to three devices each.
LSPCI = """\
00:02.0 VGA compatible controller: Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics] (rev 01)
07:00.0 Non-Volatile memory controller: KIOXIA Corporation NVMe SSD Controller BG6 (rev 00)
00:1f.3 Audio device: Intel Corporation Sunrise Point-LP HD Audio
"""

LSUSB = """\
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 003 Device 002: ID 30c9:003a Luxvisions Innotech Limited Integrated Camera
Bus 003 Device 003: ID 06cb:00da Synaptics, Inc.
"""


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """A bindir holding the two tools, and `PATH` as *only* that bindir.

    `PATH` is the bindir and nothing else rather than bindir-plus-the-real-one:
    a PATH that still resolves `/usr/bin/lsusb` makes "only this tool is
    installed" a statement about the host rather than about the test.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, output in (("lspci", LSPCI), ("lsusb", LSUSB)):
        # `printf`, not `cat`: PATH is this bindir and nothing else, so a fake
        # that shelled out to any real command would find nothing to run - and
        # would silently produce no output, which reads as "the tool said
        # nothing" rather than as a broken fixture.
        lines = " ".join(f"'{line}'" for line in output.splitlines())
        path = bindir / name
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' {lines}\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


@pytest.fixture
def no_tools(tmp_path, monkeypatch):
    bindir = tmp_path / "empty-bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


def _pci_slot(root, name, **files):
    entry = root / name
    entry.mkdir(parents=True)
    for filename, text in files.items():
        (entry / filename).write_text(text)
    return entry


def _usb_device(root, name, **files):
    entry = root / name
    entry.mkdir(parents=True)
    for filename, text in files.items():
        (entry / filename).write_text(text)
    return entry


# --- the two joins -------------------------------------------------------------


class TestTheKeysTheTwoReadersReturn:
    def test_lspci_is_keyed_by_the_slot_it_prints(self, tools):
        keys = devices._lspci()
        assert set(keys) == {"00:02.0", "07:00.0", "00:1f.3"}

    def test_the_lspci_value_is_the_vendor_not_the_class(self, tools):
        """`00:02.0 VGA compatible controller: Intel ...` - the class sits
        between the slot and the name, so splitting on the *first* ": " keeps
        the class and calls it the name."""
        assert devices._lspci()["00:02.0"].startswith("Intel Corporation")

    def test_lsusb_is_keyed_by_vendor_and_product(self, tools):
        assert set(devices._lsusb()) == {"1d6b:0002", "30c9:003a", "06cb:00da"}

    def test_lsusb_is_not_keyed_by_the_bus_and_device_numbers(self, tools):
        """The measured reason for the key being what it is.

        `Bus 003 Device 002` is sysfs device `3-8`, so a key built from those
        two numbers matches nothing - and if it ever matched something it would
        be the wrong device.
        """
        for key in devices._lsusb():
            assert "Bus" not in key and "Device" not in key
            assert key.count(":") == 1

    def test_a_line_with_no_id_is_skipped_rather_than_half_read(self, tools, tmp_path,
                                                                monkeypatch):
        bindir = tmp_path / "bin2"
        bindir.mkdir()
        (bindir / "lspci").write_text(
            "#!/bin/sh\nprintf '%s\\n' 'garbage without a slot'\n")
        (bindir / "lspci").chmod(0o755)
        (bindir / "lsusb").write_text(
            "#!/bin/sh\nprintf '%s\\n' 'garbage without an id'\n")
        (bindir / "lsusb").chmod(0o755)
        monkeypatch.setenv("PATH", str(bindir))
        assert devices._lspci() == {}
        assert devices._lsusb() == {}


class TestAPciRecordTakesTheNameForItsOwnSlot:
    def test_the_sysfs_name_is_matched_by_its_last_seven_characters(self, tmp_path,
                                                                     monkeypatch):
        root = tmp_path / "pci"
        _pci_slot(root, "0000:00:02.0", **{"class": "0x030000",
                                           "vendor": "0x8086", "device": "0x9a49"})
        monkeypatch.setattr(devices, "_PCI", root)
        (row,) = devices.read_pci({"00:02.0": "Intel Corporation GT2"})
        assert row["name"] == "Intel Corporation GT2"
        assert row["named_by"] == "lspci"

    def test_a_name_for_another_slot_is_not_borrowed(self, tmp_path, monkeypatch):
        """The join is by slot, so a neighbouring device's name cannot land here."""
        root = tmp_path / "pci"
        _pci_slot(root, "0000:00:02.0", **{"class": "0x030000"})
        _pci_slot(root, "0000:07:00.0", **{"class": "0x0108"})
        monkeypatch.setattr(devices, "_PCI", root)
        rows = {r["slot"]: r for r in devices.read_pci({"07:00.0": "KIOXIA NVMe"})}
        assert rows["0000:00:02.0"].get("named_by") is None
        assert rows["0000:07:00.0"]["name"] == "KIOXIA NVMe"

    def test_no_names_means_no_name_keys_at_all(self, tmp_path, monkeypatch):
        root = tmp_path / "pci"
        _pci_slot(root, "0000:00:02.0", **{"class": "0x030000"})
        monkeypatch.setattr(devices, "_PCI", root)
        (row,) = devices.read_pci()
        assert "name" not in row and "named_by" not in row


class TestAUsbRecordTakesTheNameForItsOwnIds:
    def test_the_join_is_on_vendor_and_product(self, tmp_path, monkeypatch):
        root = tmp_path / "usb"
        _usb_device(root, "3-8", idVendor="30c9", idProduct="003a")
        monkeypatch.setattr(devices, "_USB", root)
        (row,) = devices.read_usb({"30c9:003a": "Luxvisions Innotech Limited Camera"})
        assert row["name"] == "Luxvisions Innotech Limited Camera"
        assert row["named_by"] == "lsusb"

    def test_the_ids_are_matched_case_insensitively(self, tmp_path, monkeypatch):
        """sysfs is lowercase hex and `lsusb` prints lowercase too, but the
        normalisation is what makes the join total rather than lucky."""
        root = tmp_path / "usb"
        _usb_device(root, "3-8", idVendor="30C9", idProduct="003A")
        monkeypatch.setattr(devices, "_USB", root)
        (row,) = devices.read_usb({"30c9:003a": "Camera"})
        assert row["name"] == "Camera"

    def test_an_interface_with_no_ids_is_left_unnamed(self, tmp_path, monkeypatch):
        """`3-8:1.0` has no `idVendor` of its own, so no key can be built for it.

        This is the honest residue: on this machine 7 of 17 USB entries are
        named and the other 10 are interfaces. It is a limitation of the join,
        not a read failure, and the sense says so.
        """
        root = tmp_path / "usb"
        _usb_device(root, "3-8", idVendor="30c9", idProduct="003a")
        _usb_device(root, "3-8:1.0", **{"bInterfaceClass": "0e"})
        monkeypatch.setattr(devices, "_USB", root)
        rows = {r["port"]: r for r in devices.read_usb({"30c9:003a": "Camera"})}
        assert rows["3-8"]["named_by"] == "lsusb"
        assert "named_by" not in rows["3-8:1.0"]


# --- the sentence that was false ----------------------------------------------


@pytest.fixture
def bus(tmp_path, monkeypatch):
    """A one-device bus, so the sense's own output is small enough to read."""
    pci = tmp_path / "pci"
    _pci_slot(pci, "0000:00:02.0", **{"class": "0x030000", "vendor": "0x8086",
                                     "device": "0x9a49"})
    usb = tmp_path / "usb"
    _usb_device(usb, "3-8", idVendor="30c9", idProduct="003a", product="Integrated Camera")
    monkeypatch.setattr(devices, "_PCI", pci)
    monkeypatch.setattr(devices, "_USB", usb)
    return tmp_path


def _text(bus_fixture):
    out = devices._run({})
    return out.content if hasattr(out, "content") else str(out)


class TestTheProvenanceSentence:
    def test_it_says_which_tool_named_how_many(self, bus, tools):
        said = _text(bus)
        assert "Readable names above came from" in said
        assert "lspci" in said and "lsusb" in said

    def test_the_name_is_actually_in_the_output(self, bus, tools):
        # The substring is taken from `LSPCI`, not invented: an earlier version
        # asserted "Intel Corporation GT2", which the captured line does not
        # contain - it says "TigerLake-LP GT2" - so the assertion failed while
        # the name was plainly in the output.
        said = _text(bus)
        assert "Intel Corporation TigerLake-LP GT2" in said

    def test_it_is_absent_when_no_tool_named_anything(self, bus, no_tools):
        """The negative control for the sentence.

        Before this change the line existed only in its *absent-tools* form, so
        a claim that it "says which it is showing" was unfalsifiable: nothing
        rendered it and nothing checked for it.
        """
        said = _text(bus)
        assert "Readable names above" not in said

    def test_the_name_is_absent_without_the_tools(self, bus, no_tools):
        assert "Intel Corporation GT2" not in _text(bus)

    def test_it_does_not_claim_the_tools_are_missing_when_they_are_present(self, bus, tools):
        """The other half of the false claim.

        `lspci and lsusb not installed, so these are the kernel's own class
        codes and ids rather than the tools' names` was guarded on the tools
        being *absent*, so on a machine where both are installed - the case the
        docstring describes - nothing said so, and nothing had added a name.
        """
        said = _text(bus)
        assert "not installed" not in said

    def test_the_absent_tools_line_is_still_true_when_a_tool_is_missing(self, bus, tools,
                                                                        tmp_path):
        """And the direction that was true must not have been broken."""
        (tmp_path / "bin" / "lsusb").unlink()
        said = _text(bus)
        assert "lsusb not installed" in said
        assert "Intel Corporation TigerLake-LP GT2" in said      # the other tool still names


class TestTheRenderedRow:
    def test_a_name_the_product_already_says_is_not_printed_twice(self, bus, tools):
        """`Integrated Camera` is both the sysfs `product` and the tail of
        lsusb's "Luxvisions Innotech Limited Integrated Camera". Printing both
        reads as a stutter."""
        said = _text(bus)
        assert said.count("Integrated Camera") == 1

    def test_an_empty_product_does_not_suppress_the_only_readable_text(self, bus, tools):
        """`"" in anything` is True, so without the emptiness guard a device
        with no `product` file - the case where the tool's name is all there is
        - hides its own name."""
        bus_root = bus / "usb"
        (bus_root / "3-9").mkdir()
        (bus_root / "3-9" / "idVendor").write_text("06cb")
        (bus_root / "3-9" / "idProduct").write_text("00da")
        said = _text(bus)
        assert "Synaptics, Inc." in said