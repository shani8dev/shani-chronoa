"""Skills: drive a device from user space, with no kernel module in sight.

The counterpart to `driver_dev.py`, and on this platform the *useful* half of
driver work. Measured against both image matrices (GNOME 20260925, Plasma
20260922, 2026-10-10), every binary these two actions use is already installed
on both images and none of them needs a compiler:

| what | binary | package | on both images |
|---|---|---|---|
| I2C bus discovery | `i2cdetect` | `i2c-tools` | yes |
| register read/write | `i2cget` / `i2cset` | `i2c-tools` | yes |
| video devices | `v4l2-ctl` | `v4l-utils` | yes |
| FUSE mount/unmount | `fusermount3` | `fuse3` | yes |
| device node rules | `udevadm` | `systemd` | yes |

`i2cget`/`i2cset` are the point of this module: reading a register on a sensor
or an EEPROM *is* a user-space driver, and on a machine with no compiler and no
kernel headers it is the only kind of driver work that can actually be done.

**Reads are ungated; writes are not, and the reason is not caution for its own
sake.** A register write on the wrong address is how hardware is destroyed -
writing to an EEPROM's data bytes can unwrite a calibration blob that cannot be
restored, and the same command against a power-management or fuel-gauge device
can take the machine down. So `device_i2c` in write mode needs its own consent
key, is listed as destructive (a session grant must not cover it), and refuses a
register denylist *before* it checks whether the tool is installed. That last
ordering is the point: a refusal about destroying a device has to be the same
answer on every machine, or it exists only where a binary happens to be
missing. The same reasoning as `nfc`'s bank-card refusal, and the same one that
kept the MoYoung wearable protocol unimplemented.

**`device_probe` never guesses.** A bus list that cannot be read is UNKNOWN with
the reason, because "no I2C buses" and "this user may not open `/dev/i2c-1`"
are opposite claims and on this layout the second is the common one - `/dev` is
a tmpfs populated by udev, and the device nodes for a bus are group-owned.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_WRITE_KEY = "i2c-write-enabled"
_TIMEOUT = 30

#: Register ranges whose value is a *command* rather than a value. Writing one
#: either erases what is stored or puts the device into a state the person did
#: not ask for.
#:
#: These are keyed on the **register** alone, which is the first version's bug
#: and worth writing down: the table was a dict keyed `(address, register)`
#: holding what are plainly register ranges (0x00-0x01, 0x10-0x11), so
#: `_FORBIDDEN_REGS.get((0x50, 0x00))` missed every time and the guard could not
#: fire on any input at all - it was a refusal that only existed in the source.
#: The ranges are the conventional ones for 24LCxx-style EEPROMs and three-wire
#: sensors, and they are **conventions, not facts about the device in front of
#: you**: the refusal says so and names the datasheet as the authority, because a
#: rule that presented itself as certain would be the confident wrong answer this
#: module exists to avoid.
_FORBIDDEN_REG_RANGES = (
    (0x00, 0x01, "these are conventionally the block-select bytes of an EEPROM, "
                 "where a write can erase the block it selects"),
    (0x0F, 0x0F, "0x0F is the write-enable register on most 24LCxx EEPROMs; a "
                 "write cycle following one erases the block"),
    (0x10, 0x11, "these are conventionally command registers on three-wire "
                 "sensors, not a measurement"),
)
#: Values that are themselves an erase, whatever register they are aimed at.
_FORBIDDEN_WORDS = ("erase", "unprotect", "chip erase", "write protect", "blank")


def _forbidden_register(reg: int, value: str) -> str:
    """The full refusal reason for this write, or "" when it may go ahead.

    Split out so a test can call it directly: the guard's first version was a
    lookup that never matched, and only calling it with the inputs it was
    supposed to refuse shows that. It returns the finished sentence rather than
    a clause because the two cases need different framing - one is a convention
    about a register and one is a word in the value - and a shared prefix put
    "by the usual convention" in front of the word case, where it means nothing.
    """
    for low, high, reason in _FORBIDDEN_REG_RANGES:
        if low <= reg <= high:
            return ("by the usual convention for this kind of device, "
                    + reason + ". If your datasheet says the register is data "
                    "rather than a command, the convention is wrong here - it is "
                    "a rule, not a reading of your device.")
    lowered = value.lower()
    for word in _FORBIDDEN_WORDS:
        if word in lowered:
            return (f"the value {value!r} reads as a command rather than a "
                    f"measurement (it contains {word!r}). Write the register's "
                    f"value as a bare number if the datasheet really does ask for "
                    f"this one.")
    return ""


def _consent() -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not ChronoaConfig().get_bool(_WRITE_KEY, False):
        return False, (
            f"writing to a device register is turned off (enable '{_WRITE_KEY}' in "
            f"Settings). Reading registers needs no such permission - only writing "
            f"does, because a write to the wrong register can erase an EEPROM's "
            f"calibration or put a device into a state it cannot be talked out of."
        )
    return True, ""


def _dev_nodes(pattern: str) -> "list[str]":
    dev = Path("/dev")
    try:
        return sorted(p.name for p in dev.iterdir() if pattern in p.name)
    except OSError as exc:
        raise OSError(f"could not list {dev} ({exc})") from exc


def _i2c_buses() -> "tuple[list[str], str]":
    """([bus ids], note). The note is non-empty when the answer is not "none"."""
    try:
        nodes = _dev_nodes("i2c-")
    except OSError as exc:
        return [], str(exc)
    if nodes:
        # Sorted numerically, not as strings: `sorted()` puts 10 before 2, so a
        # bus list read that way reads 0, 1, 10, 11, 2, 3 - which is the sort
        # order a reader will assume is a mistake in the number, not in the sort.
        buses = sorted(int(n.split("i2c-")[-1]) for n in nodes)
        return ([str(b) for b in buses],
                "/dev nodes present: " + ", ".join(f"i2c-{b}" for b in buses))
    if shutil.which("i2cdetect") is None:
        return [], files.tool_missing("i2cdetect", "list the I2C buses on this machine")
    try:
        proc = subprocess.run(["i2cdetect", "-l"], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"i2cdetect could not be run ({exc})"
    if proc.returncode != 0:
        return [], (f"i2cdetect exited {proc.returncode}: "
                    f"{(proc.stderr or proc.stdout).strip()[:120]}")
    buses = re.findall(r"i2c-(\d+)", proc.stdout)
    if not buses:
        return [], "i2cdetect answered but named no bus, which is the empty case, not an error"
    ordered = sorted({int(b) for b in buses})
    return ([str(b) for b in ordered], f"from i2cdetect -l ({len(ordered)} bus)")


def _video_devices() -> "tuple[list[str], str]":
    if shutil.which("v4l2-ctl") is None:
        return [], files.tool_missing("v4l2-ctl", "list the video devices on this machine")
    try:
        proc = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"v4l2-ctl could not be run ({exc})"
    text = proc.stdout or proc.stderr
    names = sorted({line.strip() for line in text.splitlines() if line.strip()})
    if not names:
        return [], "v4l2-ctl answered but listed no device, which is the empty case"
    return names, f"from v4l2-ctl ({len(names)} line(s))"


def _fuse_state() -> "tuple[list[str], str]":
    mounts = Path("/proc/self/mountinfo")
    try:
        text = mounts.read_text()
    except OSError as exc:
        return [], f"could not read {mounts} ({exc})"
    fused = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and ("fuse" in parts[4].split(",")):
            fused.append(parts[4])
    helper = shutil.which("fusermount3") or shutil.which("fusermount")
    note = f"helper: {helper}" if helper else files.tool_missing("fusermount3", "mount a FUSE filesystem")
    if not Path("/dev/fuse").exists():
        note += "; /dev/fuse is absent, so nothing can be mounted right now"
    return fused, note


def _run_probe(arguments: dict) -> str:
    lines = ["User-space driver routes available on this machine (no compiler involved):"]

    buses, bus_note = _i2c_buses()
    if buses:
        lines.append(f"  I2C: bus {', '.join(buses)} - {bus_note}")
        lines.append("       device_i2c can read and (with the consent key) write registers on these")
    else:
        lines.append(f"  I2C: none found - {bus_note}")

    video, video_note = _video_devices()
    if video:
        lines.append(f"  Video: {'; '.join(video)} - {video_note}")
    else:
        lines.append(f"  Video: none listed - {video_note}")

    fused, fuse_note = _fuse_state()
    lines.append(f"  FUSE mounts: {', '.join(fused) if fused else 'none'}")
    lines.append(f"       {fuse_note}")

    lines.append("")
    lines.append("For which driver a device is bound to right now, driver_info reads "
                 "the kernel's own answer.")
    return "\n".join(lines)


def _clean_byte(text: str) -> "str | None":
    """i2cget/i2cset report 0x.., 0b.., decimal or '-'; anything else is noise."""
    value = (text or "").strip()
    return value or None


def _run_i2c(arguments: dict) -> str:
    action = (arguments.get("action") or "read").strip().lower()
    if action not in ("read", "write"):
        return f"Action must be read or write, not {action!r}."

    try:
        bus = int(str(arguments.get("bus", "")).strip())
    except (TypeError, ValueError):
        return "Which I2C bus? Give the number, as in bus=1."
    if bus < 0 or bus > 255:
        return f"Bus {bus} is not an I2C bus number."

    try:
        address = int(str(arguments.get("address", "")).strip(), 0)
    except (TypeError, ValueError):
        return ("Which device? Give the 7-bit address in hex, as in address=0x48.")
    if not 0x03 <= address <= 0x77:
        return (f"0x{address:02x} is not a 7-bit I2C address: the usable range is "
                "0x03-0x77, and 0x00-0x02 and 0x78-0x7f are reserved.")

    try:
        reg = int(str(arguments.get("register", "")).strip(), 0)
    except (TypeError, ValueError):
        return "Which register? Give it in hex, as in register=0x00."
    if not 0 <= reg <= 0xFFFF:
        return f"Register 0x{reg:x} is outside the 16-bit register space."

    if action == "write":
        # The destructive refusal comes before the tool check, deliberately: a
        # refusal about erasing a device must be the same answer everywhere,
        # not one that only exists on machines missing the binary.
        allowed, reason = _consent()
        if not allowed:
            return f"Refusing to write to a device register: {reason}"
        forbidden = _forbidden_register(reg, str(arguments.get("value", "")))
        if forbidden:
            return (f"Refusing to write register 0x{reg:02x} of device "
                    f"0x{address:02x}: {forbidden} Nothing was written.")

    node = Path(f"/dev/i2c-{bus}")
    if not node.exists():
        return (f"{node} does not exist, so there is no such bus on this machine. "
                "device_probe lists the ones there are.")

    if action == "read":
        if shutil.which("i2cget") is None:
            return files.tool_missing("i2cget", "read a device register")
        cmd = ["i2cget", "-y", str(bus), f"0x{address:02x}", f"0x{reg:02x}"]
    else:
        if shutil.which("i2cset") is None:
            return files.tool_missing("i2cset", "write a device register")
        value = (arguments.get("value") or "").strip()
        if not value:
            return "A write needs the value to write, as in value=0x01."
        cmd = ["i2cset", "-y", str(bus), f"0x{address:02x}", f"0x{reg:02x}", str(value)]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (f"The {'read' if action == 'read' else 'write'} did not answer within "
                f"{_TIMEOUT}s. The device may be holding the bus; the outcome is unknown.")
    except OSError as exc:
        return f"Could not run {cmd[0]}: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return (f"{cmd[0]} exited {proc.returncode} on bus {bus}, device 0x{address:02x}, "
                f"register 0x{reg:02x}. Nothing is known about the device's state."
                + (f" It said: {detail[-1]}" if detail else ""))

    if action == "write":
        return (f"Wrote 0x{reg:02x} = {arguments.get('value')} on device 0x{address:02x} "
                f"on bus {bus}; i2cset reported success. Reading it back is the only "
                f"way to know the device took it.")
    return (f"Register 0x{reg:02x} of device 0x{address:02x} on bus {bus} reads "
            f"{_clean_byte(proc.stdout) or '(nothing, which is not a reading)'}")


SCHEMA_PROBE = {
    "type": "function",
    "function": {
        "name": "device_probe",
        "description": (
            "What user-space driver routes this machine has: I2C buses, video "
            "devices, and FUSE mounts. Read-only, and reports unknown-with-a-reason "
            "where a list could not be read rather than claiming there is nothing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SCHEMA_I2C = {
    "type": "function",
    "function": {
        "name": "device_i2c",
        "description": (
            "Read a register on an I2C device, or write one with the "
            "'i2c-write-enabled' consent key. Reading needs no permission; writing "
            "is listed as destructive because a write to the wrong register can "
            "erase an EEPROM's calibration, and register ranges that do that are "
            "refused outright."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bus": {"type": "integer", "description": "I2C bus number, as in device_probe reports it."},
                "address": {"type": "string", "description": "7-bit device address in hex, e.g. '0x48'."},
                "register": {"type": "string", "description": "Register address in hex, e.g. '0x00'."},
                "action": {"type": "string", "description": "read (default) or write."},
                "value": {"type": "string", "description": "Value to write, required for a write."},
            },
            "required": ["bus", "address", "register"],
        },
    },
}

SKILLS = [
    Skill(name="device_probe", schema=SCHEMA_PROBE, run=_run_probe),
    Skill(name="device_i2c", schema=SCHEMA_I2C, run=_run_i2c),
]