"""Battery state, charge and wear, read from `/sys/class/power_supply`.

Reading a charge percentage looks like a two-line division, and it is the kind
of thing that looks correct until it is not. Four traps live in this one
directory and every one of them yields a plausible number rather than an error.
All four were found by reading the real files on this machine, not from
documentation, and the measured values are quoted below.

**The two field families are different units, and one is usually absent.** A
pack reports `charge_now`/`charge_full` in microamp-hours, or
`energy_now`/`energy_full` in microwatt-hours. On this laptop `charge_full` and
`charge_now` both read back as **empty strings**, because the embedded
controller exposes no coulomb counter - so the family is chosen by which one is
populated, never by preference.

**`energy_full` is not capacity, and the ratio can exceed 100.** Measured here:
`energy_full=36020000`, `energy_now=36130000`, so the naive ratio is
**100.3%**. That is calibration slop, not a fault: the kernel describes
`energy_full` as the last value the pack was *charged to*, not its capacity.
The percentage is therefore clamped, and an over-full ratio is recorded
separately rather than silently hidden.

**Wear needs a different file again.** `energy_full_design=45000000` against
`energy_full=36020000` is 20.0% wear. Using the design value as the current one
reports a pack that is new forever - the exact opposite of the fact this sense
exists to surface.

**An absent pack is not a zero.** A desktop has no `power_supply` entry at
all, so `determined` distinguishes "looked, and there is no battery" from
"could not look", which is the distinction this package's other senses have
each gotten wrong once.
"""

import logging
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_GLOB = "/sys/class/power_supply/*"
_AC = "Mains"
_BATTERY = "Battery"

# A pack reporting more energy than its own last-full stamp is within
# calibration slop, but a large excess is a real anomaly rather than slop, so it
# is reported rather than clamped away silently.
_OVERFULL_REPORT = 1.02


def _pack(entry: Path) -> Optional[dict]:
    """One battery's readings, or None if this is not one.

    `type` is read first because the directory also holds USB-C ports
    (`ucsi-source-psy-*`, type `USB`) and AC adapters (type `Mains`), and
    counting those as batteries is the obvious way to report a laptop as having
    three batteries.
    """
    if sysfs.read_text(entry / "type") != _BATTERY:
        return None

    now = sysfs.read_int(entry / "energy_now")
    full = sysfs.read_int(entry / "energy_full")
    design = sysfs.read_int(entry / "energy_full_design")
    unit = "Wh"

    if now is None or full is None:
        # Fall back to the charge family, which is in different units but the
        # same idea. Only consulted when the energy family is unusable.
        now = sysfs.read_int(entry / "charge_now")
        full = sysfs.read_int(entry / "charge_full")
        if now is not None and full is not None:
            unit = "Ah"
        else:
            # Neither family is populated: a pack that reports neither energy
            # nor charge cannot have a percentage derived, and saying so beats
            # a fabricated 0.
            return {
                "name": entry.name,
                "model": sysfs.read_text(entry / "model_name"),
                "status": sysfs.read_text(entry / "status") or "unknown",
                "technology": sysfs.read_text(entry / "technology"),
                "percent": None,
                "reason": "this pack reports neither energy nor charge readings",
            }

    if not now or not full:
        pack = {
            "name": entry.name,
            "model": sysfs.read_text(entry / "model_name"),
            "status": sysfs.read_text(entry / "status") or "unknown",
            "technology": sysfs.read_text(entry / "technology"),
            "percent": None,
            "reason": (
                "the pack reported a zero reading, which is a gauge that has "
                "not settled yet rather than an empty battery"
            ),
        }
        if design:
            # Reported even though it cannot yield a percentage: a pack whose
            # gauge has not settled still has a real design capacity, and
            # dropping it would discard a fact that was read successfully.
            pack["design_wh"] = round(design / 1_000_000, 2)
        return pack

    ratio = now / full
    pack = {
        "name": entry.name,
        "model": sysfs.read_text(entry / "model_name"),
        "status": sysfs.read_text(entry / "status") or "unknown",
        "technology": sysfs.read_text(entry / "technology"),
        "unit": unit,
        "now": now,
        "full": full,
        "percent": round(min(100.0, max(0.0, ratio * 100)), 1),
    }
    if ratio > _OVERFULL_REPORT:
        pack["percent"] = 100.0
        pack["note"] = (
            f"the pack is holding {ratio * 100:.1f}% of its own last-full "
            f"stamp - above 100% is beyond normal calibration slop"
        )
    elif ratio > 1.0:
        pack["note"] = "slightly above its last-full stamp; clamped to 100%"

    cycle_count = sysfs.read_int(entry / "cycle_count")
    if cycle_count is not None:
        pack["cycles"] = cycle_count

    if design and full:
        # Design capacity is the denominator that makes this a wear figure.
        # Using `full` for both would report a pack as new forever.
        pack["design_wh"] = round(design / 1_000_000, 2)
        pack["full_wh"] = round(full / 1_000_000, 2)
        pack["wear_percent"] = round(max(0.0, (1 - full / design) * 100), 1)
    elif design:
        # Design capacity alone cannot yield a wear figure - `energy_full` is
        # the denominator, and without it the only honest answer is that wear
        # is undetermined rather than a number derived from the wrong file.
        pack["design_wh"] = round(design / 1_000_000, 2)
        pack["note"] = (
            (pack["note"] + "; ") if pack.get("note") else ""
        ) + "no current full-charge reading, so wear is undetermined"

    return pack


