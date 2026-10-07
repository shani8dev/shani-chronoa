"""What can this machine actually do, asked at runtime rather than assumed.

Chronoa has two inventories of its own capabilities and both are wrong in the
same way. `tools/cli_matrix.py` generates a JSON matrix - 3,990 commands, 227
enabled units on a Shanios image - and it is correct *for the image it was run
against*. It is shipped alongside the package and describes a machine that is
not necessarily the one running it. Meanwhile `capabilities.MUTATING_TOOLS` says
*which tools exist* but nothing about whether they can work here.

That gap is why the learning layer has no signal. Features are structural -
tool name, argument keys, value shapes - because the only thing worth recording
about a call is how the machine answered, and nothing knows the machine yet.
11,590 recorded calls collapse to 344 distinct vectors because "did `ffmpeg`
exist" was never an input.

**This module asks the live machine and caches the answer**, because a probe per
call would cost more than the decision it informs. Three things are worth
asking, in ascending cost:

- **is this command present and runnable** - `shutil.which`, microseconds;
- **is this unit enabled** - a `systemctl is-enabled` per unit, so it is cached
  per service name rather than per call;
- **does this D-Bus name answer** - the most expensive and the most valuable,
  because a present command behind an absent service fails in a way nothing else
  predicts.

**A capability is reported three ways, not two.** The third - `broken` - is the
one that matters: a command that is present, runs, and produces an error is the
failure a capability check exists to catch, and treating presence as capability
is exactly the mistake that makes an assistant confidently recommend a dead end.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Probes are cached. A capability does not change while a turn is running, and
#: asking `systemctl` per tool call would cost more than it decides.
_TTL_SECONDS = 300.0

#: `systemctl is-enabled` costs about 30ms, so units are cached individually
#: rather than as one batch.
_UNIT_CACHE: Dict[str, bool] = {}

_CACHE: Dict[str, "Capabilities"] = {}
_CACHE_AT: float = 0.0


@dataclass
class Capabilities:
    """What this machine can do, at the moment it was asked."""

    commands: Dict[str, str] = field(default_factory=dict)
    units: Dict[str, bool] = field(default_factory=dict)
    #: Command name -> why it is not usable, when that is knowable cheaply.
    broken: Dict[str, str] = field(default_factory=dict)
    python: Dict[str, bool] = field(default_factory=dict)
    when: float = 0.0

    def has_command(self, name: str) -> bool:
        return name in self.commands

    def explain(self, tool: str) -> str:
        """A sentence a person can act on, or None if there is nothing to say."""
        if tool in self.broken:
            return self.broken[tool]
        if tool in self.commands:
            return ""
        return (f"{tool} is not installed on this machine"
                + ("" if not _UNIT_CACHE else ""))

    def alternatives_for(self, tool: str, candidates: Tuple[str, ...] = ()) -> List[str]:
        """Which of these related tools *can* run here.

        The point of asking the machine. A recommendation that names a tool this
        machine cannot run is worse than no recommendation, and the usual case -
        ffmpeg present but broken, sox present and working - is invisible to a
        static inventory.
        """
        return [c for c in candidates
                if c in self.commands and c not in self.broken]


#: The names worth probing. Not "everything" - 3,990 is not an inventory
#: Chronoa can reason with, it is a list. These are the ones whose absence or
#: breakage changes which path to a goal is viable.
_ALL_NAMES: Tuple[str, ...] = (
    # media
    "ffmpeg", "ffprobe", "sox", "play", "paplay", "pw-play", "vlc", "mpv",
    # images
    "magick", "convert", "identify", "zbarimg", "qrencode", "tesseract",
    "scanimage", "pdftoppm", "tesseract",
    # audio / models
    "piper", "espeak-ng", "espeak", "whisper-cli", "whisper-cpp",
    # documents
    "libreoffice", "pandoc", "pdftotext", "qpdf", "sqlite3", "duckdb",
    # network
    "ip", "ss", "ping", "traceroute", "mtr", "curl", "wget", "nc", "socat",
    "nmap", "ethtool", "iw", "iwconfig", "nmcli", "rfkill", "tailscale",
    # containers / vms
    "podman", "docker", "qemu-system-x86_64", "virsh", "vmspawn", "mkosi",
    # files
    "rsync", "rclone", "aria2c", "bat", "fd", "rg", "zstd", "xz", "bzip2",
    # system
    "systemctl", "journalctl", "loginctl", "gsettings", "busctl", "notify-send",
    "fwupdmgr", "timedatectl", "nmcli",
)


def _probe_commands(names: Tuple[str, ...] = _ALL_NAMES) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in names:
        found = shutil.which(name)
        if found:
            out[name] = found
    return out


def _probe_units(known: Tuple[str, ...] = ()) -> Dict[str, bool]:
    """Is this unit enabled? Cached per name; `systemctl` costs ~30ms each."""
    if shutil.which("systemctl") is None:
        return {}
    out: Dict[str, bool] = {}
    for unit in known:
        if unit in _UNIT_CACHE:
            out[unit] = _UNIT_CACHE[unit]
            continue
        try:
            done = subprocess.run(
                ["systemctl", "is-enabled", unit],
                capture_output=True, text=True, timeout=5, check=False)
            answer = done.stdout.strip()
            enabled = done.returncode == 0 and answer in ("enabled", "enabled-runtime",
                                                         "static", "indirect")
        except (OSError, subprocess.TimeoutExpired):
            enabled = False
        _UNIT_CACHE[unit] = enabled
        out[unit] = enabled
    return out


def _probe_python() -> Dict[str, bool]:
    """Importable here, or only on a developer machine.

    This is the check that would have caught the two numpy/symengine imports I
    added and then removed - `numpy` is importable in this dev environment and
    absent on a fresh image, and a module that imports it unconditionally is
    broken on exactly the machine it ships to.
    """
    import importlib.util
    out: Dict[str, bool] = {}
    for name in ("numpy", "httpx", "mcp", "gi", "PIL", "sounddevice",
                 "soundstretch", "symengine", "sympy", "duckdb"):
        try:
            out[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            out[name] = False
    return out


def capabilities(refresh: bool = False,
                 units: Tuple[str, ...] = ()) -> Capabilities:
    """Ask this machine. Cached for five minutes."""
    global _CACHE, _CACHE_AT
    now = time.time()
    if not refresh and _CACHE and (now - _CACHE_AT) < _TTL_SECONDS:
        return _CACHE
    cap = Capabilities(
        commands=_probe_commands(),
        units=_probe_units(units),
        python=_probe_python(),
        when=now,
    )
    _CACHE = cap
    _CACHE_AT = now
    return cap


def describe(cap: Optional[Capabilities] = None) -> str:
    """A short inventory a person can read, for the settings window or a reply."""
    cap = cap or capabilities()
    lines = [f"On this machine: {len(cap.commands)} of the commands Chronoa knows "
             f"how to use are present.", ""]
    groups = {
        "media": ("ffmpeg", "ffprobe", "sox", "pw-play", "mpv"),
        "images": ("magick", "zbarimg", "qrencode", "tesseract", "scanimage"),
        "speech": ("piper", "espeak-ng", "espeak", "whisper-cli"),
        "documents": ("libreoffice", "pdftotext", "pandoc", "sqlite3"),
        "network": ("ip", "ss", "curl", "wget", "nmcli", "iw"),
        "virtualisation": ("podman", "docker", "qemu-system-x86_64", "virsh"),
    }
    for title, names in groups.items():
        have = [n for n in names if cap.has_command(n)]
        absent = [n for n in names if not cap.has_command(n)]
        line = f"  {title:<16} {', '.join(have) or 'none'}"
        if absent:
            line += f"   (no {', '.join(absent)})"
        lines.append(line)
    # A broken tool is worse than no tool in a list: it looks usable and fails
    # late. Name it, and which of its neighbours in the same group could do the
    # job instead, because that is actionable where "broken" alone is not.
    broken = [n for n in (m for g in groups.values() for m in g) if n in cap.broken]
    for name in broken:
        # Candidates are the *working* tools in the same group. `alternatives_for`
        # already keeps only the ones present and not broken, so a broken sibling
        # cannot be named as a workaround for another broken tool.
        group = next((g for g in groups.values() if name in g), ())
        alternatives = cap.alternatives_for(name, tuple(g for g in group if g != name))
        lines.append(
            f"  {name} is installed but not usable: {cap.broken[name]}")
        if alternatives:
            lines.append(
                f"    could be worked around with: {', '.join(alternatives)}")
    present = [m for m, ok in cap.python.items() if ok]
    absent = [m for m, ok in cap.python.items() if not ok]
    lines.append("")
    lines.append(f"  python modules present: {', '.join(present) or 'none'}")
    if absent:
        lines.append(f"  python modules absent : {', '.join(absent)}")
    return "\n".join(lines)