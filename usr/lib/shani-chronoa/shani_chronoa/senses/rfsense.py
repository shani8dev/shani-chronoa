"""Radio sense: sample the WiFi link and notice when the room is moving.

`/proc/net/wireless` exposes per-interface link quality, signal level and noise
for every wireless interface the kernel tracks. A body in a room perturbs the
multipath between the access point and this machine, so a moving person moves
that signal - and the *variance* of the level over time is the signal worth
reading, not its absolute value. A steady -40 dBm means a quiet room; a -40 dBm
that swings 5 dB every second means something in the room is in motion.

**What this is not, stated plainly, because the gap is the whole point.** The
research literature on radio sensing is real and substantial: RF-Pose estimates
skeletons through walls, RF-Avatar reconstructs 3D body meshes, and a 2024
paper synthesises images directly from CSI. All of that runs on *Channel State
Information* - 52+ per-subcarrier amplitude and phase values - which is not
what this reads. RSSI is a single aggregate number per interface. It is the
coarser half of radio sensing: it reliably answers *is something moving* and
cannot answer *what*, *who*, *where*, or anything else.

**And this machine cannot do the CSI half at all.** The card here is an Intel
AX201 on `iwlwifi`, which exposes no CSI to userspace - the Intel 5300 with
Nexmon that the research uses is a different, older part, and ESP32-S3 rigs
need a USB serial device that is not attached. So `sensing_ceiling()` reports
that as a first-class fact rather than letting the user assume otherwise. A
sense that implied the research result it cannot produce would be worse than
not having the sense.

Read-only and non-reconstructive: it records a number, a spread and a verdict.
No image, no mesh, no identity - and none of those are recoverable from a
scalar that has been averaged over time.
"""

import collections
import logging
import os
from pathlib import Path
from typing import Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 30.0

_WIRELESS = Path("/proc/net/wireless")

# A spread in dB below this is ordinary multipath flicker; above it, something
# large enough to move the signal is in the room. Calibrated by eye against
# this machine's idle -40 dBm reading, and stated as a threshold rather than
# buried, because a sense whose only output is a magic number is unauditable.
_MOVEMENT_SPREAD_DB = 3.0

# How many recent samples each interface keeps. Six at a 30s interval is three
# minutes, which is long enough for a person to cross a room and short enough
# that the window does not report a stale calm after they have left.
_HISTORY = 6

# Per-interface sample history, in process memory. Deliberately not persisted:
# this is a de-duplication-free running measurement, and restarting the process
# should re-calibrate against the current room rather than trust a window
# recorded minutes or days ago in different conditions.
_HISTORY_BY_IFACE: "collections.defaultdict[str, collections.deque]" = (
    collections.defaultdict(lambda: collections.deque(maxlen=_HISTORY))
)


def movement(interface: str, level: int) -> dict:
    """Record a level and report the spread of the window.

    The spread, not the level, is the measurement: an absolute -40 dBm is an
    ordinary quiet reading in one room and an unusually good one in another,
    but a level that swings several dB between samples means the multipath is
    being disturbed, which is what a moving person does.
    """
    window = _HISTORY_BY_IFACE[interface]
    window.append(level)
    levels = list(window)
    spread = max(levels) - min(levels) if len(levels) > 1 else 0.0
    return {
        "interface": interface,
        "level": level,
        "samples": len(levels),
        "spread_db": spread,
        "moving": spread >= _MOVEMENT_SPREAD_DB,
    }

