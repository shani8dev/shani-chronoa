"""A sandbox profile's ceilings and the seccomp request: what a level may use, clamped and explained."""

from __future__ import annotations


import logging
import os
import resource
from typing import Tuple

from shani_chronoa.sandbox.profiles import AgentProfile, ResourceCeiling
from shani_chronoa.sandbox.seccomp import SeccompError
from shani_chronoa.sandbox import seccomp as _seccomp

logger = logging.getLogger(__name__)

#: Whether the next spawned child must install a seccomp filter. `preexec_fn`
#: is handed no arguments, so the decision the parent made reaches the forked
#: child the same way the profile ceiling does: written here immediately before
#: the spawn, cleared in `execute()`'s `finally`.
_PENDING_SECCOMP: "dict" = {}


def _seccomp_requested() -> bool:
    """Whether the `sandbox-seccomp-enabled` gsetting asks for the filter.

    Read per call rather than cached at import, so a user who flips the switch
    does not have to restart the assistant - the same reason the sense
    consent gates are read per call.

    An unreadable setting is `False`, and that is the correct direction rather
    than a convenient one: the schema default is `false`, so failing to read it
    can only mean the user never turned the filter on. The opposite fallback
    would be a machine that filters every skill call because GSettings was
    unavailable, which is a much worse answer to the same error.
    """
    try:
        from shani_chronoa.config import ChronoaConfig
        return bool(ChronoaConfig().get_bool(_seccomp.SECCOMP_SETTING, False))
    except Exception as exc:  # noqa: BLE001 - a gate must never raise into a command
        logger.warning("Cannot read the %s setting; treating it as off (%s)",
                       _seccomp.SECCOMP_SETTING, exc)
        return False


def _seccomp_refusal(exc: SeccompError) -> Tuple[int, str, float]:
    """Turn an uninstallable filter into a 126 rather than an opaque exit 1.

    126 because this is the same shape as every other refusal in this executor:
    the caller asked for a sandbox, the sandbox could not exist, and the
    command did not run. A generic "Execution error on the host" would report
    the same fact in a form that reads like the command itself had failed.
    """
    return (
        126,
        f"Security error: the '{_seccomp.SECCOMP_SETTING}' setting is on, so this "
        f"command must run under a seccomp filter, and no filter was installed: "
        f"{exc}",
        0.0,
    )


