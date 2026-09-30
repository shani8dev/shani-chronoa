"""Bubblewrap (bwrap) and Host Sandbox Executor for Shani Chronoa.

Adapted from sayri/adapters/sandbox/executor.py with shani-chronoa paths.
"""

from __future__ import annotations

import json
import ctypes
import logging
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Tuple

from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel
from shani_chronoa.sandbox.profiles import AgentProfile, ResourceCeiling, profile_for_origin
from shani_chronoa.tool_tracking import ORIGIN_USER
from shani_chronoa.secrets_manager import secrets_manager

logger = logging.getLogger(__name__)


def _child_pythonpath() -> str:
    """`PYTHONPATH` that lets a child process import `shani_chronoa`.

    Every skill is invoked as `python3 -c "from shani_chronoa.skills.X import
    Y"`, in a *fresh* interpreter that inherits the environment but not the
    parent's `sys.path`. The package installs to `/usr/lib/shani-chronoa/`,
    which is not a default `site-packages` entry, so without this the child
    dies with `ModuleNotFoundError: No module named 'shani_chronoa'` before the
    skill runs at all.

    This was invisible for the life of the project for two compounding reasons.
    The launcher scripts fix `sys.path` with `sys.path.insert`, which affects
    only the server process and is never exported; and
    `secrets_manager.inject_environment()` copies the parent environment, so a
    developer who happened to have `PYTHONPATH` set saw every call succeed
    while a real `.desktop` launch saw every call fail. The whole MCP surface
    was affected - `tools/list` answered with all 27 tools and every single
    `tools/call` failed.

    Computed in one place because the confined and host paths each had their own
    copy, and the host one was missing. An existing `PYTHONPATH` is appended to
    rather than replaced, so a caller's own entries still resolve.
    """
    package = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    existing = os.environ.get("PYTHONPATH", "")
    return f"{package}{os.pathsep}{existing}" if existing else package



def _bwrap_usable() -> bool:
    """Whether bubblewrap can actually create the namespaces it needs.

    Presence is not capability. A host can have bwrap installed and still be
    unable to create a user namespace - a hardened kernel, most container
    runtimes, and this development box, where it dies with "setting up uid map"
    or "loopback: Failed RTM_NEWADDR". Gating on shutil.which() alone therefore
    made every confined command fail on exactly the machines Landlock exists to
    serve. Probed once, then cached: the answer cannot change mid-process.
    """
    global _BWRAP_USABLE
    if _BWRAP_USABLE is not None:
        return _BWRAP_USABLE
    if not shutil.which("bwrap"):
        _BWRAP_USABLE = False
        return False
    try:
        probe = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev", "--proc", "/proc", "--", "true"],
            capture_output=True, timeout=10,
        )
        _BWRAP_USABLE = probe.returncode == 0
    except Exception:  # noqa: BLE001 - unusable is the answer, not an error
        _BWRAP_USABLE = False
    return _BWRAP_USABLE


_BWRAP_USABLE = None


def _landlock_abi() -> int:
    """Landlock ABI version, or 0 when the kernel has no Landlock.

    Resolved per call rather than cached: the answer depends on the kernel,
    and a cached 0 would keep reporting 'unavailable' on a host that has it.
    """
    try:
        from shani_chronoa.sandbox.landlock import abi_version

        return abi_version()
    except Exception:  # noqa: BLE001 - absence is the answer, not an error
        return 0

DANGEROUS_BINARIES = ("mkfs", "dd", "shutdown", "reboot", "mount", "umount")


#: A leading `VAR=value` word, as in `FOO=bar python3 ...`.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Programs whose meaning depends on a script string this module cannot resolve.
_SHELL_PROGRAMS = ("sh", "bash", "dash", "zsh", "ksh")

#: `env` options that take no value. From `env --help` on this machine (GNU
#: coreutils 9.4), including `-` - which env documents as "a mere - implies -i",
#: i.e. an option and emphatically not a terminator.
_ENV_FLAGS = frozenset({
    "-i", "--ignore-environment",
    "-0", "--null",
    "-v", "--debug",
    "--help", "--version",
    "--list-signal-handling",
    "-",
})

#: `env` short options that take no value, derived from `_ENV_FLAGS` rather than
#: written out again: the two lists were once separate and the short one had
#: drifted, which made `-iu` unreadable and turned a refusal into a wrong answer.
_ENV_SHORT_FLAGS = frozenset(
    name[1] for name in _ENV_FLAGS
    if len(name) == 2 and name.startswith("-") and not name.startswith("--")
)

#: `env` short options that take a value, each paired with the long form it
#: stands for: `-u`/`--unset`, `-C`/`--chdir`, `-S`/`--split-string`. They take
#: the rest of the token as the value: `-uPATH` unsets PATH and `-u PATH` unsets
#: PATH, while `-u=PATH` unsets `=PATH` (measured) - so the value is the remainder
#: of the token and never anything after an `=`.
_ENV_SHORT_VALUED = frozenset("uCS")

