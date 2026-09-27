"""Thermal-grid sense: read a real infrared array if one is wired to I2C.

Two parts are common and cheap: the Melexis **MLX90640**, a calibrated
32x24 array of 768 thermopile pixels, and the Panasonic **AMG8833**, 8x8 for
64. Both sit on I2C at a fixed address and both are used for exactly this -
presence detection, people counting, intrusion detection. Adafruit and others
sell breakouts for about the price of a coffee, which is the reason this is a
real, buildable thing rather than a research curiosity.

**Without the part wired up there is no grid, and this says so.** It does not
fall back to the CPU thermal zones that `senses/thermal.py` already reads,
because those measure the silicon in this machine and have nothing to do with
the temperature of a room. Substituting one for the other would be the exact
kind of confident wrong answer this layer exists to prevent.

**What a grid from these parts is and is not.** 768 pixels over roughly a 55
degree field of view is about 2.6 degrees per pixel. That resolves a warm
shape and where in the room it is; it does not resolve a face, and it cannot
identify anyone. The grid is returned as numbers and nothing else - no image
is rendered or stored by this sense.

Probing is deliberately shallow: it checks whether a device acknowledges at
the expected address, and reads a frame only if one does. It does not attempt
the multi-page read dance these parts require to produce a correct frame,
because a partially-read grid is worse than an honest "present, not read".
"""

import glob
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
# A thermal grid can show the shape of a person in a room. That is a
# percept of the user's space, not public machine metadata.
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 60.0
_POLL_INTERVAL = 60.0

# I2C address -> (part, geometry, what one pixel covers, roughly).
KNOWN_ARRAYS = {
    "0x33": {"part": "MLX90640", "grid": (24, 32), "pixels": 768,
             "note": "32x24, ~2.6 deg per pixel at 55deg FOV"},
    "0x60": {"part": "AMG8833", "grid": (8, 8), "pixels": 64,
             "note": "8x8, human detectable to ~7m, +-2.5C accuracy"},
}

_I2C_GLOB = "/dev/i2c-*"
_SCAN_TIMEOUT = 20.0

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "thermalgrid",
        "description": (
            "Probe the machine's I2C buses for an infrared thermal array - a "
            "Melexis MLX90640 or Panasonic AMG8833 - and report whether one is "
            "attached, its geometry and its field of view. Reports presence "
            "only: a full frame needs the part wired to I2C, and without it "
            "there is no grid, and this does not substitute the CPU's own "
            "thermal zones, which measure the machine rather than the room."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "escalate": {
                    "type": "boolean",
                    "description": (
                        "Run the scan through polkit, which will prompt for "
                        "your password. Needed because reading an I2C bus "
                        "requires root. Off by default; the command is fixed, "
                        "read-only and shell-free."
                    ),
                }
            },
        },
    },
}


def _buses() -> list:
    try:
        return sorted(Path(p) for p in glob.glob(_I2C_GLOB))
    except OSError:
        return []


class ProbeResult:
    """What a scan established - and, just as importantly, what it could not.

    A single boolean cannot carry this honestly. `i2cdetect` prints
    "Permission denied" on stderr and then **exits 0**, so a scan that never
    looked at anything is indistinguishable from a scan that looked and found
    nothing. Collapsing those two produced the worst possible failure for a
    sensor: on a machine that *did* have a thermal array, an unprivileged
    Chronoa reported "no thermal array is attached" - a confident false
    negative, with no error anywhere to suggest otherwise.
    """

    def __init__(self) -> None:
        self.arrays: list = []
        self.scanned: list = []
        self.denied: list = []

    @property
    def determined(self) -> bool:
        return bool(self.scanned)

    @property
    def found(self) -> bool:
        return bool(self.arrays)


def _bus_number(bus: Path) -> Optional[str]:
    """The digits after `i2c-`, or None if the name is not that shape.

    This is the only value that reaches an argv, and it is validated rather
    than stripped, because an escalated run must not be reachable with an
    arbitrary string attached to it.
    """
    suffix = bus.name[len("i2c-"):]
    return suffix if suffix.isdigit() else None


