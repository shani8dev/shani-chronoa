"""The `gpu` sense, and the zero-VRAM trap.

Measured on this machine:

    $ cat /sys/class/drm/card1/device/uevent
    DRIVER=i915
    PCI_CLASS=30000
    PCI_ID=8086:9A49

    $ lspci | grep -i vga
    00:02.0 VGA compatible controller: Intel Corporation TigerLake-LP GT2
        [Iris Xe Graphics] (rev 01)

Two things that bit in development:

- **`/sys/class/drm` is mostly not GPUs.** This machine's directory holds
  `card1` plus `card1-DP-1`, `card1-DP-2`, `card1-DP-3` — connectors, not
  adapters. A `startswith("card")` filter counts four GPUs on a machine with
  one.
- **`lspci` gave the real name; sysfs did not.** `Intel Corporation DG2` is
  not a name a user recognises, while `[Iris Xe Graphics]` is. But
  `pciutils` ships in **no** profile, so this must be enrichment and never a
  requirement — which is what makes sysfs the primary source.
"""

import pytest

from shani_chronoa.senses import gpu

UEVENT = """DRIVER=i915
PCI_CLASS=30000
PCI_ID=8086:9A49
PCI_SLOT_NAME=0000:00:02.0
MODALIAS=pci:v00008086d00009A49sv0000sd0000bc03sc00i00
"""


@pytest.fixture
def drm(tmp_path, monkeypatch):
    root = tmp_path / "drm"
    root.mkdir()
    monkeypatch.setattr(gpu, "_DRM", root)
    monkeypatch.setattr(gpu.shutil, "which", lambda n: None)
    return root