#: `env` long options that require a value, accepting both `--unset PATH` and
#: `--unset=PATH`. `--split-string` takes its value and splits it into several
#: arguments, but for naming the program it is one consumed token either way.
_ENV_VALUED = frozenset({"--unset", "--chdir", "--split-string"})

#: `env` long options whose value is optional, and therefore only ever accepted
#: after an `=` - GNU getopt cannot take a detached one, which is why the token
#: after a bare `--block-signal` is the command.
_ENV_OPTIONAL_VALUED = frozenset({"--block-signal", "--default-signal",
                                  "--ignore-signal"})

#: Constructs that hide the real program from any static reading of a script.
_EXPANSION = ("$(", "`", "${")

#: Launchers that return immediately instead of waiting for the program.
_BACKGROUND_PROGRAMS = ("gtk-launch", "xdg-open")

#: Chronoa's own management tools, which an isolated sandbox may not reach.
#: Named once because guard 3 and guard 6 both apply it and the two must not
#: drift apart - that is how the shell opt-in came to bypass the escalation
#: check in the first place.
_INTERNAL_BINARIES = ("shani-skills", "shani-plugins", "shani-settings",
                      "shani", "pkill", "killall")


def _env_splits_its_argument(word: str) -> bool:
    """True for `env -S`, which hides the program inside a string.

    `env -S "dd of=/tmp/x"` word-splits its own argument and execs whatever
    falls out, so the program is not a token in this argv and no walk over
    tokens can reach it. Treating `-S` as an ordinary value-taking option made
    `_program(['env','-S','dd'])` consume `dd` as the option's argument and
    return `''` - a name no blocklist holds, so all four guards passed and the
    real dd ran and wrote. Measured at LEVEL_3_HOST_USER before this check.

    Refused rather than parsed, for the reason the rest of this module is built
    on: guessing where a program sits inside an arbitrary string is the failure
    mode, not the fix. `env -S` exists for shebang lines, which no skill here
    emits, so nothing legitimate is lost.
    """
    if word in ("-S", "--split-string"):
        return True
    if word.startswith("--split-string="):
        return True
    return word.startswith("-S") and len(word) > 2


def _env_option_width(word: str) -> "int | None":
    """How many argv entries `word` swallows as an `env` option, or None.

    None means "this is not an `env` option at all", which is the ordinary case:
    the caller has found the command. A short option is one word, so its width is
    1 or 2; a long option's width depends on whether it was given a detached
    value, because `--unset PATH` and `--unset=PATH` both appear in real argv.

    Short options cluster, and the first letter that wants a value takes the rest
    of the token - `-iu PATH` unsets PATH, `-ui PATH` unsets `i`. Both measured
    against the real `env`. That walk only ever runs on a token that begins with
    `-`: without that guard it reads the *command* as a cluster, so `env -i sudo`
    consumed `su` plus the token after it and named neither.

    An option this table does not know is reported as *not an option*, so the
    caller's next step is to refuse the whole call rather than guess. That
    asymmetry is the point: an option skipped when it should have been obeyed
    lands the walk on an argument, and an argument is a name no blocklist holds;
    the reverse mistake cannot happen. The cost is that GNU's long-option
    abbreviation (`env --ignore-e dd`, which really does run dd) is refused
    instead of understood. A spelling nobody types, refused loudly, beats a
    spelling a blocklist cannot see.
    """
    if word == "--":
        return 1
    if word.startswith("--"):
        name, sep, _ = word.partition("=")
        if name in _ENV_VALUED:
            return 1 if sep else 2
        if name in _ENV_OPTIONAL_VALUED:
            return 1
        if name in _ENV_FLAGS:
            # `env --ignore-environment=true` is an error, not a flag with a
            # value, so a flag carrying one is malformed rather than accepted.
            return None if sep else 1
        return None
    if word in _ENV_FLAGS:
        return 1
    if not word.startswith("-"):
        return None
    for position, letter in enumerate(word[1:], start=1):
        if letter in _ENV_SHORT_VALUED:
            return 1 if position < len(word) - 1 else 2
        if letter not in _ENV_SHORT_FLAGS:
            return None
    return 1


