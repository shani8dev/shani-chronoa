"""Graphics: which GPU, which driver, how much memory.

Two sources, and the order matters, because they disagree in a way that has
produced confident wrong answers before.

**`lspci` is the fallback, and it is not installed on a default image.**
`pciutils` ships in no profile. So the primary source is sysfs: the DRM driver
directory under `/sys/class/drm/card*/device/`, which needs no package and
answered on this machine:

    card1 -> /sys/class/drm/card1/device/uevent:
        DRIVER=i915
        PCI_CLASS=30000
        PCI_ID=8086:9A49

`lspci` is still used when present, because sysfs does not give a marketing
name — "Intel Corporation DG2" rather than "Arc A770" — while `lspci` does.
Neither is the consumer label and neither claims to be.

**VRAM is not a single number, and for an integrated GPU there is none.** The
same `/sys/class/drm/card*/device/mem_info_vram_total` that reports dedicated
VRAM on a discrete card reports 0 on integrated graphics, which share system
memory. Reporting "0 MiB of VRAM" for a machine whose GPU has no dedicated
memory at all is a confusing way of saying something true, so the sense says
which kind of adapter it found instead of printing a zero.

**A DRM card is not necessarily a GPU.** `/sys/class/drm` also carries
connectors (`card1-DP-1`, `card1-HDMI-A-1`) and, on some drivers, a virtual
or render node. Only the `cardN` entries are adapters, and the connector
entries are skipped by name rather than by guessing.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 600.0
_POLL_INTERVAL = 600.0

_DRM = Path("/sys/class/drm")
_TIMEOUT = 15
# `card1-DP-1` and friends are connectors, not adapters. Matching on the dash
# would also exclude any future adapter naming scheme with a suffix, so the
# card entries are matched positively instead.
_CARD_PREFIX = "card"


def _uevent(device: Path) -> Dict[str, str]:
    """Parse a `uevent` file, which is `KEY=value` per line."""
    raw = sysfs.read_text(device / "uevent")
    if not raw:
        return {}
    found = {}
    for line in raw.splitlines():
        key, _, value = line.partition("=")
        if key:
            found[key] = value.strip()
    return found


def _cards() -> List[str]:
    try:
        names = sorted(p.name for p in _DRM.iterdir() if p.is_symlink() or p.is_dir())
    except OSError as exc:
        logger.debug("cannot list /sys/class/drm: %s", exc)
        return []
    cards = []
    for name in names:
        if not name.startswith(_CARD_PREFIX):
            continue
        # `cardN` exactly: `card0-DP-1` starts with the prefix too.
        if not name[len(_CARD_PREFIX):].isdigit():
            continue
        cards.append(name)
    return cards


def _vram_bytes(device: Path) -> Optional[int]:
    for name in ("mem_info_vram_total", "mem_info_gtt_total"):
        value = sysfs.read_text(device / name)
        if value and value.isdigit():
            return int(value)
    return None


def read_gpus() -> List[dict]:
    """Every DRM adapter, from sysfs."""
    out = []
    for card in _cards():
        device = _DRM / card / "device"
        events = _uevent(device)
        record: Dict[str, object] = {"card": card}
        if events.get("DRIVER"):
            record["driver"] = events["DRIVER"]
        if events.get("PCI_ID"):
            # Vendor and device, which is what identifies the silicon exactly.
            record["pci_id"] = events["PCI_ID"]
        if events.get("PCI_CLASS"):
            record["pci_class"] = events["PCI_CLASS"]
        driver_link = device / "driver"
        if driver_link.exists():
            try:
                record["driver_module"] = os.path.basename(os.readlink(driver_link))
            except OSError:
                pass
        vram = _vram_bytes(device)
        if vram is not None:
            record["vram_bytes"] = vram
        out.append(record)
    return out


def _lspci() -> List[str]:
    """Human-readable names from `lspci`, when the tool exists.

    Optional on purpose: `pciutils` ships in no profile, so this enriches the
    answer where available and is simply absent elsewhere.
    """
    if shutil.which("lspci") is None:
        return []
    try:
        proc = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("lspci failed: %s", exc)
        return []
    names = []
    for line in (proc.stdout or "").splitlines():
        # "00:02.0 VGA compatible controller: Intel Corporation DG2 [Arc A770]"
        if any(tag in line for tag in ("VGA compatible", "3D controller",
                                       "Display controller")):
            names.append(line.split(": ", 1)[-1].strip())
    return names


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("gpu"):
        return f"Not reading graphics state: {config.sense_allowed_reason('gpu')}."

    gpus = read_gpus()
    if not gpus:
        if shutil.which("lspci") is None:
            return _SENSE.to_percept(
                "No graphics adapter was found under /sys/class/drm, and "
                "lspci is not installed to look further. Both facts are about "
                "what could be read; a machine with no display at all is a "
                "different thing from a machine whose adapter this could not "
                "see.",
                source="sysfs-drm",
                metadata={"adapters": 0, "lspci_present": False},
            )
        return _SENSE.to_percept(
            "No DRM adapter was found, though lspci is installed. A headless "
            "server or a machine whose driver does not use DRM both look like "
            "this.",
            source="sysfs-drm",
            metadata={"adapters": 0, "lspci_present": True},
        )

    names = _lspci()
    lines = []
    for index, gpu in enumerate(gpus):
        header = gpu.get("driver") or "no driver reported"
        if index < len(names):
            header = f"{names[index]} ({header})"
        lines.append(f"{gpu['card']}: {header}")
        if gpu.get("pci_id"):
            lines.append(f"  PCI id {gpu['pci_id']}")
        if "vram_bytes" in gpu:
            vram = gpu["vram_bytes"]
            if vram:
                lines.append(f"  {vram / 2**30:.1f} GiB dedicated video memory")
            else:
                # Integrated graphics share system memory and report zero, so
                # this must not be printed as a capacity.
                lines.append(
                    "  no dedicated video memory; this adapter shares system "
                    "memory with the processor"
                )
    if not names:
        lines.append(
            "lspci is not installed, so these are the kernel's own identifiers "
            "rather than the marketing names."
        )
    lines.append(f"{len(gpus)} graphics adapter(s)")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-drm",
        metadata={
            "adapters": len(gpus),
            "lspci_present": shutil.which("lspci") is not None,
            "drivers": [g.get("driver") for g in gpus if g.get("driver")],
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "gpu",
        "description": (
            "Read the graphics adapters on this machine: which cards the DRM "
            "subsystem exposes, the kernel driver each one is bound to, its PCI "
            "vendor and device id, and how much dedicated video memory it has. "
            "Uses the kernel's own sysfs, so it needs no extra package, and "
            "uses lspci for the marketing name when that is installed - which is "
            "optional, since pciutils ships in no profile. An adapter with no "
            "dedicated video memory is reported as sharing system memory rather "
            "than as having zero, because integrated graphics report zero VRAM "
            "while using system RAM instead."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="gpu",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
