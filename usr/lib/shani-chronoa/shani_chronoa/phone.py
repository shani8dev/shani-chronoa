"""The user's phone, through the desktop's own phone link.

- GNOME: GSConnect (the image's gnome-shell extension). Devices are read from
  its D-Bus ObjectManager; ring / ping / share go through its daemon's own
  command line (`daemon.js --ring -d ID`, as GSConnect documents).
- Plasma: KDE Connect (`kdeconnect-cli`), battery through its D-Bus module.

What is deliberately not here: sending SMS, reading the phone's
notifications, remote input. Each speaks for the user to someone else or reads
other people's messages, and needs its own decision - ringing, pinging and
sending a file to one's own paired phone do not.

No service, no paired device and no reachable device are three different
answers, and each is reported as itself.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from typing import NamedTuple, Optional

GSCONNECT_BUS = "org.gnome.Shell.Extensions.GSConnect"
GSCONNECT_PATH = "/org/gnome/Shell/Extensions/GSConnect"
_TIMEOUT = 15


class Device(NamedTuple):
    id: str
    name: str
    reachable: bool
    paired: bool


class PhoneUnavailable(Exception):
    """No phone link service is running - which is not "no phone"."""


def _gsconnect_daemon() -> Optional[str]:
    hits = sorted(glob.glob("/usr/share/gnome-shell/extensions/gsconnect@*/service/daemon.js")
                  + glob.glob(os.path.expanduser("~/.local/share/gnome-shell/extensions/gsconnect@*/service/daemon.js")))
    return hits[0] if hits and shutil.which("gjs") else None


def backend() -> Optional[str]:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    kde = shutil.which("kdeconnect-cli") is not None
    gs = _gsconnect_daemon() is not None
    if "kde" in desktop or "plasma" in desktop:
        return "kdeconnect" if kde else ("gsconnect" if gs else None)
    return "gsconnect" if gs else ("kdeconnect" if kde else None)


def _run(argv: "list[str]") -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)


def devices() -> "list[Device]":
    which = backend()
    if which is None:
        raise PhoneUnavailable("no phone link is installed (GSConnect on GNOME, KDE Connect on Plasma)")
    if which == "kdeconnect":
        everyone = _run(["kdeconnect-cli", "-l", "--id-name-only"])
        if everyone.returncode != 0:
            raise PhoneUnavailable(f"KDE Connect did not answer ({(everyone.stderr or '').strip()[:120]})")
        reachable = _run(["kdeconnect-cli", "-a", "--id-only"])
        up = set((reachable.stdout or "").split())
        out = []
        for line in (everyone.stdout or "").splitlines():
            ident, _, name = line.strip().partition(" ")
            if ident:
                out.append(Device(ident, name.strip() or ident, ident in up, True))
        return out
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        reply = Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
            GSCONNECT_BUS, GSCONNECT_PATH, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            None, None, Gio.DBusCallFlags.NONE, _TIMEOUT * 1000, None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error: the extension is off, or no session
        raise PhoneUnavailable(f"GSConnect is not running ({str(exc)[:120]}); it is a GNOME Shell extension "
                               "that has to be switched on") from exc
    out = []
    for _path, ifaces in reply.unpack()[0].items():
        d = ifaces.get("org.gnome.Shell.Extensions.GSConnect.Device")
        if d:
            out.append(Device(str(d.get("Id", "")), str(d.get("Name", "")), bool(d.get("Connected")),
                              bool(d.get("Paired"))))
    return out


def battery(device: Device) -> "tuple[int, bool] | None":
    """(percent, charging) where the link reports it; None where it does not."""
    if backend() != "kdeconnect" or shutil.which("busctl") is None:
        return None
    base = ["busctl", "--user", "get-property", "org.kde.kdeconnect",
            f"/modules/kdeconnect/devices/{device.id}/battery", "org.kde.kdeconnect.device.battery"]
    charge, charging = _run(base + ["charge"]), _run(base + ["isCharging"])
    m = re.search(r"-?\d+", charge.stdout or "")
    if charge.returncode != 0 or not m or int(m.group(0)) < 0:
        return None
    return int(m.group(0)), "true" in (charging.stdout or "")


def act(device: Device, action: str, payload: str = "") -> "tuple[bool, str]":
    """ring, ping or share (a path or URL) to one paired, reachable device."""
    which = backend()
    if which == "kdeconnect":
        argv = {"ring": ["kdeconnect-cli", "-d", device.id, "--ring"],
                "ping": ["kdeconnect-cli", "-d", device.id, "--ping-msg", payload or "Ping from Chronoa"],
                "share": ["kdeconnect-cli", "-d", device.id, "--share", payload]}[action]
    else:
        daemon = _gsconnect_daemon()
        flag = "--share-link" if action == "share" and re.match(r"^https?://", payload) else "--share-file"
        argv = {"ring": ["gjs", "-m", daemon, "-d", device.id, "--ring"],
                "ping": ["gjs", "-m", daemon, "-d", device.id, "--ping"],
                "share": ["gjs", "-m", daemon, "-d", device.id, flag, payload]}[action]
    proc = _run(argv)
    return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()[:200]