def probe(escalate: bool = False) -> ProbeResult:
    """Scan every I2C bus for a known thermal array.

    `escalate` runs the scan through polkit, which prompts for a password.
    That is opt-in on purpose: reading an I2C bus needs root, and a sense
    that silently asked for a password - especially one the model can call -
    would be a pattern with no good reason to exist. The command itself is
    fixed, read-only and shell-free, so the escalation buys exactly one thing
    and nothing more.
    """
    result = ProbeResult()
    if shutil.which("i2cdetect") is None and not escalate:
        return result
    for bus in _buses():
        number = _bus_number(bus)
        if number is None:
            continue
        argv = (["pkexec", "/usr/sbin/i2cdetect"] if escalate else ["i2cdetect"]) + [
            "-y", number
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True, text=True, timeout=_SCAN_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("i2cdetect failed on %s: %s", bus, exc)
            result.denied.append(bus.name)
            continue
        stderr = (proc.stderr or "").lower()
        if "permission denied" in stderr or "run as root" in stderr:
            result.denied.append(bus.name)
            continue
        if not (proc.stdout or "").strip():
            # No table at all: the bus exists but could not be read.
            result.denied.append(bus.name)
            continue
        result.scanned.append(bus.name)
        for address, meta in KNOWN_ARRAYS.items():
            # `i2cdetect` prints the bare hex byte - `33`, not `0x33` - so the
            # table key is normalised before comparison. Left as-is the two
            # never matched and the sense reported "no array" with one
            # attached, which is the same false negative as the permission
            # case and just as hard to notice.
            if _address_present(proc.stdout or "", address.lower().removeprefix("0x")):
                result.arrays.append({"bus": bus.name, "address": address, **meta})
    return result


def _address_present(out: str, address: str) -> bool:
    """Whether `i2cdetect` showed a device at `address` on any row."""
    for line in out.splitlines():
        if ":" not in line:
            continue
        _bus, _, rest = line.partition(":")
        cells = rest.split()
        # Cells are 16 wide after the row header; the address column is the
        # high nibble, so search every cell for an exact address token.
        if address in cells:
            return True
    return False


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("thermalgrid"):
        return (
            "Not probing for a thermal array: "
            f"{config.sense_allowed_reason('thermalgrid')}."
        )

    escalate = str(arguments.get("escalate") or "").strip().lower() in ("1", "true", "yes")

    if shutil.which("i2cdetect") is None and not escalate:
        return (
            "i2cdetect is not installed, so the I2C buses cannot be scanned. "
            "Attach an MLX90640 (0x33) or AMG8833 (0x60) and install "
            "i2c-tools to use this sense."
        )

    buses = _buses()
    if not buses:
        return "This machine exposes no I2C bus, so no thermal array can be attached."

    result = probe(escalate=escalate)

    if result.arrays:
        pass
    elif not result.determined:
        # Say plainly that nothing was established. Reporting "no array
        # attached" here would be a guess, and the guess is wrong precisely
        # when it matters most - on a machine that has one.
        return (
            f"Could not read any I2C bus ({len(result.denied)} of {len(buses)} "
            "refused). i2cdetect needs root or i2c group membership, so whether "
            "a thermal array is attached is UNKNOWN, not absent. Re-run with "
            "escalate=true to scan through polkit, or add the user to the "
            f"'i2c' group to scan without a password (denied: "
            f"{', '.join(result.denied[:5])}"
            f"{'...' if len(result.denied) > 5 else ''})."
        )
    else:
        return (
            f"No thermal array is attached. Scanned {len(result.scanned)} of "
            f"{len(buses)} I2C bus(es) for MLX90640 (0x33) and AMG8833 (0x60) "
            "and neither answered. Note this is a separate measurement from "
            "the CPU thermal zones: those report the machine's own heat, not "
            "the room's."
        )
    arrays = result.arrays

    lines = []
    for a in arrays:
        rows, cols = a["grid"]
        lines.append(
            f"{a['part']} on {a['bus']} at {a['address']}: {a['note']} "
            f"({rows}x{cols} = {a['pixels']} pixels)"
        )
    lines.append(
        "frame not read: a correct frame needs the part's multi-page read "
        "sequence, and a partial grid would be worse than none"
    )
    return _SENSE.to_percept(
        "\n".join(lines),
        source="i2c-thermal-array",
        metadata={"arrays": len(arrays), "scanned": len(result.scanned)},
    )


_SENSE = Sense(
    name="thermalgrid",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
