"""Tests for `HardwareProfile._detect_gpu`, and the defect that made them.

`vulkaninfo --summary` **exits 0 on a machine with no GPU at all** when Mesa is
emulating on the CPU. Measured on a real ShaniOS `@blue` slot with a working
Intel i915 adapter (`card1`, four DP connectors):

    GPU0:
            apiVersion         = 1.4.354
            deviceType         = PHYSICAL_DEVICE_TYPE_CPU
            deviceName         = llvmpipe (LLVM 22.1.8, 256 bits)
            driverName         = llvmpipe

The old check asked "did `vulkaninfo` exit 0" and answered yes, so that machine
was graded `gpu_available = True, profile = "gpu"` - which is what picks the
larger model - while every pixel it drew went through the CPU.

`clinfo` has the same shape one level over: measured, **0 platforms** and
**rc=0**. Neither tool's exit status is a statement about a GPU.

Every fixture below is **captured output**, because the two signals are
load-bearing in different places and a hand-typed fixture hides the second:

- the renderer's free-form, version-stamped name (`llvmpipe (LLVM 22.1.8, ...)`),
- the spec's own `deviceType = PHYSICAL_DEVICE_TYPE_CPU`, which is what says
  "software" regardless of what the device is *called*.
"""

from __future__ import annotations

import subprocess

import pytest

from shani_chronoa import hardware_profile as hp


# --- captured: the real slot's llvmpipe-only summary ------------------------
LLVMPILE_ONLY = """\
VULKANINFO
==========
Vulkan Instance Version: 1.4.357

GPU0:
\tapiVersion         = 1.4.354
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 22.1.8, 256 bits)
\tdriverName         = llvmpipe
\tdeviceID           = 0
"""

# --- captured: a machine with a real GPU through the same tool --------------
HARDWARE_GPU = """\
GPU0:
\tapiVersion         = 1.3.290
\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
\tdeviceName         = NVIDIA GeForce RTX 3060
\tdriverName         = NVIDIA
\tdeviceID           = 0

GPU1:
\tapiVersion         = 1.3.290
\tdeviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
\tdeviceName         = Intel(R) UHD Graphics 620 (KBL GT2)
\tdriverName         = Intel
\tdeviceID           = 1
"""

# --- captured: a Vulkan-capable machine with several headers around it ------
# The real output has `Devices:` and `VkPhysicalDeviceProperties:` sections that
# also end in a colon. Counting those as devices is the parse this file exists
# to prevent.
MIXED_HEADERS = """\
Vulkan Instance Version: 1.4.357

Instance Extensions: count = 26
-------------------------------
VK_EXT_acquire_drm_display             : extension revision 1

Devices:
========
VkPhysicalDeviceProperties:
\tapiVersion         = 1.4.354
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 22.1.8, 256 bits)

GPU0:
\tapiVersion         = 1.4.354
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 22.1.8, 256 bits)
"""


@pytest.fixture
def probe(monkeypatch):
    """Answer the three GPU probes by tool name.

    Keyed on the binary, so a probe the code should not make is a miss rather
    than a silently reused answer - and `missing` models a tool that is not
    installed, which is a *different* state from one that answers.
    """

    def install(vulkan=None, nvidia=None, amd=None, missing=()):
        def run(argv, **kwargs):
            tool = argv[0]
            if tool in missing:
                raise FileNotFoundError(2, "No such file", tool)
            answer = {"vulkaninfo": vulkan, "nvidia-smi": nvidia,
                      "rocm-smi": amd}.get(tool)
            if answer is None:
                raise FileNotFoundError(2, "No such file", tool)
            rc, out, err = answer
            return subprocess.CompletedProcess(argv, rc, out, err)
        monkeypatch.setattr(hp.subprocess, "run", run)
    return install


def profile(**kw):
    p = hp.HardwareProfile.__new__(hp.HardwareProfile)
    p.gpu_available = False
    p.gpu_type = "none"
    p.gpu_name = ""
    p.ram_mb = 8192
    p.cpu_cores = 4
    p.profile = "auto"
    p._detect_gpu()
    p._select_profile()
    return p


# --------------------------------------------------------------------------
# The defect: exit 0 is not a GPU
# --------------------------------------------------------------------------

