"""Hardening applied inside a skill's child process before it runs anything: die with the parent, no core dumps, resource limits."""

from __future__ import annotations


import ctypes
import logging
import os

from shani_chronoa.sandbox import seccomp as _seccomp
from .limits import _PENDING_SECCOMP, _apply_profile_ceiling

logger = logging.getLogger(__name__)

#: `prctl(2)` option, and the signal it takes. `PR_SET_PDEATHSIG` makes the kernel
#: deliver a signal to this process when its parent dies. Linux-only, and not in POSIX.
_PR_SET_PDEATHSIG = 1

#: `PR_SET_DUMPABLE`. 0 makes the kernel refuse to let another process read this
#: process's memory through /proc/<pid>/mem or ptrace.
_PR_SET_DUMPABLE = 4

#: `RLIMIT_CORE`. Setting both the soft and the hard limit to 0 means the kernel
#: writes no core file however the child dies.
_RLIMIT_CORE = 4

_SIGTERM = 15

#: The parent pid a freshly forked child should still see. `preexec_fn` is handed no
#: arguments, so the executor records the parent immediately before spawning and the
#: child compares against it. Cleared as soon as `Popen` returns, because after that
#: the value is meaningless and a recycled dict would only invite confusion.
_EXPECTED_PARENT: "dict" = {}


def _die_with_parent() -> None:
    """Ask the kernel to signal this child if its parent dies.

    `start_new_session=True` is what makes the timeout fix work - it gives the child its
    own process group, so `os.killpg` reaches a whole `sh -c` tree rather than hitting
    Chronoa's own group. It also detaches the child from that group, so killing Chronoa
    does not reach it, and a backgrounded command is the reachable case: the executor
    returns at once, reports "continues running in the background", and keeps no record
    beyond that string. Measured on this machine - a backgrounded `sleep 4; touch MARKER`
    still wrote its marker long after the executor returned.

    The race is the part that is easy to get wrong. If the parent dies between `fork` and
    this call, the signal is never armed and nothing else would ever notice, so the check
    below compares `getppid()` against the pid recorded before the spawn and exits at
    once if they differ. Without it the fix covers the common case and silently misses
    the one where it matters most.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl.restype = ctypes.c_int
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_ulong, ctypes.c_ulong]
    except OSError:
        return  # no libc to ask; the child is no worse off than it was before
    try:
        if libc.prctl(_PR_SET_PDEATHSIG, _SIGTERM, 0, 0, 0) != 0:
            return  # unsupported or not permitted; not worth failing the command over
        expected = _EXPECTED_PARENT.get("pid")
        if expected is not None and os.getppid() != expected:
            os._exit(0)  # the parent died before the arm took effect
    except OSError:
        return


class _Rlimit(ctypes.Structure):
    """`struct rlimit`, both members `rlim_t` (an unsigned long)."""

    _fields_ = [("rlim_cur", ctypes.c_ulong), ("rlim_max", ctypes.c_ulong)]


def _disable_core_dumps(libc) -> None:
    """Stop the kernel writing this process out to disk, whatever kills it.

    The secrets a child here holds are real: `redactor.child_env()`
    puts cloud provider API keys into its environment, and a screenshot or a
    transcript is in its address space. A core dump writes all of that to a file
    on disk in cleartext, in a directory the user does not think of as holding
    credentials, and it does so at the worst possible moment - the command
    crashing, which is exactly when nobody is looking.

    Both halves matter and they do different jobs, which is worth being precise
    about because only one of them is load-bearing:

    - `RLIMIT_CORE = 0` is what survives `execve`. Rlimits are per-process and
      inherited across exec, so the limit the confined child starts under is the
      one the wrapped command runs with. Measured here: set to (0, 0) in a
      parent, read back as (0, 0) after an `execve`.
    - `PR_SET_DUMPABLE = 0` does NOT survive `execve`. `setup_new_exec()` resets
      it to `SUID_DUMP_USER` for any binary that is not setuid, so it covers
      only the fork-to-exec window - which is nonetheless where the Python
      interpreter holding whatever the parent passed actually lives.
      Measured here: `PR_GET_DUMPABLE` reads 0 immediately before `execve` and 1
      in the exec'd program.

    Best-effort by design: an old kernel, a hardened container refusing prctl, or
    a resource limit the caller cannot lower must leave the command runnable
    rather than raise inside `preexec_fn`, which would take every call down with
    an opaque "Exception occurred in preexec_fn".
    """
    libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)
    no_core = _Rlimit(0, 0)
    libc.setrlimit(_RLIMIT_CORE, ctypes.byref(no_core))




def _harden_child() -> None:
    """The single `preexec_fn` for every child this module spawns.

    Both spawn paths need it - `_run_host` through `Popen` and `_run_landlock`
    through `subprocess.run` - and a `preexec_fn` has to run in the forked child
    between `fork` and `exec`, which is the only place a process can change its
    own dumpable flag or lower its own rlimits at all. Anything done in the
    parent instead would harden Chronoa itself, which is the opposite of the
    intent.

    The seccomp filter goes last, and only if `_PENDING_SECCOMP` says this call
    asked for it. Last because a filter installed earlier would be in force
    while the calls above it run, and the rule is that a control must not be
    able to break the setup that installs it. It is also irreversible for the
    life of the process, which is why it cannot be a decision the parent
    reverses.
    """
    _die_with_parent()
    _apply_profile_ceiling()
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return
    libc.prctl.restype = ctypes.c_int
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    libc.setrlimit.restype = ctypes.c_int
    libc.setrlimit.argtypes = [ctypes.c_int, ctypes.POINTER(_Rlimit)]
    _disable_core_dumps(libc)
    # Raises SeccompError on failure, which the spawn paths turn into a 126.
    # Deliberately not wrapped in a bare `except`: a filter that was asked for
    # and could not be installed must not degrade into running the command
    # unfiltered, and the only way to guarantee that is to have no path that
    # swallows this.
    if _PENDING_SECCOMP.get("enabled"):
        try:
            count = _seccomp.apply()
        except _seccomp.SeccompError as exc:
            # CPython does not re-raise a `preexec_fn` exception: it collects it
            # in the child, writes "Exception occurred in preexec_fn." to the
            # error pipe and raises a bare `SubprocessError` in the parent. The
            # type and the message are both gone, so the reason is written to a
            # file the parent reads back. Measured, not assumed - the first
            # draft caught this as `except SeccompError` in the spawn path,
            # which can never fire for exactly this reason.
            report = _PENDING_SECCOMP.get("report")
            if report:
                try:
                    with open(report, "w", encoding="utf-8") as handle:
                        handle.write(str(exc))
                except OSError:
                    pass
            raise
        logger.debug("sandbox: installed a %d-instruction seccomp filter in the child",
                     count)
