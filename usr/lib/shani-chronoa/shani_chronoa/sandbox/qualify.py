"""Fail-closed qualification: prove the mechanisms before trusting them.

OpenShell does not believe its own sandbox; before relying on a mechanism it
proves the mechanism - a real allow+deny round-trip, incomplete evidence ⇒
`Err` (`openshell-sandbox-backend/src/boundary_protocol.rs:131-161`). Chronoa's
sandbox probed for support and then trusted the probe. This module is the
honest middle: a read-only qualification of what the *running kernel* and
*this process* will actually do, verified from outside, and charged with one
rule - never report a mechanism as proven on evidence it did not produce.

Why the negative direction is the whole point here: this machine runs
unprivileged, so `Seccomp: 0` in `/proc/self/status` is what a qualification
must conclude on it. Shipping that as "protected" would be the exact lie
the probe is here to stop. (The positive direction - seccomp filter installed,
NoNewPrivs set, Landlock ABI >= 3 - proves it from the same fields on a host
that grants them.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Qualification:
    """What is actually true of *this* process on *this* kernel.

    `proven` is only True for the evidence gathered; `missing` lists what
    ought to be there and is not; `unknown` lists what could not be read.
    """

    no_new_privs: Optional[bool] = None
    seccomp_mode: Optional[int] = None  # 0 off, 1 strict, 2 filter
    seccomp_filters: Optional[int] = None
    landlock_abi: Optional[int] = None
    found: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)

    @property
    def seccomp_is_filter(self) -> bool:
        return self.seccomp_mode == 2 and (self.seccomp_filters or 0) >= 1

    @property
    def proven(self) -> bool:
        """Everything Chronoa promises that does not hold is in `missing`,
        and nothing Chronoa promises is merely guessed."""
        return (self.no_new_privs is True
                and self.seccomp_is_filter
                and self.landlock_abi is not None and self.landlock_abi >= 1
                and not self.unknown)


def _proc_status() -> Optional[dict]:
    try:
        fields = {}
        for line in open("/proc/self/status", encoding="utf-8"):
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            fields[name.strip()] = value.strip()
        return fields
    except OSError as exc:
        logger.info("cannot read /proc/self/status: %s", exc)
        return None


def qualify() -> Qualification:
    """Read the proof this machine can give about itself. Runs anywhere;
    never raises, never pretends."""
    q = Qualification()
    status = _proc_status()
    if status is None:
        q.unknown.extend(["no_new_privs", "seccomp", "seccomp_filters"])
    else:
        try:
            q.no_new_privs = status.get("NoNewPrivs") == "1"
            q.seccomp_mode = int(status.get("Seccomp", "0"))
            q.seccomp_filters = int(status.get("Seccomp_filters", "0"))
        except ValueError:
            q.unknown.extend(["no_new_privs", "seccomp", "seccomp_filters"])
        else:
            if q.no_new_privs:
                q.found.append("no_new_privs")
            else:
                q.missing.append("no_new_privs")
            if q.seccomp_is_filter:
                q.found.append("seccomp_filter")
            else:
                q.missing.append("seccomp_filter")
    try:
        from shani_chronoa.sandbox.landlock import abi_version
        q.landlock_abi = abi_version()
    except Exception as exc:  # noqa: BLE001 - qualification reports, does not raise
        logger.info("Landlock probe failed: %s", exc)
        q.unknown.append("landlock")
    else:
        if q.landlock_abi is not None and q.landlock_abi >= 3:
            q.found.append("landlock_abi>=3")
        elif q.landlock_abi:
            q.found.append(f"landlock_abi={q.landlock_abi}")
        else:
            q.missing.append("landlock")
    return q
