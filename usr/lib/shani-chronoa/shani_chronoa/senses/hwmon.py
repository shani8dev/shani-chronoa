"""Hardware-monitoring sense: fan speed, SSD and chip temperature, voltage, current.

The kernel's `hwmon` class is where a machine reports what its own hardware is
doing: `/sys/class/hwmon/hwmon*/` carries `fan*_input` in RPM, `temp*_input`
in millidegrees, and `in*_input`, `curr*_input`, `power*_input` for the rest,
with each chip identified by its `name` (which is the driver).

This is a **complement** to `senses/thermal.py`, not a repeat of it. The
thermal zones are the CPU and board; hwmon is where a fan's RPM, the SSD's
die temperature, the WiFi chip's temperature and the battery's voltage and
current live. On this machine the two together surface a genuinely different
picture: the zones reported `SEN2: 90.0C`, and hwmon adds `nvme 37.9C` and
`iwlwifi_1` alongside two fans at 3300 RPM.

**Unpopulated slots are the trap, and they are the reason this module exists
rather than a five-line read of the directory.** Drivers routinely declare more
channels than the board physically wires up. On this machine `thinkpad`
exposes `temp1` through `temp8`, and `temp4` through `temp8` all read back
exactly `0` - which is 0.0 degrees Celsius, physically impossible on a machine
that is running. Reporting those as readings would put five fictitious
zero-degree sensors in front of a user, and a plausible-looking zero is worse
than an absent value: it is indistinguishable from a real reading until
something acts on it. So a channel that reads zero is reported as
**unpopulated**, and counted separately.

The opposite sentinel exists too, and the kernel documentation is explicit
about it for the drivers that declare these channels: a temperature above
127 degrees is a BIOS or driver read error, not a temperature. Both ends are
therefore range-checked, and a value outside a plausible band is reported as a
bad reading rather than passed through.
"""

import glob as _glob
import json
import logging
import shutil
import subprocess
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 60.0
_POLL_INTERVAL = 60.0

_HWMON_ROOT = Path("/sys/class/hwmon")
_ZONE_GLOB = "/sys/class/thermal/thermal_zone*"
_MIN_MILLIC = -40000
_MAX_MILLIC = 125000
_LIQUIDCTL_TIMEOUT = 20

# Attribute suffix -> (kind, scale, unit). `scale` converts the kernel's
# integer into the unit named here.
_KINDS = {
    "fan": ("fan", 1, "RPM"),
    "temp": ("temp", 0.001, "C"),
    "in": ("voltage", 0.001, "V"),
    "curr": ("current", 0.001, "A"),
    "power": ("power", 0.000001, "W"),
    "energy": ("energy", 0.000001, "Wh"),
    "humidity": ("humidity", 0.001, "%"),
}

# A temperature channel reading exactly 0 is an unwired slot on every driver
# that declares one; a real 0C reading on a powered machine is not a thing.
_UNPOPULATED_TEMP = 0
# Above this the kernel's own hwmon docs describe a read error, not a
# temperature (dell-smm-hwmon says so explicitly).
_MAX_PLAUSIBLE_C = 127
_MIN_PLAUSIBLE_C = -50

_ATTRIBUTE = re.compile(r"^(fan|temp|in|curr|power|energy|humidity)(\d+)_(input|label)$")

