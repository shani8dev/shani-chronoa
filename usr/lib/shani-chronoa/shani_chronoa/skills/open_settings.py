"""Skill: open the desktop's Settings at a specific page ("open Wi-Fi settings").

A skill of its own rather than a `page` argument on `open_application`:
that skill resolves a *name* to an installed .desktop file through Gio and its
post-condition checks the launched executable, while a settings page is a fixed
argv per desktop (`gnome-control-center wifi`, `systemsettings kcm_kscreen`).
Folding the two together would make `name` optional, make the post-condition
guess which of the two paths ran, and give the model one tool with two
unrelated jobs. Here the page list is closed: an unknown page is refused with
the pages that do exist, rather than opening Settings at its front page and
calling that success.

Panel ids, verified rather than remembered:

- GNOME (gnome-control-center 50.3): `gnome-control-center --list` and the
  `Exec=` lines of `/usr/share/applications/gnome-*-panel.desktop`. In GNOME
  50 Users, About, Date & Time and Region are sub-pages of `system`
  (`gnome-control-center system users`), not top-level panels.
- Plasma 6.7: the KCM plugin file names shipped in the plasma-desktop,
  plasma-workspace, plasma-nm, plasma-pa, bluedevil, kscreen, powerdevil,
  print-manager and kinfocenter packages. Opened with `systemsettings <kcm>`
  (the whole window, at that page), else `kcmshell6 <kcm>` (just the page).
  Plasma has no single Privacy page, so that one is refused on Plasma by name.

Opening a window changes nothing on the machine, so this is not consent-gated,
the same as `open_application`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from shani_chronoa.skills import Skill

#: page -> (gnome-control-center args, Plasma KCM or None, spoken label)
PAGES = {
    "wifi": (("wifi",), "kcm_networkmanagement", "Wi-Fi"),
    "network": (("network",), "kcm_networkmanagement", "Network"),
    "bluetooth": (("bluetooth",), "kcm_bluetooth", "Bluetooth"),
    "display": (("display",), "kcm_kscreen", "Display"),
    "sound": (("sound",), "kcm_pulseaudio", "Sound"),
    "power": (("power",), "kcm_powerdevilprofilesconfig", "Power"),
    "privacy": (("privacy",), None, "Privacy"),
    "keyboard": (("keyboard",), "kcm_keyboard", "Keyboard"),
    "mouse": (("mouse",), "kcm_mouse", "Mouse"),
    "touchpad": (("mouse",), "kcm_touchpad", "Touchpad"),
    "printers": (("printers",), "kcm_printer_manager", "Printers"),
    "users": (("system", "users"), "kcm_users", "Users"),
    "about": (("system", "about"), "kcm_about-distro", "About"),
    "date_time": (("system", "datetime"), "kcm_clock", "Date & Time"),
    "region": (("system", "region"), "kcm_regionandlang", "Region & Language"),
    "notifications": (("notifications",), "kcm_notifications", "Notifications"),
    "background": (("background",), "kcm_wallpaper", "Background"),
    "accessibility": (("universal-access",), "kcm_access", "Accessibility"),
}

#: spoken names -> page. Normalised first: lower case, "settings"/"the"/"page"
#: dropped, punctuation and hyphens to spaces.
ALIASES = {
    "wi fi": "wifi", "wireless": "wifi", "wlan": "wifi",
    "ethernet": "network", "wired": "network", "internet": "network", "networking": "network",
    "vpn": "network", "proxy": "network",
    "displays": "display", "screen": "display", "monitor": "display", "monitors": "display",
    "resolution": "display",
    "audio": "sound", "volume": "sound", "speakers": "sound", "microphone": "sound",
    "battery": "power", "power management": "power", "energy": "power",
    "keyboard shortcuts": "keyboard", "shortcuts": "keyboard",
    "mouse and touchpad": "mouse", "mouse touchpad": "mouse", "mouse & touchpad": "mouse", "mice": "mouse",
    "trackpad": "touchpad", "touch pad": "touchpad",
    "printer": "printers", "printing": "printers",
    "user": "users", "accounts": "users", "user accounts": "users",
    "about this computer": "about", "about this system": "about", "system info": "about",
    "system information": "about",
    "date": "date_time", "time": "date_time", "date and time": "date_time", "date & time": "date_time",
    "clock": "date_time", "datetime": "date_time", "time zone": "date_time", "timezone": "date_time",
    "language": "region", "region and language": "region", "region & language": "region",
    "locale": "region",
    "notification": "notifications",
    "wallpaper": "background", "desktop background": "background",
    "universal access": "accessibility", "a11y": "accessibility",
}

#: How long the launched Settings must stay up to count as "opened". A GNOME
#: control center that is already running forwards the request and exits 0
#: straight away, which is also reported (as "asked", not "opened").
ALIVE_SECONDS = 1.5

SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_settings",
        "description": ("Open the system Settings app at a specific page, e.g. 'wifi', 'bluetooth', "
                        "'display', 'sound', 'power', 'privacy', 'keyboard', 'mouse', 'touchpad', "
                        "'printers', 'users', 'about', 'date_time', 'region', 'notifications', "
                        "'background', 'accessibility'. GNOME and KDE Plasma. Use open_application "
                        "for Settings without a page."),
        "parameters": {"type": "object", "properties": {
            "page": {"type": "string", "description": "The settings page, e.g. 'wifi' or 'display'."}},
            "required": ["page"]},
    },
}


def normalise(raw: str) -> str:
    text = raw.strip().lower().replace("-", " ").replace("_", " ")
    text = re.sub(r"[^\w& ]+", " ", text)
    words = [w for w in text.split() if w not in ("settings", "setting", "the", "page", "panel", "my", "open")]
    text = " ".join(words)
    if text.replace(" ", "_") in PAGES:
        return text.replace(" ", "_")
    if text.replace(" ", "") in PAGES:
        return text.replace(" ", "")
    return ALIASES.get(text, "")


def desktop() -> str:
    """'plasma', 'gnome' or '' from XDG_CURRENT_DESKTOP (e.g. 'ubuntu:GNOME', 'KDE')."""
    parts = {p.strip().upper() for p in (os.environ.get("XDG_CURRENT_DESKTOP") or "").split(":")}
    if "KDE" in parts:
        return "plasma"
    if "GNOME" in parts:
        return "gnome"
    return ""


def argv_for(page: str) -> "tuple[list, str]":
    """(argv, problem). Exactly one of the two is empty."""
    gnome_args, kcm, label = PAGES[page]
    which = desktop()
    gnome = shutil.which("gnome-control-center")
    plasma = shutil.which("systemsettings") or shutil.which("kcmshell6")
    if which == "" and (gnome or plasma):
        which = "gnome" if gnome else "plasma"
    if which == "gnome":
        if not gnome:
            return [], "This is a GNOME session but gnome-control-center is not installed."
        return ["gnome-control-center", *gnome_args], ""
    if which == "plasma":
        if kcm is None:
            return [], (f"KDE Plasma has no single {label} settings page. Open System Settings and "
                        f"look under the related section instead.")
        if shutil.which("systemsettings"):
            return ["systemsettings", kcm], ""
        if shutil.which("kcmshell6"):
            return ["kcmshell6", kcm], ""
        return [], "This is a KDE Plasma session but neither systemsettings nor kcmshell6 is installed."
    return [], ("No supported Settings app was found: this works with GNOME (gnome-control-center) "
                "and KDE Plasma (systemsettings).")


def _known() -> str:
    return ", ".join(PAGES)


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return f"open_settings needs a page, one of: {_known()}."
    raw = arguments.get("page")
    if not isinstance(raw, str) or not raw.strip():
        return f"No settings page given. Known pages: {_known()}."
    page = normalise(raw)
    if not page:
        return f"There is no settings page called {raw.strip()!r}. Known pages: {_known()}."
    argv, problem = argv_for(page)
    if problem:
        return problem
    label = PAGES[page][2]
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=err, start_new_session=True)
        except OSError as exc:
            return f"Could not start {argv[0]}: {exc}."
        try:
            code = proc.wait(timeout=ALIVE_SECONDS)
        except subprocess.TimeoutExpired:
            return f"Opened {label} settings ({' '.join(argv)})."
        err.seek(0)
        message = err.read().decode("utf-8", "replace").strip()
    if code == 0:
        # an already-running Settings window took the request and this copy exited
        return f"Asked the running Settings window to show {label} ({' '.join(argv)})."
    return (f"{argv[0]} could not open {label} settings (exit {code}): "
            f"{(message.splitlines() or ['no message'])[-1][:200]}")


def _post_condition(arguments: dict):
    """Is a Settings process running now? It cannot say *which page* is showing,
    so the evidence names only the process; None when the page was refused."""
    page = normalise(arguments.get("page") or "") if isinstance(arguments, dict) and isinstance(
        arguments.get("page"), str) else ""
    if not page:
        return None
    argv, problem = argv_for(page)
    if problem:
        return None
    comm = os.path.basename(argv[0])[:15]  # the kernel truncates comm to 15 characters
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/comm", encoding="utf-8", errors="replace") as handle:
                    if handle.read().strip() == comm:
                        return True, f"{argv[0]} is running"
            except OSError:
                continue
    return False, f"no {argv[0]} process is running"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="open_settings", schema=SCHEMA, run=_run)]