def _program(argv: "list[str]") -> "str | None":
    """The program this argv will actually exec, or None if it cannot be read.

    `env FOO=bar python3 ...` and `FOO=bar python3 ...` both run python3, so a
    guard that read only argv[0] would see `env`, match nothing, and wave the
    real program straight through. Leading assignments and a leading `env` are
    therefore skipped to find the program the kernel will exec.

    Getting past `env` means getting past its *options*, which are not
    assignments. The previous version skipped assignments and one `env` and then
    stopped at the next token, so it named the option: `_program(["env","-i",
    "dd"])` was `-i`, which matches no blocklist, and all four guards read it.
    Measured against the real executor at LEVEL_3_HOST_USER before the fix, `env
    -i dd`, `env -- dd`, `env -u PATH dd` and `env env dd` each ran the real dd
    and wrote the file it was pointed at, and `env -i sudo` and `env -i pkexec`
    each ran the real privilege escalator. So this walks `env`'s whole option
    grammar to the first token that is not one.

    Three `env` behaviours make a hand-rolled walk wrong if they are missed, all
    measured against GNU coreutils 9.4 on this machine rather than assumed:

    - An assignment ends option parsing. `env FOO=bar -i echo` execs `-i`, not
      `echo` - so options after an assignment must not be skipped, or the
      resolver names a program that never runs.
    - Short options cluster and the first value-taking letter consumes the rest
      of the token, so `-iu PATH` and `-ui PATH` mean different things.
    - A repeated `env` is a launcher too. `env env dd` really does exec dd, one
      `env` deeper, and a resolver that stopped at the outer `env` named a
      program that merely forwards to the dangerous one.

    Returns None - never a guess - when the argv's program cannot be read, which
    `execute()` treats as a refusal. That happens for a malformed option (`env
    -u` with no NAME to unset, a flag handed a value it does not take) and for an
    option this table does not know. Both are cases where the two-ended-token
    problem bites: `-u` and `-C` take an argument, so a bare `-u` is not a
    program, it is half an option; and there is no safe way to skip an option of
    unknown shape, because skipping too much walks straight past the program
    while skipping too little merely misses a blocklist that was never going to
    match anyway.

    An argv that names no program at all - `env -i`, which prints its empty
    environment and exits 0 - returns "". That is not the same answer as None and
    must not be: refusing it would be refusing working code, and the fail-closed
    rule has to stop at malformed rather than at empty.

    HONEST SCOPE. This closes the resolver, which is what commit 7af8265's four
    guards are built on; it does not describe a hole that was live. Both
    production call sites set argv[0] to a literal (`tools.py` builds
    `["python3","-c",...]`, `argfile.py` likewise), and the LLM controls the
    program *string*, not argv[0]. Before this change `env -i dd` reached the real
    dd through `execute()` in isolation, and could not have reached it from either
    production caller. It becomes live the moment some caller builds an argv an
    LLM can influence - which is why it is fixed as a structural property of the
    resolver rather than as a patch at one call site.
    """
    index = 0
    inside_env = False
    options_parse = False
    while index < len(argv):
        word = argv[index]
        if os.path.basename(word) == "env":
            # Transparent whenever it is the command of the env before it, even
            # behind a `--`: the inner env then runs its own program, so the
            # walk continues into *its* options rather than stopping here.
            inside_env = True
            options_parse = True
            index += 1
            continue
        if _ENV_ASSIGNMENT.match(word):
            if inside_env:
                options_parse = False
            index += 1
            continue
        if inside_env and options_parse:
            if _env_splits_its_argument(word):
                return None  # the program is inside a string, not in this argv
            width = _env_option_width(word)
            if width is not None:
                if word == "--":
                    # The terminator ends option parsing for good, so the next
                    # token is the program even when it is shaped like an option.
                    options_parse = False
                elif index + width > len(argv):
                    return None  # a value-taking option with nothing left to take
                index += width
                continue
            if word.startswith("-") and word != "-":
                return None  # an option of unknown or malformed shape
        return word
    return ""


def _name_matches(name: str, blocked_names) -> bool:
    """Whether `name` is one of `blocked_names`.

    Basename first, so `/sbin/dd` is `dd`. Still no substring matching, so `add`
    and `ddrescue` are not `dd`; and a dotted variant (`mkfs.ext4`,
    `mount.fuse`) counts as the binary it is named after, which is the form
    anyone actually types.
    """
    base = os.path.basename(name)
    return any(base == blocked or base.startswith(blocked + ".") for blocked in blocked_names)


def _blocked_binary(argv: "list[str]", blocked_names) -> "str | None":
    r"""The blocklisted program this argv would run, if any.

    argv[0] and nothing else. The previous version split a *shell string* on
    whitespace and compared tokens, which meant the check was defeated by
        # writing the name in any form a shell would expand: measured against the
        # real executor, `dd status=...` was refused with 126 while `$(echo dd)
        # status=...` and `d\d status=...` both reached the real system `dd`, which
        # then complained about its own arguments.

    Every spelling of `dd` is now either the name `_program()` reports, or an
    argv that was refused before anything ran. The `env` option spellings were
    the missing middle case: a resolver that stopped at the token after `env`
    named `-i`, `--`, `-u` and `env` for them, and none of those is in any
    blocklist, so `env -i dd` and `env -u PATH dd` reached the real dd. The two
    ways a spelling still escapes naming are both deliberate and both tested: the
    argv was refused as unreadable (`env -h dd`, `env --un PATH dd`), or real
    `env` is not running `dd` either because an assignment ended its option
    parsing (`env FOO=bar -i dd` execs `-i`). The earlier version of this
    sentence claimed there was no such spelling at all, which was false.
    """
    program = _program(argv)
    if program and _name_matches(program, blocked_names):
        return os.path.basename(program)
    return None


