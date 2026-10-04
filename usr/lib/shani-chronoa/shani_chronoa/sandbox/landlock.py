"""Landlock sandboxing layer for Shani Chronoa.

Provides an unprivileged, namespace-free sandboxing mechanism using the
Linux Landlock LSM. Designed to be used alongside bubblewrap as a
defense-in-depth layer, or as a fallback when bubblewrap cannot be used
due to lack of user namespaces.

The Landlock ruleset is applied in the child process before executing
the user command, ensuring the parent Chronoa process remains unrestricted.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import os
import stat
import sys
from typing import List, Tuple, Optional

# Landlock constants and structures
# These are defined based on the Linux kernel headers and may need
# adjustment for different architectures or kernel versions.

# Syscall numbers for x86_64 (verified via /usr/include/asm/unistd_64.h)
# We will verify at runtime and support other architectures if needed.
_LANDLOCK_SYSCALL_CREATE_RULESET = 444
_LANDLOCK_SYSCALL_ADD_RULE = 445
_LANDLOCK_SYSCALL_RESTRICT_SELF = 446

# prctl constants for PR_SET_NO_NEW_PRIVS
_PR_SET_NO_NEW_PRIVS = 38

# Landlock rule types (filesystem)
_LANDLOCK_RULE_PATH_BENEATH = 1


class RulesetAttr(ctypes.Structure):
    """`struct landlock_ruleset_attr` - which access rights this ruleset handles.

    Required to create a usable ruleset. Calling
    `landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)` is the
    *version query*: it returns the ABI number, not a file descriptor. Passing
    that 8 back into `landlock_add_rule` is what produced EBADF on every rule
    and on the closing `os.close`.
    """

    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    """`struct landlock_path_beneath_attr`, packed, in the kernel's field order.

    `allowed_access` comes FIRST, not `parent_fd`; and `parent_fd` is a 32-bit
    int in a packed struct, so it is NOT 8 bytes. Both were wrong at one point,
    so the kernel read garbage for `allowed_access` and rejected every rule with
    EINVAL - which looks exactly like a kernel without Landlock.
    """

    _pack_ = 1
    _fields_ = [
        ("allowed_access", ctypes.c_int64),
        ("parent_fd", ctypes.c_int32),
    ]

# Landlock access rights (filesystem) - these are defined by the ABI
# We will define the known rights and allow the ABI to tell us which are valid.
# The low 16 bits are filesystem rights in ABI 1.
_LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0  # May execute files
_LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1  # May create new files
_LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2  # May read existing files
_LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3  # May list directories
_LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4  # May remove directories
_LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5  # May remove files
_LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6  # May create character devices
_LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7  # May create directories
_LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8  # May create regular files
_LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9  # May create sockets
_LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10  # May create FIFOs
_LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11  # May create block devices
_LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12  # May create symbolic links
_LANDLOCK_ACCESS_FS_REFER = 1 << 13  # May rename/link across directories
_LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14  # May truncate files

# Additional rights from higher ABIs
_LANDLOCK_ACCESS_FS_IOCTL_DEV = 1 << 15  # May ioctl on devices (ABI 3)
_LANDLOCK_ACCESS_FS_BIND_TCP = 1 << 16  # May bind to TCP ports (ABI 4)
_LANDLOCK_ACCESS_FS_CONNECT_TCP = 1 << 17  # May connect to TCP ports (ABI 4)

# Every bit that may legally appear in `handled_access_fs`: 0-15. Bits 16-17
# exist in the ABI but belong to `handled_access_net`, not this field.
_LANDLOCK_ACCESS_FS_ALL_KNOWN = 0xFFFF

# Rights that can only mean anything on a directory. See `access_for_path_fd`.
_DIRECTORY_ONLY_RIGHTS = (
    _LANDLOCK_ACCESS_FS_READ_DIR
    | _LANDLOCK_ACCESS_FS_REMOVE_DIR
    | _LANDLOCK_ACCESS_FS_MAKE_DIR
    | _LANDLOCK_ACCESS_FS_REFER
)

# Landlock creation flags
_LANDLOCK_CREATE_RULESET_VERSION = 1

# Landlock's own documentation recommends `O_PATH` for `parent_fd`: it names an
# object without opening it, so it needs no read permission and works whatever
# the type. `O_NOFOLLOW` is the half that makes the descriptor name the object
# policy asked for, and `O_CLOEXEC` keeps it from leaking into the exec'd
# command. Linux-only, as Landlock itself is; the 0 defaults keep a non-Linux
# import from raising at module scope.
_O_PATH = getattr(os, "O_PATH", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)

_PIN_FLAGS = _O_PATH | _O_NOFOLLOW | _O_CLOEXEC

# Return values for landlock_create_ruleset
# On success, returns the ABI version (>=1).
# On error, returns a negative errno.
ABI_VERSION_NOT_SUPPORTED = 0


def _get_libc() -> ctypes.CDLL:
    """Get the libc library for syscall and prctl."""
    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        raise RuntimeError("Cannot find libc")
    return ctypes.CDLL(libc_name, use_errno=True)


def _syscall(syscall_num: int, *args) -> int:
    """Invoke a system call via libc.syscall."""
    libc = _get_libc()
    # syscall(num, arg1, arg2, ...) returns a long
    libc.syscall.restype = ctypes.c_long
    libc.syscall.argtypes = [ctypes.c_long] + [ctypes.c_long] * len(args)
    res = libc.syscall(syscall_num, *[ctypes.c_long(arg) for arg in args])
    if res == -1:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return res


def _prctl(option: int, arg2: int = 0, arg3: int = 0, arg4: int = 0, arg5: int = 0) -> int:
    """Invoke prctl system call."""
    libc = _get_libc()
    libc.prctl.restype = ctypes.c_int
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    res = libc.prctl(option, arg2, arg3, arg4, arg5)
    if res == -1:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return res


def abi_version() -> int:
    """Return the Landlock ABI version supported by the kernel.

    Returns:
        The ABI version (>=1) if Landlock is supported.
        Returns 0 if Landlock is not supported (i.e., the syscall
        returns ENOSYS or similar).
        On other errors, returns a negative errno.
    """
    try:
        # The first argument is the address of the ruleset_attr (NULL for version query)
        # The second argument is the size of the ruleset_attr (0 for version query)
        # The third argument is the flags (LANDLOCK_CREATE_RULESET_VERSION)
        # We use syscall because the glibc wrapper may not exist.
        return _syscall(
            _LANDLOCK_SYSCALL_CREATE_RULESET,
            0,  # ruleset_attr (NULL)
            0,  # ruleset_attr_size
            _LANDLOCK_CREATE_RULESET_VERSION,
        )
    except OSError as e:
        if e.errno == errno.ENOSYS:
            # Landlock syscall not available
            return 0
        # For other errors, return the negative errno
        return -e.errno


def _get_filesystem_rights(abi: int) -> int:
    """Return the mask of filesystem rights known for the given ABI.

    Args:
        abi: The Landlock ABI version.

    Returns:
        A bitmask of the filesystem rights that are defined for this ABI.
    """
    # ABI 1 defines the low 16 bits (0x0000FFFF)
    if abi >= 1:
        base_rights = (
            _LANDLOCK_ACCESS_FS_EXECUTE |
            _LANDLOCK_ACCESS_FS_WRITE_FILE |
            _LANDLOCK_ACCESS_FS_READ_FILE |
            _LANDLOCK_ACCESS_FS_READ_DIR |
            _LANDLOCK_ACCESS_FS_REMOVE_DIR |
            _LANDLOCK_ACCESS_FS_REMOVE_FILE |
            _LANDLOCK_ACCESS_FS_MAKE_CHAR |
            _LANDLOCK_ACCESS_FS_MAKE_DIR |
            _LANDLOCK_ACCESS_FS_MAKE_REG |
            _LANDLOCK_ACCESS_FS_MAKE_SOCK |
            _LANDLOCK_ACCESS_FS_MAKE_FIFO |
            _LANDLOCK_ACCESS_FS_MAKE_BLOCK |
            _LANDLOCK_ACCESS_FS_MAKE_SYM |
            _LANDLOCK_ACCESS_FS_REFER |
            _LANDLOCK_ACCESS_FS_TRUNCATE
        )
    else:
        base_rights = 0

    # ABI 3 adds IOCTL_DEV (bit 15)
    if abi >= 3:
        base_rights |= _LANDLOCK_ACCESS_FS_IOCTL_DEV

    # ABI 4 adds network rights, but they are NOT filesystem rights. Landlock
    # carries them in a separate `handled_access_net` field of the same attr
    # struct, so OR-ing BIND_TCP/CONNECT_TCP into `handled_access_fs` makes the
    # kernel reject the entire ruleset with EINVAL - one spurious bit and no
    # confinement at all. Verified: every bit 0-15 is accepted individually, the
    # full 0x3ffff mask is refused. Scoping TCP needs the network field added
    # to RulesetAttr; until then this deliberately handles filesystem only.
    return base_rights & _LANDLOCK_ACCESS_FS_ALL_KNOWN


def _pin_path(path: str) -> int:
    """Return a descriptor naming exactly the object `path` names, and nothing else.

    A Landlock rule is granted per **inode**. Anything that resolves a name and
    then acts on the name a second time can be redirected in between, and the
    whole point of pinning is that there is no second resolution: the descriptor
    is the rule's `parent_fd`, so the grant is on whatever inode this returned.

    The walk is one component at a time, each opened `O_NOFOLLOW` relative to
    the descriptor of the previous one, because `O_NOFOLLOW` only covers the
    **final** component - a plain `open()` follows every component. Pinning the
    parent first means a rename-and-symlink swap of a component cannot redirect
    the rest of the walk: the swap would have to land on the pinned inode.

    Symlinked components are refused rather than followed. Following one would
    hand the grant to whatever the link names, which is the attack; and refusing
    is fail-closed in a way that is easy to act on, since the error names the
    component. Verified on this kernel rather than assumed: a rule added from an
    `O_PATH|O_NOFOLLOW` descriptor of a symlink is rejected by `landlock_add_rule`
    with a bare EINVAL, indistinguishable from a malformed attribute struct.

    Consequence, deliberate: a workspace reached through a symlinked component
    (a symlinked `$HOME`, say) now fails loudly instead of confining a different
    directory than the one policy named. Trusted constants are canonicalised by
    `get_default_allowed_paths` before they get here, so `/bin` and friends still
    resolve on a merged-`/usr` system.
    """
    if not path.startswith("/"):
        raise OSError(
            errno.EINVAL,
            f"allowlist entry {path!r} is not an absolute path, so it cannot be pinned",
        )
    components = [part for part in path.split("/") if part not in ("", ".")]
    if ".." in components:
        raise OSError(
            errno.EINVAL,
            f"allowlist entry {path!r} contains '..', which cannot be resolved without "
            "following a symlink back out of the component already pinned",
        )

    held = [os.open("/", os.O_RDONLY | os.O_DIRECTORY | _O_CLOEXEC)]
    try:
        for component in components:
            fd = os.open(component, _PIN_FLAGS, dir_fd=held[-1])
            held.append(fd)
            if stat.S_ISLNK(os.fstat(fd).st_mode):
                raise OSError(
                    errno.ELOOP,
                    f"allowlist entry {path!r} resolves through the symlink component "
                    f"{component!r}; a rule is granted per inode, so naming the link "
                    "would grant whatever it points at rather than what was asked for",
                )
        return held.pop()
    finally:
        for fd in held:
            with contextlib.suppress(OSError):
                os.close(fd)


def access_for_path_fd(fd: int, access_rights: int, path: str) -> int:
    """The rights a rule may carry for the object `fd` actually names.

    The rights in an allowlist entry were chosen somewhere else, by someone
    looking at a *pathname*, and in this codebase that is a different process:
    `get_default_allowed_paths()` runs in the parent and the rules are applied in
    the confined child. If those two disagree about what an entry is, the grant
    is not the grant that was decided on.

    Directory-only rights are where the disagreement bites, because they are the
    rights that are meaningless on the wrong kind of object - `READ_DIR` on a
    file, `MAKE_DIR` under one - and a grant carrying them would silently grant
    less than the policy believes it granted. Refused rather than masked: a mask
    turns "these two disagreed" into a quiet downgrade, and this module's rule is
    that a check which cannot fail must not be replaced by one which cannot fail
    loudly.
    """
    mode = os.fstat(fd).st_mode
    if stat.S_ISDIR(mode):
        return access_rights
    directory_only = access_rights & _DIRECTORY_ONLY_RIGHTS
    if not directory_only:
        return access_rights
    raise OSError(
        errno.ENOTDIR,
        f"allowlist entry {path!r} grants directory-only rights "
        f"0x{directory_only:x} but the pinned descriptor names mode "
        f"0x{stat.S_IFMT(mode):o}, not a directory; the entry resolved differently "
        "when its rights were chosen than when the rule was built",
    )


def apply_filesystem_allowlist(
    allowed_paths: List[Tuple[str, int]],
    *,
    abi: Optional[int] = None,
) -> None:
    """Apply a Landlock ruleset restricting filesystem access to allowed paths.

    This function must be called before the thread performs any filesystem
    operations that should be restricted. It modifies the calling thread's
    Landlock domain, and the restrictions are inherited by children.

    Args:
        allowed_paths: A list of (path, access_rights) tuples.
            path: The filesystem path to allow access to.
            access_rights: A bitmask of Landlock filesystem access rights
                (e.g., _LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_READ_DIR).
        abi: The Landlock ABI version to use. If None, the current kernel's
            ABI version is used via abi_version().

    Raises:
        OSError: If Landlock is not available or if rule application fails.
        ValueError: If an access right is not supported by the kernel's ABI.
    """
    if abi is None:
        abi = abi_version()
    if abi <= 0:
        raise OSError(
            errno.ENOSYS,
            f"Landlock not available (abi_version() returned {abi})"
        )

    # Landlock requires the no_new_privs flag to be set.
    no_new_privs()
    if abi is None:
        abi = abi_version()
    if abi <= 0:
        raise OSError(
            errno.ENOSYS,
            f"Landlock not available (abi_version() returned {abi})"
        )

    # Get the set of rights known for this ABI
    known_rights = _get_filesystem_rights(abi)

    # Prepare the ruleset. The attr must describe which rights are handled and
    # size must be non-zero: the version-query form (NULL attr, size 0, the
    # VERSION flag) returns the ABI number, not a descriptor.
    attr = RulesetAttr(handled_access_fs=known_rights)
    ruleset_fd = _syscall(
        _LANDLOCK_SYSCALL_CREATE_RULESET,
        ctypes.addressof(attr),
        ctypes.sizeof(attr),
        0,
    )

    # Pin every entry before any rule is added, so an entry that cannot be pinned
    # aborts with nothing granted rather than leaving a half-built allowlist.
    pinned: List[Tuple[str, int, int]] = []
    try:
        for path, access_rights in allowed_paths:
            if access_rights & ~known_rights:
                raise ValueError(
                    f"Access rights 0x{access_rights:x} includes bits not known "
                    f"for ABI {abi} (known: 0x{known_rights:x})"
                )
            fd = _pin_path(path)
            try:
                rights = access_for_path_fd(fd, access_rights, path)
            except BaseException:
                os.close(fd)
                raise
            pinned.append((path, rights, fd))

        for path, rights, fd in pinned:
            rule = PathBeneathAttr(parent_fd=fd, allowed_access=rights)
            # landlock_add_rule(ruleset_fd, rule_type, rule_attr, flags).
            # All four arguments are required: omitting rule_type passed the
            # attr pointer in its place and the kernel rejected the call with
            # EINVAL, so no rule was ever added and nothing was confined.
            _syscall(
                _LANDLOCK_SYSCALL_ADD_RULE,
                ruleset_fd,
                _LANDLOCK_RULE_PATH_BENEATH,
                ctypes.addressof(rule),
                0,  # flags
            )

        # Restrict self with the ruleset
        _syscall(
            _LANDLOCK_SYSCALL_RESTRICT_SELF,
            ruleset_fd,
            0,  # flags
        )
    finally:
        for _, _, fd in pinned:
            with contextlib.suppress(OSError):
                os.close(fd)
        os.close(ruleset_fd)


def no_new_privs() -> None:
    """Set the no-new-priviles bit for the current process.

    This prevents the process from gaining additional privileges via
    setuid executables or similar mechanisms. It is a prerequisite
    for using Landlock in some contexts, but Landlock itself does not
    require it. However, it is good practice to set it when applying
    strict sandboxing.
    """
    _prctl(_PR_SET_NO_NEW_PRIVS, 1)


def get_default_allowed_paths(workspace: str) -> List[Tuple[str, int]]:
    """Return the default allowed paths for a Chronoa sandbox.

    These paths are chosen to match the typical bubblewrap mounts used by
    the SandboxExecutor, providing read access to the system and write
    access to the workspace and temporary directories.

    Args:
        workspace: The absolute path to the workspace directory.

    Returns:
        A list of (path, access_rights) tuples suitable for
        apply_filesystem_allowlist.
    """
    # Define the access rights we want to allow
    # Note: We assume ABI >= 1 for the default rights.
    # The caller should check the ABI version and adjust if needed.
    traverse_execute = (
        _LANDLOCK_ACCESS_FS_READ_DIR |
        _LANDLOCK_ACCESS_FS_EXECUTE
    )
    read_only = (
        _LANDLOCK_ACCESS_FS_READ_FILE |
        _LANDLOCK_ACCESS_FS_READ_DIR
    )
    read_write_execute = (
        _LANDLOCK_ACCESS_FS_READ_FILE |
        _LANDLOCK_ACCESS_FS_READ_DIR |
        _LANDLOCK_ACCESS_FS_WRITE_FILE |
        _LANDLOCK_ACCESS_FS_MAKE_DIR |
        _LANDLOCK_ACCESS_FS_REMOVE_FILE |
        _LANDLOCK_ACCESS_FS_REMOVE_DIR |
        _LANDLOCK_ACCESS_FS_EXECUTE
    )
    write_execute = (
        _LANDLOCK_ACCESS_FS_WRITE_FILE |
        _LANDLOCK_ACCESS_FS_MAKE_DIR |
        _LANDLOCK_ACCESS_FS_REMOVE_FILE |
        _LANDLOCK_ACCESS_FS_REMOVE_DIR |
        _LANDLOCK_ACCESS_FS_EXECUTE
    )

    fixed = [
        # Traverse and run, but do NOT read. A READ_FILE rule on "/" makes every
        # file on the machine readable, which voids read confinement entirely
        # while still reporting a confined child - verified: with this entry
        # granting read_execute, a file outside the workspace was read back in
        # full. Root gets READ_DIR so paths can be traversed and EXECUTE so
        # binaries can be run; reading is granted only where it is genuinely
        # needed, below.
        ("/", traverse_execute),
        # The interpreter and its libraries must be readable or nothing runs at
        # all (`python3 -c ...` reads the standard library). This is the
        # smallest set that keeps skills working.
        ("/usr", read_only),
        ("/bin", read_only),
        ("/sbin", read_only),
        ("/lib", read_only),
        ("/lib64", read_only),
        ("/dev", read_only),
        ("/proc", read_only),
        (workspace, read_write_execute),
        ("/tmp", write_execute),
        ("/run", write_execute),
    ]

    # Resolve the hardcoded system names to the inodes they name. `_pin_path`
    # refuses a symlinked component, and on a merged-/usr system `/bin`,
    # `/sbin`, `/lib` and `/lib64` are all symlinks - so without this, every
    # modern Linux would fail to confine at all. These are literals in this
    # function, not anything a caller supplies, which is the only reason
    # resolving them here is safe. Duplicates collapse because a Landlock grant
    # is per inode: `/usr/bin` is already covered by the `/usr` entry.
    resolved: List[Tuple[str, int]] = []
    seen = set()
    for path, rights in fixed + _interpreter_read_paths(read_only):
        # The workspace is deliberately NOT resolved. Resolving it would open
        # the very window pinning closes: a swap performed before this call
        # would be baked in here and the child would faithfully pin the
        # attacker's target. Left alone, the child walks it directly and
        # refuses it if it is a symlink at that moment.
        target = path if path == workspace else os.path.realpath(path)
        if target in seen:
            continue
        seen.add(target)
        resolved.append((target, rights))
    return resolved


def _interpreter_read_paths(read_only: int) -> List[Tuple[str, int]]:
    """Read-only grants for the running interpreter, if the fixed list misses it.

    The fixed entries above cover a SYSTEM python: /usr, /bin and /lib are all
    granted read, so `python3 -c ...` starts. That silently assumes the
    interpreter lives under one of them, and it is the only thing making this
    work at all - a rule that grants EXECUTE without READ_FILE lets the kernel
    run the binary but not read it, so a missing read grant does not produce a
    permission error, it produces an interpreter that cannot initialise.

    It is not a safe assumption. A venv, pyenv, conda or /opt install puts the
    interpreter somewhere else entirely, and then the confined child dies at
    `init_import_site: Failed to import the site module` - which is exactly what
    CI hit, since its interpreter is a venv under the runner's temp dir and
    therefore in none of the entries above. The test noticed before a user did:
    "the interpreter must still run, or the confinement is useless in
    practice."

    Only the interpreter's own directories are added, and only read. This is
    not a widening of read confinement in any meaningful sense - it is the
    narrowest set that lets the process that is already being confined start,
    and it deliberately does not grant the parent of a venv, which is what
    would actually expose the user's home directory. Paths already covered by
    a fixed entry are skipped so the ruleset does not accumulate redundant
    grants.
    """
    covered = ("/usr", "/bin", "/sbin", "/lib", "/lib64")
    candidates = [sys.prefix, sys.base_prefix, os.path.dirname(sys.executable)]
    out: List[Tuple[str, int]] = []
    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        real = os.path.realpath(path)
        if any(real == c or real.startswith(c.rstrip("/") + "/") for c in covered):
            continue
        if os.path.isdir(real):
            out.append((real, read_only))
    return out


def get_landlock_wrapper() -> str:
    """Return a Python script that can be used as a Landlock wrapper.

    The wrapper expects the environment variable LANLOCK_ALLOWED_PATHS to be
    set to a JSON-encoded list of (path, access_rights) tuples.

    It applies the Landlock ruleset and then executes the command passed
    as arguments.
    """
    return r'''
import json
import os
import sys
import shani_chronoa.sandbox.landlock as landlock

def main() -> None:
    allowed_paths_json = os.environ.get('LANLOCK_ALLOWED_PATHS')
    if not allowed_paths_json:
        sys.stderr.write("Landlock: LANLOCK_ALLOWED_PATHS not set\\n")
        sys.exit(1)

    try:
        allowed_paths = json.loads(allowed_paths_json)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Landlock: failed to parse LANLOCK_ALLOWED_PATHS: {e}\\n")
        sys.exit(1)

    try:
        landlock.apply_filesystem_allowlist(allowed_paths)
    except Exception as e:
        sys.stderr.write(f"Landlock: failed to apply ruleset: {e}\\n")
        sys.exit(1)

    # Execute the command
    if len(sys.argv) < 2:
        sys.stderr.write("Landlock: no command specified\\n")
        sys.exit(1)
    os.execvp(sys.argv[1], sys.argv[1:])

if __name__ == '__main__':
    main()
'''