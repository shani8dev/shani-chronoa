"""CPU, load and uptime — the state of the processor under the user's feet.

This is the most-asked question an assistant can be asked about a machine and
the one with the most traps per line of code. Everything here is `/proc` and
`/sys`, so no package is needed, and every claim below was measured on the
machine this was written on rather than taken from documentation.

**Load average is not CPU percentage, and the distinction is not pedantic.**
`/proc/loadavg` on this machine reads `11.70 11.45 11.27` — over 11 units of
runnable work on 8 logical CPUs, which means a heavily oversubscribed box. It
is *not* "1170% CPU", and the kernel is explicit that it counts runnable and
uninterruptible tasks, not CPU time. On a machine with one core, a load of 8
is fully saturated; on one with 64 it is idle. So the raw numbers are reported
alongside the CPU count and the ratio, and the interpretation is left to the
reader, because a sense that silently converts load into a percentage invents
a fact the kernel never provided.

**`scaling_cur_freq` is a stale cached value, not a measurement.** Measured
here: `3400000` while `scaling_max_freq=4700000` and
`base_frequency=2800000`, and it moves by tens of MHz between reads. The
kernel has only updated it on the last cpufreq governor decision, so it is
reported as an indication and never as the current clock. The *available*
governors and the energy-preference list are the actionable part, and those
are exact.

**A governor and an energy preference are different settings, and a system may
have either, both or neither.** Measured: `scaling_governor=powersave` with
`energy_performance_preference=balance_performance` and
`available_preferences: default performance balance_performance balance_power
power`. Reporting one and calling it "the power setting" would be wrong, so
both are read and either may be absent.

**The governor must be read per-policy, not from cpu0 alone.** A machine with
the `intel_pstate` driver in `active` mode exposes `energy_performance_`
instead of `scaling_`, and a hybrid laptop with a P-core and an E-core has a
different policy directory per core type. Reading cpu0 gives one policy and
reports it as the machine. This sense therefore reads cpu0's policy *and*
enumerates every distinct policy path it can find, and says so when they
differ.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 60.0
_POLL_INTERVAL = 60.0

_CPU = Path("/sys/devices/system/cpu")
_LOADAVG = Path("/proc/loadavg")
_UPTIME = Path("/proc/uptime")
_MEMINFO = Path("/proc/meminfo")


def read_counts() -> dict:
    """How many CPUs, and how many are online right now.

    `possible` is the ceiling the firmware offers and `online` is what is
    actually running - a machine with cores hot-unplugged is a real state, and
    a load average measured against `possible` would be wrong.
    """
    online = sysfs.read_text(_CPU / "online")
    possible = sysfs.read_text(_CPU / "possible")
    if online is None:
        # `online` is absent on single-cpu-range kernels; count what is there.
        try:
            running = [p.name for p in _CPU.glob("cpu[0-9]*") if p.is_dir()]
        except OSError:
            running = []
        count = len(running)
        return {"online": count, "possible": count, "sourced": "cpu directories"}

    def expand(spec: str) -> int:
        total = 0
        for part in spec.split(","):
            if "-" in part:
                low, _, high = part.partition("-")
                if low.isdigit() and high.isdigit():
                    total += int(high) - int(low) + 1
            elif part.strip().isdigit():
                total += 1
        return total

    return {
        "online": expand(online),
        "possible": expand(possible) if possible else expand(online),
        "sourced": "sysfs online/possible",
    }


def read_load() -> Optional[dict]:
    """1/5/15-minute load average and the running/total process split.

    `runnable/total` is in the same file and is the cheapest way to say what
    the load consists of: a load of 11 from 17 runnable tasks is a machine
    with too many threads, not a machine with a hot CPU.
    """
    raw = sysfs.read_text(_LOADAVG)
    if not raw:
        return None
    fields = raw.split()
    if len(fields) < 3:
        return None
    try:
        one, five, fifteen = (float(fields[0]), float(fields[1]), float(fields[2]))
    except ValueError:
        return None
    record: Dict[str, object] = {"1min": one, "5min": five, "15min": fifteen}
    if len(fields) >= 4 and "/" in fields[3]:
        runnable, _, total = fields[3].partition("/")
        if runnable.isdigit() and total.isdigit():
            record["runnable"] = int(runnable)
            record["tasks"] = int(total)
    return record


def read_uptime() -> Optional[float]:
    raw = sysfs.read_text(_UPTIME)
    if not raw:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def read_memory_pressure() -> Optional[dict]:
    """`MemAvailable` and the reclaim estimate, from `/proc/meminfo`.

    `MemFree` alone is close to useless as a health signal — a machine with
    12GB free out of 32GB can still be swapping. The kernel's own
    `MemAvailable` estimate is the one that accounts for reclaimable memory, and
    that is what is reported.
    """
    try:
        text = _MEMINFO.read_text()
    except OSError:
        return None
    wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    found: Dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key not in wanted:
            continue
        value = rest.strip().split()
        if value and value[0].isdigit():
            found[key] = int(value[0]) * 1024
    if "MemTotal" not in found or "MemAvailable" not in found:
        return None
    return found


def _policies() -> List[dict]:
    """Every cpufreq policy, so a hybrid CPU is not reduced to cpu0."""
    found: Dict[str, dict] = {}
    for policy in sorted(_CPU.glob("cpu[0-9]*/cpufreq")):
        try:
            related = ",".join(sorted(
                c.name[3:] for c in policy.parent.glob("cpu[0-9]*") if c.is_dir()
            ))
        except OSError:
            related = ""
        governor = sysfs.read_text(policy / "scaling_governor")
        preference = sysfs.read_text(policy / "energy_performance_preference")
        if governor is None and preference is None:
            continue
        key = f"{governor or '-'}/{preference or '-'}"
        record = found.setdefault(key, {
            "governor": governor,
            "preference": preference,
            "cpus": [],
        })
        if related:
            record["cpus"].append(related)
    return list(found.values())


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("cpu"):
        return f"Not reading CPU state: {config.sense_allowed_reason('cpu')}."

    counts = read_counts()
    load = read_load()
    policies = _policies()
    memory = read_memory_pressure()
    uptime = read_uptime()

    if load is None and not policies:
        return (
            "Neither /proc/loadavg nor the cpufreq policies were readable, so "
            "CPU state is undetermined. That is a fact about what could be "
            "read, not a claim that this machine has no processor."
        )

    lines = []
    online = counts.get("online", 0)
    if online:
        lines.append(f"{online} of {counts.get('possible', online)} CPU(s) online")

    if load:
        one = load["1min"]
        # Load against CPU count, labelled as a ratio and not a percentage:
        # the kernel counts runnable tasks, not CPU time.
        if online:
            lines.append(
                f"load average {one:.2f} / {load['5min']:.2f} / "
                f"{load['15min']:.2f} ({one / online:.2f} per online CPU)"
            )
        else:
            lines.append(
                f"load average {one:.2f} / {load['5min']:.2f} / {load['15min']:.2f}"
            )
        if "runnable" in load:
            lines.append(
                f"  {load['runnable']} runnable of {load['tasks']} tasks"
            )
        lines.append(
            "  (load is a count of runnable and uninterruptible tasks, not CPU "
            "percentage; a machine can be oversubscribed here while the "
            "processor is still mostly idle)"
        )

    if policies:
        for record in policies:
            detail = []
            if record["governor"]:
                detail.append(f"governor {record['governor']}")
            if record["preference"]:
                detail.append(f"energy preference {record['preference']}")
            cpus = ",".join(record["cpus"])
            lines.append("  " + ", ".join(detail) + (f" on cpu {cpus}" if cpus else ""))
        available = sysfs.read_text(_CPU / "cpu0/cpufreq/energy_performance_available_preferences")
        if available:
            lines.append(f"  available energy preferences: {available}")
        if len(policies) > 1:
            lines.append(
                f"  {len(policies)} distinct cpu policies, which is what a "
                f"hybrid P-core/E-core machine looks like"
            )
        current = sysfs.read_int(_CPU / "cpu0/cpufreq/scaling_cur_freq")
        maximum = sysfs.read_int(_CPU / "cpu0/cpufreq/scaling_max_freq")
        if current:
            scale = 1000000 if current > 100000 else 1000
            text = f"  cpu0 last reported frequency {current / scale:.2f} GHz"
            if maximum:
                text += f" of {maximum / scale:.2f} GHz maximum"
            text += " (a cached value from the last governor decision, not a live measurement)"
            lines.append(text)

    if memory:
        total = memory["MemTotal"]
        available = memory["MemAvailable"]
        lines.append(
            f"memory: {available / 2**30:.1f} GiB available of "
            f"{total / 2**30:.1f} GiB"
        )
        if "SwapTotal" in memory and memory["SwapTotal"]:
            used = memory["SwapTotal"] - memory.get("SwapFree", 0)
            lines.append(
                f"  swap: {used / 2**30:.1f} GiB of "
                f"{memory['SwapTotal'] / 2**30:.1f} GiB in use"
            )

    if uptime:
        days, rest = divmod(int(uptime), 86400)
        hours, rest = divmod(rest, 3600)
        lines.append(f"up {days}d {hours}h")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc+s cpu",
        metadata={
            "cpus_online": counts.get("online"),
            "load1": load["1min"] if load else None,
            "policies": len(policies),
            "uptime_s": uptime,
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cpu",
        "description": (
            "Read processor state: how many CPUs are online, the 1/5/15-minute "
            "load average with how many of the tasks are runnable, available "
            "memory and swap, uptime, and the CPU frequency governor and energy "
            "preference. Reports load per online CPU rather than as a "
            "percentage, because load counts runnable and uninterruptible "
            "tasks and not CPU time - a machine can be oversubscribed while the "
            "processor is still mostly idle. Reads every distinct cpufreq "
            "policy, so a hybrid P-core/E-core processor is not reduced to its "
            "first core, and states that the reported frequency is a cached "
            "value from the last governor decision rather than a live "
            "measurement."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="cpu",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
