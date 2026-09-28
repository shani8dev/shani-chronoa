"""The `devices` sense, and the two parsing bugs it shipped with.

Both bugs were found by running it against this machine's real buses, and both
produce a wrong answer that looks entirely reasonable:

- **A PCI class is a 24-bit value.** `0x030000` is a display controller —
  `03` base class, `00` subclass. Slicing from index 2 takes "0000" and calls
  *every* device unclassified. The real machine then reports 1 display
  controller, 4 PCI bridges, 3 USB controllers, 1 audio device, 1 ethernet
  controller and 1 host bridge, which is what it should report.

- **A USB interface has no class of its own.** Entries like `1-0:1.0` are
  *interfaces* of a device; `bDeviceClass` lives on the parent. Without walking
  up, every root hub and every composite device comes out unclassified, and the
  hub/peripheral split — the whole point of the USB half — cannot be made.

The real readings, for the record:

    {'slot': '0000:00:02.0', 'class': 'display controller', 'id': '8086:9a49', 'driver': 'i915'}
    {'slot': '0000:04:00.0', 'class': 'ethernet controller', 'id': '10ec:8168', 'driver': 'r8169'}
    {'port': '3-10', 'class': 'wireless controller', 'speed_mbps': 12}

    pci 22, unclaimed 3, usb 17, hubs 4
"""

import pytest

from shani_chronoa.senses import devices


def _pci(root, slot, *, class_code="0x030000", vendor="0x8086", device="0x9a49",
         driver="i915"):
    entry = root / slot
    entry.mkdir(parents=True)
    (entry / "class").write_text(class_code + "\n")
    (entry / "vendor").write_text(vendor + "\n")
    (entry / "device").write_text(device + "\n")
    if driver:
        # Outside the bus root: a target created inside it would be picked up
        # by iterdir() as if it were a device.
        target = root.parent / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (entry / "driver").symlink_to(target)
    return entry


def _usb(root, name, *, product=None, manufacturer=None, class_code=None,
         speed=None):
    entry = root / name
    entry.mkdir(parents=True)
    for field, value in (("product", product), ("manufacturer", manufacturer),
                         ("bDeviceClass", class_code), ("speed", speed)):
        if value is not None:
            (entry / field).write_text(f"{value}\n")
    return entry