def _card(root, name, *, uevent=UEVENT, vram=None, gtt=None, driver="i915"):
    device = root / name / "device"
    device.mkdir(parents=True)
    if uevent is not None:
        (device / "uevent").write_text(uevent)
    if vram is not None:
        (device / "mem_info_vram_total").write_text(f"{vram}\n")
    if gtt is not None:
        (device / "mem_info_gtt_total").write_text(f"{gtt}\n")
    if driver:
        # A real symlink, as the kernel exposes it. Making it a directory
        # would test the readlink-failure branch instead of the normal one.
        # The kernel points this at .../bus/pci/drivers/<name>, so the
        # basename really is the module name.
        target = device.parent.parent / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (target / "module").write_text(f"{driver}\n")
        (device / "driver").symlink_to(target)
    return device


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(gpu.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


class TestConnectorsAreNotAdapters:
    def test_a_card_with_connectors_is_one_gpu(self, drm, monkeypatch, granted):
        """The real layout on this machine: card1 plus card1-DP-1, -DP-2, -DP-3."""
        _card(drm, "card1")
        for connector in ("card1-DP-1", "card1-DP-2", "card1-DP-3"):
            (drm / connector).mkdir()
        assert [g["card"] for g in gpu.read_gpus()] == ["card1"], (
            "DRM connectors were counted as graphics adapters"
        )
        assert gpu._run({}).metadata["adapters"] == 1

    def test_two_cards_are_two_gpus(self, drm, monkeypatch, granted):
        _card(drm, "card0")
        _card(drm, "card1")
        assert len(gpu.read_gpus()) == 2

    def test_card_numbering_is_numeric(self, drm, monkeypatch, granted):
        _card(drm, "card0")
        _card(drm, "card1")
        (drm / "card-extra").mkdir()
        (drm / "renderD128").mkdir()
        assert sorted(g["card"] for g in gpu.read_gpus()) == ["card0", "card1"]


class TestTheZeroVramTrap:
    def test_zero_vram_is_reported_as_shared_memory_not_a_capacity(self, drm, monkeypatch, granted):
        """Integrated graphics report zero dedicated VRAM and use system RAM.
        Printing '0.0 GiB' is a confusing way of saying something true."""
        _card(drm, "card1", vram=0)
        content = gpu._run({}).content
        assert "no dedicated video memory" in content
        assert "shares system memory" in content
        assert "0.0 GiB" not in content

    def test_real_vram_is_reported_in_gibibytes(self, drm, monkeypatch, granted):
        _card(drm, "card0", vram=8 * 2**30)
        assert "8.0 GiB dedicated video memory" in gpu._run({}).content

    def test_a_driver_reporting_neither_leaves_it_unsaid(self, drm, monkeypatch, granted):
        """A discrete card on a driver without those files must not read as
        having zero memory."""
        _card(drm, "card0", vram=None, gtt=None)
        content = gpu._run({}).content
        assert "video memory" not in content

    def test_gtt_is_the_fallback(self, drm, monkeypatch, granted):
        _card(drm, "card0", vram=None, gtt=2 * 2**30)
        assert "2.0 GiB dedicated video memory" in gpu._run({}).content


class TestDriverAndIdentity:
    def test_the_kernel_driver_is_read(self, drm, monkeypatch, granted):
        _card(drm, "card1")
        (gpu_gpus := gpu.read_gpus())
        assert gpu_gpus[0]["driver"] == "i915"
        assert gpu_gpus[0]["driver_module"] == "i915"

    def test_the_pci_id_is_recorded_verbatim(self, drm, monkeypatch, granted):
        """`8086:9A49` identifies the silicon exactly, where the driver name
        does not - `i915` covers a decade of very different parts."""
        _card(drm, "card1")
        assert gpu.read_gpus()[0]["pci_id"] == "8086:9A49"

    def test_a_card_with_no_uevent_is_still_listed(self, drm, monkeypatch, granted):
        """Missing metadata is a fact about the file, not a reason to drop the
        adapter entirely."""
        _card(drm, "card0", uevent=None)
        found = gpu.read_gpus()
        assert [g["card"] for g in found] == ["card0"]
        assert "driver" not in found[0]


class TestLspciIsOptionalEnrichment:
    def test_the_marketing_name_is_used_when_available(self, drm, monkeypatch, granted):
        _card(drm, "card1")
        monkeypatch.setattr(gpu.shutil, "which",
                            lambda n: "/usr/bin/lspci" if n == "lspci" else None)

        import subprocess

        class R:
            stdout = ("00:02.0 VGA compatible controller: Intel Corporation "
                      "TigerLake-LP GT2 [Iris Xe Graphics] (rev 01)\n"
                      "00:1f.3 Audio device: Intel Corporation Cannon Lake PCH\n")
            stderr = ""
            returncode = 0

        monkeypatch.setattr(gpu.subprocess, "run", lambda *a, **k: R())
        content = gpu._run({}).content
        assert "Iris Xe Graphics" in content
        assert "i915" in content, "the kernel driver must still be shown"
        assert gpu._run({}).metadata["lspci_present"] is True

    def test_a_missing_lspci_says_which_ids_are_being_shown(self, drm, monkeypatch, granted):
        """`pciutils` ships in no profile, so this is the default path on a
        real ShaniOS install and it must be explicit about the difference."""
        _card(drm, "card1")
        content = gpu._run({}).content
        assert "lspci is not installed" in content
        assert "kernel's own identifiers" in content
        assert gpu._run({}).metadata["lspci_present"] is False

    def test_lspci_ignores_non_display_devices(self, drm, monkeypatch, granted):
        _card(drm, "card1")
        monkeypatch.setattr(gpu.shutil, "which",
                            lambda n: "/usr/bin/lspci" if n == "lspci" else None)

        import subprocess

        class R:
            stdout = ("00:1f.3 Audio device: Intel Corporation Cannon Lake PCH cAVS\n"
                      "00:02.0 VGA compatible controller: Real GPU\n")
            stderr = ""
            returncode = 0

        monkeypatch.setattr(gpu.subprocess, "run", lambda *a, **k: R())
        assert gpu._lspci() == ["Real GPU"]


class TestDegradeRatherThanRefuse:
    def test_no_drm_and_no_lspci_distinguishes_the_two(self, drm, monkeypatch, granted):
        result = gpu._run({})
        assert "what could be read" in result.content
        assert result.metadata["adapters"] == 0

    def test_an_unreadable_drm_is_not_claimed_to_be_a_headless_server(self, drm, monkeypatch, granted):
        monkeypatch.setattr(gpu.shutil, "which",
                            lambda n: "/usr/bin/lspci" if n == "lspci" else None)
        result = gpu._run({})
        assert "does not use DRM" in result.content
        assert result.metadata["lspci_present"] is True

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(gpu.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(gpu._run({}), str)