_SCHEMA = {
    "type": "function",
    "function": {
        # Named for the consent surface: `sense_allowed()` builds the key as
        # "<name>-sense-enabled", so a rename here leaves the sense
        # permanently ungrantable.
        "name": "rfsense",
        "description": (
            "Sample each wireless interface's link level from "
            "/proc/net/wireless and report it, together with whether the "
            "signal level is varying enough to suggest movement in the room. "
            "RSSI only, so this is a coarse presence signal: it indicates "
            "movement, not identity, position or count, and it is not the "
            "channel-state sensing that research systems use to reconstruct "
            "poses or images."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def read_links() -> list:
    """Per-interface link level and noise, straight from the kernel.

    The file is fixed-width, and a line whose fields do not all parse is
    skipped rather than guessed at: `/proc` formats occasionally differ
    between kernels, and a fabricated level would be worse than a missing one.
    """
    try:
        text = _WIRELESS.read_text()
    except OSError as exc:
        logger.debug("cannot read %s: %s", _WIRELESS, exc)
        return []
    links = []
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        interface, _, rest = line.partition(":")
        parts = rest.split()
        if len(parts) < 4:
            continue
        # These fields are fixed-width and carry a trailing decimal point -
        # `70.` and `-40.`, not `70` and `-40`. `int()` rejects both, and
        # skipping every line on that made the sense report "no wireless
        # interface" on a machine that had one.
        try:
            status = int(parts[0])
            link = int(parts[1].rstrip("."))
            level = int(parts[2].rstrip("."))
        except ValueError:
            continue
        links.append({
            "interface": interface.strip(),
            "status": status,
            "link": link,
            "level": level,
        })
    return links


def sensing_ceiling() -> str:
    """What radio sensing this machine can actually do, and what it cannot.

    Reported because the gap between RSSI and CSI is the difference between
    "something is moving" and "reconstructing what", and a user asking whether
    their machine can do radio sensing deserves a real answer rather than an
    optimistic one.
    """
    reasons = []
    if not Path("/sys/class/net").is_dir():
        return "no network subsystem to inspect"
    wireless = [
        p.name for p in Path("/sys/class/net").iterdir()
        if (p / "wireless").is_dir()
    ] if Path("/sys/class/net").is_dir() else []
    if not wireless:
        reasons.append("no wireless interface is present")
    # iwlwifi and iwlmvm never export CSI to userspace; the Intel 5300 (the
    # part the research literature uses, via Nexmon) is a different device.
    for name in wireless:
        driver_link = Path(f"/sys/class/net/{name}/device/driver")
        try:
            driver = os.readlink(driver_link)
        except OSError:
            continue
        if "iwlwifi" in driver or "iwlmvm" in driver:
            reasons.append(
                f"{name} is on {driver.rsplit('/', 1)[-1]}, which exposes no "
                "Channel State Information; CSI radio sensing needs an "
                "ESP32-S3 rig or an Intel 5300 with Nexmon"
            )
    if not Path("/dev/ttyUSB0").exists() and not Path("/dev/ttyACM0").exists():
        reasons.append("no USB serial CSI capture device is attached")
    return "; ".join(reasons) if reasons else "an interface may expose CSI - not verified here"


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("rfsense"):
        return f"Not sampling the radio link: {config.sense_allowed_reason('rfsense')}."

    links = read_links()
    if not links:
        return (
            "No wireless interface is reporting link statistics. This machine "
            f"cannot do radio sensing as it stands: {sensing_ceiling()}."
        )

    ceiling = sensing_ceiling()
    lines = []
    any_moving = False
    for link in links:
        if link["status"] != 0:
            continue
        reading = movement(link["interface"], link["level"])
        seen = reading["samples"]
        sample_word = "sample" if seen == 1 else "samples"
        verdict = (
            f"MOVING (spread {reading['spread_db']}dB over {seen} {sample_word})"
            if reading["moving"]
            else f"still (spread {reading['spread_db']}dB over {seen} {sample_word})"
        )
        any_moving = any_moving or reading["moving"]
        lines.append(
            f"{link['interface']}: link={link['link']}/70 level={link['level']}dBm"
            f" - {verdict}"
        )
    if not any_moving and lines:
        # Naming a window the sense has not filled yet would overstate what has
        # actually been observed, so the count is the real one.
        widest = max(
            (len(_HISTORY_BY_IFACE[link["interface"]]) for link in links if link["status"] == 0),
            default=0,
        )
        if widest:
            lines.append(
                f"no interface shows movement across the {widest} sample(s) "
                f"collected so far (threshold {_MOVEMENT_SPREAD_DB}dB)"
            )
    lines.append(f"radio sensing ceiling: {ceiling}")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc-net-wireless",
        metadata={"interfaces": len(links), "moving": any_moving},
    )


_SENSE = Sense(
    name="rfsense",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
