"""What this computer can run: CPU, memory and GPU, graded into a model tier.

Moved out of config.py (2026-10-02 structure review): settings and hardware
detection change for different reasons, and config.py had grown to both.
"""

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

#: How long any of the GPU probes may take. **Measured: `vulkaninfo --summary`
#: takes 971 ms on a real slot**, so 5 s is generous for it - but the old value
#: was a literal repeated at each call site, and the measured reason for one of
#: them belonging in a constant is that this pass found the *decision* wrong
#: rather than the timing.
_GPU_TIMEOUT = 10

#: The renderers that mean "Vulkan is emulating on the CPU". These are the
#: spellings Mesa actually emits - measured, `llvmpipe (LLVM 22.1.8, 256 bits)` -
#: and `PHYSICAL_DEVICE_TYPE_CPU` is the load-bearing signal, because a software
#: device is *typed* as a CPU by the spec regardless of what it is called.
_SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "swrast_dri")


def _vulkan_devices(summary: str) -> list:
    """Every device in `vulkaninfo --summary`, as `{name, software}`.

    **Both signals are read, because either alone is wrong.** The renderer name
    is free-form and version-stamped (`llvmpipe (LLVM 22.1.8, 256 bits)`), while
    `deviceType = PHYSICAL_DEVICE_TYPE_CPU` is what the Vulkan spec says. A
    device typed CPU is software whatever it is named; a device not typed CPU is
    hardware whatever it is called. Matching only the name misses a renamed
    fallback, and matching only the type misses a CPU-typed device whose name
    carries no hint.

    Parsed from the **captured** format, in which each device is a `GPU0:`
    header followed by indented `key = value` lines. A first version read whole
    lines and would pair `deviceName` with whichever device block happened to
    be current, which is right by accident until a device omits the key.
    """
    devices = []
    current = None
    for raw in (summary or "").splitlines():
        line = raw.strip()
        if line.endswith(":") and not line.startswith("Device"):
            # `GPU0:` / `GPU1:` - a device header. `Devices:` and
            # `VkPhysicalDeviceProperties:` end in a colon too, so only the
            # bare GPU-index form opens a new device.
            match = re.fullmatch(r"GPU(\d+):?", line)
            if match:
                current = {"name": "", "type": "", "software": False}
                devices.append(current)
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "deviceName":
            current["name"] = value
            # Both signals are **sticky-true**: either one marks the device
            # software, and neither can clear the other. `deviceType` is
            # authoritative when it says CPU (a device named "Mesa Intel(R) UHD
            # Graphics 620" is still emulated), and the name still catches a
            # renamed fallback whose type claims an integrated GPU. Both
            # asymmetries were found by a test rather than by reading: real
            # output prints `deviceType` **before** `deviceName`, so a
            # last-writer-wins flag let the name overrule the type.
            if any(marker in value.lower() for marker in _SOFTWARE_RENDERERS):
                current["software"] = True
        elif key == "deviceType":
            current["type"] = value
            if "TYPE_CPU" in value.upper():
                current["software"] = True
    return [d for d in devices if d["name"] or d["type"]]


#: Where the kernel describes the CPU. A module constant rather than a literal
#: inside `physical_cores`, so a test can point it at a synthetic tree and prove
#: the counting rule instead of only proving it runs against this machine.
CPU_SYSFS = "/sys/devices/system/cpu"