def _shell_script(argv: "list[str]") -> "str | None":
    """The script of an explicit `sh -c ...` argv, or None if this is not one.

    The shell stays reachable on purpose, because a blocklist never could police
    a shell string - so the honest move is to make the shell *visible* in the argv
    rather than keep a filter that appears to work. Everything below exists to
    stop that visibility from being a hole.
    """
    program = _program(argv)
    if not program or os.path.basename(program) not in _SHELL_PROGRAMS:
        return None
    try:
        return argv[argv.index("-c") + 1]
    except (ValueError, IndexError):
        return None


def _unresolvable_script(script: str) -> "str | None":
    """Why a literal shell script cannot be policed, or None if it can be.

    An expansion construct makes the program unknowable by reading the text: that
    is precisely how the previous string filter was walked past, since
    `$(echo dd) ...` names no blocked binary anywhere in the string. Rather than
    guess, the script is refused. The cost is real and deliberate - `sh -c "echo
    $(date)"` is refused too - and the alternative is a check that silently fails
    on the input it exists to catch.
    """
    for construct in _EXPANSION:
        if construct in script:
            return (
                f"an explicit shell script contains {construct!r}, so the program it "
                "would run is not knowable from the argv; pass the program directly "
                "instead of a shell string"
            )
    return None


class SandboxExecutionError(Exception):
    pass


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

#: The kernel's signal for a soft `RLIMIT_CPU` breach. Its default action is to
#: terminate, so a child that outruns a profile's CPU budget dies here rather
#: than being reported as an ordinary non-zero exit.
_SIGXCPU = 24

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

    The secrets a child here holds are real: `secrets_manager.inject_environment()`
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
      interpreter that `secrets_manager` injected keys into actually lives.
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


#: `network_access=False` is declared by every restricted profile and enforced by
#: none of them - see the module docstring of `profiles.py` for why no path in
#: this executor creates a network namespace. Said once per (profile, level) so
#: a long-lived assistant does not fill its own log with the same warning every
#: turn, while still naming the profile on the call where it matters.
_NETWORK_WARNING_EMITTED: "set" = set()


