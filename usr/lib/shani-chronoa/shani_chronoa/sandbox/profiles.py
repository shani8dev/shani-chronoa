"""Per-origin sandbox profiles: the policy layer that sits above a SandboxLevel.

A level answers *how* a command runs - which namespaces, whether the filesystem
is confined, whether privilege is available. It cannot answer *what* that command
is allowed to do, or what ceiling it runs under, for a reason that is measured
rather than argued: every skill call in Chronoa runs at `LEVEL_3_HOST_USER`,
because skills legitimately need the session bus and the display to reach
`speak` or `open_application` (see `SandboxExecutor.execute`'s own note on why
`LEVEL_3` is exempt from the confinement requirement). A level that was also
where per-origin policy lived would either break those skills or be uniform, and
uniform policy is no policy at all.

So a profile is the second axis, and `SandboxExecutor` applies it in the child
between `fork` and `execve`. Two origins exist today and they get different
profiles, which is the entire point of this module having a caller:

- `ORIGIN_USER` - somebody is at the keyboard and asked. The permissive
  profile: no tool allowlist, network permitted.
- `ORIGIN_UNATTENDED` - an armed trigger rule fired on its own. The restricted
  profile: no network, a much tighter timeout, a lower memory ceiling, and an
  explicit allowlist. A machine-state change that nobody requested and nobody
  witnessed is the behaviour most likely to erode trust, and before this it was
  indistinguishable from the user path in every respect except one field on an
  audit record.

**`cpu_limit` is shares; `RLIMIT_CPU` is a total.** They are not the same
quantity and the field's own name is a lie if read carelessly, so the conversion
lives in one place (`cpu_seconds_budget`) and is spelled out there.

**`network_access` is declared, not enforced, and says so.** It is the one field
here the executor cannot honour, so it is the one field whose presence is not
evidence of anything. The reason is concrete rather than a shrug:

- Every skill call runs at `LEVEL_3_HOST_USER` or `LEVEL_4_HOST_ROOT`, and
  neither path creates a network namespace. `SandboxConfig.allow_network` is
  consulted in exactly one place, `_run_bwrap`, and `_run_bwrap` has no callers
  - so even the level's own network flag is currently inert.
- The confined levels use `landlock.py`'s wrapper, which restricts the
  filesystem only. Landlock's network rights exist from ABI 4 (Linux 6.7) and
  are not implemented there; `landlock.py` is not this layer's file to change.
- Seccomp-filtering `socket(2)`, or `unshare(CLONE_NEWNET)` in `preexec_fn`,
  would both work in principle and neither is safe to add blind: the first
  breaks the session bus that `LEVEL_3` exists to provide, and the second needs
  a user namespace that this development box cannot create (bwrap already dies
  with "setting up uid map" here).

**This module's third bullet above is now half-answered, and the half that
changed did not change the conclusion.** `sandbox/seccomp.py` exists and is
applied in the child's `preexec_fn` when the `sandbox-seccomp-enabled` gsetting
is on (default off). It filters 29 syscalls plus `seccomp(2)` and
`prctl(PR_SET_SECCOMP)` - and `socket(2)` is deliberately **not** one of them.
The original reasoning is not superseded, it is measured: `strace` of
`python3 -c`, `sh -c 'a; b'`, `dd`, `env`, `git`, `wpctl`, `pw-record` and
`systemctl --user` found no use of any denied syscall, while a filtered child
still binds a socket, starts a thread and spawns a subprocess - which is what
Ollama, the web-search tools and the PipeWire audio stack each need. So the
network field remains *declared and unenforced*, exactly as stated, and
`network_access` is still not evidence of anything.

Rather than pick one and call it a network sandbox, `SandboxExecutor` logs a
warning naming the profile every time a no-network profile runs on a path that
cannot isolate the network. The field is therefore *reported* as unenforced on
the exact calls where that matters, rather than recorded and forgotten. The
unattended profile's tool allowlist is what actually holds the line today, and
it holds it by never admitting a network tool in the first place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED, ORIGIN_USER

logger = logging.getLogger(__name__)

#: A whole CPU, in the cgroup sense `cpu_limit` is written in. 1024 shares is
#: one core; the field was inherited with that unit and is not changed here.
SHARES_PER_CPU = 1024

#: Seconds added to the soft CPU budget to form the hard limit. The kernel sends
#: `SIGXCPU` at the soft limit and `SIGKILL` at the hard one, so a program that
#: catches or ignores `SIGXCPU` still has a hard stop. Small on purpose: the
#: soft limit is the policy, the hard limit only exists so the policy cannot be
#: argued with.
CPU_HARD_LIMIT_GRACE_SECONDS = 5


@dataclass(frozen=True)
class ResourceCeiling:
    """What one profile imposes on one run, in rlimit units.

    Already resolved against the caller's own request, so nothing downstream has
    to re-derive it - which matters because the child that applies these cannot
    afford to make a decision: it is between `fork` and `execve`, it holds
    injected cloud API keys in its address space, and the one thing it must do
    there is apply numbers it was given.
    """

    memory_bytes: int
    cpu_seconds: int
    cpu_hard_seconds: int
    timeout_seconds: int


@dataclass
class AgentProfile:
    """Defines sandbox constraints for an agent.

    Every field here is enforced by `SandboxExecutor.execute`; nothing in this
    dataclass is advisory. A profile whose limits cannot be applied makes the
    call fail rather than run unbounded, which is a deliberate departure from
    the best-effort hardening elsewhere in the executor (see
    `_disable_core_dumps`, where a failure leaves the command runnable because a
    core dump is a hazard the child may survive, where an unapplied memory
    ceiling is a promise the caller was told would be kept).
    """

    name: str
    memory_limit: int  # in MB
    cpu_limit: int  # in shares
    network_access: bool
    allowed_tools: list[str] = field(default_factory=list)
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        # A nonsensical limit is caught here rather than at `setrlimit` time,
        # because `RLIMIT_AS = 0` does not mean "no limit" - it means the child
        # cannot allocate anything at all, and it fails as a `MemoryError`
        # several frames deep inside an unrelated program. That is a
        # plausible-looking wrong answer wearing a real error's clothes.
        for label, value in (("memory_limit", self.memory_limit),
                             ("cpu_limit", self.cpu_limit),
                             ("timeout_seconds", self.timeout_seconds)):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(
                    f"profile {self.name!r}: {label} must be a positive integer, "
                    f"got {value!r}; 0 does not mean unlimited here, it means a "
                    f"child that cannot start"
                )
        if not self.name:
            raise ValueError("a profile with no name cannot be named in a refusal")

    # -- resource ceilings -------------------------------------------------

    def memory_bytes(self) -> int:
        """`memory_limit` as the `RLIMIT_AS` byte count.

        `RLIMIT_AS` and not `RLIMIT_DATA`, which is the less obvious choice and
        was measured before being made. `RLIMIT_AS` bounds the whole address
        space, so a child that maps memory it never touches is still stopped.
        The reason to worry is inheritance: the real app's address space is
        1161 MiB with a window mapped (108 MiB RSS - the rest is shared
        libraries), so a naive reading is that a 512 MB cap would be breached
        before the child ran a line. It is not, because `execve` *replaces* the
        address space, so by the time the exec'd program starts the inherited
        mappings are gone and the ceiling applies to what that program itself
        maps. Measured directly: a parent at 1046 MiB VmSize forking a child
        capped at 512 MB `RLIMIT_AS` runs a 100 MB allocation fine and dies on a
        400 MB one. Both `RLIMIT_DATA` and `RLIMIT_AS` behave the same way
        across `execve`; `RLIMIT_AS` is chosen because it is the stricter of the
        two, so the profile cannot be walked past by mapping rather than
        allocating.
        """
        return self.memory_limit * 1024 * 1024

    def cpu_seconds_budget(self, timeout_seconds: int) -> int:
        """CPU-seconds this profile allows across the whole run.

        `cpu_limit` is *shares* - a rate, `SHARES_PER_CPU` being one core - and
        `RLIMIT_CPU` is a whole-CPU-seconds *total* with no notion of a rate at
        all. The only honest way to turn a rate into a total is to multiply it
        by the wall-clock the run is already held to, so:

            cpu_seconds = shares / SHARES_PER_CPU * timeout_seconds

        1024 shares under a 30 s command budget is one core's worth of CPU for
        the entire run; 512 shares is half of it; 256 shares under 30 s is about
        8. A runaway spin loop is therefore cut off in roughly
        `cpu_seconds` of wall time on an unloaded machine, which is what the
        test for this asserts against a real child rather than against a spy on
        `setrlimit`.

        The consequence worth stating plainly: this bounds CPU *time*, not
        parallelism. `PROFILE_FULL_ACCESS`'s 4096 shares is not "four cores for
        600 s" - it is 2400 CPU-seconds, which a single-threaded program would
        need 2400 s of wall clock to spend and a four-threaded one 600 s. The
        wall clock remains the outer bound either way, so a profile that wants
        real multi-core has to raise its share count *and* be given a long enough
        timeout to spend them in.
        """
        return max(1, int(round(
            self.cpu_limit / SHARES_PER_CPU * max(1, timeout_seconds))))

    def ceiling(self, caller_timeout_seconds: int) -> ResourceCeiling:
        """The limits and timeout this profile imposes on one run.

        A profile may only **tighten**: the effective timeout is
        `min(caller, profile)`. Without that rule, wiring `PROFILE_DEFAULT` in
        would immediately double every skill call's timeout, because the profile
        says 300 s and `tools._get_sandbox_config` asks for 30 - and a
        "restrictive layer" that can loosen is not one. It also means
        `PROFILE_DEFAULT`'s 300 s ceiling never binds in the shipped tree, which
        is correct rather than dead: it is a ceiling, and the executor logs
        which of the two numbers actually bounded the run.
        """
        timeout = max(1, min(int(caller_timeout_seconds), self.timeout_seconds))
        cpu = self.cpu_seconds_budget(timeout)
        return ResourceCeiling(
            memory_bytes=self.memory_bytes(),
            cpu_seconds=cpu,
            cpu_hard_seconds=cpu + CPU_HARD_LIMIT_GRACE_SECONDS,
            timeout_seconds=timeout,
        )

    # -- the tool allowlist ------------------------------------------------

    def allows_tool(self, tool_name: Optional[str]) -> bool:
        """Whether `tool_name` is inside this profile's allowlist.

        An empty `allowed_tools` means "no allowlist", not "allow nothing" -
        otherwise a profile nobody configured would refuse every tool, which is
        the same class of surprise as a default-deny firewall rule.

        `None` is a **refusal**, not a pass. A profile with an allowlist cannot
        verify a call whose tool it was not told about, and letting that
        through would make the allowlist disappear for exactly the callers that
        forgot to identify themselves. A security control that vanishes on the
        path nobody wired up is the same shape as a control that was never
        written, which is how this module spent its whole life.
        """
        if not self.allowed_tools:
            return True
        return tool_name in self.allowed_tools

    def refusal_for(self, tool_name: Optional[str]) -> Optional[str]:
        """Why this profile refuses `tool_name`, or `None` if it does not.

        The message names the profile, because a refusal a user cannot trace
        back to a policy is indistinguishable from a skill that broke. The
        allowlist is printed with it so the answer to "then what CAN it do" is
        in the same string rather than a file away.
        """
        if self.allows_tool(tool_name):
            return None
        allowed = ", ".join(sorted(self.allowed_tools))
        return (
            f"Refused: the tool {tool_name!r} is not in the '{self.name}' sandbox "
            f"profile's allowlist, and that profile may only run: {allowed}. "
            f"Nothing was done. This profile is selected by origin, so an "
            f"unattended actuation is held to a stricter policy than one the "
            f"user asked for."
        )

    def validate_against_profile(self, call: dict) -> bool:
        """Check if a tool call is allowed by this profile.

        Kept as the dict-shaped entry point the module has always had;
        `SandboxExecutor` calls `refusal_for` directly so that a refusal can
        carry its reason, but this remains the predicate both share rather than
        two implementations of the same allowlist.
        """
        return self.allows_tool(call.get("tool_name"))


#: Read-only tools, by the criterion the allowlist is actually built on: the tool
#: observes the machine and does not change it, so a rule firing it unattended
#: cannot leave a state the user did not ask for and would have to notice to
#: undo. Read from the shipped registry rather than assumed, and every name here
#: was checked against it - a typo would be a tool that can never run, which
#: reads exactly like a tool that is broken.
#:
#: Deliberately excluded, and the reason is in each case:
#: - every network tool (`web_search`, `translate_text`, `scan_network`,
#:   `check_updates`, `recommend_model`): the unattended profile also forbids
#:   network, and an allowlist that admitted a tool the profile's other policy
#:   forbids would be a contradiction rather than a policy.
#: - every actuator that changes state (`set_volume`, `open_application`,
#:   `write_text_file`, `delete_file`, `lock_screen`, `todo_list`, ...): this is
#:   the whole point of the profile.
#: - `speak`: an output rather than a state change, and a plausible trigger
#:   actuator, but excluded because the profile's 256 MB memory ceiling is a
#:   claim about a Piper model that could not be measured on the machine this
#:   was written on. Admitting it would be inventing a limit that is known not
#:   to hold.
_READ_ONLY_TOOLS = (
    "compare_files",
    "compute_hash",
    "directory_tree",
    "disk_usage",
    "find_files",
    "find_recently_modified",
    "get_battery_status",
    "get_datetime",
    "get_file_info",
    "get_volume",
    "list_apps",
    "list_capabilities",
    "list_directory",
    "list_percepts",
    "list_processes",
    "list_services",
    "list_windows",
    "list_wifi_networks",
    "read_logs",
    "read_text_file",
    "search_file_contents",
    "system_info",
    # The one actuation an unattended rule is for. It changes nothing on the
    # machine, and the audit trail exists precisely so the user can see that it
    # fired - so an allowlist of pure observers would leave the trigger engine
    # unable to do the one thing it exists to do.
    "notify",
)


PROFILE_DEFAULT = AgentProfile(
    name="default",
    memory_limit=512,
    cpu_limit=1024,
    network_access=True,
    allowed_tools=[],
    timeout_seconds=300,
)

#: The unattended profile. `allowed_tools` used to hold `"read_only_tool"`, a
#: name in no registry, so the one field that was actually checked on this
#: profile would have refused all eighty real tools while looking configured.
#: Replaced with the real allowlist, and `timeout_seconds` moved from 60 to 15
#: because 60 was not tighter than the 30 s `tools._get_sandbox_config` actually
#: asks for, so the field could never bind: a ceiling that is above the thing it
#: is supposed to bound is not a ceiling.
PROFILE_RESTRICTED = AgentProfile(
    name="restricted",
    memory_limit=256,
    cpu_limit=512,
    network_access=False,
    allowed_tools=list(_READ_ONLY_TOOLS),
    timeout_seconds=15,
)

#: Not selected by any origin. Reachable and enforced by name, for a caller that
#: wants a different ceiling deliberately - `SandboxExecutor.execute(profile=)`
#: is how, and there is a test that runs a real child under it. Stated plainly
#: because "reachable by name" is exactly the halfway state that reads as live.
PROFILE_FULL_ACCESS = AgentProfile(
    name="full-access",
    memory_limit=2048,
    cpu_limit=4096,
    network_access=True,
    allowed_tools=[],
    timeout_seconds=600,
)

#: Not selected by any origin either, and its 128 MB ceiling is below what the
#: real app's own address space needs: measured VmData is 136 MiB once GTK and
#: the skills registry are loaded. It is enforced faithfully - the executor
#: imposes 128 MB and a child that wants more dies - so this profile is a
#: working way to fail a command, not a working read-only profile. Kept rather
#: than quietly raised, because choosing the right number is a product decision
#: and a constant silently rewritten in the same commit that starts using it is
#: how a ceiling stops meaning anything.
PROFILE_READ_ONLY = AgentProfile(
    name="read-only",
    memory_limit=128,
    cpu_limit=256,
    network_access=False,
    allowed_tools=list(_READ_ONLY_TOOLS),
    timeout_seconds=30,
)

_PROFILES = {
    "default": PROFILE_DEFAULT,
    "restricted": PROFILE_RESTRICTED,
    "full-access": PROFILE_FULL_ACCESS,
    "read-only": PROFILE_READ_ONLY,
}


def get_profile(name: str) -> AgentProfile:
    """Get a profile by name.

    An unknown *name* falls back to the permissive profile, because a mistyped
    profile name is a configuration error and silently refusing every tool would
    break the assistant for a reason nobody could reproduce. Note that this is
    deliberately the opposite of `profile_for_origin`, which fails *closed* -
    the reasoning is there.
    """
    if name not in _PROFILES:
        logger.warning("Unknown profile %s, falling back to default", name)
        return PROFILE_DEFAULT
    return _PROFILES[name]


def profile_for_origin(origin: str) -> AgentProfile:
    """The profile a call of this origin runs under. This is the live mapping.

    `ORIGIN_UNATTENDED` gets `PROFILE_RESTRICTED`; everything else gets
    `PROFILE_DEFAULT`. So an unrecognised origin is treated as unattended rather
    than as user - the opposite of `get_profile`'s fallback, and the asymmetry is
    the point: a mistyped *profile name* is a typo, while an origin nobody
    recognises means nobody can say whether a human asked for this, and the
    permissive profile has to be opted into by name rather than reached by
    accident. `tools.py` is the only producer of an origin today
    (`ORIGIN_USER` default, `ORIGIN_UNATTENDED` from the trigger engine), so
    this fallback is defence against a future caller, not a live path.
    """
    if origin == ORIGIN_UNATTENDED:
        return PROFILE_RESTRICTED
    if origin != ORIGIN_USER:
        logger.warning(
            "Unrecognised origin %r; running it under the restricted profile "
            "because the permissive one must be asked for by name", origin)
        return PROFILE_RESTRICTED
    return PROFILE_DEFAULT
