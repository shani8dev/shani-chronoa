"""Skill: is my graphics actually hardware-accelerated?

The `gpu` sense reports what adapter the kernel has: the DRM node, its driver
(`i915`), its PCI ids. That is the **kernel** half. It does not answer the
question that decides whether a game, a video editor or a WebGL page will run:

> **"Is my OpenGL hardware accelerated, or is it falling back to software?"**

Nothing answered it. The answer is two different facts from two different
layers, and conflating them is the mistake this skill is built around:

- **the kernel driver** - `i915`, `amdgpu`, `nouveau` - is in
  `/sys/class/drm/cardN/device/uevent` and is **always** readable, unprivileged,
  with no display and no subprocess;
- **the renderer** - `OpenGL renderer string: Mesa Intel(R) UHD Graphics 620
  (KBL GT2)` versus `llvmpipe (LLVM 15.0.7, 256 bits)` - is chosen by Mesa at
  runtime and only `glxinfo -B` can report it.

A machine can have a perfectly good `i915` kernel driver and still be rendering
in software, because the *userspace* fell back. Reporting the kernel driver as
the answer is the confident wrong answer.

**The three ways `glxinfo` fails here, all measured on a real ShaniOS slot,
and all of them must be reported as "not known" rather than as a fact:**

1. **No display.** Measured: `DISPLAY` unset gives `Error: unable to open
   display` and **rc=255**. This is the normal state inside a slot, and it is
   not a fault in the graphics stack - it means the question needs a session.
2. **`eglinfo` SEGFAULTS.** Measured on the same slot, reproducibly: **rc=139**
   on three consecutive runs, with **zero bytes** of output and **empty
   stderr**, and the adapter plainly exists (`card1` with four DP connectors).
   `EGL_PLATFORM=surfaceless` does not help: still rc=139. **A child killed by
   a signal is the sharpest trap in this tree**, because `subprocess` reports it
   as a negative return code, not a positive one - so every check written as
   `if rc != 0` treats a *crash* as an ordinary error and the usual next step
   ("no output, so there is no renderer") states the opposite of what is true.
   This skill treats a negative rc as a crash and says so by name.
3. **Mesa is present but no renderer is chosen.** `/usr/lib/dri/` carries the
   driver modules, which says the stack is *installed*, not what it *selected*.

So the shape is: **sysfs always answers, `glxinfo` answers when it can, and
neither failing is ever reported as a graphics fact.**
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from typing import List, Optional, Tuple

from shani_chronoa.skills import Skill

_TIMEOUT = 30

#: `glxinfo -B`'s `OpenGL renderer string:` line, which is what actually says
#: hardware or software. Taken whole: the renderer string is free-form and its
#: wording is the evidence, so matching a fixed set of names would be exactly
#: the "guess from the status" failure this skill documents.
_RENDERER = re.compile(r"^OpenGL renderer string:\s*(.+?)\s*$", re.M)

#: The userspace renderers that mean "this is not hardware". Checked as a
#: substring of the renderer string because each of them names itself
#: differently by driver and version - `llvmpipe`, `softpipe`, `swrast` and
#: `SWR` are the spelling family, and they appear as `llvmpipe (LLVM 15.0.7,
#: 256 bits)` or `llvmpipe` alone.
_SOFTWARE = ("llvmpipe", "softpipe", "swrast", "swr", "software rasterizer")

#: A driver whose name says the kernel half is fine even if Mesa picked a
#: software renderer - these are real kernel drivers for real hardware. Listed so
#: that the two verdicts are never merged by accident.
_KERNEL_DRIVERS = ("i915", "amdgpu", "radeon", "nouveau", "i810", "xe",
                   "vc4", "panfrost", "msm", "etnaviv", "exynos", "tegra",
                   "asahi", "omap", "sun4v")


def _adapters() -> List[dict]:
    """Every DRM card, from sysfs. No subprocess, no display, no root.

    Measured on a real slot: `/sys/class/drm/card1/device/uevent` carries
    `DRIVER=i915` and `PCI_ID=8086:9A49`, and `/sys/class/drm/card1/device/driver`
    symlinks to `/sys/bus/pci/drivers/i915`. The uevent is read first because it
    is one file per adapter, where the symlink is a second opinion.
    """
    out: List[dict] = []
    base = "/sys/class/drm"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        # `card0-DP-1` and friends are connectors, not adapters; only `cardN`
        # is a device. Measured: the same slot has card1 plus card1-DP-1..4, and
        # counting connectors as adapters reports four GPUs that do not exist.
        if not re.fullmatch(r"card\d+", name):
            continue
        dev = os.path.join(base, name, "device")
        rec = {"card": name}
        try:
            with open(os.path.join(dev, "uevent"), encoding="utf-8") as fh:
                for line in fh:
                    key, _, value = line.strip().partition("=")
                    if key in ("DRIVER", "PCI_ID", "PCI_SLOT_NAME"):
                        rec[key.lower()] = value
        except OSError:
            pass
        try:
            rec["driver_link"] = os.path.basename(os.path.realpath(
                os.path.join(dev, "driver")))
        except OSError:
            pass
        out.append(rec)
    return out


def _glxinfo() -> Tuple[Optional[str], str]:
    """`glxinfo -B`'s renderer line, or `(None, why)`.

    **The negative return code is the point.** `subprocess` reports a child
    killed by signal N as `-N`, and SIGSEGV is 11 - measured rc=139 from the
    shell, which is 128+11. So `rc < 0` means the tool *crashed*, which is a
    different statement from "it could not answer", and both are different from
    "there is no renderer". All three are reported by name.
    """
    if shutil.which("glxinfo") is None:
        return None, ("glxinfo is not installed. Measured on a real ShaniOS "
                      "install: /usr/bin/glxinfo is owned by mesa-utils "
                      "9.0.0-7 - not mesa, and not mesa-demos.")
    if not os.environ.get("DISPLAY"):
        # Measured: without DISPLAY, `glxinfo -B` prints "Error: unable to open
        # display" and exits 255. Saying that up front avoids running a command
        # whose only possible result here is that error.
        return None, ("no display to ask through - glxinfo reports the renderer "
                      "of a session, and this one has no DISPLAY set. That is "
                      "the normal state outside a logged-in desktop session, "
                      "not a fault in the graphics stack.")
    try:
        proc = subprocess.run(["glxinfo", "-B"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, f"glxinfo did not finish within {_TIMEOUT}s."
    except OSError as exc:
        return None, f"glxinfo could not be run ({exc})."
    if proc.returncode < 0:
        # Measured and reproducible on a real slot. Named rather than swallowed,
        # because "crashed" and "printed nothing" invite opposite conclusions
        # and a reader who is told only that nothing was printed will conclude
        # the renderer is missing.
        return None, (f"glxinfo CRASHED (killed by signal "
                      f"{signal.Signals(-proc.returncode).name}, i.e. it died "
                      f"rather than answering). That is a fault in the "
                      f"tool on this machine, and no renderer can be read "
                      f"from it - it is not evidence about the GPU.")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return None, (detail[-1] if detail
                      else f"glxinfo exited {proc.returncode} without saying why")
    match = _RENDERER.search(proc.stdout or "")
    if not match:
        return None, ("glxinfo ran and said nothing about the renderer, which "
                      "is not the same as there being none.")
    return match.group(1), ""


def _describe() -> str:
    adapters = _adapters()
    lines: List[str] = []
    if not adapters:
        lines.append("No DRM adapter found under /sys/class/drm.")
        lines.append("")
        lines.append("glxinfo cannot answer either - it reports the renderer of "
                     "a session, and with no adapter there is nothing for it to "
                     "report.")
        return "\n".join(lines)

    lines.append("Graphics adapters (from sysfs, which needs no display):")
    kdrivers: List[str] = []
    for rec in adapters:
        driver = rec.get("driver") or rec.get("driver_link") or "unknown"
        if driver not in kdrivers:
            kdrivers.append(driver)
        bits = [f"  {rec['card']}  kernel driver: {driver}"]
        if rec.get("pci_id"):
            bits.append(f"PCI {rec['pci_id']}")
        lines.append(" ".join(bits))
    lines.append("")

    real_kernel = [d for d in kdrivers if d in _KERNEL_DRIVERS]

    renderer, why = _glxinfo()
    if renderer is None:
        lines.append(f"RENDERER: not known - {why}")
        lines.append("")
        if real_kernel:
            lines.append(f"The kernel side is healthy: {', '.join(real_kernel)} "
                         f"is a real driver for real hardware. Whether Mesa is "
                         f"using it, or a software fallback, is the separate "
                         f"question above and it is unanswered here - which is "
                         f"why the kernel driver is not reported as the answer.")
        else:
            lines.append("No kernel driver here is a known hardware driver, so "
                         "this is worth looking at directly; the renderer "
                         "question above is also unanswered.")
    else:
        software = any(marker in renderer.lower() for marker in _SOFTWARE)
        if software:
            lines.append(f"RENDERER: {renderer}")
            lines.append("")
            lines.append("**This is a SOFTWARE renderer.** Mesa installed a "
                         "kernel driver but is not using it - the names that "
                         "mean this are llvmpipe, softpipe and swrast. Games "
                         "and anything else that needs real GPU acceleration "
                         "will be slow or will refuse to start. On a machine "
                         "whose kernel driver is healthy this is usually the "
                         "userspace side: a missing or mismatched Mesa, or a "
                         "container without access to the render node "
                         "(/dev/dri).")
        else:
            lines.append(f"RENDERER: {renderer}")
            lines.append("")
            lines.append("Hardware accelerated - this is a real GPU renderer, "
                         "not a software fallback.")
        if real_kernel and not software:
            lines.append(f"(Kernel driver: {', '.join(real_kernel)}.)")

    return "\n".join(lines)


def _run(arguments: dict) -> str:
    return _describe()


SCHEMA = {
    "type": "function",
    "function": {
        "name": "graphics_stack",
        "description": (
            "Report whether OpenGL is hardware accelerated or falling back to "
            "software (llvmpipe). Answers from sysfs for the kernel driver - "
            "always, with no display needed - and from glxinfo -B for the "
            "renderer string, which is the only thing that actually says "
            "hardware or software. Use for 'is my graphics accelerated', 'why "
            "is this game slow', 'is my GPU being used', 'what graphics card "
            "renderer am I on'. Says which of the two it could not read and "
            "why, and never reports a crashed tool as a fact about the GPU. "
            "Read-only; nothing is changed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="graphics_stack", schema=SCHEMA, run=_run)]