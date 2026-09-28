"""Audio devices: what the machine can play and record, and at what volume.

`wpctl status` prints a **tree**, not a flat list, and getting that wrong
produces the most damaging wrong answer available in this package: a machine
with two microphones reported as having none, which for a privacy-adjacent fact
is the wrong direction to be wrong in. The real output:

    ├─ Sinks:
    │  *   47.alsa_output.pci-0000_00_1f.3.analog-stereo  [vol: 0.80]
    ├─ Sources:
    │      55. ... Headphones Stereo Microphone  [vol: 1.00]
    │  *   56. ... Digital Microphone  [vol: 0.43]

Two details that each defeat a naive parser, and both cost me a rewrite:

- the heading is `├─ Sinks:` — a box-drawing prefix, so a test for "no spaces"
  after stripping `│` never matches and the section is skipped entirely;
- the default marker is a `*`, not a `•`, and **only the default has one**, so
  looking for a bullet finds at most one device however many exist.

**Sink and source are different things and conflating them is a privacy bug.**
A source is something that captures — microphone, HDMI input, and, on this
machine, the integrated camera, which appears as `Integrated Camera (V4L2)`.
A sink is something that plays. Reporting "2 audio devices" when the two are a
microphone and a speaker answers a question nobody asked.

`wpctl` comes from the pipewire group, which `shani-multimedia` ships on every
desktop profile, so this is the default path — and its absence is reported as
its own fact rather than folded into "no audio hardware".
"""

import logging
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa import pipewire
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

_TIMEOUT = 20
_BOX = "│├└─ "


def _run_wpctl(*arguments: str) -> Optional[subprocess.CompletedProcess]:
    # This one keeps the None-on-failure shape the rest of the sense relies on,
    # so a missing or hung wpctl degrades to UNKNOWN here rather than raising
    # mid-collection. The invocation itself is shared.
    if shutil.which("wpctl") is None:
        return None
    try:
        return pipewire.run_wpctl(*arguments)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("wpctl failed: %s", exc)
        return None


def _parse_status(text: str) -> Dict[str, List[dict]]:
    """Sinks and sources out of `wpctl status`, as two separate lists."""
    found: Dict[str, List[dict]] = {"sinks": [], "sources": []}
    if not text.strip():
        return found
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        bare = line
        for character in _BOX:
            bare = bare.replace(character, " ")
        bare = bare.replace("*", " ").strip()
        if bare.endswith(":"):
            heading = bare.rstrip(":").strip()
            section = "sinks" if heading == "Sinks" else (
                "sources" if heading == "Sources" else None)
            continue
        if section is None:
            continue
        match = re.match(r"^(\d+)\.\s+(.*)$", bare)
        if not match:
            continue
        # Every bracketed annotation comes off, not just `[vol:` — a device
        # with `[muted: yes]` and no volume annotation otherwise carries the
        # whole bracket into its name, and the name is what a person reads.
        name = re.sub(r"\s*\[[^]]*\]\s*$", "", match.group(2)).strip()
        if not name:
            continue
        volume = None
        volume_match = re.search(r"\[vol:\s*([0-9.]+)", bare)
        if volume_match:
            try:
                volume = float(volume_match.group(1))
            except ValueError:
                volume = None
        muted = None
        if "[muted:" in bare:
            muted = "yes" in bare.split("[muted:")[1].split("]")[0]
        found[section].append({
            "id": match.group(1),
            "name": name,
            "default": "*" in line,
            "volume": volume,
            "muted": muted,
        })
    return found


def read_devices() -> Dict[str, List[dict]]:
    proc = _run_wpctl("status")
    if proc is None:
        return {"sinks": [], "sources": [], "available": False}
    return _parse_status(proc.stdout or "")


def _describe(entry: dict) -> str:
    parts = [entry["name"]]
    if entry.get("volume") is not None:
        state = "" if entry.get("muted") is not True else ", muted"
        parts.append(f"volume {entry['volume']:.0%}{state}")
    elif entry.get("muted") is True:
        parts.append("muted")
    if entry.get("default"):
        parts.append("default")
    return ", ".join(parts)


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("audio"):
        return f"Not reading audio devices: {config.sense_allowed_reason('audio')}."

    if shutil.which("wpctl") is None:
        return _SENSE.to_percept(
            "Audio devices were not determined: wpctl is not installed. It "
            "comes with the pipewire package group, which shani-multimedia "
            "ships on every desktop profile - so on a desktop ShaniOS this "
            "means the audio server is not running rather than that there is "
            "no sound hardware.",
            source="wpctl-missing",
            metadata={"available": False, "sinks": 0, "sources": 0},
        )

    devices = read_devices()
    sinks = devices["sinks"]
    sources = devices["sources"]

    if not sinks and not sources:
        return _SENSE.to_percept(
            "The audio server is running but reports no sinks and no sources. "
            "That is a real state - a machine with sound hardware but no "
            "configured output - and not the same as the audio server not "
            "running.",
            source="wpctl",
            metadata={"available": True, "sinks": 0, "sources": 0},
        )

    lines = []
    for label, entries in (("plays", sinks), ("records", sources)):
        if not entries:
            continue
        lines.append(f"{label} ({len(entries)}):")
        for entry in entries:
            lines.append(f"  {entry['id']}. {_describe(entry)}")

    if any("camera" in e["name"].lower() for e in sources):
        lines.append(
            "  note: a camera appears among the recording sources, which is "
            "normal - a V4L2 device is an audio capture node to the audio "
            "server as well as a video one."
        )
    defaults = sum(1 for e in sinks + sources if e.get("default"))
    lines.append(
        f"{len(sinks)} output(s) and {len(sources)} input(s); {defaults} "
        f"marked default. Sinks play, sources record - the integrated camera "
        f"appears as a source because that is what it is to an audio server."
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="wpctl",
        metadata={
            "available": True,
            "sinks": len(sinks),
            "sources": len(sources),
            "muted": sum(1 for e in sinks + sources if e.get("muted") is True),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "audio",
        "description": (
            "Read the machine's audio devices: what it can play (sinks) and "
            "what it can record (sources), each with its volume, whether it is "
            "muted, and which is the default. Sinks and sources are reported "
            "separately and never summed, because an output and an input are "
            "not two of the same thing - and the integrated camera appears "
            "among the sources, because a V4L2 device is an audio capture node "
            "to the audio server too. A missing wpctl is reported as its own "
            "fact: on a desktop ShaniOS that means the audio server is not "
            "running, not that there is no sound hardware."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="audio",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
