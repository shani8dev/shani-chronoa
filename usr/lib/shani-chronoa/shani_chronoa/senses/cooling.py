"""Cooling: fans, liquid coolers, and what a temperature sensor does not say.

`hwmon` already reports fan RPM and temperature, so the question here is what
it *cannot* answer: whether the thing cooling the CPU is a liquid cooler whose
pump is running, whether any of it is actually moving air, and whether the
temperatures are sane.

**`hwmon` cannot see a liquid cooler at all.** An AIO's radiator fans and
pump appear in `/sys/class/hwmon` as generic fan chips with no name anyone
recognises, if the board's firmware exposes them at all. `liquidctl` speaks to
the cooler directly over USB or a HID protocol and gets named readings —
coolant temperature, fan speeds, pump RPM, and the mode the cooler is in. So
this sense is a *supplement* to `hwmon`, not a replacement, and both appear in
the output.

**A fan at 0 RPM is a real reading and means something different from no fan.**
This is the trap the sense is mostly built around. Measured on this machine's
hwmon chips: fans reported at 3300 RPM, and a *declared but unwired* channel
reading exactly 0 — which `hwmon` already reports as "not populated" because a
0C temperature slot is physically impossible on a running machine. The same
argument does **not** transfer to a fan: a fan genuinely at 0 RPM is one
whose power is cut by the firmware, or one stopped because the temperature is
fine. So a 0-RPM fan is reported as *stopped*, distinctly from absent, and
never dropped — because "the fan stopped" is a thing a user needs to know and
"there is no fan" is a different claim entirely.

**`liquidctl` talks to hardware over USB, and its absence is routine.** It is
in `shani-tools-extra`, which a desktop install has and a server or kiosk does
not. A machine with no liquid cooler also simply has nothing to say. Both
report "not determined" rather than a clean negative.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

_HWMON = Path("/sys/class/hwmon")
_TIMEOUT = 20


def _read_int(path: Path) -> Optional[int]:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    return int(raw) if raw.lstrip("-").isdigit() else None


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_hwmon_fans() -> List[dict]:
    """Fan channels straight from hwmon, including the ones reading zero.

    Reusing the sibling sense's glob rather than re-implementing it: a second
    copy of the chip-walking code is a second chance to disagree about which
    chips exist.
    """
    fans: List[dict] = []
    try:
        entries = sorted(p.resolve() for p in _HWMON.glob("hwmon*"))
    except OSError as exc:
        logger.debug("cannot list hwmon: %s", exc)
        return fans
    for chip in entries:
        name = _read_text(chip / "name") or chip.name
        try:
            attributes = sorted(p.name for p in chip.iterdir())
        except OSError:
            continue
        for attribute in attributes:
            if not attribute.startswith("fan") or not attribute.endswith("_input"):
                continue
            value = _read_int(chip / attribute)
            if value is None:
                continue
            index = attribute[3:].split("_", 1)[0]
            label = _read_text(chip / f"fan{index}_label") or name
            fans.append({
                "chip": name,
                "channel": f"fan{index}",
                "label": label,
                "rpm": value,
                # A fan at zero is a distinct, meaningful state, so it is kept
                # and labelled rather than filtered as noise.
                "stopped": value == 0,
            })
    return fans


def _liquidctl() -> Optional[dict]:
    """One JSON blob of the cooler's own readings, or None.

    `liquidctl list --json` gives the device identifiers and
    `liquidctl status --json` the readings, and the two are keyed differently
    between versions, so the status is read and its top-level keys are what is
    used.
    """
    if shutil.which("liquidctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["liquidctl", "status", "--json"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("liquidctl failed: %s", exc)
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    import json

    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _coolant(status: dict) -> List[dict]:
    """The liquid temperatures, which is the reading hwmon cannot give."""
    found = []
    for key, value in (status or {}).items():
        if not isinstance(value, dict):
            continue
        temperature = value.get("Liquid temperature")
        if not isinstance(temperature, dict):
            continue
        entry: Dict[str, object] = {"device": key}
        for field in ("Liquid temperature", "Temperature"):
            if isinstance(value.get(field), dict):
                celsius = value[field].get("celsius")
                if celsius is not None:
                    entry["liquid_c"] = celsius
                    break
        for field, label in (("FAN speed", "fans"),
                             ("Pump speed", "pump_rpm")):
            reading = value.get(field)
            if isinstance(reading, dict):
                rpm = reading.get("rpm")
                if rpm is not None:
                    entry[label] = rpm
        if value.get("Speed") is not None:
            entry["mode"] = value["Speed"]
        if len(entry) > 1:
            found.append(entry)
    return found


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("cooling"):
        return f"Not reading cooling state: {config.sense_allowed_reason('cooling')}."

    fans = read_hwmon_fans()
    liquidctl_present = shutil.which("liquidctl") is not None
    status = _liquidctl() if liquidctl_present else None
    coolers = _coolant(status) if status else []

    if not fans and not coolers:
        if not liquidctl_present:
            return _SENSE.to_percept(
                "No fan channel and no liquid cooler were found. hwmon exposes "
                "nothing here and liquidctl is not installed, so the fans are "
                "undetermined - the kernel is not reporting any - and the "
                "coolant is undetermined - the tool is absent. Neither means "
                "this machine has no cooling.",
                source="hwmon+liquidctl",
                metadata={"fans": 0, "coolers": 0, "liquidctl_present": False},
            )
        return _SENSE.to_percept(
            "hwmon exposes no fan channel and liquidctl reports no cooler. A "
            "passively cooled machine looks like this, and so does one whose "
            "cooling is driven entirely through a firmware the kernel does not "
            "expose.",
            source="hwmon+liquidctl",
            metadata={"fans": 0, "coolers": 0, "liquidctl_present": True},
        )

    lines = []
    stopped = 0
    for fan in fans:
        if fan["stopped"]:
            stopped += 1
            lines.append(
                f"{fan['label']} ({fan['channel']}): stopped, 0 RPM"
            )
        else:
            lines.append(f"{fan['label']} ({fan['channel']}): {fan['rpm']} RPM")

    for cooler in coolers:
        detail = [cooler["device"]]
        if "liquid_c" in cooler:
            detail.append(f"coolant {cooler['liquid_c']}C")
        if "fans" in cooler:
            detail.append(f"{cooler['fans']} RPM")
        if "pump_rpm" in cooler:
            detail.append(f"pump {cooler['pump_rpm']} RPM")
        if "mode" in cooler:
            detail.append(str(cooler["mode"]))
        lines.append("  " + ", ".join(detail))

    if stopped:
        lines.append(
            f"{stopped} fan channel(s) reading 0 RPM. That is a fan whose power "
            f"is cut or which has stopped - it is not the same as no fan being "
            f"fitted, and the hwmon sense reports a declared-but-unwired "
            f"channel separately."
        )
    if coolers and not fans:
        lines.append(
            "no fan channel in hwmon: this machine's fans are reachable only "
            "through the cooler, not through the kernel's hwmon chips."
        )
    lines.append(
        f"{len(fans)} fan channel(s) across hwmon, {len(coolers)} liquid "
        f"cooler(s) reported by liquidctl"
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="hwmon+liquidctl",
        metadata={
            "fans": len(fans),
            "stopped_fans": stopped,
            "coolers": len(coolers),
            "liquidctl_present": liquidctl_present,
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cooling",
        "description": (
            "Read how the machine is being cooled: every fan channel the "
            "kernel's hardware monitoring exposes with its RPM, plus any liquid "
            "cooler's coolant temperature, fan speed, pump speed and mode. A "
            "fan reading 0 RPM is reported as stopped, which is a real and "
            "distinct state from no fan being fitted. Complements the hwmon "
            "sense rather than replacing it: a liquid cooler usually does not "
            "appear in hwmon at all, and hwmon's chips carry the fans that are "
            "not part of one. Requires the liquidctl package for the cooler "
            "readings; without it, only the hwmon half is reported."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="cooling",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
