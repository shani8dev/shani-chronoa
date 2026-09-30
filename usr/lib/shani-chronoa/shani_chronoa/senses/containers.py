"""Sense: what containers this machine is running, and what killed them.

A machine can be in a bad way in a way no other sense can see: four containers
configured, two of them exited with 137 because the kernel OOM-killer took them,
and 9 GiB of image layers still on disk. `memory` and `cgroup` would both report
that the host has room. Nothing else in the set talks to a container runtime at
all, and "did anything get OOM-killed" is precisely the question the kernel log
answers only if you already know to go looking.

**The failure that must not happen: reporting zero containers when there is no
runtime.** Podman and Docker both speak over a Unix socket, and both a missing
binary and a present binary with an unreachable socket produce no output. That
is a machine where nobody asked anything, and it is indistinguishable - if the
empty result is accepted - from a machine with no containers, which is a
confident and completely different claim. So `read_containers()` returns `None`
for *not asked* and a list (possibly empty) for *asked and nothing there*, and
the two are rendered differently.

A non-zero exit is also UNKNOWN rather than an empty list, for the same reason:
`podman ps` exits 125 when it cannot talk to its storage or its runtime, and
that is not a machine with no containers.

**Exit 137 and 143 are read as SIGKILL and SIGTERM, not as numbers.** 137 is
128+9 and 143 is 128+15. A container stopped with `docker stop` exits 143 and a
container the OOM killer took exits 137, and those are opposite diagnoses, so
the mapping is stated rather than left for the reader. `OOMKilled` is preferred
where the runtime reports it, and its *absence* is reported as unknown rather
than as false - older `docker ps` versions do not emit the field at all.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
# Container names and images are what the user chose to run. That is the
# machine's own state, but it is a statement about their work, not its hardware.
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0
_TIMEOUT = 20

#: Podman first: on ShaniOS it is the default rootless runtime, and it does not
#: need a daemon, so it works where docker's socket is dead. `docker` is the
#: fallback rather than the reverse.
#:
#: The two use different output formats for the same question - podman's
#: `--format json` is a single JSON array, docker's `{{json .}}` is one JSON
#: object per line - which is why both are parsed here rather than normalised
#: upstream.
_RUNTIMES = (
    ("podman", ["podman", "ps", "-a", "--format", "json"]),
    ("docker", ["docker", "ps", "-a", "--format", "{{json .}}"]),
)

#: 128 + signal. A container exit code above 128 is the shell convention for
#: "killed by signal N", and these two carry opposite diagnoses.
_SIGNAL_EXIT_CODES = {137: "SIGKILL (9) - most often the OOM killer, but a "
                            "manual kill looks identical", 
                      143: "SIGTERM (15) - an orderly stop, e.g. `docker stop`"}

_MAX_LISTED = 25


def _run_cmd(argv: List[str]):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("%s failed: %s", argv[0], exc)
        return None


def _parse_containers(stdout: str) -> Optional[List[dict]]:
    """Either output format into one list, or None if it is neither.

    A malformed body is None rather than `[]`: the runtime answered, and the
    answer was not the documented shape, so this version's output is unknown.
    """
    text = (stdout or "").strip()
    if not text:
        return []

    # podman: one JSON array. docker: one JSON object per line.
    if text[0] == "[":
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return None
        return [row for row in decoded if isinstance(row, dict)] if isinstance(decoded, list) else None

    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict):
            return None
        rows.append(row)
    return rows


def read_containers() -> Optional[dict]:
    """Every container the runtime knows about, or None when none was asked.

    None means one of: no runtime binary is installed, the binary could not be
    executed, it exited non-zero, or its output was not in a shape this file
    understands. All four are "not asked", and none of them is "no containers".
    """
    for runtime, argv in _RUNTIMES:
        if shutil.which(runtime) is None:
            continue
        proc = _run_cmd(argv)
        if proc is None:
            return {"runtime": runtime, "reason": "could not be executed",
                    "containers": None}
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return {
                "runtime": runtime,
                "reason": (f"exited {proc.returncode}: "
                           + (detail[0] if detail else "no message")),
                "containers": None,
            }
        parsed = _parse_containers(proc.stdout)
        if parsed is None:
            return {"runtime": runtime,
                    "reason": "its output was not in a recognised JSON shape",
                    "containers": None}
        return {"runtime": runtime, "reason": "", "containers": parsed}
    return {"runtime": "", "reason": "no container runtime is installed",
            "containers": None}


def read_disk_usage(runtime: str) -> Optional[str]:
    """`<runtime> system df` output, or None when it cannot be read.

    Disk cost of images and volumes is the other half of the question and is
    usually where the answer is: exited containers keep their writable layers.
    """
    if not runtime or shutil.which(runtime) is None:
        return None
    proc = _run_cmd([runtime, "system", "df"])
    if proc is None or proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    return text or None


def _classify(record: dict) -> dict:
    """Name, state and - for a stopped container - why it stopped."""
    state = str(record.get("State") or record.get("state") or "").lower()
    raw_exit = record.get("ExitCode", record.get("exitcode"))
    try:
        exit_code = int(raw_exit) if raw_exit not in (None, "") else None
    except (TypeError, ValueError):
        exit_code = None

    oom = record.get("OOMKilled", record.get("oomm_killed"))
    if isinstance(oom, str):
        oom = oom.lower() == "true"
    elif oom is not None and not isinstance(oom, bool):
        oom = None

    names = record.get("Names") or record.get("names") or []
    if isinstance(names, str):
        names = [names]
    image = record.get("Image") or record.get("image") or "?"

    return {
        "name": names[0] if names else "(unnamed)",
        "image": str(image).split(":")[0] if image != "?" else "?",
        "state": state or "unknown",
        "exit_code": exit_code,
        "exit_meaning": _SIGNAL_EXIT_CODES.get(exit_code),
        "oom_killed": oom,
    }


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("containers"):
        return f"Not reading container state: {config.sense_allowed_reason('containers')}."

    result = read_containers()
    runtime = result["runtime"]
    containers = result["containers"]

    if containers is None:
        return (
            f"Containers: UNKNOWN - {result['reason']}. No container runtime "
            f"answered, so nothing was asked of the machine. This is NOT a "
            f"report of zero containers: a runtime with an unreachable socket "
            f"and a machine that has never run a container are indistinguishable "
            f"from here, and they are opposite situations."
        )

    classified = [_classify(raw) for raw in containers]
    running = [c for c in classified if c["state"] in ("running", "up")]
    lines = [f"{len(classified)} container(s) known to {runtime}: "
             f"{len(running)} running"]

    for info in classified[:_MAX_LISTED]:
        line = f"  {info['name']} ({info['image']}): {info['state']}"
        if info["exit_code"] is not None and info["state"] not in ("running", "up"):
            line += f", exit {info['exit_code']}"
            if info["exit_meaning"]:
                line += f" = {info['exit_meaning']}"
        if info["oom_killed"] is True:
            line += ", OOM-KILLED"
        elif info["oom_killed"] is None and info["state"] not in ("running", "up"):
            line += " (this runtime did not report an OOMKilled flag, so that is unknown)"
        lines.append(line)
    if len(classified) > _MAX_LISTED:
        lines.append(f"  and {len(classified) - _MAX_LISTED} more")

    killed = [c for c in classified if c["oom_killed"] is True]
    if killed:
        lines.append(
            f"  {len(killed)} container(s) were OOM-killed. An exited 137 says "
            f"the same thing but does not distinguish the OOM killer from a "
            f"manual kill - the OOMKilled flag is the one that does."
        )

    usage = read_disk_usage(runtime)
    if usage:
        lines.append("disk held by images, containers and volumes:")
        for line in usage.splitlines()[:8]:
            lines.append(f"  {line}")
    else:
        lines.append("disk usage: UNKNOWN - `<runtime> system df` did not answer")

    return _SENSE.to_percept(
        "\n".join(lines),
        source=runtime or "container-runtime",
        metadata={
            "enumerated": True,
            "runtime": runtime,
            "container_count": len(containers),
            "running_count": len(running),
            "oom_killed_count": len(killed),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "containers",
        "description": (
            "List the containers this machine knows about - running and exited - "
            "with each one's exit code, whether it was OOM-killed, and how much "
            "disk the images and volumes hold. Explains that exit 137 is SIGKILL "
            "(usually the OOM killer) and 143 is an orderly SIGTERM stop. "
            "Reports UNKNOWN - never zero containers - when no runtime binary is "
            "installed, when its socket cannot be reached, or when it exits "
            "non-zero."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="containers",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