class SandboxExecutor:
    """Executes commands adhering to fine-grained SandboxLevel configurations."""

    def __init__(self, sandboxes_root: str | None = None) -> None:
        self.sandboxes_root = sandboxes_root or os.path.expanduser("~/.local/share/shani-chronoa/sandboxes")
        os.makedirs(self.sandboxes_root, exist_ok=True)
        self.bwrap_available = bool(shutil.which("bwrap"))

    def execute(
        self,
        argv: "list[str]",
        config: SandboxConfig,
        agent_id: str = "default",
        *,
        origin: str = ORIGIN_USER,
        tool_name: "str | None" = None,
        profile: "AgentProfile | None" = None,
    ) -> Tuple[int, str, float]:
        """Executes a command under the specified sandbox level.

        `argv` is a list of arguments, never a command string. Every policy guard
        below is a property of the program the kernel will exec, which is a
        field of argv rather than something recovered by parsing text - so a
        check can no longer be defeated by writing the same name in a form a
        shell would have expanded.

        A string is refused rather than quietly shelled for exactly that reason:
        all four guards used to read a shell string, and every one of them could
        be walked past (`$(echo sudo) reboot` cleared the escalation check;
        `$(echo dd) status=...` ran the real `dd`).

        The one remaining way to lose them is to hand `execute()` an argv whose
        program cannot be read, so that is refused too rather than run blind: a
        malformed or unreadable `env` prefix returns None from `_program()`, and
        no guard can check a program nobody can name.

        `origin` selects the profile layer (`profiles.profile_for_origin`), and
        `tool_name` is the skill being run, which is what the profile's allowlist
        is written against. Neither can be recovered from `argv`: the argv is
        `python3 -c "from shani_chronoa.skills.clock import get_datetime; ..."`,
        so the tool's identity is a property of the *call*, not of the command,
        and re-deriving it by parsing the program text would rebuild exactly the
        parallel copy of the transport that the argv migration removed.
        `profile` overrides the origin's choice outright, for a caller that wants
        a different ceiling deliberately.

        All three default to today's behaviour - `ORIGIN_USER`, no tool, and the
        profile that origin maps to - so a caller that passes nothing gets the
        permissive profile and the resource ceilings it declares. That is the
        reason `memory_limit` and `cpu_limit` are enforced without any change
        outside this package: `PROFILE_DEFAULT` applies to every skill call that
        exists today. The allowlist does not, because an empty one is not a
        restriction, and `ORIGIN_UNATTENDED` is the only origin that has one.

        Returns: (exit_code, output, duration_ms)
        """
        start_time = time.monotonic()

        if isinstance(argv, str):
            return (
                126,
                "Security error: execute() takes an argv list, not a command string. "
                "A shell string is the shape the policy guards could not read; pass "
                "the program and its arguments separately.",
                0.0,
            )
        argv = [str(argument) for argument in argv]
        if not argv:
            return (126, "Security error: an empty argv has no program to execute.", 0.0)
        program = _program(argv)
        if program is None:
            return (
                126,
                "Security error: the program this argv would run cannot be read "
                "from the argv itself - a malformed `env` invocation, or an "
                "`env` option this executor does not know. Every policy guard "
                "below is a statement about the program that runs, so an argv "
                "whose program cannot be named is refused rather than run "
                "unchecked.",
                0.0,
            )

        # 0. The profile layer, before anything is spawned. Placed here so a
        # refusal is a refusal and not a command that ran and reported failure
        # afterwards, and before the argv guards so that a tool the profile does
        # not allow is never evaluated against a blocklist it has no business
        # reaching.
        active = profile if profile is not None else profile_for_origin(origin)
        refusal = active.refusal_for(tool_name)
        if refusal is not None:
            logger.warning("Refusing %r under profile %s", tool_name, active.name)
            return (126, refusal, 0.0)
        if not active.network_access:
            key = (active.name, config.level.value)
            if key not in _NETWORK_WARNING_EMITTED:
                _NETWORK_WARNING_EMITTED.add(key)
                logger.warning(
                    "Profile '%s' declares network_access=False and this is NOT "
                    "enforced on the %s path: no sandbox level here creates a "
                    "network namespace (only _run_bwrap's --unshare-net would, and "
                    "it has no callers). The tool allowlist is what holds this "
                    "profile's line today. See profiles.py for why.", active.name,
                    config.level.value)

        # 1. Level 0: Total Prohibition
        if config.level == SandboxLevel.LEVEL_0_NO_EXEC:
            return (
                126,
                "Security error (LEVEL_0_NO_EXEC): This sub-agent has the LEVEL_0_NO_EXEC level configured. "
                "It has no permissions to execute commands on the system.",
                0.0,
            )

        # 2. Prevent Privilege Escalation for non-Level 4
        if _name_matches(program, ("sudo", "pkexec", "su")) \
                and config.level != SandboxLevel.LEVEL_4_HOST_ROOT:
            return (
                126,
                f"Security error: Privilege escalation attempt blocked. "
                f"The sandbox level '{config.level.value}' does not allow administrative elevation (sudo/pkexec).",
                0.0,
            )

        # 3. Block internal manager binaries in isolated sandboxes
        is_isolated = config.level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV)
        if is_isolated:
            if _name_matches(program, _INTERNAL_BINARIES):
                return (
                    126,
                    f"Security error: The internal management tool '{os.path.basename(program)}' "
                    f"is blocked in the sandbox '{config.level.value}'.",
                    0.0,
                )

        # 4. Explicit blocked binaries check
        blocked = _blocked_binary(argv, config.blocked_binaries)
        if blocked is not None:
            return (
                126,
                f"Security error: The command '{blocked}' is explicitly blocked in the agent's policy.",
                0.0,
            )

        # 5. Dangerous binary blocklist
        blocked = _blocked_binary(argv, DANGEROUS_BINARIES)
        if blocked is not None:
            return (
                126,
                f"Security error: The command '{blocked}' is blocked by the sandbox policy.",
                0.0,
            )

        # 6. The explicit shell opt-in. Guard #5 above read argv[0], which for
        # `sh -c "..."` is just `sh` - so without this the opt-in would undo the
        # whole migration: `sh -c "mkfs.ext4 /dev/sda"` names the binary in plain
        # text and used to be refused by the old string filter. A script whose
        # program cannot be read at all is refused outright; one that can be read
        # is held to exactly the same blocklists as a direct execution.
        script = _shell_script(argv)
        if script is not None:
            unresolvable = _unresolvable_script(script)
            if unresolvable is not None:
                return (126, f"Security error: {unresolvable}.", 0.0)
            script_blocked = list(config.blocked_binaries) + list(DANGEROUS_BINARIES)
            if config.level != SandboxLevel.LEVEL_4_HOST_ROOT:
                script_blocked += ["sudo", "pkexec", "su"]
            if is_isolated:
                script_blocked += list(_INTERNAL_BINARIES)
            for word in script.split():
                if _name_matches(word, script_blocked):
                    return (
                        126,
                        f"Security error: The command '{os.path.basename(word)}' inside an "
                        f"explicit shell script is blocked by the sandbox policy.",
                        0.0,
                    )

        # A level that promises isolation must never degrade to plain host
        # execution. Landlock counts: it is unprivileged and needs no user
        # namespaces, so confinement survives on hosts where bubblewrap cannot
        # create them. Only when NEITHER mechanism exists do we refuse - and we
        # refuse loudly rather than running unconfined while reporting success.
        if (
            config.level
            in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV)
            and not self.bwrap_available
            and not _landlock_abi() >= 1
        ):
            return (
                126,
                "Security error: this sandbox level needs bubblewrap or Landlock "
                "and neither is available. Install 'bubblewrap' or run on a "
                "kernel with Landlock (5.13+); refusing to run the command "
                "unconfined rather than pretending it was confined.",
                0.0,
            )

        # Resolved here, in the parent, so the decision about whether this profile
        # can be honoured is made before a child exists. A profile may only
        # tighten the caller's timeout - see `AgentProfile.ceiling`.
        ceiling, limits, ceiling_refusal = _resolve_ceiling(
            active, config.timeout_seconds)
        if ceiling_refusal is not None:
            return (126, f"Security error: {ceiling_refusal}", 0.0)
        timeout = ceiling.timeout_seconds
        logger.debug(
            "sandbox: profile=%s origin=%s level=%s tool=%r timeout=%ds "
            "(caller asked %ds, profile ceiling %ds, memory=%dMiB cpu=%ds)",
            active.name, origin, config.level.value, tool_name, timeout,
            config.timeout_seconds, active.timeout_seconds,
            active.memory_limit, ceiling.cpu_seconds)

        # 7. Level 4: Elevated Host with pkexec
        _PENDING_CEILING["limits"] = limits
        _PENDING_CEILING["profile"] = active.name
        _PENDING_CEILING["cpu_seconds"] = ceiling.cpu_seconds
        try:
            if config.level == SandboxLevel.LEVEL_4_HOST_ROOT:
                # `sudo X` becomes `pkexec X`; a program that is already pkexec is
                # left alone rather than wrapped twice.
                if os.path.basename(program) == "sudo":
                    argv = ["pkexec"] + argv[argv.index(program) + 1:]
                elif os.path.basename(program) != "pkexec":
                    argv = ["pkexec"] + argv
                return self._run_host(argv, timeout, start_time, elevated=True)

            # 8. Level 3: Host as Current User
            if config.level == SandboxLevel.LEVEL_3_HOST_USER:
                return self._run_host(argv, timeout, start_time, elevated=False)

            # Levels 1 and 2 exist to be isolated. If bubblewrap is missing they
            # cannot be, and running them anyway is the worst outcome available:
            # the caller asked for a sandbox, the sandbox silently did not exist,
            # and the only symptom is that the command worked. Refuse instead, and
            # name the package. LEVEL_3 is deliberately exempt - it means "host as
            # this user" with no isolation promised, because skills legitimately
            # need the session bus and display to reach `speak` or `open_application`.

            # 8. Level 1 & 2: confined.
            #
            # Landlock first, and it is not a fallback: it is unprivileged and needs
            # no user namespaces, so it confines on machines where bubblewrap cannot
            # run at all (hardened kernels, most container runtimes, and this
            # development container, where bwrap dies with
            # "loopback: Failed RTM_NEWADDR: Operation not permitted"). Requiring
            # bubblewrap for the isolated levels meant the levels were simply
            # unavailable wherever namespaces are restricted - the opposite of what
            # a confinement level is for.
            return self._run_landlock(
                argv, config, agent_id, timeout, start_time, use_bwrap=self.bwrap_available
            )
        finally:
            # Cleared here rather than inside the spawn helpers so that
            # `_run_host` can still read `_PENDING_CEILING` while it runs, which
            # is how a SIGXCPU gets a sentence naming the budget that caused it.
            _PENDING_CEILING.clear()

    def _run_landlock(
        self,
        argv: "list[str]",
        config: SandboxConfig,
        agent_id: str,
        timeout: int,
        start_time: float,
        use_bwrap: bool = False,
    ) -> Tuple[int, str, float]:
        """Confine via a Landlock wrapper that restricts, then execs the command.

        The wrapper is a separate program precisely so `landlock_restrict_self`
        runs in the child: it cannot be undone for the life of a process, so
        applying it here would permanently strip the app of filesystem access.
        """
        workspace = config.isolated_dir or os.path.join(self.sandboxes_root, agent_id)
        os.makedirs(workspace, exist_ok=True)

        from shani_chronoa.sandbox import landlock as _landlock

        paths = _landlock.get_default_allowed_paths(workspace)
        wrapper = os.path.join(workspace, ".chronoa-landlock-wrapper.py")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(_landlock.get_landlock_wrapper())

        # The wrapper ends in `os.execvp(sys.argv[1], sys.argv[1:])`, so the
        # command's own argv reaches the kernel untouched. This used to build a
        # shell string and run it with `shell=True`, wrapping a `/bin/sh -c` in
        # another shell - two parse steps between the policy check and the exec,
        # which is exactly where a string check stops meaning anything.
        inner_argv = [sys.executable, wrapper, *argv]
        use_bwrap = use_bwrap and _bwrap_usable()
        if use_bwrap:
            inner_argv = [
                "bwrap",
                "--ro-bind", "/", "/",
                "--bind", workspace, workspace,
                "--dev-bind", "/dev", "/dev",
                "--proc", "/proc",
                "--die-with-parent",
                "--",
                *inner_argv,
            ]

        env = secrets_manager.inject_environment()
        env.update(
            {
                "LANLOCK_ALLOWED_PATHS": json.dumps([list(p) for p in paths]),
                "PYTHONPATH": _child_pythonpath(),
            }
        )
        for key in ("HOME", "USER", "LANG", "LC_ALL"):
            if key in os.environ and key not in env:
                env[key] = os.environ[key]

        try:
            _EXPECTED_PARENT["pid"] = os.getpid()
            try:
                # The Landlock wrapper is a Python interpreter that parses the
                # allowlist and holds the injected secrets before it execs
                # anything, so it needs the same hardening the host path gets.
                # Without a preexec_fn here it was the one spawn in this module
                # that left a child's dumps enabled.
                proc = subprocess.run(
                    inner_argv, env=env, capture_output=True, text=True,
                    timeout=timeout, preexec_fn=_harden_child,
                )
            finally:
                _EXPECTED_PARENT.clear()
        except subprocess.TimeoutExpired:
            return (124, f"Error: Command timed out after {timeout}s and was terminated.", 0.0)
        except Exception as exc:  # noqa: BLE001
            return (1, f"Execution error in the confined scope: {exc}", 0.0)

        duration = (time.monotonic() - start_time) * 1000.0
        out = proc.stdout or ""
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            detail = err[-1] if err else f"exit status {proc.returncode}"
            out = out or f"ERROR(exit={proc.returncode}): {detail}"
        return (proc.returncode, out, duration)

    def _run_host(
        self, argv: "list[str]", timeout: int, start_time: float, elevated: bool = False
    ) -> Tuple[int, str, float]:
        env = secrets_manager.inject_environment()
        # Set outright, not added to the passthrough loop below. That loop only
        # copies a key when it is absent, and `inject_environment` has already
        # put the parent's PYTHONPATH there - so a passthrough entry for it
        # would silently do nothing, which is the shape of the bug this fixes.
        env["PYTHONPATH"] = _child_pythonpath()
        # GSETTINGS_* travel with the child because a skill's own consent gate
        # runs in that child, reading the same schema this process reads. Left
        # out, a run with a non-default schema directory - a build tree, or a
        # system that keeps its schemas somewhere unusual - gave the parent one
        # set of answers and the child another, so a gated skill refused for a
        # reason the parent could not reproduce. Same reasoning as PYTHONPATH
        # above: the child must import and read what the parent reads.
        # The XDG_* directories travel too, for the same reason as GSETTINGS_*:
        # a caller that redirects them - a test isolating its own writes, or a
        # run with a relocated data directory - got a child that ignored the
        # redirection and wrote to the real ~/.local/share instead. That made
        # the suite non-hermetic: a dispatched screenshot landed in the
        # developer's own home rather than the fixture directory.
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
                  "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
                  "GSETTINGS_SCHEMA_DIR", "GSETTINGS_BACKEND",
                  "GSETTINGS_BACKEND_MEMORY", "GSETTINGS_KEYFILE_BACKEND",
                  "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                  "HOME", "USER"):
            if k in os.environ and k not in env:
                env[k] = os.environ[k]

        # A trailing `&` cannot appear in an argv - it is a shell operator, and
        # there is no shell here to interpret it. A caller that genuinely wants
        # background semantics now says so explicitly with `["sh", "-c", "... &"]`,
        # which is visible in the argv instead of hidden in a string. The two
        # launchers that return immediately are recognised by program.
        program = _program(argv)
        is_bg = bool(program) and os.path.basename(program) in _BACKGROUND_PROGRAMS
        if not is_bg:
            # A trailing `&` inside an explicit shell script still means
            # background, and dropping that would make the assistant *wait* for a
            # command it used to return from at once. It stays visible in the
            # argv rather than hiding in a command string.
            script = _shell_script(argv)
            is_bg = script is not None and script.rstrip().endswith("&")
        wait_limit = min(timeout, 2.5) if is_bg else timeout

        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as tmp_out, \
             tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as tmp_err:
            try:
                _EXPECTED_PARENT["pid"] = os.getpid()
                try:
                    proc = subprocess.Popen(
                        argv, env=env,
                        stdout=tmp_out, stderr=tmp_err,
                        start_new_session=True,
                        preexec_fn=_harden_child,
                    )
                finally:
                    _EXPECTED_PARENT.clear()

                poll_start = time.monotonic()
                while time.monotonic() - poll_start < wait_limit:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.08)

                duration = (time.monotonic() - start_time) * 1000.0
                retcode = proc.poll()

                if retcode is not None:
                    tmp_out.seek(0)
                    tmp_err.seek(0)
                    out_text = tmp_out.read().strip()
                    err_text = tmp_err.read().strip()
                    combined = (out_text + "\n" + err_text).strip()

                    if retcode in (126, 127) and elevated:
                        combined = "The user cancelled or denied the graphical administrator authorization (Polkit)."
                    elif retcode < 0 and -retcode == _SIGXCPU:
                        # The kernel reports a soft `RLIMIT_CPU` breach as
                        # `SIGXCPU`, whose default action terminates. Left
                        # untranslated the caller sees exit -24, which is not
                        # distinguishable from the child having been signalled
                        # by something else - so the ceiling is named, and
                        # with it the fact that this was a policy decision.
                        budget = _PENDING_CEILING.get("cpu_seconds")
                        ceiling_note = (
                            f" after {budget} CPU-seconds"
                            if budget is not None else ""
                        )
                        combined = (
                            f"Killed by the sandbox profile's CPU-time ceiling"
                            f"{ceiling_note} (SIGXCPU), not by the command's own "
                            f"{timeout}s timeout. The command used all the CPU "
                            f"that profile allows."
                        )
                    elif retcode != 0:
                        if not combined:
                            combined = f"(Process exited with error code {retcode})"
                    elif not combined:
                        combined = f"(Command completed with exit code 0)"

                    return (retcode, combined, duration)

                if is_bg:
                    tmp_out.seek(0)
                    tmp_err.seek(0)
                    partial_out = tmp_out.read().strip()
                    partial_err = tmp_err.read().strip()
                    combined_partial = (partial_out + "\n" + partial_err).strip()

                    msg = f"Command started and continues running in the background (PID: {proc.pid})."
                    if combined_partial:
                        msg += f"\nOutput:\n{combined_partial}"

                    return (0, msg, duration)

                # Foreground command exceeded its timeout: kill the whole process
                # group, not just the direct child, or its own children survive
                # proc.kill() - rather than silently reporting success while it
                # keeps running unbounded. `start_new_session=True` puts the
                # child in its own group, which is what makes the group reachable.
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

                duration = (time.monotonic() - start_time) * 1000.0
                return (124, f"Error: Command timed out after {timeout}s and was terminated.", duration)

            except Exception as exc:
                duration = (time.monotonic() - start_time) * 1000.0
                return (1, f"Execution error on the host: {exc}", duration)

    def _run_bwrap(
        self,
        argv: "list[str]",
        config: SandboxConfig,
        agent_id: str,
        timeout: int,
        start_time: float,
    ) -> Tuple[int, str, float]:
        workspace = config.isolated_dir or os.path.join(self.sandboxes_root, agent_id)
        os.makedirs(workspace, exist_ok=True)

        bwrap_args = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--tmpfs", "/run",
            "--bind", workspace, workspace,
            "--chdir", workspace,
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
        ]

        if not config.allow_network:
            bwrap_args.append("--unshare-net")

        # Straight to the program. This used to append `["--", "bash", "-c", cmd]`,
        # which put a shell back between the policy check and the exec and made
        # this the one path the argv migration would have quietly left open.
        bwrap_args.extend(["--", *argv])

        try:
            res = subprocess.run(
                bwrap_args, capture_output=True, text=True,
                timeout=timeout,
                env=secrets_manager.inject_environment(allowed_keys=[]),
            )
            out = (res.stdout + "\n" + res.stderr).strip()
            retcode = res.returncode

            # Check for graphical display connection failures
            display_error_indicators = (
                "Failed to open display", "Cannot open display",
                "Unable to init server", "Authorization required, but no authorization protocol specified",
                "could not connect to display", "No protocol specified",
            )
            if any(ind in out for ind in display_error_indicators):
                retcode = 126
                out = (
                    f"Security/Sandbox error ({config.level.value}): Cannot open a graphical window "
                    f"from this isolated container (no access to Wayland/X11).\n"
                    f"To open desktop applications on the user's screen, LEVEL_3_HOST_USER is required.\n"
                    f"Technical detail: {out}"
                )
            elif not out:
                out = f"(Sandbox bwrap finished with exit code {retcode})"

            duration = (time.monotonic() - start_time) * 1000.0
            return (retcode, out, duration)
        except subprocess.TimeoutExpired as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (124, f"Error: Sandbox time limit ({timeout}s) exceeded.", duration)
        except Exception as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (1, f"bwrap sandbox error: {exc}", duration)