_SCHEMA = {
    "type": "function",
    "function": {
        # Named for the consent surface: `sense_allowed()` builds the key as
        # "<name>-sense-enabled", so a rename here leaves the sense
        # permanently ungrantable.
        "name": "hwmon",
        "description": (
            "Read the kernel's hardware-monitoring sensors: fan speed in RPM, "
            "and the temperature, voltage, current and power readings each "
            "driver exposes, identified by driver name. Complements the "
            "thermal sense, which reports the CPU and board. Channels a "
            "driver declares but the board does not populate are reported as "
            "unpopulated rather than as a reading."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _chips() -> List[Path]:
    """Every hwmon chip, following the symlinked hwmonX directories.

    `Path.glob` does not descend through a symlinked directory, and every
    attribute lives inside one, so a plain glob over `/sys/class/hwmon/*/`
    finds the directories and nothing inside them. Sorted by name so a chip
    does not change identity between polls.
    """
    chips = []
    for entry in sorted(_glob.glob(str(_HWMON_ROOT / "hwmon*"))):
        path = Path(entry)
        # Resolve the symlink so attribute access works on the real path.
        try:
            chips.append(path.resolve())
        except OSError:
            continue
    return chips


def _channels(chip: Path) -> Dict[str, List[dict]]:
    """grouped by kind -> [{channel, label, value, state}]."""
    grouped: Dict[str, List[dict]] = {}
    try:
        entries = sorted(os.listdir(chip))
    except OSError as exc:
        logger.debug("cannot list %s: %s", chip, exc)
        return grouped

    for name in entries:
        match = _ATTRIBUTE.match(name)
        if not match:
            continue
        kind, index, item = match.group(1), match.group(2), match.group(3)
        if kind not in _KINDS:
            continue
        if item == "label":
            continue

        label = _read_text(chip / f"{kind}{index}_label")
        raw = _read_text(chip / name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue

        resolved, scale, unit = _KINDS[kind]
        record = {
            "channel": f"{kind}{index}",
            "label": label or chip.name,
            "state": "ok",
        }
        if resolved == "temp":
            if value == _UNPOPULATED_TEMP:
                # A declared-but-unwired slot. Not 0C, and not a reading.
                record["state"] = "unpopulated"
            elif not _MIN_PLAUSIBLE_C <= value / 1000.0 <= _MAX_PLAUSIBLE_C:
                # A read error, per the kernel's own documentation for these
                # drivers. Reported as bad rather than passed through.
                record["state"] = "bad-reading"
            else:
                record["celsius"] = round(value * scale, 1)
        else:
            record["value"] = round(value * scale, 3)
            record["unit"] = unit
            if value < 0:
                record["state"] = "bad-reading"

        grouped.setdefault(resolved, []).append(record)
    return grouped


def read_chips() -> List[dict]:
    """Every chip, with its readings and any unpopulated channels."""
    out = []
    for chip in _chips():
        name = _read_text(chip / "name") or chip.name
        channels = _channels(chip)
        if not channels:
            continue
        out.append({"chip": name, "path": str(chip), "channels": channels})
    return out


def _read_zone(zone: Path) -> Optional[dict]:
    try:
        raw = (zone / "temp").read_text().strip()
        milli = int(raw)
    except (OSError, ValueError):
        return None
    if not _MIN_MILLIC <= milli <= _MAX_MILLIC:
        return None
    try:
        name = (zone / "type").read_text().strip()
    except OSError:
        name = zone.name
    return {"zone": zone.name, "name": name or zone.name, "celsius": round(milli / 1000.0, 1)}


def read_zones() -> list:
    zones = []
    # `Path().glob` rejects an absolute pattern outright, which is a
    # NotImplementedError rather than an empty result, so the stdlib glob is
    # what actually supports an absolute sysfs pattern.
    for zone in sorted(Path(p) for p in _glob.glob(_ZONE_GLOB)):
        reading = _read_zone(zone)
        if reading is not None:
            zones.append(reading)
    return zones



def read_pwm(chips: List[dict]) -> List[dict]:
    """Fan PWM outputs, read only.

    `pwmN` is the commanded duty cycle as 0-255 and `pwmN_enable` says whether
    anything is actually applying it. Reading the pair is useful because the
    two can disagree: a commanded 0 with the fan still spinning means the
    command is being ignored, which is a fault worth seeing and is invisible
    from the tachometer alone.

    The meaning of `pwmN_enable` is a convention rather than a standard. The
    kernel's own driver documentation has `dell-smm-hwmon` describing 1 as
    "BIOS fan control disabled" and 2 as re-enabling it, while `g762` calls 2
    "closed-loop mode" - so the number is reported verbatim and the reading is
    labelled as the convention it is.

    Nothing here writes. Writing a PWM value is a standing policy decision with
    physical consequences, not a bounded action, and it belongs to the machine's
    firmware unless a human has deliberately taken it over.
    """
    out: List[dict] = []
    for chip in chips:
        # `chip` is the driver's own name; `path` is the hwmonN directory the
        # channels were read from. Using the name here would look for a
        # directory called "thinkpad" and silently find no PWM at all.
        directory = Path(chip["path"])
        try:
            entries = sorted(p.name for p in directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.startswith("pwm") or not entry[3:].isdigit():
                continue
            index = entry[3:]
            value = _read_int(directory / entry)
            if value is None:
                continue
            record: Dict[str, object] = {
                "chip": chip["chip"],
                "channel": f"pwm{index}",
                "value": value,
            }
            enable = _read_int(directory / f"pwm{index}_enable")
            record["enable"] = enable
            record["mode"] = _PWM_ENABLE_MEANING.get(enable, "unknown")
            record["duty_pct"] = round(100.0 * value / 255.0, 1)
            out.append(record)
    return out


# The de-facto reading of pwmN_enable. Not a kernel guarantee: see read_pwm().
_PWM_ENABLE_MEANING = {
    0: "no speed control, fan runs at full",
    1: "manual",
    2: "automatic, closed loop",
}


def _pwm_lines(pwm: List[dict], fans: List[dict]) -> List[str]:
    by_chip: Dict[str, Dict[str, int]] = {}
    for fan in fans:
        by_chip.setdefault(fan["chip"], {})[fan["channel"]] = fan["rpm"]

    lines: List[str] = []
    for record in pwm:
        detail = (
            f"{record['duty_pct']:g}% ({record['value']}/255), "
            f"enable {record['enable']} - {record['mode']} "
            f"(driver-specific)"
        )
        line = f"  {record['chip']} {record['channel']}: {detail}"
        # A commanded stop the fan is ignoring is a fault, and reporting only
        # the duty cycle or only the tachometer would hide it.
        for channel, rpm in by_chip.get(record["chip"], {}).items():
            if record["value"] == 0 and rpm > 0:
                line += (f" - but {channel} is still spinning at {rpm} RPM, "
                         f"so the two disagree and the stop is not taking effect")
        lines.append(line)
    return lines


def fans_from(chips: List[dict]) -> List[dict]:
    """Fan channels, derived from the chip walk rather than a second one.

    A fan reading zero is a distinct, meaningful state - a stopped fan is not an
    absent fan - so it is kept and labelled. `read_chips()` already preserves
    it: the "declared but not populated" rule applies only to temperature
    channels, so a 0 RPM fan arrives here as a normal reading. That is what
    makes this a derivation and not a second implementation; walking
    /sys/class/hwmon a second time in the same sense is exactly the
    duplication the merge exists to remove.
    """
    out: List[dict] = []
    for chip in chips:
        for record in chip["channels"].get("fan", []):
            if record["state"] != "ok":
                continue
            value = record.get("value")
            if value is None:
                continue
            out.append({
                "chip": chip["chip"],
                "channel": record["channel"],
                "label": record.get("label") or chip["chip"],
                "rpm": value,
                "stopped": value == 0,
            })
    return out


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
            capture_output=True, text=True, timeout=_LIQUIDCTL_TIMEOUT, check=False,
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
    if not config.sense_allowed("hwmon"):
        return f"Not reading hardware sensors: {config.sense_allowed_reason('hwmon')}."

    chips = read_chips()
    zones = read_zones()
    fans = fans_from(chips)
    pwm = read_pwm(chips)

    if not chips and not zones:
        return (
            "No hwmon chip and no thermal zone exposed a readable channel. "
            "That is a fact about what could be read on this machine, not a "
            "claim that it has no sensors."
        )

    lines = []
    total_unpopulated = 0
    total_readings = 0
    for entry in chips:
        lines.append(f"{entry['chip']}:")
        for kind in sorted(entry["channels"]):
            for record in entry["channels"][kind]:
                if record["state"] == "unpopulated":
                    total_unpopulated += 1
                    lines.append(f"  {record['channel']} ({record['label']}): not populated")
                    continue
                if record["state"] == "bad-reading":
                    lines.append(f"  {record['channel']} ({record['label']}): reading out of range")
                    continue
                total_readings += 1
                if kind == "temp":
                    lines.append(
                        f"  {record['channel']} ({record['label']}): {record['celsius']}C"
                    )
                else:
                    lines.append(
                        f"  {record['channel']} ({record['label']}): "
                        f"{record['value']} {record['unit']}"
                    )

    liquidctl_present = shutil.which("liquidctl") is not None
    status = _liquidctl() if liquidctl_present else None
    coolers = _coolant(status) if status else []

    if not fans and not coolers and not liquidctl_present:
        return _SENSE.to_percept(
            "No fan channel and no liquid cooler were found. hwmon exposes "
            "nothing here and liquidctl is not installed, so the fans are "
            "undetermined - the kernel is not reporting any - and the coolant "
            "is undetermined - the tool is absent. Neither means this machine "
            "has no cooling.",
            source="sysfs-hwmon",
            metadata={"chips": len(chips), "readings": total_readings,
                      "unpopulated": total_unpopulated, "zones": len(zones),
                      "fans": 0, "stopped_fans": 0, "coolers": 0,
                      "liquidctl_present": False},
        )

    stopped = 0
    for fan in fans:
        if fan["stopped"]:
            stopped += 1
            lines.append(f"{fan['label']} ({fan['channel']}): stopped, 0 RPM")
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
            f"fitted, and a declared-but-unwired channel is reported "
            f"separately above."
        )
    if coolers and not fans:
        lines.append(
            "no fan channel in hwmon: this machine's fans are reachable only "
            "through the cooler, not through the kernel's hwmon chips."
        )
    if fans or coolers:
        lines.append(
            f"{len(fans)} fan channel(s) across hwmon, {len(coolers)} liquid "
            f"cooler(s) reported by liquidctl"
        )

    if pwm:
        lines.append(
            f"fan control: {len(pwm)} PWM output(s) exposed (read only)"
        )
        lines.extend(_pwm_lines(pwm, fans))

    if zones:
        hottest = max(
            (z for z in zones if z.get("celsius") is not None),
            key=lambda z: z["celsius"], default=None)
        lines.append(
            f"thermal zones: {len(zones)}"
            + (f", hottest {hottest['zone']} at {hottest['celsius']}C"
               if hottest else "")
        )
        for zone in zones:
            if zone.get("celsius") is None:
                continue
            lines.append(f"  {zone['zone']}: {zone['celsius']}C")

    lines.append(
        f"{total_readings} reading(s) across {len(chips)} chip(s); "
        f"{total_unpopulated} channel(s) declared but not populated"
    )
    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-hwmon",
        metadata={
            "chips": len(chips),
            "readings": total_readings,
            "unpopulated": total_unpopulated,
            "zones": len(zones),
            "fans": len(fans),
            "pwm_channels": len(pwm),
            "stopped_fans": stopped,
            "coolers": len(coolers),
            "liquidctl_present": liquidctl_present,
        },
    )


_SENSE = Sense(
    name="hwmon",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
