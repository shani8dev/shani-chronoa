"""Sensor-contention sense: who else is holding this machine's capture devices.

Chronoa is not the only thing that can open a webcam, and it is not always the
thing holding the microphone. The user already has a vocabulary for this on
other platforms - GNOME's own privacy indicator, the OS-level "camera in use"
chip, Firefox's permission model - and Chronoa had no equivalent: it would
happily take the mic and stream audio with no way to say that something else
was also streaming, which is a question a user on a call has a genuine reason
to want answered.

The mechanism is deliberately dull, because the interesting part is not
mechanical. `fuser` reports which PIDs hold a device node open, and
`/proc/<pid>/cmdline` turns those PIDs into command lines. That is the whole
technique, and it is the same one a user would run by hand.

**What this sense deliberately does not do.** A shorter version of this
question - match process names against a list of known commercial monitoring
vendors and report which are running - produces a tool whose entire purpose
is to survey the user for surveillance software. That is a different product
from "tell me what is using my camera", and the difference is not
sophistication, it is who the output is for and what it enables. So the sense
enumerates *every* holder of every capture device and names what it finds.
It has no vendor list, no allowlist of things to worry about, and no memory of
what it saw last hour. A user who is being recorded by commercial software
gets the same complete, undifferentiated answer as a user in a video call,
because both are just processes holding a device - and the complete answer is
more useful to the person actually trying to understand their machine than
any curated subset would be.

**Latching.** A device held open for an entire meeting is a single fact worth
recording once, not once per poll, but a device that stays open across a
reboot of the offending process should be re-stated. `latch.Latch` handles
that, so a long call produces one percept instead of one per interval.

**Consent is checked before any device node is read.** The ordering is
load-bearing for the same reason it is in `senses/scheduler.py`: the check has
to come before the observation, because a filter applied after the fact would
mean the machine had already been inspected. `contention-sense-enabled` does
not exist in the shipped gschema, so `sense_allowed("contention")` is False
by default and this sense is fail-closed until the user grants it.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa import subproc
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
# Names processes the user is running, so this is more than public metadata
# and less than a percept of their world. "metadata" is not a valid tier.
SENSITIVITY = SENSITIVITY_PERSONAL
# Contention is a fact about device ownership, not about the user's world. It
# expires reasonably fast because a camera being free now says nothing about
# it in ten minutes, and a stale "in use" is actively misleading.
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 60.0

# Both node families are capture surfaces. Video is the obvious one; audio is
# the one that actually matters for a machine with a microphone, and omitting
# it would leave the common case uncovered.
#
# The audio pattern matches the ALSA `<kind>C<card>D<device><suffix>` shape
# rather than enumerating suffixes. An enumerated `p|u?m?` form looks
# equivalent and is not: it misses the `c` capture suffix (the microphone) and
# every `hwC*D*` hardware node, and `\d+` stops at a two-digit device number
# like `pcmC0D31p`. The suffix set is open-ended and the device field is not
# single-digit, so a literal enumeration is wrong by construction; matching the
# family's shape is what survives contact with a real /dev/snd.
_VIDEO_DIR = Path("/dev")
_VIDEO_NODE = re.compile(r"^video\d+$")
_SOUND_NODE = re.compile(r"^(?:control|hw|pcm|dmix|dsnoop|midi)C\d+D?\d*[a-z]*$")

_SCHEMA = {
    "type": "function",
    "function": {
        # Named for the consent surface, not for how it reads:
        # `sense_allowed()` builds the key as "<name>-sense-enabled", so a
        # rename here silently leaves the sense permanently ungrantable.
        "name": "capture",
        "description": (
            "Report which processes are currently holding this machine's "
            "capture devices open - webcams under /dev/video*, and audio "
            "nodes under /dev/snd. Read-only, and it does not act on what it "
            "finds: it names every holder of every device, with no list of "
            "programs to worry about. Omit `device` for all of them, or pass "
            "one node to inspect just that one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Restrict the report to one device node, e.g. /dev/video0. Optional; omit for all capture devices.",
                },
            },
        },
    },
}


def _dev_nodes() -> List[Path]:
    """Every capture device node currently present on this machine.

    ALSA nodes live in `/dev/snd/`, not in `/dev/` itself, so this scans both.
    Listing only `/dev` finds the webcams and no audio at all - and the audio
    half is the half that matters, because `pcmC*D*c` is the microphone.
    """
    found: List[Path] = []
    for directory, pattern in ((Path("/dev"), _VIDEO_NODE), (Path("/dev/snd"), _SOUND_NODE)):
        try:
            entries = sorted(os.listdir(directory))
        except OSError as exc:
            logger.debug("cannot enumerate %s: %s", directory, exc)
            continue
        for name in entries:
            if pattern.match(name):
                found.append(directory / name)
    return found


_FUSER_MISSING: "Optional[str]" = None


def _holders(node: Path, timeout: float = 2.0) -> List[int]:
    """PIDs holding `node` open, or None when that cannot be determined.

    `fuser` writes PIDs to stdout and signals access through its exit status,
    so a non-zero exit is normal here and must not be treated as failure - an
    unused device legitimately yields no PIDs and a non-zero status.

    The None case matters more than it looks. `fuser` comes from `psmisc`,
    which is not a Chronoa dependency and need not be installed, and an
    OSError from a missing binary is indistinguishable from "no process holds
    this". Returning [] for both made the sense report every microphone and
    every webcam as free on any machine without psmisc - which is the exact
    inverse of what it exists to tell you, and wrong in the direction that
    matters. The caller reports that as UNKNOWN rather than as "free".
    """
    global _FUSER_MISSING
    if shutil.which("fuser") is None:
        _FUSER_MISSING = "fuser (from psmisc) is not installed"
        return None
    try:
        proc = subprocess.run(
            ["fuser", str(node)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("fuser failed for %s: %s", node, exc)
        _FUSER_MISSING = f"fuser could not be run for {node}: {exc}"
        return None
    pids: List[int] = []
    for token in re.findall(r"\d+", proc.stdout or ""):
        value = int(token)
        if value > 0 and value not in pids:
            pids.append(value)
    return pids


def describe(node: Path) -> Dict[str, object]:
    """One device node and everything currently holding it open.

    `in_use` is None - not False - when the holders could not be determined,
    so a missing `fuser` can never be reported as an unused microphone.
    """
    pids = _holders(node)
    holders = [{"pid": pid, "cmdline": subproc.cmdline(pid) or ""} for pid in (pids or [])]
    return {
        "device": str(node),
        "in_use": None if pids is None else bool(pids),
        "holders": holders,
    }


def _read_name(node: Path) -> Optional[str]:
    try:
        return (node / "name").read_text().strip()
    except OSError:
        return None



def video_labels() -> dict:
    """`/dev/videoN` -> the name the kernel reports for it, where it knows one.

    Only video nodes are labelled. Sound nodes are named by their own ALSA
    identity, which is not in the filesystem, and guessing one from the node
    number would be inventing it.
    """
    out = {}
    try:
        entries = sorted(_VIDEO_DIR.iterdir())
    except OSError:
        return out
    for entry in entries:
        if _VIDEO_NODE.match(entry.name):
            out[str(entry)] = _read_name(entry)
    return out


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("capture"):
        return f"Not checking device use: {config.sense_allowed_reason('capture')}."

    requested = str(arguments.get("device") or "").strip()
    if requested:
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = Path("/dev") / candidate.name
        nodes = [candidate]
    else:
        nodes = _dev_nodes()

    reports = [describe(node) for node in nodes]
    busy = [r for r in reports if r["in_use"]]

    if not reports:
        return "No capture devices were found on this machine."

    labels = video_labels()

    if not busy and all(r["in_use"] is None for r in reports):
        # Every holder lookup failed, so nothing is known - which is not the
        # same as everything being free, and the two were previously reported
        # by different senses that could disagree with each other.
        return _SENSE.to_percept(
            "Could not determine who holds the capture devices open: "
            f"{_FUSER_MISSING or 'the holder lookup is unavailable'}. Whether "
            "anything is using the camera or microphone is UNKNOWN, not free.",
            source="device-contention",
            metadata={"busy": 0, "total": len(reports), "determined": False},
        )

    def _label(device: str) -> str:
        name = labels.get(device)
        return f"{device} ({name})" if name else device

    lines = []
    for report in reports:
        device = str(report["device"])
        if report["in_use"]:
            names = ", ".join(
                f"{h['pid']} ({h['cmdline'] or 'no command line'})"
                for h in report["holders"]
            )
            lines.append(f"{_label(device)}: in use by {names}")
        elif report["in_use"] is False:
            lines.append(f"{_label(device)}: free")
    if not busy:
        lines.append("No process holds any capture device open.")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="device-contention",
        metadata={"busy": len(busy), "total": len(reports), "determined": True},
    )


_SENSE = Sense(
    name="capture",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