class TestVulkanExitZeroIsNotAGpu:

    def test_a_cpu_typed_device_is_not_a_gpu(self, probe):
        # **The exact failure, reproduced.** rc=0, real output from the real
        # slot, and the answer must be no.
        probe(vulkan=(0, LLVMPILE_ONLY, ""))
        p = profile()
        assert p.gpu_available is False
        assert p.profile != "gpu", (
            "a software renderer must not select the GPU tier - that is the "
            "whole defect")

    def test_the_software_renderer_is_still_named(self, probe):
        # Dropping the finding would be the opposite mistake: the machine DOES
        # have a Vulkan stack, it is just emulating.
        probe(vulkan=(0, LLVMPILE_ONLY, ""))
        p = profile()
        assert p.gpu_type == "software"
        assert "llvmpipe" in p.gpu_name

    def test_a_hardware_device_is_a_gpu(self, probe):
        probe(vulkan=(0, HARDWARE_GPU, ""))
        p = profile()
        assert p.gpu_available is True
        assert p.gpu_type == "vulkan"
        assert "RTX 3060" in p.gpu_name

    def test_one_hardware_device_among_software_ones_is_a_gpu(self, probe):
        # Both kinds on one machine is the case a `not devices` check gets
        # wrong: llvmpipe listed first would decide for the whole machine.
        both = HARDWARE_GPU.replace("GPU0:", "GPU0:\n\tdeviceType         "
                                       "= PHYSICAL_DEVICE_TYPE_CPU\n"
                                       "\tdeviceName         = llvmpipe", 1)
        probe(vulkan=(0, both, ""))
        p = profile()
        assert p.gpu_available is True

    def test_a_device_typed_cpu_is_software_whatever_it_is_named(self, probe):
        # The spec's own type is load-bearing: a CPU-typed device with a
        # hardware-looking name is still emulated, and the reverse is a renamed
        # fallback. Matching only the name would miss one of these.
        renamed = LLVMPILE_ONLY.replace("llvmpipe (LLVM 22.1.8, 256 bits)",
                                         "Mesa Intel(R) UHD Graphics 620")
        probe(vulkan=(0, renamed, ""))
        assert profile().gpu_available is False

    def test_a_software_name_is_software_whatever_it_is_typed(self, probe):
        retype = LLVMPILE_ONLY.replace("PHYSICAL_DEVICE_TYPE_CPU",
                                       "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU")
        probe(vulkan=(0, retype, ""))
        assert profile().gpu_available is False

    def test_no_devices_at_all_is_not_a_gpu(self, probe):
        # rc=0 and an empty summary - which is what `vulkaninfo` prints when
        # there is no ICD at all.
        probe(vulkan=(0, "Vulkan Instance Version: 1.4.357\n", ""))
        p = profile()
        assert p.gpu_available is False
        assert p.gpu_type == "none"

    def test_a_nonzero_exit_is_not_a_gpu(self, probe):
        probe(vulkan=(1, "", "ERROR: cannot find ICD"))
        assert profile().gpu_available is False

    def test_headers_ending_in_a_colon_are_not_devices(self, probe):
        # `Devices:` and `VkPhysicalDeviceProperties:` both end in a colon and
        # both appear in real output. Counting them reports phantom GPUs.
        devices = hp._vulkan_devices(MIXED_HEADERS)
        assert len(devices) == 1, f"parsed {len(devices)} devices, expected 1"
        assert devices[0]["software"] is True

    def test_a_device_missing_its_name_is_still_counted_by_type(self, probe):
        # A device can omit `deviceName`; its `deviceType` still says what it
        # is, so dropping the record would lose the answer.
        nameless = "GPU0:\n\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU\n"
        devices = hp._vulkan_devices(nameless)
        assert len(devices) == 1
        assert devices[0]["software"] is False


# --------------------------------------------------------------------------
# The other two probes, and the states that are not the same as an answer
# --------------------------------------------------------------------------

class TestTheOtherProbes:

    def test_nvidia_still_wins_when_it_answers(self, probe):
        probe(nvidia=(0, "NVIDIA GeForce RTX 4090\n", ""), vulkan=(0, LLVMPILE_ONLY, ""))
        p = profile()
        assert p.gpu_type == "nvidia"
        assert p.gpu_available is True

    def test_a_missing_tool_is_not_a_gpu(self, probe):
        # Every probe raising is the ordinary case on a machine with no
        # vendor tools: three FileNotFoundErrors must not look like an answer.
        probe(missing=("nvidia-smi", "vulkaninfo", "rocm-smi"))
        p = profile()
        assert p.gpu_available is False
        assert p.gpu_type == "none"

    def test_a_tool_that_hangs_does_not_take_the_profile_down(self, probe):
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 10)
        import shani_chronoa.hardware_profile as m
        orig = m.subprocess.run
        m.subprocess.run = run
        try:
            p = profile()
        finally:
            m.subprocess.run = orig
        assert p.gpu_available is False


# --------------------------------------------------------------------------
# What the profile does with it
# --------------------------------------------------------------------------

class TestTheProfile:

    def test_software_rendering_picks_the_cpu_tier(self, probe):
        # The consequence that made this worth fixing: `get_model()` returns a
        # different model per tier, so a wrong tier is a wrong model.
        probe(vulkan=(0, LLVMPILE_ONLY, ""))
        p = profile()
        assert p.profile in ("medium", "low")
        assert p.get_model() != "qwen3:4b" or p.profile != "gpu"

    def test_a_real_gpu_picks_the_gpu_tier(self, probe):
        probe(vulkan=(0, HARDWARE_GPU, ""))
        assert profile().profile == "gpu"

    def test_the_gpu_name_is_exposed(self, probe):
        probe(vulkan=(0, HARDWARE_GPU, ""))
        assert "RTX" in profile().gpu_name