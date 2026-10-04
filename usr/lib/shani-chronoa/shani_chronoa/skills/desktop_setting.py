"""Skill: read or change a desktop setting from a fixed list - on GNOME (gsettings) and Plasma (kconfig).

An allowlist, never "any key": each entry names the GNOME schema/key and the
Plasma file/group/key it means, with the values it may take. A free-form key
would let a model rewrite any of the desktop's ~200 schemas; a list does not.
Every change is read back (POST_CONDITION), and a setting the current desktop
does not have says so rather than writing the other desktop's key.

Plasma writes go through `kwriteconfig6 --notify`, so running apps pick up the
change; a few (cursor size) apply to windows opened afterwards. Gated by
`appearance-control-enabled`, the same permission set_theme and set_scaling
use - "change how this desktop looks and behaves". Reading needs none.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import desktop_session
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "appearance-control-enabled"
_TIMEOUT = 10

#: name -> description, value kind, GNOME (schema, key, to_gs, from_gs), Plasma (file, group, key, type, to_k, from_k)
_B = (lambda v: "true" if v else "false", lambda s: s.strip() == "true")
SETTINGS = {
    "animations": ("Window and menu animations", "bool",
                   ("org.gnome.desktop.interface", "enable-animations", *_B),
                   ("kdeglobals", "KDE", "AnimationDurationFactor", "", lambda v: "1" if v else "0",
                    lambda s: (s.strip() or "1") not in ("0", "0.0"))),
    "cursor_size": ("Mouse pointer size in pixels (24 is normal; 32, 48, 64 are larger)", "int",
                    ("org.gnome.desktop.interface", "cursor-size", str, lambda s: int(s.split()[-1])),
                    ("kcminputrc", "Mouse", "cursorSize", "", str, lambda s: int(s.strip() or 24))),
    "single_click": ("Open files and folders with a single click", "bool", None,
                     ("kdeglobals", "KDE", "SingleClick", "bool", lambda v: "true" if v else "false",
                      lambda s: s.strip() == "true")),
    "clock_24h": ("Show the top-bar clock in 24-hour format", "bool",
                  ("org.gnome.desktop.interface", "clock-format", lambda v: "'24h'" if v else "'12h'",
                   lambda s: "24h" in s), None),
    "clock_seconds": ("Show seconds in the top-bar clock", "bool",
                      ("org.gnome.desktop.interface", "clock-show-seconds", *_B), None),
    "battery_percentage": ("Show the battery percentage in the top bar", "bool",
                           ("org.gnome.desktop.interface", "show-battery-percentage", *_B), None),
    "hot_corner": ("Open the overview when the pointer hits the top-left corner", "bool",
                   ("org.gnome.desktop.interface", "enable-hot-corners", *_B), None),
    "natural_scroll": ("Touchpad natural (reverse) scrolling", "bool",
                       ("org.gnome.desktop.peripherals.touchpad", "natural-scroll", *_B), None),
    "tap_to_click": ("Tap the touchpad to click", "bool",
                     ("org.gnome.desktop.peripherals.touchpad", "tap-to-click", *_B), None),
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "desktop_setting",
        "description": ("Read or change one desktop setting from a fixed list: "
                        + "; ".join(f"{k} ({v[0].lower()})" for k, v in SETTINGS.items())
                        + ". Changing requires the 'appearance-control-enabled' consent key."),
        "parameters": {"type": "object", "properties": {
            "setting": {"type": "string", "enum": list(SETTINGS)},
            "value": {"type": "string", "description": "To change it: true/false, or a number for cursor_size. "
                                                       "Omit to read it."},
        }, "required": ["setting"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, f"changing desktop settings is turned off (enable '{_CONSENT_KEY}' in Settings)"
    return True, ""


def desktop() -> str:
    """This session's desktop in this module's own terms ("plasma" for KDE), from desktop_session."""
    kind = desktop_session.kind()
    return "plasma" if kind == "kde" else kind


def _cmd(argv: "list[str]") -> "subprocess.CompletedProcess | None":
    if shutil.which(argv[0]) is None:
        return None
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def read(name: str, where: str):
    """The setting's current value on this desktop, or raises LookupError with why."""
    _, _, gnome, plasma = SETTINGS[name]
    if where == "gnome" and gnome:
        proc = _cmd(["gsettings", "get", gnome[0], gnome[1]])
        if proc is None or proc.returncode != 0:
            raise LookupError(f"gsettings could not read {gnome[0]} {gnome[1]}")
        try:
            return gnome[3](proc.stdout)
        except (ValueError, IndexError) as exc:
            raise LookupError(f"gsettings returned {proc.stdout.strip()[:40]!r} for {gnome[1]}, which is not a value it should hold") from exc
    if where == "plasma" and plasma:
        proc = _cmd(["kreadconfig6", "--file", plasma[0], "--group", plasma[1], "--key", plasma[2]])
        if proc is None:
            raise LookupError("kreadconfig6 is not installed")
        try:
            return plasma[5](proc.stdout)
        except (ValueError, IndexError) as exc:
            raise LookupError(f"{plasma[0]} holds {proc.stdout.strip()[:40]!r} for {plasma[2]}, which is not a value it should hold") from exc
    raise LookupError(f"{name} is not a setting this desktop ({where}) has")


def _parse(kind: str, raw: str):
    s = str(raw).strip().lower()
    if kind == "bool":
        if s in ("true", "on", "yes", "1"):
            return True
        if s in ("false", "off", "no", "0"):
            return False
        raise ValueError("expected true or false")
    n = int(s)
    if not 16 <= n <= 128:
        raise ValueError("cursor size must be between 16 and 128")
    return n


def _run(arguments: dict) -> str:
    name = (arguments.get("setting") or "").strip().lower()
    if name not in SETTINGS:
        return f"setting must be one of {', '.join(SETTINGS)}, not {name!r}."
    label, kind, gnome, plasma = SETTINGS[name]
    where = desktop()
    raw = arguments.get("value")
    try:
        before = read(name, where)
    except LookupError as exc:
        return f"Cannot do that here: {exc}."
    if raw in (None, ""):
        return f"{label}: {before}."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change {name}: {reason}."
    try:
        value = _parse(kind, raw)
    except ValueError as exc:
        return f"Not changed: {exc}."
    if where == "gnome":
        proc = _cmd(["gsettings", "set", gnome[0], gnome[1], gnome[2](value)])
    else:
        argv = ["kwriteconfig6", "--file", plasma[0], "--group", plasma[1], "--key", plasma[2]]
        argv += (["--type", plasma[3]] if plasma[3] else []) + ["--notify", plasma[4](value)]
        proc = _cmd(argv)
    if proc is None or proc.returncode != 0:
        return f"Could not change {name}: {((proc.stderr if proc else '') or 'the tool did not run').strip()[:160]}."
    after = read(name, where)
    if after != value:
        return f"The change to {name} was accepted but reads back as {after}, so it is not verified."
    return f"{label}: {before} -> {after} (verified)."


def _post_condition(arguments: dict):
    name = (arguments.get("setting") or "").strip().lower()
    if name not in SETTINGS or arguments.get("value") in (None, ""):
        return None
    try:
        want = _parse(SETTINGS[name][1], arguments["value"])
        got = read(name, desktop())
    except (ValueError, LookupError):
        return None
    return got == want, f"{name} reads back {got}"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="desktop_setting", schema=SCHEMA, run=_run)]