def physical_cores() -> int:
    """This machine's **physical** cores, not its threads.

    The reason this is a function and not `os.cpu_count()` is that llama.cpp's
    `-t` is the single biggest lever on CPU decode speed and the obvious value
    for it is the wrong one. Measured with `llama-bench` on this i7-1165G7
    (4 cores, 8 threads), `Qwen3-8B-Q4_K_M`, quiet box, `-r 2`:

    | `-t` | 3 | **4** | 5 | 6 | 8 |
    |---|---|---|---|---|---|
    | decode tok/s | 3.23 | **3.73** | 3.70 | 3.62 | 2.32 |
    | prompt tok/s | 16.2 | 19.7 | 18.8 | 19.9 | 19.0 |

    **`-t 8` is 1.61x slower than `-t 4`.** SMT siblings share execution ports,
    so a second thread per core adds contention and no throughput for a workload
    that is already bandwidth-bound. Prompt processing is far less sensitive,
    which is why this looked like a decode-only problem.

    Four launchers here passed `os.cpu_count() - 1` - **7 on this box** - and
    were throwing away that difference on every start. llama.cpp's own maintainer
    put it plainly in ggml-org/llama.cpp#34 ("speed with command line config -t
    4. If use -t 8, half speed"), and the upstream performance-tuning docs name
    incorrect thread settings as "the #1 cause of slow inference".

    Read from sysfs rather than `lscpu`, because `lscpu` is a binary a minimal
    image may not have and this repository answers from the kernel wherever it
    can (the same reason `skills/system_info.py` does). Falls back to
    `os.cpu_count()` when sysfs is unreadable, which is the old behaviour and no
    worse.
    """
    groups = set()
    try:
        for entry in os.listdir(CPU_SYSFS):
            if not entry.startswith("cpu") or not entry[3:].isdigit():
                continue
            with open(os.path.join(CPU_SYSFS, entry, "topology",
                                   "thread_siblings_list"), encoding="utf-8") as handle:
                groups.add(handle.read().strip())
    except OSError:
        groups = set()
    return len(groups) if groups else max(1, os.cpu_count() or 1)


