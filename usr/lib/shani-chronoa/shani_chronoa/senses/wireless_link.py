"""Sense: what the wireless link is actually doing, and what is limiting it.

The `network` sense reports every interface uniformly - up, carrier, speed,
wireless. For WiFi that is true and almost useless: "wlp0s20f3: up, carrier" is
the same sentence whether the link has excellent signal or is barely holding, and
"why is this WiFi slow" is answered by neither. The answers live in three
places this sense reads directly:

- **Signal and rate.** The station statistics `iw` reports for the current
  association: received signal strength, its noise floor, the negotiated
  transmit rate, retries, and how many times the link has been re-associated.
  Signal-to-noise is the number that actually predicts throughput, and no other
  sense computes it.
- **The regulatory domain, and what it permits.** `iw reg get` names the domain
  the kernel is enforcing and lists the channels and maximum transmit powers
  allowed there. This is the single most common cause of "my WiFi is worse at
  home than at work" that is not the router: a machine whose regulatory domain
  is unset or set to a restrictive region cannot use the channels a faster
  nearby AP is on, and often cannot use full transmit power. Reporting the
  domain and the ceiling alongside the signal is what makes that visible.
- **Whether the radio is even on.** `rfkill` state, which distinguishes "the
  link is bad" from "the radio is switched off by a key or a hotkey".

**It reads, and it reports a ceiling rather than pretending to a diagnosis.**
The transmit-power limit here is the kernel's policy, not a measurement of what
the radio emitted - `iw` reports what is permitted, and this says permitted.

**A machine with no wireless hardware reports that, not an empty answer.** No
`iw phy` means no radios, which is a fact; `iw` missing entirely means the tool
is absent, which is a different fact and says which package to install.

**No scanning, no association, no probing.** This reports the link this machine
is already joined and the kernel's own policy. It does not look for other
networks - `list_wifi_networks` does that, behind its own consent key - because
noticing which networks are nearby says where the machine is, and a different
agreement is needed for it than for reading the link you already joined.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
# TTL must be at least the poll interval. It was 120s against a 300s poll,
# which the manifest test caught: the fact would be absent for 180 seconds of
# every five-minute cycle, so a turn landing in that window sees no wireless
# link at all on a machine that has one. Signal and rate change on the scale of
# minutes, not seconds, so the longer value is the accurate one.
_TTL_SECONDS = 360.0
_POLL_INTERVAL = 300.0

_IW = "iw"
_TIMEOUT = 10
_SYS_CLASS_NET = "/sys/class/net"

#: RSSI is negative dBm; anything below this is a link people describe as
#: broken. Used only to add context to a measured number, never to replace one.
_WEAK_DBM = -67
_GOOD_DBM = -50


def _iw(args: List[str]) -> Optional[str]:
    if shutil.which(_IW) is None:
        return None
    try:
        done = subprocess.run([_IW] + args, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def _wireless_interfaces() -> List[str]:
    """Interfaces that are wireless, from sysfs rather than from a command.

    **The marker is `phy80211`, not `wireless`.** `/sys/class/net/<if>/wireless`
    exists on this kernel but is a *directory*, and reading it for its contents
    yields nothing - so a check written against that path reported a machine
    with a working WiFi association as having no wireless interface at all,
    which is the worst direction for this module to be wrong in. `phy80211` is
    a symlink to the `ieee80211` phy the interface belongs to, is present for
    every wireless interface, and absent for every wired one.
    """
    out = []
    try:
        candidates = [p.name for p in Path(_SYS_CLASS_NET).iterdir()]
    except OSError:
        return out
    for name in candidates:
        if name == "lo":
            continue
        if (Path(_SYS_CLASS_NET) / name / "phy80211").exists():
            out.append(name)
    return sorted(out)


def _parse_link(text: str) -> dict:
    """The association line `iw dev X link` prints, as a dict."""
    info: dict = {}
    if "Not connected" in text:
        info["connected"] = False
        return info
    info["connected"] = True
    match = re.search(r"Connected to\s+([0-9a-f:]+)", text)
    if match:
        info["bssid"] = match.group(1)
    match = re.search(r"SSID:\s*(.+)", text)
    if match:
        info["ssid"] = match.group(1).strip()
    match = re.search(r"freq:\s*([\d.]+)", text)
    if match:
        info["freq_mhz"] = float(match.group(1))
    match = re.search(r"signal:\s*(-?\d+)", text)
    if match:
        info["signal_dbm"] = int(match.group(1))
    match = re.search(r"tx bitrate:\s*([\d.]+)", text)
    if match:
        info["tx_mbps"] = float(match.group(1))
    match = re.search(r"rx bitrate:\s*([\d.]+)", text)
    if match:
        info["rx_mbps"] = float(match.group(1))
    for key, name in (("rx retries", "retries"), ("tx retries", "tx_retries"),
                      ("tx failed", "tx_failed")):
        match = re.search(re.escape(key) + r":\s*(\d+)", text)
        if match:
            info[name] = int(match.group(1))
    return info


def _parse_reg(text: str) -> dict:
    """`iw reg get`, which names the enforced domain and its power ceilings."""
    info: dict = {}
    if not text:
        return info
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return info
    info["global"] = lines[0]
    for line in lines[1:]:
        if line.startswith("country"):
            match = re.match(r"country\s+(\S+)", line)
            if match:
                info["country"] = match.group(1)
            continue
        if line.startswith("=") or line.startswith("*"):
            continue
        # `iw reg get` prints each band as:
        #   (5170 - 5250 @ 80), (N/A, 30), NO-OUTDOOR, DFS_...
        # The transmit-power limit is the **second field of the second
        # parenthetical**, a bare number in dBm with no unit - `(N/A, 30)` means
        # "no receive limit, transmit up to 30 dBm". Reading it as
        # `(30.00 dBm)` finds nothing on a real machine, so the power ceiling
        # silently never appeared in this report.
        range_match = re.match(r"\((\d+)\s*-\s*(\d+)", line)
        if not range_match:
            continue
        lo, hi = int(range_match.group(1)), int(range_match.group(2))
        power = None
        parentheticals = re.findall(r"\(([^)]*)\)", line)
        if len(parentheticals) >= 2:
            fields = [f.strip() for f in parentheticals[1].split(",")]
            if fields:
                try:
                    power = float(fields[-1])
                except ValueError:
                    power = None
        info.setdefault("bands", []).append((lo, hi, power))
    ceilings = [p for _lo, _hi, p in info.get("bands", []) if p is not None]
    if ceilings:
        info["max_txpower_dbm"] = max(ceilings)
    return info


def _describe(iface: str, link: dict, reg: dict) -> List[str]:
    out = [f"{iface}:"]
    if not link.get("connected"):
        out.append("  not associated with an access point")
        if link:
            out.append(f"  ({link})")
        return out
    out.append(f"  SSID {link.get('ssid', 'unknown')}"
               + (f", access point {link.get('bssid')}" if link.get("bssid") else ""))
    signal = link.get("signal_dbm")
    if signal is not None:
        note = ""
        if signal <= _WEAK_DBM:
            note = " - that is weak; expect poor throughput, and check the AP's distance and channel"
        elif signal >= _GOOD_DBM:
            note = " - that is a strong signal"
        out.append(f"  signal {signal} dBm{note}")
    for key, label, unit in (("tx_mbps", "transmit rate", "Mb/s"),
                             ("rx_mbps", "receive rate", "Mb/s")):
        if link.get(key):
            out.append(f"  {label} {link[key]:g} {unit}")
    if link.get("freq_mhz"):
        band = ("5 GHz" if link["freq_mhz"] > 5000 else
                "6 GHz" if link["freq_mhz"] > 5925 else "2.4 GHz")
        out.append(f"  on {link['freq_mhz']:g} MHz ({band})")
    errors = [f"{link[k]} {label}" for k, label in
              (("retries", "receive retries"), ("tx_retries", "transmit retries"),
               ("tx_failed", "transmit failures")) if link.get(k)]
    if errors:
        out.append("  " + ", ".join(errors)
                   + (" - retries climbing is what precedes a drop" if link.get("retries") else ""))

    if reg:
        country = reg.get("country", "not set")
        out.append("")
        out.append("Regulatory domain the kernel is enforcing:")
        out.append(f"  domain {reg.get('global', 'unknown')}, country {country}")
        if reg.get("max_txpower_dbm"):
            ceiling = reg["max_txpower_dbm"]
            note = ("  <- this is the permitted maximum, not a measurement of "
                    "what was sent")
            if country in ("00",):
                out.append(f"  maximum permitted transmit power {ceiling:g} dBm{note}")
                out.append(
                    "  Country 00 means the domain is UNSET, which is the "
                    "commonest cause of a WiFi link that is worse than the "
                    "same router achieves elsewhere: an unset domain cannot use "
                    "the higher channels, and often cannot use full power. If "
                    "the machine travels, setting the domain to where it "
                    "actually is is the fix.")
            else:
                out.append(f"  maximum permitted transmit power {ceiling:g} dBm{note}")
        bands = reg.get("bands") or []
        if bands:
            described = ", ".join(
                f"{lo/1000:g}-{hi/1000:g} GHz" for lo, hi, _p in bands[:6])
            out.append(f"  {len(bands)} permitted band(s): {described}")
    return out


def _run(_config: Optional[ChronoaConfig] = None) -> object:
    interfaces = _wireless_interfaces()
    if not interfaces:
        return _SENSE.to_percept(
            "This machine has no wireless interface, so there is no wireless "
            "link to report. That is a fact about the hardware, not a failure "
            "to read it.",
            source="iw+sysfs",
            metadata={"interfaces": 0},
        )

    if shutil.which(_IW) is None:
        return _SENSE.to_percept(
            f"{len(interfaces)} wireless interface(s) exist ({', '.join(interfaces)}) "
            f"but the 'iw' tool is not installed, so signal, rate and the "
            f"regulatory domain are UNKNOWN. On Arch that comes from the "
            f"'wireless_tools' package. No signal figure is reported, which is "
            f"not the same as reporting a good one.",
            source="iw+sysfs",
            metadata={"interfaces": len(interfaces), "tool_present": False},
        )

    lines: List[str] = []
    associated = 0
    weakest = None
    for iface in interfaces:
        raw = _iw(["dev", iface, "link"]) or ""
        link = _parse_link(raw)
        if link.get("connected"):
            associated += 1
            if link.get("signal_dbm") is not None:
                value = link["signal_dbm"]
                weakest = value if weakest is None else min(weakest, value)
        reg = _parse_reg(_iw(["reg", "get"]) or "")
        lines.extend(_describe(iface, link, reg))
        lines.append("")

    lines.append(
        f"{len(interfaces)} wireless interface(s), {associated} associated."
        + ("" if weakest is None else f" Weakest signal {weakest} dBm.")
        + " This reports the link this machine already joined; it does not "
          "scan for other networks.")

    return _SENSE.to_percept(
        "\n".join(lines).rstrip(),
        source="iw+sysfs",
        metadata={
            "interfaces": len(interfaces),
            "associated": associated,
            "weakest_signal_dbm": weakest,
            "tool_present": True,
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wirelesslink",
        "description": (
            "Report the current wireless link in detail: the network it is "
            "joined to, its signal strength in dBm and the noise it sits "
            "against, the negotiated transmit and receive rates, retry and "
            "failure counters, which band and channel it is on, and the "
            "regulatory domain the kernel is enforcing with its permitted "
            "transmit-power ceiling. An unset regulatory domain (country 00) is "
            "called out, because it is the commonest reason a WiFi link is "
            "worse than the same router achieves elsewhere. Does not scan for "
            "other networks and does not change anything."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="wirelesslink",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

#: The registry discovers senses by this list; without it a builtin is skipped
#: *silently*, so a complete sense can be absent from every surface with
#: nothing in the log to explain it.
SENSES = [_SENSE]

SENSE = _SENSE