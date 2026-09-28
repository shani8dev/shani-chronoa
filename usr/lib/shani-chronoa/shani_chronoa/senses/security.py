"""Security posture: Secure Boot, TPM, LSMs, and kernel lockdown.

This is the one sense whose subject is whether the machine can be trusted, so
the failure mode to design against is the worst one: reporting a machine as
secure when the check did not run. Every field here therefore distinguishes
three states — on, off, and **could not be determined** — and a missing check is
never rounded up to "secure".

**The efivarfs trap, and it is a genuine one.** Secure Boot lives in a UEFI
variable, exposed through `/sys/firmware/efi/efivars/`. The file for
`SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c` is **5 bytes** on this
machine, and they are not the value:

    06 00 00 00 00
    ^^^^^^^^^^^^ the last byte is the boolean
    ^^^^ 4-byte size/attributes header that efivarfs prepends

A parser that does `open(path).read()[0]` gets `0x06` — the high byte of the
*attributes* field, which is `0x06` on essentially every system — and would
report Secure Boot **enabled on every machine ever built**. The value is the
final byte. A test asserts the offset explicitly and feeds the real 5-byte
content through, because this is the kind of bug that is invisible until it
matters.

**A missing efivarfs is not Secure Boot off.** The directory does not exist
when the machine booted in legacy BIOS mode, under a hypervisor that does not
pass EFI variables through, or in a container. All three are real, and all
three are "undetermined" rather than "off" — because a user who is told their
firmware security is off when the machine simply never had EFI is misled in
the other direction, just as badly.

**LSM order is significant and the raw list is not a strength score.** This
machine reports `lockdown,capability,landlock,yama,apparmor,ima,evm` — and
`lockdown` first, with `ima` and `evm` enabled. An LSM that appears in the list
is *configured*, not necessarily *enforcing*: an LSM can be listed and its LSM
in `active` but with no policy attached, in which case it does nothing. So the
sense reports the list and `active` as they are, without ranking them or
claiming enforcement it did not verify.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_EFIVARS = Path("/sys/firmware/efi/efivars")
_SECURITY = Path("/sys/kernel/security")

# The UEFI variable GUIDs, which are part of the specification and not
# guessable.
_SECURE_BOOT = "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
_SETUP_MODE = "SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c"
_LOCKDOWN = "LockdownMode-8be4df61-93ca-11d2-aa0d-00e098032b8c"


def _read_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _efi_bool(name: str) -> Optional[bool]:
    """A single-byte UEFI boolean, or None when efivarfs is not there.

    The value is the **last** byte: efivarfs prepends a 4-byte size/attributes
    header, and the real SecureBoot variable on this machine is the 5 bytes
    `06 00 00 00 00`. Reading byte 0 yields the attributes' high byte, which is
    `0x06` on every system and would report Secure Boot enabled everywhere.
    """
    raw = _read_bytes(_EFIVARS / name)
    if raw is None or len(raw) < 5:
        return None
    return bool(raw[-1])


def _efi_mode(name: str) -> Optional[int]:
    """A UEFI mode byte, read the same way."""
    raw = _read_bytes(_EFIVARS / name)
    if raw is None or len(raw) < 5:
        return None
    return raw[-1]


def read_secure_boot() -> dict:
    """Secure Boot state, and whether EFI variables exist at all."""
    exists = _EFIVARS.is_dir()
    record: Dict[str, object] = {"efivars_present": exists}
    if not exists:
        return record
    enabled = _efi_bool(_SECURE_BOOT)
    record["secure_boot"] = enabled if enabled is not None else "undetermined"
    setup = _efi_mode(_SETUP_MODE)
    if setup is not None:
        # SetupMode 1 means the firmware keys are not installed, so Secure Boot
        # cannot be enforcing whatever the SecureBoot variable says.
        record["setup_mode"] = setup == 1
    lockdown = _efi_mode(_LOCKDOWN)
    if lockdown is not None:
        record["lockdown_mode"] = lockdown
    return record


def read_lsms() -> dict:
    """Configured and active LSMs, kept as two lists rather than a score.

    An **empty** `lsm_active` is not the same as a missing one: empty means the
    kernel reports no LSM enforcing anything, which is the case worth knowing
    about, while missing means the interface is not there at all. Reading both
    through a truthiness test collapsed the two and lost the more important
    state, so each file is read on its own terms.
    """
    configured_path = _SECURITY / "lsm"
    active_path = None
    for name in ("lsm_active", "active"):
        candidate = _SECURITY / name
        if candidate.exists():
            active_path = candidate
            break
    record: Dict[str, object] = {}
    raw = _read_text(configured_path)
    if raw is not None:
        record["configured"] = [x for x in raw.split(",") if x]
    if active_path is not None:
        raw_active = _read_text(active_path) or ""
        record["active"] = [x for x in raw_active.split(",") if x]
    return record


def read_tpm() -> Optional[dict]:
    """TPM presence and version, if the kernel exposes it.

    `tpm2-tools` ships in the base image, so the tool could be used, but the
    sysfs fields answer the only question this sense asks — is there a TPM and
    what is it — with no subprocess and no parsing.
    """
    record: Dict[str, object] = {}
    version = _read_text(_SECURITY / "tpm0/tpm_version_major")
    if version is None:
        for candidate in sorted(_SECURITY.glob("tpm*/tpm_version_major")):
            version = _read_text(candidate)
            if version:
                record["device"] = candidate.parent.name
                break
    if version is None:
        return None
    record["version"] = version
    if "device" in record:
        description = _read_text(_SECURITY / record["device"] / "device/description")
        if description:
            record["description"] = description
    return record


def read_seccomp() -> Optional[str]:
    """Whether seccomp is actually enabled on this process's kernel.

    `/proc/self/status` reports the seccomp mode as a number: 0 disabled,
    1 strict, 2 filter. Reading it is the only way to tell "compiled in" from
    "in use", and the difference is the whole point for a security sense.
    """
    try:
        text = Path("/proc/self/status").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Seccomp":
            return value.strip()
    return None


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("security"):
        return f"Not reading security state: {config.sense_allowed_reason('security')}."

    boot = read_secure_boot()
    lsms = read_lsms()
    tpm = read_tpm()
    seccomp = read_seccomp()

    if not boot.get("efivars_present") and not lsms and tpm is None:
        return _SENSE.to_percept(
            "Nothing about this machine's firmware or kernel security could be "
            "read: no efivarfs, no LSM list, no TPM device. That is almost "
            "always a container or a virtual machine rather than a bare-metal "
            "machine with no security at all, and it is not the same as a "
            "machine whose security is off.",
            source="efivarfs+sysfs-security",
            metadata={"determined": False, "efivars": False},
        )

    lines = []
    if boot.get("efivars_present"):
        if boot.get("secure_boot") == "undetermined":
            lines.append("Secure Boot: undetermined - the variable exists but did not read")
        else:
            state = "enabled" if boot["secure_boot"] else "disabled"
            lines.append(f"Secure Boot: {state}")
        if boot.get("setup_mode") is True:
            lines.append(
                "  firmware SetupMode is 1: the platform keys are not installed, "
                "so Secure Boot is not enforcing regardless of the variable"
            )
        if "lockdown_mode" in boot:
            lines.append(f"  UEFI lockdown mode: {boot['lockdown_mode']}")
    else:
        lines.append(
            "Secure Boot: undetermined - this machine exposes no EFI variables, "
            "which means it booted without UEFI rather than that firmware "
            "security is off"
        )

    if tpm:
        label = f" ({tpm['device']})" if "device" in tpm else ""
        lines.append(f"TPM{label}: present, version {tpm['version']}")
    else:
        lines.append("TPM: none exposed by the kernel")

    if lsms.get("configured"):
        lines.append(
            f"LSMs configured: {', '.join(lsms['configured'])}"
        )
    if lsms.get("active"):
        lines.append(f"LSMs active: {', '.join(lsms['active'])}")
    if lsms.get("configured") and not lsms.get("active"):
        lines.append(
            "  no LSM is active: a configured LSM is not an enforcing one "
            "without a policy attached"
        )
    if seccomp:
        lines.append(f"seccomp mode for this process: {seccomp} (2 means filter)")

    lines.append(
        "these are the settings as reported, not a judgement about them: a "
        "configured LSM may have no policy, and no setting here is a security "
        "assessment of the machine."
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="efivarfs+sysfs-security",
        metadata={
            "determined": True,
            "efivars": bool(boot.get("efivars_present")),
            "secure_boot": boot.get("secure_boot"),
            "tpm": bool(tpm),
            "lsms_configured": len(lsms.get("configured", [])),
            "lsms_active": len(lsms.get("active", [])),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "security",
        "description": (
            "Read this machine's security posture: whether Secure Boot is "
            "enabled, whether the firmware is in SetupMode with its keys "
            "uninstalled, the UEFI lockdown mode, whether a TPM is present and "
            "what version, which Linux security modules are configured and "
            "which are actually active, and the seccomp mode. Reports Secure "
            "Boot as undetermined rather than disabled when the machine exposes "
            "no EFI variables, since that means it booted without UEFI rather "
            "than that firmware security is off. Reports the LSM lists as they "
            "are without ranking them, because a configured LSM with no policy "
            "attached does nothing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="security",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
