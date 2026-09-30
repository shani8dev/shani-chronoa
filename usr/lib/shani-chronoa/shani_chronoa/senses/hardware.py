"""Sense: which physical machine this is.

Nothing else in the sense set answers "what am I actually running on". `cpu`
reports processor count and load, `devices` walks PCI, `storage` walks
`/sys/block`, and `config.HardwareProfile` grades `/proc/cpuinfo` into a model
size - none of which is a model name. When someone asks an assistant what
machine they are on, "16GB, 8 cores" is a spec sheet, not an answer.

Two sources, in order, because they are not equally available:

- **DMI** (`/sys/class/dmi/id/`) is the firmware's own table. It is present on
  essentially every x86 machine, and the four attributes read here are the ones
  that describe the *model* rather than the individual unit.
- **The device tree** (`/sys/firmware/devicetree/base/model`) is where the same
  fact lives on ARM, where there is no DMI at all.

**`product_uuid` and `product_serial` sit in the same directory and are
deliberately not read.** They are identifiers, not state: a serial number
distinguishes this unit from every other unit, which is exactly what a vendor
warranty lookup needs and exactly what an assistant has no business holding in
a percept that gets pasted into a prompt and potentially into a cloud fallback.
The distinction this whole file is built on - a fact about the machine versus a
fact about *which* machine - is exactly the distinction those two attributes
fail.

**`uname -m` is never used as the model.** It answers `x86_64` or `aarch64` -
the instruction set, and it is identical on every machine on earth of that
architecture. Substituting it for a model is the single most common way this
answer goes wrong, because `x86_64` looks like a plausible machine name.

Firmware also writes placeholders into DMI, and they are common enough to
matter: `To Be Filled By O.E.M.` on budget boards, `System Product Name` and
`Default String` on OEM firmware, `Unknown` on a great many VMs. A sense that
reported one of those as a model would be confidently wrong, so a placeholder
is treated as *absent* and the search falls through to the device tree, then to
UNKNOWN. That is why this file reads several attributes and prefers a
non-placeholder one: `sys_vendor` on a VM is often a real vendor string while
`product_name` is a placeholder, and picking the wrong field order would throw
away the one usable answer.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
# A model name is a fact about the hardware, not about the person using it. It
# is also the one machine fact that is safe to send anywhere: there is nothing
# in it that identifies the individual unit, which is the reason the serial and
# UUID are excluded above.
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 3600.0
_POLL_INTERVAL = 3600.0

_DMI = Path("/sys/class/dmi/id")
_DEVTREE_MODEL = Path("/sys/firmware/devicetree/base/model")

#: Read in this order, so the most model-like attribute that actually holds a
#: value wins. `product_name` first because it is the canonical one, then the
#: family string (a laptop line that beats a generic "Notebook"), then the
#: board, then the vendor as a last resort before giving up.
_DMI_ATTRS = ("product_name", "product_family", "board_name", "sys_vendor")

#: Values firmware writes when it has nothing to say. Compared lowercased with
#: surrounding whitespace stripped, because the strings appear with and without
#: trailing NULs and inconsistent capitalisation.
_PLACEHOLDERS = frozenset({
    "to be filled by o.e.m.",
    "to be filled by oem",
    "to be filled by o.e.m",
    "to be filled by o.e.m.",
    "system product name",
    "system version",
    "system serial number",
    "default string",
    "default",
    "unknown",
    "none",
    "n/a",
    "na",
    "not applicable",
    "not specified",
    "not available",
    "oem",
    "oem string",
    "oem product",
    "empty",
    "0",
    "-",
})


def _read_text(path: Path) -> Optional[str]:
    """File contents, or None when it could not be read at all."""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def _clean(raw: Optional[str]) -> Optional[str]:
    """Trim a firmware string, or None if it is empty or a placeholder.

    The NUL strip is not cosmetic: `/sys/firmware/devicetree/base/model` is a
    fixed-size property and is NUL-padded to its declared length, so a 32-byte
    model string arrives with a tail of NUL bytes that would otherwise be
    printed verbatim.
    """
    if raw is None:
        return None
    value = raw.replace("\x00", "").strip()
    if not value:
        return None
    if value.lower() in _PLACEHOLDERS:
        return None
    return value


def read_dmi() -> Dict[str, str]:
    """The model-describing DMI attributes that hold a real value.

    Deliberately excludes `product_uuid` and `product_serial`: they identify
    this individual unit, which is not a question this sense was asked.
    """
    found: Dict[str, str] = {}
    for attribute in _DMI_ATTRS:
        value = _clean(_read_text(_DMI / attribute))
        if value is not None:
            found[attribute] = value
    return found


def read_devicetree_model() -> Optional[str]:
    """The device-tree model string, NUL-padding stripped, or None."""
    return _clean(_read_text(_DEVTREE_MODEL))


def read_identity() -> Optional[dict]:
    """What machine this is, or None when neither source can say.

    None is the honest answer for a machine with no readable DMI and no device
    tree - a stripped container, most commonly. It is *not* the same as an
    empty `fields` dict claiming a machine with no model, and it is emphatically
    not permission to fall back to the architecture.
    """
    dmi = read_dmi()
    if dmi:
        return {"fields": dmi, "source": "DMI (/sys/class/dmi/id)"}
    model = read_devicetree_model()
    if model:
        return {"fields": {"model": model}, "source": "device tree"}
    return None


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("hardware"):
        return f"Not identifying the machine: {config.sense_allowed_reason('hardware')}."

    identity = read_identity()
    if identity is None:
        return (
            "Machine model is UNKNOWN - 'DMI unavailable'. Neither "
            "/sys/class/dmi/id nor /sys/firmware/devicetree/base/model held a "
            "value that was not a firmware placeholder, which is normal inside "
            "a stripped container. That is not a claim that the machine has no "
            "model, and the architecture is deliberately not substituted for "
            "one: uname -m reports the instruction set, which is identical on "
            "every x86_64 machine."
        )

    fields = identity["fields"]
    # The headline is the most model-like attribute present, in the same
    # preference order the reader used.
    headline = next((fields[a] for a in _DMI_ATTRS if a in fields), None)
    if headline is None:
        headline = fields["model"]

    lines = [f"machine: {headline}"]
    for attribute in _DMI_ATTRS:
        if attribute in fields and fields[attribute] != headline:
            lines.append(f"  {attribute}: {fields[attribute]}")
    if "model" in fields and fields["model"] != headline:
        lines.append(f"  model: {fields['model']}")
    lines.append(
        "  (from DMI. Serial and UUID are deliberately not read - they identify "
        "this individual unit rather than describing the machine.)"
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="dmi",
        metadata={
            "identity_known": True,
            "read_from": identity["source"],
            "model": headline,
            "vendor": fields.get("sys_vendor"),
            "board": fields.get("board_name"),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "hardware",
        "description": (
            "Identify the machine this is running on: the model and vendor from "
            "the firmware's DMI table, falling back to the device-tree model on "
            "hardware that has no DMI. Reports the model only - the serial "
            "number and UUID are deliberately excluded because they identify "
            "the individual unit rather than describing the machine. Says "
            "UNKNOWN when the firmware offers no real value, and never "
            "substitutes the CPU architecture for a model name."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="hardware",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
