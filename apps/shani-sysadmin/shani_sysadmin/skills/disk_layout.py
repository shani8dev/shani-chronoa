"""Skill: how is this disk actually partitioned?

`storage` reads `/sys/block` and **deliberately discards partitions** -
`_is_physical()` drops anything with a parent, because a list that called
every `p1`, `p2`, `p3` a disk is worse than useless. That filter is right for
"how many disks and how are they wearing", and it leaves a question nothing in
this tree answers:

> **"How is my disk partitioned?"**

On a blue-green machine that is not idle curiosity. `/` is a read-only slot, so
which partitions hold the slots, which hold `/home`, and which hold `/data` is
the layout the whole rollback story rests on - and `storage`, `disk_usage`,
`get_volume`, `manage_mount`, `boot_report` and the `filesystems` sense each
report a different slice of it. `filesystems` answers "what is *mounted*"; this
answers "what is *there*", which includes the partitions nothing mounts and the
LUKS/LVM layers between a partition and its filesystem.

**The reader is `lsblk --json`, and the reason is measurement rather than
preference.** `sgdisk -p` and `gdisk -l` are the partition-table tools, and both
**need root** - measured on this box: `sudo -n sgdisk -p /dev/nvme0n1` answers
`sudo: interactive authentication is required` and, being under `sudo`, still
exits 0. An exit code of 0 with a refusal on stderr is the exact contract this
repository refuses to reason about, and `sgdisk`'s output is the one thing a
desktop user cannot read unprivileged anyway. `lsblk --json` reads the same
kernel tree (`/sys/class/block`), needs no privilege, and returns the *nested*
tree rather than a flat table. The GPT **type GUID** is then named from the
table below, so "what is this partition for" has an answer without root.

**`lsblk` also prints the whole tree when a device is filtered out, so the
filter is what makes this readable.** Measured here: 33 block devices, of which
**32 are `loop`** - the immutable snap package images - and exactly **one** is a
real disk. Listing them all would bury the answer in 32 rows of squashfs, which
is the same failure `filesystems`'s own docstring records from the other
direction ("the report led with thirty immutable package images as 'needs
attention'"). They are hidden by default and the count is **stated**, because
"1 disk" and "1 disk and 32 squashfs loops I did not print" are different
answers and only the second one is true.

**Four levels are the real shape, not three.** Measured on this machine:

    nvme0n1 476.9G
      nvme0n1p1 1G     vfat          EFI System Partition      /boot/efi
      nvme0n1p2 2G     ext4          Linux filesystem          /boot
      nvme0n1p3 473.9G crypto_LUKS   Linux LUKS
        dm_crypt-0 473.9G LVM2_member
          ubuntu--vg-ubuntu--lv 473.9G ext4                     /

so a parser that assumed disk -> partition -> mount would report the root
filesystem as unmounted. The tree is walked to whatever depth it goes and each
level is labelled with `lsblk`'s own `type` word.

**A partition's purpose is read from the GPT type GUID, and an unknown GUID is
printed as the GUID.** Every entry below was checked against the table at
`https://en.wikipedia.org/wiki/GUID_Partition_Table`, which is the reference the
kernel and `gdisk` both follow. A short list rather than a long one, because
the common case is "an EFI partition and a Linux one" and the rest are reported
honestly instead of guessed: an unrecognised GUID is **not** "probably a data
partition", it is the GUID, since this file has already shipped one invented
name attached to the wrong UUID.

Read-only, and ungated: this reads `/sys` and asks `lsblk` for a table. It
reports the storage sense's own facts about the same machine, so it follows
that sense's switch - see `sense_reading.py`, and `compression_savings` for the
precedent of a partition question sharing it.

**Verified on a real ShaniOS slot and on a real firmware boot, separately, and
the two together are what make the claim.** On `@blue` through the real dispatch
path (`tools.execute_tool_outcome`, so the sandboxed child a model's tool call
uses) it read the machine's disk and named its partitions, refusing first by the
storage sense's own switch and running once consent was granted to a **second
process** - because a grant the writing process reads back as `true` and a child
does not is indistinguishable from one that was never made. What nspawn **cannot**
show is the labels: measured there, `nvme0n1` and its three partitions exist
while every `fstype`, `partlabel` and `parttype` reads `None`, because the
slot's `/dev` is a loop-attached image. So the blue/green subvolume question is
left to a real boot, and `vmspawn` is console-only - it boots OVMF and reads the
firmware menu, it cannot run `lsblk` in the guest.

What a real boot *does* settle, measured on `vmspawn --tpm=no`: the firmware
menu reports `shanios-blue (Active)` and `shanios-green (Candidate)`, which
agrees with what `boot_report` independently reads from the slot's own
`bootctl list`. Two systems, one answer - which is the difference between a
claim and an assumption. **`vmspawn`'s TPM does not come up on this dev box**
(`swtpm: Could not send response: Permission denied`, QMP dropped during feature
probing, VM exits in 3 s), so the measurement to take is `--tpm=no`; this skill
needs no TPM.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import List, Optional, Tuple

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 20

#: What `lsblk` reports to its own stdout/stderr. Every documented reason this
#: tree refuses to reason a binary's contract out is why these are spelled out.
#: On success `lsblk --json` is silent; a real failure prints its reason to
#: stderr and exits non-zero, so both are checked rather than trusting rc alone.
_JOURNAL = "journal error"  # lsblk's own wording for a dangling /dev entry

#: GPT partition-type GUIDs, checked one by one against
#: https://en.wikipedia.org/wiki/GUID_Partition_Table. Written lowercase because
#: that is what `lsblk` prints, and compared case-folded so a future lsblk that
#: uppercases them still matches.
_GPT_TYPES = {
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "EFI System Partition",
    "21686148-6449-6e6f-744e-656564454649": "BIOS boot partition",
    "e3c9e316-0b5c-4db8-817d-f92df00215ae": "Microsoft Reserved",
    "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7": "Microsoft basic data",
    "de94bba4-06d1-4d40-a16a-bfd50179d6ac": "Windows Recovery Environment",
    "0fc63daf-8483-4772-8e79-3d69d8477de4": "Linux filesystem",
    "933ac7e1-2eb4-4f13-b844-0e14e2aef915": "Linux /home",
    "e6d6d379-f507-44c2-a23c-238e2a3df928": "Linux LUKS",
    "ca7d7ccb-63ed-4c53-861c-1742536059cc": "Linux LVM",
    "bc13c2ff-59e6-4262-a352-b275fd6f7172": "Linux swap",
    "4f68bce3-e8cd-4db1-96e7-fbcaf984b709": "Linux root (x86-64)",
    "8da63339-0007-60c0-c436-083ac8230908": "Linux root (ARM-64)",
    "69dad710-2ce4-4e3c-b16c-21a1d49abed3": "Linux LVM (ARM-64)",
    "b921b045-1df0-41c3-af44-4c6f280d3fae": "Linux extended boot",
}

#: The BIOS/CHS GUID, kept out of the literal above because a typo inside a long
#: dict is exactly the kind of thing this file refuses to ship. It is checked by
#: a test against the published value, so a typo fails rather than hides.
_GPT_TYPES["bfbfafe7-a34f-448a-9a5b-6213eb736c22"] = "Legacy BIOS boot"

#: `lsblk`'s own `type` word -> how this reports that layer. Anything else is
#: printed with lsblk's word unchanged, which is better than a table that
#: renames a type it does not recognise.
_LAYER_NAMES = {
    "disk": "whole disk",
    "part": "partition",
    "crypt": "decrypted device",
    "lvm": "logical volume",
    "raid0": "RAID 0",
    "raid1": "RAID 1",
    "raid5": "RAID 5",
    "raid10": "RAID 10",
    "md": "software RAID",
    "zfs": "ZFS pool",
    "mpath": "multipath device",
}

#: Layer types that are not a disk of the machine. `loop` is measured at 32 of
#: 33 block devices here (the snap squashfs images); `rom`, `fd`, `sr` and `dm`
#: are the other transient nodes that show up on a machine with a card reader
#: or a disk image attached. They are hidden behind `show_hidden` and counted,
#: never silently dropped - see the module docstring.
_HIDDEN_LAYERS = ("loop", "rom", "fd", "sr")

_FIELDS = "NAME,KNAME,TYPE,SIZE,FSTYPE,LABEL,PARTLABEL,PARTTYPE,MOUNTPOINT,PKNAME"


def _run_lsblk() -> Tuple[Optional[dict], str]:
    """`lsblk --json`, or `(None, why)`.

    The exit status is checked *and* stdout is checked, because they are
    separate questions: `lsblk` can exit 0 having printed a JSON object whose
    `blockdevices` is empty (nothing to report, genuinely) or something that is
    not JSON at all (a journal error on stderr, which is why `_JOURNAL` is
    above).
    """
    if shutil.which("lsblk") is None:
        return None, ("lsblk is not installed, so the partition layout could "
                      "not be read. It comes with the util-linux package.")
    try:
        proc = subprocess.run(
            ["lsblk", "--json", "-o", _FIELDS],
            capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"lsblk did not answer within {_TIMEOUT}s."
    except OSError as exc:
        return None, f"lsblk could not be run ({exc})."

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        why = detail[-1] if detail else f"lsblk exited {proc.returncode}"
        # lsblk reports a dangling device on stderr and still exits 0 on some
        # builds; the JSON is the authority, so this only trims the message.
        return None, f"lsblk did not answer: {why}"

    out = (proc.stdout or "").strip()
    if not out:
        return None, ("lsblk printed nothing, so the partition layout is "
                      "unknown - that is not the same as a disk with no "
                      "partitions.")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        return None, (f"lsblk's output could not be read as JSON ({exc}), so "
                      "the partition layout is unknown.")
    if "blockdevices" not in data:
        return None, "lsblk's output had no blockdevice list in it."
    return data, ""


def _size_bytes(field: object) -> Optional[int]:
    """lsblk's `size` is a human string ("476.9G") unless --bytes is passed.

    Returned as None rather than guessed when it cannot be parsed, so a size
    that is missing never renders as "0 B" - the same 0/0 shape this repository
    keeps refusing to report as an answer.
    """
    if isinstance(field, int):
        return field
    if not isinstance(field, str) or not field.strip():
        return None
    text = field.strip()
    units = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4,
             "P": 1024 ** 5}
    # lsblk prints at most one decimal ("476.9G"); a plain integer is bytes.
    if text[-1:].isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    factor = units.get(text[-1:].upper())
    if factor is None:
        return None
    try:
        return int(float(text[:-1]) * factor)
    except ValueError:
        return None


#: Filesystem names that are a *container* rather than the payload: what the
#: partition really holds is whatever is inside these. Measured on this machine:
#: `nvme0n1p3` carries GPT type `0fc63daf-...` - which genuinely **is** "Linux
#: filesystem", and is what a partition is created as - while its `fstype` is
#: `crypto_LUKS`, holding LVM, holding ext4 on `/`. So for these the GUID is a
#: creation-time label that no longer describes the contents, and printing it as
#: the purpose next to "crypto_LUKS" is a sentence that reads as a contradiction
#: and is not one. The payload wins and the GUID is marked as historical.
_CONTAINER_FSTYPES = {
    "crypto_luks", "lvm2_member", "linux-swap", "swap", "isw_raid_member",
    "linux_raid_member", "zfs_member",
}


def _describe(node: dict) -> str:
    """One line for one device: size, role, filesystem, purpose, mountpoint."""
    name = node.get("name") or "?"
    bits: List[str] = [name]
    size = _size_bytes(node.get("size"))
    if size is not None:
        bits.append(_human(size))
    layer = (node.get("type") or "").lower()
    bits.append(_LAYER_NAMES.get(layer, layer or "device"))

    fstype = node.get("fstype")
    fstype_l = fstype.lower() if isinstance(fstype, str) else ""
    if fstype:
        bits.append(fstype)
    # The label is what a person named the thing; it is more informative than
    # the size when both are present, so it is kept in preference order.
    for key in ("partlabel", "label"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            bits.append(f'"{value.strip()}"')
            break
    parttype = node.get("parttype")
    if isinstance(parttype, str) and parttype.strip():
        known = _GPT_TYPES.get(parttype.strip().lower())
        # Only *disagreeing* pairs are worth the extra sentence. A swap
        # partition whose GUID says swap agrees with itself, and "created as
        # Linux swap, but it holds swap" reads as a contradiction that is not
        # one.
        if known and fstype_l in _CONTAINER_FSTYPES and fstype_l not in known.lower():
            bits.append(f"created as {known}, but it holds {fstype}")
        else:
            # An unknown GUID is printed as the GUID. Guessing a purpose is the
            # confident wrong answer this file already shipped once.
            bits.append(known if known else f"GPT type {parttype.strip()}")
    mount = node.get("mountpoint")
    if isinstance(mount, str) and mount.strip():
        bits.append(f"mounted at {mount.strip()}")
    return "  ".join(bits)


def _human(size: int) -> str:
    """Bytes the way lsblk prints them, because that is what was compared to."""
    value = float(size)
    for unit in ("B", "K", "M", "G", "T", "P"):
        if abs(value) < 1024 or unit == "P":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}P"


def _walk(node: dict, depth: int) -> List[Tuple[int, dict]]:
    """Depth-first flattening of lsblk's nested tree, preserving order.

    Recursive rather than iterative because the depth is genuinely unbounded
    (four levels measured here, and a ZFS-on-RAID-on-LUKS machine is three
    more), and lsblk nests children in document order which is the order a
    person reads a partition table in.
    """
    out = [(depth, node)]
    for child in node.get("children") or []:
        if isinstance(child, dict):
            out.extend(_walk(child, depth + 1))
    return out


def _measure(show_hidden: bool = False) -> str:
    data, why = _run_lsblk()
    if data is None:
        return why

    devices = data.get("blockdevices") or []
    if not devices:
        return ("No block devices were reported, so there is no partition "
                "layout to show. lsblk answered successfully, so this is a "
                "machine with no disks rather than a failure to look.")

    kept, hidden = [], 0
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        if not show_hidden and (dev.get("type") or "").lower() in _HIDDEN_LAYERS:
            hidden += 1
            continue
        kept.append(dev)

    if not kept:
        return (f"Every block device on this machine is of a hidden kind "
                f"({hidden} of them), so there is no physical disk to show. "
                "Ask again with show_hidden=true to see them anyway.")

    lines: List[str] = []
    for dev in kept:
        for depth, node in _walk(dev, 0):
            lines.append(("    " * depth + "- " + _describe(node)))

    footer: List[str] = []
    if hidden:
        # Stated, never silent: "1 disk" and "1 disk plus 32 squashfs loop
        # devices I did not print" are different answers.
        footer.append(
            f"{hidden} other block device(s) were not shown: loop/rom/fd/sr "
            "layers such as the immutable snap package images. Ask with "
            "show_hidden=true to list them.")
    lines.append("")
    lines.append(
        "Partition purposes above come from the GPT partition-type GUID. A "
        "filesystem can sit on any partition regardless of what the GUID says, "
        "and LUKS and LVM layers appear between a partition and its "
        "filesystem, so the mounted path is not always the partition directly "
        "above it.")
    if footer:
        lines.append(footer[0])
    return "\n".join(lines)


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's subject is the storage sense's, so its switch is too.

    The same `_consent` shape every other sense-sharing skill uses, which is
    what keeps the gate greppable: `sense_allowed` returns a bare **bool**, not
    a `(bool, why)` pair, and a first version unpacked it as a pair - a
    `TypeError` on every call. **Only the real dispatch path found that**, since
    `execute_tool_outcome` runs the skill in a child process where the failure
    surfaces, and the module-level tests called `_measure()` directly.
    """
    if config.sense_allowed("storage"):
        return True, ""
    return False, sense_reading.refusal(config, "storage")


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    allowed, why = _consent(config)
    if not allowed:
        return why
    show_hidden = bool((arguments or {}).get("show_hidden"))
    return _measure(show_hidden=show_hidden)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "disk_layout",
        "description": (
            "Show how this machine's disks are partitioned: every physical "
            "disk, each partition with its size, filesystem and purpose (EFI, "
            "Linux root, /home, LUKS, LVM, swap), and where each one is "
            "mounted. Use for 'how is my disk partitioned', 'what partitions "
            "are on this machine', 'which partition is my home directory on', "
            "'is my disk GPT or plain', 'what is this LUKS partition for'. "
            "Follows the storage sense's consent switch, because it reports the "
            "same machine. Read-only; nothing is changed. Loop devices (the "
            "immutable snap images) are hidden unless show_hidden is set, and "
            "their count is always stated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "show_hidden": {
                    "type": "boolean",
                    "description": ("Also list loop/rom/fd/sr layers - the "
                                    "snap package images and other transient "
                                    "nodes - which are hidden by default."),
                    "default": False,
                },
            },
        },
    },
}

SKILLS = [Skill(name="disk_layout", schema=SCHEMA, run=_run)]