def _seccomp_child_failure() -> "SeccompError | None":
    """The reason the child could not install a filter, if it wrote one.

    Paired with the handler in `_harden_child`. Returns None when no filter was
    requested, so the spawn paths can call it unconditionally on any spawn
    failure and only change their answer when this is what went wrong.
    """
    report = _PENDING_SECCOMP.pop("report", None)
    if not report:
        return None
    text = ""
    try:
        with open(report, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    finally:
        try:
            os.unlink(report)
        except OSError:
            pass
    return SeccompError(text) if text else None


class ProfileLimitError(RuntimeError):
    """A profile ceiling the child could not impose on itself.

    Distinct from `SandboxExecutionError` because it is not a policy refusal -
    the policy said yes and the mechanism could not deliver, which is a
    different thing for a caller to be told about and the reason it is not
    folded into the 126 the guards return.
    """


#: The ceilings the next spawned child must impose on itself, plus the CPU budget
#: to name if the kernel kills it. Set in the parent immediately before a spawn
#: and cleared in a `finally` after it, for the same reason `_EXPECTED_PARENT`
#: is: `preexec_fn` is handed no arguments, so a per-call ceiling can only reach
#: the forked child through a channel the parent writes first. The `cpu_seconds`
#: entry is read back inside `_run_host` to explain a `SIGXCPU`, which is why the
#: clearing `finally` sits in `execute()` rather than in the spawn helpers.
_PENDING_CEILING: "dict" = {}


def _apply_profile_ceiling() -> None:
    """Apply the resolved profile's resource ceilings to this child.

    Runs between `fork` and `execve`, which is the only window in which a
    process can lower its own limits. Rlimits are inherited across `execve`, so
    what is set here is what the exec'd program actually runs under - measured
    rather than assumed, and the measurement is the only reason to believe it: a
    child that reports `resource.getrlimit(RLIMIT_AS)` from inside the exec'd
    `python3` reads back the number the parent chose.

    Raises rather than warns, which is a deliberate break from
    `_disable_core_dumps` next to it. That one is best-effort and correct: a
    core dump is a hazard the child might survive, so an old kernel that refuses
    `prctl` should leave the command runnable. A memory or CPU ceiling is a
    promise the caller was told would be kept, and a silently unapplied one is
    exactly the "configured but not enforced" shape this layer exists to
    remove. An exception out of `preexec_fn` is caught by the spawn paths and
    returned as `Execution error on the host: ...` naming the profile; the
    command does not run, which is the correct answer to a ceiling that cannot
    be applied.
    """
    limits = _PENDING_CEILING.get("limits")
    if not limits:
        return
    for what, soft, hard in limits:
        try:
            resource.setrlimit(what, (soft, hard))
        except (ValueError, OSError) as exc:
            name = _PENDING_CEILING.get("profile", "the active")
            raise ProfileLimitError(
                f"the '{name}' sandbox profile requires a limit of "
                f"{soft} (hard {hard}) on {_limit_name(what)} that this child "
                f"could not be given: {exc}. The command was not run, because a "
                f"profile whose ceiling cannot be applied is not a profile."
            ) from exc


def _limit_name(what: int) -> str:
    for name, value in (("address space", resource.RLIMIT_AS),
                        ("CPU time", resource.RLIMIT_CPU)):
        if what == value:
            return name
    return f"resource limit {what}"


def _clamp(what: int, soft: int, hard: int) -> "tuple[int, int] | None":
    """`(soft, hard)` as this process is actually able to impose, else None.

    An unprivileged process may lower a hard limit but never raise one, so a
    profile asking for more than this process already has gets `EPERM` rather
    than the policy it was promised. Deciding that here, in the parent, is what
    lets the refusal name the profile, the ceiling it asked for and the ceiling
    this process really has - instead of surfacing as a bare `setrlimit` error
    from inside `preexec_fn`, naming none of them.
    """
    _, current_hard = resource.getrlimit(what)
    if current_hard == resource.RLIM_INFINITY:
        return (soft, hard)
    if soft > current_hard:
        return None
    return (soft, min(hard, current_hard))


def _resolve_ceiling(
    profile: AgentProfile, caller_timeout_seconds: int
) -> "tuple[ResourceCeiling, list[tuple[int, int, int]], str | None]":
    """`(ceiling, [(what, soft, hard)], refusal)` for this profile on this call.

    The ceiling comes back rather than being recomputed by the caller, so the
    numbers the child is given and the numbers the log line and the timeout use
    cannot drift apart.

    Returns a refusal string rather than raising, because a profile that cannot
    be honoured is a policy answer the caller can read - the same 126 shape the
    argv guards use - and not an exception from a sandbox helper.
    """
    ceiling = profile.ceiling(caller_timeout_seconds)
    wanted = (
        ("address space", resource.RLIMIT_AS,
         ceiling.memory_bytes, ceiling.memory_bytes),
        ("CPU time", resource.RLIMIT_CPU,
         ceiling.cpu_seconds, ceiling.cpu_hard_seconds),
    )
    limits: "list[tuple[int, int, int]]" = []
    for label, what, soft, hard in wanted:
        pair = _clamp(what, soft, hard)
        if pair is None:
            allowed = resource.getrlimit(what)[1]
            return ceiling, [], (
                f"the '{profile.name}' sandbox profile allows {soft} of {label} "
                f"but this process is already limited to {allowed}, and a hard "
                f"limit cannot be raised without privilege. Refusing rather than "
                f"running the command without the ceiling it was promised."
            )
        limits.append((what, pair[0], pair[1]))
    return ceiling, limits, None
