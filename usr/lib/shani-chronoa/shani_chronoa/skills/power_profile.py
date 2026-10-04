"""Switch the machine between its power profiles.

`power-profiles-daemon` is in `shani-desktop-gnome`, `cosmic` and `plasma`, so
this is the desktop default — and absent on the server and kiosk profiles, which
is reported rather than treated as a machine with no power management.

**The profile list is read, never assumed.** The profiles a machine offers
depend on its hardware and its drivers, and the two drivers that matter here
offer different ones: `intel_pstate` provides `performance`, `balanced` and
`power-saver`, while a plain `acpi_cpufreq` driver offers the same names on a
desktop and nothing at all on some laptops. Hardcoding the list is how a skill
ends up reporting "set to power-saver" on a machine that has no such profile.
So the set is read with `powerprofilesctl list` and an unknown name is refused
with the valid ones named.

`powerprofilesctl get` is the check, and it is a **separate process** from the
one that set it — the setting is asynchronous, so reading back immediately can
race the daemon and report the previous profile. When the read-back disagrees
with what was asked for, that disagreement is reported rather than smoothed over.
"""

import logging
import re
import shutil
import subprocess
from typing import List

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_TIMEOUT = 20


def _run_ppd(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powerprofilesctl", *arguments], capture_output=True, text=True,
        timeout=_TIMEOUT, check=False,
    )


def available() -> List[str]:
    """The profiles this machine actually offers."""
    if shutil.which("powerprofilesctl") is None:
        return []
    try:
        proc = _run_ppd("list")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("powerprofilesctl failed: %s", exc)
        return []
    found = []
    for line in (proc.stdout or "").splitlines():
        # The active profile is printed as "* balanced:" with the star in
        # column 0, while the others are indented - so a regex requiring
        # leading whitespace drops exactly the profile a user is most likely to
        # ask for, which is the one already in use.
        match = re.match(r"^\s*\*?\s*([a-z][a-z0-9-]*):\s*$", line)
        if match:
            found.append(match.group(1))
    return found


def current() -> str:
    if shutil.which("powerprofilesctl") is None:
        return ""
    try:
        return (_run_ppd("get").stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_power_profile",
        "description": (
            "Switch the machine between its power profiles - typically "
            "power-saver, balanced and performance - and report which one is "
            "active now. The profile list is read from the machine rather than "
            "assumed, because which profiles exist depends on the CPU driver: "
            "switching to one the machine does not have is refused with the "
            "valid names listed. Changes the CPU and platform power behaviour, "
            "not a per-application setting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "description": (
                        "Profile to switch to, e.g. 'power-saver'. Omit to "
                        "just report the current one."
                    ),
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    if shutil.which("powerprofilesctl") is None:
        return (
            "Power profiles are unavailable: powerprofilesctl is not installed. "
            "It comes with power-profiles-daemon, which the GNOME, COSMIC and "
            "Plasma desktop profiles ship - so this is a machine running the "
            "server or kiosk profile, or one without the daemon installed. It "
            "does not mean the machine has no power management."
        )
    wanted = arguments.get("profile")
    profiles = available()
    if not wanted:
        active = current()
        if not active:
            return (
                "The power profile could not be read: power-profiles-daemon "
                "returned nothing. The daemon may not be running."
            )
        return f"Power profile: {active}. Available: {', '.join(profiles) or 'none reported'}."

    wanted = str(wanted).strip()
    if profiles and wanted not in profiles:
        return (
            f"'{wanted}' is not a profile this machine offers. "
            f"Available: {', '.join(profiles)}."
        )
    try:
        proc = _run_ppd("set", wanted)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Could not set the power profile: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return (
            f"Power profile was NOT changed to '{wanted}': "
            f"{detail or 'powerprofilesctl failed'}"
        )
    active = current()
    if active and active != wanted:
        return (
            f"Asked for '{wanted}' but the daemon reports '{active}' as active. "
            f"The change did not take - some platforms refuse a profile the "
            f"firmware does not implement, and this one did not say why."
        )
    return f"Power profile set to {active or wanted}."



def _post_condition(arguments: dict):
    """`powerprofilesctl get`, a separate process from the one that set it."""
    wanted = (arguments.get("profile") or "").strip()
    if not wanted:
        return None
    now = current()
    return now == wanted, f"read back: {now or 'nothing'}"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="set_power_profile", schema=SCHEMA, run=_run)]
