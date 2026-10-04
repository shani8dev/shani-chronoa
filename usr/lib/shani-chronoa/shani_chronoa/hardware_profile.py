"""What this computer can run: CPU, memory and GPU, graded into a model tier.

Moved out of config.py (2026-10-02 structure review): settings and hardware
detection change for different reasons, and config.py had grown to both.
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


class HardwareProfile:
    """Hardware detection and model selection."""

    def __init__(self) -> None:
        self.gpu_available: bool = False
        self.gpu_type: str = "none"
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
        """Detect CPU core count."""
        try:
            self.cpu_cores = os.cpu_count() or 4
        except Exception:
            self.cpu_cores = 4

    def _detect_gpu(self) -> None:
        """Detect GPU availability."""
        try:
            # Check for NVIDIA
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                self.gpu_available = True
                self.gpu_type = "nvidia"
                return
        except Exception:
            pass

        # Check for Vulkan
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.gpu_available = True
                self.gpu_type = "vulkan"
                return
        except Exception:
            pass

        # Check for AMD
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True, text=True, timeout=5
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
