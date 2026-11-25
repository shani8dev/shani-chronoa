"""Skill: which driver does each device use, and is a given driver loaded?

"Is the NVIDIA driver loaded?" and "what driver does my WiFi use?" are read
straight from the kernel: `/proc/modules` for what is loaded, and the
`device/driver` symlink under `/sys/class/<net|drm|sound|bluetooth>` for which
driver claimed each device. No `lsmod`, no `modinfo`, no `lspci` - none of which
is guaranteed on a minimal image - and no privileges.

Honesty rules: a module absent from `/proc/modules` may still be *built into*
the kernel, so "not loaded" is checked against `/sys/module/<name>` (which also
lists built-ins) before it is said; and a device with no driver link is reported
as having no driver bound, which is a real and useful state.
"""

from __future__ import annotations

import os
from pathlib import Path

from shani_chronoa.skills import Skill

_PROC_MODULES = Path("/proc/modules")
_SYS_MODULE = Path("/sys/module")
_SYS_CLASS = Path("/sys/class")
_CLASSES = (("net", "Network"), ("drm", "Graphics"), ("sound", "Sound"), ("bluetooth", "Bluetooth"))

SCHEMA = {
    "type": "function",
    "function": {
        "name": "driver_info",
        "description": (
            "Which kernel driver each network, graphics, sound and Bluetooth "
            "device uses, or - given a module name - whether that driver is "
            "loaded, built in, or absent, and its version. Use for 'is the "
            "nvidia driver loaded', 'what driver does my wifi use'. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "module": {"type": "string",
                           "description": "A driver/module name to check, e.g. 'nvidia', 'iwlwifi'."},
            },
        },
    },
}


def loaded_modules(path: Path = _PROC_MODULES) -> "dict[str, dict] | None":
    try:
        text = path.read_text()
    except OSError:
        return None
    mods = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            users = [u for u in parts[3].split(",") if u and u != "-"]
            mods[parts[0]] = {"size": int(parts[1]) if parts[1].isdigit() else 0, "used_by": users}
    return mods


def _read(path: Path) -> "str | None":
    try:
        return path.read_text().strip()
    except OSError:
        return None


def device_drivers(root: Path = _SYS_CLASS) -> "list[tuple[str, str, str | None]]":
    """(class label, device name, driver or None) for each device."""
    rows = []
    for cls, label in _CLASSES:
        base = root / cls
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if cls == "drm" and not (name.startswith("card") and name[4:].isdigit()):
                continue
            if cls == "sound" and not name.startswith("card"):
                continue
            if cls == "net" and name == "lo":
                continue
            device = entry / "device"
            if not device.exists():
                continue  # virtual (bridges, tun, veth): no hardware driver to report
            link = device / "driver"
            driver = os.path.basename(os.readlink(link)) if link.is_symlink() else None
            rows.append((label, name, driver))
    return rows


def check_module(name: str, mods: "dict | None", sys_module: Path = _SYS_MODULE) -> str:
    key = name.strip().replace("-", "_")
    if mods is not None and key in mods:
        info = mods[key]
        version = _read(sys_module / key / "version")
        users = ", ".join(info["used_by"]) or "nothing else"
        return (f"{key} is loaded ({info['size'] // 1024} KiB), used by {users}"
                + (f", version {version}" if version else "") + ".")
    if (sys_module / key).is_dir():
        return f"{key} is built into the kernel (it appears in /sys/module but is not a loadable module)."
    if mods is None:
        return f"Whether {key} is loaded is UNKNOWN: /proc/modules could not be read."
    near = sorted(m for m in mods if key in m or m in key)[:5]
    hint = f" Loaded modules with a similar name: {', '.join(near)}." if near else ""
    return f"{key} is not loaded and not built into this kernel.{hint}"


def _run(arguments: dict) -> str:
    mods = loaded_modules()
    name = (arguments.get("module") or "").strip()
    if name:
        return check_module(name, mods)
    lines = []
    rows = device_drivers()
    if not rows:
        lines.append("No hardware devices were found under /sys/class, so device drivers are UNKNOWN.")
    for label, dev, driver in rows:
        lines.append(f"  {label:<10} {dev:<16} {driver or 'no driver bound'}")
    if rows:
        lines.insert(0, "Devices and the driver each one uses:")
    lines.append(f"{len(mods)} kernel modules are loaded." if mods is not None
                 else "The loaded-module list could not be read.")
    return "\n".join(lines)


SKILLS = [Skill(name="driver_info", schema=SCHEMA, run=_run)]
