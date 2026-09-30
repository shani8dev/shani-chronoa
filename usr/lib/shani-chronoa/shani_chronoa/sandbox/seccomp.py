"""A seccomp syscall filter for the sandboxed child, layered on Landlock.

**Python has no seccomp binding.** There is no `seccomp()` in the standard
library and no pip-installable one that is not a C extension this repo may
not add. So the filter is a classic BPF program assembled here as a
`ctypes.Structure` array and installed with one `seccomp(2)` call. That is
viable, and it was measured before it was written rather than assumed -
including the two ways it went wrong first:

- `seccomp(SECCOMP_GET_ACTION_AVAIL, SECCOMP_RET_ALLOW, &action)` returns
  `EINVAL` on this kernel **7.0.0-34-generic**, from a C program as well as
  from Python. The most obvious capability probe is therefore useless here,
  so availability is decided by attempting the install and reading the
  errno, never by asking a question the kernel declines to answer.
- The arch guard's jump was written the wrong way round first (`jt=0, jf=1`
  where it needed `jt=1, jf=0`). That variant **installs cleanly**,
  `Seccomp:` in `/proc/self/status` correctly reports `2`, and then returns
  `ENOSYS` for *every* syscall including `write(2)` and `exit_group(2)` -
  the process dies before printing anything. A filter that installs, looks
  installed, and denies everything is the worst of the three possible
  outcomes, and it is only distinguishable from a working one by actually
  running a command under it. Every test in
  `tests/test_sandbox_seccomp.py` therefore executes the real thing.

**What it buys, measured.** The harvest calls `memfd_create` "a complete
bypass" of the existing Landlock layer, and that was checked rather than
repeated: a C program run under this repo's own
`landlock.apply_filesystem_allowlist(get_default_allowed_paths(ws))` calls
`memfd_create`, copies `/bin/echo` into the descriptor, `fchmod`s it 0700
and `execveat`s it with `AT_EMPTY_PATH`. It printed its argument and
exited 0. A file with no pathname is not reachable by a rule granted on a
path, so `LANDLOCK_ACCESS_FS_EXECUTE` is never consulted against anything
the policy reasoned about. With this filter on, the same program gets
`EPERM` from `memfd_create` and does not run.

**What it does not buy, stated up front so nobody assumes otherwise:**

- **`clone`/`clone3` namespace creation is not filtered.** `clone3` takes a
  pointer to a struct, which seccomp cannot dereference, and glibc's
  `clone3` fallback to `clone` triggers on `ENOSYS` only - so denying
  `clone3` outright would break every Python thread in every skill. The
  reachable half, `unshare(2)`, is filtered; the `clone` half is a real
  residual gap and is a gap, not an oversight.
- **No network restriction.** `socket(2)` is deliberately untouched.
  Ollama, web search and the PipeWire audio stack all need it, and
  `profiles.py` already records that filtering it would break the session
  bus that `LEVEL_3_HOST_USER` exists to provide. The module docstring
  there is updated to say this filter exists and still does not do that.
- **x86_64 only.** The syscall table below is a literal list of x86_64
  numbers, read out of `/usr/include/x86_64-linux-gnu/asm/unistd_64.h` on
  this machine rather than from memory. On any other architecture
  `apply()` raises rather than installing a filter that would deny whatever
  number happened to be at that index.

**One-way is the reason this is here at all.** A filter the sandboxed child
can widen is not a filter, so two syscalls are denied unconditionally:
`seccomp(2)` itself, and `prctl(PR_SET_SECCOMP, ...)`, the older install
path. Linux ANDs multiple filters, so a child cannot remove a block by
adding a permissive one - but it can also no longer add a filter of its own
at all, which is what `tests/test_sandbox_seccomp.py::test_the_child_cannot
_install_a_permissive_filter` observes from inside a real filtered child.

`PR_SET_NO_NEW_PRIVS` is set immediately before the install, and it is
irreversible for the life of the process. It is also why this never runs on
`LEVEL_4_HOST_ROOT`: `PR_SET_NO_NEW_PRIVS` makes the kernel refuse to honour
`setuid`, so a filter installed before `pkexec` would leave the elevation
silently broken. `SandboxExecutor` logs that rather than doing it anyway.

**Default off, and off means byte-identical.** The gate is the
`sandbox-seccomp-enabled` gsetting, default `false`. With it off, `execute()`
takes one extra `get_bool` and `_harden_child` takes one branch on a
module-global; no filter is assembled, no syscall is made, and the child's
`Seccomp:` field stays `0`. With it on and the filter uninstallable, the
call is **refused** (exit 126) in the parent before any child exists, and a
failure *inside* the child is refused too - never downgraded to running
unfiltered, which is the "configured but not enforced" shape this repo keeps
recording as a defect.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import platform
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: The gsetting that turns the filter on. Read by `SandboxExecutor`, not
#: here, so that this module has no GSettings dependency and can be imported
#: (and unit-tested) with no schema present at all.
SECCOMP_SETTING = "sandbox-seccomp-enabled"


class SeccompError(RuntimeError):
    """The filter was required and could not be provided.

    Distinct from a bare `OSError` so the executor can turn it into a 126
    policy refusal rather than the generic exit-1 spawn error: the caller
    asked for a sandbox, the sandbox could not exist, and the command must
    not run without it.
    """


# --------------------------------------------------------------------------
# BPF. Values from linux/bpf_common.h and linux/seccomp.h; verified against a
# C program compiled with the real headers on this machine, because a filter
# assembled from memory is exactly the thing this module exists not to be.
# --------------------------------------------------------------------------

_BPF_LD = 0x00
_BPF_W = 0x00
_BPF_ABS = 0x20
_BPF_JMP = 0x05
_BPF_JEQ = 0x10
_BPF_K = 0x00
_BPF_RET = 0x06

_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000

#: `AUDIT_ARCH_X86_64`. A filter that only matches on `nr` is walked past by a
#: 32-bit compat syscall on the same process, which reaches the same
#: operations through a different table. A non-matching arch gets every
#: syscall answered `ENOSYS` rather than `ALLOW`, which neutralises the compat
#: path without killing a process outright.
_AUDIT_ARCH_X86_64 = 0xC000003E

_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_SET_MODE_FILTER = 1
_SYS_SECCOMP = 317
_SYS_PRCTL = 157
_SYS_PTRACE = 101

#: `offsetof(struct seccomp_data, ...)`. `nr` at 0, `arch` at 4, and `args`
#: at 16 - the two `u32` fields then the instruction pointer, then six
#: `u64` arguments.
_OFF_NR = 0
_OFF_ARCH = 4
_OFF_ARG0 = 16

#: `PTRACE_TRACEME`. See `_BLOCKED` for why this one value is allowed.
_PTRACE_TRACEME = 0


class _SockFilter(ctypes.Structure):
    """`struct sock_filter` - 8 bytes, the classic BPF instruction."""

    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    ]


class _SockFprog(ctypes.Structure):
    """`struct sock_fprog` - a *count* and a pointer, not a length.

    `len` is the number of instructions, and the pointer must outlive the
    `seccomp(2)` call, so the caller keeps the `_SockFilter` array alive for
    the duration.
    """

    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(_SockFilter)),
    ]


@dataclass(frozen=True)
class BlockedSyscall:
    """One denied syscall, and the reason it is denied.

    The reason is a required field rather than a comment because the whole
    failure mode of a blocklist is silent over-filtering: a syscall nobody
    can justify being in the list is a syscall that breaks a skill with no
    visible cause. Every entry below was checked against what the real
    programs in this repo's skill path actually issue - `strace` of
    `python3 -c`, `sh -c 'a; b'`, `dd`, `env`, `sleep`, `git status`,
    `git log`, `ls`, `wpctl`, `pw-record`, `systemctl --user` and a
    PyGObject import found **zero** calls to any of them.
    """

    number: int
    name: str
    why: str


#: The list. Grouped by what the syscall would achieve, because "we blocked
#: some numbers" is not a policy anyone can review.
_BLOCKED: Tuple[BlockedSyscall, ...] = (
    # -- reshaping the filesystem. Landlock grants rights per inode, and has
    #    no mount rights at all in any ABI, so anything that changes what is
    #    mounted is entirely outside what the filesystem layer can see.
    BlockedSyscall(165, "mount", "Landlock has no mount right in any ABI; "
                                 "mounting over a path is invisible to it."),
    BlockedSyscall(166, "umount2", "same, and the natural way to make a "
                                   "mount someone else made disappear."),
    BlockedSyscall(155, "pivot_root", "changes the root a process resolves "
                                      "every pathname against."),
    BlockedSyscall(161, "chroot", "the same escape in its older, weaker form."),
    # The new mount API is a complete second front door to `mount`:
    # fsopen + fsconfig + fsmount + move_mount. Blocking only 165 would leave
    # the identical hole open one syscall to the right.
    BlockedSyscall(430, "fsopen", "new mount API: first half of the replacement for mount(2)."),
    BlockedSyscall(431, "fsconfig", "new mount API: configures the object fsopen returned."),
    BlockedSyscall(432, "fsmount", "new mount API: produces a detached mount fd."),
    BlockedSyscall(429, "move_mount", "new mount API: attaches it somewhere. This is the mount(2) equivalent."),
    BlockedSyscall(428, "open_tree", "new mount API: clones a mount or subtree."),
    BlockedSyscall(433, "fspick", "new mount API: returns a detached mount fd for a path."),
    BlockedSyscall(442, "mount_setattr", "new mount API: rewrites mount options, including making a tree private."),

    # -- fileless exec. This is the measured one; see the module docstring.
    BlockedSyscall(319, "memfd_create",
                   "MEASURED on this machine: memfd_create + execveat(AT_EMPTY_PATH) runs a "
                   "binary with no pathname from inside this repo's own Landlock ruleset, "
                   "because a rule granted on a path never matches an inode that has none. "
                   "A complete bypass of the filesystem layer on its own."),

    # -- kernel code and kernel introspection.
    BlockedSyscall(175, "init_module", "loads kernel code."),
    BlockedSyscall(313, "finit_module", "loads kernel code from a file, without a second copy in memory."),
    BlockedSyscall(176, "delete_module", "unloads kernel code."),
    BlockedSyscall(246, "kexec_load", "loads and boots a different kernel."),
    BlockedSyscall(320, "kexec_file_load", "the same, reading the image from a file descriptor."),
    BlockedSyscall(321, "bpf", "loads BPF programs; on a host that grants CAP_BPF this is "
                               "arbitrary kernel introspection, and it is how a filter gets "
                               "outperformed rather than argued with."),
    BlockedSyscall(298, "perf_event_open",
                   "samples another process's execution. Kept even though this host already "
                   "refuses it: /proc/sys/kernel/perf_event_paranoid reads 4 here, which is a "
                   "host setting a user can lower, and a filter that depends on a sysctl is not "
                   "a filter."),

    # -- the seccomp-evading submission path. io_uring performs filesystem and
    #    network operations on behalf of the caller without a syscall per
    #    operation, so a filter that only denies mount/openat is simply not on
    #    that path at all. This is the documented bypass class, not a theory.
    BlockedSyscall(425, "io_uring_setup", "io_uring is a syscall-bypassing execution path; "
                                          "without it a filter never sees the operations it submits."),
    BlockedSyscall(426, "io_uring_enter", "io_uring: submits the work a filter cannot inspect."),
    BlockedSyscall(427, "io_uring_register", "io_uring: registers buffers and rings."),

    # -- reaching into another process. `ptrace` is in `_CONDITIONAL` below
    #    rather than here, because it is the one entry with a legal value.
    BlockedSyscall(310, "process_vm_readv", "reads another process's memory."),
    BlockedSyscall(311, "process_vm_writev", "writes another process's memory."),
    BlockedSyscall(434, "pidfd_open", "the handle pidfd_getfd operates on. Harmless alone; "
                                     "listed so the pair is not left with one half."),
    BlockedSyscall(438, "pidfd_getfd", "steals a file descriptor from another process, which is a "
                                       "complete defeat of a per-inode policy: the descriptor was "
                                       "opened by someone whose rights were never checked."),

    # -- namespaces.
    BlockedSyscall(272, "unshare",
                   "the one namespace escape an unprivileged process genuinely has: unshare(CLONE_NEWUSER) "
                   "grants a full capability set inside the new namespace, and every mount syscall above "
                   "is reachable again from there. clone(CLONE_NEWUSER) is NOT filtered - see the module "
                   "docstring for why seccomp cannot do it without breaking every Python thread."),
    BlockedSyscall(308, "setns", "joins an existing namespace, including a mount namespace the "
                                 "confined process is not currently in."),
)

#: Denied on all but one argument value, so these are **not** in `_BLOCKED`: a
#: syscall in both lists is denied by the unconditional chain before the
#: argument check is ever reached, which is exactly the bug that shipped in
#: this module's first draft - `ptrace(0)` returned `EPERM` while the
#: `PTRACE_TRACEME` exception sat unreachable below it.
_CONDITIONAL: Tuple[BlockedSyscall, ...] = (
    BlockedSyscall(101, "ptrace",
                   "attachment and memory access on a process this one did not fork, for every "
                   "request except PTRACE_TRACEME (arg0 == 0), which is still allowed: it only "
                   "declares that this process's *parent* may trace it, and grants the child "
                   "nothing. seccomp cannot see the parent/child relation, so an argument check "
                   "is the strongest statement available here rather than the strongest one "
                   "wanted. Measured: PTRACE_TRACEME returns 0 unfiltered and still returns 0 "
                   "under this filter, while PTRACE_ATTACH is refused with EPERM by the filter."),
)

#: `seccomp(2)` itself. See the module docstring: Linux ANDs filters, so a
#: child cannot *remove* a block by adding a permissive filter - but denying
#: the install path entirely means it cannot add one either, and that is the
#: property the one-way test asserts.
_BLOCKED_SECCOMP_SYSCALL = 317

#: `prctl(PR_SET_SECCOMP, ...)`, the pre-3.5 install path. Argument-checked
#: rather than denied wholesale, because `prctl(2)` itself is used by
#: ordinary programs - `PR_SET_NAME` for thread naming above all - and
#: denying all of it would be the kind of over-filtering that breaks skills
#: with no visible cause.
_PR_SET_SECCOMP_ARG = 22

#: The public, read-only views used by the surface and the tests.
BLOCKED: Tuple[BlockedSyscall, ...] = _BLOCKED
CONDITIONAL: Tuple[BlockedSyscall, ...] = _CONDITIONAL


def blocked_numbers() -> Tuple[int, ...]:
    """Every denied syscall number, sorted, for reporting and tests."""
    return tuple(sorted(entry.number for entry in _BLOCKED + _CONDITIONAL))


def _libc() -> ctypes.CDLL:
    name = ctypes.util.find_library("c") or "libc.so.6"
    return ctypes.CDLL(name, use_errno=True)


def architecture() -> str:
    """The machine architecture this table is valid for."""
    return platform.machine()


def supported_architecture() -> bool:
    """Whether the syscall table in this module is valid for this machine.

    Not a preference. These are literal x86_64 numbers, and installing them
    on aarch64 would deny whatever syscall happens to occupy index 165 there,
    which is why `apply()` refuses rather than guesses.
    """
    return architecture() == "x86_64"


def mode() -> Optional[str]:
    """`/proc/self/status`'s `Seccomp:` field, or None if it cannot be read.

    Duplicated from `senses.security.read_seccomp()` rather than imported
    from it: this runs inside `preexec_fn` in a forked child, and importing
    the senses package there would drag GSettings and the whole sense
    registry into the window between fork and exec. Two four-line readers of
    the same file is the cheaper trade, and the sense is the surface this
    feeds - it calls the function that owns the reporting.
    """
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key.strip() == "Seccomp":
                    return value.strip()
    except OSError:
        return None
    return None


def is_active() -> bool:
    """Whether *this* process is under a seccomp filter.

    Note what this can and cannot answer: the filter is installed in the
    sandboxed child, so the long-lived assistant process reads `False` even
    while every skill call is filtered. That is not a bug in the probe, it is
    the shape of the design, and `senses/security.py` says so rather than
    reporting a machine-wide claim it cannot support.
    """
    return mode() == "2"


def program() -> Tuple[List[_SockFilter], int]:
    """Assemble the BPF program and return `(instructions, count)`.

    Kept separate from `apply()` so the filter can be inspected - and
    disassembled in a test - without installing anything, and so a bug in the
    assembly shows up as a wrong program rather than as a dead process.

    The shape:

        0   load arch
        1   arch == x86_64 ? skip the ENOSYS return : fall into it
        2   RET ERRNO(ENOSYS)              # a compat-arch process gets nothing
        3   load nr
        ..  per blocked syscall: JEQ -> RET ERRNO(EPERM)
        ..  ptrace: JEQ -> load arg0 -> JEQ 0 -> skip -> RET ERRNO(EPERM)
        last RET ALLOW

    Every denial is `ERRNO(EPERM)`, not `ALLOW` and not `KILL`: a program
    that tries something forbidden gets the ordinary "operation not
    permitted" it would get from a permission check, which is both visible
    in output and recoverable, and a filter that killed the child would turn
    a refused command into an unexplained signal.
    """
    ins: List[Tuple[int, int, int, int]] = [
        (_BPF_LD | _BPF_W | _BPF_ABS, 0, 0, _OFF_ARCH),
        # jt=1/jf=0. Inverted, this installs cleanly, reports Seccomp: 2, and
        # answers ENOSYS to every syscall including write(2) - measured.
        (_BPF_JMP | _BPF_JEQ | _BPF_K, 1, 0, _AUDIT_ARCH_X86_64),
        (_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_ERRNO | errno.ENOSYS),
        (_BPF_LD | _BPF_W | _BPF_ABS, 0, 0, _OFF_NR),
    ]
    for entry in _BLOCKED:
        ins.append((_BPF_JMP | _BPF_JEQ | _BPF_K, 0, 1, entry.number))
        ins.append((_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))
    ins.append((_BPF_JMP | _BPF_JEQ | _BPF_K, 0, 1, _BLOCKED_SECCOMP_SYSCALL))
    ins.append((_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))
    # Two argument-checked syscalls follow. `prctl` is denied only for
    # PR_SET_SECCOMP rather than wholesale, because prctl(PR_SET_NAME) is how
    # programs name their own threads; `ptrace` is denied for every request
    # except PTRACE_TRACEME. Each is JEQ <syscall> / LD arg0 / JEQ <value>,
    # and the `jf=3` on each JEQ is a hand-computed skip past the next four
    # instructions to whatever comes after - recounted wrongly, the program
    # still installs and reports Seccomp: 2 while denying the wrong thing.
    ins.append((_BPF_JMP | _BPF_JEQ | _BPF_K, 0, 3, _SYS_PRCTL))
    ins.append((_BPF_LD | _BPF_W | _BPF_ABS, 0, 0, _OFF_ARG0))
    ins.append((_BPF_JMP | _BPF_JEQ | _BPF_K, 0, 1, _PR_SET_SECCOMP_ARG))
    ins.append((_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))
    ins.append((_BPF_JMP | _BPF_JEQ | _BPF_K, 0, 3, _SYS_PTRACE))
    ins.append((_BPF_LD | _BPF_W | _BPF_ABS, 0, 0, _OFF_ARG0))
    ins.append((_BPF_JMP | _BPF_JEQ | _BPF_K, 1, 0, _PTRACE_TRACEME))
    ins.append((_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))
    ins.append((_BPF_RET | _BPF_K, 0, 0, _SECCOMP_RET_ALLOW))

    insns = (_SockFilter * len(ins))()
    for index, (code, jt, jf, k) in enumerate(ins):
        insns[index].code = code
        insns[index].jt = jt
        insns[index].jf = jf
        insns[index].k = k
    return list(insns), len(ins)


def unavailable_reason() -> Optional[str]:
    """Why the filter cannot be provided here, or None if it looks available.

    A *pre-flight only* check, used by `SandboxExecutor` to refuse before a
    child exists so the refusal is a policy answer with a readable message.
    It is not sufficient on its own: the definitive test is the install, and
    `apply()` still raises if the syscall refuses. Anything that reports "no
    problem" here and then fails to install fails closed, not open.

    It deliberately does not consult `/proc/sys/kernel/seccomp/actions_avail`
    or ask `SECCOMP_GET_ACTION_AVAIL`. The latter was measured returning
    `EINVAL` on this kernel from a C program with the real headers, so a
    probe built on it would refuse to run on a machine that filters
    perfectly well.
    """
    if not supported_architecture():
        return (
            f"the syscall table in sandbox/seccomp.py is x86_64 and this "
            f"machine is {architecture()}; installing it here would deny "
            f"whatever syscall happens to share those numbers"
        )
    try:
        _libc()
    except OSError as exc:
        return f"no usable libc to call seccomp(2) through: {exc}"
    return None


def apply() -> int:
    """Install the filter on **this** process. Irreversible.

    Call this in a forked child between `fork` and `execve`, and nowhere
    else: seccomp cannot be removed, so a filter installed in the assistant
    would confine the assistant.

    Returns the number of BPF instructions installed, which is not a
    formality - it is what lets a caller assert the program it thinks it
    installed is the program that reached the kernel.

    Raises `SeccompError` on any failure, with the errno named. A caller
    that required the filter must not fall back to running the command
    without it.
    """
    reason = unavailable_reason()
    if reason is not None:
        raise SeccompError(f"seccomp cannot be provided here: {reason}")

    insns, count = program()
    try:
        libc = _libc()
        libc.prctl.restype = ctypes.c_int
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_ulong, ctypes.c_ulong]
        libc.syscall.restype = ctypes.c_long
        libc.syscall.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_long,
                                 ctypes.c_void_p]
    except OSError as exc:  # pragma: no cover - unreachable where _libc worked above
        raise SeccompError(f"no usable libc to call prctl(2)/seccomp(2) through: {exc}") from exc

    # PR_SET_NO_NEW_PRIVS first, always, and before the filter: it is what
    # makes an unprivileged process allowed to install one at all, and it is
    # irreversible. Landlock needs it too, and `_run_landlock`'s wrapper
    # path sets it again, which is harmless and idempotent.
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise SeccompError(
            f"PR_SET_NO_NEW_PRIVS was refused ({errno.errorcode.get(err, err)}); "
            f"an unprivileged process cannot install a seccomp filter without it"
        )

    # `backing` and `fprog` are named locals on purpose. `_SockFprog.filter` is
    # a raw pointer, and the kernel dereferences it during the syscall; a
    # temporary passed inline is a use-after-free waiting for a refactor to
    # collect it, and a filter read from freed memory is a filter nobody has
    # reviewed.
    backing = (_SockFilter * count)(*insns)
    fprog = _SockFprog(len=count, filter=backing)
    ctypes.set_errno(0)
    rc = libc.syscall(_SYS_SECCOMP, _SECCOMP_SET_MODE_FILTER, 0, ctypes.byref(fprog))
    if rc != 0:
        err = ctypes.get_errno()
        raise SeccompError(
            f"seccomp(SECCOMP_SET_MODE_FILTER) refused a {count}-instruction "
            f"filter: {errno.errorcode.get(err, err)}. The command was not run, "
            f"because a sandbox that was asked for and could not be built is "
            f"not a sandbox."
        )
    return count


def prctl_set_seccomp_attempt() -> Tuple[int, int]:
    """Attempt the old `prctl(PR_SET_SECCOMP, ...)` install path.

    Exists so the one-way property can be *observed* rather than asserted: a
    test calls this from inside a real filtered child and requires the
    kernel to refuse with `EPERM`. `EFAULT` would mean the probe's own
    pointer was bad, which is a different failure and must not be allowed to
    read as a pass - hence the explicit `argtypes`, without which ctypes
    truncates the address to 32 bits. Returns `(rc, errno)`.
    """
    libc = _libc()
    insns, count = program()
    backing = (_SockFilter * count)(*insns)
    fprog = _SockFprog(len=count, filter=backing)
    libc.prctl.restype = ctypes.c_int
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    ctypes.set_errno(0)
    rc = libc.prctl(_PR_SET_SECCOMP, 2, ctypes.addressof(fprog), 0, 0)
    return rc, ctypes.get_errno()


def describe() -> str:
    """One sentence naming what is denied, for logs and refusals."""
    names = ", ".join(entry.name for entry in _BLOCKED + _CONDITIONAL)
    return (f"seccomp filter active: {len(_BLOCKED)} syscalls denied outright, "
            f"{len(_CONDITIONAL)} on all but one argument value, plus seccomp(2) "
            f"and prctl(PR_SET_SECCOMP) ({names})")