class HardwareProfile:
    """Hardware detection and model selection."""

    def __init__(self) -> None:
        self.gpu_available: bool = False
        self.gpu_type: str = "none"
        #: What the probe actually reported, e.g. "Mesa Intel(R) UHD..." or
        #: "llvmpipe (LLVM 22.1.8, 256 bits)". Empty when nothing answered.
        self.gpu_name: str = ""
        self.ram_mb: int = 0
        self.cpu_cores: int = 0
        self.profile: str = "auto"
        self._detect()

    def _detect(self) -> None:
        """Detect hardware capabilities."""
        self._detect_ram()
        self._detect_cpu()
        self._detect_gpu()
        self._select_profile()

    def _detect_ram(self) -> None:
        """Detect total system RAM."""
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        self.ram_mb = int(line.split()[1]) // 1024
                        break
        except Exception:
            self.ram_mb = 4096  # Default fallback

    def _detect_cpu(self) -> None:
        """Detect CPU core count.

        **Physical cores, not threads** - `os.cpu_count()` on a machine with SMT
        reports twice the cores, and every llama.cpp launcher in this package
        wants the smaller number. See `physical_cores` for the measurement that
        decided it.
        """
        try:
            self.cpu_cores = physical_cores()
        except Exception:
            self.cpu_cores = 4

    def _detect_gpu(self) -> None:
        """Detect GPU availability."""
        try:
            # Check for NVIDIA
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=_GPU_TIMEOUT
            )
            if result.returncode == 0 and result.stdout.strip():
                self.gpu_available = True
                self.gpu_type = "nvidia"
                return
        except Exception:
            pass

        # **Vulkan: the exit code is not the answer - measured, and this was a
        # live defect.** On a real ShaniOS @blue slot with a perfectly good
        # Intel i915 adapter (`card1`, four DP connectors), `vulkaninfo
        # --summary` exits **0** and its one device is:
        #
        #     deviceName = llvmpipe (LLVM 22.1.8, 256 bits)
        #     deviceType = PHYSICAL_DEVICE_TYPE_CPU
        #
        # So asking "did vulkaninfo succeed?" declared a machine that renders
        # entirely in software to be `gpu_available = True, profile = "gpu"`,
        # which is what selects the larger model. The question is "is any
        # reported device an actual GPU", and the device lines say so.
        #
        # A software renderer is not a failure either - it is a GPU that is
        # being emulated - so it is recorded as its own type rather than
        # dropped, and the caller can tell the two apart.
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True, text=True, timeout=_GPU_TIMEOUT
            )
            if result.returncode == 0:
                devices = _vulkan_devices(result.stdout)
                hardware = [d for d in devices if not d["software"]]
                software = [d for d in devices if d["software"]]
                if hardware:
                    self.gpu_available = True
                    self.gpu_type = "vulkan"
                    self.gpu_name = hardware[0]["name"]
                    return
                if software:
                    # Emulated on the CPU: a real Vulkan stack, no real GPU.
                    self.gpu_available = False
                    self.gpu_type = "software"
                    self.gpu_name = software[0]["name"]
                    return
        except Exception:
            pass

        # Check for AMD
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True, text=True, timeout=_GPU_TIMEOUT
            )
            if result.returncode == 0:
                self.gpu_available = True
                self.gpu_type = "amd"
                return
        except Exception:
            pass

    def _select_profile(self) -> None:
        """Select hardware profile based on detected capabilities."""
        if self.gpu_available:
            self.profile = "gpu"
        elif self.ram_mb >= 16384 and self.cpu_cores >= 8:
            self.profile = "high"
        elif self.ram_mb >= 8192:
            self.profile = "medium"
        else:
            self.profile = "low"

    def get_model(self) -> str:
        """Get the smallest Ollama model that still does reliable tool-calling.

        Qwen3 is tool-call-trained at every dense size (unlike most small
        model families, which only call functions reliably at 7B+); 4B keeps
        argument formatting reliable while staying ~2.5GB on disk. 1.7B is
        used only on the lowest tier and may need bumping back to 4B via
        `--model=qwen3:4b` if tool calls come out malformed on real hardware.
        """
        if self.profile in ("gpu", "high", "medium"):
            return "qwen3:4b"
        else:
            return "qwen3:1.7b"

    def get_vision_model(self) -> str:
        """Get the vision model `senses/vision.py` describes images with.

        **Independent of `get_model()` on purpose.** The text pin is Qwen3,
        chosen for reliable tool-call argument formatting; a text model has no
        vision tower at all, so pointing the vision sense at it cannot work,
        and pointing the text LLM at a VLM makes every tool call worse. A user
        who sets `--model=` on the command line, or the `model` gsetting, has
        expressed a preference about *chat* and must not silently change what
        looks at their screen. The `vision-model` gsetting overrides this
        (see `ChronoaConfig.vision_model`), and only that.

        Qwen3-VL is the vision sibling of the text pin, so the family is at
        least familiar, and 2B is the smallest size that still reads a
        screenshot's small text rather than describing wallpaper. Tiers mirror
        `get_model()` exactly so the two are comparable at a glance.

        **Not verified live.** No Ollama server was reachable on the machine
        this was written on, so these tags have never been pulled or run -
        treat them as the intended default, not a tested one. If a tag is
        missing, `ollama pull qwen3-vl:2b` (or a `vision-model` override)
        resolves it, and a wrong tag surfaces as an Ollama error the sense
        reports verbatim rather than as a silently blank description.
        """
        if self.profile in ("gpu", "high"):
            return "qwen3-vl:8b"
        elif self.profile == "medium":
            return "qwen3-vl:4b"
        else:
            return "qwen3-vl:2b"

    def get_whisper_model(self) -> str:
        """Get the appropriate whisper model for hardware."""
        if self.profile in ("high", "gpu"):
            return "medium"
        elif self.profile == "medium":
            return "base"
        else:
            return "tiny"

    def get_context_window(self) -> int:
        """Get an Ollama context window (num_ctx) size for the hardware tier.

        Was a single hardcoded 2048 in ollama_llm.py regardless of hardware - too
        tight once the system prompt, all ~8 tool schemas, and a multi-turn
        tool-calling conversation are all in context at once.
        """
        if self.profile in ("gpu", "high"):
            return 8192
        elif self.profile == "medium":
            return 4096
        else:
            return 2048
