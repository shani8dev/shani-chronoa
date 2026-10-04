"""Sense: the limits this process is actually held to, if any.

`memory` reports what the kernel sees as RAM, and `cpu` reports what the
processor is doing. Neither one is the question that matters when an OOM kill
happens or an LLM model will not load: **what is this process allowed to use?**
A container is routinely capped at 2 GiB and 0.5 CPU on a machine with 64 GiB and
16 cores, and every one of the machine-level senses will report the host
figures. Without this sense, "why did it get OOM-killed" is unanswerable from
anything else in the set.

Two hierarchies, because a machine may be on either and the layout moved in
2015:

- **cgroup v2** - the unified hierarchy, one file per controller directly under
  the mount: `/sys/fs/cgroup/memory.max`, `cpu.max`, `pids.max`.
- **cgroup v1** - one directory per controller, and the CPU limit is a *pair*
  of files (`cpu.cfs_quota_us` and `cpu.cfs_period_us`) that mean nothing
  individually. A v1 CPU quota of -1 is "no quota", not "minus one second".

**The literal string `max` means unlimited, and is reported as unlimited.** It
is not a large number, and the tempting conversion of `max` to a huge integer is
how a container reports a ceiling of 18 exabytes and a memory sense concludes
the machine is roomy. Likewise a v1 memory limit at the kernel's page-counter
maximum (`PAGE_COUNTER_MAX`, which shows up as ~9223372036854771712) is the
"no limit" sentinel, not a real limit.

**`MemTotal` is never used as a fallback ceiling, and that is the specific
failure this file exists to prevent.** A sense that could not read the cgroup
and fell back to physical memory would report a 64 GiB ceiling on a container
hard-capped at 2 GiB - a confident, plausible, completely wrong answer to the
one question asked. There is no fallback: if `/sys/fs/cgroup` is not there, the
answer is UNKNOWN.

Process limits are also worth separating from host limits. `/sys/fs/cgroup` at
the root is the *root* cgroup, so on a plain systemd machine with no delegation
what is read is the host's own limits - which are unlimited - and that is a
different fact from what a container would see. The current process's own
cgroup is reported too, from `/proc/self/cgroup`, so the two are not confused.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_PROC_SELF_CGROUP = Path("/proc/self/cgroup")

#: cgroup v1's "no limit" sentinel for memory. The kernel's
#: `PAGE_COUNTER_MAX` on a 64-bit box, and a value at or above this is the
#: absence of a limit rather than a limit of 9.2 exabytes.
_V1_UNLIMITED_THRESHOLD = 1 << 60

#: A v1 quota of -1 is the absence of a quota.
_V1_NO_QUOTA = -1


def read_memory_max() -> Optional[dict]:
    """`memory.max` (v2) or `memory.limit_in_bytes` (v1), or None.

    `{"limit": <bytes|None>, "unlimited": <bool>}` when a limit was found, and
    None when the file is not there - a machine on the other hierarchy, or a
    container without the memory controller delegated to it.
    """
    raw = sysfs.read_text(_CGROUP_ROOT / "memory.max")
    if raw is not None:
        if raw == "max":
            return {"limit": None, "unlimited": True, "file": "memory.max (v2)"}
        value = _as_int(raw)
        if value is None:
            return None
        return {"limit": value, "unlimited": False, "file": "memory.max (v2)"}

    raw = sysfs.read_text(_CGROUP_ROOT / "memory" / "memory.limit_in_bytes")
    if raw is not None:
        value = _as_int(raw)
        if value is None:
            return None
        unlimited = value >= _V1_UNLIMITED_THRESHOLD
        return {
            "limit": None if unlimited else value,
            "unlimited": unlimited,
            "file": "memory/memory.limit_in_bytes (v1)",
        }
    return None


def read_cpu_max() -> Optional[dict]:
    """`cpu.max` (v2) or the v1 quota/period pair, or None.

    v2 is two whitespace-separated fields: quota then period, and the quota alone
    is the literal `max` for unlimited. v1 is two *files*, and a -1 quota means
    no quota at all.
    """
    raw = sysfs.read_text(_CGROUP_ROOT / "cpu.max")
    if raw is not None:
        parts = raw.split()
        if not parts:
            return None
        quota = parts[0]
        period = _as_int(parts[1]) if len(parts) > 1 else None
        if quota == "max":
            return {"cores": None, "unlimited": True, "file": "cpu.max (v2)",
                    "raw": raw}
        value = _as_int(quota)
        if value is None or not period:
            return None
        return {"cores": value / period, "unlimited": False,
                "file": "cpu.max (v2)", "raw": raw}

    quota = _as_int(sysfs.read_text(_CGROUP_ROOT / "cpu" / "cpu.cfs_quota_us"))
    period = _as_int(sysfs.read_text(_CGROUP_ROOT / "cpu" / "cpu.cfs_period_us"))
    if quota is None or period is None:
        return None
    if quota == _V1_NO_QUOTA or quota <= 0 or not period:
        return {"cores": None, "unlimited": True,
                "file": "cpu/cpu.cfs_quota_us (v1)", "raw": f"{quota} {period}"}
    return {"cores": quota / period, "unlimited": False,
            "file": "cpu/cpu.cfs_quota_us (v1)", "raw": f"{quota} {period}"}


def read_pids_max() -> Optional[dict]:
    """`pids.max` (v2) or the v1 equivalent, or None."""
    raw = sysfs.read_text(_CGROUP_ROOT / "pids.max")
    if raw is None:
        raw = sysfs.read_text(_CGROUP_ROOT / "pids" / "pids.max")
        source = "pids/pids.max (v1)"
    else:
        source = "pids.max (v2)"
    if raw is None:
        return None
    if raw == "max":
        return {"pids": None, "unlimited": True, "file": source}
    value = _as_int(raw)
    if value is None:
        return None
    return {"pids": value, "unlimited": False, "file": source}


def read_own_cgroup() -> Optional[str]:
    """This process's own cgroup path, from `/proc/self/cgroup`.

    Worth reporting because `/sys/fs/cgroup` at the mount root is the *root*
    cgroup. On a plain systemd machine that holds the host's limits, not this
    process's, and saying "unlimited" from there is a true statement about the
    wrong thing.
    """
    raw = sysfs.read_text(_PROC_SELF_CGROUP)
    if not raw:
        return None
    for line in raw.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1] in ("0", "", "name=systemd"):
            return parts[2]
    return None


def _as_int(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def read_limits() -> Optional[Dict[str, Optional[dict]]]:
    """Every limit found, or None when the cgroup filesystem is not there.

    None here means "/sys/fs/cgroup does not exist" and nothing else. It is
    explicitly not permission to report physical memory as the ceiling.
    """
    if not _CGROUP_ROOT.is_dir():
        return None
    limits: Dict[str, Optional[dict]] = {
        "memory": read_memory_max(),
        "cpu": read_cpu_max(),
        "pids": read_pids_max(),
    }
    return limits


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("cgroup"):
        return f"Not reading cgroup limits: {config.sense_allowed_reason('cgroup')}."

    limits = read_limits()
    if limits is None:
        return (
            "Cgroup limits are UNKNOWN: /sys/fs/cgroup is not present, so there "
            "is no controller to read. This is deliberately NOT answered with "
            "the machine's physical memory. A container capped at 2 GiB on a "
            "64 GiB host would otherwise report a 64 GiB ceiling, which is a "
            "confident and completely wrong answer to the only question asked."
        )

    if all(value is None for value in limits.values()):
        return (
            "Cgroup limits are UNKNOWN: /sys/fs/cgroup exists but none of "
            "memory.max, cpu.max or pids.max (nor their cgroup v1 equivalents) "
            "could be read. No controller was delegated to this cgroup, which "
            "is normal inside some containers and is not the same as being "
            "unlimited."
        )

    lines = []
    unlimited_anything = False

    memory = limits["memory"]
    if memory is None:
        lines.append("memory: UNKNOWN - no memory controller limit file was readable")
    elif memory["unlimited"]:
        unlimited_anything = True
        lines.append("memory: unlimited (the limit file literally reads 'max', "
                     "which is the absence of a limit and not a large number)")
    else:
        lines.append(
            f"memory: {memory['limit'] / 2**30:.2f} GiB ceiling "
            f"[{memory['file']}]"
        )

    cpu = limits["cpu"]
    if cpu is None:
        lines.append("cpu: UNKNOWN - no cpu controller limit file was readable")
    elif cpu["unlimited"]:
        unlimited_anything = True
        lines.append(f"cpu: unlimited [raw: {cpu['raw']}]")
    else:
        lines.append(f"cpu: {cpu['cores']:.2f} cores' worth of quota [raw: {cpu['raw']}]")

    pids = limits["pids"]
    if pids is None:
        lines.append("processes: UNKNOWN - no pids limit file was readable")
    elif pids["unlimited"]:
        unlimited_anything = True
        lines.append("processes: unlimited (the limit file literally reads 'max')")
    else:
        lines.append(f"processes: {pids['pids']} at most [pids]")

    if unlimited_anything:
        lines.append(
            "  ('unlimited' here is what the controller file says about this "
            "cgroup - it is not a claim that the host is unbounded.)"
        )

    own = read_own_cgroup()
    if own is not None:
        lines.append(
            f"  this process's own cgroup: {own} - the figures above come from "
            f"the cgroup mount root, which is the root cgroup and not "
            f"necessarily the one this process is in"
        )

    known = sum(1 for value in limits.values() if value is not None)
    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-cgroup",
        metadata={
            "cgroup_present": True,
            "limits_known": known,
            "memory_capped_bytes": (memory or {}).get("limit"),
            "memory_unlimited": (memory or {}).get("unlimited"),
            "cpu_cores": (cpu or {}).get("cores"),
            "pids_max": (pids or {}).get("pids"),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cgroup",
        "description": (
            "Report the memory, CPU and process-count limits this process is "
            "held to by its cgroup, on either cgroup v2 or v1. Reads the "
            "controller files directly: the literal string 'max' is reported "
            "as unlimited rather than as a very large number, and physical RAM "
            "is never substituted for a limit that could not be read - reports "
            "UNKNOWN instead, because a container capped far below the host's "
            "memory is exactly the case this answers."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="cgroup",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