def read_packs() -> List[dict]:
    """Every battery the kernel knows about."""
    packs = []
    for raw in sorted(Path(_GLOB).parent.glob(Path(_GLOB).name)):
        try:
            entry = raw.resolve()
        except OSError:
            continue
        found = _pack(entry)
        if found is not None:
            packs.append(found)
    return packs


def read_ac() -> List[dict]:
    """Mains adapters, so "is it plugged in" is answerable."""
    adapters = []
    for raw in sorted(Path(_GLOB).parent.glob(Path(_GLOB).name)):
        try:
            entry = raw.resolve()
        except OSError:
            continue
        if sysfs.read_text(entry / "type") != _AC:
            continue
        adapters.append({
            "name": entry.name,
            "online": sysfs.read_text(entry / "online") or "unknown",
        })
    return adapters


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("power"):
        return f"Not reading battery state: {config.sense_allowed_reason('power')}."

    packs = read_packs()
    ac = read_ac()

    if not packs:
        detail = (
            "This machine has no battery - a desktop or a server. "
            f"{len(ac)} mains adapter(s) visible."
            if ac
            else "No power-supply entry is exposed to this user at all."
        )
        return _SENSE.to_percept(
            detail,
            source="sysfs-power-supply",
            metadata={"packs": 0, "ac_adapters": len(ac), "determined": True},
        )

    lines = []
    for pack in packs:
        header = pack.get("model") or pack["name"]
        lines.append(f"{header}: {pack['status']}")
        if pack.get("percent") is not None:
            lines.append(f"  {pack['percent']}% of last full charge ({pack['unit']})")
        if pack.get("reason"):
            lines.append(f"  charge undetermined: {pack['reason']}")
        if "wear_percent" in pack:
            lines.append(
                f"  {pack['wear_percent']}% worn: holds {pack['full_wh']}Wh "
                f"of a {pack['design_wh']}Wh design capacity"
            )
        if "cycles" in pack:
            lines.append(f"  {pack['cycles']} charge cycles")
        if pack.get("technology"):
            lines.append(f"  chemistry: {pack['technology']}")
        if pack.get("note"):
            lines.append(f"  note: {pack['note']}")

    plugged = [a["name"] for a in ac if a["online"] == "1"]
    if ac:
        lines.append(
            f"mains: {', '.join(plugged) + ' online' if plugged else 'no adapter online'}"
        )

    usable = sum(1 for p in packs if p.get("percent") is not None)
    lines.append(f"{usable} of {len(packs)} pack(s) reported a charge reading")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-power-supply",
        metadata={
            "packs": len(packs),
            "with_charge": usable,
            "worst_wear": max(
                (p["wear_percent"] for p in packs if "wear_percent" in p),
                default=None,
            ),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        # `sense_allowed()` builds the key as "<name>-sense-enabled", so this
        # name is also the consent key: battery-sense-enabled.
        "name": "power",
        "description": (
            "Read battery charge, wear and cycle count, and whether a mains "
            "adapter is online. Charge comes from whichever of the energy or "
            "charge field families the pack actually populates, and is "
            "clamped to 100% because a pack can sit slightly above its own "
            "last-full stamp. Wear is measured against the design capacity, so "
            "a degraded pack reports as degraded rather than as new. A machine "
            "with no battery says so instead of reporting zero."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="power",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
