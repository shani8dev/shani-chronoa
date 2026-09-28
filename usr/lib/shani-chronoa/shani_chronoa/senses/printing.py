"""Sense: what this machine can print to, and what it can scan from.

Read-only inventory, and deliberately split into two independent halves because
they come from two different stacks with two different availability stories on a
real install:

- printers need `cups`, which arrives with `shani-printer` and is in the
  gnome/cosmic/plasma Packages-Desktop manifests;
- scanners need `sane`, which arrives as `shani-scanner` -> `sane-airscan` ->
  `sane` (verified 2026-09-28 by reading the cached `.PKGINFO`, and
  `sane-1.4.0-4` does contain `usr/bin/scanimage`).

Neither half suppresses the other. A machine with working printers and no
scanner software is an ordinary configuration - the same machine is the reason
`shani-printer` and `shani-scanner` are separate packages - and reporting only
the half that works would make a real gap invisible.

The distinction this sense exists to get right: `lpstat -e` printing nothing
means no printers are **configured**, not that no printers **exist**. A printer
plugged in and never added in CUPS is a printer. "No printers" in that state is
the same class of error as a camera reported as disabled when it is not.

Package names here are the ARCH ones. The Arch package is `sane`; `sane-utils`
is the Debian name for the same thing and does not exist in the Arch repos, so
naming it in a refusal would send a user after a package they cannot install.

Nothing in this module prints or scans. Both have physical, paper-consuming,
privacy-relevant effects and belong in gated actuators, not in a polled sense.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense, Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
# ttl == poll, as every ambient sense now is: a shorter TTL would let the
# percept expire before the scheduler refreshes it. Enforced registry-wide by
# tests/test_sense_manifest.py::TestPollIntervalNeverExceedsTtl.
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_TIMEOUT = 20
# The Debian name for the same software. Named in a comment rather than in a
# message because a user who searches Arch for it finds nothing.
_DEBIAN_SANE_PKG = "sane-utils"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "printing",
        "description": (
            "Report which printers are configured on this machine and which "
            "scanners are detected. Read-only: it never prints or scans "
            "anything."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _query(cmd: list[str]) -> Optional[subprocess.CompletedProcess]:
    """Run a read-only query, or return None if it could not be run at all."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("%s could not be run: %s", cmd, exc)
        return None


def _printers() -> str:
    """Read-only CUPS queries only: -r state, -e destinations, -d default."""
    if shutil.which("lpstat") is None:
        return (
            "Printers: not determined - the CUPS command line tools are not "
            "installed (they come with the `cups` package, shipped by "
            "shani-printer). That is not the same as this machine having no "
            "printer."
        )

    running = _query(["lpstat", "-r"])
    if running is None:
        return "Printers: unknown - lpstat could not be run."
    if "not running" in (running.stdout or "").lower():
        return (
            "Printers: unknown - the CUPS scheduler is not running, so no "
            "printer can currently be reached. That is a stopped service, not "
            "an absence of printers."
        )

    destinations = _query(["lpstat", "-e"])
    if destinations is None:
        return "Printers: unknown - lpstat failed while listing destinations."

    names = [line.strip() for line in (destinations.stdout or "").splitlines()
             if line.strip()]
    if not names:
        return (
            "Printers: none are configured in CUPS. A printer can be plugged "
            "in and never added, so this does not mean none exist - it means "
            "none are set up."
        )

    default = _query(["lpstat", "-d"])
    default_name = ""
    if default is not None:
        text = (default.stdout or "").strip()
        if text.startswith("system default destination:"):
            default_name = text.split(":", 1)[1].strip()

    out = [f"Printers: {len(names)} configured"]
    for name in names:
        out.append(f"  {name}" + ("  (default)" if name == default_name else ""))
    if not default_name:
        out.append("  No default destination is set, so a print job would "
                   "have nowhere to go.")
    return "\n".join(out)


def _scanners() -> str:
    if shutil.which("scanimage") is None:
        return (
            "Scanners: not determined - `scanimage` is not installed (it comes "
            f"with the `sane` package, shipped by shani-scanner; "
            f"`{_DEBIAN_SANE_PKG}` is the Debian name for the same thing and is "
            f"not an Arch package). That is not the same as having no scanner."
        )

    listing = _query(["scanimage", "-L"])
    if listing is None:
        return "Scanners: unknown - scanimage could not be run."

    found = []
    for line in (listing.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("device "):
            continue
        # `device `NAME` is a DESCRIPTION`
        _, _, rest = line.partition("device ")
        name, _, description = rest.partition("`")
        description, _, _ = description.partition("` is a ")
        found.append((name.strip(), description.strip()))

    if not found:
        if listing.returncode != 0:
            return (
                "Scanners: unknown - scanimage did not list anything and "
                "exited non-zero, so whether a scanner is present is not "
                "established."
            )
        # SANE ran and reported nothing, which is a real answer and not an error.
        return "Scanners: none detected (scanimage ran and identified nothing)."

    out = [f"Scanners: {len(found)} detected"]
    for name, description in found:
        out.append(f"  {name}  {description}".rstrip())
    return "\n".join(out)


def _run(_arguments: dict) -> str:
    return "\n\n".join((_printers(), _scanners()))


_SENSE = Sense(
    name="printing",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
