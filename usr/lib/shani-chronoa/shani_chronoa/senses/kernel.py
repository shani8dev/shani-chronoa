"""Sense: which kernel this booted, with what arguments, and inside what.

Four files, no binaries, and all four are facts that do not change while the
machine is up - which is what makes this one of the cheapest senses in the set
and the one whose value is highest exactly when something has gone wrong. A
kernel panic, a black screen after an update, or an LLM model that will not load
for memory reasons are all diagnosed from the kernel release, the boot
arguments, and whether the machine is a container at all.

**`/proc/cmdline` is scrubbed, and the scrubbing is the load-bearing part of
this file.** A kernel command line routinely carries `rd.luks=...` arguments -
`rd.luks.name`, `rd.luks.options`, `rd.luks.uuid` - which name the encrypted
volume and, in the `options` case, can carry a passphrase or a keyfile
location. This sense writes its answer into a `Percept`, and a `Percept` is
injected verbatim into an LLM prompt on every subsequent turn and is a candidate
for a cloud fallback. So everything from `root=` onward is dropped, any
`rd.luks*` argument anywhere in the line is replaced with a marker, and the
number of omitted arguments is reported so the truncation is visible rather than
looking like a complete command line.

**"No container marker found" is not "this is physical hardware", and the two
are reported as different states.** `/.dockerenv` and `/run/.containerenv` are
markers written by Docker and Podman. A KVM/QEMU virtual machine has neither, so
their absence proves nothing about virtualisation. What can honestly be said
is: a marker was found, or none was found, or the filesystem could not be
consulted. Those are three answers and only the first two are about the
machine; the third is about this process's permissions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 3600.0
_POLL_INTERVAL = 3600.0

_PROC = Path("/proc")
_OSRELEASE = _PROC / "sys/kernel/osrelease"
_CMDLINE = _PROC / "cmdline"
_VERSION = _PROC / "version"
_STAT = Path("/etc/os-release")

#: Container markers. Both are single files created by the runtime on the host
#: root, so their absence is a fact about the runtime's convention, not proof of
#: bare metal.
_CONTAINER_MARKERS = {
    "/.dockerenv": "docker (/.dockerenv)",
    "/run/.containerenv": "podman (/run/.containerenv)",
}

#: Argument prefixes redacted wherever they appear in the line. The tail after
#: `root=` is dropped entirely, but `rd.luks` arguments are conventionally
#: written *before* it, so the redaction is not redundant.
_SECRET_ARG_PREFIXES = ("rd.luks",)

#: Tri-state, and the three values are not interchangeable.
VIRT_CONTAINER = "container-marker-present"
VIRT_NO_MARKER = "no-container-marker"
VIRT_UNKNOWN = "could-not-determine"


def _read_text(path: Path) -> Optional[str]:
    """File contents, or None when it could not be read at all."""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def read_osrelease() -> Optional[str]:
    """The kernel release string, e.g. `6.11.4-arch1-1`."""
    value = _read_text(_OSRELEASE)
    return value.strip() if value else None


def read_version() -> Optional[str]:
    """The full `/proc/version` line, including the build compiler."""
    value = _read_text(_VERSION)
    return value.strip() if value else None


def read_pretty_name() -> Optional[str]:
    """`PRETTY_NAME` from `/etc/os-release`, or None.

    Quoted in the file, so the quotes are stripped. Some distributions write
    `NAME` and no `PRETTY_NAME`; the version is then appended to `NAME` rather
    than reporting a distribution with no version, which would be wrong in the
    other direction.
    """
    text = _read_text(_STAT)
    if not text:
        return None
    fields: dict = {}
    for line in text.splitlines():
        key, _, raw = line.partition("=")
        fields[key.strip()] = raw.strip().strip('"')
    pretty = fields.get("PRETTY_NAME")
    if pretty:
        return pretty
    name = fields.get("NAME")
    version = fields.get("VERSION") or fields.get("VERSION_ID")
    if name and version:
        return f"{name} {version}"
    return name or None


def scrub_cmdline(raw: Optional[str]) -> Optional[dict]:
    """Split `/proc/cmdline` into the part that is safe to report and what went.

    Returns None when the file could not be read - a container with `/proc` not
    mounted, which is the case this whole sense has to be honest about.
    """
    if raw is None:
        return None
    tokens = raw.replace("\x00", " ").split()
    if not tokens:
        return None

    kept = []
    dropped = 0
    redacted = 0
    seen_root = False
    for token in tokens:
        if seen_root:
            dropped += 1
            continue
        if token.startswith("root="):
            seen_root = True
            kept.append("root=")
            continue
        if any(token.startswith(prefix) for prefix in _SECRET_ARG_PREFIXES):
            kept.append(f"{token.split('=', 1)[0]}=<redacted>")
            redacted += 1
            continue
        kept.append(token)

    return {"safe": " ".join(kept), "dropped": dropped, "redacted": redacted}


def read_virtualisation() -> dict:
    """Container marker state, as one of the three tri-state values."""
    found = []
    unreadable = False
    for marker, label in _CONTAINER_MARKERS.items():
        try:
            present = os.path.exists(marker)
        except OSError:
            unreadable = True
            continue
        if present:
            found.append(label)
    if found:
        return {"state": VIRT_CONTAINER, "markers": found}
    if unreadable:
        return {"state": VIRT_UNKNOWN, "markers": []}
    return {"state": VIRT_NO_MARKER, "markers": []}


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("kernel"):
        return f"Not reading kernel facts: {config.sense_allowed_reason('kernel')}."

    release = read_osrelease()
    version = read_version()
    cmdline = scrub_cmdline(_read_text(_CMDLINE))
    distribution = read_pretty_name()

    if release is None and version is None and cmdline is None:
        return (
            "Kernel facts are UNKNOWN: /proc could not be read, so neither the "
            "release, the build nor the boot arguments could be established. "
            "That is a statement about this process's access to /proc, not a "
            "claim about the kernel."
        )

    virt = read_virtualisation()
    lines = []
    if distribution:
        lines.append(f"distribution: {distribution}")
    if release:
        lines.append(f"kernel: {release}")
    if version:
        lines.append(f"build: {version}")
    if cmdline:
        line = f"boot arguments: {cmdline['safe'] or '(none reported)'}"
        withheld = cmdline["dropped"] + cmdline["redacted"]
        if withheld:
            line += (
                f"  [{withheld} argument(s) withheld - everything after root=, "
                f"plus {cmdline['redacted']} rd.luks argument(s): initramfs "
                f"arguments can name encrypted volumes and carry key options, "
                f"and this percept is sent to a model]"
            )
        lines.append(line)

    if virt["state"] == VIRT_CONTAINER:
        lines.append("virtualisation: running in a container ("
                     + ", ".join(virt["markers"]) + ")")
    elif virt["state"] == VIRT_UNKNOWN:
        lines.append(
            "virtualisation: COULD NOT DETERMINE - the container marker files "
            "could not be consulted, so nothing either way is claimed"
        )
    else:
        lines.append(
            "virtualisation: no container marker found. This is NOT the same as "
            "physical hardware: a KVM or QEMU virtual machine has no such "
            "marker either, and only the absence of one is established."
        )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc",
        metadata={
            "kernel": release,
            "distribution": distribution,
            "cmdline_arguments_withheld": cmdline["dropped"] if cmdline else None,
            "virtualisation": virt["state"],
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "kernel",
        "description": (
            "Report the running kernel release, the distribution's PRETTY_NAME, "
            "the kernel build string, the (sanitised) boot command line, and "
            "whether this is a container. Boot arguments after root= and any "
            "rd.luks argument are withheld, because they can name encrypted "
            "volumes or carry key options. Distinguishes 'a container marker "
            "was found' from 'no container marker found', which is not the same "
            "as physical hardware - a virtual machine has no marker either."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="kernel",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
