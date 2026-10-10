"""Skill: what is charging this machine - AC or USB-C, Power Delivery, and its wattage when the firmware says.

Read from the kernel only (/sys/class/power_supply, /sys/class/typec and
/sys/class/usb_power_delivery), so nothing can be missing. Many machines' UCSI
firmware does not expose the charger's offers; then the wattage is reported as
not reported, never as a guess.
"""

from __future__ import annotations

from pathlib import Path

from shani_chronoa.skills import Skill

POWER_SUPPLY_DIR = Path("/sys/class/power_supply")
TYPEC_DIR = Path("/sys/class/typec")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "charger_info",
        "description": ("Report what is powering this machine: on battery or plugged in, AC adapter or USB-C, "
                        "whether USB Power Delivery is in use, and the charger's wattage when the firmware "
                        "reports it, plus battery charge and rate."),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _pd_watts(pd: Path) -> "float | None":
    best = None
    for pdo in pd.glob("source-capabilities/*"):
        v = _read(pdo / "voltage") or _read(pdo / "maximum_voltage")
        i = _read(pdo / "maximum_current")
        w = _read(pdo / "maximum_power")
        try:
            watts = int(w) / 1000 if w else (int(v.rstrip("mV")) / 1000) * (int(i.rstrip("mA")) / 1000)
        except ValueError:
            continue
        best = max(best or 0, watts)
    return best


def _run(arguments: dict) -> str:
    lines, plugged = [], False
    for dev in sorted(POWER_SUPPLY_DIR.iterdir()) if POWER_SUPPLY_DIR.is_dir() else []:
        kind, online = _read(dev / "type"), _read(dev / "online")
        if kind in ("Mains", "USB") and online == "1":
            plugged = True
            usb = _read(dev / "usb_type")
            active = next((t.strip("[]") for t in usb.split() if t.startswith("[")), "")
            lines.append(f"plugged in through {'USB-C' if kind == 'USB' else 'an AC adapter'}"
                         + (f" ({active})" if active else ""))
        if kind == "UPS":
            # A UPS answers both halves, exactly as it does for the powerstate
            # trigger: `online` is whether utility power is reaching it, so it is
            # a mains source and NOT counting it here reported "Running on
            # battery" on a machine sitting on mains. `capacity` is its own
            # charge, so it is a battery source too.
            if online == "1":
                plugged = True
            cap, status = _read(dev / "capacity"), _read(dev / "status")
            if online == "1":
                lines.append("on mains through a UPS")
            else:
                lines.append("ON BATTERY - the UPS is carrying the machine")
            if cap:
                lines.append(f"UPS battery {cap}%"
                             + (f" ({status.lower()})" if status else ""))
        if kind == "Battery" and _read(dev / "scope") != "Device":
            cap, status = _read(dev / "capacity"), _read(dev / "status")
            rate = _read(dev / "power_now")
            watts = f", {int(rate) / 1e6:.1f} W" if rate.isdigit() and int(rate) > 0 else ""
            lines.append(f"battery {cap}% ({status.lower()}{watts})" if cap else "battery present")
    for partner in sorted(TYPEC_DIR.glob("port*-partner")) if TYPEC_DIR.is_dir() else []:
        pd = partner / "usb_power_delivery"
        if pd.exists():
            w = _pd_watts(pd.resolve())
            lines.append(f"USB-C partner on {partner.name.split('-')[0]} speaks Power Delivery"
                         + (f", offering up to {w:.0f} W" if w else "; this firmware does not report its wattage"))
    if not lines:
        return "The kernel reports no power supplies here (a desktop on mains with no battery reports none)."
    lines = sorted(dict.fromkeys(lines), key=lambda l: (not l.startswith("plugged"), l.startswith("USB-C partner")))
    head = "" if plugged else "Running on battery. "
    return head + "; ".join(lines)[:1].upper() + "; ".join(lines)[1:] + "."


SKILLS = [Skill(name="charger_info", schema=SCHEMA, run=_run)]