@pytest.fixture
def buses(tmp_path, monkeypatch):
    pci = tmp_path / "pci"
    usb = tmp_path / "usb"
    pci.mkdir()
    usb.mkdir()
    monkeypatch.setattr(devices, "_PCI", pci)
    monkeypatch.setattr(devices, "_USB", usb)
    monkeypatch.setattr(devices.shutil, "which", lambda n: None)
    return pci, usb


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(devices.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


class TestPciClassParsing:
    def test_a_display_controller_is_not_unclassified(self, buses, granted):
        """`0x030000` is `03`/`00`. Slicing from index 2 gets "0000"."""
        _pci(buses[0], "0000:00:02.0", class_code="0x030000", driver="i915")
        (found,) = devices.read_pci()
        assert found["class"] == "display controller"

    def test_the_real_classes_on_this_machine(self, buses, granted):
        _pci(buses[0], "a", class_code="0x060000", driver=None)
        _pci(buses[0], "b", class_code="0x060400", driver="pcieport")
        _pci(buses[0], "c", class_code="0x0c0330", driver="xhci_hcd")
        _pci(buses[0], "d", class_code="0x020000", driver="r8169")
        _pci(buses[0], "e", class_code="0x028000", driver="iwlwifi")
        _pci(buses[0], "f", class_code="0x010802", driver="nvme")
        classes = {d["slot"]: d["class"] for d in devices.read_pci()}
        assert classes == {
            "a": "host bridge", "b": "PCI bridge", "c": "USB controller",
            "d": "ethernet controller", "e": "audio device",
            "f": "non-Volatile memory controller",
        }

    def test_an_unmapped_class_is_unknown_not_guessed(self, buses, granted):
        _pci(buses[0], "a", class_code="0x0a0040", driver="x")
        assert devices.read_pci()[0]["class"] == "unknown"

    def test_a_class_without_the_0x_prefix_still_parses(self, buses, granted):
        _pci(buses[0], "a", class_code="30000", driver="i915")
        assert devices.read_pci()[0]["class"] == "display controller"


class TestUnclaimedDevices:
    def test_a_device_with_no_driver_symlink_is_unclaimed(self, buses, granted):
        _pci(buses[0], "a", driver=None)
        (found,) = devices.read_pci()
        assert found["driver"] is None
        assert "NO DRIVER BOUND" in devices._run({}).content

    def test_unclaimed_is_a_finding_not_a_read_failure(self, buses, granted):
        """An unbound device is hardware the kernel does not know what to do
        with. That is different from a driver that could not be read."""
        _pci(buses[0], "a", driver=None)
        _pci(buses[0], "b", driver="nvme")
        content = devices._run({}).content
        assert "no driver bound" in content
        assert "different from a device whose driver could not be read" in content
        assert devices._run({}).metadata["pci_unclaimed"] == 1

    def test_every_device_bound_means_none_flagged(self, buses, granted):
        _pci(buses[0], "a", driver="i915")
        assert devices._run({}).metadata["pci_unclaimed"] == 0
        assert "NO DRIVER BOUND" not in devices._run({}).content


class TestUsbIsATree:
    def test_an_interface_takes_its_parents_class(self, buses, granted):
        """`1-0:1.0` is an interface; `bDeviceClass` lives on `usb1`."""
        _usb(buses[1], "usb1", product="xHCI Host Controller", class_code="09")
        _usb(buses[1], "1-0", product="xHCI Host Controller", class_code="09")
        _usb(buses[1], "1-0:1.0")
        found = {d["port"]: d for d in devices.read_usb()}
        assert found["usb1"]["class"] == "hub"
        assert "1-0:1.0" in found
        assert found["1-0:1.0"].get("class") == "hub", (
            f"an interface with no class of its own was left unclassified: "
            f"{found['1-0:1.0']}"
        )

    def test_a_hub_and_a_peripheral_are_distinguished(self, buses, granted):
        _usb(buses[1], "usb1", class_code="09", product="xHCI Host Controller")
        _usb(buses[1], "3-10", class_code="e0", product="WiFi", speed="12")
        content = devices._run({}).content
        assert "1 of these are root hubs" in content
        assert "1 are peripherals" in content
        assert devices._run({}).metadata["usb_hubs"] == 1

    def test_a_peripheral_is_not_called_a_hub(self, buses, granted):
        _usb(buses[1], "3-1", class_code="03", product="Keyboard")
        assert devices.read_usb()[0]["class"] == "human interface device"
        assert "root hubs" not in devices._run({}).content

    def test_the_port_path_is_kept(self, buses, granted):
        """Which port something is in is the question a user has about a device
        that stopped working."""
        _usb(buses[1], "3-10.2", class_code="08", product="Thumb drive")
        found = devices.read_usb()[0]
        assert found["port"] == "3-10.2"
        assert "port 3-10.2" in devices._run({}).content

    def test_link_speed_is_reported_when_present(self, buses, granted):
        _usb(buses[1], "3-10", class_code="e0", speed="12")
        assert "12 Mb/s" in devices._run({}).content

    def test_a_device_with_no_speed_says_nothing_about_speed(self, buses, granted):
        _usb(buses[1], "3-1", class_code="03", product="Keyboard")
        assert "Mb/s" not in devices._run({}).content

    def test_an_unmapped_usb_class_keeps_its_number(self, buses, granted):
        _usb(buses[1], "3-1", class_code="42")
        assert devices.read_usb()[0]["class"] == "class 42"


class TestToolsAreOptionalEnrichment:
    def test_the_output_says_when_it_is_showing_kernel_ids(self, buses, granted):
        _pci(buses[0], "a", driver="i915")
        content = devices._run({}).content
        assert "lspci and lsusb not installed" in content
        assert devices._run({}).metadata["lspci_present"] is False

    def test_both_present_is_reported(self, buses, monkeypatch, granted):
        monkeypatch.setattr(devices.shutil, "which", lambda n: f"/usr/bin/{n}")
        _pci(buses[0], "a", driver="i915")
        percept = devices._run({})
        assert percept.metadata["lspci_present"] is True
        assert percept.metadata["lsusb_present"] is True
        assert "not installed" not in percept.content


class TestDegradeRatherThanRefuse:
    def test_no_buses_at_all_is_undetermined_not_empty_hardware(self, tmp_path, monkeypatch, granted):
        monkeypatch.setattr(devices, "_PCI", tmp_path / "no-pci")
        monkeypatch.setattr(devices, "_USB", tmp_path / "no-usb")
        percept = devices._run({})
        assert percept.metadata["determined"] is False
        assert "fact about what could be read" in percept.content
        assert percept.metadata["determined"] is False

    def test_one_bus_present_is_still_reported(self, tmp_path, monkeypatch, granted):
        pci = tmp_path / "pci"
        usb = tmp_path / "usb"
        pci.mkdir()
        usb.mkdir()
        monkeypatch.setattr(devices, "_PCI", pci)
        monkeypatch.setattr(devices, "_USB", usb)
        _usb(usb, "3-1", class_code="03", product="Keyboard")
        percept = devices._run({})
        assert percept.metadata["usb"] == 1
        assert percept.metadata["pci"] == 0

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(devices.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(devices._run({}), str)
