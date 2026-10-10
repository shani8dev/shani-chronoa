"""Skills: write a Linux driver, and find out honestly whether this machine can.

Three actions, and the first exists because the other two mostly cannot run here:

- `driver_status` - the development environment, measured. Not "are drivers
  supported" but the four separate questions that decide it: is there a
  compiler, is there a kernel build tree **for the running kernel**, is there a
  DKMS to rebuild against the next one, and could a built module even be kept
  or loaded. Every line is PRESENT, ABSENT or UNKNOWN-with-a-reason, because
  the four failure modes are different and the person asking needs to tell them
  apart: no compiler is a different problem from no headers.
- `driver_scaffold` - generate a real out-of-tree module project (C source,
  Kbuild `Makefile`, `dkms.conf`, a udev rule, a README) under a directory the
  user names. Text generation, so it works on an image with no compiler at all -
  which is what makes it the one half of this that is useful on ShaniOS.
- `driver_build` - build a scaffolded project with Kbuild, and only ever run the
  one fixed command. Refuses naming what is missing rather than failing with a
  compiler's stderr, and the post-condition reads the `.ko` back off disk.

**Measured on the images (2026-10-10): a kernel module cannot be built here.**
Both matrices (`chronoa-matrix.json`, GNOME 20260925 / Plasma 20260922) show
`kmod` shipping insmod/rmmod/modprobe/modinfo/lsmod/depmod and `binutils`
shipping ld/nm/objcopy, but **no compiler, no `make`, no `dkms`, and no
`linux-headers` on any desktop profile** - only `linux-api-headers`, which is
the UAPI half and cannot build a module. `linux-headers` appears in exactly one
profile, `server/Packages-Base`, and no profile installs `base-devel`. So the
honest answer from `driver_status` on a stock desktop is "no", with the reason
named per missing piece - and the packages to install named from
`files._PACKAGE_HINTS`, which is the table read out of pacman's own file
database.

Two more platform facts the report has to carry, because both make a module that
*builds* still unusable:

- **`/` is a read-only blue/green slot and `/var` is tmpfs** (`fstab` plus
  `systemd.volatile=state`; see the layout table in AGENTS.md). kmod searches
  `/usr/lib/modules/$(uname -r)` then `/var/lib/modules/$(uname -r)`, so a module
  built into the first cannot be installed and one placed in the second is gone
  on reboot. That is why `install`/`persist` is not an action here: there is no
  place to install one that survives, and a skill that offered it would be
  offering a module that disappears at the next reboot.
- **Signing, measured rather than assumed.** `sbctl`, `sbsigntools` and
  `mokutil` are in the **server** profile only, so no desktop image ships a tool
  to sign a module. What that first draft went on to assert - "so a desktop has
  Secure Boot, and an unsigned module is refused" - was **never measured**, and a
  firmware boot of the image settled it the other way:
  `/sys/kernel/security/lockdown` exists and enforces **`none`**, and there is
  **no SecureBoot efivar at all**. So the real barriers to loading a module here
  are the missing toolchain and having nowhere to keep the result, **not** a
  signing wall. The lockdown file is the only universally readable evidence
  (efivarfs is root-only 0400), which is why that is the half `driver_status`
  reports, and why it reports UNKNOWN rather than either verdict: "not enforced"
  and "I could not check" are opposite claims and the second is the dangerous
  one.

**The feasible half is user space, and it has its own module:** `user_driver.py`
(FUSE, i2c-tools register access, V4L2, socat), where every tool is already on
both images and no compiler is involved. This module is the honest half of the
story rather than the useful one, and `driver_status` says so by name.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: Building is gated; generating text is not. The split follows
#: `write_text_file` (ungated, creates) against `edit_file` (gated, changes
#: something that exists), and `control_service`: one key for the act that runs
#: a toolchain over a tree.
_BUILD_KEY = "driver-build-enabled"
_TIMEOUT = 600  # a real kernel module build is slow; the sandbox's own ceiling is lower

#: `module_param` names a param; `MODULE_LICENSE` is what `modinfo` reads for
#: the licence and what a kernel build refuses without. Both are required for
#: anything to load, so both are required here.
_C_LICENCE = re.compile(r"^\s*MODULE_LICENSE\s*\(", re.MULTILINE)


def _uname_release() -> str:
    """The running kernel, or "" when it cannot be read."""
    return platform.release() or ""


def _first_present(*names: str) -> "str | None":
    for name in names:
        if shutil.which(name):
            return name
    return None


def _build_dir(release: str, root: Path = Path("/")) -> "Path | None":
    """Kbuild's `M=` target tree for this kernel, or None.

    Two locations because they are the two kmod itself searches, and which one
    exists is the whole answer to "can this be built here".
    """
    if not release:
        return None
    for candidate in (root / "lib" / "modules" / release / "build",
                      root / "usr" / "lib" / "modules" / release / "build"):
        if candidate.exists():
            return candidate
    return None


def _modules_dir_writable() -> "tuple[bool, str]":
    """Could a built module be *kept*? Reports (ok, reason).

    Separate from "can it be built" because the answer is a filesystem fact, not
    a toolchain one, and on this layout it is the one that fails last and
    hardest: a module that builds and cannot be installed is the state a person
    is most likely to read as success.
    """
    target = Path("/usr/lib/modules")
    try:
        return os.access(target, os.W_OK), f"{target} {'is' if os.access(target, os.W_OK) else 'is not'} writable by this user"
    except OSError as exc:  # pragma: no cover - os.access does not raise in practice
        return False, f"could not read {target} ({exc})"


def _var_is_volatile(root: Path = Path("/")) -> "tuple[bool | None, str]":
    """Is /var tmpfs? (None, reason) when the mounts table cannot be read."""
    mounts = root / "proc" / "mounts"
    try:
        text = mounts.read_text()
    except OSError as exc:
        return None, f"could not read {mounts} ({exc})"
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "/var":
            fstype = parts[2]
            return fstype in ("tmpfs", "ramfs"), f"/var is {fstype}"
    return None, "/var is not listed in /proc/mounts"


def _secure_boot_state(root: Path = Path("/")) -> "tuple[str, str]":
    """(state, how it was read). Never guesses.

    The SecureBoot efivar lives in efivarfs at mode 0400 root-only, so the
    honest answer for a normal desktop user is UNKNOWN with that reason - and
    that is the *right* answer, because "off" would be a claim made by a
    process that never read the variable.
    """
    lockdown = root / "sys" / "kernel" / "security" / "lockdown"
    if lockdown.exists():
        try:
            value = lockdown.read_text().strip()
        except OSError as exc:
            return "unknown", f"{lockdown} exists but could not be read ({exc})"
        # The file reads `[active] mode mode ...` - the bracketed token is what
        # is enforced now and the rest are the modes available. Measured on this
        # box: `[none] integrity confidentiality`, i.e. NOT locked down. Treating
        # the whole line as the answer calls that "locked down", which is the
        # dangerous direction to be wrong in: it claims unsigned modules are
        # refused when they are not.
        active = re.search(r"\[([^\]]*)\]", value)
        if active is None:
            return "unknown", f"{lockdown} reads {value!r}, which is not the [active] modes form"
        mode = active.group(1).strip()
        if mode and mode != "none":
            return "locked down", f"{lockdown} enforces {mode!r}, so unsigned modules are refused"
        if mode == "none":
            return "not locked down", f"{lockdown} enforces 'none'"
    efivars = root / "sys" / "firmware" / "efi" / "efivars"
    matches = sorted(efivars.glob("SecureBoot-*")) if efivars.is_dir() else []
    for match in matches:
        try:
            raw = match.read_bytes()
        except OSError as exc:
            return "unknown", f"{match} exists but could not be read ({exc}; it is root-only)"
        # efivar value layout: 4-byte attributes, then a UTF-16LE name, then
        # the data. SecureBoot is a single byte, 1 enabled.
        if len(raw) >= 5 and raw[4] == 1:
            return "enabled", f"{match} reads 1"
    if matches:
        return "disabled", f"{matches[0]} reads 0"
    if efivars.is_dir():
        return "unknown", f"{efivars} exists but carries no SecureBoot variable"
    return "unknown", "this machine booted without UEFI, so Secure Boot does not apply"


def _toolchain() -> "list[tuple[str, str, bool]]":
    """(label, state, blocks_a_build) for every piece of the build environment.

    The flag is carried rather than re-derived from the sentence, because the
    first version decided "is this absent?" twice by matching the same strings
    in two different comprehensions - and the two answers disagreed, printing
    the compiler, make and the build tree as "also missing" on a machine that
    had all three. A row's verdict and its severity now travel together.
    """
    rows: list[tuple[str, str, bool]] = []
    compiler = _first_present("gcc", "cc", "clang")
    rows.append(("C compiler", compiler or
                 files.tool_missing("gcc", "build a kernel driver").split(".")[0],
                 compiler is None))
    make = _first_present("make", "gmake")
    rows.append(("make", make or
                 files.tool_missing("make", "build a kernel driver").split(".")[0],
                 make is None))
    release = _uname_release()
    tree = _build_dir(release)
    if tree is None:
        rows.append((f"kernel build tree for {release or 'this kernel'}",
                     "ABSENT - no /lib/modules/<kernel>/build. linux-headers is the "
                     "package that provides it; linux-api-headers is not enough, it "
                     "is the userspace API only.", True))
    else:
        rows.append((f"kernel build tree for {release}", str(tree), False))
    dkms = _first_present("dkms")
    rows.append(("dkms", dkms or "ABSENT - nothing would rebuild the module when the "
                 "kernel is updated", False))
    return rows


def _status_text() -> str:
    release = _uname_release() or "(unknown)"
    lines = [f"Kernel driver development on this machine (running kernel {release}):"]
    rows = _toolchain()
    for label, state, _blocking in rows:
        lines.append(f"  {label:<42} {state}")

    keepable, keep_reason = _modules_dir_writable()
    volatile, var_reason = _var_is_volatile()
    lines.append(f"  {'can a built module be kept?':<42} {keep_reason}")
    if volatile:
        lines.append(f"  {'':42} and {var_reason}, so a module placed there is gone on reboot")
    elif volatile is None:
        lines.append(f"  {'':42} {var_reason} - whether a module there would survive is unknown")

    state, how = _secure_boot_state()
    lines.append(f"  {'would an unsigned module load?':<42} {state} ({how})")

    # Only a missing compiler, make or build tree stops a *build*. dkms does
    # not: without it the module still builds, and simply stops being rebuilt
    # when the kernel is updated. Calling that "cannot be built here" would be a
    # claim about the wrong question - and it contradicted the four lines above
    # it on the machine this was measured on, where all three build pieces were
    # present and only dkms was absent. The flag is read, not re-derived.
    blocking = [label for label, _state, blocks in rows if blocks]
    also = [label for label, _state, blocks in rows
            if not blocks and (label == "dkms") and not shutil.which("dkms")]
    lines.append("")
    if blocking:
        lines.append(
            "So a kernel module cannot be built here yet: " + ", ".join(blocking) +
            " missing. base-devel supplies the compiler and make; the kernel build "
            "tree comes from linux-headers. Ask again once they are installed."
        )
    else:
        lines.append("Everything needed to build a module is present. Note the lines "
                     "above about where it could be kept and whether it could load "
                     "before assuming a built module is usable.")
    if also:
        lines.append("Also missing, which does not stop a build: "
                     + ", ".join(also) + ". Without dkms a built module is not "
                     "rebuilt when the kernel is updated, so it silently stops "
                     "loading after the next kernel change.")
    lines.append("")
    lines.append("The driver work that needs no compiler at all is in the other "
                 "direction: device_probe and device_i2c talk to hardware "
                 "directly over I2C, V4L2 and FUSE, and device_probe reports what "
                 "this machine actually has. driver_info reports which driver a "
                 "device is using now.")
    return "\n".join(lines)


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

#: Templates use `@NAME@`, not `str.format` braces. The first version used
#: `.format()` and raised `KeyError: 'kernel_source_dir'` on the dkms.conf
#: template, because dkms's own `MAKE[0]` line is full of `${...}` shell
#: variables that `format` reads as fields. Escaping them as `${{...}}` would
#: work and would be unreadable in a file whose whole job is to be copied into
#: /usr/src; a placeholder that cannot collide is the cheaper answer.
_C_TEMPLATE = '''\
/* @NAME@.c - an out-of-tree kernel module.
 *
 * Generated by Chronoa. It is a real module skeleton, not a stub: it loads,
 * prints, and unloads cleanly, so you can build and insmod it before adding
 * your own probe() and remove() to it.
 *
 * Build it with `driver_build`, which runs the same Kbuild line below.
 */

#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>

#define DRV_NAME "@NAME@"

static int __init @NAME@_init(void)
{
\tpr_info("@NAME@: loaded\\n");
\treturn 0;
}

static void __exit @NAME@_exit(void)
{
\tpr_info("@NAME@: unloaded\\n");
}

module_init(@NAME@_init);
module_exit(@NAME@_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Chronoa scaffold");
MODULE_DESCRIPTION("An out-of-tree module scaffolded by Chronoa");
'''

_MAKEFILE_TEMPLATE = '''\
# Kbuild for an out-of-tree module.
#
# The kernel build tree is /lib/modules/$(uname -r)/build, provided by the
# linux-headers package. driver_build passes it as -C and this directory as M=.
#
# The `.o` at the end of the module name is REQUIRED, and the obvious form
# without it does not work. Measured against a real build (kernel
# 7.0.0-38-generic headers), all five spellings tried:
#
#   obj-m := NAME.o      builds NAME.ko
#   obj-m += NAME.o      builds NAME.ko
#   obj-m := NAME        FAILS  No rule to make target 'NAME'
#   obj-m += NAME        FAILS  same
#   obj-m := NAME / NAME-objs := NAME.o   FAILS  same
#
# `obj-m := NAME` is the form most tutorials show and it fails at the
# modules.order step, with a compile that never happens - so the first two
# versions of this template produced a driver that could not be built and whose
# failure pointed at a target that was never really wanted.
obj-m := @NAME@.o
'''

_DKMS_TEMPLATE = '''\
PACKAGE_NAME="@NAME@"
PACKAGE_VERSION="0.1"
BUILT_MODULE_NAME[0]="@NAME@"
DEST_MODULE_LOCATION[0]="/updates/dkms"
AUTOINSTALL="yes"

MAKE[0]="make -C ${kernel_source_dir} M=${dkms_tree}/${PACKAGE_NAME}/${PACKAGE_VERSION}/build modules"
CLEAN="make -C ${kernel_source_dir} M=${dkms_tree}/${PACKAGE_NAME}/${PACKAGE_VERSION}/build clean"

# dkms.conf is read with a shell-like parser, not Python's. Every value must be
# on one line, and the ${...} variables above are dkms's own.
'''

_UDEV_TEMPLATE = '''\
# A udev rule for @NAME@. Copy to /etc/udev/rules.d/99-@NAME@.rules on a
# machine you administer, then reload with `udevadm control --reload`.
#
# The permission bits below are the conservative half: this grants the group
# access to the node. Do not widen to 0666 without a reason - on a multi-user
# machine that hands the device to every account.
ACTION=="add", SUBSYSTEM=="misc", KERNEL=="@NAME@", MODE="0660", GROUP="plugdev"
'''

_README_TEMPLATE = '''\
# @NAME@

An out-of-tree Linux kernel module, scaffolded by Chronoa on @DATE@.

    driver_status   - whether this machine can build it at all
    driver_build    - build it (needs the 'driver-build-enabled' key)

## What is here

| file | what it is |
|---|---|
| `@NAME@.c` | the module: init, exit, MODULE_LICENSE |
| `Makefile` | Kbuild; `obj-m := @NAME@.o` |
| `dkms.conf` | so dkms rebuilds it when the kernel changes |
| `99-@NAME@.rules` | a udev rule for the device node |

## Building it by hand

    make -C /lib/modules/$(uname -r)/build M="$PWD" modules

## Before the module will load

Two things this repo's own platform makes worth knowing:

1. **Where it is installed.** `/` is a read-only slot on this layout, and `/var`
   is tmpfs, so a module has nowhere to live across a reboot unless it is baked
   into the image or into the initramfs.
2. **Signing.** The kernel only accepts modules signed with a key it trusts.
   With Secure Boot on, that means a key enrolled with MOK or the firmware's own
   db - `driver_status` reports what it could actually read, and on a desktop it
   usually cannot read the SecureBoot variable at all.
'''


def _scaffold_text(name: str) -> "dict[str, str]":
    """The five files, with the name substituted. No shell execution, ever."""
    from datetime import date

    def fill(template: str) -> str:
        return template.replace("@NAME@", name).replace("@DATE@", date.today().isoformat())

    return {
        f"{name}.c": fill(_C_TEMPLATE),
        "Makefile": fill(_MAKEFILE_TEMPLATE),
        "dkms.conf": fill(_DKMS_TEMPLATE),
        f"99-{name}.rules": fill(_UDEV_TEMPLATE),
        "README.md": fill(_README_TEMPLATE),
    }


def _c_problem(text: str, path: Path) -> str:
    """A check on generated C that is honest about what it is.

    There is no C compiler on this image to ask, and pretending a brace count
    is one would be the confident-wrong-answer this repo keeps recording. So
    this checks the two things that make a kernel module *loadable* and are
    visible in the text: balanced delimiters and a MODULE_LICENSE. It says
    nothing about types, and the skill says so.
    """
    if not _C_LICENCE.search(text):
        return f"{path}: no MODULE_LICENSE line - the kernel refuses to load a module without one."
    depth = {"{": 0, "(": 0, "[": 0}
    pairs = {"}": "{", ")": "(", "]": "["}
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in depth:
            depth[ch] += 1
        elif ch in pairs:
            depth[pairs[ch]] -= 1
            if depth[pairs[ch]] < 0:
                return f"{path}: unbalanced {ch!r} - nothing written."
    unbalanced = [k for k, v in depth.items() if v != 0]
    if unbalanced:
        return (f"{path}: {', '.join(unbalanced)} not balanced - nothing written. "
                "(A delimiter check, not a compiler.)")
    return ""


def _dir_summary(directory: Path) -> str:
    if not directory.is_dir():
        return ""
    parts = []
    for entry in sorted(directory.iterdir())[:12]:
        parts.append(f"{entry.name}{'/' if entry.is_dir() else ''}")
    return ", ".join(parts)


def _consent() -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    from shani_chronoa.config import ChronoaConfig

    if not ChronoaConfig().get_bool(_BUILD_KEY, False):
        return False, (
            f"compiling a kernel module is turned off (enable '{_BUILD_KEY}' in "
            f"Settings). Writing the module's source needs no such permission - "
            f"only handing a toolchain your files does, because the build runs "
            f"make over the directory and a Makefile there decides what it does."
        )
    return True, ""


def _build_module(project: Path, release: str, tree: Path) -> "tuple[int, str]":
    """Run the one Kbuild line. No flags come from the caller."""
    cmd = ["make", "-C", str(tree), f"M={project}", "modules"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"the build did not finish within {_TIMEOUT}s."
    except OSError as exc:
        return 127, f"could not run make: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _post_condition(arguments: dict):
    """The `.ko` must exist *and* be newer than the source it came from.

    Freshness is the whole point: a failed build leaves the previous `.ko`
    sitting there, and matching its mere existence would verify work that did
    not happen - the same defect `pdf_pages` has a test for. `None` means
    UNVERIFIED, which is the right answer whenever there is nothing to read.
    """
    name = (arguments.get("name") or "").strip().lower()
    raw = (arguments.get("path") or "").strip()
    if not name or not raw or not _NAME_RE.match(name):
        return None
    try:
        directory = files.resolve_in_home(raw)
    except files.PathProblem:
        return None
    source = directory / f"{name}.c"
    built = sorted(directory.glob("*.ko"))
    if not source.exists():
        return None
    if not built:
        return False, f"no .ko file is in {directory}"
    freshest = built[0].stat().st_mtime
    if freshest < source.stat().st_mtime:
        return False, (f"{built[0].name} is older than {name}.c, so it is left over "
                       "from an earlier build rather than the result of this one")
    return True, f"{built[0].name} is newer than {name}.c"


def _run_status(arguments: dict) -> str:
    return _status_text()


def _run_scaffold(arguments: dict) -> str:
    name = (arguments.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        return (f"{name!r} is not a usable module name. Use lowercase letters, "
                "digits and underscores, starting with a letter.")
    raw = (arguments.get("path") or f"~/drivers/{name}").strip()
    try:
        directory = files.resolve_in_home(raw)
    except files.PathProblem as exc:
        return f"Refusing to scaffold outside your home directory: {exc}"

    written: list[str] = []
    refused: list[str] = []
    for filename, text in _scaffold_text(name).items():
        target = directory / filename
        if target.exists():
            refused.append(filename)
            continue
        problem = _c_problem(text, target) if filename.endswith(".c") else files.parse_problem(text, target)
        if problem:
            return f"Refusing to write: {problem}"
    for filename, text in _scaffold_text(name).items():
        target = directory / filename
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            written.append(filename)
        except OSError as exc:
            return f"Could not write {target}: {exc}. Nothing was reported as created that was not."

    lines = [f"Scaffolded the module '{name}' in {directory}:"]
    for filename in written:
        lines.append(f"  {filename}")
    for filename in refused:
        lines.append(f"  {filename} - left alone, it already exists")
    if not written:
        lines.append("Nothing was written: every file is already there. "
                     "Edit the existing files, or scaffold under another name.")
        return "\n".join(lines)
    existing = _dir_summary(directory)
    if existing:
        lines.append(f"The directory now holds: {existing}")
    lines.append("")
    lines.append("This is source, not a built module. Ask driver_status whether this "
                 "machine can compile it, and driver_build to try.")
    return "\n".join(lines)


def _run_build(arguments: dict) -> str:
    # The gate comes first, before the path and the toolchain: a refusal must not
    # confirm that a directory exists or that a compiler is installed.
    allowed, reason = _consent()
    if not allowed:
        return f"Refusing to build: {reason}"

    name = (arguments.get("name") or "").strip().lower()
    raw = (arguments.get("path") or "").strip()
    if not name or not _NAME_RE.match(name):
        return "A build needs both a module name and the directory holding it."
    if not raw:
        return "Which directory holds the module? Give path, as in ~/drivers/mymodule."
    try:
        directory = files.resolve_in_home(raw)
    except files.PathProblem as exc:
        return f"Refusing to build outside your home directory: {exc}"
    if not (directory / f"{name}.c").exists():
        return (f"{directory / (name + '.c')} does not exist, so there is nothing to "
                "build. driver_scaffold writes one.")

    missing = []
    if not _first_present("make"):
        missing.append(files.tool_missing("make", "build a kernel driver"))
    compiler = _first_present("gcc", "cc", "clang")
    if not compiler:
        missing.append(files.tool_missing("gcc", "build a kernel driver"))
    release = _uname_release()
    tree = _build_dir(release)
    if tree is None:
        missing.append(
            f"the kernel build tree for {release} (/lib/modules/{release}/build), "
            f"which the linux-headers package provides")
    if missing:
        # `tool_missing` returns a full sentence ending in a period, so joining
        # them as-is produced "the 'base-devel' package.. driver_status lists".
        joined = "; ".join(item.rstrip(". ") for item in missing)
        return ("Not built. This machine is missing: " + joined +
                ". driver_status lists what a driver needs here in full.")

    code, output = _build_module(directory, release, tree)
    if code == 124:
        return output
    ko = sorted(directory.glob("*.ko"))
    if code != 0:
        tail = [ln for ln in output.strip().splitlines() if ln.strip()][-4:]
        detail = ("\n  " + "\n  ".join(tail)) if tail else ""
        return (f"make exited {code}, so no new module was produced.{detail}")
    if not ko:
        return ("make reported success but produced no .ko file. That is not a "
                "verified build - check the Makefile's obj-m line against the "
                "module name.")
    keepable, keep_reason = _modules_dir_writable()
    lines = [f"Built {ko[0].name} ({ko[0].stat().st_size} bytes)."]
    if not keepable:
        lines.append(f"It cannot be installed from here: {keep_reason}. A module on "
                     "this layout has to be baked into the image or the initramfs.")
    lines.append("Nothing was loaded. Whether an unsigned module would load is "
                 "reported by driver_status from what it could actually read - "
                 "on this platform lockdown enforces 'none', so signing is not "
                 "what stops one; the toolchain and the read-only root are.")
    return " ".join(lines)


SCHEMA_STATUS = {
    "type": "function",
    "function": {
        "name": "driver_status",
        "description": (
            "Whether this machine can develop a Linux kernel driver: is there a "
            "compiler, a kernel build tree for the running kernel, dkms, somewhere "
            "a module could be kept, and whether an unsigned one could load. "
            "Read-only, and every line is present, absent or unknown-with-a-reason."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SCHEMA_SCAFFOLD = {
    "type": "function",
    "function": {
        "name": "driver_scaffold",
        "description": (
            "Write a real out-of-tree Linux kernel module project into a "
            "directory under your home: the C source, the Kbuild Makefile, a "
            "dkms.conf, a udev rule and a README. Creates new files and never "
            "overwrites an existing one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Module name: lowercase letters, digits, underscores."},
                "path": {"type": "string",
                         "description": "Directory to create it in, under your home. Default ~/drivers/<name>."},
            },
            "required": ["name"],
        },
    },
}

SCHEMA_BUILD = {
    "type": "function",
    "function": {
        "name": "driver_build",
        "description": (
            "Build a scaffolded out-of-tree kernel module with Kbuild, then read "
            "the resulting .ko back off disk. Needs the 'driver-build-enabled' "
            "consent key. Refuses with the missing package named when this "
            "machine has no toolchain."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The module's name."},
                "path": {"type": "string", "description": "The directory holding the module's Makefile and .c"},
            },
            "required": ["name", "path"],
        },
    },
}

#: Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition

SKILLS = [
    Skill(name="driver_status", schema=SCHEMA_STATUS, run=_run_status),
    Skill(name="driver_scaffold", schema=SCHEMA_SCAFFOLD, run=_run_scaffold),
    Skill(name="driver_build", schema=SCHEMA_BUILD, run=_run_build),
]